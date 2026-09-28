"""Exact R16c-5 counterexamples for §II.b/§IV.a pricing."""

from dataclasses import replace
from types import SimpleNamespace

from factorylab.charter.charter import MetricCard
from factorylab.charter.controller import CardRegion
from factorylab.charter.measurement import metric_identity
from factorylab.charter.windows import MetricWindow
from factorylab.runtime.pricing import MeasureWindow, PricingMixin


def test_reused_card_id_cannot_price_held_mean_as_std():
    old = MetricCard("c", "n", "d", "fraction", MetricWindow("windows", 1, None),
                     {"rule": "at least", "lo": 0.9}, "verdict_mean", "evaluator")
    live = replace(old, observation="verdict_std")
    window = MeasureWindow(2, 0)
    window.closed_values = {}
    window.closed_regions = {"c": CardRegion("c", "min", 0.9, None, 1.0)}
    window.closed_held = {"c": {"identity": list(metric_identity(old)),
                                 "source_window": 1, "value": 0.8}}
    rt = PricingMixin()
    rt.charter = SimpleNamespace(cards=(live,))
    rt.price_origins = {}
    rt.regions = {"c": CardRegion("c", "min", 0.95, None, 1.0)}
    rt.m = SimpleNamespace(prices=SimpleNamespace(penalty_cap=0.9))
    rt._priced_cards = lambda _: [(old, SimpleNamespace(id="verdict_mean"), window, 1.0)]
    assert rt._penalty_terms("evaluator", None, abstaining=True) == []
