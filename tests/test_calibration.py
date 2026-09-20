"""Automatic power-table calibration tests."""

from __future__ import annotations

import asyncio
from itertools import pairwise
from unittest.mock import AsyncMock

import pytest
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.dyson_fan.calibration import (
    CALIBRATION_REFERENCE_TABLE,
    CalibrationError,
    build_calibrated_table,
    build_full_calibrated_table,
    robust_power_average,
)
from custom_components.dyson_fan.const import (
    CONF_CALIBRATION_MODE,
    CONF_IR_SEND_INTERVAL,
    CONF_MAX_ATTEMPTS,
    CONF_OSCILLATION_TOGGLE_ACTION,
    CONF_POWER_SENSOR,
    CONF_POWER_TOGGLE_ACTION,
    CONF_SPEED_DOWN_ACTION,
    CONF_SPEED_UP_ACTION,
    DOMAIN,
    CalibrationMode,
)
from custom_components.dyson_fan.controller import DysonFanController
from custom_components.dyson_fan.models import Command, FanState, TargetState
from custom_components.dyson_fan.power import PowerSignatureTable


def _entry(*, calibration_mode: CalibrationMode | None = None) -> MockConfigEntry:
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
        options={
            CONF_MAX_ATTEMPTS: 1,
            CONF_IR_SEND_INTERVAL: 0,
            **(
                {CONF_CALIBRATION_MODE: calibration_mode}
                if calibration_mode is not None
                else {}
            ),
        },
    )


def test_calibration_preserves_non_linear_reference_curve() -> None:
    """Projection retains the active curve and one oscillation increment."""
    table = CALIBRATION_REFERENCE_TABLE

    result = build_calibrated_table(1.2345, 5.4321, 57.5432)

    assert result.table.off == 1.23
    assert result.table.speeds[(1, False)] == 5.43
    assert result.table.speeds[(10, False)] == 57.54
    assert result.table.speeds[(5, True)] == round(
        result.table.speeds[(5, False)] + table.oscillation_delta, 2
    )
    stationary = [result.table.speeds[(speed, False)] for speed in range(1, 11)]
    increments = [right - left for left, right in pairwise(stationary)]
    assert len({round(value, 2) for value in increments}) > 1
    assert all(
        value == round(value, 2)
        for value in (result.table.off, *result.table.speeds.values())
    )


def test_endpoint_calibration_uses_current_user_curve() -> None:
    """Endpoint mode shifts and scales the active stationary curve."""
    options = CALIBRATION_REFERENCE_TABLE.as_options()
    options["power_speed_5_stationary"] = 19.0
    reference = PowerSignatureTable.from_options(options)

    result = build_calibrated_table(
        1.3,
        5.0,
        55.0,
        oscillation_delta=3.1,
        reference_table=reference,
    )

    expected = round(result.scale * 19.0 + result.offset, 2)
    assert result.table.speeds[(5, False)] == expected
    assert result.table.oscillation_delta == 3.1


def test_full_calibration_uses_every_stationary_measurement() -> None:
    """Full mode stores measured stationary values without curve projection."""
    stationary = {speed: float(speed * 5) for speed in range(1, 11)}

    result = build_full_calibrated_table(1.2, stationary, 2.9)

    assert result.table.speeds[(6, False)] == 30.0
    assert result.table.speeds[(6, True)] == 32.9


def test_robust_power_average_rejects_outliers() -> None:
    """One transient spike does not bias an otherwise stable measurement."""
    assert robust_power_average([10.0, 10.1, 45.0, 9.9, 10.0]) == 10.0
    assert robust_power_average([10.0, 11.0, 9.0]) is None


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


async def test_full_mode_measures_each_speed_and_confirms_endpoint(
    hass: HomeAssistant,
) -> None:
    """Full mode sends one command per speed and keeps each measurement."""
    entry = _entry(calibration_mode=CalibrationMode.FULL)
    controller = DysonFanController(hass, entry, entry.data)
    controller.target_revision = 1
    controller.supposed = FanState(True, 1, False)
    controller._async_calibration_send = AsyncMock()  # type: ignore[method-assign]
    controller._async_measure_stable_power = AsyncMock(  # type: ignore[method-assign]
        side_effect=[6.5, 9.7, 13.0, 18.2, 22.8, 28.5, 35.3, 43.3, 52.2]
    )
    controller._async_find_endpoint = AsyncMock(  # type: ignore[method-assign]
        return_value=52.2
    )

    result = await controller._async_perform_full_calibration(
        1,
        1.2,
        4.8,
        2.9,
        controller.decoder.table,
    )

    assert controller._async_calibration_send.await_count == 9
    assert all(
        call.args[0] is Command.SPEED_UP
        for call in controller._async_calibration_send.await_args_list
    )
    assert result.table.speeds[(7, False)] == 28.5
    assert result.table.speeds[(7, True)] == 31.4


async def test_full_mode_rejects_a_missing_speed_step(hass: HomeAssistant) -> None:
    """A negligible power increase aborts rather than shifting later labels."""
    entry = _entry(calibration_mode=CalibrationMode.FULL)
    controller = DysonFanController(hass, entry, entry.data)
    controller.target_revision = 1
    controller.supposed = FanState(True, 1, False)
    controller._async_calibration_send = AsyncMock()  # type: ignore[method-assign]
    controller._async_measure_stable_power = AsyncMock(  # type: ignore[method-assign]
        return_value=4.9
    )

    with pytest.raises(CalibrationError, match="did not increase"):
        await controller._async_perform_full_calibration(
            1,
            1.2,
            4.8,
            2.9,
            controller.decoder.table,
        )


