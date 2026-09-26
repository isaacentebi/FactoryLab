"""Evaluation horizons are measured in world ticks, not internal events (defect 1).

``verdict_timeout_events`` and ``consequence_backstop_events`` were compared with the
runtime's internal event counter, which advances about twenty times per world tick
(every fill, verdict, meta verdict and watcher firing is an event). A 20-"event"
verdict timeout was therefore about one tick: shorter than the cascade window that
releases verdicts to the metas, so almost every evaluator decision was censored
before a meta could read it. Both horizons now count world ticks consumed; the
manifest keys keep their names and their numbers.
"""

from factorylab.kernel.queue import PropensityRecord, SettleStatus
from factorylab.runtime.feedback import PendingJudgement
from factorylab.runtime.shared import CH_CONFORMITY
from tests.conftest import make_runtime


def _judgement(rt):
    prop = PropensityRecord(("eval-a",), (1.0,), "eval-a", 0, "router:ProducerReturn", "t")
    handle = rt.queue.open(actor="router:ProducerReturn", event_id="judge", propensity=prop,
                           channel=CH_CONFORMITY, deadline_ns=10**18, parent_handle=None,
                           cost_ceiling=0)
    rt.handle_to_assembly[handle] = "eval-a"
    rt.pending[handle] = PendingJudgement(handle, CH_CONFORMITY, rt.n,
                                          opened_at_tick=rt.ticks_consumed)
    return handle


def test_a_verdict_awaiting_its_meta_outlives_a_burst_of_internal_events():
    rt = make_runtime()
    timeout = rt.ev.verdict_timeout_events
    handle = _judgement(rt)
    rt.n += 20 * timeout  # a busy tick: many internal events, no world tick consumed
    rt._censor_stale_judgements()
    assert rt.queue.get(handle).status is SettleStatus.PENDING
    rt.ticks_consumed += timeout  # at the horizon, not past it
    rt._censor_stale_judgements()
    assert rt.queue.get(handle).status is SettleStatus.PENDING
    rt.ticks_consumed += 1
    rt._censor_stale_judgements()
    assert rt.queue.get(handle).status is SettleStatus.CENSORED


def test_the_consequence_horizon_marks_an_open_position_on_the_venue_clock():
    """Wave 16, D2: an open position's outcome is fixed at H = world_repricing /
    min_ratio of venue time after the return opened: not after internal events, and not
    after ticks consumed, so a slow factory does not change the fact it is graded on."""
    rt = make_runtime()
    rt._read_fee_schedule()
    prop = PropensityRecord(("seed-decider",), (1.0,), "seed-decider", 0, "router:Tick", "t")
    handle = rt.queue.open(actor="router:Tick", event_id="trade", propensity=prop,
                           channel="verdict", deadline_ns=10**18, parent_handle=None,
                           cost_ceiling=0)
    rt.consequences.start(handle, rt.n)
    rt.consequences.order_result(handle, {"status": "filled", "order_id": "o1",
                                          "filled_size": "0.001"}, {"size": "0.001"}, rt.n)
    rt.consequences.observe("Fill", {"order_id": "o1", "coin": "BTC", "is_buy": True,
                                     "size": "0.001", "px": "60000", "fee_usd": "0"}, rt.n)
    rt.consequences.observe("MarketMid", {"coin": "BTC", "mid": "60000"}, rt.n)
    rt.consequences.finish(handle, 0)
    backstop = rt.ev.consequence_backstop_events
    rt.n += 20 * backstop
    rt.ticks_consumed += 20 * backstop
    assert all(p.handle != handle for p in rt.consequences.resolve(rt.n))
    rt.clock.now_ns += rt._horizon_ns() - 1
    assert all(p.handle != handle for p in rt.consequences.resolve(rt.n))
    rt.clock.now_ns += 1
    # The batch's Tick at H comes before its MarketMid at H: the cached 60000 is an
    # earlier instant's price, so nothing is fixed until a mid at or after H arrives.
    assert all(p.handle != handle for p in rt.consequences.resolve(rt.n))
    rt.consequences.observe("MarketMid", {"coin": "BTC", "mid": "60000",
                                          "ts_ns": rt.clock.now_ns}, rt.n)
    (payoff,) = [p for p in rt.consequences.resolve(rt.n) if p.handle == handle]
    assert payoff.marked
    # Marked to liquidation value: 60 USD of notional at the venue's 3.5 bp taker rate.
    assert payoff.exit_fee_micro == 21_000 and payoff.net_micro == -21_000 and payoff.y == 0

