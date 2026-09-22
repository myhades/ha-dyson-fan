"""Tests for the Dyson Fan config flow."""

from __future__ import annotations

from unittest.mock import AsyncMock, Mock, patch

import pytest
from homeassistant import config_entries
from homeassistant.components.frontend import DATA_EXTRA_MODULE_URL
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import STATE_OFF, STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType, InvalidData
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry, mock_component

from custom_components.dyson_fan import async_migrate_entry
from custom_components.dyson_fan.const import (
    CONF_CALIBRATION_MODE,
    CONF_FEEDBACK_BURST_ACTION,
    CONF_IR_SEND_INTERVAL,
    CONF_MAX_ATTEMPTS,
    CONF_OSCILLATION_TOGGLE_ACTION,
    CONF_POWER_OFF,
    CONF_POWER_OSCILLATION_DELTA,
    CONF_POWER_SENSOR,
    CONF_POWER_TOGGLE_ACTION,
    CONF_RESTORE_DEFAULT_POWER_TABLE,
    CONF_SPEED_DOWN_ACTION,
    CONF_SPEED_UP_ACTION,
    DOMAIN,
    CalibrationMode,
    power_signature_key,
)
from custom_components.dyson_fan.power import PowerSignatureTable


@pytest.fixture(autouse=True)
def mock_frontend(hass: HomeAssistant) -> None:
    """Keep flow tests independent of the optional frontend distribution."""
    mock_component(hass, "frontend")
    hass.http = Mock()
    hass.http.async_register_static_paths = AsyncMock()
    hass.data[DATA_EXTRA_MODULE_URL] = set()


def _valid_input() -> dict[str, object]:
    """Return four valid no-op-style event actions."""
    return {
        CONF_POWER_SENSOR: "sensor.dyson_power",
        CONF_POWER_TOGGLE_ACTION: [{"event": "dyson_test_power"}],
        CONF_OSCILLATION_TOGGLE_ACTION: [{"event": "dyson_test_oscillation"}],
        CONF_SPEED_UP_ACTION: [{"event": "dyson_test_speed_up"}],
        CONF_SPEED_DOWN_ACTION: [{"event": "dyson_test_speed_down"}],
    }


@pytest.mark.parametrize("unit", ["W", None, ""])
async def test_user_flow(hass: HomeAssistant, unit: str | None) -> None:
    """A power sensor and four actions create a config entry."""
    attributes = {} if unit is None else {"unit_of_measurement": unit}
    hass.states.async_set("sensor.dyson_power", "1.2", attributes)
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], _valid_input()
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Dyson Fan"
    assert result["data"][CONF_POWER_SENSOR] == "sensor.dyson_power"
    await hass.async_block_till_done()
    assert result["result"].state is ConfigEntryState.LOADED
    assert hass.states.get("fan.dyson_fan").state == STATE_UNAVAILABLE
    assert hass.states.get("fan.dyson_fan").attributes["icon"] == "dyson-fan:fan"
    hass.http.async_register_static_paths.assert_awaited_once()
    assert len(hass.data[DATA_EXTRA_MODULE_URL]) == 1
    assert hass.states.get("button.dyson_fan_calibrate_power_table") is not None
    # Repeated writes of the same wattage arrive through state_reported and must
    # count as separate feedback samples.
    hass.states.async_set("sensor.dyson_power", "1.2", attributes)
    hass.states.async_set("sensor.dyson_power", "1.2", attributes)
    hass.states.async_set("sensor.dyson_power", "1.2", attributes)
    await hass.async_block_till_done()
    assert hass.states.get("fan.dyson_fan").state == STATE_OFF

    # An impossible reading immediately invalidates the feedback channel.
    hass.states.async_set("sensor.dyson_power", "100.1", {"unit_of_measurement": "W"})
    await hass.async_block_till_done()
    assert hass.states.get("fan.dyson_fan").state == STATE_UNAVAILABLE

    # Reloads do not register routes again or replace an explicit user icon.
    registry = er.async_get(hass)
    registry.async_update_entity("fan.dyson_fan", icon="mdi:fan")
    assert await hass.config_entries.async_reload(result["result"].entry_id)
    await hass.async_block_till_done()
    assert hass.states.get("fan.dyson_fan").attributes["icon"] == "mdi:fan"
    hass.http.async_register_static_paths.assert_awaited_once()


async def test_empty_action_is_rejected(hass: HomeAssistant) -> None:
    """The flow reports the specific invalid action instead of creating an entry."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    user_input = _valid_input()
    user_input[CONF_SPEED_UP_ACTION] = []
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], user_input
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {CONF_SPEED_UP_ACTION: "invalid_action"}


async def test_energy_sensor_is_rejected(hass: HomeAssistant) -> None:
    """Prevent an energy total from silently becoming instantaneous fan power."""
    hass.states.async_set("sensor.dyson_power", "1.2", {"unit_of_measurement": "kWh"})
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], _valid_input()
    )
    assert result["errors"] == {CONF_POWER_SENSOR: "invalid_power_unit"}


async def test_options_flow(hass: HomeAssistant) -> None:
    """Control options are editable through the native options menu."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], _valid_input()
    )
    entry = result["result"]
    await hass.async_block_till_done()

    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.MENU
    assert result["menu_options"] == ["control", "power_table"]
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "control"}
    )
    assert result["type"] is FlowResultType.FORM
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            "max_attempts": 2,
            "ir_send_interval": 0.5,
            CONF_CALIBRATION_MODE: CalibrationMode.FULL,
        },
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options["max_attempts"] == 2
    assert entry.options["ir_send_interval"] == 0.5
    assert entry.options[CONF_CALIBRATION_MODE] == CalibrationMode.FULL


