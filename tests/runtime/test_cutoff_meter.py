"""A cutoff is how long a loop took to close (R16b-2).

A decision that reaches its tick cutoff closes its role's settle loop at that cutoff;
a meter that saw only the decisions that returned measured the survivors. A decline,
censoring or cutoff credited to its router at its window's close (D5) is a learned
round and closes the router's loop too; a NOOP never does (its own due is derived
from that meter). No closure is recorded twice.
"""

from __future__ import annotations

import pytest

from factorylab.kernel.queue import PropensityRecord, SettleStatus
from tests.runtime.test_penalty_attribution import _runtime


def _open(rt, channel: str, *, at: int, cutoff: int) -> str:
    rt.ticks_consumed = at
    handle = rt.queue.open(
        actor="test-router", event_id=f"e-{channel}-{at}", channel=channel,
        deadline_ns=10**18, deadline_tick=cutoff, parent_handle=None, cost_ceiling=0,
        propensity=PropensityRecord(("seed-decider",), (1.0,), "seed-decider", 0,
                                    "test-router", "state"))
    rt.handle_to_assembly[handle] = "seed-decider"
    rt._contribution(handle, "producer")
    return handle


def test_a_cutoff_closes_its_roles_settle_loop_at_the_cutoff_once(monkeypatch):
    rt = _runtime(monkeypatch)
    handle = _open(rt, "verdict", at=10, cutoff=20)
    rt.ticks_consumed = 25  # timed out after its cutoff tick passed
    assert rt.queue.expire_due() == [handle]
    assert rt.clockwork.latencies["settle:producer"] == [10]  # open to cutoff
    assert "scored:producer" not in rt.clockwork.latencies  # no score closed it
    rt.queue.settle(handle, channel="verdict", score=0.0, status=SettleStatus.CENSORED,
                    definition_version="late", sampling_ref=None)
    assert rt.clockwork.latencies["settle:producer"] == [10]  # a late settle is no closure


def test_a_policy_cutoff_is_no_roles_sample(monkeypatch):
    rt = _runtime(monkeypatch)
    handle = _open(rt, "policy", at=10, cutoff=20)
    rt.ticks_consumed = 25
    assert rt.queue.expire_due() == [handle]
    assert not [name for name in rt.clockwork.latencies if name.startswith("settle:")]


def _drawn_at(rt, chosen: str, at: int):
    """One Tick-router decision that drew ``chosen``, opened at tick ``at``; a keyed
    router's round is frozen under its key, as the runtime's draw freezes it."""
    import random

    from factorylab.runtime.routing import _KeyedLearner

    rt.ticks_consumed = at
    state = rt.routers["Tick"][0]
    keyed = isinstance(state.learner, _KeyedLearner)
    key = f"{state.learner.id}:{at}:{chosen}"
    if keyed:
        state.learner.current_key = key
    feasible = lambda a: (a == chosen, "")  # noqa: E731 - only this arm may be woken
    sample = next(s for s in (state.router.route("Tick", feasible, random.Random(i))
                              for i in range(200)) if s.chosen == chosen)
    handle = rt.queue.open(actor=state.learner.id, event_id=f"tick-{chosen}-{at}",
                           propensity=rt._propensity(sample), channel="verdict",
                           deadline_ns=10**18, deadline_tick=at + 1_000, parent_handle=None,
                           cost_ceiling=rt.wallet.available)
    if keyed:
        rt.snapshot_keys[handle] = key
    return state, handle


