from types import SimpleNamespace

import pytest

from factorylab.charter.amendment import PredictedEffect
from factorylab.charter.controller import CardRegion, promise_kept
from factorylab.cortex.registration import RouterProposal
from factorylab.cortex.request import Return
from factorylab.kernel.queue import PropensityRecord, SettleStatus
from tests.conftest import make_runtime


def test_votes_have_one_queue_decision_per_seat_per_amendment(monkeypatch):
    rt = make_runtime()
    monkeypatch.setattr(rt.charter_book, 'vote', lambda *_: None)
    monkeypatch.setattr(rt.charter_book, 'abstain', lambda *_: None)
    monkeypatch.setattr(rt.charter_book, 'tally', lambda *_: 'failed')
    amendment = SimpleNamespace(id='test', proposed_prices=(), add=(), replace=(), remove=(),
                                predicted_effect=PredictedEffect("cost_per_return", "decrease", 1),
                    tick_interval=None)
    committee = SimpleNamespace(seats=[('seat1', 'seed-decider'), ('seat2', 'seed-decider')])
    rt._hold_vote(amendment, committee)
    n = rt.stats.invocations
    rt._hold_vote(amendment, committee)
    assert rt.stats.invocations == n == 2
    items = rt.ledger._recovery_items()
    decisions = [i for i in items if i['kind'] == 'decision.open']
    calls = [i for i in items if i['kind'] == 'invocation']
    assert len(decisions) == len(calls) == 2
    assert {i['handle'] for i in decisions} == {i['handle'] for i in calls}
    # Charter audit P1: a failed motion's ballots are not censored; each waits to be
    # graded on the reject branch, against the unchanged charter.
    waiting = {v['handle']: v['branch'] for v in rt.pending_votes}
    assert waiting == {call['handle']: 'reject' for call in calls}
    assert rt.window.invocations == 2


def test_a_router_add_at_the_cap_is_refused_before_the_receipt_is_spent():
    """Codex finding: an `add` proposal registered its router contract, consuming the
    novelty receipt, and only then did `_build_router` refuse it at the cap: an orphan
    contract in an append-only registry and no refund. Everything that can refuse the
    router is checked first."""
    rt = make_runtime()
    rt._manage_reserve_window()
    cap = rt.m.tools.max_routers_per_kind
    while len(rt.routers['Tick']) < cap:
        rt._build_router('Tick', 'exp3', .1, replace=False)
    registry, remaining = rt.registry.state(), rt.reserve.remaining()
    rt._apply_registrations('author', Return('author', {'register': [
        {'kind': 'router', 'learner': 'exp3', 'event_kind': 'Tick', 'add': True}]}, 0, 'ok'))
    assert rt.stats.registrations_rejected == 1 and rt.stats.registrations_accepted == 0
    rejected = [i for i in rt.ledger._recovery_items() if i['kind'] == 'registration.rejected']
    assert 'router cap reached' in rejected[-1]['reason']
    assert len(rt.routers['Tick']) == cap
    assert rt.registry.state() == registry and rt.reserve.remaining() == remaining
    assert not [i for i in rt.ledger._recovery_items() if i['kind'] == 'novelty.release']
    assert rt.wallet.check_conservation()
    # Replacing a router is still allowed at the cap, and that one does register.
    rt._register('author', RouterProposal('Tick', 'exp3', .1))
    assert len(rt.routers['Tick']) == 1 and rt.reserve.remaining() < remaining


FLOOR = CardRegion("well_formed_rate", "min", 0.9, None, 1.0)


def _policy_decision(rt, assembly="eval-a"):
    """A ballot's decision lives on the policy channel under the assembly's identity."""
    return rt.queue.open(
        actor=f"assembly:{assembly}", event_id="test", channel="policy", deadline_ns=10**18,
        parent_handle=None, cost_ceiling=0,
        propensity=PropensityRecord((assembly,), (1.0,), assembly, 0, f"assembly:{assembly}",
                                    "direct-committee-seat"),
    )


