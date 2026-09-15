"""The venue's listing is read once a tick, not once a prompt.

A live venue's listing runs to thousands of instruments and about 260 KB, and
every read of it through the recorded-I/O layer is written into the diary in
full. A tick that builds prompts for producers, judges and meta judges read it
once per prompt: a twenty-minute rehearsal wrote 21 MB of the same listing, and
``deploy/backup.sh`` copies the diary. The runtime now holds the raw listing for
the tick that read it.

The memo sits above the recorder, so a replayed diary sees exactly the calls
that were recorded, and it is not resumable state: the first prompt after a
resume reads afresh and records that read like any other.
"""

from __future__ import annotations

from factorylab.runtime.loop import Runtime
from factorylab.runtime.resume import decode, runtime_state
from factorylab.runtime.worlds import load_manifest
from factorylab.world.exchange import FakeExchange
from factorylab.world.scripted import ScriptedProvider


def padded_runtime() -> Runtime:
    """A scripted runtime whose venue lists far more than the world may trade."""
    exchange = FakeExchange(
        coins=("BTC", "ETH"), spot_pairs=("BTC/USDC",),
        listed_coins=tuple(f"C{i:04d}" for i in range(212)),
        listed_spot_pairs=tuple(f"S{i:04d}/USDC" for i in range(321)),
    )
    rt = Runtime(load_manifest("scripted"), events=0, seed=1,
                 initial_balance_micro=100_000_000, ledger_path=None, drip=False,
                 router_gamma=.1, exchange=exchange, provider=ScriptedProvider())
    # ``run`` opens the journal; these tests build prompts without running the loop.
    rt.ledger.active = True
    return rt


def listing_reads(rt: Runtime) -> int:
    """Recorded ``exchange.instruments`` calls: exactly what a resume must replay."""
    return len([i for i in rt.ledger._recovery_items()
                if i["kind"] == "io.call" and i["name"] == "exchange.instruments"])


def test_two_prompts_in_one_tick_record_one_listing_read():
    rt = padded_runtime()
    before = listing_reads(rt)
    first, second = rt._world_block(), rt._world_block()
    assert first["venue"] == second["venue"]
    assert first["venue"]["perp"] and first["venue"]["spot"]  # the block is still populated
    assert listing_reads(rt) - before == 1


def test_the_next_tick_reads_the_listing_again():
    rt = padded_runtime()
    before = listing_reads(rt)
    rt._world_block()
    rt.ticks_consumed += 1
    rt._world_block()
    rt._world_block()
    assert listing_reads(rt) - before == 2


def test_the_memo_is_not_resumable_state():
    """A resumed runtime reads the listing afresh and records that read."""
    rt = padded_runtime()
    rt._world_block()
    saved = decode(runtime_state(rt)["runtime"])
    assert not [name for name in saved if "instrument" in name]

    resumed = padded_runtime()
    resumed.ticks_consumed = rt.ticks_consumed
    before = listing_reads(resumed)
    resumed._world_block()
    assert listing_reads(resumed) - before == 1


def test_a_checkpoint_carries_no_listing_and_the_run_holds_none_past_it():
    """What makes the memo safe to leave out of a checkpoint: both sides drop it there.

    A resume restores a checkpoint holding no listing; the run that wrote the
    checkpoint holds none past it either, so the replayed tail asks the venue
    exactly where the recorded tail did.
    """
    rt = padded_runtime()
    rt._world_block()
    assert rt._instruments_memo is not None
    assert rt._snapshot("test") is True
    assert rt._instruments_memo is None
    before = listing_reads(rt)
    rt._world_block()
    assert listing_reads(rt) - before == 1


def test_a_market_registered_in_the_tick_still_finds_its_record():
    """The memo holds the raw listing, so a new traded market is served from it."""
    rt = padded_runtime()
    rt._world_block()
    before = listing_reads(rt)
    rt.venue_tools.coins = (*rt.venue_tools.coins, "C0100")
    venue = rt._world_block()["venue"]
    assert "C0100" in {row["coin"] for row in venue["perp"]}
    assert listing_reads(rt) == before
