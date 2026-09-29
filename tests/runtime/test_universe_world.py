"""A world with a market universe: resolved once at launch, pinned, broadcast bounded.

The venue is the world (AGENTS.md): a manifest may select every perp and every USDC
spot pair. The selectors are resolved against the venue's listing at launch and the
lists pinned for the world's life (Chapter II §II), and a tick's cost follows the
markets the world is in play on, never the universe's size (Chapter II §IV.c). Each
test attempts to violate one of those and asserts it fails.
"""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import replace
from decimal import Decimal

import pytest

from factorylab.kernel.ledger import Ledger
from factorylab.runtime.loop import Runtime
from factorylab.runtime.worlds import load_manifest
from factorylab.world.exchange import FakeExchange
from factorylab.world.scripted import ScriptedProvider


def universe_manifest():
    base = load_manifest("scripted")
    return replace(base, exchange=replace(base.exchange, coins=("*",),
                                          spot_pairs=("*/USDC",)))


def fake(n: int) -> FakeExchange:
    """A fake venue listing BTC, ETH and ``n`` more perps, and two USDC pairs."""
    return FakeExchange(coins=(), start_cash_usd=Decimal(1000),
                        listed_coins=("BTC", "ETH", *(f"C{i}" for i in range(n))),
                        listed_spot_pairs=("PURR/USDC", "HYPE/USDC"))


def runtime(n: int, *, path=None, events=0) -> Runtime:
    return Runtime(universe_manifest(), events=events, seed=1, initial_balance_micro=None,
                   ledger_path=None if path is None else str(path), router_gamma=.1,
                   provider=ScriptedProvider(), exchange=fake(n))


def test_selectors_resolve_at_launch_and_the_resolved_lists_are_the_trading_seed():
    rt = runtime(40)
    assert rt.universe["coins"] == ["BTC", "ETH", *(f"C{i}" for i in range(40))]
    assert rt.universe["spot_pairs"] == ["PURR/USDC", "HYPE/USDC"]
    assert rt.venue_tools.coins[:3] == ("BTC", "ETH", "C0")
    assert "C39" in rt._trading_markets()
    pinned = [item for item in rt.ledger.items() if item.get("kind") == "venue.universe"]
    assert len(pinned) == 1
    assert pinned[0]["selectors"] == {"coins": ["*"], "spot_pairs": ["*/USDC"]}


def test_the_prompt_carries_the_universe_by_its_selectors_never_by_its_markets():
    rt = runtime(300)
    markets = rt._trading_markets_section()
    assert markets["universe"]["resolved"] == {"perp": 302, "spot": 2}
    assert markets["perp"] == [] and markets["spot"] == []
    # The world block's instrument records and freshness rows follow the broadcast set:
    # with nothing in play, no market's record is in the prompt.
    assert rt._traded_instruments() == {"perp": [], "spot": []}
    assert rt._market_data_as_of() == {}
    # Every resolved market's record is one world.read away, with its terms.
    records = rt.institution_section("markets")
    assert len(records["perp"]) == 302 and len(records["spot"]) == 2
    for row in records["perp"]:
        assert {"lot_size", "tick_size", "min_order_value_usd", "max_leverage",
                "taker_fee_rate", "maker_fee_rate"} <= set(row)


def test_a_market_in_play_joins_the_broadcast_and_one_that_is_not_never_does():
    rt = runtime(300)
    assert rt._broadcast_markets() == ()
    rt.order_intents["c"] = {"args": {"coin": "C7"}, "operation": "venue.place_market"}
    assert rt._broadcast_markets() == ("C7",)
    # A market outside the universe is never broadcast, whatever names it.
    rt.order_intents["d"] = {"args": {"coin": "NOT-LISTED"}, "operation": "venue.place_market"}
    assert rt._broadcast_markets() == ("C7",)


def test_an_order_outside_the_universe_is_refused_by_the_venue_tools():
    rt = runtime(3)
    result = rt.venue_tools.call("venue.place_market",
                                 {"coin": "DOGE", "side": "buy", "size": "1"})
    assert "error" in result


def test_a_world_without_selectors_is_unchanged():
    rt = Runtime(load_manifest("scripted"), events=0, seed=1, initial_balance_micro=None,
                 ledger_path=None, router_gamma=.1, provider=ScriptedProvider(),
                 exchange=FakeExchange(listed_coins=("SOL",)))
    assert rt.universe is None
    assert rt._broadcast_markets() == rt._trading_markets()
    assert "universe" not in rt._trading_markets_section()


