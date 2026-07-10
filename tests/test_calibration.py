"""Automatic power-table calibration tests."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.dyson_fan.calibration import (
    CalibrationCancelled,
    CalibrationError,
    build_calibrated_table,
)
from custom_components.dyson_fan.const import (
    CONF_IR_SEND_INTERVAL,
    CONF_MAX_ATTEMPTS,
    CONF_OSCILLATION_TOGGLE_ACTION,
    CONF_POWER_SENSOR,
    CONF_POWER_TOGGLE_ACTION,
    CONF_SPEED_DOWN_ACTION,
    CONF_SPEED_UP_ACTION,
    DOMAIN,
)
from custom_components.dyson_fan.controller import DysonFanController
from custom_components.dyson_fan.models import Command, FanState, TargetState
from custom_components.dyson_fan.power import PowerSignatureTable


def _entry() -> MockConfigEntry:
    """Return a controller-ready config entry."""
    return MockConfigEntry(
        domain=DOMAIN,
        entry_id="calibration-entry",
        title="Dyson Fan",
        unique_id="calibration-entry",
        data={
            CONF_POWER_SENSOR: "sensor.dyson_power",
            CONF_POWER_TOGGLE_ACTION: [{"event": "dyson_test_power"}],
            CONF_OSCILLATION_TOGGLE_ACTION: [{"event": "dyson_test_oscillation"}],
            CONF_SPEED_UP_ACTION: [{"event": "dyson_test_speed_up"}],
            CONF_SPEED_DOWN_ACTION: [{"event": "dyson_test_speed_down"}],
        },
        options={CONF_MAX_ATTEMPTS: 1, CONF_IR_SEND_INTERVAL: 0},
    )


def test_calibration_identity_keeps_existing_table() -> None:
    """Matching endpoint measurements preserve every signature."""
    table = PowerSignatureTable.from_options({})

    result = build_calibrated_table(
        table,
        table.off,
        table.speeds[(1, False)],
        table.speeds[(10, False)],
    )

    assert result.table.off == pytest.approx(table.off)
    assert result.table.speeds == pytest.approx(table.speeds)
    assert result.scale == pytest.approx(1)
    assert result.offset == pytest.approx(0)


def test_calibration_affinely_updates_stationary_and_oscillating_states() -> None:
    """Both signature families use the same validated endpoint transform."""
    table = PowerSignatureTable.from_options({})

    result = build_calibrated_table(table, 1.5, 5.4, 57.54)

    assert result.table.off == 1.5
    assert result.table.speeds[(1, False)] == pytest.approx(5.4)
    assert result.table.speeds[(10, False)] == pytest.approx(57.54)
    assert result.table.speeds[(5, True)] == pytest.approx(
        result.scale * table.speeds[(5, True)] + result.offset
    )


@pytest.mark.parametrize(
    ("off_watts", "speed_1_watts", "speed_10_watts"),
    [
        (5.0, 4.0, 52.0),
        (1.2, 4.8, 20.0),
        (1.2, 2.0, 52.0),
        (1.2, 4.8, 99.9),
        (1.2, 4.8, 100.0),
    ],
)
def test_calibration_rejects_unsafe_endpoints(
    off_watts: float, speed_1_watts: float, speed_10_watts: float
) -> None:
    """Unreasonable measurements never produce a replacement table."""
    with pytest.raises(CalibrationError):
        build_calibrated_table(
            PowerSignatureTable.from_options({}),
            off_watts,
            speed_1_watts,
            speed_10_watts,
        )


async def test_endpoint_requires_two_unchanged_probe_batches(
    hass: HomeAssistant,
) -> None:
    """An endpoint is accepted only after redundant IR commands change nothing."""
    entry = _entry()
    controller = DysonFanController(hass, entry, entry.data)
    controller.target_revision = 1
    controller._async_calibration_send = AsyncMock()  # type: ignore[method-assign]
    controller._async_measure_stable_power = AsyncMock(  # type: ignore[method-assign]
        side_effect=[4.8, 4.9, 4.7]
    )

    measured = await controller._async_find_endpoint(Command.SPEED_DOWN, "speed_1", 1)

    assert measured == 4.8
    assert controller._async_calibration_send.await_count == 19
    assert controller._async_measure_stable_power.await_count == 3


async def test_endpoint_that_keeps_moving_is_rejected(hass: HomeAssistant) -> None:
    """A likely dropped-IR sequence cannot be mistaken for a speed endpoint."""
    entry = _entry()
    controller = DysonFanController(hass, entry, entry.data)
    controller.target_revision = 1
    controller._async_calibration_send = AsyncMock()  # type: ignore[method-assign]
    controller._async_measure_stable_power = AsyncMock(  # type: ignore[method-assign]
        side_effect=[20, 18, 16, 14, 12, 10, 8]
    )

    with pytest.raises(CalibrationError, match="Could not confirm"):
        await controller._async_find_endpoint(Command.SPEED_DOWN, "speed_1", 1)


async def test_calibration_uses_at_least_one_second_ir_interval(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Calibration slows every IR action even when normal control is faster."""
    from custom_components.dyson_fan import controller as controller_module

    entry = _entry()
    controller = DysonFanController(hass, entry, entry.data)
    controller.target_revision = 1
    controller._last_ir_sent_monotonic = 9.25
    controller._async_send_command = AsyncMock(  # type: ignore[method-assign]
        return_value=True
    )
    sleep = AsyncMock()
    monkeypatch.setattr(controller_module, "monotonic", lambda: 10.0)
    monkeypatch.setattr(controller_module.asyncio, "sleep", sleep)

    await controller._async_calibration_send(Command.SPEED_DOWN, 1)

    sleep.assert_awaited_once_with(pytest.approx(0.25))


