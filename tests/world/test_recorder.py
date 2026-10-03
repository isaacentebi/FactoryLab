"""The broad-universe tape recorder: batched public reads, replayable output.

A tape is the world (AGENTS.md). Each test attempts to violate one invariant of the
recorder (factorylab/world/recorder.py) and asserts it fails: its reads per poll never
grow with the markets (Chapter II §IV.c), an unanswered read is absent rather than an
old answer (§III.b), and what it writes is a tape a fake venue replays.
"""

from __future__ import annotations

from collections import Counter
from decimal import Decimal

import pytest

from factorylab.world.exchange import Order, OrderKind
from factorylab.world.recorder import JOURNAL_FORMAT, Recorder, compact, read_journal, record
from factorylab.world.tape import Tape, TapeVenue

S = 10**9


class Venue:
    """Hyperliquid's public info endpoint, as a function of the request body."""

    def __init__(self, n: int):
        self.n, self.calls, self.fail, self.step = n, Counter(), set(), 0
        self.perps = ["BTC", *(f"C{i}" for i in range(n))]

    def __call__(self, body: dict):
        kind, dex = body["type"], body.get("dex", "")
        self.calls[(kind, dex)] += 1
        if (kind, dex) in self.fail:
            raise OSError("unanswered")
        if kind == "spotMeta":
            return {"tokens": [{"index": 0, "name": "USDC", "szDecimals": 8},
                               {"index": 1, "name": "PURR", "szDecimals": 0},
                               {"index": 2, "name": "USDH", "szDecimals": 2}],
                    "universe": [{"name": "PURR/USDC", "tokens": [1, 0], "index": 0},
                                 {"name": "@7", "tokens": [2, 0], "index": 7},
                                 {"name": "@8", "tokens": [1, 2], "index": 8}]}
        if kind == "meta":
            return self._meta(dex)
        if kind == "userFees":
            return {"userCrossRate": "0.00045", "userAddRate": "0.00015",
                    "userSpotCrossRate": "0.0007", "userSpotAddRate": "0.0004",
                    "activeReferralDiscount": "0.0"}
        if kind == "allMids":
            if dex:
                return {"xyz:TSLA": str(200 + self.step)}
            return {**{c: str(100 + self.step) for c in self.perps}, "PURR/USDC": "0.2",
                    "@7": "1.0", "#10": "0.5"}
        if kind == "metaAndAssetCtxs":
            meta = self._meta(dex)
            return [meta, [{"funding": "0.0001", "premium": "0"} for _ in meta["universe"]]]
        if kind == "l2Book":
            return {"time": 1_000 + self.step,
                    "levels": [[{"px": "99", "sz": "5"}] * 30, [{"px": "101", "sz": "5"}] * 30]}
        raise AssertionError(kind)

    def _meta(self, dex):
        if dex:
            return {"collateralToken": 0, "universe": [
                {"name": "xyz:TSLA", "szDecimals": 3, "maxLeverage": 10,
                 "deployerFeeScale": "1.0", "marginMode": "noCross"}]}
        return {"universe": [{"name": c, "szDecimals": 2, "maxLeverage": 20}
                             for c in self.perps]}


def recorder(venue, **kw):
    clock = iter(range(10 * S, 10**6 * S, 10 * S))
    return Recorder(venue, coins=["*", "xyz:*"], spot_pairs=["*/USDC"],
                    clock=lambda: next(clock), source="hyperliquid-test", **kw)


@pytest.mark.parametrize("n", [3, 3000])
def test_a_poll_sends_the_same_reads_whatever_the_number_of_markets(n):
    venue = Venue(n)
    rec = recorder(venue, books_per_poll=4)
    rec.start()
    venue.calls.clear()
    poll = rec.poll()
    assert venue.calls == Counter({("allMids", ""): 1, ("allMids", "xyz"): 1,
                                   ("metaAndAssetCtxs", ""): 1,
                                   ("metaAndAssetCtxs", "xyz"): 1, ("l2Book", ""): 4})
    assert len(poll["mids"]) == n + 4  # every perp, the dex's, and both USDC pairs
    assert "#10" not in poll["mids"]  # an outcome coin is not a market of the universe
    assert poll["weight"] == 2 + 2 + 20 + 20 + 4 * 2


def test_books_rotate_through_the_universe_at_the_bounded_depth():
    venue = Venue(3)
    rec = recorder(venue, books_per_poll=2, book_depth=3)
    header = rec.start()
    seen = [m for _ in range(4) for m in rec.poll()["books"]]
    assert seen == (header["markets"] * 2)[:8]  # every market in turn, then again
    book = rec.poll()["books"]
    assert all(len(side) == 3 for row in book.values() for side in row[1:])


def test_an_unanswered_read_is_absent_never_an_older_answer():
    venue = Venue(3)
    rec = recorder(venue)
    rec.start()
    rec.poll()
    venue.fail.add(("allMids", "xyz"))
    poll = rec.poll()
    assert "xyz:TSLA" not in poll["mids"] and "BTC" in poll["mids"]
    assert poll["failed"] == ["allMids:xyz:OSError"]


