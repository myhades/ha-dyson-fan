"""Tests for power decoding and stable feedback."""

from __future__ import annotations

import pytest

from custom_components.dyson_fan.const import DEFAULT_POWER_SIGNATURES
from custom_components.dyson_fan.models import FanState
from custom_components.dyson_fan.power import (
    InvalidPowerReading,
    PowerDecoder,
    PowerSignatureTable,
    StablePowerTracker,
)


@pytest.fixture
def decoder() -> PowerDecoder:
    """Return a decoder using the built-in Dyson table."""
    return PowerDecoder(PowerSignatureTable.from_options({}))


@pytest.mark.parametrize(("speed", "oscillating"), DEFAULT_POWER_SIGNATURES.keys())
def test_exact_signatures(decoder: PowerDecoder, speed: int, oscillating: bool) -> None:
    """Every built-in signature maps back to its physical state."""
    watts = DEFAULT_POWER_SIGNATURES[(speed, oscillating)]
    assert decoder.decode(watts).state == FanState(True, speed, oscillating)


def test_off_and_negative_meter_direction(decoder: PowerDecoder) -> None:
    """Off decodes correctly and reversed meters are treated as absolute power."""
    assert decoder.decode(1.2).state == FanState(False, None, False)
    assert decoder.decode(-18.2).state == FanState(True, 5, False)


@pytest.mark.parametrize("value", ["unknown", None, float("nan"), 100.1, -500])
def test_invalid_or_unsafe_power(decoder: PowerDecoder, value: object) -> None:
    """Non-numeric and greater-than-100 W readings are rejected."""
    with pytest.raises(InvalidPowerReading):
        decoder.decode(value)


def test_stability_uses_reports_not_watt_equality(decoder: PowerDecoder) -> None:
    """Slightly different wattages can confirm the same decoded physical state."""
    tracker = StablePowerTracker(3)
    assert tracker.add(decoder.decode(18.0)) is None
    assert tracker.add(decoder.decode(18.2)) is None
    stable = tracker.add(decoder.decode(18.4))
    assert stable is not None
    assert stable.decoded.state == FanState(True, 5, False)
    assert tracker.add(decoder.decode(18.3)) is None


def test_stability_resets_when_decoded_state_changes(decoder: PowerDecoder) -> None:
    """A different decoded state starts a new consecutive report run."""
    tracker = StablePowerTracker(2)
    assert tracker.add(decoder.decode(18.2)) is None
    assert tracker.add(decoder.decode(22.8)) is None
    stable = tracker.add(decoder.decode(22.9))
    assert stable is not None
    assert stable.decoded.state.speed == 6
