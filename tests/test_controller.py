"""Focused state-machine tests for Dyson Fan."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

from homeassistant.components.sensor import SensorDeviceClass
from homeassistant.const import EntityCategory
from homeassistant.core import Context, HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.dyson_fan import DysonFanRuntimeData
from custom_components.dyson_fan.button import DysonFanCalibrationButton
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
    ControllerPhase,
)
from custom_components.dyson_fan.controller import DysonFanController
from custom_components.dyson_fan.models import Command, FanState, TargetState
from custom_components.dyson_fan.sensor import DysonFanDiagnosticsSensor


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


async def test_diagnostics_attributes_omit_power_table(hass: HomeAssistant) -> None:
    """Diagnostics use translated enum states without duplicating editable options."""
    entry = _entry()
    controller = DysonFanController(hass, entry, entry.data)
    entry.runtime_data = DysonFanRuntimeData(controller)
    sensor = DysonFanDiagnosticsSensor(entry)
    calibration_button = DysonFanCalibrationButton(entry)

    assert sensor.device_class is SensorDeviceClass.ENUM
    assert sensor.options == [phase.value for phase in ControllerPhase]
    assert calibration_button.entity_category is EntityCategory.DIAGNOSTIC
    assert "power_signatures" not in controller.diagnostics()


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
    hass.states.async_set("sensor.dyson_power", "18.2")

    first = DysonFanController(hass, entry, entry.data)
    await first.async_start()
    hass.states.async_set("sensor.dyson_power", "18.2")
    hass.states.async_set("sensor.dyson_power", "18.2")
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

    assert controller.handled_revision == 1, (
        controller.phase,
        controller.stable_report_count,
        controller._samples_enabled,
        controller._feedback_sequence,
        controller.last_error,
    )
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
    hass.states.async_set("sensor.dyson_power", "1.2")
    controller = DysonFanController(hass, entry, entry.data)
    await controller.async_start()
    hass.states.async_set("sensor.dyson_power", "1.2")
    hass.states.async_set("sensor.dyson_power", "1.2")
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
        hass.states.async_set("sensor.dyson_power", "16.0")
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
        hass.states.async_set("sensor.dyson_power", "16.0")
        await asyncio.sleep(0)

    for _ in range(100):
        if controller.handled_revision == 1:
            break
        await asyncio.sleep(0)
    assert controller.handled_revision == 1, (
        controller.phase,
        controller.stable_report_count,
        controller._samples_enabled,
        controller._feedback_sequence,
        controller.last_error,
    )
    assert controller.accepted == FanState(True, 4, True)
    assert controller.last_speed == 4
    await controller.async_shutdown()
