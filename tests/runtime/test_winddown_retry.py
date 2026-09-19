"""A reconciled refusal owes the target its next attempt, and against the size it asked for.

Two ways the wind-down executor can leave residual exposure behind after a kill
that reported itself done:

1. A crash between ``winddown.op`` and ``winddown.op_result`` (or a dropped
   acknowledgement) is reconciled by reading the venue. When that read comes back
   a definitive refusal or a partial fill, it is exactly as retryable as the same
   answer observed directly, and must feed the same retry path.
2. Completeness is judged against the size the attempt *asked for*, not against
   whatever residual the next round's account read happens to show. Closing 1 BTC
   that fills 0.6 leaves 0.4; 0.6 is not 1, so the target is still owed an attempt.

No network here: the venue is a double and the diary is a list.
"""

from __future__ import annotations

import json
from decimal import Decimal

from factorylab.runtime.winddown import (
    OP,
    OP_RESULT,
    WindDownExecutor,
    operation_id,
    sequence_of,
)
from factorylab.world.exchange import AccountState, OrderResult, Position

NONCE = "b" * 32


class Diary:
    """A ledger double that keeps what it took, as plain JSON."""

    def __init__(self, rows: list | None = None) -> None:
        self.rows: list[dict] = list(rows or [])

    def append(self, row: dict) -> int:
        self.rows.append(json.loads(json.dumps(row, default=str)))
        return len(self.rows)

    def items(self):
        return list(self.rows)

    def kinds(self, kind: str) -> list[dict]:
        return [row for row in self.rows if row.get("kind") == kind]


class Venue:
    """One BTC position that shrinks by whatever each close actually fills."""

    name = "double"

    def __init__(self, *, size=Decimal("1"), results=(), lookup_result=None) -> None:
        self.size = size
        self.results = list(results)
        self.lookup_result = lookup_result
        self.calls: list[tuple] = []

    def open_orders(self):
        return []

    def account(self):
        positions = (Position("BTC", self.size, Decimal("100")),) if self.size else ()
        return AccountState(equity_usd=Decimal("100"), cash_usd=Decimal("100"),
                            positions=positions, margin_used_usd=Decimal("0"))

    def mids(self):
        return {"BTC": Decimal("100")}

    def instruments(self):
        return {"perp": [{"coin": "BTC", "min_order_value_usd": "10"}], "spot": []}

    def close(self, coin, size=None, *, client_id=None, market="perp"):
        self.calls.append(("close", coin, size, client_id, market))
        result = (self.results.pop(0) if self.results
                  else OrderResult("o", "filled", self.size, None))
        self.size -= result.filled_size
        return result

    def lookup(self, client_id, *, order_id=None):
        self.calls.append(("lookup", client_id))
        return self.lookup_result or OrderResult(None, "uncertain", Decimal("0"), None)

    def place(self, order):  # pragma: no cover - reached only by a bug
        raise AssertionError("the wind-down executor may never open a position")

    def closes(self) -> list[tuple]:
        return [call for call in self.calls if call[0] == "close"]


def _attempt(n: int) -> str:
    return operation_id(NONCE, "BTC", "perp", "sell", sequence_of(0, n))


def test_a_reconciled_refusal_is_retried_inside_the_same_kill():
    """A crash before the result was written, and the venue says it rejected the close."""
    diary = Diary([{"kind": OP, "op": "close", "op_id": _attempt(0), "coin": "BTC",
                    "market": "perp", "side": "sell", "sequence": "0", "attempt": 0,
                    "size": "1"}])
    venue = Venue(lookup_result=OrderResult(None, "rejected", Decimal("0"), None))
    report = WindDownExecutor(venue, diary, launch_nonce=NONCE).run()

    dispositions = [row["disposition"] for row in diary.kinds(OP_RESULT)]
    assert dispositions[0] == "reconciled"
    # The refusal the reconciliation read is a refusal like any other: the target's
    # next attempt goes out in this kill, under its own identity.
    assert [call[3] for call in venue.closes()] == [_attempt(1)]
    assert report["residual"]["positions"] == []
