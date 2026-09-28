"""Chapter II §III.b, §IV.c: recursive rewards retain their world's funding patience."""

from dataclasses import replace

import pytest

from factorylab.kernel.queue import SettleStatus
from factorylab.runtime.resume import restore_runtime, runtime_state
from factorylab.runtime.shared import CH_COUNTER, CH_FAST
from factorylab.world.exchange import NS_PER_HOUR
from tests.runtime.test_consequence_horizon import S, _named_hold, _world
from tests.runtime.test_loop import _consequence_decision
from tests.runtime.test_reward_chain import _meta, _rows


def _chain():
    rt = _world(1)
    rt.exchange.target.settled_funding = True
    rt.exchange.target.funding_interval_ns = 60 * S
    start = 3 * NS_PER_HOUR
    producer, judge = _named_hold(rt, start)
    meta = _meta(rt, judge)
    # A further tier uses the same pending contract, independent of the seed roster.
    upper = _consequence_decision(rt, "meta-a", CH_FAST)
    rt.pending[upper] = replace(rt.pending[meta], handle=upper, about=meta, tier=3)
    counter = _consequence_decision(rt, "eval-a", CH_COUNTER)
    rt.pending_counters[counter] = {
        "about": producer, "q": 0.2, "judge_handle": judge, "judge_q": 0.8,
        "evaluator_id": "eval-a", "tick": rt.ticks_consumed, "ns": start,
    }
    for handle in (judge, meta, upper):
        rt.pending[handle].grade_closed = True
    rt._observe_mid("BTC", start + 90 * S, "90")
    return rt, start, producer, judge, meta, upper, counter


@pytest.mark.parametrize("resume", [False, True])
@pytest.mark.parametrize("publication", [None, -1, 0, 1])
def test_recursive_verdicts_and_counter_keep_underlying_funding_deadline(publication, resume):
    rt, start, producer, judge, meta, upper, counter = _chain()
    if resume:
        state = runtime_state(rt)
        restored = _world(1)
        restored.exchange.target.settled_funding = True
        restored.exchange.target.funding_interval_ns = 60 * S
        restore_runtime(restored, state)
        rt = restored
    deadline = start + rt.m.timing.world_repricing_ns
    assert start + rt._patience_ns() < deadline - 1
    rt.clock.now_ns = deadline - 1
    rt.tick_through_ns = deadline - 1
    rt._settle_evaluations()
    for handle in (judge, meta, upper):
        assert handle in rt.pending, "dependent verdict expired before funding deadline"
        assert not rt.pending[handle].consequence_closed
    assert counter in rt.pending_counters
    assert not _rows(rt, "counter.settled")

    if publication is not None:
        rt.clock.now_ns = deadline + publication
        rt.tick_through_ns = rt.clock.now_ns
        rt._observe_funding("BTC", start + 60 * S, "0.001", "100", settled=True)
    else:
        rt.clock.now_ns = deadline
        rt.tick_through_ns = deadline
    rt._settle_evaluations()
    # Repeated passes cannot deliver a second reward or revive an expired outcome.
    rt.clock.now_ns = deadline + S
    rt.tick_through_ns = rt.clock.now_ns
    rt._settle_evaluations()
    measured = publication == -1
    for handle in (judge, meta, upper, counter):
        (settlement,) = rt.queue.history(handle)
        assert settlement.status is (SettleStatus.SETTLED if measured else SettleStatus.CENSORED)
    assert len(_rows(rt, "verdict.consequence", handle=judge)) == int(measured)
    for handle in (meta, upper):
        assert len(_rows(rt, "meta.consequence", handle=handle)) == int(measured)
    (counter_row,) = _rows(rt, "counter.settled", handle=counter)
    assert (counter_row["score"] is not None) == measured
    assert rt.world_outcomes[producer]["state"] == ("measured" if measured else "none")


@pytest.mark.parametrize("closed", ["record", "score", "outcome", "missing", "cycle"])
def test_closed_missing_or_cyclic_dependencies_do_not_extend_patience(closed):
    rt, start, producer, judge, meta, upper, counter = _chain()
    ordinary = start + rt._patience_ns()
    if closed == "record":
        rt.pending[judge].consequence_closed = True
    elif closed == "score":
        rt.consequence_scores[judge] = (None, start)
    elif closed == "outcome":
        rt.world_outcomes[producer] = {"state": "none", "ns": start}
    elif closed == "missing":
        del rt.pending[judge]
    else:
        rt.pending[judge].about = meta
    assert rt._consequence_deadline_ns(meta, ordinary) == ordinary
    assert rt._consequence_deadline_ns("missing", ordinary) == ordinary
