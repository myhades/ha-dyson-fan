"""Config and options flows for Dyson Fan."""

from __future__ import annotations

from copy import deepcopy
from itertools import pairwise
from typing import Any
from uuid import uuid4

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.config_entries import ConfigFlowResult
from homeassistant.const import ATTR_UNIT_OF_MEASUREMENT
from homeassistant.core import callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.script import async_validate_actions_config
from homeassistant.helpers.selector import (
    ActionSelector,
    EntitySelector,
    EntitySelectorConfig,
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
)

from .const import (
    ACTION_KEYS,
    CONF_CALIBRATION_MODE,
    CONF_FEEDBACK_BURST_ACTION,
    CONF_IR_SEND_INTERVAL,
    CONF_MAX_ATTEMPTS,
    CONF_OSCILLATION_TOGGLE_ACTION,
    CONF_POWER_OFF,
    CONF_POWER_OSCILLATION_DELTA,
    CONF_POWER_SENSOR,
    CONF_POWER_TOGGLE_ACTION,
    CONF_RESTORE_DEFAULT_POWER_TABLE,
    CONF_SPEED_DOWN_ACTION,
    CONF_SPEED_UP_ACTION,
    DEFAULT_CALIBRATION_MODE,
    DEFAULT_IR_SEND_INTERVAL,
    DEFAULT_MAX_ATTEMPTS,
    DEFAULT_NAME,
    DOMAIN,
    MAX_IR_SEND_INTERVAL,
    MIN_IR_SEND_INTERVAL,
    SPEED_COUNT,
    CalibrationMode,
    power_signature_key,
)
from .power import (
    InvalidPowerReading,
    PowerSignatureTable,
    merge_power_table_options,
    power_to_watts,
)


def _configuration_schema(suggested: dict[str, Any] | None = None) -> vol.Schema:
    """Build the setup/reconfigure schema with optional suggested values."""
    suggested = suggested or {}

    def marker(key: str, *, optional: bool = False) -> vol.Marker:
        description = {"suggested_value": suggested[key]} if key in suggested else None
        marker_class = vol.Optional if optional else vol.Required
        return marker_class(key, description=description)

    return vol.Schema(
        {
            marker(CONF_POWER_SENSOR): EntitySelector(
                EntitySelectorConfig(domain="sensor")
            ),
            marker(CONF_POWER_TOGGLE_ACTION): ActionSelector(),
            marker(CONF_OSCILLATION_TOGGLE_ACTION): ActionSelector(),
            marker(CONF_SPEED_UP_ACTION): ActionSelector(),
            marker(CONF_SPEED_DOWN_ACTION): ActionSelector(),
            marker(CONF_FEEDBACK_BURST_ACTION, optional=True): ActionSelector(),
        }
    )


async def _async_validate_actions(
    hass: Any, user_input: dict[str, Any]
) -> dict[str, str]:
    """Validate every user-selected HA action sequence."""
    errors: dict[str, str] = {}
    if state := hass.states.get(user_input[CONF_POWER_SENSOR]):
        try:
            power_to_watts(0, state.attributes.get(ATTR_UNIT_OF_MEASUREMENT))
        except InvalidPowerReading:
            errors[CONF_POWER_SENSOR] = "invalid_power_unit"
    for key in ACTION_KEYS:
        value = user_input.get(key)
        sequence = [value] if isinstance(value, dict) else value
        if not isinstance(sequence, list) or not sequence:
            errors[key] = "invalid_action"
            continue
        try:
            await async_validate_actions_config(hass, deepcopy(sequence))
        except HomeAssistantError, ValueError, vol.Invalid:
            errors[key] = "invalid_action"
    if value := user_input.get(CONF_FEEDBACK_BURST_ACTION):
        sequence = [value] if isinstance(value, dict) else value
        if not isinstance(sequence, list) or not sequence:
            errors[CONF_FEEDBACK_BURST_ACTION] = "invalid_action"
        else:
            try:
                await async_validate_actions_config(hass, deepcopy(sequence))
            except HomeAssistantError, ValueError, vol.Invalid:
                errors[CONF_FEEDBACK_BURST_ACTION] = "invalid_action"
    return errors


class DysonFanConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle Dyson Fan setup and reconfiguration."""

    VERSION = 3

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Set up a fan from a power sensor and four HA actions."""
        errors: dict[str, str] = {}
        if user_input is not None:
            errors = await _async_validate_actions(self.hass, user_input)
            if not errors:
                await self.async_set_unique_id(uuid4().hex)
                return self.async_create_entry(
                    title=DEFAULT_NAME,
                    data=user_input,
                    options={
                        CONF_MAX_ATTEMPTS: DEFAULT_MAX_ATTEMPTS,
                        CONF_IR_SEND_INTERVAL: DEFAULT_IR_SEND_INTERVAL,
                        CONF_CALIBRATION_MODE: DEFAULT_CALIBRATION_MODE,
                    },
                )

        return self.async_show_form(
            step_id="user",
            data_schema=_configuration_schema(),
            errors=errors,
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Change the power sensor and Home Assistant actions."""
        entry = self._get_reconfigure_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            errors = await _async_validate_actions(self.hass, user_input)
            if not errors:
                await self.async_set_unique_id(entry.unique_id)
                self._abort_if_unique_id_mismatch()
                return self.async_update_reload_and_abort(entry, data=user_input)

        return self.async_show_form(
            step_id="reconfigure",
            data_schema=_configuration_schema(dict(entry.data)),
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> DysonFanOptionsFlow:
        """Create the options flow."""
        return DysonFanOptionsFlow()


class DysonFanOptionsFlow(config_entries.OptionsFlowWithReload):
    """Manage the small set of user-tunable controller options."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Show an options menu."""
        return self.async_show_menu(
            step_id="init",
            menu_options=["control", "power_table"],
        )

    async def async_step_control(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Configure retry count and the IR send interval."""
        if user_input is not None:
            options = dict(self.config_entry.options)
            options.update(user_input)
            return self.async_create_entry(data=options)

        schema = vol.Schema(
            {
                vol.Required(
                    CONF_MAX_ATTEMPTS,
                    default=int(
                        self.config_entry.options.get(
                            CONF_MAX_ATTEMPTS, DEFAULT_MAX_ATTEMPTS
                        )
                    ),
                ): NumberSelector(
                    NumberSelectorConfig(
                        min=1,
                        max=5,
                        step=1,
                        mode=NumberSelectorMode.BOX,
                    )
                ),
                vol.Required(
                    CONF_IR_SEND_INTERVAL,
                    default=float(
                        self.config_entry.options.get(
                            CONF_IR_SEND_INTERVAL, DEFAULT_IR_SEND_INTERVAL
                        )
                    ),
                ): NumberSelector(
                    NumberSelectorConfig(
                        min=MIN_IR_SEND_INTERVAL,
                        max=MAX_IR_SEND_INTERVAL,
                        step=0.05,
                        unit_of_measurement="s",
                        mode=NumberSelectorMode.BOX,
                    )
                ),
                vol.Required(
                    CONF_CALIBRATION_MODE,
                    default=str(
                        self.config_entry.options.get(
                            CONF_CALIBRATION_MODE, DEFAULT_CALIBRATION_MODE
                        )
                    ),
                ): SelectSelector(
                    SelectSelectorConfig(
                        options=[mode.value for mode in CalibrationMode],
                        mode=SelectSelectorMode.DROPDOWN,
                        translation_key="calibration_mode",
                    )
                ),
            }
        )
        return self.async_show_form(step_id="control", data_schema=schema)

    async def async_step_power_table(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Edit off and stationary power with one oscillation increment."""
        errors: dict[str, str] = {}
        if user_input is not None:
            if user_input.get(CONF_RESTORE_DEFAULT_POWER_TABLE) is True:
                options = merge_power_table_options(
                    self.config_entry.options, PowerSignatureTable.from_options({})
                )
                return self.async_create_entry(data=options)
            try:
                table = _validate_power_table(user_input)
            except vol.Invalid:
                errors["base"] = "invalid_power_table"
            else:
                options = merge_power_table_options(self.config_entry.options, table)
                return self.async_create_entry(data=options)

        defaults = PowerSignatureTable.from_options(
            self.config_entry.options
        ).as_options()
        # Optional UI fields allow saving a reset even if a number was cleared.
        # Their defaults still preserve existing values for normal submissions.
        fields: dict[vol.Marker, NumberSelector | type[bool]] = {
            vol.Optional(CONF_RESTORE_DEFAULT_POWER_TABLE, default=False): bool,
            vol.Optional(
                CONF_POWER_OSCILLATION_DELTA,
                default=defaults[CONF_POWER_OSCILLATION_DELTA],
            ): _power_input(),
            vol.Optional(
                CONF_POWER_OFF, default=defaults[CONF_POWER_OFF]
            ): _power_input(),
        }
        for speed in range(1, SPEED_COUNT + 1):
            key = power_signature_key(speed, False)
            fields[vol.Optional(key, default=defaults[key])] = _power_input()

        return self.async_show_form(
            step_id="power_table",
            data_schema=_PowerTableSchema(fields),
            errors=errors,
        )


class _PowerTableSchema(vol.Schema):
    """Discard ignored power fields before HA validates their number selectors."""

    def __call__(self, data: Any) -> dict[str, Any]:
        if (
            isinstance(data, dict)
            and data.get(CONF_RESTORE_DEFAULT_POWER_TABLE) is True
        ):
            return {CONF_RESTORE_DEFAULT_POWER_TABLE: True}
        return super().__call__(data)


def _power_input() -> NumberSelector:
    """Return the standard wattage input selector."""
    return NumberSelector(
        NumberSelectorConfig(
            min=0,
            max=100,
            step=0.01,
            unit_of_measurement="W",
            mode=NumberSelectorMode.BOX,
        )
    )


def _validate_power_table(values: dict[str, Any]) -> PowerSignatureTable:
    """Normalize and validate a signature table at two-decimal precision."""
    table = PowerSignatureTable.from_options(values)
    stationary = [table.speeds[(speed, False)] for speed in range(1, 11)]
    if table.off >= stationary[0]:
        raise vol.Invalid("Off power must be lower than speed 1")
    if any(left >= right for left, right in pairwise(stationary)):
        raise vol.Invalid("Stationary signatures must increase with speed")
    if table.oscillation_delta <= 0:
        raise vol.Invalid("Oscillation power increment must be positive")
    if table.speeds[(SPEED_COUNT, True)] >= 100:
        raise vol.Invalid("Oscillating power must remain below 100 W")
    return table
