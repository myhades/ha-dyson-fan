"""Focused state-machine tests for Dyson Fan."""

from __future__ import annotations

import asyncio
import logging
from unittest.mock import AsyncMock, Mock, patch

import pytest
from homeassistant.core import Context, HomeAssistant, State
from homeassistant.exceptions import HomeAssistantError
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.dyson_fan.const import (
    CONF_FEEDBACK_BURST_ACTION,
    CONF_IR_SEND_INTERVAL,
    CONF_MAX_ATTEMPTS,
    CONF_OSCILLATION_TOGGLE_ACTION,
    CONF_POWER_SENSOR,
    CONF_POWER_TOGGLE_ACTION,
    CONF_SPEED_DOWN_ACTION,
    CONF_SPEED_UP_ACTION,
    DOMAIN,
)
from custom_components.dyson_fan.controller import DysonFanController, _ActionLogger
from custom_components.dyson_fan.models import Command, FanState, TargetState


def _entry() -> MockConfigEntry:
    """Return a controller-ready config entry."""
    return MockConfigEntry(
        domain=DOMAIN,
        entry_id="test-entry",
        title="Dyson Fan",
        unique_id="test-entry",
        data={
            CONF_POWER_SENSOR: "sensor.dyson_power",
            CONF_POWER_TOGGLE_ACTION: [{"event": "dyson_test_power"}],
            CONF_OSCILLATION_TOGGLE_ACTION: [{"event": "dyson_test_oscillation"}],
            CONF_SPEED_UP_ACTION: [{"event": "dyson_test_speed_up"}],
            CONF_SPEED_DOWN_ACTION: [{"event": "dyson_test_speed_down"}],
        },
        options={CONF_MAX_ATTEMPTS: 1, CONF_IR_SEND_INTERVAL: 0},
    )


async def test_invalid_feedback_logs_once_per_category_and_recovers(
    hass: HomeAssistant, caplog: pytest.LogCaptureFixture
) -> None:
    """Changing invalid watts and repeated good samples must not flood logs."""
    caplog.set_level(logging.DEBUG, logger="custom_components.dyson_fan")
    entry = _entry()
    controller = DysonFanController(hass, entry, entry.data)
    for watts in range(101, 151):
        controller._async_process_power_state(
            State(controller.power_sensor, str(watts), {"unit_of_measurement": "W"})
        )
    assert caplog.text.count("Invalid power feedback") == 1
    for _ in range(20):
        controller._async_process_power_state(
            State(controller.power_sensor, "52.2", {"unit_of_measurement": "W"})
        )
    assert caplog.text.count("Power feedback recovered") == 1
    assert caplog.text.count("Observed fan state changed") == 1
    controller._async_process_power_state(
        State(controller.power_sensor, "155", {"unit_of_measurement": "W"})
    )
    assert caplog.text.count("Invalid power feedback") == 2


@pytest.mark.parametrize("feedback", [None, FanState(True, 3, False)])
async def test_control_failure_logs_one_warning_after_attempts(
    hass: HomeAssistant, caplog: pytest.LogCaptureFixture, feedback: FanState | None
) -> None:
    """Timeouts and exhausted mismatch retries have one actionable summary."""
    entry = _entry()
    controller = DysonFanController(hass, entry, entry.data)
    controller.max_attempts = 3
    controller.target_revision = 1
    controller.target = TargetState(True, 7, False)
    controller.supposed = FanState(True, 1, False)
    controller._async_execute_target = AsyncMock(return_value=True)
    controller._async_wait_for_feedback = AsyncMock(return_value=feedback)
    await controller._async_worker()
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1
    assert "Control failed" in warnings[0].message
    assert ("feedback_timeout" if feedback is None else "target_mismatch") in warnings[
        0
    ].message
    assert controller.attempt_count == (1 if feedback is None else 3)


