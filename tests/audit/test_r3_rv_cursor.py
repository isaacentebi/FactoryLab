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
    record_venue_facts,
    window_cursor,
    window_facts_since,
)
from factorylab.runtime.resume import restore_runtime, runtime_state
from factorylab.runtime.shared import PredicateRunner
from factorylab.world.models import ModelResponse
from tests.audit.test_a1_composition import make_runtime, routed
from tests.audit.test_r3_j_runtime import register_work
from tests.runtime.test_connectors import ledger_items


def code_for(coin):
    """`a <coin> mid arrived`, read over the facts that followed the claim."""
    return f"def resolve(facts): return bool(facts['mids'].get({coin!r}))"


CODE = code_for("BTC")
MID_MICRO = 100_000_000


def feed_mids(rt, count, *, start=0, coin="BTC"):
    """Deliver real MarketMid events through the ordinary observation path."""
    for index in range(count):
        rt._observe_delivered_event(Event(
            f"mid-{coin}-{start + index}", EventKind.MARKET_MID, start + index + 1,
            {"coin": coin, "mid": "100"}, "world"))


def runner_for(coin):
    """The jailed resolver, run in process: one coin's presence in the facts it is given."""
    def run(self, code, facts):
        return bool(facts["mids"].get(coin)), None
    return run


_runner = runner_for("BTC")


def sealed_claim(monkeypatch, *, coin="BTC"):
    """A desk that seals `a <coin> mid arrived` over the open window on every tick."""
    predicate_id = f"{coin.lower()}-mid"
    rt = make_runtime()
    register_work(rt)
    rt._close_price_window()
    rt.tool_jail_available = True
    monkeypatch.setattr(PredicateRunner, "run", runner_for(coin))
    rt._apply_registrations("decision-0", Return("decision-0", {"register": [{
        "kind": "predicate", "id": predicate_id, "description": f"A {coin} mid arrived.",
        "code": code_for(coin),
    }]}, 0, "ok"))
    assert rt.predicates.get(predicate_id) is not None, rt.registration_feedback
    monkeypatch.setattr(type(rt.provider.target), "complete", lambda self, req: ModelResponse(
        req.model_id, json.dumps({"forecasts": [{"predicate": predicate_id,
            "params": {"horizon_events": 1}, "q": 0.9}]}), 1, 1, "end_turn"))
    return rt


def seal(rt, predicate_id="btc-mid"):
    routed(rt, "weather-desk", Event("weather", EventKind.TICK, 0, {}, "world"))
    return next(f for f in rt.book.pending() if f.predicate_id == predicate_id)


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


def test_a_coin_absent_at_sealing_keeps_its_position_when_its_samples_are_evicted():
    """A coin the window has never published stands at position zero, not nowhere.

    The mark carries no entry for it, so its later absence from the facts is
    indistinguishable from a coin that never arrived — unless the cursor is read
    against what the series has discarded.
    """
    rt = make_runtime()
    feed_mids(rt, MAX_WORLD_SAMPLES)
    cursor = window_cursor(rt.window)  # ETH has no samples at all here
    feed_mids(rt, 3, coin="ETH")
    feed_mids(rt, MAX_WORLD_SAMPLES, start=MAX_WORLD_SAMPLES)
    retained = {row["coin"] for row in rt.window.mids}
    assert retained == {"BTC"}  # every ETH sample was evicted by the global cap
    assert window_facts_since(rt.window, cursor) is None


def test_a_coin_absent_at_sealing_is_still_read_over_the_samples_that_followed():
    rt = make_runtime()
    feed_mids(rt, MAX_WORLD_SAMPLES)
    cursor = window_cursor(rt.window)
    feed_mids(rt, 3, coin="ETH")
    since = window_facts_since(rt.window, cursor)
    assert len(since["mids"]["ETH"]) == 3 and since["mids"]["BTC"] == []


def test_a_claim_about_a_newly_seen_coin_is_scored_on_its_retained_samples(monkeypatch):
    rt = sealed_claim(monkeypatch, coin="ETH")
    feed_mids(rt, MAX_WORLD_SAMPLES)
    forecast = seal(rt, "eth-mid")
    feed_mids(rt, 3, coin="ETH")
    status, score = settle(rt, forecast)
    assert status is SettleStatus.SETTLED
    assert score == pytest.approx(0.99)  # 1 - (0.9 - 1) ** 2


