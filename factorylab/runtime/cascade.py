"""Pure, immutable windows for progressively slower evaluatory tiers."""

from __future__ import annotations

from dataclasses import dataclass, replace
from math import ceil, isfinite
from statistics import fmean

from factorylab.kernel.events import Event, EventKind


def release_threshold(min_ratio: int, jitter_fraction: float, draw: float) -> int:
    """Preserve the minimum separation, with bounded upward jitter from a supplied draw."""
    if type(min_ratio) is not int or min_ratio < 3:
        raise ValueError("cascade min_ratio must be an integer >= 3")
    if not isfinite(jitter_fraction) or jitter_fraction < 0:
        raise ValueError("jitter_fraction must be finite and nonnegative")
    if not isfinite(draw) or not 0 <= draw < 1:
        raise ValueError("draw must be in [0, 1)")
    jitter = ceil(min_ratio * jitter_fraction)
    return min_ratio + int(draw * (jitter + 1))


def event_tier(event: Event) -> int:
    """Only verdict events enter the cascade; producer judgements occupy tier one."""
    if event.kind is EventKind.VERDICT:
        return 1
    if event.kind is EventKind.META_VERDICT:
        return event.payload["tier"]
    raise ValueError("cascade arrivals must be Verdict or MetaVerdict events")


@dataclass(frozen=True)
class CascadeGate:
    """An arrival returns new state; no window releases early or crosses evaluatory tiers."""

    threshold: int
    arrivals: tuple[Event, ...] = ()

    def __post_init__(self) -> None:
        if type(self.threshold) is not int or self.threshold < 3:
            raise ValueError("threshold must be an integer >= 3")
        object.__setattr__(self, "arrivals", tuple(self.arrivals))
        if len(self.arrivals) >= self.threshold:
            raise ValueError("a full window must already have been released")
        if len({event_tier(e) for e in self.arrivals}) > 1:
            raise ValueError("a gate cannot mix tiers")

    def add(self, event: Event) -> tuple[CascadeGate | None, Event | None]:
        """Release only the latest event, enriched with the complete disjoint arrival window."""
        tier = event_tier(event)
        if self.arrivals and event_tier(self.arrivals[0]) != tier:
            raise ValueError("a gate cannot mix tiers")
        arrivals = (*self.arrivals, event)
        if len(arrivals) < self.threshold:
            return replace(self, arrivals=arrivals), None
        scores = [e.payload["verdict" if tier == 1 else "score"] for e in arrivals]
        handles = [e.payload["evaluator_handle" if tier == 1 else "by"] for e in arrivals]
        window = {
            "count": len(arrivals),
            "mean": fmean(scores),
            "min": min(scores),
            "max": max(scores),
            "handles": handles,
        }
        return None, replace(event, payload={**event.payload, "window": window})
