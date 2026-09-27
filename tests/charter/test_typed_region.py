"""A card's region is typed data and its sentence is derived (charter audit P2).

Chapter II §IV: specs become "probabilistic, containing multivariate possibilities
of acceptable output distributions … with holdouts, confidence intervals, and sample
size requirements all subject to change". A region the runtime has to re-read from
prose, and a promise the runtime infers from that prose, are neither.
"""

from dataclasses import replace

import pytest

from factorylab.charter.charter import MetricCard
from factorylab.charter.region import PREVIOUS_MEDIAN, CardRule
from factorylab.charter.windows import Interval, MetricWindow
from factorylab.runtime.cards import parses, region_for
from tests.seed_charter import seed_charter


def _card(region, **changes):
    return MetricCard(**{"id": "c", "norm": "n", "description": "d", "units": "u",
                         "window": MetricWindow("returns", 3, "role"), "region": region,
                         "observation": "well_formed_rate", "answers_for": "all", **changes})


@pytest.mark.parametrize(("sentence", "typed", "prose"), [
    ("at most 0.30", {"rule": "at most", "hi": 0.3}, "at most 0.3"),
    ("At least 0.9.", {"rule": "at least", "lo": 0.9}, "at least 0.9"),
    ("above zero", {"rule": "above", "lo": 0.0}, "above 0"),
    ("below 5000", {"rule": "below", "hi": 5000.0}, "below 5000"),
    ("between 0.2 and 0.8", {"rule": "between", "lo": 0.2, "hi": 0.8}, "between 0.2 and 0.8"),
    (PREVIOUS_MEDIAN, {"rule": PREVIOUS_MEDIAN}, PREVIOUS_MEDIAN),
])
def test_a_sentence_and_its_typed_rule_are_one_region(sentence, typed, prose):
    rule = CardRule.parse(sentence)
    assert rule == CardRule.parse(typed) and rule.as_dict() == typed
    assert rule.prose() == prose
    # The prose is derived from the rule, whichever form the card was given in.
    assert _card(sentence).acceptable_region == _card(typed).acceptable_region == prose
    assert _card(sentence) == _card(typed)


@pytest.mark.parametrize("bad", [
    {"rule": "at most"}, {"rule": "at most", "lo": 1}, {"rule": "between", "lo": 2, "hi": 1},
    {"rule": "roughly", "hi": 1}, {"rule": "at least", "lo": float("nan")},
    {"rule": "at least", "lo": True}, {"rule": PREVIOUS_MEDIAN, "hi": 1},
    {"rule": "at most", "hi": 1, "scale": 2},
])
def test_a_typed_region_carries_exactly_the_bounds_its_rule_needs(bad):
    with pytest.raises(ValueError, match="card c region"):
        _card(bad)


def test_an_unread_sentence_holds_no_region_and_no_price():
    card = _card("keep it reasonable")
    assert card.rule is None and not parses(card)
    assert card.acceptable_region == "keep it reasonable"
    assert region_for(card, rolling={}) is None


def test_restating_a_region_in_words_replaces_the_typed_rule():
    card = _card({"rule": "at least", "lo": 0.9})
    moved = replace(card, acceptable_region="at most 0.5")
    assert moved.rule == CardRule("at most", hi=0.5)
    assert replace(card, region={"rule": "below", "hi": 2}).acceptable_region == "below 2"


def test_the_controller_region_is_built_from_the_rule_not_the_words():
    card = _card({"rule": "between", "lo": 0.25, "hi": 0.75})
    region = region_for(card, rolling={})
    assert (region.kind, region.lo, region.hi) == ("band", 0.25, 0.75)
    deferred = seed_charter().cards[0]
    assert deferred.rule.deferred and region_for(deferred, rolling={}) is None
    assert region_for(deferred, rolling={f"{deferred.id}_prev_median": 7.0}).hi == 7.0


def test_a_manifest_card_may_state_a_typed_region_and_both_forms_must_agree():
    from factorylab.runtime.worlds import manifest_from_dict
    from tests.runtime.test_manifests import _with_charter

    raw = _with_charter()
    row = dict(raw["charter"]["cards"][1])
    row.pop("acceptable_region", None)
    row["region"] = {"rule": "at least", "lo": 0.95}
    raw["charter"]["cards"][1] = row
    assert manifest_from_dict(raw).charter.cards[1].acceptable_region == "at least 0.95"
    row["acceptable_region"] = "at least 0.5"
    with pytest.raises(ValueError, match="acceptable_region: disagrees"):
        manifest_from_dict(raw)


def test_a_window_may_require_a_confidence_interval():
    interval = Interval(0.95, 0.1)
    assert MetricWindow.parse({"kind": "returns", "n": 5, "per": None,
                               "interval": {"level": 0.95, "half_width": 0.1}}).interval \
        == interval
    # A window without one renders exactly as before.
    assert str(MetricWindow("returns", 5, None)) == '{"kind": "returns", "n": 5, "per": null}'
    assert interval.satisfied([0.5] * 10)
    assert not interval.satisfied([0.0, 1.0] * 5)  # 1.96 * 0.5 / sqrt(10) > 0.1
    assert interval.satisfied([0.0, 1.0] * 50)  # 1.96 * 0.5 / 10 < 0.1
    assert not interval.satisfied([0.3])  # one sample states no spread
    for bad in ({"level": 1.0, "half_width": 0.1}, {"level": 0.9, "half_width": 0},
                {"level": 0.9}):
        with pytest.raises(ValueError, match="interval"):
            MetricWindow.parse({"kind": "returns", "n": 5, "per": None, "interval": bad})


def test_a_scope_below_its_required_precision_is_unmeasured():
    from factorylab.charter.measurement import CardSamples, measure_card
    from factorylab.cortex.request import Return

    def samples(outcomes):
        rows = CardSamples()
        for index, ok in enumerate(outcomes):
            rows.returned(handle=f"h{index}", assembly="a", role="producer", window=1,
                          ret=Return(f"h{index}", {}, 1, "ok" if ok else "failed"))
        return rows

    precise = _card({"rule": "at least", "lo": 0.5}, window=MetricWindow(
        "returns", 20, "role", {"level": 0.9, "half_width": 0.2}))
    assert measure_card(precise, samples([True, False] * 10)) == {"producer": 0.5}
    stricter = replace(precise, window=MetricWindow("returns", 20, "role",
                                                    {"level": 0.9, "half_width": 0.05}))
    assert measure_card(stricter, samples([True, False] * 10)) == {}
    # A spread-free sample meets any interval.
    assert measure_card(stricter, samples([True] * 20)) == {"producer": 1.0}