def test_the_listing_states_each_markets_terms_and_its_hip3_fee():
    header = recorder(Venue(3)).start()
    rows = {row["coin"]: row for kind in ("perp", "spot") for row in header["instruments"][kind]}
    assert header["markets"] == ["BTC", "C0", "C1", "C2", "xyz:TSLA", "PURR/USDC", "USDH/USDC"]
    tsla = rows["xyz:TSLA"]
    assert (tsla["dex"], tsla["margin"], tsla["max_leverage"]) == ("xyz", "isolated", 10)
    assert (tsla["taker_fee_rate"], tsla["tick_size"]) == ("0.0009", "0.001")
    assert (rows["PURR/USDC"]["taker_fee_rate"], rows["PURR/USDC"]["tick_size"]) == (
        "0.0007", "1E-8")
    assert header["wire"]["USDH/USDC"] == "@7"


def test_a_recording_compacts_to_a_tape_a_fake_venue_replays(tmp_path):
    venue = Venue(3)
    rec = recorder(venue, books_per_poll=7)

    def advance(_seconds):
        venue.step += 1

    journal = record(rec, tmp_path / "t.journal.jsonl", polls=4, interval_s=10, sleep=advance)
    header, polls = read_journal(journal)
    assert header["format"] == JOURNAL_FORMAT and header["interval_s"] == 10
    tape = Tape.from_data(compact(header, polls))
    assert tape.data["venue"] == "hyperliquid-test"
    assert tape.data["declared_tick_ns"] == 10 * S
    assert len(tape.ticks) == 4 and "xyz:TSLA" in tape.perps
    # Funding kept only where it changed: one row a perp for an unchanging rate.
    assert [len(rows) for rows in tape.data["funding"].values()] == [1] * 5
    assert tape.data["funding_regime"]["xyz:TSLA"] == "legacy"
    assert tape.fees("xyz:TSLA", tape.start_ns) == (Decimal("0.0009"), Decimal("0.0003"))
    replay = TapeVenue(tape, coins=("xyz:TSLA",), start_cash_usd=Decimal(1000))
    assert replay.place(Order("xyz:TSLA", True, Decimal("1"), OrderKind.MARKET)).status == (
        "resting")
    replay.advance(tape.ticks[2])
    fills = replay.fills(0)
    assert fills and fills[0].coin == "xyz:TSLA"


def test_a_journal_that_is_not_a_recording_is_refused(tmp_path):
    path = tmp_path / "x.jsonl"
    path.write_text('{"format": "something-else"}\n')
    with pytest.raises(ValueError, match="not a factorylab tape journal"):
        read_journal(path)


def test_a_poll_is_stamped_when_its_answers_arrived_never_before():
    """Codex P2 on #178: a poll stamped before its reads backdated a slow read's facts,
    a look-ahead on replay. The stamp is taken after the last answer arrived."""
    now = [100 * S]
    venue = Venue(3)

    def slow(body):
        answer = venue(body)
        now[0] += 5 * S  # every read takes five seconds
        return answer

    rec = Recorder(slow, coins=["*"], spot_pairs=["*/USDC"], clock=lambda: now[0],
                   books_per_poll=2)
    rec.start()
    before = now[0]
    poll = rec.poll()
    assert poll["ts"] == now[0] > before


def _future_tape(tmp_path):
    """A broad recording (first-dex perps, an xyz perp, USDC pairs) after every cutoff."""
    venue = Venue(3)
    clock = iter(range(2_000_000_000 * S, 2_000_001_000 * S, 10 * S))
    rec = Recorder(venue, coins=["*", "xyz:*"], spot_pairs=["*/USDC"],
                   clock=lambda: next(clock), source="hyperliquid-test", books_per_poll=7)

    def advance(_seconds):
        venue.step += 1

    journal = record(rec, tmp_path / "f.journal.jsonl", polls=4, interval_s=10, sleep=advance)
    return Tape.from_data(compact(*read_journal(journal)))


def test_a_breadth_replay_keeps_its_hip3_markets(tmp_path):
    """Codex P1 on #178: the simulated manifest stripped HIP-3 names for every fake
    venue, so a tape that recorded xyz:TSLA replayed first-dex perps only. Only the
    random walk, which lists no HIP-3 dex, drops them."""
    from dataclasses import replace

    from factorylab.runtime.loop import Runtime
    from factorylab.runtime.worlds import WORLDS_DIR
    from factorylab.world.scripted import ScriptedProvider
    from scripts import fastloop

    tape = _future_tape(tmp_path)
    world = WORLDS_DIR / "edition7-breadth-testnet.toml"
    walked = fastloop.simulation_manifest(world, 1)
    assert "xyz:*" not in walked.exchange.coins
    manifest = fastloop.simulation_manifest(world, 1, tape=tape, allow_unknown_cutoff=True)
    assert "xyz:*" in manifest.exchange.coins
    manifest = replace(manifest, assemblies=tuple(
        replace(a, max_tokens=1024) for a in manifest.assemblies))
    rt = Runtime(manifest, events=0, seed=1, initial_balance_micro=None, ledger_path=None,
                 provider=ScriptedProvider(),
                 exchange=fastloop.tape_venue(tape, manifest))
    assert "xyz:TSLA" in rt.universe["coins"] and "BTC" in rt.universe["coins"]
