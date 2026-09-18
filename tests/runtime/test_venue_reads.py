"""The venue's mids and account state are read once a tick for prompts, not once a prompt.

Rehearsal 3, defect 2. In five hours the runtime read ``exchange.mids`` 860 times
and recorded each result in full — every listed instrument, about 58 KB a read —
which was 50 MB of a 194 MB diary, and read ``exchange.account`` 4,198 times for
another 3.5 MB. Both are the same shape as the venue listing #89 memoised: a tick
builds a dozen prompts for producers, judges and meta judges, and every one of
them asked the venue again for facts that do not move inside the tick.

The memo sits above the recorded-I/O layer, so a replayed diary sees exactly the
calls that were recorded, and ``_snapshot`` drops it at every checkpoint so a
replayed tail asks the venue where the recorded run did. It is not resumable
state: the first prompt after a resume reads afresh and records that read.

What is not memoised is anything a price or a balance has a consequence for. The
population's own paid ``venue.mids`` and ``venue.positions`` calls, the
pre-submission collateral check's account read, fills, funding and the position
marking at a window boundary all read the venue itself.
"""

from __future__ import annotations

from decimal import Decimal

from factorylab.kernel.queue import PropensityRecord
from factorylab.runtime.loop import Runtime
from factorylab.runtime.worlds import load_manifest
from factorylab.world.exchange import FakeExchange
from factorylab.world.scripted import ScriptedProvider


def venue_runtime() -> Runtime:
    """A scripted runtime with a venue rich enough to carry the orders here."""
    rt = Runtime(load_manifest("scripted"), events=0, seed=1,
                 initial_balance_micro=100_000_000, ledger_path=None, drip=False,
                 router_gamma=.1, provider=ScriptedProvider(),
                 exchange=FakeExchange(start_cash_usd=Decimal("1000")))
    # The scripted world's fake treasury recomputes its pot view from the venue on
    # every read, which no live treasury does — ``Treasury.pots`` serves the cached
    # observation ``refresh_pots`` wrote. Detaching the fake rail's venue view leaves
    # the account reads in these counts as the ones the prompt path is answerable for.
    rt.treasury.rail.exchange = None
    # ``run`` opens the journal; these tests build prompts without running the loop.
    rt.ledger.active = True
    rt._manage_reserve_window()
    return rt


def reads(rt: Runtime, name: str) -> int:
    """Recorded venue calls by name: exactly what a resume must replay."""
    return len([i for i in rt.ledger._recovery_items()
                if i["kind"] == "io.call" and i["name"] == name])


def place(rt: Runtime, size: str) -> dict:
    owner = "seed-decider"
    handle = rt.queue.open(
        actor=owner, event_id="venue-reads", propensity=PropensityRecord(
            (owner,), (1.,), owner, 0, owner, "test"), channel="verdict",
        deadline_ns=rt.clock.now_ns + 10**12, parent_handle=None, cost_ceiling=10_000_000)
    rt.handle_to_assembly[handle] = owner
    rt.consequences.start(handle, rt.n)
    return rt._run_tool(owner, handle, {
        "tool": "venue.place_market",
        "args": {"coin": "BTC", "side": "buy", "size": size}})[0]


def test_the_paid_population_reads_are_never_served_from_the_memo():
    """A seat that pays for a venue read is told what the venue says now."""
    rt = venue_runtime()
    rt._tick_mids(), rt._world_block()
    before = (reads(rt, "exchange.mids"), reads(rt, "exchange.account"))
    assert Decimal(rt.venue_tools.call("venue.mids", {})["mids"]["BTC"]) > 0
    assert "positions" in rt.venue_tools.call("venue.positions", {})
    assert reads(rt, "exchange.mids") - before[0] == 1
    assert reads(rt, "exchange.account") - before[1] == 1


def test_an_order_and_its_settlement_in_the_same_tick_read_the_account_fresh():
    """A consequence is weighed against the account as it is, never against the tick's memo."""
    rt = venue_runtime()
    rt._tick_mids(), rt._world_block()
    before = reads(rt, "exchange.account")
    assert place(rt, "0.002")["status"] == "filled"
    # The collateral check reads the account itself, and so does every settlement
    # path that follows the fill.
    assert reads(rt, "exchange.account") > before
    marked = reads(rt, "exchange.account")
    rt._observe_positions()
    assert reads(rt, "exchange.account") > marked
    assert rt._equity_micro() and reads(rt, "exchange.account") > marked + 1
