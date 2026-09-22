"""The scripted treasury reads its custodians once per change, not once per pot view.

Every ``treasury.rail.balances`` read (and the venue ``account`` read inside it)
is recorded I/O. The fake treasury's pot view read the rail three times per view,
and a world block builds several views, so a 100-event world recorded ~120k
``io.call``/``io.result`` items of the same numbers. ``FakeTreasury`` now holds the
read, keyed on the journal's count of venue and treasury writes and on the pots
the rail reads directly, above the recorded-I/O layer; ``_snapshot`` drops it, and
it is never in a checkpoint.
"""

from __future__ import annotations

from decimal import Decimal

from factorylab.runtime.loop import Runtime
from factorylab.runtime.resume import decode, runtime_state
from factorylab.runtime.worlds import load_manifest
from factorylab.world.exchange import FakeExchange, Order
from factorylab.world.scripted import ScriptedProvider


def pot_runtime() -> Runtime:
    rt = Runtime(load_manifest("scripted"), events=0, seed=1,
                 initial_balance_micro=100_000_000, ledger_path=None, router_gamma=.1,
                 provider=ScriptedProvider(),
                 exchange=FakeExchange(start_cash_usd=Decimal("1000")))
    rt.ledger.active = True  # ``run`` opens the journal; these tests call pots directly
    rt.treasury.forget_observations()  # start from nothing held
    return rt


def rail_reads(rt: Runtime) -> int:
    return len([i for i in rt.ledger._recovery_items()
                if i["kind"] == "io.call" and i["name"] == "treasury.rail.balances"])


def test_repeated_pot_views_read_the_rail_once():
    rt = pot_runtime()
    before = rail_reads(rt)
    first = rt.wallet.pots()
    for _ in range(5):
        assert rt.wallet.pots() == first
    assert rail_reads(rt) - before == 1


def test_a_venue_write_makes_the_next_view_read_again_and_see_it():
    rt = pot_runtime()
    rt.wallet.pots()
    before = rail_reads(rt)
    rt.exchange.place(Order("BTC", True, Decimal("0.001")))  # a recorded venue write
    rt.exchange.advance(1)
    after = rt.wallet.pots()
    assert rail_reads(rt) - before == 1
    fresh = rt.treasury.rail.target.balances()  # the custodians, read unrecorded
    assert after["venue"] == fresh["venue"] and after["perps"] == fresh["perps"]


def test_a_directly_moved_rail_pot_is_seen_without_a_write():
    rt = pot_runtime()
    rt.wallet.pots()
    rt.treasury.rail.reserve += 7
    assert rt.wallet.pots()["reserve"] == rt.treasury.rail.reserve


def test_the_held_read_is_dropped_at_a_checkpoint_and_never_saved():
    rt = pot_runtime()
    rt.wallet.pots()
    assert rt.treasury._balances_memo is not None
    assert "balances_memo" not in repr(decode(runtime_state(rt)["treasury"]))
    assert rt._snapshot("test") is True
    assert rt.treasury._balances_memo is None
    before = rail_reads(rt)
    rt.wallet.pots()
    assert rail_reads(rt) - before == 1
