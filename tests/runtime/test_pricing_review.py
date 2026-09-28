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
