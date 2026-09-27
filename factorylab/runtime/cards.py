"""A card's typed region becomes the controller's numeric region here.

The controller refuses to parse acceptable-region text, and so does this module
now: a card carries its region as typed data (``charter.region.CardRule``,
charter audit P2), and a card whose sentence no rule reads holds no region and
simply carries no price.
"""

from __future__ import annotations

import math

from factorylab.charter.charter import MetricCard
from factorylab.charter.controller import CardRegion
from factorylab.runtime.observations import ObservationBook, seed_book


def parses(card: MetricCard) -> bool:
    """True when the card holds a typed region (its bound available or not)."""
    return card.rule is not None


def region_for(
    card: MetricCard,
    *,
    rolling: dict[str, float],
    observations: ObservationBook | None = None,
) -> CardRegion | None:
    """Return a region only for a registered observation with a usable bound.

    Exclusive rules ("above zero") are treated as inclusive bounds. The
    previous-median rule reads ``rolling[f"{card.id}_prev_median"]`` and yields
    None until that record exists. Scale is the width of the observation's
    declared unit range; the bound's magnitude cannot change it, and a
    population-registered observation declares that range too. Without a book
    only the seed vocabulary is readable.
    """
    rule = card.rule
    observation = (observations or seed_book()).get(card.observation)
    if rule is None or observation is None:
        return None
    lo, hi = rule.lo, rule.hi
    if rule.deferred:
        prev = rolling.get(f"{card.id}_prev_median")
        if prev is None:
            return None
        hi = float(prev)
        if not math.isfinite(hi):
            return None
    return CardRegion(card.id, rule.kind, lo, hi, observation.scale)  # type: ignore[arg-type]
