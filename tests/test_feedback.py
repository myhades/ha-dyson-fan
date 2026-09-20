"""Sampling windows and timeout budgets across reporting speeds."""

import pytest

from custom_components.dyson_fan.calibration import robust_power_average
from custom_components.dyson_fan.feedback import ReportCadence, stable_window_average


def test_changing_tail_is_not_discarded_as_an_outlier() -> None:
    """A new power plateau must not be mistaken for a transient historical spike."""
    assert robust_power_average([4.8, 4.8, 4.8, 4.8, 6.5]) is None
    assert robust_power_average([4.8, 4.8, 6.5]) is None
    assert robust_power_average([6.5, 40.0, 6.4, 6.5, 6.6]) == 6.5


@pytest.mark.parametrize("interval", [0.25, 1 / 3, 1.0, 10.0])
def test_recent_window_recovers_after_transition(interval: float) -> None:
    """A long old plateau cannot prevent accepting a newer settled plateau."""
    count = max(5, int(3 / interval) + 1)
    samples = [(i * interval, 4.8) for i in range(count)]
    samples.append((count * interval, 6.5))
    assert stable_window_average(samples) is None
    samples.extend(((count + i) * interval, 6.5) for i in range(1, count))
    assert stable_window_average(samples) == 6.5


def test_burst_cadence_does_not_delay_fast_feedback_or_starve_slow_feedback() -> None:
    """A device changing cadence in either direction gets a current time budget."""
    cadence = ReportCadence()
    for timestamp in (0, 10, 20):
        cadence.add(timestamp)
    assert cadence.timeout(15, 3) >= 35
    for timestamp in (20.25, 20.5, 20.75, 21):
        cadence.add(timestamp)
    assert cadence.timeout(15, 3) == 15
    cadence.add(31)
    assert cadence.timeout(15, 3) >= 35


def test_fast_meter_must_observe_time_not_only_count() -> None:
    assert stable_window_average([(i * 0.01, 6.5) for i in range(20)]) is None
    assert stable_window_average([(i * 0.25, 6.5) for i in range(9)]) == 6.5


def test_slow_meter_only_needs_three_recent_stable_reports() -> None:
    assert stable_window_average([(10, 6.5), (20, 6.6), (30, 6.4)]) == 6.5