def test_a_claim_about_a_newly_seen_coin_is_closed_unscored_when_it_is_evicted(monkeypatch):
    rt = sealed_claim(monkeypatch, coin="ETH")
    feed_mids(rt, MAX_WORLD_SAMPLES)
    forecast = seal(rt, "eth-mid")
    feed_mids(rt, 3, coin="ETH")
    feed_mids(rt, MAX_WORLD_SAMPLES, start=MAX_WORLD_SAMPLES)
    status, _score = settle(rt, forecast)
    assert status is SettleStatus.CENSORED
    assert [i["predicate"] for i in ledger_items(rt, "forecast.evidence_discarded")] == ["eth-mid"]


def test_the_position_of_an_absent_coin_survives_resume(monkeypatch):
    rt = sealed_claim(monkeypatch, coin="ETH")
    feed_mids(rt, MAX_WORLD_SAMPLES)
    forecast = seal(rt, "eth-mid")
    restored = make_runtime()
    restore_runtime(restored, json.loads(json.dumps(runtime_state(rt))))
    monkeypatch.setattr(PredicateRunner, "run", runner_for("ETH"))
    feed_mids(restored, 3, coin="ETH")
    feed_mids(restored, MAX_WORLD_SAMPLES, start=MAX_WORLD_SAMPLES)
    status, _score = settle(restored, forecast)
    assert status is SettleStatus.CENSORED


def feed_funding(rt, count, *, start=0, coin="BTC"):
    """Deliver real Funding rate events through the ordinary observation path."""
    for index in range(count):
        rt._observe_delivered_event(Event(
            f"funding-{coin}-{start + index}", EventKind.FUNDING, start + index + 1,
            {"coin": coin, "rate": "0.0001"}, "world"))


def batch_coins(coin, extra=8):
    """One venue-wide read: the named coin first, then more instruments than fit."""
    return [coin] + [f"C{index}" for index in range(MAX_WORLD_SAMPLES + extra)]


def test_a_batched_mids_read_counts_the_samples_it_dropped():
    """A venue-wide read is one paid observation of every instrument it names.

    More instruments than the bound retains is an eviction like any other: the
    samples the read itself never kept are still samples the window took, and a
    coin whose position they moved must not read as a coin that stood still.
    """
    rt = make_runtime()
    feed_mids(rt, 1, coin="ETH")
    cursor = window_cursor(rt.window)
    record_venue_facts(rt.window, "venue.mids", {},
                       {"mids": {coin: "100" for coin in batch_coins("ETH")}}, 1)
    assert "ETH" not in {row["coin"] for row in rt.window.mids}
    assert rt.window.series_discarded["mids/ETH"] == 2
    assert window_facts_since(rt.window, cursor) is None


def test_a_batched_funding_read_counts_the_samples_it_dropped():
    rt = make_runtime()
    feed_funding(rt, 1, coin="ETH")
    cursor = window_cursor(rt.window)
    record_venue_facts(rt.window, "venue.funding", {}, {"funding": [
        {"coin": coin, "ts_ns": 1, "rate": "0.0001"} for coin in batch_coins("ETH")]}, 1)
    assert "ETH" not in {row["coin"] for row in rt.window.funding}
    assert rt.window.series_discarded["funding/ETH"] == 2
    assert window_facts_since(rt.window, cursor) is None


def test_a_batched_read_that_fits_discards_nothing_of_a_marked_coin():
    """The accounting only moves when the bound actually evicts something."""
    rt = make_runtime()
    feed_mids(rt, 1, coin="ETH")
    cursor = window_cursor(rt.window)
    record_venue_facts(rt.window, "venue.mids", {},
                       {"mids": {coin: "100" for coin in ["ETH", "BTC", "SOL"]}}, 1)
    since = window_facts_since(rt.window, cursor)
    assert len(since["mids"]["ETH"]) == 1 and rt.window.series_discarded == {}
