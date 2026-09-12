"""Metric-card prose becomes numeric regions here, never inside the controller.

The controller (spec v0.6 section 8.1) refuses to parse acceptable-region
text. This module understands the handful of phrasings the seed charter and
the population's amendments use, and returns ``None`` for anything else so
an unparsed card simply carries no price.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass

from factorylab.charter.charter import MetricCard
from factorylab.charter.controller import CardRegion
from factorylab.runtime.observations import observation_for

_NUMBER = r"([-+]?\d+(?:\.\d+)?(?:e[-+]?\d+)?|zero|one)"
_WORDS = {"zero": 0.0, "one": 1.0}
_DEFERRED = "below the median of the previous window"


@dataclass(frozen=True)
class _Bounds:
    kind: str
    lo: float | None
    hi: float | None
    deferred: bool = False  # hi comes from the rolling record, not the text


def _num(token: str) -> float:
    return _WORDS[token] if token in _WORDS else float(token)


def _parse(text: str) -> _Bounds | None:
    """Return bounds for a recognised phrasing, or None when the prose is not understood."""
    t = " ".join(text.lower().strip().rstrip(".").split())
    if t == _DEFERRED:
        return _Bounds("max", None, None, deferred=True)
    patterns: tuple[tuple[str, str], ...] = (
        (rf"^at least {_NUMBER}$", "min"),
        (rf"^above {_NUMBER}$", "min"),
        (rf"^at most {_NUMBER}$", "max"),
        (rf"^below {_NUMBER}$", "max"),
        (rf"^between {_NUMBER} and {_NUMBER}$", "band"),
    )
    for pattern, kind in patterns:
        m = re.match(pattern, t)
        if m is None:
            continue
        if kind == "band":
            lo, hi = _num(m.group(1)), _num(m.group(2))
            return (_Bounds("band", lo, hi)
                    if math.isfinite(lo) and math.isfinite(hi) and lo < hi else None)
        value = _num(m.group(1))
        if not math.isfinite(value):
            return None
        return _Bounds(kind, value, None) if kind == "min" else _Bounds(kind, None, value)
    return None


def parses(card: MetricCard) -> bool:
    """True when the card's acceptable region is a recognised phrasing (bound available or not)."""
    return _parse(card.acceptable_region) is not None


def region_for(card: MetricCard, *, rolling: dict[str, float]) -> CardRegion | None:
    """Return a region only for a catalogue observation with a usable bound.

    Exclusive phrasings ("above zero") are treated as inclusive bounds. "Below
    the median of the previous window" reads ``rolling[f"{card.id}_prev_median"]``
    and yields None until that record exists. Scale is 1.0 for ratios and
    fractions and ``max(1, |bound|)`` for cards whose units mention USD.
    """
    bounds = _parse(card.acceptable_region)
    if bounds is None or observation_for(card.observation) is None:
        return None
    lo, hi = bounds.lo, bounds.hi
    if bounds.deferred:
        prev = rolling.get(f"{card.id}_prev_median")
        if prev is None:
            return None
        hi = float(prev)
        if not math.isfinite(hi):
            return None
    magnitude = max(abs(b) for b in (lo, hi) if b is not None)
    scale = max(1.0, magnitude) if "usd" in card.units.lower() else 1.0
    return CardRegion(card.id, bounds.kind, lo, hi, scale)  # type: ignore[arg-type]
