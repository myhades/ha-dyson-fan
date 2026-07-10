"""Runtime models for Dyson Fan."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any


@dataclass(frozen=True, slots=True)
class FanState:
    """A feedback-confirmed or supposed physical fan state."""

    power: bool
    speed: int | None
    oscillating: bool

    def as_dict(self) -> dict[str, bool | int | None]:
        """Return a JSON-serializable representation."""
        return {
            "power": self.power,
            "speed": self.speed,
            "oscillating": self.oscillating,
        }


@dataclass(frozen=True, slots=True)
class TargetState:
    """The latest state requested by Home Assistant."""

    power: bool
    speed: int | None
    oscillating: bool

    def as_dict(self) -> dict[str, bool | int | None]:
        """Return a JSON-serializable representation."""
        return {
            "power": self.power,
            "speed": self.speed,
            "oscillating": self.oscillating,
        }


@dataclass(frozen=True, slots=True)
class DecodedPower:
    """The fan state nearest to a power reading."""

    watts: float
    state: FanState
    signature_watts: float
    difference_watts: float

    def as_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable representation."""
        return {
            "watts": self.watts,
            "state": self.state.as_dict(),
            "signature_watts": self.signature_watts,
            "difference_watts": self.difference_watts,
        }


class Command(StrEnum):
    """Relative infrared commands understood by a Dyson fan."""

    POWER_TOGGLE = "power_toggle"
    OSCILLATION_TOGGLE = "oscillation_toggle"
    SPEED_UP = "speed_up"
    SPEED_DOWN = "speed_down"


@dataclass(frozen=True, slots=True)
class StableObservation:
    """A decoded state confirmed by repeated sensor reports."""

    decoded: DecodedPower
    report_count: int
