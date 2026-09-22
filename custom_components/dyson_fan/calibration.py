"""Power-table calibration math for Dyson Fan."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from itertools import pairwise
from math import ceil
from statistics import fmean, median

from .const import (
    CALIBRATION_OUTLIER_FLOOR_WATTS,
    CALIBRATION_OUTLIER_MAD_MULTIPLIER,
    CALIBRATION_OUTLIER_RATIO,
    CALIBRATION_STABILITY_FLOOR_WATTS,
    CALIBRATION_STABILITY_RATIO,
    MAX_SANE_POWER_WATTS,
    SPEED_COUNT,
)
from .power import PowerSignatureTable, round_power_watts

MIN_CALIBRATION_SPAN_WATTS = 20.0
MIN_ENDPOINT_FACTOR = 0.5
MAX_ENDPOINT_FACTOR = 1.75

CALIBRATION_REFERENCE_TABLE = PowerSignatureTable.from_options({})


class CalibrationError(RuntimeError):
    """Raised when calibration cannot safely update the power table."""


class CalibrationCancelled(CalibrationError):
    """Raised when a user command supersedes calibration."""


def robust_power_average(samples: list[float]) -> float | None:
    """Reject outliers and average a stable set of power samples."""
    if len(samples) < 3:
        return None
    center = float(median(samples))
    mad = float(median(abs(sample - center) for sample in samples))
    outlier_limit = max(
        CALIBRATION_OUTLIER_FLOOR_WATTS,
        abs(center) * CALIBRATION_OUTLIER_RATIO,
        mad * CALIBRATION_OUTLIER_MAD_MULTIPLIER,
    )
    retained = [sample for sample in samples if abs(sample - center) <= outlier_limit]
    if len(retained) < max(3, ceil(len(samples) * 0.6)):
        return None
    # A changing tail is not a historical outlier: wait for the new plateau.
    if any(abs(sample - center) > outlier_limit for sample in samples[-2:]):
        return None
    measured = float(fmean(retained))
    stability_limit = max(
        CALIBRATION_STABILITY_FLOOR_WATTS,
        abs(measured) * CALIBRATION_STABILITY_RATIO,
    )
    if max(retained) - min(retained) > stability_limit:
        return None
    return round_power_watts(measured)


@dataclass(frozen=True, slots=True)
class CalibrationResult:
    """A validated table measured directly or projected from the active curve."""

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
    *,
    oscillation_delta: float | None = None,
    reference_table: PowerSignatureTable = CALIBRATION_REFERENCE_TABLE,
) -> CalibrationResult:
    """Shift and scale the active stationary curve onto measured endpoints."""
    reference_low = reference_table.speeds[(1, False)]
    reference_high = reference_table.speeds[(10, False)]
    oscillation_delta = round_power_watts(
        reference_table.oscillation_delta
        if oscillation_delta is None
        else oscillation_delta
    )

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
    stationary = {
        speed: round_power_watts(
            speed_1_watts
            + (
                (reference_table.speeds[(speed, False)] - reference_low)
                / (reference_high - reference_low)
            )
            * (speed_10_watts - speed_1_watts)
        )
        for speed in range(1, SPEED_COUNT + 1)
    }
    table = _build_table(off_watts, stationary, oscillation_delta)
    return CalibrationResult(
        table=table,
        off_watts=off_watts,
        speed_1_watts=speed_1_watts,
        speed_10_watts=speed_10_watts,
        scale=scale,
        offset=offset,
    )


def build_full_calibrated_table(
    off_watts: float,
    stationary_watts: Mapping[int, float],
    oscillation_delta: float,
    *,
    reference_table: PowerSignatureTable = CALIBRATION_REFERENCE_TABLE,
) -> CalibrationResult:
    """Build a table from measurements of every stationary speed."""
    if set(stationary_watts) != set(range(1, SPEED_COUNT + 1)):
        raise CalibrationError("Full calibration requires speeds 1 through 10")
    stationary = {
        speed: round_power_watts(stationary_watts[speed])
        for speed in range(1, SPEED_COUNT + 1)
    }
    table = _build_table(off_watts, stationary, oscillation_delta)
    reference_low = reference_table.speeds[(1, False)]
    reference_high = reference_table.speeds[(10, False)]
    scale = (stationary[10] - stationary[1]) / (reference_high - reference_low)
    offset = stationary[1] - scale * reference_low
    return CalibrationResult(
        table=table,
        off_watts=round_power_watts(off_watts),
        speed_1_watts=stationary[1],
        speed_10_watts=stationary[10],
        scale=scale,
        offset=offset,
    )


def _build_table(
    off_watts: float,
    stationary: Mapping[int, float],
    oscillation_delta: float,
) -> PowerSignatureTable:
    """Validate stationary measurements and add one shared oscillation delta."""
    off_watts = round_power_watts(off_watts)
    oscillation_delta = round_power_watts(oscillation_delta)
    values = [stationary[speed] for speed in range(1, SPEED_COUNT + 1)]
    if not 0 <= off_watts < values[0] < values[-1] < MAX_SANE_POWER_WATTS:
        raise CalibrationError(
            "Calibration requires 0 <= off < speed 1 < speed 10 < 100 W"
        )
    if values[0] - off_watts < 0.5:
        raise CalibrationError("Speed 1 is not sufficiently above off power")
    if values[-1] - values[0] < MIN_CALIBRATION_SPAN_WATTS:
        raise CalibrationError("The measured speed range is too small")
    if any(left >= right for left, right in pairwise(values)):
        raise CalibrationError("Stationary power must increase with speed")
    if oscillation_delta <= 0:
        raise CalibrationError("Oscillation power increment must be positive")

    speeds = {
        (speed, oscillating): round_power_watts(
            stationary[speed] + (oscillation_delta if oscillating else 0)
        )
        for speed in range(1, SPEED_COUNT + 1)
        for oscillating in (False, True)
    }
    if any(not 0 < watts < MAX_SANE_POWER_WATTS for watts in speeds.values()):
        raise CalibrationError("The transformed power table exceeds safe limits")
    return PowerSignatureTable(off=off_watts, speeds=speeds)
