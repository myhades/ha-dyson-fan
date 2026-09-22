"""Diagnostic telemetry must not turn fast feedback into excessive state writes."""

from types import SimpleNamespace
from unittest.mock import Mock

from homeassistant.core import HomeAssistant

from custom_components.dyson_fan.sensor import DysonFanDiagnosticsSensor


async def test_raw_reports_are_throttled_but_calibration_result_is_immediate(
    hass: HomeAssistant,
) -> None:
    controller = SimpleNamespace(
        phase="idle",
        calibrating=False,
        calibration_result=None,
        calibration_step=None,
    )
    entry = SimpleNamespace(
        entry_id="test",
        title="Test",
        runtime_data=SimpleNamespace(controller=controller),
    )
    sensor = DysonFanDiagnosticsSensor(entry)
    sensor.hass = hass
    sensor.async_write_ha_state = Mock()
    for _ in range(100):
        sensor._async_controller_updated()
    assert sensor.async_write_ha_state.call_count == 1
    controller.calibration_result = "success"
    sensor._async_controller_updated()
    assert sensor.async_write_ha_state.call_count == 2
    assert sensor._pending_update is None
    assert not sensor.should_poll