async def test_power_table_input_is_rounded_before_validation_and_storage(
    hass: HomeAssistant,
) -> None:
    """Manual power tables use the same two-decimal precision as calibration."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], _valid_input()
    )
    entry = result["result"]
    await hass.async_block_till_done()

    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "power_table"}
    )
    assert result["type"] is FlowResultType.FORM
    assert [marker.schema for marker in result["data_schema"].schema] == [
        CONF_RESTORE_DEFAULT_POWER_TABLE,
        CONF_POWER_OSCILLATION_DELTA,
        CONF_POWER_OFF,
        *(power_signature_key(speed, False) for speed in range(1, 11)),
    ]

    values = PowerSignatureTable.from_options({}).as_options()
    values = {key: value + 0.004 for key, value in values.items()}
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], values
    )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert all(entry.options[key] == round(value, 2) for key, value in values.items())
    assert power_signature_key(1, True) not in entry.options

    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "power_table"}
    )
    values = PowerSignatureTable.from_options(entry.options).as_options()
    values[CONF_POWER_OFF] = 1.231
    values[power_signature_key(1, False)] = 1.234
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], values
    )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "invalid_power_table"}


@pytest.mark.parametrize(
    "ignored_values",
    [
        {},
        {
            CONF_POWER_OFF: "invalid",
            CONF_POWER_OSCILLATION_DELTA: None,
            power_signature_key(1, False): "",
            power_signature_key(2, False): -5,
            power_signature_key(10, False): 1000,
        },
    ],
)
async def test_restore_factory_power_table_ignores_inputs(
    hass: HomeAssistant, ignored_values: dict[str, object]
) -> None:
    """Reset bypasses both selector and table validation without resetting controls."""
    original_data = _valid_input()
    control_options = {
        CONF_MAX_ATTEMPTS: 4,
        CONF_IR_SEND_INTERVAL: 0.8,
        CONF_CALIBRATION_MODE: CalibrationMode.FULL,
    }
    entry = MockConfigEntry(
        domain=DOMAIN,
        version=3,
        data=original_data,
        options={
            **control_options,
            CONF_POWER_OFF: 2.0,
            CONF_POWER_OSCILLATION_DELTA: 4.0,
            power_signature_key(1, False): 6.0,
            power_signature_key(1, True): 10.0,
        },
    )
    entry.add_to_hass(hass)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "power_table"}
    )
    fields = list(result["data_schema"].schema)
    assert fields[0].schema == CONF_RESTORE_DEFAULT_POWER_TABLE
    assert fields[0].default() is False

    with patch(
        "custom_components.dyson_fan.config_flow._validate_power_table"
    ) as validate:
        result = await hass.config_entries.options.async_configure(
            result["flow_id"],
            {CONF_RESTORE_DEFAULT_POWER_TABLE: True, **ignored_values},
        )
    validate.assert_not_called()
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert dict(entry.options) == {
        **control_options,
        **PowerSignatureTable.from_options({}).as_options(),
    }
    assert dict(entry.data) == original_data

    # Reset is an instruction for one save, not a persistent option.
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "power_table"}
    )
    assert next(iter(result["data_schema"].schema)).default() is False


async def test_power_table_still_validates_numbers_without_reset(
    hass: HomeAssistant,
) -> None:
    """The reset bypass does not weaken validation for normal edits."""
    entry = MockConfigEntry(domain=DOMAIN, version=3, data=_valid_input())
    entry.add_to_hass(hass)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "power_table"}
    )
    with pytest.raises(InvalidData):
        await hass.config_entries.options.async_configure(
            result["flow_id"],
            {CONF_RESTORE_DEFAULT_POWER_TABLE: False, CONF_POWER_OFF: "invalid"},
        )
    assert not entry.options


async def test_legacy_power_table_migrates_to_shared_oscillation_delta(
    hass: HomeAssistant,
) -> None:
    """Version 2 tables retain stationary readings and median oscillation use."""
    legacy_options: dict[str, float | int] = {
        "max_attempts": 2,
        CONF_POWER_OFF: 1.2,
    }
    for speed in range(1, 11):
        legacy_options[power_signature_key(speed, False)] = float(speed * 4)
        legacy_options[power_signature_key(speed, True)] = float(speed * 4 + 3)
    entry = MockConfigEntry(
        domain=DOMAIN,
        entry_id="legacy-entry",
        unique_id="legacy-entry",
        version=2,
        data=_valid_input(),
        options=legacy_options,
    )
    entry.add_to_hass(hass)

    assert await async_migrate_entry(hass, entry)

    assert entry.version == 3
    assert entry.options[CONF_POWER_OSCILLATION_DELTA] == 3.0
    assert entry.options[power_signature_key(10, False)] == 40.0
    assert power_signature_key(10, True) not in entry.options


async def test_reconfigure_flow(hass: HomeAssistant) -> None:
    """The sensor and all four actions can be replaced without deleting the fan."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], _valid_input()
    )
    entry = result["result"]
    await hass.async_block_till_done()

    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={
            "source": config_entries.SOURCE_RECONFIGURE,
            "entry_id": entry.entry_id,
        },
    )
    assert result["type"] is FlowResultType.FORM
    updated = _valid_input()
    updated[CONF_POWER_SENSOR] = "sensor.new_dyson_power"
    updated[CONF_FEEDBACK_BURST_ACTION] = [{"event": "dyson_feedback_burst"}]
    result = await hass.config_entries.flow.async_configure(result["flow_id"], updated)
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    assert entry.data[CONF_POWER_SENSOR] == "sensor.new_dyson_power"
    assert entry.data[CONF_FEEDBACK_BURST_ACTION] == [{"event": "dyson_feedback_burst"}]