@pytest.mark.parametrize("direction, baseline, value, kept", [
    # Already inside: kept only by staying inside without moving against the promise.
    ("increase", 0.95, 0.92, False),   # the reviewer's case: compliant, went the wrong way
    ("decrease", 0.92, 0.95, False),   # the mirror
    ("increase", 0.92, 0.95, True),
    ("decrease", 0.95, 0.92, True),
    ("increase", 0.95, 0.945, True),   # within resolution: did not move
    ("decrease", 0.95, 0.955, True),
    ("increase", 0.95, 0.99, True),
    ("decrease", 0.95, 0.85, False),   # left the region
    # Outside at baseline: kept only by moving the promised way by the resolution.
    ("increase", 0.5, 0.6, True),      # still outside, but the promise held
    ("increase", 0.5, 0.505, False),   # below resolution
    ("increase", 0.5, 0.4, False),
    ("decrease", 0.5, 0.4, True),
])
def test_promise_grading_scores_the_direction_against_the_baseline(direction, baseline, value,
                                                                   kept):
    assert promise_kept(direction, baseline, value, FLOOR, resolution=0.01) is kept


def _ballot(rt, monkeypatch, direction, *, baseline, value, vote=True):
    """One favourable ballot on the well-formed card, activated at ``baseline``."""
    import factorylab.runtime.governance as governance

    reading = {"value": baseline}
    monkeypatch.setattr(governance, "measure_card",
                        lambda card, samples, observations=None: {"all": reading["value"]})
    card = next(c for c in rt.charter.cards if c.id == "well_formed_rate")
    proposal = SimpleNamespace(id=f"am-{direction}",
                               predicted_effect=PredictedEffect(card.id, direction, 1))
    handle = _policy_decision(rt)
    rt._record_policy_ballot(proposal, handle, "eval-a", vote)
    rt._activate_policy_ballots(proposal.id)
    ballot = rt.pending_votes[-1]
    assert ballot["baseline"] == baseline and ballot["region"] is not None
    reading["value"] = value
    # Grading waits min_ratio consequence periods after activation (time audit T2).
    rt._close_policy_window(rt.window.index)
    assert not [i for i in rt.ledger._recovery_items() if i["kind"] == "policy.outcome"]
    rt.ticks_consumed += rt._policy_floor()
    rt._close_policy_window(rt.window.index)
    outcome = [i for i in rt.ledger._recovery_items() if i["kind"] == "policy.outcome"][-1]
    return handle, outcome


@pytest.mark.parametrize("direction, baseline, value", [
    ("increase", 0.95, 0.92), ("decrease", 0.92, 0.95),
])
def test_a_vote_for_a_change_that_went_the_wrong_way_is_wrong_inside_the_region(
        monkeypatch, direction, baseline, value):
    """P2-09: the frozen region still holds, yet the promise was broken."""
    rt = make_runtime()
    handle, outcome = _ballot(rt, monkeypatch, direction, baseline=baseline, value=value)
    assert outcome["y"] is False and outcome["score"] == 0.0
    assert outcome["direction"] == direction and outcome["resolution"] == 0.01
    assert outcome["baseline"] == baseline and outcome["value"] == value
    settled = rt.queue.history(handle)[-1]
    assert settled.score == 0.0 and settled.status is SettleStatus.SETTLED
    assert settled.definition_version == "policy-promise-brier-v2"


def test_a_ballot_without_a_baseline_is_censored(monkeypatch):
    import factorylab.runtime.governance as governance

    rt = make_runtime()
    monkeypatch.setattr(governance, "measure_card", lambda card, samples, observations=None: {})
    card = next(c for c in rt.charter.cards if c.id == "well_formed_rate")
    proposal = SimpleNamespace(id="am-none",
                               predicted_effect=PredictedEffect(card.id, "increase", 1))
    handle = _policy_decision(rt)
    rt._record_policy_ballot(proposal, handle, "eval-a", True)
    rt._activate_policy_ballots("am-none")
    assert rt.pending_votes[-1]["baseline"] is None
    monkeypatch.setattr(governance, "measure_card",
                        lambda card, samples, observations=None: {"all": 0.95})
    rt.ticks_consumed += rt._policy_floor()
    rt._close_policy_window(rt.window.index)
    outcome = [i for i in rt.ledger._recovery_items() if i["kind"] == "policy.outcome"][-1]
    assert outcome["y"] is None and outcome["status"] == str(SettleStatus.CENSORED)
    assert rt.queue.history(handle)[-1].status is SettleStatus.CENSORED