async def test_user_cancel_keeps_command_already_transmitted(
    hass: HomeAssistant,
) -> None:
    """Cancellation occurs after updating the predicted state for an in-flight IR."""
    entry = _entry()
    controller = DysonFanController(hass, entry, entry.data)
    controller.target_revision = 1
    controller.supposed = FanState(True, 3, False)

    async def send_then_cancel(command: Command, revision: int) -> bool:
        controller._calibration_cancel.set()
        return True

    controller._async_send_command = send_then_cancel  # type: ignore[method-assign]

    with pytest.raises(CalibrationCancelled):
        await controller._async_calibration_send(Command.SPEED_UP, 1)
    assert controller.supposed == FanState(True, 4, False)


async def test_failed_calibration_does_not_change_options(
    hass: HomeAssistant,
) -> None:
    """A failed run restores state but leaves the configured table untouched."""
    entry = _entry()
    controller = DysonFanController(hass, entry, entry.data)
    original_options = dict(entry.options)
    controller.calibrating = True
    controller._calibration_requested = True
    controller.target_revision = 1
    controller.supposed = FanState(True, 5, False)
    controller._async_perform_calibration = AsyncMock(  # type: ignore[method-assign]
        side_effect=CalibrationError("bad endpoint")
    )
    controller._async_restore_after_calibration = AsyncMock()  # type: ignore[method-assign]

    await controller._async_calibrate(
        restore_target=TargetState(True, 5, False),
        revision=1,
    )

    assert entry.options == original_options
    assert controller.calibration_result == "failed"
    assert controller.calibration_error == "bad endpoint"
    controller._async_restore_after_calibration.assert_awaited_once()


async def test_successful_calibration_commits_whole_table_once(
    hass: HomeAssistant,
) -> None:
    """Only a complete result atomically replaces the runtime and entry table."""
    entry = _entry()
    entry.add_to_hass(hass)
    controller = DysonFanController(hass, entry, entry.data)
    old_table = controller.decoder.table
    result = build_calibrated_table(old_table, 1.5, 5.4, 57.54)
    controller.calibrating = True
    controller._calibration_requested = True
    controller.target_revision = 1
    controller.supposed = FanState(True, 5, False)
    controller._async_perform_calibration = AsyncMock(  # type: ignore[method-assign]
        return_value=result
    )
    controller._async_restore_after_calibration = AsyncMock()  # type: ignore[method-assign]

    await controller._async_calibrate(
        restore_target=TargetState(True, 5, False),
        revision=1,
    )

    assert controller.calibration_result == "success"
    assert controller.decoder.table == result.table
    assert all(
        entry.options[key] == value for key, value in result.table.as_options().items()
    )
    controller._async_restore_after_calibration.assert_awaited_once()
