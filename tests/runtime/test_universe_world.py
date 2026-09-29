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
