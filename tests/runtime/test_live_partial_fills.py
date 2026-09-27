"""A live order filled in parts is booked part by part, each once (Codex and Sol on #152).

The consequence fill cursor (``FillCursor``) is the one fill path: its key is every fact
of an execution with its multiplicity, never the order id, so an order filled in pieces
is several fills. Pinned across two polls and a resume between them; and a checkpoint
that kept the deleted live-venue fill fields restores with them ignored.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from factorylab.kernel.ledger import Ledger
from factorylab.runtime.resume import decode, encode
from factorylab.settlement.consequence import FillCursor
from factorylab.world.exchange import FakeExchange, Fill

MS = 1_000_000


def _part(ts: int, *, same: bool) -> Fill:
    """One part of ``order-1``; ``same`` parts share every fact but their time."""
    size = Decimal("0.001") if same else Decimal("0.001") * (ts // MS)
    return Fill(order_id="order-1", coin="BTC", is_buy=True, size=size,
                px=Decimal("60000"), fee=Decimal("0.01"), ts_ns=ts)


class PartialVenue(FakeExchange):
    shown: list

    def fills(self, since_ns):
        return [f for f in self.shown if f.ts_ns >= since_ns]


@pytest.mark.parametrize("same", [True, False], ids=["identical-parts", "distinct-parts"])
@pytest.mark.parametrize("resume", [False, True], ids=["live", "resumed"])
def test_an_order_filled_in_three_parts_is_booked_three_times_once_each(same, resume):
    parts = [_part(10 * MS, same=same), _part(20 * MS, same=same), _part(30 * MS, same=same)]
    venue = PartialVenue()
    venue.shown = parts[:2]
    cursor = FillCursor(Ledger(), start_ns=0)
    first = cursor.poll(venue, strict=True, now_ns=25 * MS)
    if resume:
        state = decode(encode({k: getattr(cursor, k)
                               for k in ("since_ns", "seen", "through_ns")}))
        cursor = FillCursor(Ledger(), start_ns=0)
        for name, value in state.items():
            setattr(cursor, name, value)
    venue.shown = parts  # the third part, and the second again (an inclusive read)
    second = cursor.poll(venue, strict=True, now_ns=35 * MS)
    assert [ts for ts, _ in first] == [10 * MS, 20 * MS]
    assert [ts for ts, _ in second] == [30 * MS]
    assert all(payload["order_id"] == "order-1" for _ts, payload in first + second)


def test_a_checkpoint_with_the_retired_venue_fill_fields_restores_without_them():
    from factorylab.runtime.resume import restore_runtime, runtime_state
    from tests.conftest import make_runtime

    live = make_runtime(live=True)
    state = runtime_state(live)
    venue = decode(state["venue"])
    venue.update(last_fill_ns=123, seen_fills={"order-1"})  # as an older checkpoint wrote
    state["venue"] = encode(venue)
    restored = make_runtime(live=True)
    restore_runtime(restored, state)
    assert not hasattr(restored.venue, "seen_fills")
    assert not hasattr(restored.venue, "last_fill_ns")
    assert restored.venue.last_funding_ns == live.venue.last_funding_ns
