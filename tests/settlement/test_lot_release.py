"""Closed, fully settled accounts are released into counts (wave 17b).

Essay II.IV.c: a consequence is "consumed ... and then discarded"; what persists is
aggregates. An account is released only once every order it placed is confirmed
terminal by the venue's own order status; a released account's counts survive in
the table, and a fill the venue still reports on one of its orders (a venue error)
is its owner's money, booked late and never graded.
"""

from fractions import Fraction

import pytest

from factorylab.kernel.ledger import Ledger
from factorylab.settlement.consequence import ReturnConsequences
from factorylab.settlement.lots import RELEASED_ORDER, LotTable


def _closed_table():
    """Four returns: one that traded and closed its lot, a closer, one holding, one void.

    ``o4`` was cancelled with size left: a fill that executed before the cancel took
    effect may still arrive, so until the venue confirms it terminal it pins its owner.
    """
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
    table = table.order("o4", "opener", "2").cancel("o4")
    return table.resolve(9, 5, {"BTC": "115"})


def _confirmed(table):
    for order_id, filled in (("o1", "1"), ("o2", "1"), ("o3", "1"), ("o4", "0")):
        table = table.confirm(order_id, filled)
    return table


def test_an_account_is_closed_only_once_the_venue_confirms_every_order_terminal():
    table = _closed_table()
    assert table.account("opener").payoff is not None
    # Filled as observed, cancel acknowledged: neither is the venue's word.
    assert not table.closed("opener") and not table.closed("closer")
    assert table.closed("hold")  # placed no order
    confirmed = _confirmed(table)
    assert confirmed.closed("opener") and confirmed.closed("closer")
    assert not confirmed.closed("holder")  # its lot is open: a late realization is owed
    # A venue that says more filled than was accounted keeps the account pinned until
    # those fills are observed.
    ahead = table.confirm("o1", "1").confirm("o2", "1").confirm("o4", "1")
    assert not ahead.closed("opener")


def test_a_venue_confirmed_account_is_released_and_every_count_is_kept():
    ledger = Ledger()
    book = ReturnConsequences(ledger, 5)
    book.table = _closed_table()
    for order_id, filled in (("o1", "1"), ("o2", "1"), ("o4", "0")):
        book.confirm_terminal(order_id, "filled" if filled == "1" else "cancelled", filled, 8)
    terminal = [i for i in ledger._recovery_items() if i["kind"] == "consequence.terminal"]
    assert [(i["order_id"], i["status"], i["filled"]) for i in terminal] == [
        ("o1", "filled", "1"), ("o2", "filled", "1"), ("o4", "cancelled", "0")]
    before = book.counts()
    book.release(["opener", "closer", "hold"], mark=7, authors={"opener": "seat-o"})
    assert [r.handle for r in book.table.returns] == ["holder"]
    assert book.counts() == before
    assert book.table.released_counts()["accounts"] == 3
    assert book.table.order_owner("o1") == "opener" and book.table.order_owner("o3") == "holder"
    assert book.table.released_author("opener") == "seat-o"
    with pytest.raises(KeyError):
        book.table.account("opener")
    assert not book.account_open("opener")


@pytest.mark.parametrize("handle", ["holder", "unknown", "opener"])
def test_invariant_an_open_or_unconfirmed_account_is_never_released(handle):
    """Violation attempts: release an account holding a lot, one never admitted, and one
    whose cancelled order the venue has not confirmed terminal."""
    table = _closed_table() if handle == "opener" else _confirmed(_closed_table())
    with pytest.raises(ValueError):
        table.release([handle, "closer"], mark=1)
    book = ReturnConsequences(Ledger(), 5)
    book.table = table
    if handle != "unknown":
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
    assert not table.confirm("o9", "0").closed("waiting")  # a live order, whatever is said
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


