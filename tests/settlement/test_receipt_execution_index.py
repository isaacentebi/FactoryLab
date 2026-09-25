"""Execution receipt cursors stay bounded without changing receipt-book semantics."""

import pytest

from factorylab.kernel.ledger import Ledger
from factorylab.settlement.receipts import ReceiptBook, execution_receipt, learning_receipt


def _execution(book, handle, event, result):
    return execution_receipt(
        book, kind="program_result", handle=handle, owner="maker",
        at_event=event, facts={"result": result})


def test_execution_cursor_and_per_handle_index_preserve_order_and_idempotence():
    book = ReceiptBook(Ledger())
    first = _execution(book, "a", 1, "first")
    cursor = book.execution_count()
    learning_receipt(
        book, handle="a", assessed="maker", scoring_rule="test",
        rule_version="v1", horizon=1, outcome=1, score=1)
    other = _execution(book, "b", 2, "other")
    second = _execution(book, "a", 3, "second")
    assert _execution(book, "a", 1, "first") == first
    assert book.execution_count() == 3
    assert [receipt.id for receipt in book.executions_since("a", cursor)] == [second]
    assert [receipt.id for receipt in book.executions_since("b", cursor)] == [other]
    assert book.ids("execution") == [first, other, second]


def test_restore_rebuilds_execution_index_without_ledger_entries():
    original = ReceiptBook(Ledger())
    _execution(original, "a", 1, "before")
    cursor = original.execution_count()
    after = _execution(original, "a", 2, "after")
    _execution(original, "b", 3, "other")
    ledger = Ledger()
    restored = ReceiptBook(ledger)
    restored.restore(list(original))
    assert ledger._recovery_items() == []
    assert restored.execution_count() == 3
    assert [receipt.id for receipt in restored.executions_since("a", cursor)] == [after]
    with pytest.raises(ValueError, match="outside"):
        restored.executions_since("a", 4)


def test_a_release_out_of_record_order_keeps_every_held_receipt_s_position():
    """Wave 17b releases decisions newest first, so a later receipt can go while an
    earlier one stays. The held one keeps its position across a checkpoint: a cursor
    taken past it never reads it again (regression: held receipts were renumbered
    after the released count, and ``executions_since`` returned A a second time)."""
    original = ReceiptBook(Ledger())
    first = _execution(original, "a", 1, "A")
    _execution(original, "b", 2, "B")
    cursor = 1  # past A, at B
    assert original.executions_since("a", cursor) == []
    original.release(["b"])
    assert original.execution_ordinals() == {first: 0}
    restored = ReceiptBook(Ledger())
    restored.restore(list(original), released_executions=original.released_executions(),
                     ordinals=original.execution_ordinals())
    assert restored.execution_count() == original.execution_count() == 2
    assert restored.executions_since("a", cursor) == []
    assert [r.id for r in restored.executions_since("a", 0)] == [first]
    later = _execution(restored, "a", 3, "C")
    assert [r.id for r in restored.executions_since("a", cursor)] == [later]
