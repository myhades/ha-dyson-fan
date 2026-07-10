"""Fan platform for Dyson Fan."""

from __future__ import annotations

from typing import Any

from homeassistant.components.fan import FanEntity, FanEntityFeature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import DysonFanConfigEntry
from .const import SPEED_COUNT
from .entity import DysonFanEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: DysonFanConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the feedback fan entity."""
    async_add_entities([DysonFeedbackFan(entry)])


class DysonFeedbackFan(DysonFanEntity, FanEntity):
    """A normal HA fan whose state is confirmed by measured power."""

    _attr_name = None
    _attr_speed_count = SPEED_COUNT
    _attr_supported_features = (
        FanEntityFeature.SET_SPEED
        | FanEntityFeature.OSCILLATE
        | FanEntityFeature.TURN_ON
        | FanEntityFeature.TURN_OFF
    )

    def __init__(self, entry: DysonFanConfigEntry) -> None:
        """Initialize the fan."""
        super().__init__(entry)
        self._attr_unique_id = entry.entry_id

    @property
    def available(self) -> bool:
        """Return whether stable, sane power feedback is available."""
        return self.controller.available

    @property
    def is_on(self) -> bool | None:
        """Return feedback-confirmed power state."""
        if self.controller.accepted is None:
            return None
        return self.controller.accepted.power

    @property
    def percentage(self) -> int | None:
        """Return feedback-confirmed speed as a Home Assistant percentage."""
        accepted = self.controller.accepted
        if accepted is None:
            return None
        if not accepted.power:
            return 0
        return accepted.speed * 10 if accepted.speed is not None else None

    @property
    def oscillating(self) -> bool | None:
        """Return feedback-confirmed oscillation state."""
        accepted = self.controller.accepted
        return accepted.oscillating if accepted is not None else None

    async def async_turn_on(
        self,
        percentage: int | None = None,
        preset_mode: str | None = None,
        **kwargs: Any,
    ) -> None:
        """Request power on and return without pretending feedback arrived."""
        self.controller.async_request_turn_on(percentage, self._context)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Request power off."""
        self.controller.async_request_turn_off(self._context)

    async def async_set_percentage(self, percentage: int) -> None:
        """Request one of ten physical speeds; zero turns the fan off."""
        self.controller.async_request_percentage(percentage, self._context)

    async def async_oscillate(self, oscillating: bool) -> None:
        """Request oscillation without turning an off fan on."""
        self.controller.async_request_oscillation(oscillating, self._context)