@pytest.mark.parametrize(
    "reports",
    [
        [(0.0, 10.0), (0.3, 10.1), (0.6, 40.0), (1.0, 9.9), (2.1, 10.0)],
        [(0.0, 10.0), (10.0, 10.1), (20.0, 9.9)],
    ],
)
async def test_stable_measurement_adapts_to_fast_and_slow_reporters(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
    reports: list[tuple[float, float]],
) -> None:
    """Fast meters finish promptly while ten-second meters need only three reports."""
    from custom_components.dyson_fan import controller as controller_module

    entry = _entry()
    controller = DysonFanController(hass, entry, entry.data)
    controller.target_revision = 1
    controller._source_valid = True
    controller._async_run_feedback_burst = AsyncMock()  # type: ignore[method-assign]
    monkeypatch.setattr(controller_module, "CALIBRATION_MEASUREMENT_SETTLE_SECONDS", 0)

    task = asyncio.create_task(controller._async_measure_stable_power("test", 1))
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    for sequence, (timestamp, watts) in enumerate(reports, 1):
        controller._raw_sequence = sequence
        controller._raw_samples.append((sequence, timestamp, watts))
        controller._wake_event.set()
        await asyncio.sleep(0)

    assert await task == pytest.approx(10.0, abs=0.05)


@pytest.mark.parametrize("interval", [0.25, 10.0])
async def test_measurement_waits_for_delayed_power_increase(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch, interval: float
) -> None:
    """Old stable feedback after IR must not terminate the new step measurement."""
    from custom_components.dyson_fan import controller as module

    monkeypatch.setattr(module, "CALIBRATION_MEASUREMENT_SETTLE_SECONDS", 0)
    entry = _entry()
    controller = DysonFanController(hass, entry, entry.data)
    controller.target_revision = 1
    controller._source_valid = True
    controller._async_run_feedback_burst = AsyncMock()
    task = asyncio.create_task(
        controller._async_measure_stable_power(
            "speed_2", 1, accept=lambda watts: watts >= 5.31
        )
    )
    await asyncio.sleep(0)
    await asyncio.sleep(0)

    count = 9 if interval < 1 else 3
    readings = [4.8] * count + [6.5] * count
    for sequence, watts in enumerate(readings, 1):
        timestamp = sequence * interval
        controller._raw_sequence = sequence
        controller._raw_samples.append((sequence, timestamp, watts))
        controller._report_cadence.add(timestamp)
        controller._wake_event.set()
        await asyncio.sleep(0)
        if sequence <= count + 1:
            assert not task.done()
    assert await asyncio.wait_for(task, 1) == 6.5


async def test_power_increase_that_never_arrives_times_out(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A dropped IR command gets the waiting budget, then fails without a table."""
    from custom_components.dyson_fan import controller as module

    monkeypatch.setattr(module, "CALIBRATION_MEASUREMENT_SETTLE_SECONDS", 0)
    entry = _entry()
    controller = DysonFanController(hass, entry, entry.data)
    controller.target_revision = 1
    controller._source_valid = True
    controller._async_run_feedback_burst = AsyncMock()
    monkeypatch.setattr(controller._report_cadence, "timeout", lambda *_: 0.02)
    with pytest.raises(CalibrationError, match="Timed out"):
        await controller._async_measure_stable_power(
            "speed_2", 1, accept=lambda watts: watts >= 5.31
        )


async def test_restoration_uses_feedback_instead_of_failed_command_prediction(
    hass: HomeAssistant,
) -> None:
    """An IR action can transmit and then raise, so its old prediction is unsafe."""
    entry = _entry()
    controller = DysonFanController(hass, entry, entry.data)
    controller.target_revision = 1
    controller.supposed = FanState(True, 5, False)
    controller._async_wait_for_feedback = AsyncMock(
        return_value=FanState(True, 6, False)
    )
    planned = []

    async def restore(target):
        planned.append((controller.supposed, target))

    controller._async_restore_target = restore
    target = TargetState(True, 5, False)
    await controller._async_restore_after_calibration(target)
    assert planned == [(FanState(True, 6, False), target)]


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


async def test_calibration_measures_oscillation_then_power_cycles_again(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Both modes share an oscillation measurement and a trusted reset."""
    from custom_components.dyson_fan import controller as controller_module

    entry = _entry()
    controller = DysonFanController(hass, entry, entry.data)
    controller.target_revision = 1
    controller.supposed = FanState(True, 5, True)
    controller._async_calibration_send = AsyncMock()  # type: ignore[method-assign]
    controller._async_measure_stable_power = AsyncMock(  # type: ignore[method-assign]
        side_effect=[1.2, 21.1, 7.7, 1.2, 4.8]
    )
    controller._async_find_endpoint = AsyncMock(  # type: ignore[method-assign]
        side_effect=[4.8, 52.2]
    )
    sleep = AsyncMock()
    monkeypatch.setattr(controller_module.asyncio, "sleep", sleep)

    await controller._async_perform_calibration(1)

    assert [
        call.args[0] for call in controller._async_calibration_send.await_args_list
    ] == [
        Command.POWER_TOGGLE,
        Command.POWER_TOGGLE,
        Command.OSCILLATION_TOGGLE,
        Command.POWER_TOGGLE,
        Command.POWER_TOGGLE,
    ]
    assert sleep.await_count == 2
    sleep.assert_any_await(3.0)


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
    result = build_calibrated_table(1.5, 5.4, 57.54)
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
