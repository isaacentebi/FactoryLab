"""A live venue's wind-down fills are read after it, and every realised dollar has an owner.

Sol on #152, for the live testnet run: a recorded venue's wind-down fills are drained at
its seal, but a live venue's arrive only on a fills read. The kill reads them once more
after the wind-down (bounded; a read that never answers is on the record, never a
hang), through the one fill path, so the closes bound to the kernel's wind-down account
close the lots the venue flattened. A decision censored at termination whose lot was
closed keeps its realised P&L: booked to its owner once, as money, never as a grade.
"""

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal

from factorylab.kernel.queue import PropensityRecord, SettleStatus
from factorylab.runtime.winddown import ROUNDS_PER_KILL
from factorylab.world.events import WorldEvent, WorldEventKind
from factorylab.world.exchange import FakeExchange, Order
from tests.conftest import make_runtime


class LiveLikeExchange(FakeExchange):
    """A venue that is not a recording: no seal drains it, so its fills reach the
    runtime only through a fills read, and that read can be made to fail."""

    unanswered = False

    def fills(self, since_ns):
        if self.unanswered:
            raise RuntimeError("fills read unanswered")
        return super().fills(since_ns)


def _world_with_an_open_long():
    """A live-like world owing a wind-down, with one decision's BTC long booked through
    the ticks' own fills read, and its outcome not yet fixed."""
    rt = make_runtime()
    rt.exchange = LiveLikeExchange()
    rt.m = replace(rt.m, kill=replace(rt.m.kill, wind_down=True))
    prop = PropensityRecord(("seed-decider",), (1.0,), "seed-decider", 0, "router:Tick", "t")
    handle = rt.queue.open(actor="router:Tick", event_id="long", propensity=prop,
                           channel="verdict", deadline_ns=rt.clock.now_ns + 10**18,
                           parent_handle=None, cost_ceiling=0)
    rt.handle_to_assembly[handle] = "seed-decider"
    rt.consequences.start(handle, rt.n)
    placed = rt.exchange.place(Order("BTC", True, Decimal("0.001"), client_id="open-1"))
    rt.consequences.order_result(handle, {"status": placed.status,
                                          "order_id": placed.order_id,
                                          "filled_size": str(placed.filled_size)},
                                 {"size": "0.001"}, rt.n)
    fills = rt.consequence_fills.poll(rt.exchange, strict=True, now_ns=rt.clock.now_ns)
    rt._settle_exchange_effects([WorldEvent(WorldEventKind.FILL, max(rt.clock.now_ns, ts),
                                            rt.exchange.name, payload)
                                 for ts, payload in fills], observe_positions=False)
    rt.consequences.finish(handle, 0)
    rt.exchange.drain_events()
    assert [lot.handle for lot in rt.consequences.table.lots] == [handle]
    assert rt.consequences.payoff(handle) is None
    return rt, handle


def _rows(rt, kind):
    return [i for i in rt.ledger._recovery_items() if i["kind"] == kind]


def test_a_live_venues_wind_down_fills_are_read_after_it_and_close_the_lots():
    rt, _handle = _world_with_an_open_long()
    rt.clock.now_ns += 5_000_000_000
    report = rt.kill("explicit_kill:budget")
    assert report["exposure_state"] == "flat"
    assert not rt.consequences.table.lots  # the lot the venue flattened is closed
    kinds = [i["kind"] for i in rt.ledger._recovery_items()]
    wound = kinds.index("kill.wind_down")
    assert "consequence.wind_down_order" in kinds[wound:]
    assert "consequence.fill" in kinds[wound:]  # read only after the wind-down
    assert rt.consequence_fills.through_ns == rt.clock.now_ns  # the watermark advanced
    assert not _rows(rt, "wind_down.fills_unread")


def test_a_wind_down_fills_read_that_never_answers_is_on_the_record():
    rt, handle = _world_with_an_open_long()
    rt.exchange.unanswered = True
    report = rt.kill("explicit_kill:budget")
    (unread,) = _rows(rt, "wind_down.fills_unread")
    assert unread["attempts"] == ROUNDS_PER_KILL
    assert report["production_state"] == "killed" and rt.termination.final
    # Nothing was read, so nothing is attributed: the lot is unmoved, no money booked.
    assert [lot.handle for lot in rt.consequences.table.lots] == [handle]
    assert not _rows(rt, "consequence.realized_at_termination")


def test_a_censored_decisions_closed_lot_books_its_pnl_once_and_is_never_graded():
    rt, handle = _world_with_an_open_long()
    claims = rt.budget.venue_claims().get("seed-decider", 0)
    rt.kill("explicit_kill:budget")
    (booked,) = _rows(rt, "consequence.realized_at_termination")
    account = rt.consequences.table.account(handle)
    realized = account.realized_micro.numerator // account.realized_micro.denominator
    assert booked["handle"] == handle and booked["micro"] == realized != 0
    assert rt.budget.venue_claims().get("seed-decider", 0) - claims == realized
    kinds = [i["kind"] for i in rt.ledger._recovery_items()]
    assert kinds.index("consequence.realized_at_termination") < kinds.index(
        "decision.censored")
    (censored,) = rt.queue.history(handle)  # censored for grading, never scored
    assert censored.status is SettleStatus.CENSORED and censored.score == 0.0
    assert rt.consequences.payoff(handle) is None
    rt._settle_realized_at_termination()  # once: nothing more to book
    assert len(_rows(rt, "consequence.realized_at_termination")) == 1


def test_wind_down_fills_read_but_not_booked_are_on_the_record():
    """Sol on #152: the read succeeded, so its fills are consumed; booking them raised.
    Their facts and the failure are ledgered, and the kill still completes."""
    rt, _handle = _world_with_an_open_long()
    booked = rt._settle_exchange_effects
    calls = []

    def failing(events, **kwargs):
        calls.append(len(events))
        if events:
            raise ValueError("booking failed")
        return booked(events, **kwargs)

    rt._settle_exchange_effects = failing
    report = rt.kill("explicit_kill:budget")
    (row,) = _rows(rt, "wind_down.fills_booking_failed")
    assert row["error"] == "ValueError" and row["message"] == "booking failed"
    (fill,) = row["fills"]
    assert fill["coin"] == "BTC" and fill["is_buy"] is False and fill["size"] == "0.001"
    assert {"order_id", "px", "fee_usd", "fact_ns"} <= set(fill)
    assert report["production_state"] == "killed" and rt.termination.final
