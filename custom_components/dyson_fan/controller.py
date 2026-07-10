"""Feedback-driven controller for Dyson Fan."""

from __future__ import annotations

import asyncio
import logging
import math
from collections import deque
from collections.abc import Callable, Mapping
from contextlib import suppress
from copy import deepcopy
from datetime import datetime
from statistics import median
from time import monotonic
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import (
    CALLBACK_TYPE,
    Context,
    Event,
    EventStateChangedData,
    EventStateReportedData,
    HomeAssistant,
    State,
    callback,
)
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.event import (
    async_track_state_change_event,
    async_track_state_report_event,
)
from homeassistant.helpers.script import Script, async_validate_actions_config
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .calibration import (
    CalibrationCancelled,
    CalibrationError,
    CalibrationResult,
    build_calibrated_table,
)
from .const import (
    CALIBRATION_ENDPOINT_CHANGE_FLOOR_WATTS,
    CALIBRATION_ENDPOINT_CHANGE_RATIO,
    CALIBRATION_INITIAL_COMMANDS,
    CALIBRATION_IR_SEND_INTERVAL_SECONDS,
    CALIBRATION_MAX_PROBES,
    CALIBRATION_MEASUREMENT_SAMPLES,
    CALIBRATION_MEASUREMENT_SETTLE_SECONDS,
    CALIBRATION_MEASUREMENT_TIMEOUT_SECONDS,
    CALIBRATION_PROBE_COMMANDS,
    CALIBRATION_RESTORE_TIMEOUT_SECONDS,
    CALIBRATION_STABILITY_FLOOR_WATTS,
    CALIBRATION_STABILITY_RATIO,
    CALIBRATION_UNCHANGED_PROBES,
    CONF_BURST_BUTTON,
    CONF_IR_SEND_INTERVAL,
    CONF_MAX_ATTEMPTS,
    CONF_OSCILLATION_TOGGLE_ACTION,
    CONF_POWER_SENSOR,
    CONF_POWER_TOGGLE_ACTION,
    CONF_SPEED_DOWN_ACTION,
    CONF_SPEED_UP_ACTION,
    DEFAULT_IR_SEND_INTERVAL,
    DEFAULT_MAX_ATTEMPTS,
    DOMAIN,
    FEEDBACK_TIMEOUT_SECONDS,
    PERSIST_DELAY_SECONDS,
    POST_COMMAND_SETTLE_SECONDS,
    STABLE_REPORTS_REQUIRED,
    STATE_CALIBRATING,
    STATE_ERROR,
    STATE_IDLE,
    STATE_INITIALIZING,
    STATE_SENDING,
    STATE_WAITING_FEEDBACK,
    STORAGE_KEY_PREFIX,
    STORAGE_VERSION,
)
from .models import Command, DecodedPower, FanState, StableObservation, TargetState
from .power import (
    InvalidPowerReading,
    PowerDecoder,
    PowerSignatureTable,
    StablePowerTracker,
)

_LOGGER = logging.getLogger(__name__)

type ControllerListener = Callable[[], None]

_COMMAND_ACTION_KEYS: Mapping[Command, str] = {
    Command.POWER_TOGGLE: CONF_POWER_TOGGLE_ACTION,
    Command.OSCILLATION_TOGGLE: CONF_OSCILLATION_TOGGLE_ACTION,
    Command.SPEED_UP: CONF_SPEED_UP_ACTION,
    Command.SPEED_DOWN: CONF_SPEED_DOWN_ACTION,
}


