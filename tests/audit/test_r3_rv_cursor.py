"""T59: a sealed predicate cursor marks a sample position, not a list length.

Public world series retain only their latest 1,024 samples. A cursor that stores
lengths silently slides when a series rolls: post-forecast evidence disappears
and the claim is scored false. A monotonic position plus the retained-prefix
offset either finds the evidence or reports it unavailable.
"""

import json

import pytest

from factorylab.cortex.request import Return
from factorylab.kernel.events import Event, EventKind
from factorylab.kernel.queue import SettleStatus
from factorylab.runtime.observations import (
    MAX_WORLD_SAMPLES,
    window_cursor,
    window_facts_since,
)
from factorylab.runtime.resume import restore_runtime, runtime_state
from factorylab.runtime.shared import PredicateRunner
from factorylab.world.models import ModelResponse
from tests.audit.test_a1_composition import make_runtime, routed
from tests.audit.test_r3_j_runtime import register_work

CODE = "def resolve(facts): return bool(facts['mids'].get('BTC'))"
MID_MICRO = 100_000_000


def feed_mids(rt, count, *, start=0, coin="BTC"):
    """Deliver real MarketMid events through the ordinary observation path."""
    for index in range(count):
        rt._observe_delivered_event(Event(
            f"mid-{coin}-{start + index}", EventKind.MARKET_MID, start + index + 1,
            {"coin": coin, "mid": "100"}, "world"))


def _runner(self, code, facts):
    return bool(facts["mids"].get("BTC")), None


def sealed_claim(monkeypatch):
    """A desk that seals `a BTC mid arrived` over the open window on every tick."""
    rt = make_runtime()
    register_work(rt)
    rt._close_price_window()
    rt.tool_jail_available = True
    monkeypatch.setattr(PredicateRunner, "run", _runner)
    rt._apply_registrations("decision-0", Return("decision-0", {"register": [{
        "kind": "predicate", "id": "btc-mid", "description": "A BTC mid arrived.",
        "code": CODE,
    }]}, 0, "ok"))
    assert rt.predicates.get("btc-mid") is not None, rt.registration_feedback
    monkeypatch.setattr(type(rt.provider.target), "complete", lambda self, req: ModelResponse(
        req.model_id, json.dumps({"forecasts": [{"predicate": "btc-mid",
            "params": {"horizon_events": 1}, "q": 0.9}]}), 1, 1, "end_turn"))
    return rt


def seal(rt):
    routed(rt, "weather-desk", Event("weather", EventKind.TICK, 0, {}, "world"))
    return next(f for f in rt.book.pending() if f.predicate_id == "btc-mid")


def settle(rt, forecast):
    rt.n += 1
    rt.balance_at[:] = [rt.wallet.balance] * (rt.n + 1)
    rt._settle_due_forecasts()
    return rt.queue.get(forecast.handle).status, rt.queue.history(forecast.handle)[-1].score


def test_a_saturated_series_still_marks_the_position_of_its_next_sample():
    rt = make_runtime()
    feed_mids(rt, MAX_WORLD_SAMPLES)
    cursor = window_cursor(rt.window)
    feed_mids(rt, 1, start=MAX_WORLD_SAMPLES)
    assert len(rt.window.mids) == MAX_WORLD_SAMPLES  # the oldest sample was evicted
    since = window_facts_since(rt.window, cursor)
    assert since["mids"]["BTC"] == [[MAX_WORLD_SAMPLES + 1, MID_MICRO]]


def test_the_whole_retained_interval_is_returned_when_nothing_after_the_mark_was_lost():
    rt = make_runtime()
    feed_mids(rt, 10)
    cursor = window_cursor(rt.window)
    feed_mids(rt, MAX_WORLD_SAMPLES, start=10)
    since = window_facts_since(rt.window, cursor)
    assert len(since["mids"]["BTC"]) == MAX_WORLD_SAMPLES


def test_evidence_discarded_after_the_mark_is_unavailable():
    rt = make_runtime()
    feed_mids(rt, 10)
    cursor = window_cursor(rt.window)
    feed_mids(rt, MAX_WORLD_SAMPLES + 10, start=10)
    assert window_facts_since(rt.window, cursor) is None


def test_a_series_evicted_entirely_by_another_coin_is_unavailable():
    rt = make_runtime()
    feed_mids(rt, 4, coin="ETH")
    cursor = window_cursor(rt.window)
    feed_mids(rt, 2, start=4, coin="ETH")
    feed_mids(rt, MAX_WORLD_SAMPLES, start=10, coin="BTC")
    assert "ETH" not in {row["coin"] for row in rt.window.mids}
    assert window_facts_since(rt.window, cursor) is None


def test_a_forecast_is_scored_on_the_samples_that_followed_a_rollover(monkeypatch):
    rt = sealed_claim(monkeypatch)
    feed_mids(rt, MAX_WORLD_SAMPLES)
    forecast = seal(rt)
    feed_mids(rt, 1, start=MAX_WORLD_SAMPLES)
    status, score = settle(rt, forecast)
    assert status is SettleStatus.SETTLED
    assert score == pytest.approx(0.99)  # 1 - (0.9 - 1) ** 2


def test_a_forecast_whose_interval_was_discarded_is_closed_unscored(monkeypatch):
    rt = sealed_claim(monkeypatch)
    feed_mids(rt, 10)
    forecast = seal(rt)
    feed_mids(rt, MAX_WORLD_SAMPLES + 10, start=10)
    status, _score = settle(rt, forecast)
    assert status is SettleStatus.CENSORED


def test_sample_positions_survive_resume(monkeypatch):
    rt = sealed_claim(monkeypatch)
    feed_mids(rt, MAX_WORLD_SAMPLES)
    forecast = seal(rt)
    feed_mids(rt, 1, start=MAX_WORLD_SAMPLES)
    restored = make_runtime()
    restore_runtime(restored, json.loads(json.dumps(runtime_state(rt))))
    monkeypatch.setattr(PredicateRunner, "run", _runner)
    status, score = settle(restored, forecast)
    assert status is SettleStatus.SETTLED
    assert score == pytest.approx(0.99)
