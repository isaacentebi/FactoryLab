"""A4: bounded, attributable prices retain a reward gradient under persistent failure."""

from dataclasses import replace

import pytest

from factorylab.charter.book import validate_observation_bindings
from factorylab.charter.charter import Charter, MetricCard
from factorylab.charter.controller import CardRegion, violation
from factorylab.charter.windows import MetricWindow
from factorylab.cortex.request import Return
from factorylab.kernel.queue import PropensityRecord
from factorylab.runtime.cards import region_for
from factorylab.runtime.loop import Runtime
from factorylab.runtime.observations import observation_for
from factorylab.runtime.pricing import MeasureWindow
from factorylab.runtime.resume import restore_runtime, runtime_state
from factorylab.runtime.worlds import load_manifest


def runtime():
    rt = Runtime(load_manifest("scripted"), events=0, seed=1, initial_balance_micro=None,
                 ledger_path=None, drip=False, router_gamma=.1)
    rt.charter = Charter(1, rt.charter.norms, (
        MetricCard("cost", rt.charter.norms[0], "cost", "micro-USD",
                   MetricWindow("windows", 1, None), "at most 500",
                   "cost_per_return", "producer"),
    ))
    rt._manage_reserve_window()
    return rt


def decision(rt, cost, *, ok=True, role="producer", tools=0):
    handle = rt.queue.open(actor="test", event_id="test", channel="verdict", deadline_ns=10**18,
                           parent_handle=None, cost_ceiling=0,
                           propensity=PropensityRecord(("seed-decider",), (1.,), "seed-decider",
                                                       0, "test", "state"))
    rt._contribution(handle, role).update(cost=cost, ok=int(ok), invocations=1, tool_calls=tools)
    rt.window.invocations += 1
    rt.window.ok += int(ok)
    rt.window.tool_calls += tools
    if ok and role == "producer":
        rt.window.costs.append(cost)
    return handle


def settle(rt, handle, score):
    rt._settle_priced(handle, channel="verdict", score=score, definition_version="test",
                      sampling_ref=None, cards="producer")
    return rt.queue.returns_for("test")[-1].score


def test_a4_ten_violating_windows_preserve_cheap_success_gradient():
    rt = runtime()
    for window in range(1, 11):
        rt.window = MeasureWindow(window, rt.wallet.balance)
        rt.price_windows[window] = rt.window
        for _ in range(10):
            decision(rt, 1_100)
        rt.n += 10
        rt._close_price_window()
        cheap = decision(rt, 100)
        expensive = decision(rt, 11_000, ok=False)
        assert settle(rt, cheap, 1) > settle(rt, expensive, 0)
    assert rt.wallet.check_conservation()


def test_a4_saturation_preserves_perfect_returns_and_attributes_cost():
    rt = runtime()
    cheap, expensive = decision(rt, 100), decision(rt, 1_000_000_000)
    rt.n = 10
    rt._close_price_window()
    assert rt.controller.price("cost") == 1
    assert rt._penalty_for("producer") == .5
    cheap_penalty = rt._penalty_for("producer", cheap)
    expensive_penalty = rt._penalty_for("producer", expensive)
    assert cheap_penalty < expensive_penalty
    assert cheap_penalty + expensive_penalty == pytest.approx(.5)
    assert settle(rt, cheap, 1) >= .5
    assert settle(rt, expensive, 1) >= .5


def test_a4_late_settlement_retains_original_window_after_resume():
    rt = runtime()
    cheap, expensive = decision(rt, 100), decision(rt, 10_000_000)
    rt.n = 10
    rt._close_price_window()
    before = rt._penalty_for("producer", cheap)
    rt.window = MeasureWindow(2, rt.wallet.balance)
    decision(rt, 10_000_000_000)
    restored = runtime()
    restore_runtime(restored, runtime_state(rt))
    assert restored._penalty_for("producer", cheap) == before
    assert restored._penalty_for("producer", expensive) > before
    assert settle(restored, cheap, 1) == settle(rt, cheap, 1)


@pytest.mark.parametrize("observation,role", [("cost_per_return", "producer"),
                                               ("well_formed_rate", "evaluator"),
                                               ("turnover", "producer"), ("tool_calls", "meta")])
