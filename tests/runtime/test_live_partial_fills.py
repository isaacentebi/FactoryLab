"""A live order filled in parts is booked part by part, each once (Codex on #152).

The live event stream de-duplicated fills by ORDER id, so every later part of an order
filled in pieces was dropped: realised money and positions were understated. Each
execution now has its own identity (Hyperliquid's ``hash:tid``, else its facts and
multiplicity). The consequence fill cursor never keyed by order id; both are pinned here
across two polls and a resume between them, and a checkpoint that kept order ids reads
on without doubling.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from factorylab.kernel.ledger import Ledger
from factorylab.runtime.live import LiveVenue
from factorylab.runtime.resume import decode, encode
from factorylab.settlement.consequence import FillCursor
from factorylab.world.exchange import FakeExchange, Fill

MS = 1_000_000


def _part(i: int, ts: int, *, ids: bool) -> Fill:
    return Fill(order_id="order-1", coin="BTC", is_buy=True, size=Decimal("0.001"),
                px=Decimal("60000"), fee=Decimal("0.01"), ts_ns=ts,
                fill_id=f"0xabc:{i}" if ids else None)


class PartialVenue(FakeExchange):
    """One order's three parts: the first two before the first poll, the third after."""

    shown: list

    def fills(self, since_ns):
        return [f for f in self.shown if f.ts_ns >= since_ns]


def _venue(parts) -> PartialVenue:
    venue = PartialVenue()
    venue.shown = parts
    return venue


def _fills(events) -> list:
    return [e for e in events if str(e.kind) == "Fill"]


def _restored(live: LiveVenue, exchange) -> LiveVenue:
    """The venue's checkpointed fill state, through the checkpoint's own encoding."""
    state = decode(encode({"last_fill_ns": live.last_fill_ns, "seen_fills": live.seen_fills}))
    return LiveVenue(exchange, last_fill_ns=state["last_fill_ns"],
                     seen_fills=set(state["seen_fills"]))


@pytest.mark.parametrize("ids", [True, False], ids=["venue-fill-id", "facts-only"])
@pytest.mark.parametrize("resume", [False, True], ids=["live", "resumed"])
def test_an_order_filled_in_three_parts_is_booked_three_times_once_each(ids, resume):
    parts = [_part(1, 10 * MS, ids=ids), _part(2, 20 * MS, ids=ids),
             _part(3, 30 * MS, ids=ids)]
    exchange = _venue(parts[:2])
    live = LiveVenue(exchange, last_fill_ns=0)
    first = _fills(live.on_tick(25 * MS))
    assert [e.payload["order_id"] for e in first] == ["order-1", "order-1"]
    if resume:
        live = _restored(live, exchange)
    exchange.shown = parts  # the third part, and the second again (an inclusive read)
    second = _fills(live.on_tick(35 * MS))
    assert [e.ts_ns for e in second] == [35 * MS]  # the third part only
    assert len(first) + len(second) == 3


def test_a_checkpoint_that_kept_order_ids_neither_doubles_nor_drops_a_later_part():
    """Written before: ``seen_fills`` held the order id, read through 20 ms. The part at
    20 ms was emitted then and is not again; the part at 30 ms is new and is."""
    parts = [_part(1, 10 * MS, ids=True), _part(2, 20 * MS, ids=True),
             _part(3, 30 * MS, ids=True)]
    live = LiveVenue(_venue(parts), last_fill_ns=20 * MS, seen_fills={"order-1"})
    assert [e.ts_ns for e in _fills(live.on_tick(35 * MS))] == [35 * MS]


def test_the_consequence_cursor_books_each_part_once_across_a_resume():
    parts = [_part(1, 10 * MS, ids=True), _part(2, 20 * MS, ids=True),
             _part(3, 30 * MS, ids=True)]
    exchange = _venue(parts[:2])
    cursor = FillCursor(Ledger(), start_ns=0)
    first = cursor.poll(exchange, strict=True, now_ns=25 * MS)
    state = decode(encode({k: getattr(cursor, k) for k in ("since_ns", "seen", "through_ns")}))
    restored = FillCursor(Ledger(), start_ns=0)
    for name, value in state.items():
        setattr(restored, name, value)
    exchange.shown = parts
    second = restored.poll(exchange, strict=True, now_ns=35 * MS)
    assert [ts for ts, _ in first] == [10 * MS, 20 * MS]
    assert [ts for ts, _ in second] == [30 * MS]
