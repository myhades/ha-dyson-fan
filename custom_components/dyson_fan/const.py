"""Constants for the Dyson Fan integration."""

from __future__ import annotations

from enum import StrEnum
from typing import Final

from homeassistant.const import Platform

DOMAIN: Final = "dyson_fan"
DEFAULT_NAME: Final = "Dyson Fan"

PLATFORMS: Final = [Platform.FAN, Platform.SENSOR, Platform.BUTTON]

CONF_POWER_SENSOR: Final = "power_sensor"
CONF_POWER_TOGGLE_ACTION: Final = "power_toggle_action"
CONF_OSCILLATION_TOGGLE_ACTION: Final = "oscillation_toggle_action"
CONF_SPEED_UP_ACTION: Final = "speed_up_action"
CONF_SPEED_DOWN_ACTION: Final = "speed_down_action"
CONF_FEEDBACK_BURST_ACTION: Final = "feedback_burst_action"

ACTION_KEYS: Final = (
    CONF_POWER_TOGGLE_ACTION,
    CONF_OSCILLATION_TOGGLE_ACTION,
    CONF_SPEED_UP_ACTION,
    CONF_SPEED_DOWN_ACTION,
)

CONF_MAX_ATTEMPTS: Final = "max_attempts"
CONF_IR_SEND_INTERVAL: Final = "ir_send_interval"
CONF_CALIBRATION_MODE: Final = "calibration_mode"


class CalibrationMode(StrEnum):
    """Available automatic calibration strategies."""

    ENDPOINTS = "endpoints"
    FULL = "full"


DEFAULT_MAX_ATTEMPTS: Final = 1
DEFAULT_IR_SEND_INTERVAL: Final = 0.35
DEFAULT_CALIBRATION_MODE: Final = CalibrationMode.ENDPOINTS
MIN_IR_SEND_INTERVAL: Final = 0.05
MAX_IR_SEND_INTERVAL: Final = 3.0

# This integration controls non-heating Dyson fans. A reading above this limit is
# treated as a broken/wrong feedback source instead of being decoded to the
# nearest fan signature.
MAX_SANE_POWER_WATTS: Final = 100.0
POWER_TABLE_DECIMAL_PLACES: Final = 2

SPEED_COUNT: Final = 10
STABLE_REPORTS_REQUIRED: Final = 3
POST_COMMAND_SETTLE_SECONDS: Final = 1.0
FEEDBACK_TIMEOUT_SECONDS: Final = 15.0
FEEDBACK_BURST_TIMEOUT_SECONDS: Final = 5.0
EVENT_CALIBRATION_FINISHED: Final = f"{DOMAIN}_calibration_finished"
PERSIST_DELAY_SECONDS: Final = 5.0

STORAGE_VERSION: Final = 1
STORAGE_KEY_PREFIX: Final = f"{DOMAIN}.state"


class ControllerPhase(StrEnum):
    """Possible states of the feedback controller."""

    INITIALIZING = "initializing"
    IDLE = "idle"
    SENDING = "sending"
    WAITING_FEEDBACK = "waiting_feedback"
    CALIBRATING = "calibrating"
    ERROR = "error"


STATE_INITIALIZING: Final = ControllerPhase.INITIALIZING
STATE_IDLE: Final = ControllerPhase.IDLE
STATE_SENDING: Final = ControllerPhase.SENDING
STATE_WAITING_FEEDBACK: Final = ControllerPhase.WAITING_FEEDBACK
STATE_CALIBRATING: Final = ControllerPhase.CALIBRATING
STATE_ERROR: Final = ControllerPhase.ERROR

CALIBRATION_INITIAL_COMMANDS: Final = 15
CALIBRATION_IR_SEND_INTERVAL_SECONDS: Final = 1.0
CALIBRATION_PROBE_COMMANDS: Final = 2
CALIBRATION_UNCHANGED_PROBES: Final = 2
CALIBRATION_MAX_PROBES: Final = 6
CALIBRATION_MEASUREMENT_MIN_SAMPLES: Final = 3
CALIBRATION_MEASUREMENT_FAST_SAMPLES: Final = 5
CALIBRATION_SLOW_REPORT_INTERVAL_SECONDS: Final = 5.0
CALIBRATION_MIN_OBSERVATION_SECONDS: Final = 2.0
# Allows three fresh reports even when a meter updates only once every 10 seconds
# and the measurement starts immediately after one of its scheduled reports.
CALIBRATION_MEASUREMENT_TIMEOUT_SECONDS: Final = 35.0
CALIBRATION_MEASUREMENT_SETTLE_SECONDS: Final = 1.0
CALIBRATION_POWER_CYCLE_OFF_SECONDS: Final = 3.0
CALIBRATION_STABILITY_FLOOR_WATTS: Final = 0.6
CALIBRATION_STABILITY_RATIO: Final = 0.025
CALIBRATION_OUTLIER_FLOOR_WATTS: Final = 0.2
CALIBRATION_OUTLIER_RATIO: Final = 0.01
CALIBRATION_OUTLIER_MAD_MULTIPLIER: Final = 4.4478
CALIBRATION_ENDPOINT_CHANGE_FLOOR_WATTS: Final = 0.8
CALIBRATION_ENDPOINT_CHANGE_RATIO: Final = 0.02
CALIBRATION_FULL_STEP_FLOOR_WATTS: Final = 0.5
CALIBRATION_FULL_STEP_REFERENCE_RATIO: Final = 0.3
CALIBRATION_OSCILLATION_DELTA_MIN_WATTS: Final = 0.5
CALIBRATION_OSCILLATION_DELTA_MAX_WATTS: Final = 10.0
CALIBRATION_RESTORE_TIMEOUT_SECONDS: Final = 60.0


def power_signature_key(speed: int, oscillating: bool) -> str:
    """Return the options key for a power signature."""
    suffix = "oscillating" if oscillating else "stationary"
    return f"power_speed_{speed}_{suffix}"


CONF_POWER_OFF: Final = "power_off"
CONF_POWER_OSCILLATION_DELTA: Final = "power_oscillation_delta"

DEFAULT_POWER_OFF: Final = 1.2
DEFAULT_POWER_OSCILLATION_DELTA: Final = 2.9
DEFAULT_POWER_SIGNATURES: Final[dict[tuple[int, bool], float]] = {
    (1, False): 4.8,
    (1, True): 7.7,
    (2, False): 6.5,
    (2, True): 9.3,
    (3, False): 9.7,
    (3, True): 12.5,
    (4, False): 13.0,
    (4, True): 16.0,
    (5, False): 18.2,
    (5, True): 21.1,
    (6, False): 22.8,
    (6, True): 25.7,
    (7, False): 28.5,
    (7, True): 31.2,
    (8, False): 35.3,
    (8, True): 38.3,
    (9, False): 43.3,
    (9, True): 46.3,
    (10, False): 52.2,
    (10, True): 55.2,
}
