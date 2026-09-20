"""Timing and recent sample windows shared by feedback and calibration."""

from __future__ import annotations

from collections import deque
from itertools import pairwise
from statistics import median

from .calibration import robust_power_average
from .const import (
    CALIBRATION_MEASUREMENT_FAST_SAMPLES,
    CALIBRATION_MEASUREMENT_MIN_SAMPLES,
    CALIBRATION_MIN_OBSERVATION_SECONDS,
    CALIBRATION_SLOW_REPORT_INTERVAL_SECONDS,
)


class ReportCadence:
    """Estimate current reporting speed, including changes caused by burst."""

    def __init__(self) -> None:
        self._timestamps: deque[float] = deque(maxlen=5)

    def add(self, timestamp: float) -> None:
        """Observe an actual report, including reports with unchanged power."""
        self._timestamps.append(timestamp)

    @property
    def interval(self) -> float | None:
        """Use recent intervals so a previous burst cannot dominate indefinitely."""
        return report_interval(list(self._timestamps))

    def timeout(self, minimum: float, reports: int) -> float:
        """Budget enough time for reports without delaying a successful result."""
        return min(180.0, max(minimum, (self.interval or 0) * reports + 5.0))


def report_interval(timestamps: list[float]) -> float | None:
    """React immediately when a burst ends, smoothing acceleration over two reports."""
    intervals = [
        right - left for left, right in pairwise(timestamps[-5:]) if right > left
    ]
    if not intervals:
        return None
    return max(intervals[-1], float(median(intervals[-3:])))


def stable_window_average(samples: list[tuple[float, float]]) -> float | None:
    """Average the newest stable interval, never the whole transition history."""
    if len(samples) < CALIBRATION_MEASUREMENT_MIN_SAMPLES:
        return None
    cadence = report_interval([timestamp for timestamp, _ in samples[-5:]]) or 0
    required = (
        CALIBRATION_MEASUREMENT_MIN_SAMPLES
        if cadence >= CALIBRATION_SLOW_REPORT_INTERVAL_SECONDS
        else CALIBRATION_MEASUREMENT_FAST_SAMPLES
    )
    if len(samples) < required:
        return None
    # Fast meters observe at least two seconds; slow meters need only three
    # recent reports. Include the sample immediately before the time boundary.
    first = len(samples) - required
    while (
        first > 0
        and samples[-1][0] - samples[first][0] < CALIBRATION_MIN_OBSERVATION_SECONDS
    ):
        first -= 1
    window = samples[first:]
    if window[-1][0] - window[0][0] < CALIBRATION_MIN_OBSERVATION_SECONDS:
        return None
    return robust_power_average([watts for _, watts in window])
