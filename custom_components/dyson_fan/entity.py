"""Shared entity helpers for Dyson Fan."""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceInfo

from . import DysonFanConfigEntry
from .const import DOMAIN
from .controller import DysonFanController


class DysonFanEntity:
    """Base class for entities backed by one Dyson Fan controller."""

    _attr_has_entity_name = True

    def __init__(self, entry: DysonFanConfigEntry) -> None:
        """Initialize the entity."""
        self._entry = entry
        self.controller: DysonFanController = entry.runtime_data.controller
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=entry.title,
            manufacturer="Dyson",
            model="Infrared fan",
        )

    async def async_added_to_hass(self) -> None:
        """Subscribe after the entity has been registered with Home Assistant."""
        self.async_on_remove(
            self.controller.async_add_listener(self._async_controller_updated)
        )

    def _async_controller_updated(self) -> None:
        """Write the controller's in-memory state to Home Assistant."""
        self.async_write_ha_state()
