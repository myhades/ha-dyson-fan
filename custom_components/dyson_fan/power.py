"""Power unit normalization, signature decoding, and stability tracking."""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from statistics import median

from homeassistant.util.unit_conversion import PowerConverter

from .const import (
    CONF_POWER_OFF,
    CONF_POWER_OSCILLATION_DELTA,
    DEFAULT_POWER_OFF,
    DEFAULT_POWER_OSCILLATION_DELTA,
    DEFAULT_POWER_SIGNATURES,
    MAX_SANE_POWER_WATTS,
    POWER_TABLE_DECIMAL_PLACES,
    SPEED_COUNT,
    power_signature_key,
)
from .models import DecodedPower, FanState, StableObservation


class InvalidPowerReading(ValueError):
    """Raised when a power reading cannot be used as feedback."""


def power_to_watts(value: object, unit: str | None) -> float:
    """Normalize power units, assuming watts only when the unit is absent or blank."""
    if unit is None or (isinstance(unit, str) and not unit.strip()):
        unit = "W"
    if unit not in PowerConverter.VALID_UNITS:
        raise InvalidPowerReading(f"Unsupported power unit: {unit!r}")
    return PowerConverter.convert(_finite_float(value), unit, "W")


@dataclass(frozen=True, slots=True)
class PowerSignatureTable:
    """Expected wattage for every observable fan state."""

    off: float
    speeds: Mapping[tuple[int, bool], float]

    @classmethod
    def from_options(cls, options: Mapping[str, object]) -> PowerSignatureTable:
        """Build a signature table from config entry options."""
        off = round_power_watts(options.get(CONF_POWER_OFF, DEFAULT_POWER_OFF))
        stationary = {
            speed: round_power_watts(
                options.get(
                    power_signature_key(speed, False),
                    DEFAULT_POWER_SIGNATURES[(speed, False)],
                )
            )
            for speed in range(1, SPEED_COUNT + 1)
        }
        if CONF_POWER_OSCILLATION_DELTA in options:
            oscillation_delta = round_power_watts(options[CONF_POWER_OSCILLATION_DELTA])
        else:
            legacy_deltas = [
                round_power_watts(options[power_signature_key(speed, True)])
                - stationary[speed]
                for speed in range(1, SPEED_COUNT + 1)
                if power_signature_key(speed, True) in options
            ]
            oscillation_delta = round_power_watts(
                median(legacy_deltas)
                if legacy_deltas
                else DEFAULT_POWER_OSCILLATION_DELTA
            )
        speeds = {
            (speed, oscillating): round_power_watts(
                stationary[speed] + (oscillation_delta if oscillating else 0)
            )
            for speed in range(1, SPEED_COUNT + 1)
            for oscillating in (False, True)
        }
        return cls(off=off, speeds=speeds)

    @property
    def oscillation_delta(self) -> float:
        """Return the shared oscillation power increment."""
        return round_power_watts(
            median(
                self.speeds[(speed, True)] - self.speeds[(speed, False)]
                for speed in range(1, SPEED_COUNT + 1)
            )
        )

    def as_options(self) -> dict[str, float]:
        """Return the table in config-entry options format."""
        result = {
            CONF_POWER_OSCILLATION_DELTA: self.oscillation_delta,
            CONF_POWER_OFF: round_power_watts(self.off),
        }
        result.update(
            {
                power_signature_key(speed, False): round_power_watts(
                    self.speeds[(speed, False)]
                )
                for speed in range(1, SPEED_COUNT + 1)
            }
        )
        return result


def merge_power_table_options(
    options: Mapping[str, object], table: PowerSignatureTable
) -> dict[str, object]:
    """Replace every current or legacy power-table option atomically."""
    result = dict(options)
    result.pop(CONF_POWER_OSCILLATION_DELTA, None)
    result.pop(CONF_POWER_OFF, None)
    for speed in range(1, SPEED_COUNT + 1):
        for oscillating in (False, True):
            result.pop(power_signature_key(speed, oscillating), None)
    result.update(table.as_options())
    return result


class PowerDecoder:
    """Decode power measurements using nearest-neighbour signatures."""

    def __init__(self, table: PowerSignatureTable) -> None:
        """Initialize the decoder."""
        self.table = table

    def decode(self, value: object) -> DecodedPower:
        """Decode a numeric sensor value into a physical fan state."""
        watts = abs(_finite_float(value))
        if watts > MAX_SANE_POWER_WATTS:
            raise InvalidPowerReading(
                f"Power reading {watts:.3f} W exceeds the 100 W safety limit"
            )

        candidates: list[tuple[float, FanState]] = [
            (self.table.off, FanState(False, None, False))
        ]
        candidates.extend(
            (
                signature,
                FanState(True, speed, oscillating),
            )
            for (speed, oscillating), signature in self.table.speeds.items()
        )
        signature, state = min(candidates, key=lambda item: abs(item[0] - watts))
        return DecodedPower(
            watts=watts,
            state=state,
            signature_watts=signature,
            difference_watts=abs(signature - watts),
        )


class StablePowerTracker:
    """Confirm a decoded state after repeated matching sensor reports."""

    def __init__(self, required_reports: int) -> None:
        """Initialize the tracker."""
        if required_reports < 1:
            raise ValueError("required_reports must be positive")
        self.required_reports = required_reports
        self.candidate: DecodedPower | None = None
        self.count = 0
        self._emitted = False

    def reset(self) -> None:
        """Forget all samples in the current feedback window."""
        self.candidate = None
        self.count = 0
        self._emitted = False

    def add(self, decoded: DecodedPower) -> StableObservation | None:
        """Add a report and return an observation once it becomes stable."""
        if self.candidate is None or self.candidate.state != decoded.state:
            self.candidate = decoded
            self.count = 1
            self._emitted = False
        else:
            # Retain the newest raw wattage and residual for diagnostics.
            self.candidate = decoded
            self.count += 1

        if self.count < self.required_reports or self._emitted:
            return None

        self._emitted = True
        return StableObservation(decoded=self.candidate, report_count=self.count)


def _finite_float(value: object) -> float:
    """Coerce a value to a finite float."""
    try:
        result = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError) as err:
        raise InvalidPowerReading(f"Invalid power reading: {value!r}") from err
    if not math.isfinite(result):
        raise InvalidPowerReading(f"Power reading is not finite: {value!r}")
    return result


def round_power_watts(value: object) -> float:
    """Return a finite wattage rounded to the power-table precision."""
    return round(_finite_float(value), POWER_TABLE_DECIMAL_PLACES)
