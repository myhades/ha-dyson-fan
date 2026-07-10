"""Power-table calibration math for Dyson Fan."""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType

from .const import (
    DEFAULT_POWER_OFF,
    DEFAULT_POWER_SIGNATURES,
    MAX_SANE_POWER_WATTS,
)
from .power import PowerSignatureTable

MIN_CALIBRATION_SPAN_WATTS = 20.0
MIN_ENDPOINT_FACTOR = 0.5
MAX_ENDPOINT_FACTOR = 1.75

# Always calibrate from this immutable factory curve. Using the active table here
# would compound manual edits and previous calibration runs.
CALIBRATION_REFERENCE_TABLE = PowerSignatureTable(
    off=DEFAULT_POWER_OFF,
    speeds=MappingProxyType(dict(DEFAULT_POWER_SIGNATURES)),
)


class CalibrationError(RuntimeError):
    """Raised when calibration cannot safely update the power table."""


class CalibrationCancelled(CalibrationError):
    """Raised when a user command supersedes calibration."""


@dataclass(frozen=True, slots=True)
class CalibrationResult:
    """A validated projection of the built-in non-linear reference curve."""

    table: PowerSignatureTable
    off_watts: float
    speed_1_watts: float
    speed_10_watts: float
    scale: float
    offset: float


def build_calibrated_table(
    off_watts: float,
    speed_1_watts: float,
    speed_10_watts: float,
) -> CalibrationResult:
    """Map the immutable non-linear reference curve onto measured endpoints."""
    reference_low = CALIBRATION_REFERENCE_TABLE.speeds[(1, False)]
    reference_high = CALIBRATION_REFERENCE_TABLE.speeds[(10, False)]

    if not 0 <= off_watts < speed_1_watts < speed_10_watts < MAX_SANE_POWER_WATTS:
        raise CalibrationError(
            "Calibration requires 0 <= off < speed 1 < speed 10 < 100 W"
        )
    if speed_1_watts - off_watts < 0.5:
        raise CalibrationError("Speed 1 is not sufficiently above off power")
    if speed_10_watts - speed_1_watts < MIN_CALIBRATION_SPAN_WATTS:
        raise CalibrationError("The measured speed range is too small")
    if not (
        reference_low * MIN_ENDPOINT_FACTOR
        <= speed_1_watts
        <= reference_low * MAX_ENDPOINT_FACTOR
    ):
        raise CalibrationError("The measured speed 1 power is outside the safe range")
    if not (
        reference_high * MIN_ENDPOINT_FACTOR
        <= speed_10_watts
        <= min(reference_high * MAX_ENDPOINT_FACTOR, MAX_SANE_POWER_WATTS)
    ):
        raise CalibrationError("The measured speed 10 power is outside the safe range")

    scale = (speed_10_watts - speed_1_watts) / (reference_high - reference_low)
    offset = speed_1_watts - scale * reference_low
    transformed = {
        state: speed_1_watts
        + ((watts - reference_low) / (reference_high - reference_low))
        * (speed_10_watts - speed_1_watts)
        for state, watts in CALIBRATION_REFERENCE_TABLE.speeds.items()
    }
    if any(not 0 < watts < MAX_SANE_POWER_WATTS for watts in transformed.values()):
        raise CalibrationError("The transformed power table exceeds safe limits")

    table = PowerSignatureTable(off=off_watts, speeds=transformed)
    return CalibrationResult(
        table=table,
        off_watts=off_watts,
        speed_1_watts=speed_1_watts,
        speed_10_watts=speed_10_watts,
        scale=scale,
        offset=offset,
    )
