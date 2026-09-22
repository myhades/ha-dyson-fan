"""Bundled icon registration and fan defaults."""

from hashlib import sha256
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

import pytest
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.dyson_fan import (
    DysonFanRuntimeData,
    async_setup,
    async_setup_entry,
)
from custom_components.dyson_fan.const import ACTION_KEYS, CONF_POWER_SENSOR, DOMAIN
from custom_components.dyson_fan.fan import DysonFeedbackFan
from custom_components.dyson_fan.models import Command


async def test_setup_registers_content_versioned_icon_module(
    hass: HomeAssistant,
) -> None:
    """Serve just the icon module and load it without manual dashboard resources."""
    hass.http = Mock()
    hass.http.async_register_static_paths = AsyncMock()
    with patch("custom_components.dyson_fan.add_extra_js_url") as add_url:
        assert await async_setup(hass, {})

    hass.http.async_register_static_paths.assert_awaited_once()
    paths = hass.http.async_register_static_paths.call_args.args[0]
    assert len(paths) == 1
    resource = paths[0]
    content = await hass.async_add_executor_job(Path(resource.path).read_bytes)
    digest = sha256(content).hexdigest()[:16]
    assert resource.url_path == f"/dyson_fan/icons-{digest}.js"
    assert resource.cache_headers
    add_url.assert_called_once_with(hass, resource.url_path)


def test_fan_default_icon() -> None:
    """Only set the entity default; do not edit the user's registry override."""
    entry = MockConfigEntry(domain=DOMAIN)
    entry.runtime_data = DysonFanRuntimeData(Mock(), fan_icon="dyson-fan:fan")
    assert DysonFeedbackFan(entry).icon == "dyson-fan:fan"


@pytest.mark.parametrize("failure", ["read", "register"])
async def test_icon_failure_falls_back_without_blocking_control(
    hass: HomeAssistant, caplog: pytest.LogCaptureFixture, failure: str
) -> None:
    """Missing assets and failed routes are cosmetic, not control failures."""
    hass.http = Mock()
    hass.http.async_register_static_paths = AsyncMock(
        side_effect=RuntimeError("route unavailable") if failure == "register" else None
    )
    with patch("custom_components.dyson_fan.add_extra_js_url") as add_url:
        if failure == "read":
            with patch.object(
                Path, "read_bytes", side_effect=FileNotFoundError("icons.js")
            ):
                assert await async_setup(hass, {})
        else:
            assert await async_setup(hass, {})
    add_url.assert_not_called()
    assert caplog.text.count("using mdi:fan instead") == 1

    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_POWER_SENSOR: "sensor.dyson_power",
            **{key: [{"event": "dyson_test_action"}] for key in ACTION_KEYS},
        },
    )
    entry.add_to_hass(hass)
    with patch.object(hass.config_entries, "async_forward_entry_setups", AsyncMock()):
        assert await async_setup_entry(hass, entry)
    assert DysonFeedbackFan(entry).icon == "mdi:fan"
    assert await entry.runtime_data.controller._async_send_command(Command.SPEED_UP, 0)
    await entry.runtime_data.controller.async_shutdown()
