"""Opt-in diagnostic sensor for Dyson Fan."""

from __future__ import annotations

from time import monotonic
from typing import Any, ClassVar

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.const import EntityCategory
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import async_call_later

from . import DysonFanConfigEntry
from .const import DIAGNOSTICS_UPDATE_INTERVAL_SECONDS, ControllerPhase
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
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options: ClassVar[list[str]] = [phase.value for phase in ControllerPhase]
    _unrecorded_attributes = frozenset(
        {
            "last_read",
            "last_operation",
            "last_confirmation",
            "last_decoded",
            "stable_report_count",
            "target_revision",
            "handled_revision",
            "calibration_measurements",
            "report_interval",
        }
    )

    def __init__(self, entry: DysonFanConfigEntry) -> None:
        """Initialize the diagnostics entity."""
        super().__init__(entry)
        self._attr_unique_id = f"{entry.entry_id}_diagnostics"
        self._last_diagnostic_write = 0.0
        self._pending_update: CALLBACK_TYPE | None = None
        self._last_status: tuple[object, ...] | None = None

    async def async_added_to_hass(self) -> None:
        """Also cancel a scheduled diagnostics write when the entity is removed."""
        await super().async_added_to_hass()
        self.async_on_remove(self._cancel_pending_update)

    @callback
    def _cancel_pending_update(self) -> None:
        if self._pending_update is not None:
            self._pending_update()
            self._pending_update = None

    @callback
    def _async_controller_updated(self) -> None:
        """Throttle raw telemetry while publishing important state changes at once."""
        status = (
            self.controller.phase,
            self.controller.calibrating,
            self.controller.calibration_result,
            self.controller.calibration_step,
            self.controller.can_undo_calibration,
        )
        remaining = DIAGNOSTICS_UPDATE_INTERVAL_SECONDS - (
            monotonic() - self._last_diagnostic_write
        )
        if status != self._last_status or remaining <= 0:
            self._last_status = status
            self._cancel_pending_update()
            self._write_diagnostics()
        elif self._pending_update is None:
            self._pending_update = async_call_later(
                self.hass, remaining, self._write_diagnostics
            )

    @callback
    def _write_diagnostics(self, *_: object) -> None:
        self._pending_update = None
        self._last_diagnostic_write = monotonic()
        self.async_write_ha_state()

    @property
    def native_value(self) -> ControllerPhase:
        """Return the current controller phase."""
        return self.controller.phase

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return detailed state-machine diagnostics."""
        return self.controller.diagnostics()
