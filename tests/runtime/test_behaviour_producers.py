"""Owner-known effects retain their own holdout facts (§II.b/§IV.a)."""
from types import SimpleNamespace

import pytest

from factorylab.kernel.events import EventKind
from factorylab.runtime.compute import ComputeMixin
from tests.runtime.test_pricing_review import attribution_runtime


def runtime(field):
    rt = attribution_runtime(field)
    rt.window.decisions = {h: {"role": "producer", "cost": 0, "ok": 0,
                               "invocations": 0, "tool_calls": 0, "notional_micro": 0}
                           for h in ("0", "1")}
    rt.price_windows = {1: rt.window}
    rt.return_kinds = {}
    rt.handle_to_assembly = {}
    rt.assemblies = {}
    return rt


def test_real_market_purchase_has_only_its_owner():
    rt = runtime("market_purchases")
    ComputeMixin._record_market(rt, {"kind": "observation.market_purchase", "handle": "0"})
    facts = rt._holdout_decision_facts()
    assert rt.window.market_purchases == 1
    assert facts["0"]["market_purchases"] == 1
    assert facts["1"]["market_purchases"] == 0
    assert rt._holdout_attribution({"c": {"results": {"hold@1": False}}})["c"]["shares"] == {
        "0": 1.0}


@pytest.mark.parametrize("field,expected", [("fills", 1), ("realized_pnl_micro", -200_000),
                                            ("notional_micro", 6_000_000)])
def test_real_fill_records_owner_once(field, expected):
    rt = runtime(field)
    rt.consequences = SimpleNamespace(table=SimpleNamespace(orders=[
        SimpleNamespace(order_id="order", handle="0")]))
    rt._record_fill_notional({"order_id": "order", "size": "2", "px": "3",
                             "realized_usd": "-0.2"})
    facts = rt._holdout_decision_facts()
    assert facts["0"][field] == expected
    assert facts["1"][field] == 0


def test_delivered_meta_verdict_has_author_and_empty_nonowner_list():
    rt = runtime("meta_verdicts")
    rt._observe_delivered_event(SimpleNamespace(kind=EventKind.META_VERDICT,
                                               payload={"by": "0", "score": 0.2}))
    facts = rt._holdout_decision_facts()
    assert rt.window.meta_verdicts == [0.2]
    assert facts["0"]["meta_verdicts"] == [0.2]
    assert facts["1"]["meta_verdicts"] == []


def test_late_purchase_records_known_but_uncharged_owner_outside_price_window():
    rt = runtime("market_purchases")
    rt.price_origins["0"]["origin"] = 0
    ComputeMixin._record_market(rt, {"kind": "observation.market_purchase", "handle": "0"})
    attribution = rt._holdout_attribution({"c": {"results": {"hold@1": False}}})["c"]
    assert attribution["shares"] == {}
    assert attribution["violation"] == 0
    assert attribution["predicates"]["hold@1"]["uncharged_owners"] == ["0"]
    rt.window.closed_holdout_attribution = {"c": attribution}
    rt._ledger_unattributed()
    row = list(rt.ledger._recovery_items())[-1]
    assert row["reason"] == "owner_outside_price_window"
    assert row["uncharged_owners"] == ["0"]
    assert rt.price_origins["0"]["origin"] == 0


def test_forecast_fact_is_owned_by_decision_not_child_commitment():
    rt = runtime("outcomes")
    forecast = SimpleNamespace(handle="forecast-child", evaluator_id="eval", about_handle="subject",
                               predicate_id="return_paid_off")
    rt.card_samples.resolved_forecast(forecast=forecast, role="producer", window=1, skill=0.2,
                                     y=1, status="settled", source={"handle": "0", "verdict": 0.8},
                                     subject={"acted": True})
    facts = rt._holdout_decision_facts()
    assert facts["0"]["outcomes"] == 1
    assert facts["0"]["forecast_skills"] == [0.2]
    assert facts["0"]["consequences_settled"] == 1
    assert facts["1"]["outcomes"] == 0


def test_contribution_only_owner_does_not_dilute_existing_proxy_split():
    from factorylab.runtime.pricing import PricingMixin

    rt = runtime("market_purchases")
    rt._in_split = lambda *args: True
    rt._split_decisions = lambda window: PricingMixin._split_decisions(rt, window)
    ComputeMixin._record_market(rt, {"kind": "observation.market_purchase", "handle": "late"})
    assert set(rt._split_decisions(rt.window)) == {"0", "1"}
    assert rt._holdout_decision_facts()["late"]["market_purchases"] == 1
    ComputeMixin._record_market(rt, {"kind": "observation.market_purchase", "handle": "late"})
    assert set(rt._split_decisions(rt.window)) == {"0", "1"}
    assert rt._holdout_decision_facts()["late"]["market_purchases"] == 2
    rt._contribution("late", "producer")
    assert set(rt._split_decisions(rt.window)) == {"0", "1", "late"}


def test_known_counter_has_supported_zero_even_without_any_events():
    rt = runtime("market_purchases")
    assert all(f["market_purchases"] == 0 for f in rt._holdout_decision_facts().values())


def test_pool_only_facts_remain_unsupported():
    rt = runtime("max_position_notional_micro")
    rt.window.max_position_notional_micro = 100_000
    assert all(f["max_position_notional_micro"] is None
               for f in rt._holdout_decision_facts().values())
