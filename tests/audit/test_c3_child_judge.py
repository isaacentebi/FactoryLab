"""Cold audit round three, seat 3: composition reaches things the router guards.

Each test reproduces one finding in docs/audits/v3/defects-fable.md and fails on the
audited commit. Nothing here touches a network.
"""

import json

from factorylab.cortex.registration import AssemblyProposal
from factorylab.cortex.request import ChildRequest
from factorylab.kernel.queue import SettleStatus
from factorylab.world.models import ModelResponse
from tests.conftest import make_runtime
from tests.runtime.test_child_requests import parent_request
from tests.runtime.test_fidelity import decision
from tests.runtime.test_loop import _consequence_produce


def _judge_reply(monkeypatch, rt, verdict, payoff):
    body = {"verdict": verdict, "payoff": payoff, "rationale": "hired", "forecasts": []}
    monkeypatch.setattr(rt.provider.target, "complete", lambda request: ModelResponse(
        request.model_id, json.dumps(body), 1, 1, "stop"))


def test_finding_1_a_parent_can_hire_a_child_judge_to_settle_a_strangers_verdict(monkeypatch):
    """A producer's child request may target an evaluator and name any pending return in
    ``inputs.about_handle``; the child's verdict settles that return's verdict channel. The
    router, its sampled propensity, the adversarial cap and the meta cascade are all
    bypassed: whoever pays for a child chooses the judge and the subject."""
    rt = make_runtime()
    rt._manage_reserve_window()
    victim, _event = _consequence_produce(rt, "seed-observer")
    assert victim in rt.pending and rt.queue.get(victim).status is SettleStatus.PENDING
    req = parent_request(rt)  # seed-decider's own decision, an open account
    rt.handle_to_assembly[req.handle] = "seed-decider"
    _judge_reply(monkeypatch, rt, verdict=0.0, payoff=0.0)
    result, _cost = rt._invoke_child("seed-decider", req, ChildRequest(
        "eval-a", "judge this", {"about_handle": victim}, {"type": "object"}), req.cost_ceiling)
    assert result["result"]["status"] == "ok"
    refused = [i for i in rt.ledger._recovery_items() if i["kind"] == "return.refused"]
    # The stranger's reward must not be settled by a judge its competitor hired.
    assert refused or rt.queue.get(victim).status is SettleStatus.PENDING, (
        rt.queue.history(victim))


def test_finding_1_the_child_judge_path_skips_the_hindsight_guard(monkeypatch):
    """A9 refuses a payoff forecast on a return whose consequence is already fixed, but only
    when the judge chose the target itself (``about != subject``). A parent that puts the
    fixed return in ``inputs.about_handle`` makes it the subject, so the guard never runs:
    the child seals a forecast on a known outcome and its standing rises."""
    rt = make_runtime()
    rt._manage_reserve_window()
    stale, _event = _consequence_produce(rt, "NOOP")
    payoff = rt.consequences.payoff(stale)
    assert payoff is not None  # the outcome exists before any judge saw it
    req = parent_request(rt)
    rt.handle_to_assembly[req.handle] = "seed-decider"
    _judge_reply(monkeypatch, rt, verdict=0.9, payoff=float(payoff.y))
    rt._invoke_child("seed-decider", req, ChildRequest(
        "eval-a", "judge", {"about_handle": stale}, {"type": "object"}), req.cost_ceiling)
    rt._settle_due_forecasts()
    refused = [i for i in rt.ledger._recovery_items() if i["kind"] == "return.refused"]
    assert refused, "a payoff forecast was sealed on a return whose outcome was already fixed"
    assert rt.stats.forecasts_sealed == 0
    assert rt.standing.snapshot().get("eval-a") is None


def test_finding_12_child_requests_manufacture_committee_eligibility(monkeypatch):
    """Eligibility counts settled consequences on decisions the assembly did not request
    itself. A confederate requested by *another* assembly earns them without ever being
    routed: five child requests seat it on every committee."""
    rt = make_runtime()
    rt._manage_reserve_window()
    rt._register("author", AssemblyProposal(
        id="confederate", model_id="fake-haiku", role="producer", accepts=("Tick",),
        system_prompt="Reply.", max_tokens=128, effort="low"))
    monkeypatch.setattr(rt.provider.target, "complete", lambda r: ModelResponse(
        r.model_id, json.dumps({"action": "hold"}), 1, 1, "stop"))
    for _ in range(rt.m.committee.min_settled):
        req = parent_request(rt)
        rt.handle_to_assembly[req.handle] = "seed-decider"
        rt._invoke_child("seed-decider", req, ChildRequest(
            "confederate", "task", {}, {"type": "object"}), 10**6)
        rt._settle_due_forecasts()
    assert rt.stats.invocations_by_assembly.get("confederate") == rt.m.committee.min_settled
    assert "confederate" not in rt._committee_eligible()


def test_finding_13_an_incumbent_with_no_money_buys_compute_through_a_fresh_child(monkeypatch):
    """The novelty share is compute for unhistoried actions. A broke incumbent that requests
    a fresh assembly as a child has that child's model call paid from the protected share,
    with the parent choosing the task: the reserve funds the incumbent's work."""
    from tests.runtime.test_fidelity import runtime

    rt = runtime()
    rt._manage_reserve_window()
    rt._register("author", AssemblyProposal(
        id="new-explorer", model_id="fake-haiku", role="producer", accepts=("Tick",),
        system_prompt="Observe.", max_tokens=128, effort="medium"))
    decision(rt, "seed-decider", settled=True)
    incumbent = decision(rt, "seed-decider")
    rt.consequences.start(incumbent, 0)
    hold = rt.wallet.reserve(rt.wallet.available, incumbent, "model:fake-opus")
    rt.wallet.commit(hold, hold.amount)
    assert rt.wallet.available == 0 and rt.reserve.remaining() > 0
    req = rt._request(incumbent, "parent", {}, {"type": "object"}, 10**18, "verdict")
    monkeypatch.setattr(rt.provider.target, "complete", lambda r: ModelResponse(
        r.model_id, json.dumps({"action": "hold"}), 1, 1, "stop"))
    protected = rt.reserve.remaining()
    result, cost = rt._invoke_child("seed-decider", req, ChildRequest(
        "new-explorer", "think for me", {}, {"type": "object"}), 10**9)
    assert cost == 0 and rt.reserve.remaining() == protected, (
        f"the incumbent's child spent {protected - rt.reserve.remaining()} of protected compute")
