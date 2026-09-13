"""Round three, rehearsal row T53: a tick broadcasts the world's markets, not the venue's.

The live rehearsal measured about 1,687 world events per tick on Hyperliquid
testnet, because ``LiveVenue.on_tick`` emitted one ``MarketMid`` per coin the
venue lists and one ``Funding`` per listed perpetual. Group K opened public
reads to the whole listing on purpose; the per-tick broadcast is a different
thing, and it is bounded by the manifest seed plus every registered market.
"""

from dataclasses import replace

from factorylab.cortex.request import Return
from factorylab.kernel.queue import PropensityRecord
from factorylab.runtime.loop import Runtime
from factorylab.runtime.resume import restore_runtime, runtime_state
from factorylab.runtime.worlds import load_manifest
from factorylab.world.events import WorldEventKind
from factorylab.world.exchange import FakeExchange
from factorylab.world.scripted import ScriptedProvider

LISTED = tuple(f"C{i}" for i in range(200))


def decision(rt, owner="seed-decider"):
    """Open one addressable handle on a wall-clock world, whose deadline is ahead of now."""
    handle = rt.queue.open(
        actor=owner, event_id="tick-broadcast", propensity=PropensityRecord(
            (owner,), (1.,), owner, 0, owner, "test"), channel="verdict",
        deadline_ns=rt.clock.now_ns + 10**12, parent_handle=None, cost_ceiling=10_000_000,
    )
    rt.handle_to_assembly[handle] = owner
    return handle


def crowded_runtime():
    """A live-shaped world whose venue lists 200 perpetuals and whose manifest seeds two."""
    manifest = load_manifest("scripted")
    manifest = replace(
        manifest,
        exchange=replace(manifest.exchange, kind="hyperliquid",
                         coins=("BTC", "ETH"), spot_pairs=()),
        drip=None,
    )
    exchange = FakeExchange(coins=("BTC", "ETH"), listed_coins=LISTED)
    return Runtime(manifest, events=0, seed=1, initial_balance_micro=100_000_000,
                   ledger_path=None, drip=False, router_gamma=.1,
                   exchange=exchange, provider=ScriptedProvider())


def broadcast(rt, now_ns=10):
    events = rt.venue.on_tick(now_ns)
    return ({str(e.payload["coin"]) for e in events if e.kind is WorldEventKind.MARKET_MID},
            {str(e.payload["coin"]) for e in events if e.kind is WorldEventKind.FUNDING})


def test_tick_broadcasts_only_the_worlds_trading_markets():
    rt = crowded_runtime()
    assert rt.venue is not None and len(rt.exchange.instruments()["perp"]) == 202
    mids, funding = broadcast(rt)
    assert mids == {"BTC", "ETH"} and funding == {"BTC", "ETH"}


def test_public_reads_still_answer_for_every_listed_coin():
    rt = crowded_runtime()
    assert set(rt.venue_tools.call("venue.mids", {})["mids"]) >= set(LISTED)
    assert len(rt.venue_tools.call("venue.funding", {})["funding"]) >= len(LISTED)
    book = rt.venue_tools.call("venue.order_book", {"coin": "C7", "depth": 2})
    assert "error" not in book, book
    assert {row["coin"] for row in rt.venue_tools.call("venue.instruments", {})["perp"]} >= set(
        LISTED)
    # …and an unregistered market is still not tradeable.
    assert "error" in rt.venue_tools.call(
        "venue.place_market", {"coin": "C7", "side": "buy", "size": "1"})


def test_a_registered_market_joins_the_broadcast_from_the_next_tick():
    rt = crowded_runtime()
    rt._manage_reserve_window()
    handle = decision(rt)
    rt._apply_registrations(handle, Return(
        handle, {"register": [{"kind": "market", "coin": "C7"}]}, 0, "ok"))
    assert "C7" in rt.venue_tools.coins, rt.registration_feedback
    mids, funding = broadcast(rt, 20)
    assert mids == {"BTC", "ETH", "C7"} and funding == {"BTC", "ETH", "C7"}


def test_resume_restores_the_bounded_broadcast_set():
    rt = crowded_runtime()
    rt._manage_reserve_window()
    handle = decision(rt)
    rt._apply_registrations(handle, Return(
        handle, {"register": [{"kind": "market", "coin": "C7"}]}, 0, "ok"))
    restored = crowded_runtime()
    restore_runtime(restored, runtime_state(rt))
    mids, funding = broadcast(restored, 30)
    assert mids == {"BTC", "ETH", "C7"} and funding == {"BTC", "ETH", "C7"}
