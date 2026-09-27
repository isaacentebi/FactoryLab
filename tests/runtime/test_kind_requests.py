"""Primitive audit F5 and information audit M1: requests are addressed to kinds.

Essay II.I: Class 3 composability means an orchestration layer that can "discover
available primitives, assess their contracts, and assemble them against an input
without any single system or agent needing to hold the full topology", where an
agent "can be swapped, rerouted, parallelized, or removed without cascading
failures". A request therefore names a kind of work; that kind's request router
draws the executor with a logged propensity, learns from the child's settlement,
and a retirement changes its menu without failing anyone. The requester's own
propensity travels forward with the request (essay II.I.b).
"""

import json
from dataclasses import replace

import pytest

from factorylab.cortex.assembly import _check_child
from factorylab.cortex.request import ChildRequest
from factorylab.kernel.queue import SettleStatus
from factorylab.runtime.composition import REQUESTER_DECLARED
from factorylab.runtime.shared import CH_VERDICT, NOOP, request_router_key
from factorylab.settlement.vocabulary import COMMISSIONED_JUDGE_REFUSAL
from factorylab.world.models import ModelResponse
from tests.conftest import make_runtime
from tests.runtime.test_child_requests import parent_request
from tests.runtime.test_loop import lists_nothing

TASK = ChildRequest("ProducerReturn", "helper task", {"q": 1}, {"type": "object"})


def _world(monkeypatch, *helpers, reply=None):
    """A scripted world with extra ProducerReturn seats and a fixed child answer."""
    rt = lists_nothing(make_runtime())
    spec = rt.assemblies["seed-decider"].spec
    for aid in helpers:
        rt._instantiate(replace(spec, id=aid))
    monkeypatch.setattr(rt.provider.target, "complete", lambda request: ModelResponse(
        request.model_id, json.dumps(reply or {"action": "hold"}), 1, 1, "stop"))
    return rt, _parent(rt)


def _parent(rt):
    """A decision of seed-decider's that requests work, as a routed one would be."""
    req = parent_request(rt)
    rt.handle_to_assembly[req.handle] = "seed-decider"
    return req


def _children(rt):
    return [i for i in rt.ledger._recovery_items() if i["kind"] == "request.child"]


def test_the_kinds_router_draws_the_executor_with_a_logged_propensity(monkeypatch):
    rt, req = _world(monkeypatch, "helper-a", "helper-b")
    result, cost = rt._invoke_child("seed-decider", req, TASK, req.cost_ceiling)
    assert result["tool"] == "request:ProducerReturn" and result["result"]["status"] == "ok"
    (child,) = _children(rt)
    decision = rt.queue.get(child["handle"])
    key = request_router_key("ProducerReturn")
    state = rt.routers[key][0]
    # The router over this kind's emitters opened the decision, as the actor that drew it.
    assert decision.actor == state.learner.id == child["router"]
    prop = decision.propensity
    assert prop.source == "sampled" and prop.chosen == child["target"]
    assert set(prop.action_ids) == {"helper-a", "helper-b", "seed-observer", NOOP}
    # Neither the requester nor NOOP can be drawn: the work is already paid for.
    assert "seed-decider" not in prop.action_ids
    assert prop.probs[prop.action_ids.index(NOOP)] == 0
    assert rt._router_sampled(decision)


def test_the_request_router_learns_from_the_childs_settlement(monkeypatch):
    rt, req = _world(monkeypatch, "helper-a")
    rt._invoke_child("seed-decider", req, TASK, req.cost_ceiling)
    (child,) = _children(rt)
    state = rt.routers[request_router_key("ProducerReturn")][0]
    before = state.learner.state()
    rt.pending.pop(child["handle"], None)
    rt.queue.settle(child["handle"], channel=CH_VERDICT, score=0.9,
                    status=SettleStatus.SETTLED, definition_version="verdict-v1",
                    sampling_ref=None)
    rt._deliver_returns()
    assert state.latency[1] == 1 and state.observed.sums[child["target"]][1] == 1
    assert state.learner.state() != before


def test_a_request_router_survives_a_checkpoint(monkeypatch):
    from factorylab.runtime.resume import restore_runtime, runtime_state

    rt, req = _world(monkeypatch, "helper-a")
    rt._invoke_child("seed-decider", req, TASK, req.cost_ceiling)
    monkeypatch.undo()
    rt.provider.target.__dict__.pop("complete", None)
    key = request_router_key("ProducerReturn")
    restored = make_runtime()
    restored._instantiate(replace(restored.assemblies["seed-decider"].spec, id="helper-a"))
    restore_runtime(restored, runtime_state(rt))
    assert restored.routers[key][0].learner.state() == rt.routers[key][0].learner.state()
    assert restored.routers[key][0].universe == rt.routers[key][0].universe


