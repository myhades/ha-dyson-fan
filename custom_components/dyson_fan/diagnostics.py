"""Downloadable diagnostics for Dyson Fan."""

from __future__ import annotations

from typing import Any

from homeassistant.core import HomeAssistant

from . import DysonFanConfigEntry


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: DysonFanConfigEntry
) -> dict[str, Any]:
    """Return non-sensitive configuration and runtime diagnostics."""
    return {
        "entry": {
            "title": entry.title,
            "options": dict(entry.options),
            "power_sensor": entry.runtime_data.controller.power_sensor,
            "feedback_burst_configured": (
                entry.runtime_data.controller.feedback_burst_configured
            ),
            "configured_actions": sorted(
                key for key in entry.data if key.endswith("_action")
            ),
        },
        "controller": entry.runtime_data.controller.diagnostics(),
    }
