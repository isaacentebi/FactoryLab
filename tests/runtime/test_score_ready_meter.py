"""An inner loop's meter closes when the world fixed the outcome (R16b-1, II.IV.c).

Under D5 a decision whose score is in waits in ``deferred_settlements`` for its origin
window's close. Its role's settle meter closed at that wait, so the price loop measured
its own period as its inner loop's and ran away (the gauntlet's runaway). The meter now
closes at score ready (``ready_tick``); the frozen share still lands at the close; and
a policy decision (an outer loop on its own schedule) is never a role's sample.
"""

from __future__ import annotations

import pytest

from factorylab.kernel.queue import PropensityRecord, SettleStatus
from factorylab.runtime.resume import decode, encode
from tests.runtime.test_penalty_attribution import _producer, _rows, _runtime, _settle


def _deferred_at(rt, opened: int, ready: int):
    rt.ticks_consumed = opened
    handle = _producer(rt, "seed-decider", "hold")
    rt.ticks_consumed = ready
    _settle(rt, handle)
    assert rt.queue.get(handle).status is SettleStatus.PENDING  # waits for the close
    return handle


def test_the_settle_meter_closes_at_score_ready_not_at_the_price_close(monkeypatch):
    rt = _runtime(monkeypatch)
    handle = _deferred_at(rt, opened=10, ready=13)
    assert rt.deferred_settlements[handle]["ready_tick"] == 13
    rt.ticks_consumed = 20  # the window closes seven ticks after the score was fixed
    rt._close_price_window()
    assert rt.queue.get(handle).status is SettleStatus.SETTLED
    assert rt.clockwork.latencies["settle:producer"] == [3]  # never 10
    assert rt.clockwork.latencies["scored:producer"] == [3]
    (penalty,) = _rows(rt, "price.penalty", handle=handle)  # D5 untouched: at the close
    assert penalty["penalty"] > 0


def test_a_checkpointed_deferral_keeps_its_ready_tick_and_an_older_one_the_close(
        monkeypatch):
    rt = _runtime(monkeypatch)
    kept = _deferred_at(rt, opened=10, ready=13)
    older = _deferred_at(rt, opened=10, ready=14)
    rt.deferred_settlements = decode(encode(rt.deferred_settlements))  # a checkpoint
    assert rt.deferred_settlements[kept]["ready_tick"] == 13
    del rt.deferred_settlements[older]["ready_tick"]  # written before R16b-1
    rt.ticks_consumed = 20
    rt._close_price_window()
    assert sorted(rt.clockwork.latencies["settle:producer"]) == [3, 10]


def test_a_policy_decision_is_never_a_role_meters_sample(monkeypatch):
    rt = _runtime(monkeypatch)
    rt.ticks_consumed = 10
    ballot = rt.queue.open(
        actor="committee", event_id="ballot", channel="policy", deadline_ns=10**18,
        parent_handle=None, cost_ceiling=0,
        propensity=PropensityRecord(("seed-decider",), (1.0,), "seed-decider", 0,
                                    "committee", "state"))
    rt.handle_to_assembly[ballot] = "seed-decider"  # filed under the seat's role
    rt.ticks_consumed = 70  # graded at a window far later, on its own schedule
    rt._settle_policy(ballot, 0.7, SettleStatus.SETTLED)
    assert rt.queue.get(ballot).status is SettleStatus.SETTLED
    assert not [name for name in rt.clockwork.latencies
                if name.startswith(("settle:", "scored:"))]


@pytest.mark.gate
def test_the_price_loop_does_not_run_away_when_every_producer_decision_defers():
    """R16b-1: every producer decision's settlement waits for its window's close (the
    holds card, priced). Its meter closes at score ready, so the price period stays
    ``min_ratio × inner`` with a steady inner. Recording at the close instead (the
    runaway) grew it 3, 6, 9, 12, 15, 45 ticks and closed only ten windows."""
    import math
    from dataclasses import replace

    from factorylab.runtime.loop import Runtime
    from factorylab.runtime.worlds import load_manifest
    from tests.runtime.test_penalty_attribution import HOLDS

    seed = load_manifest("scripted")
    manifest = replace(seed, charter=replace(seed.charter, cards=(HOLDS,)),
                       charter_prices=((HOLDS.id, 0.8),))
    rt = Runtime(manifest, events=150, seed=1, initial_balance_micro=None,
                 ledger_path=None, router_gamma=0.1)
    rt.run()
    items = rt.ledger._recovery_items()
    assert sum(i["kind"] == "price.deferred" for i in items) > 100  # every one waits
    loops = [i for i in items if i["kind"] == "clock.loop" and i["loop"] == "price"]
    assert len(loops) >= 30
    timing = rt.m.timing
    for row in loops:
        bound = math.ceil(timing.min_ratio * (1 + timing.jitter_fraction) * row["inner_ticks"])
        assert row["period_ticks"] <= bound, row
    steady = loops[1]["period_ticks"]
    assert loops[-1]["period_ticks"] <= steady * (1 + timing.jitter_fraction)
    assert max(row["inner_ticks"] for row in loops) == loops[0]["inner_ticks"]
