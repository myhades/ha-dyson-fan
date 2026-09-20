"""Dyson Fan integration setup."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryError, HomeAssistantError
from homeassistant.helpers.storage import Store

from .const import (
    ACTION_KEYS,
    CONF_FEEDBACK_BURST_ACTION,
    PLATFORMS,
    STORAGE_KEY_PREFIX,
    STORAGE_VERSION,
)
from .controller import DysonFanController
from .power import PowerSignatureTable, merge_power_table_options


@dataclass(slots=True)
class DysonFanRuntimeData:
    """Objects owned by a loaded config entry."""

    controller: DysonFanController


type DysonFanConfigEntry = ConfigEntry[DysonFanRuntimeData]


async def async_migrate_entry(hass: HomeAssistant, entry: ConfigEntry[Any]) -> bool:
    """Migrate legacy per-speed oscillation signatures to one increment."""
    if entry.version < 3:
        table = PowerSignatureTable.from_options(entry.options)
        hass.config_entries.async_update_entry(
            entry,
            options=merge_power_table_options(entry.options, table),
            version=3,
        )
    return True


async def async_setup_entry(hass: HomeAssistant, entry: DysonFanConfigEntry) -> bool:
    """Set up Dyson Fan from a config entry."""
    controller: DysonFanController | None = None
    try:
        action_configs = {key: entry.data[key] for key in ACTION_KEYS}
        if burst_action := entry.data.get(CONF_FEEDBACK_BURST_ACTION):
            action_configs[CONF_FEEDBACK_BURST_ACTION] = burst_action
        controller = DysonFanController(hass, entry, action_configs)
        await controller.async_start()
    except (HomeAssistantError, KeyError, TypeError, ValueError, vol.Invalid) as err:
        raise ConfigEntryError(f"Invalid Dyson Fan configuration: {err}") from err

    assert controller is not None
    entry.runtime_data = DysonFanRuntimeData(controller)
    try:
        await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    except Exception:
        await controller.async_shutdown()
        raise
    return True


async def async_unload_entry(hass: HomeAssistant, entry: DysonFanConfigEntry) -> bool:
    """Unload a Dyson Fan config entry."""
    if not await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        return False
    await entry.runtime_data.controller.async_shutdown()
    return True


async def async_remove_entry(hass: HomeAssistant, entry: ConfigEntry[Any]) -> None:
    """Remove persistent state after a config entry is deleted."""
    store: Store[dict[str, Any]] = Store(
        hass, STORAGE_VERSION, f"{STORAGE_KEY_PREFIX}.{entry.entry_id}"
    )
    await store.async_remove()
