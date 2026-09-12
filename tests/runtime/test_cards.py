from dataclasses import replace

import pytest

from factorylab.charter.charter import MetricCard, seed_charter
from factorylab.charter.controller import CardRegion
from factorylab.charter.windows import MetricWindow
from factorylab.runtime.cards import parses, region_for


def card(region: str, units: str = "ratio", id: str = "c") -> MetricCard:
    return MetricCard(id, "useful inquiry", "d", units,
                   MetricWindow("windows", 1, None), region, "turnover", "all")


@pytest.mark.parametrize(
    ("text", "units", "expected"),
    [
        ("at least 0.9", "fraction", CardRegion("c", "min", 0.9, None, 1.0)),
        ("above zero", "score difference in [-1, 1]", CardRegion("c", "min", 0.0, None, 1.0)),
        ("Above 0.25.", "ratio", CardRegion("c", "min", 0.25, None, 1.0)),
        ("below 5", "ratio", CardRegion("c", "max", None, 5.0, 1.0)),
        ("at most 2", "ratio", CardRegion("c", "max", None, 2.0, 1.0)),
        ("between -1 and 1", "ratio", CardRegion("c", "band", -1.0, 1.0, 1.0)),
        # money cards are scaled by their own bound's magnitude, never below 1
        ("below 2500", "micro-USD per return", CardRegion("c", "max", None, 2500.0, 2500.0)),
        ("below 0.5", "USD", CardRegion("c", "max", None, 0.5, 1.0)),
        ("between 100 and 3000", "micro-USD", CardRegion("c", "band", 100.0, 3000.0, 3000.0)),
    ],
)
def test_region_phrasings(text: str, units: str, expected: CardRegion) -> None:
    assert region_for(card(text, units), rolling={}) == expected


@pytest.mark.parametrize(
    "text",
    ["roughly stable", "between 5 and 1", "below", "at least", "above 1 and below 2", "≥ 0.9"],
)
def test_unparseable_prose_carries_no_region(text: str) -> None:
    c = card(text)
    assert region_for(c, rolling={"c_prev_median": 1.0}) is None
    assert parses(c) is False


def test_median_of_previous_window_waits_for_the_rolling_record() -> None:
    c = card("below the median of the previous window", "micro-USD per return", "cost_per_return")
    assert parses(c) is True  # understood, just not yet bounded: no price.unparsed entry
    assert region_for(c, rolling={}) is None
    assert region_for(c, rolling={"other_prev_median": 9.0}) is None
    got = region_for(c, rolling={"cost_per_return_prev_median": 1000.0})
    assert got == CardRegion("cost_per_return", "max", None, 1000.0, 1000.0)
    # a tiny median still yields a unit scale
    got = region_for(c, rolling={"cost_per_return_prev_median": 0.25})
    assert got is not None and got.scale == 1.0 and got.hi == 0.25


def test_seed_charter_cards_are_readable() -> None:
    cards = {c.id: c for c in seed_charter().cards}
    assert all(parses(c) for c in cards.values())
    assert region_for(cards["well_formed_rate"], rolling={}) == CardRegion(
        "well_formed_rate", "min", 0.9, None, 1.0
    )
    assert region_for(cards["forecast_skill"], rolling={}) == CardRegion(
        "forecast_skill", "min", 0.0, None, 1.0
    )
    assert region_for(cards["cost_per_return"], rolling={}) is None


def test_observation_is_required_for_a_region_and_is_normalized():
    c = card("below 5")
    assert region_for(replace(c, observation=" TURNOVER "), rolling={}) is not None
    with pytest.raises(ValueError, match="observation"):
        replace(c, observation="")
    for name in ("venue fills", "computed_per_window/turnover"):
        unknown = replace(c, observation=name)
        assert parses(unknown)  # The region is readable; the observation is not.
        assert region_for(unknown, rolling={}) is None
