"""Exact cold-review scenarios for §II.b/§IV.a reward pricing."""

from dataclasses import asdict, replace
from types import SimpleNamespace

import pytest

from factorylab.charter.charter import MetricCard
from factorylab.charter.controller import CardRegion
from factorylab.charter.measurement import metric_identity
from factorylab.charter.windows import MetricWindow
from factorylab.runtime.pricing import MeasureWindow, PricingMixin


@pytest.mark.parametrize("closed", [False, True])
@pytest.mark.parametrize(("old_floor", "floor", "expected"), [(0.9, 0.5, 0), (0.5, 0.9, 0.1)])
def test_held_raw_measurement_uses_current_region(old_floor, floor, expected, closed):
    card = MetricCard("c", "n", "d", "fraction", MetricWindow("windows", 1, None),
                      {"rule": "at least", "lo": floor}, "forecast_skill", "evaluator")
    window = MeasureWindow(2, 0)
    fact = {"identity": list(metric_identity(card)), "source_window": 1, "value": 0.8,
            "region": asdict(CardRegion("c", "min", old_floor, None, 1.0)),
            "violation": max(0, old_floor - 0.8)}
    rt = PricingMixin()
    rt.price_origins = {}
    rt.card_samples = SimpleNamespace(values={})
    rt.regions = {"c": CardRegion("c", "min", floor, None, 1.0)}
    rt.card_held = {"c": fact}
    if closed:
        window.closed_values = {}
        window.closed_held = rt.card_held
        window.closed_regions = {"c": CardRegion("c", "min", old_floor, None, 1.0)}
    rt.m = SimpleNamespace(prices=SimpleNamespace(penalty_cap=0.9))
    rt._priced_cards = lambda _: [(card, SimpleNamespace(id="forecast_skill"), window, 1.0)]
    terms = rt._penalty_terms("evaluator", None, abstaining=True)
    assert sum(t["weight"] for t in terms) == pytest.approx(expected)


def attribution_runtime(field="notional_micro", count=2):
    from factorylab.charter.measurement import CardSamples
    from factorylab.kernel.ledger import Ledger
    from factorylab.settlement.vocabulary import PredicateBook
    from tests.runtime.test_holdouts import InProcessPredicates

    rt = PricingMixin()
    rt.window = MeasureWindow(1, 0)
    rt.card_samples = CardSamples()
    rt.card_samples.closed(rt.window)
    card = MetricCard("c", "n", "d", "fraction", MetricWindow("windows", 1, None),
                      {"rule": "at least", "lo": 0.5}, "revision_rate", "producer",
                      holdout=("hold@1",))
    rt.charter = SimpleNamespace(cards=(card,))
    rt.predicate_runner = InProcessPredicates()
    rt.predicates = PredicateBook(run=rt.predicate_runner.run)
    rt.predicates.register("hold", "constraint",
                           f"def resolve(facts):\n    return facts['{field}'] == 0\n",
                           facts={field: 0}, persist=lambda _: None)
    rt.window.decisions = {str(i): {"role": "producer", field: i + 1} for i in range(count)}
    rt.price_origins = {h: {"origin": 1} for h in rt.window.decisions}
    rt._split_decisions = lambda w: w.decisions
    rt._resolution_step = lambda _: 0.02
    rt.ledger = Ledger(None)
    rt.clock = SimpleNamespace(now_ns=0)
    return rt


def test_mixed_menu_abstention_bears_each_role_holdout_at_ordinary_weights():
    rt = attribution_runtime(count=1)
    rt.window.decisions["0"].update(role="evaluator",
                                    menu_roles={"evaluator": 0.75, "producer": 0.25})
    producer = rt.charter.cards[0]
    evaluator = replace(producer, id="e", answers_for="evaluator")
    rt.charter.cards = (producer, evaluator)
    attribution = rt._holdout_attribution({
        c.id: {"results": {"hold@1": False}} for c in rt.charter.cards})
    assert attribution["c"]["shares"] == {"0": 1.0}
    assert attribution["e"]["shares"] == {"0": 1.0}
    # Ordinary abstention pricing applies menu weights once, outside each role price.
    weights = rt.window.decisions["0"]["menu_roles"]
    assert weights["producer"] * attribution["c"]["violation"] == pytest.approx(0.005)
    assert weights["evaluator"] * attribution["e"]["violation"] == pytest.approx(0.015)
    from factorylab.runtime.feedback import FeedbackMixin

    rt.price_windows = {1: rt.window}
    rt.window.closed_values = {"c": 0.8, "e": 0.8}
    rt.window.closed_regions = {c.id: CardRegion(c.id, "min", 0.5, None, 1.0)
                                for c in rt.charter.cards}
    rt.window.closed_holdouts = {"c": 0.02, "e": 0.02}
    rt.window.closed_holdout_attribution = attribution
    rt.m = SimpleNamespace(prices=SimpleNamespace(penalty_cap=0.9))
    rt._is_price_abstention = lambda _: True
    rt._is_niche = lambda *args: False
    rt._decision_share = lambda *args, **kwargs: 0
    rt._attributed_share = lambda *args, **kwargs: None
    rt._relievers = lambda *args: set()
    rt._priced_cards = lambda _: [(c, SimpleNamespace(id=c.observation), rt.window,
                                  2.0 if c.id == "c" else 1.0) for c in rt.charter.cards]
    assert FeedbackMixin._priced_abstention(rt, "0") == pytest.approx(0.25 * 0.04 + 0.75 * 0.02)
