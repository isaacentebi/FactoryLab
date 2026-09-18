"""An order's fills never execute more than it ordered (defect 9).

The lot table tracked only an order's remaining quantity, clipped at zero, so a
fill of 2 BTC on a 1 BTC order opened a 2 BTC lot for the ordering return. An
order now remembers what it ordered and what has executed against it: a fill
beyond that bound is an inconsistency, quarantined rather than attributed, while
a legitimate late fill after a cancel is still accepted up to the original size.
"""

from fractions import Fraction

import pytest

from factorylab.settlement.consequence import ReturnConsequences
from factorylab.settlement.lots import LotTable


def _fill(table, order_id, size, **extra):
    return table.fill(order_id=order_id, coin="BTC", is_buy=True, size=size, px="100",
                      fee_usd="0", **extra)


def _table():
    return LotTable().start("h", 0).order("o1", "h", "1")


def test_a_fill_beyond_the_ordered_quantity_is_refused_by_the_table():
    with pytest.raises(ValueError, match="exceeds"):
        _fill(_table(), "o1", "2")


def test_a_late_fill_after_cancel_is_accepted_up_to_the_original_size():
    table = _fill(_table(), "o1", "0.6").cancel("o1")
    table = _fill(table, "o1", "0.4")  # executed before the cancel took effect
    assert sum(lot.size for lot in table.lots) == Fraction(1)
    with pytest.raises(ValueError, match="exceeds"):
        _fill(table, "o1", "0.1")


def test_the_consequence_book_quarantines_an_inconsistent_fill(ledger):
    book = ReturnConsequences(ledger, 200)
    book.start("h", 0)
    book.order_result("h", {"status": "resting", "order_id": "o1"}, {"size": "1"}, 0)
    book.observe("Fill", {"order_id": "o1", "coin": "BTC", "is_buy": True, "size": "2",
                          "px": "100", "fee_usd": "0"}, 1)
    assert book.table.lots == ()
    items = ledger._recovery_items()
    quarantined = [i for i in items if i["kind"] == "consequence.quarantined"]
    assert len(quarantined) == 1 and quarantined[0]["order_id"] == "o1"
    # The order's own later, consistent fill is still its owner's.
    book.observe("Fill", {"order_id": "o1", "coin": "BTC", "is_buy": True, "size": "1",
                          "px": "100", "fee_usd": "0"}, 2)
    assert sum(lot.size for lot in book.table.lots) == Fraction(1)
