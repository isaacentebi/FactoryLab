"""A live fills read is complete before any watermark passes it (Codex on #152).

Hyperliquid's ``userFillsByTime`` answers at most 2,000 rows. A read that stopped at one
page let the fills watermark jump to now, so a return could resolve at its horizon
before older fills (after an outage) were applied. ``fills()`` now reads page after
page from the latest millisecond, deduplicated by trade id, until a short page proves
the rest delivered; a page that fails leaves the watermark where it was.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from factorylab.kernel.ledger import Ledger
from factorylab.settlement.consequence import FillCursor
from factorylab.world.exchange import FILLS_PAGE, HyperliquidExchange, VenueUnavailable

NS_PER_MS = 1_000_000


def _row(tid: int, ms: int, oid: int = 7) -> dict:
    return {"tid": tid, "oid": oid, "coin": "BTC", "side": "B", "sz": "0.001",
            "px": "60000", "fee": "0.01", "closedPnl": "0", "time": ms}


def _venue(fetch) -> HyperliquidExchange:
    ex = HyperliquidExchange.__new__(HyperliquidExchange)
    ex.coins, ex.spot_pairs = ("BTC",), ()
    ex._sz_decimals, ex._spot_names, ex._spot_tokens = {"BTC": 5}, {}, {}
    ex._address = "synthetic"
    ex._guarded = lambda name, call: call()
    ex._info = SimpleNamespace(user_fills_by_time=fetch)
    return ex


#: A full first page ending at 2,000 ms, then the rest: two more rows at that same
#: millisecond (one the first page already held) and one after it.
FIRST = [_row(i, i + 1) for i in range(FILLS_PAGE)]
REST = [_row(FILLS_PAGE - 1, FILLS_PAGE), _row(FILLS_PAGE, FILLS_PAGE, oid=8),
        _row(FILLS_PAGE + 1, FILLS_PAGE + 1, oid=8)]


def test_a_full_page_is_followed_to_the_end_and_boundary_peers_are_kept_once():
    calls = []

    def fetch(user, start):
        calls.append(start)
        return FIRST if start == 0 else REST

    fills = _venue(fetch).fills(0)
    assert calls == [0, FILLS_PAGE]
    assert len(fills) == FILLS_PAGE + 2  # every trade once, the reread boundary included


@pytest.mark.parametrize("since_ms, complete", [(0, False), (1, False)])
def test_retained_history_cap_is_unknown_and_survives_journal_replay(since_ms, complete):
    from factorylab.runtime.resume import RecoveryJournal
    from factorylab.world.exchange import FILLS_HISTORY

    history = [_row(i, i + 1) for i in range(FILLS_HISTORY)]
    venue = _venue(lambda user, start: [row for row in history if row["time"] >= start]
                   [:FILLS_PAGE])
    ledger = Ledger()
    journal = RecoveryJournal(ledger, lambda: 0)
    journal.active = True
    fills = journal.call("exchange.fills", venue.fills, (since_ms * NS_PER_MS,), {})
    assert len(fills) == FILLS_HISTORY
    assert all(fill.history_complete == complete for fill in fills)
    replay = RecoveryJournal(ledger, lambda: 0)
    replay.io_store = journal.io_store
    replay.active = True
    replay.tail = [{**row, "ts": 0} for row in ledger._recovery_items()]

    def refuse(*args):
        raise AssertionError("replay cannot reread the venue")

    recovered = replay.call("exchange.fills", refuse, (since_ms * NS_PER_MS,), {})
    assert recovered == fills
    cursor = FillCursor(Ledger(), start_ns=since_ms * NS_PER_MS, measured=True)
    assert len(cursor.poll(SimpleNamespace(fills=lambda start: recovered),
                           now_ns=fills[0].observed_at_ns)) == FILLS_HISTORY
    assert (cursor.through_ns is not None) == complete
    row = [r for r in cursor.ledger._recovery_items()
           if r["kind"] == "consequence.fill_propagation"][-1]
    assert row["history_complete"] == complete


@pytest.mark.parametrize("bad", [None, {"coin": "BTC"},
                                    {**_row(2, 2), "sz": "NaN"},
                                    {**_row(2, 2), "side": "X"},
                                    {**_row(2, 2), "coin": "BTC/USDC", "feeToken": "OTHER"}])
@pytest.mark.parametrize("valid_peer", [False, True])
def test_any_unrepresentable_execution_fails_entire_read(bad, valid_peer):
    rows = ([_row(1, 1)] if valid_peer else []) + [bad]
    venue = _venue(lambda user, start: rows)
    venue._is_spot = lambda coin: coin == "BTC/USDC"
    venue._public_coin = lambda coin: coin
    with pytest.raises(VenueUnavailable, match="fill"):
        venue.fills(0)


def test_anchor_read_passes_upper_bound_to_every_inclusive_page():
    calls = []

    def fetch(user, start, *, end_time):
        calls.append((start, end_time))
        return FIRST if start == 0 else REST[:2]

    fills = _venue(fetch).fills(0, until_ns=FILLS_PAGE * NS_PER_MS)
    assert calls == [(0, FILLS_PAGE), (FILLS_PAGE, FILLS_PAGE)]
    assert len(fills) == FILLS_PAGE + 1
    assert all(f.ts_ns <= FILLS_PAGE * NS_PER_MS for f in fills)


def test_a_stalled_full_page_fails_closed():
    same = [_row(i, 5) for i in range(FILLS_PAGE)]
    with pytest.raises(VenueUnavailable, match="stalled"):
        _venue(lambda user, start: same).fills(5 * NS_PER_MS)


def test_the_watermark_does_not_pass_fills_not_yet_read(monkeypatch):
    """The second page fails once: the poll fails, nothing is consumed and the
    watermark stays; the next poll reads both pages and only then advances it."""
    outage = {"second": True}

    def fetch(user, start):
        if start == 0:
            return FIRST
        if outage["second"]:
            raise VenueUnavailable("second page unanswered")
        return REST

    venue = _venue(fetch)
    cursor = FillCursor(Ledger(), start_ns=0, measured=True)
    monkeypatch.setattr("time.time_ns", lambda: 10_010 * NS_PER_MS)
    with pytest.raises(VenueUnavailable):
        cursor.poll(venue, strict=True, now_ns=10_000 * NS_PER_MS)
    assert cursor.through_ns is None  # nothing proven delivered
    outage["second"] = False
    fills = cursor.poll(venue, strict=True, now_ns=10_001 * NS_PER_MS)
    assert len(fills) == FILLS_PAGE + 2
    assert cursor.propagation_bound_ns == 10_009 * NS_PER_MS
    assert cursor.through_ns is None  # no independent baseline/fee evidence