def test_a_decline_credited_at_its_windows_close_closes_the_routers_loop_once(
        monkeypatch):
    from types import SimpleNamespace

    from factorylab.kernel.events import Event, EventKind
    from factorylab.runtime.shared import NOOP
    from tests.runtime.test_refusal_price import _past_the_verdict_timeout, _priced_runtime

    rt = _priced_runtime(monkeypatch)
    state, refused = _drawn_at(rt, "seed-decider", at=10)
    rt.n += 1
    rt._producer_step(Event(f"tick-{rt.n}", EventKind.TICK, rt.clock.now_ns, {"index": 0},
                            "test"), refused, SimpleNamespace(chosen="seed-decider"),
                      rt.queue.get(refused).deadline_ns)
    _noop_state, noop = _drawn_at(rt, NOOP, at=10)
    rt._contribution(noop, "producer")
    _past_the_verdict_timeout(rt)  # the refusal settles declined, its window still open
    rt._deliver_returns()
    assert refused in rt.noop_credits  # owed until its window's close (D5)
    meter = f"router:{state.kind}"
    assert meter not in rt.clockwork.latencies
    learned_at = rt.ticks_consumed
    rt._close_price_window()
    rt._deliver_returns()
    assert refused not in rt.noop_credits
    assert rt.clockwork.latencies[meter] == [learned_at - 10]  # one sample: the decline
    rt._deliver_returns()
    assert rt.clockwork.latencies[meter] == [learned_at - 10]  # never twice, NOOP never


@pytest.mark.parametrize(("cutoff", "late", "close"), [(15, 18, 20), (20, 25, 30)])
def test_a_cutoff_credited_at_the_close_closes_the_routers_loop_at_its_cutoff(
        monkeypatch, cutoff, late, close):
    """Astra and Sol on #157 (II.IV.c; R16b-1/2): a round opened at tick 10 and cut off
    at ``cutoff``, whose credit waits for its window's close, closed its router's loop at
    its cutoff. The router sample is ``cutoff - 10``, as its role's is, never ``close -
    10``: the wait for the price close is the outer loop's. A late score moves nothing
    and trains nothing again."""
    from tests.runtime.test_refusal_price import _priced_runtime

    rt = _priced_runtime(monkeypatch)
    state, handle = _drawn_at(rt, "seed-decider", at=10)
    rt._contribution(handle, "producer")
    rt.decision_ticks[handle][1] = cutoff
    rt.ticks_consumed = cutoff
    assert rt.queue.expire_due() == [handle]
    rt._deliver_returns()
    assert handle in rt.noop_credits  # owed until its window's close (D5)
    rt.ticks_consumed = late
    rt.queue.settle(handle, channel="verdict", score=0.8, status=SettleStatus.SETTLED,
                    definition_version="t", sampling_ref=None)  # late: no new closure
    rt._deliver_returns()
    rt.ticks_consumed = close
    rt._close_price_window()
    rt._deliver_returns()
    assert rt.clockwork.latencies[f"router:{state.kind}"] == [cutoff - 10]
    assert rt.clockwork.latencies["settle:producer"] == [cutoff - 10]
    learned = [i for i in rt.ledger._recovery_items()
               if i["kind"] == "router.learned" and i["handle"] == handle]
    assert [(i["path"], i["scored"]) for i in learned] == [("credit", False)]


def _forecast(rt, at: int) -> str:
    """A sealed forecast's decision, as ``open_forecast_decision`` opens one."""
    from factorylab.settlement.forecast import open_forecast_decision

    rt.ticks_consumed = at
    return open_forecast_decision(
        rt.queue, evaluator_id="evaluator:x", event_id=f"forecast-{at}", q=0.7,
        deadline_ns=rt.clock.now_ns + 5 * rt.tick_clock.interval_ns, parent_handle=None,
        now_event=0, horizon=3)


def test_a_forecast_is_no_producers_settle_sample(monkeypatch):
    """Sol on #157: a forecast decision has no seat and no emitted kind, so it fell
    through to ``producer``: a stalled forecast's timeout, and a settled forecast's
    closure, lengthened producer pricing. Its loop is the ``forecast`` meter only."""
    rt = _runtime(monkeypatch)
    stalled = _forecast(rt, at=10)
    rt.ticks_consumed = 40
    assert rt.queue.expire_due() == [stalled]
    settled = _forecast(rt, at=50)
    rt.ticks_consumed = 52
    rt.queue.settle(settled, channel="consequence", score=0.1, status=SettleStatus.SETTLED,
                    definition_version="brier-v1", sampling_ref=None)
    assert not [name for name in rt.clockwork.latencies
                if name.startswith(("settle:", "scored:"))]
