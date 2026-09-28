"""Exact R16c-5 counterexamples for §II.b/§IV.a pricing."""

from dataclasses import replace
from types import SimpleNamespace

from factorylab.charter.charter import MetricCard
from factorylab.charter.controller import CardRegion
from factorylab.charter.measurement import metric_identity
from factorylab.charter.windows import MetricWindow
from factorylab.runtime.pricing import MeasureWindow, PricingMixin
from tests.runtime.test_pricing_review import attribution_runtime


def bookkeeping_runtime():
    rt = attribution_runtime()
    rt.price_windows = {1: rt.window}
    rt.return_kinds = {}
    rt.handle_to_assembly = {}
    rt.assemblies = {}
    del rt._split_decisions
    return rt


def test_late_fill_owner_does_not_dilute_current_proxy_split():
    rt = bookkeeping_runtime()
    rt.price_origins["old"] = {"origin": 0}
    rt.consequences = SimpleNamespace(table=SimpleNamespace(
        orders=[SimpleNamespace(order_id="order", handle="old")]))
    rt._record_fill_notional({"order_id": "order", "size": "1", "px": "2"})
    assert rt.window.decisions["old"]["contribution_only"] is True
    assert set(rt._split_decisions(rt.window)) == {"0", "1"}
    assert rt.price_origins["old"] == {"origin": 0, "turnover": 1}


def test_pruned_owner_late_activation_stays_ownerless():
    rt = bookkeeping_runtime()
    rt._record_behaviour("finalized", amendments_activated=1)
    rt.predicates.register("activation", "constraint",
                           "def resolve(facts):\n    return facts['amendments_activated'] == 0\n",
                           facts={"amendments_activated": 0}, persist=lambda _: None)
    assert "finalized" not in rt.price_origins
    rt.charter.cards = (replace(rt.charter.cards[0], holdout=("activation@1",)),)
    held = {"c": {"results": {"activation@1": False}}}
    rt.window.closed_holdout_attribution = rt._holdout_attribution(held)
    assert "finalized" not in rt.window.closed_holdout_attribution["c"]["shares"]
    rt._ledger_unattributed()
    assert any(row["kind"] == "price.unattributed" for row in rt.ledger._recovery_items())


def test_revision_facts_are_owners_own_count_not_global():
    rt = bookkeeping_runtime()
    rt.card_samples.windows[-1]["revision_handles"] = {"0"}
    rt.window.decisions["0"]["revised_decisions"] = 1
    rt.window.decisions["1"]["revised_decisions"] = 0
    rt.card_samples.returns.append({"window": 1, "handle": "1", "role": "producer",
                                    "invoked": True, "ok": True, "cost": 0,
                                    "tool_calls": 0, "noop": False, "revision": True})
    facts = rt._holdout_decision_facts()
    assert facts["0"]["revised_decisions"] == 1
    assert facts["1"]["revised_decisions"] == 0


def test_prior_window_forecast_owner_gets_own_facts():
    rt = bookkeeping_runtime()
    rt.price_origins["old"] = {"origin": 0}
    rt.card_samples.forecasts.append({"window": 1, "owner_handle": "old",
                                      "handle": "forecast", "role": "producer",
                                      "skill": -0.5, "status": "settled"})
    facts = rt._holdout_decision_facts()
    assert facts["old"]["forecast_skills"] == [-0.5]
    assert facts["0"]["forecast_skills"] == []
    assert "old" not in rt._split_decisions(rt.window)


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