@pytest.mark.gate
@pytest.mark.parametrize("n", [3, 300])
def test_mids_broadcast_per_tick_are_bounded_whatever_the_universe(n):
    rt = runtime(n, events=40)
    rt.run()
    per_tick: list[int] = []
    for row in rt.events_log:
        if row["kind"] == "Tick":
            per_tick.append(0)
        elif row["kind"] == "MarketMid" and per_tick:
            per_tick[-1] += 1
    assert per_tick, "the world ticked"
    # Only markets in play are broadcast: never one mid per market of the universe.
    assert max(per_tick) <= 4, per_tick


@pytest.mark.gate
def test_the_pinned_universe_survives_a_resume_and_is_never_resolved_again(tmp_path,
                                                                          monkeypatch):
    from factorylab.runtime.resume import resume_runtime
    from factorylab.world import universe

    class Death(Exception):
        pass

    path = tmp_path / "universe.jsonl"
    rt = runtime(12, path=path, events=30)
    original = rt._process_event

    def interrupted(event):
        result = original(event)
        if rt.ticks_consumed == 3 and str(event.kind) == "Tick":
            raise Death
        return result

    rt._process_event = interrupted
    with pytest.raises(Death):
        rt.run()

    def resolve_again(*args, **kwargs):
        raise AssertionError("a resume resolved the universe again")

    monkeypatch.setattr(universe, "resolve", resolve_again)
    restored = resume_runtime(universe_manifest(), str(path), provider=ScriptedProvider(),
                              exchange=fake(12))
    assert restored.universe == rt.universe
    assert restored.venue_tools.coins == rt.venue_tools.coins
    ledger = Ledger.open_read_only(str(path), manifest=json.loads(
        universe_manifest().canonical_json()))
    assert Counter(item.get("kind") for item in ledger.items())["venue.universe"] == 1


def test_a_market_that_left_the_broadcast_keeps_no_mark_a_trade_could_open_at():
    """A seat could read today's price and name the move it already saw: a mark of a
    market no longer broadcast is dropped, so a trade named on it opens at the first
    mid at or after it is named (ruling R10-h)."""
    rt = runtime(30)
    rt.venue_marks["C3"] = [1, "100"]  # broadcast once, long ago
    rt.venue_marks["C4"] = [1, "100"]
    rt.order_intents["c"] = {"args": {"coin": "C4"}, "operation": "venue.place_market"}
    rt._forget_unbroadcast_marks()
    assert "C3" not in rt.venue_marks and "C4" in rt.venue_marks


def test_a_resting_order_the_fill_cursor_tracks_keeps_its_market_in_play():
    rt = runtime(30)
    rt.consequence_fills.orders["k"] = {"coin": "C9", "oid": "5", "booked": "0"}
    assert "C9" in rt._broadcast_markets()


def test_the_schematics_render_of_a_selector_world_resolves_nothing_and_says_so():
    """A render reads no venue (runtime/published.py): the universe is published by its
    selectors as not yet resolved, and no coin is invented for a tool example."""
    from scripts import charter_session

    block = charter_session.launch_world(load_manifest("edition7-breadth-testnet"), "run")
    universe = block["trading_markets"]["universe"]
    assert universe["selectors"] == {"coins": ["*", "xyz:*"], "spot_pairs": ["*/USDC"]}
    assert universe["resolved"]["status"] == "unavailable"
    assert block["trading_markets"]["perp"] == []


def _produced(n: int, reply: dict):
    """One producer request of a universe world over ``n`` extra markets: the prompt the
    seat read, its outcome schema, and the (status, reason) the kernel recorded."""
    from tests.runtime.test_counterfactual_contract import Seat, _published, _returned
    from tests.runtime.test_loop import _consequence_produce

    seat = Seat(reply)
    rt = Runtime(universe_manifest(), events=0, seed=1, initial_balance_micro=None,
                 ledger_path=None, router_gamma=.1, provider=seat, exchange=fake(n))
    rt._manage_reserve_window()
    handle, _event = _consequence_produce(rt)
    (prompt,) = seat.prompts
    return prompt, json.dumps(_published(prompt)), _returned(rt, handle)


def test_a_producer_prompt_and_its_schema_do_not_grow_with_the_universe():
    """Chapter II §IV.c: a request's size follows the world, not the venue's listing. The
    tick payload carries the broadcast markets' mids, and the counterfactual coin is
    named by reference once the listing is long (the kernel still checks it)."""
    reply = {"action": "hold", "counterfactual": {"coin": "BTC", "side": "buy"}}
    small_prompt, small_schema, small = _produced(3, reply)
    large_prompt, large_schema, large = _produced(3000, reply)
    assert small == large == ("ok", None)
    assert abs(len(large_prompt) - len(small_prompt)) < 600
    assert abs(len(large_schema) - len(small_schema)) < 400
    assert "C2999" not in large_prompt


