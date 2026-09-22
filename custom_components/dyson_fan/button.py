"""Button platform for Dyson Fan."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import DysonFanConfigEntry
from .const import DOMAIN
from .entity import DysonFanEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: DysonFanConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the automatic calibration button."""
    registry = er.async_get(hass)
    obsolete_entity = registry.async_get_entity_id(
        "button", DOMAIN, f"{entry.entry_id}_undo_calibration"
    )
    if obsolete_entity is not None:
        registry.async_remove(obsolete_entity)
    async_add_entities([DysonFanCalibrationButton(entry)])


class DysonFanCalibrationButton(DysonFanEntity, ButtonEntity):
    """Start a guarded automatic calibration run."""

    _attr_translation_key = "calibrate_power_table"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_icon = "mdi:tune-variant"

    def __init__(self, entry: DysonFanConfigEntry) -> None:
        """Initialize the calibration button."""
        super().__init__(entry)
        self._attr_unique_id = f"{entry.entry_id}_calibrate_power_table"

    @property
    def available(self) -> bool:
        """Only allow calibration with sane, initialized power feedback."""
        return self.controller.available and not self.controller.calibrating

    async def async_press(self) -> None:
        """Schedule calibration without blocking the button action call."""
        self.controller.async_request_calibration(self._context)