async def test_script_logging_is_scoped_and_quiet_at_info(
    hass: HomeAssistant, caplog: pytest.LogCaptureFixture
) -> None:
    """IR script chatter is visible under integration DEBUG, not INFO."""
    entry = _entry()
    controller = DysonFanController(hass, entry, entry.data)
    await controller.async_start()
    caplog.set_level(logging.INFO)
    assert await controller._async_send_command(Command.SPEED_UP, 0)
    assert not caplog.records
    caplog.set_level(logging.DEBUG, logger="custom_components.dyson_fan")
    assert await controller._async_send_command(Command.SPEED_UP, 0)
    script_records = [
        r for r in caplog.records if "Running dyson_fan script" in r.message
    ]
    assert len(script_records) == 1
    assert script_records[0].name == "custom_components.dyson_fan.controller.actions"
    assert script_records[0].levelno == logging.DEBUG
    await controller.async_shutdown()


def test_action_logger_preserves_unexpected_tracebacks(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Expected script errors are details; programming errors retain a traceback."""
    logger = _ActionLogger(logging.getLogger("custom_components.dyson_fan.actions"), {})
    caplog.set_level(logging.DEBUG)
    logger.error("expected action failure")
    try:
        raise RuntimeError("unexpected failure")
    except RuntimeError:
        logger.exception("unexpected action failure")
    assert caplog.records[0].levelno == logging.DEBUG
    assert caplog.records[1].levelno == logging.ERROR
    assert caplog.records[1].exc_info is not None


async def test_action_retries_do_not_duplicate_warning_logs(
    hass: HomeAssistant, caplog: pytest.LogCaptureFixture
) -> None:
    """A real HA script failure on each retry has just one final warning."""

    def fail(call):
        raise HomeAssistantError("IR transmitter offline")

    hass.services.async_register("dyson_test", "fail", fail)
    entry = _entry()
    data = dict(entry.data)
    data[CONF_SPEED_UP_ACTION] = [{"action": "dyson_test.fail"}]
    controller = DysonFanController(hass, entry, data)
    await controller.async_start()
    controller.max_attempts = 3
    controller.target_revision = 1
    controller.target = TargetState(True, 2, False)
    controller.supposed = FanState(True, 1, False)
    caplog.set_level(logging.DEBUG, logger="custom_components.dyson_fan")

    await controller._async_worker()

    failures = [r for r in caplog.records if r.levelno >= logging.WARNING]
    assert len(failures) == 1
    assert "Control failed" in failures[0].message
    assert "IR transmitter offline" in failures[0].message
    assert controller.attempt_count == 3
    await controller.async_shutdown()


async def test_feedback_burst_runs_generic_action(hass: HomeAssistant) -> None:
    """Feedback acceleration accepts an arbitrary Home Assistant action."""
    entry = _entry()
    data = dict(entry.data)
    data[CONF_FEEDBACK_BURST_ACTION] = [{"event": "dyson_feedback_burst"}]
    entry.add_to_hass(hass)
    controller = DysonFanController(hass, entry, data)
    events: list[object] = []
    hass.bus.async_listen("dyson_feedback_burst", events.append)

    await controller.async_start()
    await controller._async_run_feedback_burst()

    assert len(events) == 1
    assert controller.feedback_burst_configured
    await controller.async_shutdown()


async def test_invalid_burst_is_rejected_before_scripts_are_created(
    hass: HomeAssistant,
) -> None:
    """A malformed optional action must not leave the four IR scripts behind."""
    entry = _entry()
    data = dict(entry.data)
    data[CONF_FEEDBACK_BURST_ACTION] = "invalid action"
    controller = DysonFanController(hass, entry, data)
    controller._store.async_save = AsyncMock()
    with patch("custom_components.dyson_fan.controller.Script") as script:
        with pytest.raises(ValueError, match="not a Home Assistant action"):
            await controller.async_start()
    script.assert_not_called()
    controller._store.async_save.assert_not_called()


@pytest.mark.parametrize(
    "error", [RuntimeError("setup failed"), asyncio.CancelledError()]
)
async def test_partial_startup_cleans_scripts_and_listeners_without_saving(
    hass: HomeAssistant, error: BaseException
) -> None:
    """Failed/cancelled setup releases allocated resources, preserving storage."""
    entry = _entry()
    controller = DysonFanController(hass, entry, entry.data)
    controller._store.async_load = AsyncMock(return_value={"last_speed": 7})
    controller._store.async_save = AsyncMock()
    scripts = [Mock(async_unload=AsyncMock()) for _ in range(4)]
    unsubscribe = Mock()
    with (
        patch("custom_components.dyson_fan.controller.Script", side_effect=scripts),
        patch(
            "custom_components.dyson_fan.controller.async_track_state_change_event",
            return_value=unsubscribe,
        ),
        patch(
            "custom_components.dyson_fan.controller.async_track_state_report_event",
            side_effect=error,
        ),
        pytest.raises(type(error)),
    ):
        await controller.async_start()

    unsubscribe.assert_called_once()
    for script in scripts:
        script.async_unload.assert_awaited_once()
    assert not controller._actions
    assert not controller._unsubscribers
    assert controller.last_speed == 7
    controller._store.async_save.assert_not_called()


@pytest.mark.parametrize("supersede", [False, True])
async def test_burst_wait_is_bounded_and_cancellable(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch, supersede: bool
) -> None:
    """An optional action cannot block feedback indefinitely or hold a newer target."""
    from custom_components.dyson_fan import controller as module

    monkeypatch.setattr(module, "FEEDBACK_BURST_TIMEOUT_SECONDS", 0.02)
    entry = _entry()
    controller = DysonFanController(hass, entry, entry.data)
    entered = asyncio.Event()
    stopped = asyncio.Event()

    async def blocked(**kwargs: object) -> None:
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            stopped.set()

    controller._feedback_burst_action = AsyncMock()
    controller._feedback_burst_action.async_run.side_effect = blocked
    task = asyncio.create_task(controller._async_run_feedback_burst())
    await entered.wait()
    if supersede:
        controller.async_request_turn_off()
    await asyncio.wait_for(task, 1)
    assert stopped.is_set()


async def test_feedback_normalizes_units_before_decoding(hass: HomeAssistant) -> None:
    """kW works, while energy and missing units invalidate the same sensor."""
    entry = _entry()
    controller = DysonFanController(hass, entry, entry.data)
    for _ in range(3):
        controller._async_process_power_state(
            State("sensor.dyson_power", "0.0522", {"unit_of_measurement": "kW"})
        )
    assert controller.accepted == FanState(True, 10, False)
    for unit in ("kWh", None):
        controller._async_process_power_state(
            State("sensor.dyson_power", "52.2", {"unit_of_measurement": unit})
        )
        assert not controller.available
        assert "Unsupported power unit" in controller.last_error


@pytest.mark.parametrize("interval", [0.25, 10.0])
async def test_feedback_window_uses_actual_cadence(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch, interval: float
) -> None:
    """Real feedback accepts three reports and gives a slow source enough time."""
    from custom_components.dyson_fan import controller as module

    monkeypatch.setattr(module, "POST_COMMAND_SETTLE_SECONDS", 0)
    entry = _entry()
    controller = DysonFanController(hass, entry, entry.data)
    controller.target_revision = 1
    controller.during_attempt = True
    controller._async_run_feedback_burst = AsyncMock()
    deadlines: list[float] = []
    original_timeout = asyncio.timeout

    class Deadline:
        async def __aenter__(self):
            self.actual = original_timeout(1)
            await self.actual.__aenter__()
            return self

        def reschedule(self, deadline):
            deadlines.append(deadline - hass.loop.time())

        async def __aexit__(self, *args):
            return await self.actual.__aexit__(*args)

    monkeypatch.setattr(module.asyncio, "timeout", lambda _: Deadline())
    task = asyncio.create_task(controller._async_wait_for_feedback(1))
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    for index in range(3):
        monkeypatch.setattr(module, "monotonic", lambda i=index: i * interval)
        controller._async_process_power_state(
            State("sensor.dyson_power", "18.2", {"unit_of_measurement": "W"})
        )
        await asyncio.sleep(0)
    assert await task == FanState(True, 5, False)
    assert max(deadlines) == pytest.approx(35 if interval == 10 else 15, abs=0.1)


async def test_failed_action_does_not_advance_predicted_state(
    hass: HomeAssistant,
) -> None:
    """A known action failure remains unsent so feedback recovery can retry it."""
    entry = _entry()
    controller = DysonFanController(hass, entry, entry.data)
    controller.target_revision = 1
    controller.target = TargetState(True, 2, False)
    controller.supposed = FanState(True, 1, False)
    controller._async_run_feedback_burst = AsyncMock()  # type: ignore[method-assign]
    controller._actions[Command.SPEED_UP] = AsyncMock()
    controller._actions[Command.SPEED_UP].async_run.side_effect = HomeAssistantError(
        "transmitter unavailable"
    )

    assert not await controller._async_execute_target(1)
    assert controller.supposed == FanState(True, 1, False)
    assert controller.last_error == "action_error: transmitter unavailable"


async def test_oscillation_precedes_multi_step_speed_change(
    hass: HomeAssistant,
) -> None:
    """An already-on fan corrects oscillation before slow speed commands."""
    entry = _entry()
    controller = DysonFanController(hass, entry, entry.data)
    controller.target_revision = 1
    controller.target = TargetState(True, 5, True)
    controller.supposed = FanState(True, 1, False)
    controller._async_run_feedback_burst = AsyncMock()  # type: ignore[method-assign]

    sent: list[Command] = []

    async def record(command: Command, revision: int) -> bool:
        sent.append(command)
        return revision == controller.target_revision

    controller._async_send_command = record  # type: ignore[method-assign]

    assert await controller._async_execute_target(1)
    assert sent == [
        Command.OSCILLATION_TOGGLE,
        Command.SPEED_UP,
        Command.SPEED_UP,
        Command.SPEED_UP,
        Command.SPEED_UP,
    ]


async def test_power_off_stops_oscillation_before_toggle(
    hass: HomeAssistant,
) -> None:
    """Normal shutdown stops oscillation before power and retains the speed."""
    entry = _entry()
    controller = DysonFanController(hass, entry, entry.data)
    controller.target_revision = 1
    controller.target = TargetState(False, 7, False)
    controller.supposed = FanState(True, 7, True)
    controller._async_run_feedback_burst = AsyncMock()  # type: ignore[method-assign]
    controller._remember_speed = lambda speed: None  # type: ignore[method-assign]

    sent: list[Command] = []

    async def record(command: Command, revision: int) -> bool:
        sent.append(command)
        return True

    controller._async_send_command = record  # type: ignore[method-assign]

    assert await controller._async_execute_target(1)
    assert sent == [Command.OSCILLATION_TOGGLE, Command.POWER_TOGGLE]
    assert controller.supposed == FanState(False, 7, False)


async def test_new_revision_stops_old_speed_sequence(hass: HomeAssistant) -> None:
    """A newer target supersedes a long relative speed sequence at a command edge."""
    entry = _entry()
    controller = DysonFanController(hass, entry, entry.data)
    controller.target_revision = 1
    controller.target = TargetState(True, 10, False)
    controller.supposed = FanState(True, 1, False)
    controller._async_run_feedback_burst = AsyncMock()  # type: ignore[method-assign]

    sent: list[Command] = []

    async def supersede_after_first(command: Command, revision: int) -> bool:
        sent.append(command)
        controller.target_revision = 2
        controller.target = TargetState(False, 1, False)
        return False

    controller._async_send_command = supersede_after_first  # type: ignore[method-assign]

    assert not await controller._async_execute_target(1)
    assert sent == [Command.SPEED_UP]


async def test_target_changed_during_action_keeps_transmitted_step(
    hass: HomeAssistant,
) -> None:
    """A command already in flight still advances supposed state before replanning."""
    entry = _entry()
    controller = DysonFanController(hass, entry, entry.data)
    controller.target_revision = 1
    controller.target = TargetState(True, 3, False)
    controller.supposed = FanState(True, 1, False)
    controller._async_run_feedback_burst = AsyncMock()  # type: ignore[method-assign]

    action = AsyncMock()

    async def change_target(*args: object, **kwargs: object) -> None:
        controller.target_revision = 2
        controller.target = TargetState(False, 1, False)

    action.async_run.side_effect = change_target
    controller._actions[Command.SPEED_UP] = action

    assert not await controller._async_execute_target(1)
    assert action.async_run.await_count == 1
    assert controller.supposed == FanState(True, 2, False)


async def test_last_feedback_speed_survives_controller_restart(
    hass: HomeAssistant,
) -> None:
    """The last observed non-zero speed is stored independently of fan power."""
    entry = _entry()
    entry.add_to_hass(hass)
    hass.states.async_set("sensor.dyson_power", "18.2", {"unit_of_measurement": "W"})

    first = DysonFanController(hass, entry, entry.data)
    await first.async_start()
    hass.states.async_set("sensor.dyson_power", "18.2", {"unit_of_measurement": "W"})
    hass.states.async_set("sensor.dyson_power", "18.2", {"unit_of_measurement": "W"})
    assert first.last_speed == 5
    await first.async_shutdown()

    second = DysonFanController(hass, entry, entry.data)
    await second.async_start()
    assert second.last_speed == 5
    await second.async_shutdown()


async def test_already_confirmed_target_completes_without_action(
    hass: HomeAssistant,
) -> None:
    """A no-op service request does not wait for unnecessary fresh feedback."""
    entry = _entry()
    controller = DysonFanController(hass, entry, entry.data)
    state = FanState(False, 4, False)
    controller.accepted = state
    controller.supposed = state
    controller.target = TargetState(False, 4, False)
    controller.target_revision = 1

    await controller._async_worker()

    assert controller.handled_revision == 1
    assert controller.attempt_count == 0


async def test_partial_request_after_failure_uses_confirmed_state(
    hass: HomeAssistant,
) -> None:
    """A handled failed target cannot make oscillate turn an actually-off fan on."""
    entry = _entry()
    controller = DysonFanController(hass, entry, entry.data)
    controller.accepted = FanState(False, 4, False)
    controller.supposed = FanState(False, 4, False)
    controller.target = TargetState(True, 8, False)
    controller.target_revision = 1
    controller.handled_revision = 1
    controller._ensure_worker = lambda: None  # type: ignore[method-assign]

    controller.async_request_oscillation(True)

    assert controller.target == TargetState(False, 4, False)


async def test_first_power_on_learns_unknown_speed_and_converges(
    hass: HomeAssistant, monkeypatch: object
) -> None:
    """A first install can learn hardware speed without an initial-speed option."""
    from custom_components.dyson_fan import controller as controller_module

    monkeypatch.setattr(controller_module, "POST_COMMAND_SETTLE_SECONDS", 0)
    monkeypatch.setattr(controller_module, "FEEDBACK_TIMEOUT_SECONDS", 1)

    entry = _entry()
    entry.add_to_hass(hass)
    hass.states.async_set("sensor.dyson_power", "1.2", {"unit_of_measurement": "W"})
    controller = DysonFanController(hass, entry, entry.data)
    await controller.async_start()
    hass.states.async_set("sensor.dyson_power", "1.2", {"unit_of_measurement": "W"})
    hass.states.async_set("sensor.dyson_power", "1.2", {"unit_of_measurement": "W"})
    assert controller.accepted == FanState(False, None, False)

    commands: list[str] = []
    hass.bus.async_listen("dyson_test_power", lambda event: commands.append("power"))
    hass.bus.async_listen(
        "dyson_test_oscillation", lambda event: commands.append("oscillation")
    )

    controller._async_set_target(TargetState(True, 4, True), Context())
    for _ in range(100):
        if commands == ["power", "oscillation"]:
            break
        await asyncio.sleep(0)
    assert commands == ["power", "oscillation"]
    for _ in range(100):
        if controller.phase == "waiting_feedback" and controller._samples_enabled:
            break
        await asyncio.sleep(0)
    assert controller.phase == "waiting_feedback"
    assert controller._samples_enabled

    # The first three reports teach the previously unknown hardware speed. The
    # next three independently confirm the final state after command planning.
    for _ in range(3):
        hass.states.async_set(
            "sensor.dyson_power", "16.0", {"unit_of_measurement": "W"}
        )
        await asyncio.sleep(0)
    for _ in range(100):
        if (
            controller.accepted == FanState(True, 4, True)
            and controller.stable_report_count == 0
            and controller._samples_enabled
        ):
            break
        await asyncio.sleep(0)
    assert controller.stable_report_count == 0
    for _ in range(3):
        hass.states.async_set(
            "sensor.dyson_power", "16.0", {"unit_of_measurement": "W"}
        )
        await asyncio.sleep(0)

    for _ in range(100):
        if controller.handled_revision == 1:
            break
        await asyncio.sleep(0)
    assert controller.handled_revision == 1
    assert controller.accepted == FanState(True, 4, True)
    assert controller.last_speed == 4
    await controller.async_shutdown()
