"""A live order filled in parts is booked part by part, each once (Codex and Sol on #152).

The consequence fill cursor (``FillCursor``) is the one fill path: its key is every fact
of an execution with its multiplicity, never the order id, so an order filled in pieces
is several fills. Pinned across two polls and a resume between them; and a checkpoint
that kept the deleted live-venue fill fields restores with them ignored.
"""

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal
from types import SimpleNamespace

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


def test_live_first_seen_delay_holds_unknown_then_grows_without_losing_older_fills():
    from factorylab.runtime.resume import restore_runtime, runtime_state
    from factorylab.runtime.venue import VenueMixin
    from tests.conftest import make_runtime

    rt = make_runtime(live=True)
    cursor = rt.consequence_fills
    start = cursor.since_ns
    venue = PartialVenue()
    venue.shown = []
    assert cursor.poll(venue, now_ns=start + 100) == []
    assert rt._stream_through(("fills",)) == float("-inf")
    newer = replace(_part(start + 80, same=True), observed_at_ns=start + 110)
    venue.shown = [newer]
    assert len(cursor.poll(venue, now_ns=start + 100)) == 1
    assert cursor.propagation_bound_ns == 30  # response, not request time
    assert rt._stream_through(("fills",)) == start + 69

    restored = make_runtime(live=True)
    restore_runtime(restored, runtime_state(rt))
    cursor = restored.consequence_fills
    older = replace(_part(start + 20, same=True), observed_at_ns=start + 200)
    venue.shown = [older, newer, older]  # identical executions retain multiplicity
    assert [ts for ts, _ in cursor.poll(venue, now_ns=start + 190,
                                      tick_ns=100)] == [start + 20] * 2
    assert cursor.since_ns == start
    assert cursor.propagation_bound_ns == 180
    assert restored._stream_through(("fills",)) == start + 9  # can retreat
    # A later response timestamp is not part of an execution's identity or first-seen.
    venue.shown = [replace(f, observed_at_ns=start + 300) for f in venue.shown]
    assert cursor.poll(venue, now_ns=start + 290) == []
    assert cursor.propagation_bound_ns == 180
    assert restored._stream_through(("fills",)) == start + 109
    row = [r for r in restored.ledger._recovery_items()
           if r["kind"] == "consequence.fill_propagation"][-1]
    assert row["bound_ns"] == 180 and row["observations"] == []
    # Fake/tape advancement remains exact regardless of a cursor's measured state.
    exact = SimpleNamespace(venue=None, advance_through_ns=start + 300)
    assert VenueMixin._stream_through(exact, ("fills",)) == start + 300


def test_live_missing_observation_never_becomes_a_completeness_claim():
    cursor = FillCursor(Ledger(), start_ns=0, measured=True)
    venue = PartialVenue()
    venue.shown = [replace(_part(10, same=True), observed_at_ns=20)]
    cursor.poll(venue, now_ns=20)
    venue.shown += [_part(15, same=True)]
    cursor.poll(venue, now_ns=30)
    assert cursor.through_ns is None
    venue.shown += [replace(_part(40, same=True), observed_at_ns=50)]
    cursor.poll(venue, now_ns=50)
    assert cursor.through_ns is None


@pytest.mark.parametrize("gap", [False, True], ids=["complete", "history-gap"])
def test_live_fill_window_and_checkpoint_stay_bounded_after_5000_polls(gap):
    import json

    from factorylab.runtime.resume import restore_runtime, runtime_state
    from tests.conftest import make_runtime

    rt = make_runtime(live=True)
    cursor = rt.consequence_fills
    start = cursor.launch_ns
    venue = PartialVenue()
    retained, sizes = [], []
    booked = 0
    for i in range(5000):
        at = start + (i + 1) * 10
        venue.shown = [replace(_part(at - age, same=True), observed_at_ns=at + 20,
                               history_complete=not (gap and i == 100))
                       for age in (0, 10, 20, 30, 40, 50, 60) if at - age >= start + 10]
        booked += len(cursor.poll(venue, now_ns=at + 20, tick_ns=10))
        assert cursor.poll(venue, now_ns=at + 20, tick_ns=10) == []
        assert all(key[0] >= cursor.since_ns for key in cursor.seen)
        retained.append(len(cursor.seen))
        if i in (999, 2499, 4999):
            state = runtime_state(rt)
            sizes.append(len(json.dumps(encode(decode(state["components"])["consequence_fills"]))))
            restored = make_runtime(live=True)
            restore_runtime(restored, state)
            rt, cursor = restored, restored.consequence_fills
    assert booked == 5000
    assert max(retained[100:]) <= 5
    assert max(sizes) - min(sizes) <= 32
    assert (cursor.through_ns is None) == gap


def test_growth_beyond_discarded_fill_overlap_is_ledgered_unknown():
    cursor = FillCursor(Ledger(), start_ns=0, measured=True)
    venue = PartialVenue()
    venue.shown = [replace(_part(100, same=True), observed_at_ns=110)]
    cursor.poll(venue, now_ns=110, tick_ns=10)
    cursor.poll(venue, now_ns=200, tick_ns=10)
    venue.shown += [replace(_part(180, same=True), observed_at_ns=300)]
    assert len(cursor.poll(venue, now_ns=300, tick_ns=10)) == 1
    assert cursor.through_ns is None
    row = [r for r in cursor.ledger._recovery_items()
           if r["kind"] == "consequence.fill_propagation"][-1]
    assert row["lost_overlap"] and not row["observation_complete"]


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
