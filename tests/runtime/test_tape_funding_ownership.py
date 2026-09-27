"""Boundary ownership survives delayed publication (Chapter II §III.b)."""

from decimal import Decimal
from types import SimpleNamespace

import pytest

from factorylab.kernel.ledger import Ledger
from factorylab.runtime.resume import decode, encode
from factorylab.runtime.venue import VenueMixin
from factorylab.settlement.consequence import ReturnConsequences
from factorylab.world.exchange import NS_PER_HOUR, Position
from factorylab.world.tape import TapeVenue
from tests.world.test_live_diary import _settled_tape


def _fill(book, owner, oid, buy, ts):
    book.start(owner, 0)
    book.order_result(owner, {"status": "filled", "order_id": oid, "filled_size": "2"},
                      {"coin": "BTC"}, 0)
    book.observe("Fill", {"order_id": oid, "coin": "BTC", "is_buy": buy,
                          "size": "2", "px": "100", "fee_usd": "0", "ts_ns": ts}, 0)


@pytest.mark.parametrize("replacement", [False, True])
def test_delayed_funding_keeps_boundary_owner_after_close_and_resume(tmp_path, replacement):
    tape = _settled_tape(tmp_path, repeats=True)
    tape.data["settled_funding"]["BTC"][1]["rate"] = "0.02"
    h = NS_PER_HOUR
    book = ReturnConsequences(Ledger(), 10)
    _fill(book, "A", "a", True, h - 5)
    venue = TapeVenue(tape, coins=("BTC",), start_cash_usd=Decimal(1000))
    venue._positions["BTC"] = Position("BTC", Decimal(2), Decimal(100))
    rt = SimpleNamespace(exchange=venue, consequences=book)
    VenueMixin._advance_venue(rt, h)
    _fill(book, "closer", "c", False, h + 1)
    venue._positions.clear()
    if replacement:
        _fill(book, "B", "b", True, h + 2)
        venue._positions["BTC"] = Position("BTC", Decimal(2), Decimal(100))
    book.table = decode(encode(book.table))
    for ts in (h + 10, h + 20):
        events = VenueMixin._advance_venue(rt, ts)
        for event in events:
            if event.kind == "Funding":
                book.observe("Funding", dict(event.payload, ts_ns=event.payload["funding_ns"]), 0)
        assert book.table.account("A").realized_micro == -Decimal("2.40246913578") * (
            1 if ts == h + 10 else 2) * 1_000_000
        if replacement:
            assert book.table.account("B").realized_micro == 0
            assert all(lot.charges_micro == 0 for lot in book.table.lots)
    assert book.table.funding_allocations == ()


def test_leap_captures_boundaries_before_same_instant_arrivals(tmp_path):
    from factorylab.world.events import WorldEvent, WorldEventKind

    tape = _settled_tape(tmp_path)
    h = NS_PER_HOUR
    tape.data["ticks"] = [h - 10, 2 * h + 10]
    row = tape.data["settled_funding"]["BTC"][0]
    tape.data["settled_funding"]["BTC"] = [
        dict(row, published_at_ns=2 * h + 10),
        dict(row, funding_ns=2 * h, published_at_ns=2 * h + 10)]
    book = ReturnConsequences(Ledger(), 10)
    _fill(book, "A", "a", True, h - 5)
    book.start("closer", 0)
    book.order_result("closer", {"status": "filled", "order_id": "c", "filled_size": "2"},
                      {"coin": "BTC"}, 0)
    venue = TapeVenue(tape, coins=("BTC",), start_cash_usd=Decimal(1000))
    venue._positions["BTC"] = Position("BTC", Decimal(2), Decimal(100))
    close = {"order_id": "c", "coin": "BTC", "is_buy": False, "size": "2",
             "px": "100", "fee_usd": "0", "ts_ns": 2 * h + 10}
    venue._arrive = lambda: [WorldEvent(WorldEventKind.FILL, 2 * h + 10, "test", close)]
    rt = SimpleNamespace(exchange=venue, consequences=book)
    events = VenueMixin._advance_venue(rt, 2 * h + 10)
    assert len(book.table.funding_allocations) == 2
    for event in events:
        book.observe(str(event.kind), dict(event.payload, ts_ns=event.payload.get(
            "funding_ns", event.ts_ns)), 0)
    assert book.table.account("A").realized_micro == -Decimal("4.80493827156") * 1_000_000
    assert book.table.funding_allocations == ()


def test_boundary_allocation_preserves_unowned_share_and_zero_payment():
    from dataclasses import replace
    from fractions import Fraction

    from factorylab.settlement.lots import Lot, LotTable

    table = LotTable().start("A", 0).start("B", 0)
    table = replace(table, lots=(
        Lot("A", "BTC", True, Fraction(1), Fraction(100), Fraction(0)),
        Lot(None, "BTC", True, Fraction(1), Fraction(100), Fraction(0))))
    charged = table.capture_funding("BTC", 10).funding("BTC", "2", boundary=10, final=True)
    assert charged.account("A").realized_micro == -1_000_000
    assert charged.released_late == ()
    offset = replace(table, lots=(table.lots[0], replace(table.lots[1], handle="B", is_buy=False)))
    zero = offset.capture_funding("BTC", 10).funding("BTC", "0", boundary=10, final=True)
    assert zero.account("A").realized_micro == zero.account("B").realized_micro == 0
    assert zero.funding_allocations == ()


def test_tape_boundary_sizes_retained_only_until_last_publication(tmp_path):
    tape = _settled_tape(tmp_path, repeats=True)
    tape.data["settled_funding"]["BTC"][1]["rate"] = "0.02"
    venue = TapeVenue(tape, coins=("BTC",), start_cash_usd=Decimal(1000))
    h = NS_PER_HOUR
    venue.advance(h)
    assert h in venue._funding_sizes
    venue.advance(h + 10)
    assert h in venue._funding_sizes
    venue.advance(h + 20)
    assert venue._funding_sizes == {}


def test_legacy_checkpoint_defaults_empty_boundary_allocations():
    from factorylab.settlement.lots import LotTable

    value = encode(LotTable())
    value["fields"].pop("funding_allocations", None)
    assert decode(value).funding_allocations == ()