def test_a_retired_executor_never_fails_a_request(monkeypatch):
    rt, req = _world(monkeypatch, "helper-a")
    rt._invoke_child("seed-decider", req, TASK, req.cost_ceiling)
    first = _children(rt)[0]["target"]
    rt._retire_assembly(first, "test")
    for _ in range(3):
        parent = _parent(rt)
        result, _cost = rt._invoke_child("seed-decider", parent, TASK, parent.cost_ceiling)
        assert result["result"]["status"] == "ok"
    later = {c["target"] for c in _children(rt)[1:]}
    assert first not in later and later
    menu = rt.routers[request_router_key("ProducerReturn")][0].universe
    assert first not in menu


def test_a_request_for_a_kind_nobody_else_serves_is_refused_before_any_decision(monkeypatch):
    rt, req = _world(monkeypatch)
    for target in ("seed-decider", "Nothing"):  # an id is not a kind
        result, cost = rt._invoke_child(
            "seed-decider", req, replace(TASK, target=target), req.cost_ceiling)
        assert cost == 0 and "no live contract" in result["result"]["error"]
    # Only judges emit Verdict, and a judge cannot be commissioned.
    result, _ = rt._invoke_child("seed-decider", req, replace(TASK, target="Verdict"), 10**9)
    assert result["result"]["error"] == COMMISSIONED_JUDGE_REFUSAL
    assert not _children(rt)


def test_an_emitted_kind_is_served_by_its_emitters_before_its_acceptors(monkeypatch):
    rt, req = _world(monkeypatch)
    assert rt._request_universe("ProducerReturn") == ["seed-decider", "seed-observer"]
    # Nobody emits Funding; the seats that accept it are its menu.
    assert rt._request_universe("Funding") == ["seed-observer"]
    rt._invoke_child("seed-decider", req, replace(TASK, target="Funding"), req.cost_ceiling)
    assert _children(rt)[0]["target"] == "seed-observer"


def test_the_requesters_propensity_is_forwarded_and_recorded_on_the_childs_handle(monkeypatch):
    rt, req = _world(monkeypatch, "helper-a")
    seen = []
    monkeypatch.setattr(rt.provider.target, "complete", lambda request: (
        seen.append(request.messages[-1]["content"]) or ModelResponse(
            request.model_id, json.dumps({"action": "hold"}), 1, 1, "stop")))
    forwarded = replace(TASK, propensity={"delegate": 0.6, "do-it-myself": 0.4},
                        chosen="delegate")
    rt._invoke_child("seed-decider", req, forwarded, req.cost_ceiling)
    (child,) = _children(rt)
    router, requester, executor = rt.queue.propensities(child["handle"])
    assert router.source == "sampled" and router.chosen == child["target"]
    assert (requester.source, requester.learner_state_hash) == ("declared", REQUESTER_DECLARED)
    assert dict(zip(requester.action_ids, requester.probs, strict=True)) == {
        "delegate": 0.6, "do-it-myself": 0.4}
    assert requester.chosen == "delegate" and executor.chosen != "delegate"
    assert child["forwarded_propensity"] == {"delegate": 0.6, "do-it-myself": 0.4}
    # It travels forward on the child's own request.
    assert "do-it-myself" in seen[0]


@pytest.mark.parametrize("item,reason", [
    ({"chosen": "delegate"}, "chosen needs the propensity"),
    ({"propensity": {"delegate": 1.0}, "chosen": "other"}, "positive mass"),
    ({"propensity": {"delegate": 1.0}}, "positive mass"),
    ({"propensity": {"delegate": 0.5}, "chosen": "delegate"}, "sum to one"),
])
def test_a_malformed_forwarded_propensity_is_refused(item, reason):
    with pytest.raises(ValueError, match=reason):
        _check_child({"target": "ProducerReturn", "description": "d", "inputs": {},
                      "outcome_schema": {"type": "object"}, **item})


def test_a_self_request_stays_parent_selected_and_trains_no_router(monkeypatch):
    rt, req = _world(monkeypatch)
    rt._invoke_child("seed-decider", req, replace(TASK, target="self"), req.cost_ceiling)
    (child,) = _children(rt)
    decision = rt.queue.get(child["handle"])
    assert child["target"] == "seed-decider" and child["router"] is None
    assert decision.propensity.learner_state_hash == "parent-selected"
    assert not rt._router_sampled(decision)
