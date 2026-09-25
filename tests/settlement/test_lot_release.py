"""Closed, fully settled accounts are released into counts (wave 17b).

Essay II.IV.c: a consequence is "consumed ... and then discarded"; what persists is
aggregates. A released account's counts survive in the table; its orders keep their
owner for a horizon, so a very late fill on one is refused by name and ledgered,
never pooled, never a crash and never a silent drop.
"""

import pytest

from factorylab.kernel.ledger import Ledger
from factorylab.settlement.consequence import ReturnConsequences
from factorylab.settlement.lots import RELEASED_ORDER, LotTable


def _closed_table():
    """Three returns: one that traded and closed its lot, one voided, one still holding."""
    table = LotTable()
    for handle, event in (("opener", 1), ("closer", 2), ("holder", 3), ("hold", 4)):
        table = table.start(handle, event)
    table = table.finish("opener", 10).finish("closer", 10).finish("holder", 10)
    table = table.void("hold")
    table = table.order("o1", "opener", "1").fill(order_id="o1", coin="BTC", is_buy=True,
                                                 size="1", px="100", fee_usd="0")
    table = table.order("o2", "closer", "1").fill(order_id="o2", coin="BTC", is_buy=False,
                                                 size="1", px="120", fee_usd="0")
    table = table.order("o3", "holder", "1").fill(order_id="o3", coin="BTC", is_buy=True,
                                                 size="1", px="110", fee_usd="0")
    # A cancelled order that could still fill: its liability is gone, its size is not.
    table = table.order("o4", "opener", "2").cancel("o4", 3)
    return table.resolve(9, 5, {"BTC": "115"})


def test_a_closed_account_is_released_and_every_count_is_kept():
    table = _closed_table()
    assert all(table.closed(h, tick=7, patience=2) for h in ("opener", "closer", "hold"))
    assert not table.closed("holder", tick=7)  # its lot is open: a late realization is owed
    ledger = Ledger()
    book = ReturnConsequences(ledger, 5)
    book.table = table
    before = book.counts()
    book.release(["opener", "closer", "hold"], mark=7, patience=2)
    assert [r.handle for r in book.table.returns] == ["holder"]
    assert book.counts() == before
    assert book.table.released_counts()["accounts"] == 3
    assert book.table.order_owner("o1") == "opener" and book.table.order_owner("o3") == "holder"
    with pytest.raises(KeyError):
        book.table.account("opener")
    assert not book.account_open("opener")


@pytest.mark.parametrize("handle", ["holder", "unknown"])
def test_invariant_an_open_account_is_never_released(handle):
    """Violation attempts: release an account holding a lot, and one never admitted."""
    table = _closed_table()
    with pytest.raises(ValueError):
        table.release([handle, "opener"], mark=1)
    book = ReturnConsequences(Ledger(), 5)
    book.table = table
    if handle == "holder":
        with pytest.raises(ValueError):
            book.release([handle], mark=1)
    assert book.table is table


def test_invariant_an_account_with_a_live_order_or_an_unanswered_intent_is_not_released():
    """Violation attempts: an unfilled liability, an unacknowledged intent, an
    unresolved one, and a deferred fill whose owner is not known yet."""
    table = LotTable().start("waiting", 1).finish("waiting", 10)
    table = table.order("o9", "waiting", "1").resolve(2, 1, {})
    assert table.account("waiting").payoff is not None
    assert not table.closed("waiting")  # its order may still fill
    with pytest.raises(ValueError):
        table.release(["waiting"], mark=1)
    book = ReturnConsequences(Ledger(), 5)
    book.table = LotTable().start("sent", 1).finish("sent", 10).resolve(2, 1, {})
    assert book.releasable("sent")
    book.order_intent("c1", "sent", "BTC")
    assert not book.releasable("sent")
    with pytest.raises(ValueError):
        book.release(["sent"], mark=1)
    book.release_unresolved("c1", 3)
    assert not book.releasable("sent")  # the venue may still admit to the order
    book.order_acknowledged("c1")
    assert book.releasable("sent")
    book.deferred_events.append(("Fill", {"order_id": "x"}, 4))
    assert not book.releasable("sent")


