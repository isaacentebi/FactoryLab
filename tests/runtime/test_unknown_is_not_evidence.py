"""An outcome nobody observed is not evidence about the seat (defect 5).

The venue never answered whether an order filled, so the return's consequence is
censored: unknown, not zero. It still ended one of the seat's novelty trials, and
five such unknowns qualified the seat to sit on a committee as a seat with five
independently settled decisions. Only observed outcomes count toward either.
"""

from factorylab.kernel.queue import PropensityRecord
from tests.conftest import make_runtime

SEAT = "seed-decider"


def _censored_return(rt, i):
    prop = PropensityRecord((SEAT,), (1.0,), SEAT, 0, "router:Tick", "probe")
    handle = rt.queue.open(actor="router:Tick", event_id=f"probe-{i}", propensity=prop,
                           channel="verdict", deadline_ns=10**18, parent_handle=None,
                           cost_ceiling=0)
    rt.handle_to_assembly[handle] = SEAT
    rt.consequences.start(handle, i + 1)
    rt.consequences.finish(handle, 0)
    rt.consequences.order_intent(f"order-{i}", handle, "BTC")
    rt.consequences.release_unresolved(f"order-{i}", i + 2)
    return handle


def test_a_censored_payoff_ends_no_novelty_trial():
    rt = make_runtime()
    _censored_return(rt, 0)
    rt._settle_due_forecasts()
    payoffs = [r.payoff for r in rt.consequences.table.returns if r.payoff is not None]
    assert payoffs and all(p.censored for p in payoffs)
    assert rt.stats.consequences_by_assembly.get(SEAT, 0) == 0


def test_censored_decisions_do_not_qualify_a_committee_seat():
    rt = make_runtime()
    for i in range(rt.m.committee.min_settled):
        _censored_return(rt, i)
    rt._settle_due_forecasts()
    assert SEAT not in rt._committee_eligible()


def test_a_meta_whose_graded_decision_has_no_world_outcome_ends_no_novelty_trial():
    """A meta's second signal is the consequence of the decision it graded (ruling R1).
    With none, the meta settles censored: an answer with no fact in it spends no trial."""
    rt = make_runtime()
    prop = PropensityRecord(("meta-a",), (1.0,), "meta-a", 0, "router:Verdict", "probe")
    handle = rt.queue.open(actor="router:Verdict", event_id="probe-meta", propensity=prop,
                           channel="fast", deadline_ns=10**18, parent_handle=None,
                           cost_ceiling=0)
    rt.handle_to_assembly[handle] = "meta-a"
    rt._open_evaluation(handle, about="judge-decision", q=0.7, evaluator_id="meta-a", tier=2)
    rt._close_consequence("judge-decision", None)
    rt._settle_evaluations()
    assert str(rt.queue.get(handle).status) == "censored"
    assert rt.stats.consequences_by_assembly.get("meta-a", 0) == 0