def test_a4_observation_scales_are_catalogue_units_not_card_prose(observation, role):
    card = MetricCard("x", "care", "d", "made-up USD unit", MetricWindow("windows", 1, None),
                      "at most 500", observation, role)
    region = region_for(card, rolling={})
    metadata = observation_for(observation)
    assert region.scale == metadata.unit_range[1] - metadata.unit_range[0]
    changed = region_for(replace(card, acceptable_region="at most 1"), rolling={})
    assert region.scale == changed.scale
    assert violation(region, 500 + region.scale) == 1


def test_a4_duplicate_observation_refused_publicly_before_reserving(monkeypatch):
    rt = runtime()
    entries = []
    append = rt.ledger.append

    def capture(item):
        entries.append(item)
        return append(item)

    monkeypatch.setattr(rt.ledger, "append", capture)
    card = dict(vars(rt.charter.cards[0]), id="duplicate",
                window={"kind": "windows", "n": 1, "per": None})
    proposal = dict(kind="amendment", id="duplicate", add=[card],
                    predicted_effect={"card_id": "duplicate", "direction": "decrease",
                                      "window": 1})
    before = rt.reserve.remaining()
    rt._apply_registrations("author", Return("author", {"register": [proposal]}, 0, "ok"))
    reasons = [e["reason"] for e in entries if e["kind"] == "registration.rejected"]
    assert len(reasons) == 1 and "already named" in reasons[0]
    assert rt.reserve.remaining() == before
    assert not rt.charter_book.pending()
    # A replacement may reuse its own observation, and disjoint roles do not duplicate prices.
    live = rt.charter.cards[0]
    validate_observation_bindings((replace(live, acceptable_region="at most 400"),))
    validate_observation_bindings((live, replace(live, id="dup", answers_for="evaluator")))
    with pytest.raises(ValueError, match="already named"):
        validate_observation_bindings((live, replace(live, id="dup", answers_for="all")))


def test_a4_defect_tool_and_turnover_shares_use_own_contributions():
    rt = runtime()
    good = decision(rt, 100, tools=1)
    bad = decision(rt, 100, ok=False, tools=3)
    shares = rt._decision_share
    quality = CardRegion("quality", "min", .9, None, 1)
    assert shares(rt.window, good, "well_formed_rate", "all", quality, .5) == 0
    assert shares(rt.window, bad, "well_formed_rate", "all", quality, .5) == 1
    calls = CardRegion("calls", "max", None, 1, 1)
    assert shares(rt.window, good, "tool_calls", "all", calls, 4) == .25
    rt.window.decisions[good]["notional_micro"] = 100
    rt.window.decisions[bad]["notional_micro"] = 300
    rt.window.notional_micro = 400
    assert shares(rt.window, good, "turnover", "all", calls, 4) == .25
    assert shares(rt.window, good, "forecast_skill", "producer", quality, -.2) == .5


def test_a4_scripted_windows_never_erase_every_settlement(w1_scripted_diary):
    from collections import defaultdict

    rt, entries = w1_scripted_diary
    assert rt.stats.amendments_activated >= 1
    assert any(item["kind"] == "price.penalty" and
               sum(t["weight"] for t in item["terms"]) >= rt.m.prices.penalty_cap
               for item in entries)
    windows = defaultdict(list)
    current = 0
    for item in entries:
        if item["kind"] == "immune.window":
            current = item["window"] + 1
        elif item["kind"] == "price.penalty":
            windows[current].append(item)
    assert len(windows) >= 3
    for settlements in windows.values():
        assert any(item["effective"] > 0 for item in settlements)
        assert all(item["penalty"] <= .5 for item in settlements)
        assert all(item["effective"] >= .5 for item in settlements if item["raw"] == 1)


def test_a4_filled_notional_keeps_order_owner_and_venue_rounding():
    from factorylab.world.events import WorldEvent, WorldEventKind

    rt = runtime()
    owner = decision(rt, 100)
    rt.consequences.start(owner, rt.n)
    rt.consequences.table = rt.consequences.table.order("order", owner, "1")
    rt.window = MeasureWindow(2, rt.wallet.balance)
    fill = WorldEvent(WorldEventKind.FILL, 0, "fake", {
        "order_id": "order", "size": "1", "px": "0.0000016", "coin": "BTC",
        "is_buy": True, "realized_usd": "0", "fee_usd": "0",
    })
    rt._settle_exchange_effects([fill])
    assert rt.window.notional_micro == rt.window.decisions[owner]["notional_micro"] == 2
    assert rt.price_origins[owner] == {"origin": 1, "turnover": 2}
