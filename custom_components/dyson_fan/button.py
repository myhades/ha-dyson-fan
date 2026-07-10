"""Button platform for Dyson Fan."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
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
    """Set up the automatic calibration button."""
    async_add_entities([DysonFanCalibrationButton(entry)])


class DysonFanCalibrationButton(DysonFanEntity, ButtonEntity):
    """Start a guarded automatic calibration run."""

    _attr_translation_key = "calibrate_power_table"
    _attr_entity_category = EntityCategory.CONFIG
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