class DysonFanController:
    """Serialize relative IR commands and converge using power feedback."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        action_configs: Mapping[str, object],
    ) -> None:
        """Initialize the controller."""
        self.hass = hass
        self.entry = entry
        registry = er.async_get(hass)
        power_sensor = er.async_resolve_entity_id(
            registry, str(entry.data[CONF_POWER_SENSOR])
        )
        if power_sensor is None:
            raise HomeAssistantError("The configured power sensor no longer exists")
        self.power_sensor = power_sensor

        burst_value = entry.data.get(CONF_BURST_BUTTON)
        self.burst_button = (
            er.async_resolve_entity_id(registry, str(burst_value))
            if burst_value
            else None
        )

        self.max_attempts = int(
            entry.options.get(CONF_MAX_ATTEMPTS, DEFAULT_MAX_ATTEMPTS)
        )
        self.ir_send_interval = float(
            entry.options.get(CONF_IR_SEND_INTERVAL, DEFAULT_IR_SEND_INTERVAL)
        )
        self.decoder = PowerDecoder(PowerSignatureTable.from_options(entry.options))
        self.tracker = StablePowerTracker(STABLE_REPORTS_REQUIRED)
        self._action_configs = action_configs
        self._actions: dict[Command, Script] = {}

        self._store: Store[dict[str, Any]] = Store(
            hass, STORAGE_VERSION, f"{STORAGE_KEY_PREFIX}.{entry.entry_id}"
        )
        self._listeners: set[ControllerListener] = set()
        self._unsubscribers: list[CALLBACK_TYPE] = []
        self._worker_task: asyncio.Task[None] | None = None
        self._calibration_task: asyncio.Task[None] | None = None
        self._shutting_down = False

        self.accepted: FanState | None = None
        self.target: TargetState | None = None
        self.supposed: FanState | None = None
        self.last_decoded: DecodedPower | None = None
        self.last_stable: StableObservation | None = None
        self.last_speed: int | None = None

        self.phase = STATE_INITIALIZING
        self.available = False
        self.during_attempt = False
        self.attempt_count = 0
        self.stable_report_count = 0
        self.target_revision = 0
        self.handled_revision = 0
        self.last_command: Command | None = None
        self.last_error: str | None = None
        self.last_read: datetime | None = None
        self.last_operation: datetime | None = None
        self.last_confirmation: datetime | None = None

        self.calibrating = False
        self.calibration_step: str | None = None
        self.calibration_result: str | None = None
        self.calibration_error: str | None = None
        self.calibration_measurements: dict[str, float] = {}
        self.calibration_scale: float | None = None
        self.calibration_offset: float | None = None
        self.calibration_started: datetime | None = None
        self.calibration_finished: datetime | None = None

        self._source_valid = False
        self._feedback_valid = False
        self._samples_enabled = True
        self._feedback_sequence = 0
        self._latest_feedback: FanState | None = None
        self._wake_event = asyncio.Event()
        self._last_ir_sent_monotonic: float | None = None
        self._context = Context()
        self._calibration_requested = False
        self._calibration_cancel = asyncio.Event()
        self._raw_sequence = 0
        self._raw_samples: deque[tuple[int, float]] = deque(maxlen=100)

    async def async_start(self) -> None:
        """Load persistent data, prepare actions, and subscribe to feedback."""
        stored = await self._store.async_load()
        if stored and isinstance(stored.get("last_speed"), int):
            speed = int(stored["last_speed"])
            if 1 <= speed <= 10:
                self.last_speed = speed

        validated_actions: dict[Command, list[dict[str, Any]]] = {}
        for command, key in _COMMAND_ACTION_KEYS.items():
            raw_sequence = _normalize_action_sequence(self._action_configs[key])
            validated_actions[command] = await async_validate_actions_config(
                self.hass, deepcopy(raw_sequence)
            )

        for command, validated in validated_actions.items():
            self._actions[command] = Script(
                self.hass,
                validated,
                f"{self.entry.title} {command.value}",
                DOMAIN,
                log_exceptions=True,
            )

        self._unsubscribers.extend(
            (
                async_track_state_change_event(
                    self.hass, self.power_sensor, self._async_on_state_changed
                ),
                async_track_state_report_event(
                    self.hass, self.power_sensor, self._async_on_state_reported
                ),
            )
        )

        if current := self.hass.states.get(self.power_sensor):
            self._async_process_power_state(current)

    async def async_shutdown(self) -> None:
        """Stop work, unsubscribe listeners, and persist remembered speed."""
        self._shutting_down = True
        for unsubscribe in self._unsubscribers:
            unsubscribe()
        self._unsubscribers.clear()

        if self._worker_task and not self._worker_task.done():
            self._worker_task.cancel()
            with suppress(asyncio.CancelledError):
                await self._worker_task

        if self._calibration_task and not self._calibration_task.done():
            self._calibration_task.cancel()
            with suppress(asyncio.CancelledError):
                await self._calibration_task

        for action in self._actions.values():
            await action.async_unload()
        self._actions.clear()
        await self._store.async_save({"last_speed": self.last_speed})

    @callback
    def async_add_listener(self, listener: ControllerListener) -> CALLBACK_TYPE:
        """Subscribe an entity to controller state updates."""
        self._listeners.add(listener)

        @callback
        def remove_listener() -> None:
            self._listeners.discard(listener)

        return remove_listener

    @callback
    def async_request_turn_on(
        self, percentage: int | None, context: Context | None = None
    ) -> None:
        """Request that the fan turn on, optionally at a specific speed."""
        if percentage is not None and percentage <= 0:
            self.async_request_turn_off(context)
            return
        base = self._target_base()
        speed = base.speed
        if percentage is not None:
            speed = _percentage_to_speed(percentage)
        self._async_set_target(
            TargetState(power=True, speed=speed, oscillating=base.oscillating),
            context,
        )

    @callback
    def async_request_turn_off(self, context: Context | None = None) -> None:
        """Request that the fan turn off."""
        base = self._target_base()
        self._async_set_target(
            TargetState(power=False, speed=base.speed, oscillating=False), context
        )

    @callback
    def async_request_percentage(
        self, percentage: int, context: Context | None = None
    ) -> None:
        """Request a Home Assistant percentage."""
        if percentage <= 0:
            self.async_request_turn_off(context)
            return
        base = self._target_base()
        self._async_set_target(
            TargetState(
                power=True,
                speed=_percentage_to_speed(percentage),
                oscillating=base.oscillating,
            ),
            context,
        )

    @callback
    def async_request_oscillation(
        self, oscillating: bool, context: Context | None = None
    ) -> None:
        """Request oscillation without turning on an off fan."""
        base = self._target_base()
        self._async_set_target(
            TargetState(
                power=base.power,
                speed=base.speed,
                oscillating=bool(oscillating) if base.power else False,
            ),
            context,
        )

    @callback
    def async_request_calibration(self, context: Context | None = None) -> None:
        """Start one guarded automatic power-table calibration."""
        if (
            self.calibrating
            or not self.available
            or self.accepted is None
            or self.supposed is None
        ):
            return

        restore_target = TargetState(
            self.accepted.power,
            self.accepted.speed,
            self.accepted.oscillating,
        )
        self.calibrating = True
        self._calibration_requested = True
        self._calibration_cancel.clear()
        self.calibration_step = "starting"
        self.calibration_result = None
        self.calibration_error = None
        self.calibration_measurements = {}
        self.calibration_scale = None
        self.calibration_offset = None
        self.calibration_started = dt_util.utcnow()
        self.calibration_finished = None
        self.phase = STATE_CALIBRATING
        if context is not None:
            self._context = context

        # Stop normal convergence after any IR action already in progress.
        self.target = restore_target
        self.target_revision += 1
        revision = self.target_revision
        self._wake_event.set()
        self._notify_listeners()
        self._calibration_task = self.entry.async_create_background_task(
            self.hass,
            self._async_calibrate(restore_target, revision),
            "Dyson Fan automatic calibration",
        )

    @callback
    def _async_set_target(
        self,
        target: TargetState,
        context: Context | None,
        *,
        cancel_calibration: bool = True,
    ) -> None:
        """Replace the requested target and wake the serialized worker."""
        if self.calibrating and cancel_calibration:
            self._calibration_cancel.set()
        self.target = target
        self.target_revision += 1
        if context is not None:
            self._context = context
        self.last_error = None
        self._wake_event.set()
        self._notify_listeners()
        self._ensure_worker()

    @callback
    def _ensure_worker(self) -> None:
        """Ensure a config-entry-owned convergence task is running."""
        if self._shutting_down or self._calibration_requested or self.supposed is None:
            return
        if self._worker_task and not self._worker_task.done():
            return
        self._worker_task = self.entry.async_create_background_task(
            self.hass,
            self._async_worker(),
            "Dyson Fan convergence worker",
        )

    async def _async_worker(self) -> None:
        """Converge to the newest target, one command at a time."""
        while not self._shutting_down:
            if self._calibration_requested:
                return
            if (
                self.target is None
                or self.supposed is None
                or self.target_revision == self.handled_revision
            ):
                return

            revision = self.target_revision
            self.during_attempt = True
            self.attempt_count = 0
            completed = False

            if (
                self.accepted is not None
                and self._feedback_matches_target(self.accepted, self.target)
                and self._feedback_matches_target(self.supposed, self.target)
            ):
                self.phase = STATE_IDLE
                self.handled_revision = revision
                self.during_attempt = False
                self._notify_listeners()
                return

            for attempt in range(1, self.max_attempts + 1):
                if revision != self.target_revision:
                    break
                self.attempt_count = attempt
                self.last_error = None
                self._notify_listeners()

                planned = await self._async_execute_target(revision)
                if revision != self.target_revision:
                    break
                if not planned:
                    if attempt == self.max_attempts:
                        self.phase = STATE_ERROR
                        completed = True
                    continue

                feedback = await self._async_wait_for_feedback(revision)
                if revision != self.target_revision:
                    break
                if feedback is None:
                    self.last_error = "feedback_timeout"
                    self.phase = STATE_ERROR
                    completed = True
                    break

                self.supposed = feedback
                if self._feedback_matches_target(feedback, self.target):
                    if self.target.speed is None and feedback.power:
                        self.target = TargetState(
                            power=True,
                            speed=feedback.speed,
                            oscillating=self.target.oscillating,
                        )
                    self.last_error = None
                    self.phase = STATE_IDLE
                    completed = True
                    break

                self.last_error = "target_mismatch"
                if attempt == self.max_attempts:
                    self.phase = STATE_ERROR
                    completed = True

            self.during_attempt = False
            self._samples_enabled = True
            if completed and revision == self.target_revision:
                self.handled_revision = revision
            self._notify_listeners()

            if revision == self.target_revision:
                return

    async def _async_execute_target(self, revision: int) -> bool:
        """Execute all currently knowable corrections for a target revision."""
        target = self.target
        supposed = self.supposed
        if target is None or supposed is None:
            return False

        self.phase = STATE_SENDING
        self._samples_enabled = False
        self.tracker.reset()
        self.stable_report_count = 0
        await self._async_press_burst()
        self._notify_listeners()

        if not target.power:
            if supposed.power and supposed.oscillating:
                if not await self._async_send_command(
                    Command.OSCILLATION_TOGGLE, revision
                ):
                    return False
                supposed = FanState(True, supposed.speed, False)
                self.supposed = supposed
            if supposed.power:
                if not await self._async_send_command(Command.POWER_TOGGLE, revision):
                    return False
                self._remember_speed(supposed.speed)
                supposed = FanState(False, supposed.speed, False)
                self.supposed = supposed
            return revision == self.target_revision

        if not supposed.power:
            if not await self._async_send_command(Command.POWER_TOGGLE, revision):
                return False
            supposed = FanState(True, supposed.speed, False)
            self.supposed = supposed

        if revision != self.target_revision:
            return False

        # Oscillation is deliberately corrected before slow multi-step speed changes.
        if supposed.oscillating != target.oscillating:
            if not await self._async_send_command(Command.OSCILLATION_TOGGLE, revision):
                return False
            supposed = FanState(True, supposed.speed, target.oscillating)
            self.supposed = supposed

        # On the first installation, an off fan has no observable speed. Power it on,
        # learn the hardware's remembered speed, then continue within the same attempt.
        if target.speed is not None and supposed.speed is None:
            baseline = await self._async_wait_for_feedback(revision)
            if baseline is None or revision != self.target_revision:
                if revision == self.target_revision:
                    self.last_error = "speed_baseline_timeout"
                return False
            self.supposed = supposed = baseline
            if not baseline.power:
                return True
            self.phase = STATE_SENDING
            self._samples_enabled = False
            self.tracker.reset()

        if target.speed is not None and supposed.speed is not None:
            command = (
                Command.SPEED_UP
                if target.speed > supposed.speed
                else Command.SPEED_DOWN
            )
            while supposed.speed != target.speed:
                if not await self._async_send_command(command, revision):
                    return False
                new_speed = supposed.speed + (1 if command is Command.SPEED_UP else -1)
                supposed = FanState(True, new_speed, supposed.oscillating)
                self.supposed = supposed

        return revision == self.target_revision

    async def _async_send_command(self, command: Command, revision: int) -> bool:
        """Run one configured action, observing spacing and target supersession."""
        if self._last_ir_sent_monotonic is not None:
            remaining = self.ir_send_interval - (
                monotonic() - self._last_ir_sent_monotonic
            )
            if remaining > 0:
                await asyncio.sleep(remaining)
        if revision != self.target_revision:
            return False

        self.last_command = command
        self.last_operation = dt_util.utcnow()
        self._notify_listeners()
        try:
            await self._actions[command].async_run(context=self._context)
        except (HomeAssistantError, RuntimeError, ValueError) as err:
            # Script action failures are also detected by missing/mismatched feedback.
            # Keep the exception in diagnostics when it propagates from HA.
            self.last_error = f"action_error: {err}"
            _LOGGER.warning("%s action failed: %s", command.value, err)
        finally:
            self._last_ir_sent_monotonic = monotonic()
        # The action may have transmitted before a newer target arrived. Report that
        # it ran so the caller updates supposed state, then re-plan at the next command
        # boundary using the new revision.
        return True

    async def _async_calibrate(
        self, restore_target: TargetState, revision: int
    ) -> None:
        """Run calibration transactionally, then restore or honor a new target."""
        user_cancelled = False
        try:
            previous_worker = self._worker_task
            if previous_worker and not previous_worker.done():
                with suppress(asyncio.CancelledError):
                    await previous_worker
            self._check_calibration_revision(revision)

            self.handled_revision = revision
            self._samples_enabled = False
            self.tracker.reset()
            result = await self._async_perform_calibration(revision)
            self._check_calibration_revision(revision)
            self._apply_calibration_result(result)
            self.calibration_result = "success"
        except CalibrationCancelled:
            user_cancelled = True
            self.calibration_result = "cancelled"
            self.calibration_error = None
        except CalibrationError as err:
            self.calibration_result = "failed"
            self.calibration_error = str(err)
            _LOGGER.warning("Automatic power-table calibration failed: %s", err)
        except (HomeAssistantError, RuntimeError, ValueError) as err:
            self.calibration_result = "failed"
            self.calibration_error = f"unexpected_error: {err}"
            _LOGGER.exception("Unexpected automatic calibration failure")
        finally:
            self._calibration_requested = False
            self._samples_enabled = True
            self.tracker.reset()
            self._wake_event.set()

        if not user_cancelled and not self._shutting_down:
            try:
                self.calibration_step = "restoring"
                self._notify_listeners()
                await self._async_restore_after_calibration(restore_target)
            except CalibrationCancelled:
                user_cancelled = True
                if self.calibration_result == "success":
                    self.calibration_result = "success_restore_cancelled"
                elif self.calibration_result == "failed":
                    self.calibration_result = "failed_restore_cancelled"
                else:
                    self.calibration_result = "cancelled"
                    self.calibration_error = None
            except (CalibrationError, TimeoutError) as err:
                suffix = f"restore_failed: {err}"
                self.calibration_error = (
                    f"{self.calibration_error}; {suffix}"
                    if self.calibration_error
                    else suffix
                )
                if self.calibration_result == "success":
                    self.calibration_result = "success_restore_failed"

        self.calibrating = False
        self.calibration_step = "finished"
        self.calibration_finished = dt_util.utcnow()
        if user_cancelled:
            self._ensure_worker()
        elif self.phase != STATE_ERROR:
            self.phase = STATE_IDLE
        self._notify_listeners()

    async def _async_perform_calibration(self, revision: int) -> CalibrationResult:
        """Normalize the fan and measure its stationary endpoint powers."""
        supposed = self.supposed
        if supposed is None:
            raise CalibrationError("Fan state is not initialized")

        self.calibration_step = "normalizing_off"
        self._notify_listeners()
        if supposed.power and supposed.oscillating:
            await self._async_calibration_send(Command.OSCILLATION_TOGGLE, revision)
            supposed = self.supposed
        if supposed is not None and supposed.power:
            await self._async_calibration_send(Command.POWER_TOGGLE, revision)

        off_watts = await self._async_measure_stable_power("off", revision)
        off_boundary = (
            self.decoder.table.off + self.decoder.table.speeds[(1, False)]
        ) / 2
        if off_watts >= off_boundary:
            raise CalibrationError("The fan did not reach the expected off state")

        self.calibration_step = "powering_on"
        self._notify_listeners()
        await self._async_calibration_send(Command.POWER_TOGGLE, revision)
        power_on_watts = await self._async_measure_stable_power("power_on", revision)
        if power_on_watts - off_watts < 0.5:
            raise CalibrationError("The fan did not turn on after the power command")
        power_on_state = self.decoder.decode(power_on_watts).state
        if not power_on_state.power:
            raise CalibrationError("Power feedback still identifies the fan as off")
        if power_on_state.oscillating:
            self.calibration_step = "stopping_oscillation"
            self._notify_listeners()
            await self._async_calibration_send(Command.OSCILLATION_TOGGLE, revision)
            self.supposed = FanState(True, power_on_state.speed, False)
            stationary_watts = await self._async_measure_stable_power(
                "power_on_stationary", revision
            )
            stationary_state = self.decoder.decode(stationary_watts).state
            if not stationary_state.power or stationary_state.oscillating:
                raise CalibrationError("Could not confirm that oscillation is off")
            self.supposed = FanState(True, stationary_state.speed, False)
        else:
            self.supposed = FanState(True, power_on_state.speed, False)

        speed_1_watts = await self._async_find_endpoint(
            Command.SPEED_DOWN, "speed_1", revision
        )
        self.supposed = FanState(True, 1, False)
        speed_10_watts = await self._async_find_endpoint(
            Command.SPEED_UP, "speed_10", revision
        )
        self.supposed = FanState(True, 10, False)
        return build_calibrated_table(
            self.decoder.table,
            off_watts,
            speed_1_watts,
            speed_10_watts,
        )

    async def _async_find_endpoint(
        self, command: Command, label: str, revision: int
    ) -> float:
        """Drive past an endpoint and verify that redundant commands change nothing."""
        self.calibration_step = f"{label}_initial"
        self._notify_listeners()
        for _ in range(CALIBRATION_INITIAL_COMMANDS):
            await self._async_calibration_send(command, revision)
        current = await self._async_measure_stable_power(label, revision)

        unchanged_probes = 0
        for probe in range(1, CALIBRATION_MAX_PROBES + 1):
            self.calibration_step = f"{label}_probe_{probe}"
            self._notify_listeners()
            for _ in range(CALIBRATION_PROBE_COMMANDS):
                await self._async_calibration_send(command, revision)
            measured = await self._async_measure_stable_power(
                f"{label}_probe_{probe}", revision
            )
            threshold = max(
                CALIBRATION_ENDPOINT_CHANGE_FLOOR_WATTS,
                abs(current) * CALIBRATION_ENDPOINT_CHANGE_RATIO,
                abs(measured) * CALIBRATION_ENDPOINT_CHANGE_RATIO,
            )
            change = measured - current
            moving_opposite = (
                command is Command.SPEED_DOWN and change > threshold
            ) or (command is Command.SPEED_UP and change < -threshold)
            if moving_opposite:
                raise CalibrationError(
                    f"Power moved in the wrong direction while finding {label}"
                )
            if abs(change) <= threshold:
                unchanged_probes += 1
            else:
                current = measured
                unchanged_probes = 0
            if unchanged_probes >= CALIBRATION_UNCHANGED_PROBES:
                self.calibration_measurements[label] = current
                return current

        raise CalibrationError(f"Could not confirm the {label} endpoint")

    async def _async_calibration_send(self, command: Command, revision: int) -> None:
        """Send one calibration command and retain every transmitted state step."""
        self._check_calibration_revision(revision)
        if self._last_ir_sent_monotonic is not None:
            calibration_interval = max(
                self.ir_send_interval, CALIBRATION_IR_SEND_INTERVAL_SECONDS
            )
            remaining = calibration_interval - (
                monotonic() - self._last_ir_sent_monotonic
            )
            if remaining > 0:
                await asyncio.sleep(remaining)
        self._check_calibration_revision(revision)
        if not await self._async_send_command(command, revision):
            raise CalibrationCancelled

        supposed = self.supposed
        if supposed is not None:
            if command is Command.POWER_TOGGLE:
                supposed = FanState(
                    not supposed.power,
                    supposed.speed,
                    False if supposed.power else supposed.oscillating,
                )
            elif command is Command.OSCILLATION_TOGGLE and supposed.power:
                supposed = FanState(True, supposed.speed, not supposed.oscillating)
            elif command in (Command.SPEED_UP, Command.SPEED_DOWN) and supposed.power:
                delta = 1 if command is Command.SPEED_UP else -1
                speed = (
                    min(10, max(1, supposed.speed + delta))
                    if supposed.speed is not None
                    else None
                )
                supposed = FanState(True, speed, supposed.oscillating)
            self.supposed = supposed

        self.phase = STATE_CALIBRATING
        self._check_calibration_revision(revision)

    async def _async_measure_stable_power(self, label: str, revision: int) -> float:
        """Measure a stable median from fresh raw sensor reports."""
        self.calibration_step = f"measuring_{label}"
        self.phase = STATE_CALIBRATING
        await self._async_press_burst()
        self._notify_listeners()
        await asyncio.sleep(CALIBRATION_MEASUREMENT_SETTLE_SECONDS)
        self._check_calibration_revision(revision)

        start_sequence = self._raw_sequence
        self._wake_event.clear()
        try:
            async with asyncio.timeout(CALIBRATION_MEASUREMENT_TIMEOUT_SECONDS):
                while True:
                    self._check_calibration_revision(revision)
                    if not self._source_valid:
                        raise CalibrationError("Power sensor is unavailable or invalid")
                    samples = [
                        watts
                        for sequence, watts in self._raw_samples
                        if sequence > start_sequence
                    ][-CALIBRATION_MEASUREMENT_SAMPLES:]
                    if len(samples) == CALIBRATION_MEASUREMENT_SAMPLES:
                        measured = float(median(samples))
                        tolerance = max(
                            CALIBRATION_STABILITY_FLOOR_WATTS,
                            abs(measured) * CALIBRATION_STABILITY_RATIO,
                        )
                        if max(samples) - min(samples) <= tolerance:
                            self.calibration_measurements[label] = measured
                            self._notify_listeners()
                            return measured

                    observed_sequence = self._raw_sequence
                    self._wake_event.clear()
                    if self._raw_sequence != observed_sequence:
                        continue
                    await self._wake_event.wait()
        except TimeoutError as err:
            raise CalibrationError(
                f"Timed out waiting for stable power at {label}"
            ) from err

    def _apply_calibration_result(self, result: CalibrationResult) -> None:
        """Commit a fully validated calibration to options and runtime state."""
        options = dict(self.entry.options)
        options.update(result.table.as_options())
        self.hass.config_entries.async_update_entry(self.entry, options=options)
        self.decoder = PowerDecoder(result.table)
        self.calibration_scale = result.scale
        self.calibration_offset = result.offset
        self.calibration_measurements.update(
            {
                "off": result.off_watts,
                "speed_1": result.speed_1_watts,
                "speed_10": result.speed_10_watts,
            }
        )
        self.supposed = FanState(True, 10, False)
        self._remember_speed(10)

    async def _async_restore_after_calibration(
        self, restore_target: TargetState
    ) -> None:
        """Restore physical state, including the fan's remembered off speed."""
        if self._calibration_cancel.is_set():
            raise CalibrationCancelled

        if not restore_target.power and restore_target.speed is not None:
            await self._async_restore_target(
                TargetState(True, restore_target.speed, False)
            )
        await self._async_restore_target(restore_target)

    async def _async_restore_target(self, target: TargetState) -> None:
        """Converge one internal restoration target or yield to a user target."""
        self._async_set_target(target, self._context, cancel_calibration=False)
        revision = self.target_revision
        worker = self._worker_task
        if worker is None:
            raise CalibrationError("Unable to start restoration")
        try:
            async with asyncio.timeout(CALIBRATION_RESTORE_TIMEOUT_SECONDS):
                await worker
        except TimeoutError:
            if not worker.done():
                worker.cancel()
            raise
        if self._calibration_cancel.is_set() or revision != self.target_revision:
            raise CalibrationCancelled
        if self.handled_revision != revision or self.phase == STATE_ERROR:
            raise CalibrationError(self.last_error or "Restoration did not converge")

    def _check_calibration_revision(self, revision: int) -> None:
        """Raise when a user request or shutdown superseded calibration."""
        if (
            self._shutting_down
            or self._calibration_cancel.is_set()
            or revision != self.target_revision
        ):
            raise CalibrationCancelled

    async def _async_wait_for_feedback(self, revision: int) -> FanState | None:
        """Open a fresh stable-sample window after commands settle."""
        self.phase = STATE_WAITING_FEEDBACK
        self._samples_enabled = False
        self.tracker.reset()
        self.stable_report_count = 0
        await self._async_press_burst()
        self._notify_listeners()

        await asyncio.sleep(POST_COMMAND_SETTLE_SECONDS)
        if revision != self.target_revision:
            return None

        self.tracker.reset()
        self.stable_report_count = 0
        start_sequence = self._feedback_sequence
        self._samples_enabled = True
        self._wake_event.clear()
        self._notify_listeners()

        try:
            async with asyncio.timeout(FEEDBACK_TIMEOUT_SECONDS):
                while revision == self.target_revision:
                    if self._feedback_sequence > start_sequence:
                        return self._latest_feedback
                    self._wake_event.clear()
                    if revision != self.target_revision:
                        return None
                    if self._feedback_sequence > start_sequence:
                        return self._latest_feedback
                    await self._wake_event.wait()
        except TimeoutError:
            return None
        finally:
            self._samples_enabled = True
        return None

    async def _async_press_burst(self) -> None:
        """Ask an optional ESPHome button to temporarily accelerate sampling."""
        if not self.burst_button:
            return
        try:
            await self.hass.services.async_call(
                "button",
                "press",
                {"entity_id": self.burst_button},
                blocking=True,
                context=self._context,
            )
        except (HomeAssistantError, ValueError) as err:
            # Burst is an optimization; losing it must never block normal feedback.
            _LOGGER.debug("Unable to request power feedback burst: %s", err)

    @callback
    def _async_on_state_changed(self, event: Event[EventStateChangedData]) -> None:
        """Handle a changed power sensor state."""
        if new_state := event.data["new_state"]:
            self._async_process_power_state(new_state)
        else:
            self._async_mark_invalid_feedback("power_sensor_removed")

    @callback
    def _async_on_state_reported(self, event: Event[EventStateReportedData]) -> None:
        """Handle an unchanged value being reported again."""
        self._async_process_power_state(event.data["new_state"])

    @callback
    def _async_process_power_state(self, state: State) -> None:
        """Decode one sensor report and advance the stability tracker."""
        self.last_read = dt_util.utcnow()
        if state.state in (STATE_UNKNOWN, STATE_UNAVAILABLE):
            self._async_mark_invalid_feedback(f"power_sensor_{state.state}")
            return
        try:
            decoded = self.decoder.decode(state.state)
        except InvalidPowerReading as err:
            self._async_mark_invalid_feedback(str(err))
            return

        self._source_valid = True
        self.last_decoded = decoded
        self._raw_sequence += 1
        self._raw_samples.append((self._raw_sequence, decoded.watts))
        self._wake_event.set()
        if not self._samples_enabled:
            self._notify_listeners()
            return

        stable = self.tracker.add(decoded)
        self.stable_report_count = self.tracker.count
        if stable is not None:
            self._async_accept_observation(stable)
        else:
            self._notify_listeners()

    @callback
    def _async_accept_observation(self, observation: StableObservation) -> None:
        """Accept stable power feedback as the public physical truth."""
        decoded_state = observation.decoded.state
        speed = decoded_state.speed if decoded_state.power else self.last_speed
        accepted = FanState(
            power=decoded_state.power,
            speed=speed,
            oscillating=decoded_state.oscillating if decoded_state.power else False,
        )
        self.accepted = accepted
        self.last_stable = observation
        self.last_confirmation = dt_util.utcnow()
        self._feedback_valid = True
        self.available = True
        self._latest_feedback = accepted
        self._feedback_sequence += 1
        self._wake_event.set()
        if accepted.power:
            self._remember_speed(accepted.speed)

        pending_target = self.target_revision != self.handled_revision
        if not self.during_attempt:
            self.supposed = accepted
            if not pending_target:
                self.target = TargetState(
                    power=accepted.power,
                    speed=accepted.speed,
                    oscillating=accepted.oscillating,
                )
                self.phase = STATE_IDLE
            else:
                self._ensure_worker()
        self._notify_listeners()

    @callback
    def _async_mark_invalid_feedback(self, reason: str) -> None:
        """Make entities unavailable until stable valid reports return."""
        self._source_valid = False
        self._feedback_valid = False
        self.available = False
        self.last_error = reason
        self.tracker.reset()
        self.stable_report_count = 0
        self._wake_event.set()
        self._notify_listeners()

    @callback
    def _remember_speed(self, speed: int | None) -> None:
        """Persist the last meaningful or best-known non-zero speed."""
        if speed is None or not 1 <= speed <= 10 or speed == self.last_speed:
            return
        self.last_speed = speed
        self._store.async_delay_save(
            lambda: {"last_speed": self.last_speed}, PERSIST_DELAY_SECONDS
        )

    @callback
    def _target_base(self) -> TargetState:
        """Return the best base for merging a partial HA request."""
        if self.target is not None and self.target_revision != self.handled_revision:
            return self.target
        if self.accepted is not None:
            return TargetState(
                self.accepted.power,
                self.accepted.speed,
                self.accepted.oscillating,
            )
        if self.target is not None:
            return self.target
        return TargetState(False, self.last_speed, False)

    @staticmethod
    def _feedback_matches_target(feedback: FanState, target: TargetState) -> bool:
        """Return whether feedback satisfies a requested target."""
        if feedback.power != target.power:
            return False
        if not target.power:
            return True
        return (
            target.speed is None or feedback.speed == target.speed
        ) and feedback.oscillating == target.oscillating

    @callback
    def _notify_listeners(self) -> None:
        """Notify all entity views that in-memory state changed."""
        for listener in tuple(self._listeners):
            listener()

    def diagnostics(self) -> dict[str, Any]:
        """Return the controller state used by diagnostics and the opt-in sensor."""
        return {
            "phase": self.phase,
            "available": self.available,
            "calibrating": self.calibrating,
            "calibration_step": self.calibration_step,
            "calibration_result": self.calibration_result,
            "calibration_error": self.calibration_error,
            "calibration_measurements": dict(self.calibration_measurements),
            "calibration_scale": self.calibration_scale,
            "calibration_offset": self.calibration_offset,
            "calibration_started": _as_iso(self.calibration_started),
            "calibration_finished": _as_iso(self.calibration_finished),
            "source_valid": self._source_valid,
            "feedback_valid": self._feedback_valid,
            "during_attempt": self.during_attempt,
            "attempt_count": self.attempt_count,
            "max_attempts": self.max_attempts,
            "stable_report_count": self.stable_report_count,
            "stable_reports_required": STABLE_REPORTS_REQUIRED,
            "target_revision": self.target_revision,
            "handled_revision": self.handled_revision,
            "accepted": self.accepted.as_dict() if self.accepted else None,
            "target": self.target.as_dict() if self.target else None,
            "supposed": self.supposed.as_dict() if self.supposed else None,
            "last_speed": self.last_speed,
            "last_decoded": self.last_decoded.as_dict() if self.last_decoded else None,
            "last_command": self.last_command.value if self.last_command else None,
            "last_error": self.last_error,
            "last_read": _as_iso(self.last_read),
            "last_operation": _as_iso(self.last_operation),
            "last_confirmation": _as_iso(self.last_confirmation),
            "power_sensor": self.power_sensor,
            "burst_button": self.burst_button,
            "ir_send_interval": self.ir_send_interval,
        }


def _normalize_action_sequence(value: object) -> list[dict[str, Any]]:
    """Normalize an action selector value to a non-empty action sequence."""
    if isinstance(value, dict):
        sequence: list[object] = [value]
    elif isinstance(value, list):
        sequence = value
    else:
        raise ValueError("Configured command is not a Home Assistant action sequence")
    if not sequence or not all(isinstance(action, dict) for action in sequence):
        raise ValueError("Configured command action sequence is empty or invalid")
    return [dict(action) for action in sequence]


def _percentage_to_speed(percentage: int) -> int:
    """Map Home Assistant percentage to one of ten physical speed steps."""
    return min(10, max(1, math.ceil(percentage / 10)))


def _as_iso(value: datetime | None) -> str | None:
    """Serialize a timestamp for entity attributes and diagnostics."""
    return value.isoformat() if value else None
