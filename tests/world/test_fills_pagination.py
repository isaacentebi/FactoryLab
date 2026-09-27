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


def test_a_stalled_full_page_fails_closed():
    same = [_row(i, 5) for i in range(FILLS_PAGE)]
    with pytest.raises(VenueUnavailable, match="stalled"):
        _venue(lambda user, start: same).fills(5 * NS_PER_MS)


def test_the_watermark_does_not_pass_fills_not_yet_read():
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
    cursor = FillCursor(Ledger(), start_ns=0)
    with pytest.raises(VenueUnavailable):
        cursor.poll(venue, strict=True, now_ns=10_000 * NS_PER_MS)
    assert cursor.through_ns is None  # nothing proven delivered
    outage["second"] = False
    fills = cursor.poll(venue, strict=True, now_ns=10_001 * NS_PER_MS)
    assert len(fills) == FILLS_PAGE + 2
    assert cursor.through_ns == 10_001 * NS_PER_MS