def test_a_counterfactual_off_the_listing_is_refused_when_named_by_reference():
    from factorylab.runtime.grounded import COUNTERFACTUAL_UNLISTED

    _prompt, schema, returned = _produced(
        3000, {"action": "hold", "counterfactual": {"coin": "NOT-LISTED", "side": "buy"}})
    assert '"enum": ["BTC"' not in schema  # the listing is named, not enumerated
    assert returned == ("malformed", COUNTERFACTUAL_UNLISTED)


def test_a_counterfactual_on_a_listed_market_outside_the_universe_opens_and_settles():
    """Astra P1 on #178: the contract accepts any listed coin as a counterfactual, so the
    world observes it whether or not it may trade it. Trading stays pinned: an order on
    it is still refused."""
    from tests.runtime.test_counterfactual_contract import Seat
    from tests.runtime.test_loop import _consequence_produce

    base = load_manifest("scripted")
    manifest = replace(base, exchange=replace(base.exchange, coins=("BTC", "ETH"),
                                              spot_pairs=("*/USDC",)))
    seat = Seat({"action": "hold", "counterfactual": {"coin": "C5", "side": "buy"}})
    rt = Runtime(manifest, events=0, seed=1, initial_balance_micro=None, ledger_path=None,
                 router_gamma=.1, provider=seat, exchange=fake(8))
    rt._manage_reserve_window()
    rt._read_fee_schedule()  # the listing, as the first broadcast mid reads it
    assert "C5" in rt._listed_instruments() and "C5" not in rt.venue_tools.coins
    handle, _event = _consequence_produce(rt)
    frozen = rt.reference_mids[handle]
    assert frozen["coin"] == "C5" and frozen["open_ns"] is None
    assert "C5" in rt._broadcast_markets()
    step = rt.tick_clock.interval_ns
    now = rt.clock.now_ns
    while frozen.get("res") is None and now < rt.clock.now_ns + 10 * rt._horizon_ns():
        now += step
        rt._settle_exchange_effects(rt._advance_venue(now))
    assert frozen["open_ns"] is not None and frozen["res"] is not None
    refused = rt.venue_tools.call("venue.place_market",
                                  {"coin": "C5", "side": "buy", "size": "1"})
    assert "error" in refused


def test_a_simulated_venue_advances_only_the_markets_the_world_watches(monkeypatch):
    """Codex P1 on #178: the fake and tape venues walked every listed market each tick
    (its mid, its history, its event) before the broadcast filter. With the same market
    in play, per-tick work is the same at 3 and at 3000 markets."""
    created: list[int] = []
    advance = FakeExchange.advance

    def counted(self, ts_ns):
        events = advance(self, ts_ns)
        created.append(len(events))
        return events

    monkeypatch.setattr(FakeExchange, "advance", counted)
    work = {}
    for n in (3, 3000):
        created.clear()
        rt = runtime(n)
        rt.order_intents["c"] = {"args": {"coin": "C1"}, "operation": "venue.place_market"}
        step = rt.tick_clock.interval_ns
        for k in range(1, 6):
            rt._settle_exchange_effects(rt._advance_venue(rt.clock.now_ns + k * step))
        venue = rt.exchange.target
        work[n] = (list(created), sum(len(h) for h in venue._mid_history.values()))
    assert work[3] == work[3000] == ([1] * 5, 5)


@pytest.mark.parametrize("n", [3, 3000])
def test_the_broadcast_set_is_built_without_scanning_the_universe_each_call(monkeypatch, n):
    """Codex P2 on #178: _broadcast_markets rebuilt sets from the whole pinned universe
    and walked every trading market on every call, several times a tick. Its fixed
    membership is kept until a registration or a new listing read changes it: over many
    calls the universe and the listing are each walked once, at 3 and at 3000 markets."""
    rt = runtime(n)
    rt._read_fee_schedule()
    walked = {"trading": 0, "listed": 0}
    trading, listed = type(rt)._trading_markets, type(rt)._listed_instruments

    def count_trading(self):
        walked["trading"] += 1
        return trading(self)

    def count_listed(self):
        walked["listed"] += 1
        return listed(self)

    monkeypatch.setattr(type(rt), "_trading_markets", count_trading)
    monkeypatch.setattr(type(rt), "_listed_instruments", count_listed)
    rt.order_intents["c"] = {"args": {"coin": "C1"}, "operation": "venue.place_market"}
    for _ in range(20):
        assert rt._broadcast_markets() == ("C1",)
    assert walked == {"trading": 1, "listed": 1}
    # A registration changes the fixed membership, and the next call sees it.
    rt._admit_market("NEW", "perp")
    assert "NEW" in rt._broadcast_markets()
