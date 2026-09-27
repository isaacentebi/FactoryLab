"""Card windows are executable sample selections, never free-text promises."""

from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class Interval:
    """A confidence-interval requirement on a card's measured mean.

    Essay II.IV: specs become probabilistic, "with holdouts, confidence
    intervals, and sample size requirements all subject to change". A scope is
    measured only when the ``level`` interval of its mean, under the normal
    approximation (``z * s / sqrt(n)``, ``s`` the population standard deviation of
    its samples), has a half-width of at most ``half_width`` observation units.
    Otherwise it is unmeasured, exactly like a scope with fewer than ``n`` samples.
    """

    level: float
    half_width: float

    def __post_init__(self) -> None:
        for name in ("level", "half_width"):
            value = getattr(self, name)
            if type(value) not in (int, float) or not math.isfinite(value):
                raise ValueError(f"window.interval.{name} must be a finite number")
            object.__setattr__(self, name, float(value))
        if not 0 < self.level < 1:
            raise ValueError("window.interval.level must be in (0, 1)")
        if self.half_width <= 0:
            raise ValueError("window.interval.half_width must be positive")

    @classmethod
    def parse(cls, value: object) -> Interval | None:
        """None, an Interval, or exactly ``{level, half_width}``."""
        if value is None or isinstance(value, cls):
            return value
        if not isinstance(value, dict) or set(value) != {"level", "half_width"}:
            raise ValueError("window.interval must be an object with level and half_width")
        return cls(**value)

    def satisfied(self, values: list[float]) -> bool:
        """True when the samples' mean is known to within ``half_width`` at ``level``."""
        from statistics import NormalDist, fmean, pstdev

        if not values:
            return False
        if len(values) == 1:
            return False  # one sample states no spread, so no interval
        z = NormalDist().inv_cdf(0.5 + self.level / 2)
        spread = pstdev(values, fmean(values))
        return z * spread / math.sqrt(len(values)) <= self.half_width


@dataclass(frozen=True)
class MetricWindow:
    """A positive sample count selects returns, settled forecasts or closed windows.

    ``interval``, when present, is the card's confidence-interval requirement.
    """

    kind: str
    n: int
    per: str | None
    interval: Interval | None = None

    def __post_init__(self) -> None:
        if self.kind not in ("returns", "forecasts", "windows"):
            raise ValueError("window.kind must be returns, forecasts or windows")
        if type(self.n) is not int or self.n < 1:
            raise ValueError("window.n must be a positive integer")
        if self.per not in (None, "role", "assembly"):
            raise ValueError("window.per must be role, assembly or null")
        object.__setattr__(self, "interval", Interval.parse(self.interval))

    @classmethod
    def parse(cls, value: object) -> MetricWindow:
        """Reject prose, omitted fields and unsupported selectors without coercion."""
        if isinstance(value, cls):
            return value
        if (not isinstance(value, dict)
                or not {"kind", "n", "per"} <= set(value) <= {"kind", "n", "per", "interval"}):
            raise ValueError("window must be an object with kind, n and per "
                             "(and optionally interval)")
        return cls(**value)

    def __str__(self) -> str:
        # A window without an interval renders exactly as it always did.
        fields = asdict(self)
        if fields["interval"] is None:
            del fields["interval"]
        return json.dumps(fields, sort_keys=True)


def window_schema() -> dict:
    """Return a detached public schema for the exact accepted window shape."""
    return {
        "type": "object",
        "properties": {
            "kind": {"enum": ["returns", "forecasts", "windows"]},
            "n": {"type": "integer", "minimum": 1},
            "per": {"enum": ["role", "assembly", None]},
            "interval": {"type": "object", "properties": {
                "level": {"type": "number", "exclusiveMinimum": 0, "exclusiveMaximum": 1},
                "half_width": {"type": "number", "exclusiveMinimum": 0}},
                "required": ["level", "half_width"], "additionalProperties": False},
        },
        "required": ["kind", "n", "per"],
        "additionalProperties": False,
    }