def test_a_fill_after_the_cancel_is_awaited_however_long_it_takes_and_booked():
    """The cancel was acknowledged, and any horizon has passed: until the venue reads
    the order back terminal, the account stays, and a fill that executed before the
    cancel took effect is its owner's, as any fill is."""
    ledger = Ledger()
    book = ReturnConsequences(ledger, 5)
    book.table = _closed_table().confirm("o1", "1").confirm("o2", "1")
    assert not book.releasable("opener")  # o4: cancel acknowledged, never read back
    with pytest.raises(ValueError):
        book.release(["opener"], mark=10**6)
    before = book.table.account("opener").realized_micro
    book.observe("Fill", {"order_id": "o4", "coin": "BTC", "is_buy": False, "size": "1",
                          "px": "130", "fee_usd": "0"}, 10**6)
    assert book.table.account("opener").realized_micro > before
    assert book.settle_late(10**6)["opener"] > 0  # booked to its owner, late
    book.confirm_terminal("o4", "cancelled", "1", 10**6)
    assert book.releasable("opener")


def test_a_forced_fill_on_a_released_order_books_its_money_and_grades_nothing():
    """Venue error: an order confirmed terminal and released fills anyway. The fill
    moves the lots as the venue's position moved, its owner's money is booked late,
    and no fixed outcome and no released count changes."""
    ledger = Ledger()
    book = ReturnConsequences(ledger, 5)
    book.table = _confirmed(_closed_table())
    book.release(["opener", "closer"], mark=7, authors={"opener": "seat-o"})
    counts = book.table.released_counts()
    holder = book.table.account("holder")
    # A sell on the released o4 closes the holder's long lot: its closer share is the
    # released opener's, the opener share is the holder's, as for any close.
    book.observe("Fill", {"order_id": "o4", "coin": "BTC", "is_buy": False, "size": "1",
                          "px": "130", "fee_usd": "0"}, 10)
    released = [i for i in ledger._recovery_items() if i["kind"] == "consequence.released_fill"]
    assert released[-1]["handle"] == "opener" and released[-1]["reason"] == RELEASED_ORDER
    assert not any(lot.handle == "holder" for lot in book.table.lots)
    assert book.table.account("holder").realized_micro > holder.realized_micro
    assert book.table.account("holder").payoff == holder.payoff  # fixed, never re-graded
    late = book.settle_late(10)
    closer_share = Fraction(130 - 110) * 130 / (110 + 130) * 1_000_000
    assert late["opener"] == int(closer_share)
    assert book.table.released_counts() == counts
    items = [i for i in ledger._recovery_items() if i["kind"] == "consequence.late"]
    assert {"handle": "opener", "micro": late["opener"]}.items() <= items[-1].items()
    assert book.settle_late(11) == {}  # booked once
    # A buy on it opens a lot the released owner holds: its later close is booked too.
    book.observe("Fill", {"order_id": "o4", "coin": "BTC", "is_buy": True, "size": "1",
                          "px": "100", "fee_usd": "0"}, 12)
    assert any(lot.handle == "opener" for lot in book.table.lots)
    book.table = book.table.order("o5", "holder", "1")
    book.observe("Fill", {"order_id": "o5", "coin": "BTC", "is_buy": False, "size": "1",
                          "px": "110", "fee_usd": "0"}, 13)
    assert book.settle_late(13)["opener"] > 0
    assert book.table.released_late == ()
    # A released order id cannot be bound to another decision.
    with pytest.raises(ValueError):
        book.table.order("o4", "holder", "1")


def test_the_released_counts_survive_a_checkpoint_codec():
    from factorylab.runtime.resume import decode, encode

    table = _confirmed(_closed_table()).release(["opener", "closer", "hold"], mark=7)
    restored = decode(encode(table))
    assert restored == table
    assert restored.released_counts() == table.released_counts()
    assert restored.order_owner("o4") == "opener"
    # A table checkpointed before release restores with nothing released.
    older = encode(_closed_table())
    for name in ("released", "released_orders", "released_late"):
        older["fields"].pop(name)
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
    table = table.confirm("m1", "1")
    assert not table.closed("marked")
    with pytest.raises(ValueError):
        table.release(["marked"], mark=1)
    table, late = table.late_realizations()
    assert late["marked"] > 0 and table.closed("marked")
    assert table.release(["marked"], mark=1).released_counts()["marked"] == 1