def test_a_very_late_fill_on_a_released_order_is_refused_by_name_and_ledgered():
    """The order was cancelled with size left, its account closed and released; the venue
    then reports a fill that executed before the cancel took effect."""
    ledger = Ledger()
    book = ReturnConsequences(ledger, 5)
    book.table = _closed_table()
    book.release(["opener", "closer"], mark=7, patience=2)
    lots = book.table.lots
    fill = {"order_id": "o4", "coin": "BTC", "is_buy": True, "size": "1", "px": "116",
            "fee_usd": "0"}
    book.observe("Fill", fill, 10)
    refused = [i for i in ledger._recovery_items() if i["kind"] == "consequence.refused"]
    assert refused[-1]["reason"] == RELEASED_ORDER and refused[-1]["handle"] == "opener"
    assert refused[-1]["payload"] == fill
    assert book.table.lots == lots  # no lot moved, nobody else was credited
    # Past the horizon the order is forgotten: the fill names an order no account owns,
    # and is refused as such, still ledgered.
    book.forget_released_orders(8)
    assert book.table.order_owner("o4") is None
    book.observe("Fill", {**fill, "px": "117"}, 11)
    refused = [i for i in ledger._recovery_items() if i["kind"] == "consequence.refused"]
    assert refused[-1]["reason"] == "fill without an open consequence account"
    assert book.table.lots == lots


def test_the_released_counts_survive_a_checkpoint_codec():
    from factorylab.runtime.resume import decode, encode

    table = _closed_table().release(["opener", "closer", "hold"], mark=7, patience=2)
    restored = decode(encode(table))
    assert restored == table
    assert restored.released_counts() == table.released_counts()
    assert restored.order_owner("o4") == "opener"
    # A table checkpointed before release restores with nothing released.
    older = encode(_closed_table())
    older["fields"].pop("released")
    older["fields"].pop("released_orders")
    assert decode(older).released_counts()["accounts"] == 0


def test_invariant_an_account_owed_late_money_is_not_released():
    """Violation attempt: a marked outcome whose lot closed after the mark has realised
    money its owner has not been booked yet; it is released only once booked."""
    table = LotTable().start("marked", 1).finish("marked", 10).start("closer", 2)
    table = table.finish("closer", 10)
    table = table.order("m1", "marked", "1").fill(order_id="m1", coin="BTC", is_buy=True,
                                                 size="1", px="100", fee_usd="0")
    table = table.resolve(9, 5, {"BTC": "105"})
    assert table.account("marked").payoff.marked
    table = table.order("c1", "closer", "1").fill(order_id="c1", coin="BTC", is_buy=False,
                                                 size="1", px="110", fee_usd="0")
    assert not table.closed("marked")
    with pytest.raises(ValueError):
        table.release(["marked"], mark=1)
    table, late = table.late_realizations()
    assert late["marked"] > 0 and table.closed("marked")
    assert table.release(["marked"], mark=1).released_counts()["marked"] == 1


def test_invariant_a_cancelled_order_that_could_still_fill_holds_its_account():
    """Violation attempt: release an account whose cancelled order executed less than
    its size before the patience has passed; a fill that executed before the cancel
    took effect is still accepted, and books to its owner as any fill does."""
    table = _closed_table()
    assert not table.closed("opener", tick=4, patience=2)  # cancelled at 3
    assert not table.closed("opener")  # no clock, no patience spent
    with pytest.raises(ValueError):
        table.release(["opener"], mark=4, patience=2)
    table = table.fill(order_id="o4", coin="BTC", is_buy=True, size="1", px="116",
                       fee_usd="0")
    assert any(lot.handle == "opener" for lot in table.lots)
    # A released order id cannot be bound to another decision.
    released = _closed_table().release(["opener"], mark=7, patience=2)
    with pytest.raises(ValueError):
        released.order("o4", "closer", "1")
