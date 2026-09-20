"""Tests for the Dyson Fan config flow."""

from __future__ import annotations

from homeassistant import config_entries
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import STATE_OFF, STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.dyson_fan import async_migrate_entry
from custom_components.dyson_fan.const import (
    CONF_CALIBRATION_MODE,
    CONF_FEEDBACK_BURST_ACTION,
    CONF_OSCILLATION_TOGGLE_ACTION,
    CONF_POWER_OFF,
    CONF_POWER_OSCILLATION_DELTA,
    CONF_POWER_SENSOR,
    CONF_POWER_TOGGLE_ACTION,
    CONF_SPEED_DOWN_ACTION,
    CONF_SPEED_UP_ACTION,
    DOMAIN,
    CalibrationMode,
    power_signature_key,
)
from custom_components.dyson_fan.power import PowerSignatureTable


def _valid_input() -> dict[str, object]:
    """Return four valid no-op-style event actions."""
    return {
        CONF_POWER_SENSOR: "sensor.dyson_power",
        CONF_POWER_TOGGLE_ACTION: [{"event": "dyson_test_power"}],
        CONF_OSCILLATION_TOGGLE_ACTION: [{"event": "dyson_test_oscillation"}],
        CONF_SPEED_UP_ACTION: [{"event": "dyson_test_speed_up"}],
        CONF_SPEED_DOWN_ACTION: [{"event": "dyson_test_speed_down"}],
    }


async def test_user_flow(hass: HomeAssistant) -> None:
    """A power sensor and four actions create a config entry."""
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
    assert hass.states.get("button.dyson_fan_calibrate_power_table") is not None
    # Repeated writes of the same wattage arrive through state_reported and must
    # count as separate feedback samples.
    hass.states.async_set("sensor.dyson_power", "1.2", {"unit_of_measurement": "W"})
    hass.states.async_set("sensor.dyson_power", "1.2", {"unit_of_measurement": "W"})
    hass.states.async_set("sensor.dyson_power", "1.2", {"unit_of_measurement": "W"})
    await hass.async_block_till_done()
    assert hass.states.get("fan.dyson_fan").state == STATE_OFF

    # An impossible reading immediately invalidates the feedback channel.
    hass.states.async_set("sensor.dyson_power", "100.1", {"unit_of_measurement": "W"})
    await hass.async_block_till_done()
    assert hass.states.get("fan.dyson_fan").state == STATE_UNAVAILABLE


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
