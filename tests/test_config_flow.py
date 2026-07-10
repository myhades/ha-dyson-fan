"""Tests for the Dyson Fan config flow."""

from __future__ import annotations

from homeassistant import config_entries
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import STATE_OFF, STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers.translation import async_get_translations
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.dyson_fan import async_migrate_entry
from custom_components.dyson_fan.const import (
    CONF_FEEDBACK_BURST_ACTION,
    CONF_LEGACY_BURST_BUTTON,
    CONF_OSCILLATION_TOGGLE_ACTION,
    CONF_POWER_SENSOR,
    CONF_POWER_TOGGLE_ACTION,
    CONF_SPEED_DOWN_ACTION,
    CONF_SPEED_UP_ACTION,
    DOMAIN,
)


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
    translations = await async_get_translations(
        hass, "zh-Hans", "entity", integrations={DOMAIN}
    )
    assert (
        translations[
            "component.dyson_fan.entity.sensor.diagnostics.state.waiting_feedback"
        ]
        == "等待反馈"
    )

    # Repeated writes of the same wattage arrive through state_reported and must
    # count as separate feedback samples.
    hass.states.async_set("sensor.dyson_power", "1.2")
    hass.states.async_set("sensor.dyson_power", "1.2")
    hass.states.async_set("sensor.dyson_power", "1.2")
    await hass.async_block_till_done()
    assert hass.states.get("fan.dyson_fan").state == STATE_OFF

    # An impossible reading immediately invalidates the feedback channel.
    hass.states.async_set("sensor.dyson_power", "100.1")
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
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "control"}
    )
    assert result["type"] is FlowResultType.FORM
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"max_attempts": 2, "ir_send_interval": 0.5}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options["max_attempts"] == 2
    assert entry.options["ir_send_interval"] == 0.5


async def test_options_flow_edits_feedback_and_actions(
    hass: HomeAssistant,
) -> None:
    """The options menu also exposes config-entry entities and actions."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], _valid_input()
    )
    entry = result["result"]
    original_options = dict(entry.options)
    await hass.async_block_till_done()

    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.MENU
    assert "configuration" in result["menu_options"]
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "configuration"}
    )
    assert result["type"] is FlowResultType.FORM

    updated = _valid_input()
    updated[CONF_POWER_SENSOR] = "sensor.new_dyson_power"
    updated[CONF_FEEDBACK_BURST_ACTION] = [{"event": "dyson_feedback_burst"}]
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], updated
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.data[CONF_POWER_SENSOR] == "sensor.new_dyson_power"
    assert entry.data[CONF_FEEDBACK_BURST_ACTION] == [{"event": "dyson_feedback_burst"}]
    assert entry.options == original_options
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED


async def test_version_1_burst_button_migrates_to_action(
    hass: HomeAssistant,
) -> None:
    """Existing button selections become equivalent generic HA actions."""
    data = _valid_input()
    data[CONF_LEGACY_BURST_BUTTON] = "button.dyson_power_burst"
    entry = MockConfigEntry(
        domain=DOMAIN,
        version=1,
        data=data,
        options={},
    )
    entry.add_to_hass(hass)

    assert await async_migrate_entry(hass, entry)
    assert entry.version == 2
    assert CONF_LEGACY_BURST_BUTTON not in entry.data
    assert entry.data[CONF_FEEDBACK_BURST_ACTION] == [
        {
            "action": "button.press",
            "target": {"entity_id": "button.dyson_power_burst"},
        }
    ]


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
    result = await hass.config_entries.flow.async_configure(result["flow_id"], updated)
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    assert entry.data[CONF_POWER_SENSOR] == "sensor.new_dyson_power"
