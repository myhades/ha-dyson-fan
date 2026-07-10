"""Opt-in diagnostic sensor for Dyson Fan."""

from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import SensorEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import DysonFanConfigEntry
from .entity import DysonFanEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: DysonFanConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the disabled-by-default diagnostics sensor."""
    async_add_entities([DysonFanDiagnosticsSensor(entry)])


class DysonFanDiagnosticsSensor(DysonFanEntity, SensorEntity):
    """Expose the controller state for advanced troubleshooting."""

    _attr_translation_key = "diagnostics"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_entity_registry_enabled_default = False
    _attr_icon = "mdi:state-machine"

    def __init__(self, entry: DysonFanConfigEntry) -> None:
        """Initialize the diagnostics entity."""
        super().__init__(entry)
        self._attr_unique_id = f"{entry.entry_id}_diagnostics"

    @property
    def native_value(self) -> str:
        """Return the current controller phase."""
        return self.controller.phase

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return detailed state-machine diagnostics."""
        return self.controller.diagnostics()
