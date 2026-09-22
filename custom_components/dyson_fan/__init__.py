"""Dyson Fan integration setup."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Any

import voluptuous as vol
from homeassistant.components.frontend import add_extra_js_url
from homeassistant.components.http import StaticPathConfig
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryError, HomeAssistantError
from homeassistant.helpers.storage import Store
from homeassistant.helpers.typing import ConfigType

from .const import (
    ACTION_KEYS,
    CONF_FEEDBACK_BURST_ACTION,
    PLATFORMS,
    STORAGE_KEY_PREFIX,
    STORAGE_VERSION,
)
from .controller import DysonFanController
from .power import PowerSignatureTable, merge_power_table_options

_LOGGER = logging.getLogger(__name__)
_DATA_ICON_AVAILABLE = "dyson_fan_icon_available"


@dataclass(slots=True)
class DysonFanRuntimeData:
    """Objects owned by a loaded config entry."""

    controller: DysonFanController
    fan_icon: str = "mdi:fan"


type DysonFanConfigEntry = ConfigEntry[DysonFanRuntimeData]


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Register the bundled icon once, shared by all entries and reloads."""
    hass.data[_DATA_ICON_AVAILABLE] = False
    try:
        icon_path = Path(__file__).parent / "frontend" / "icons.js"
        content = await hass.async_add_executor_job(icon_path.read_bytes)
        digest = sha256(content).hexdigest()[:16]
        url = f"/dyson_fan/icons-{digest}.js"
        await hass.http.async_register_static_paths(
            [StaticPathConfig(url, str(icon_path), True)]
        )
        add_extra_js_url(hass, url)
    except (OSError, RuntimeError, ValueError) as err:
        _LOGGER.warning(
            "Unable to load the custom fan icon; using mdi:fan instead: %s", err
        )
    else:
        hass.data[_DATA_ICON_AVAILABLE] = True
    return True


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
    entry.runtime_data = DysonFanRuntimeData(
        controller,
        fan_icon="dyson-fan:fan" if hass.data.get(_DATA_ICON_AVAILABLE) else "mdi:fan",
    )
    try:
        await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    except Exception, asyncio.CancelledError:
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
