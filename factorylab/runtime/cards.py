"""Metric-card prose becomes numeric regions here, never inside the controller.

The controller refuses to parse acceptable-region text. This module
understands the handful of phrasings the seed charter and the population's
amendments use, and returns ``None`` for anything else so an unparsed card
simply carries no price.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass

from factorylab.charter.charter import MetricCard
from factorylab.charter.controller import CardRegion
from factorylab.runtime.observations import ObservationBook, seed_book

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


def region_for(
    card: MetricCard,
    *,
    rolling: dict[str, float],
    observations: ObservationBook | None = None,
) -> CardRegion | None:
    """Return a region only for a registered observation with a usable bound.

    Exclusive phrasings ("above zero") are treated as inclusive bounds. "Below
    the median of the previous window" reads ``rolling[f"{card.id}_prev_median"]``
    and yields None until that record exists. Scale is the width of the
    observation's declared unit range; card prose and bound magnitude cannot
    change it, and a population-registered observation declares that range too
    Without a book only the seed vocabulary is readable.
    """
    bounds = _parse(card.acceptable_region)
    observation = (observations or seed_book()).get(card.observation)
    if bounds is None or observation is None:
        return None
    lo, hi = bounds.lo, bounds.hi
    if bounds.deferred:
        prev = rolling.get(f"{card.id}_prev_median")
        if prev is None:
            return None
        hi = float(prev)
        if not math.isfinite(hi):
            return None
    scale = observation.scale
    return CardRegion(card.id, bounds.kind, lo, hi, scale)  # type: ignore[arg-type]


def accountable_scopes(kind: str | None) -> frozenset[str]:
    """The scopes a return of this emitted kind is answered for: the kind and its role.

    A card's ``answers_for`` is either a role alias (``producer``, ``evaluator``,
    …) or a population-registered emitted kind, so a claim about a return
    answers for both spellings of the same seat's work.
    """
    from factorylab.cortex.registration import measured_role

    if kind is None:
        return frozenset()
    scopes = {str(kind)}
    try:
        scopes.add(measured_role(str(kind)))
    except (ValueError, KeyError):  # an unmeasured kind answers for itself alone
        pass
    return frozenset(scopes)


def forecast_weight(charter, kind: str | None) -> float:
    """The charter's weight on one settled claim about a return of this emitted kind.

    This is the whole of what replaces ``return_paid_off``'s old privilege: the
    cards say which claims the population holds anyone accountable for, and a
    charter whose cards name no particular scope weights every claim equally.
    """
    from factorylab.settlement.weights import scope_weight

    return scope_weight(charter, accountable_scopes(kind))
