"""Calibration button registration tests."""

from types import SimpleNamespace
from unittest.mock import Mock

from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.dyson_fan.button import (
    DysonFanCalibrationButton,
    async_setup_entry,
)
from custom_components.dyson_fan.const import DOMAIN


async def test_setup_removes_obsolete_undo_button(hass: HomeAssistant) -> None:
    """Existing installs retain only the calibration button, not an orphan."""
    entry = MockConfigEntry(domain=DOMAIN)
    entry.add_to_hass(hass)
    entry.runtime_data = SimpleNamespace(controller=Mock())
    registry = er.async_get(hass)
    obsolete = registry.async_get_or_create(
        "button",
        DOMAIN,
        f"{entry.entry_id}_undo_calibration",
        config_entry=entry,
    )
    add_entities = Mock()

    await async_setup_entry(hass, entry, add_entities)

    assert registry.async_get(obsolete.entity_id) is None
    add_entities.assert_called_once()
    entities = add_entities.call_args.args[0]
    assert len(entities) == 1
    assert isinstance(entities[0], DysonFanCalibrationButton)
