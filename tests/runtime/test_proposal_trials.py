"""A proposal is paid for by the seat that proposes it (defect 13).

Every voter pays for its own ballot, but the amendment, retirement, connector and
challenge proposals that summon those ballots took their novelty receipt without
charging the proposer anything: the one seat that chose to spend the committee's
money was the one seat that spent none of its own. A tool, a model or a learner
registration charges its proposer the trial; so does a proposal now.
"""

import pytest

from factorylab.charter.amendment import PredictedEffect
from factorylab.cortex.registration import RetireProposal
from factorylab.kernel.queue import PropensityRecord
from factorylab.kernel.wallet import Infeasible
from tests.conftest import make_runtime

SEAT = "seed-decider"


def _proposer_handle(rt, seat=SEAT, event="proposal"):
    prop = PropensityRecord((seat,), (1.0,), seat, 0, "router:Tick", "test")
    handle = rt.queue.open(actor="router:Tick", event_id=event, propensity=prop,
                           channel="verdict", deadline_ns=10**18, parent_handle=None,
                           cost_ceiling=0)
    rt.handle_to_assembly[handle] = seat
    return handle


def _runtime(monkeypatch):
    rt = make_runtime()
    rt._manage_reserve_window()
    votes = []
    monkeypatch.setattr(rt, "_hold_vote", lambda am, committee, **_k: votes.append(am.id))
    # Enough eligible seats, besides proposer and target, for committee.quorum.
    monkeypatch.setattr(rt, "_committee_eligible", lambda: {
        "eval-b": "evaluator", "meta-a": "meta", "antagonist-a": "antagonist"})
    return rt, votes


def test_a_retirement_proposal_charges_its_proposer_the_trial(monkeypatch):
    rt, votes = _runtime(monkeypatch)
    before = rt.budget.entitlement(SEAT)
    rt._propose_retirement(_proposer_handle(rt), RetireProposal("eval-a"),
                           predicted_effect=PredictedEffect("cost_per_return", "decrease", 1))
    assert votes
    assert rt.budget.entitlement(SEAT) == before - rt.ev.trial_amount_micro
    assert rt.budget.check_invariant()


def test_an_amendment_proposal_charges_its_proposer_the_trial(monkeypatch):
    rt, votes = _runtime(monkeypatch)
    before = rt.budget.entitlement(SEAT)
    card = rt.charter.cards[0]
    rt._propose_amendment(_proposer_handle(rt), {
        "kind": "amendment", "id": "drop-one", "remove": [card.id],
        "predicted_effect": {"card_id": rt.charter.cards[-1].id, "direction": "increase",
                             "window": 1}})
    # The motion waits for the next governance boundary's committee (charter audit C1).
    assert votes == [] and [am.id for am in rt.charter_book.agenda()] == ["drop-one"]
    assert rt.budget.entitlement(SEAT) == before - rt.ev.trial_amount_micro


def test_a_proposer_that_cannot_pay_summons_no_committee(monkeypatch):
    rt, votes = _runtime(monkeypatch)
    rt.budget.debit(SEAT, rt.budget.entitlement(SEAT) - 1, "test: nearly spent")
    registry = rt.registry.state()
    with pytest.raises(Infeasible):
        rt._propose_retirement(_proposer_handle(rt), RetireProposal("eval-a"),
                               predicted_effect=PredictedEffect("cost_per_return", "decrease", 1))
    assert votes == [] and rt.registry.state() == registry
    assert rt.budget.entitlement(SEAT) == 1
