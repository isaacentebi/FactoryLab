"""The collaboration credit: composition earns through the ordinary reward channel.

Chapter II rulings §2, Composition: "When the requesting decision is scored, the
reward flows back to each executor it composed. That is the collaboration credit,
carried by the ordinary reward channel." Essay II.I.b: the reward is a thin score,
delayed, and "must find its way back to the exact decision". The attribution rule
and its argument are ``factorylab.runtime.feedback.composed_reward``.
"""

import json
from dataclasses import replace

import pytest

from factorylab.cortex.registration import AssemblyProposal, ToolProposal
from factorylab.cortex.request import ChildRequest
from factorylab.kernel.queue import SettleStatus
from factorylab.runtime.feedback import PendingJudgement, composed_reward
from factorylab.runtime.resume import restore_runtime, runtime_state
from factorylab.runtime.shared import (
    CH_VERDICT,
    DEF_COMPOSED,
    DEF_VERDICT,
    request_router_key,
)
from factorylab.world.models import ModelResponse
from tests.conftest import make_runtime as _runtime
from tests.runtime.test_child_requests import parent_request

TASK = ChildRequest("ProducerReturn", "helper task", {"q": 1}, {"type": "object"})


def make_runtime():
    rt = _runtime()
    rt._manage_reserve_window()
    return rt


def _answering(rt, monkeypatch):
    monkeypatch.setattr(rt.provider.target, "complete", lambda request: ModelResponse(
        request.model_id, json.dumps({"action": "hold"}), 1, 1, "stop"))


def _requester(rt):
    """A producer decision of seed-decider's, judged like a routed one."""
    req = parent_request(rt)
    rt.handle_to_assembly[req.handle] = "seed-decider"
    rt.pending[req.handle] = PendingJudgement(req.handle, CH_VERDICT, rt.n,
                                              opened_at_tick=rt.ticks_consumed)
    return req


def _only_helper(rt, monkeypatch, *, registered_by=None):
    """One executor besides the requester for ProducerReturn: helper-a."""
    if registered_by is None:
        rt._instantiate(replace(rt.assemblies["seed-decider"].spec, id="helper-a"))
    else:
        handle = parent_request(rt).handle
        rt.handle_to_assembly[handle] = registered_by
        rt._register(handle, AssemblyProposal("helper-a", "producer", "fake-haiku",
                                              "Reply with JSON.", ("Tick",), 128, "low"))
    rt._retire_assembly("seed-observer", "test")
    _answering(rt, monkeypatch)


def _child(rt, req):
    rt._invoke_child("seed-decider", req, TASK, req.cost_ceiling)
    row = [i for i in rt.ledger._recovery_items() if i["kind"] == "request.child"][-1]
    assert row["target"] == "helper-a"
    return row["handle"]


def _verdicts(rt, about, *scores):
    rt.arrived_verdicts[about] = [[f"judge-{i}", s] for i, s in enumerate(scores)]
    rt._settle_arrived_verdicts()
    rt._settle_composed()


def test_credit_reaches_the_executors_handle_only_after_its_requester_settles(monkeypatch):
    rt = make_runtime()
    _only_helper(rt, monkeypatch)
    req = _requester(rt)
    child = _child(rt, req)
    assert rt.pending[child].requester == req.handle
    # The child's own judges have spoken; it still waits for its requester.
    _verdicts(rt, child, 0.8)
    assert rt.queue.get(child).status is SettleStatus.PENDING
    assert rt.pending[child].verdicts == [["judge-0", 0.8]]
    # The requester settles on its judges; then, and only then, the child does.
    _verdicts(rt, req.handle, 0.4)
    assert rt.queue.get(req.handle).status is SettleStatus.SETTLED
    (settled,) = rt.queue.history(child)
    assert settled.definition_version == DEF_COMPOSED
    assert settled.score == pytest.approx(composed_reward(0.8, 0.4)) == pytest.approx(0.6)
    assert rt.queue.history(req.handle)[0].score == pytest.approx(0.4)  # untouched
    row = [i for i in rt.ledger._recovery_items() if i["kind"] == "composed.settled"]
    assert [(r["handle"], r["verdict"], r["credit"]) for r in row] == [(child, 0.8, 0.4)]
    # It is the executor's reward on the executor's own handle: its inbox is told...
    item = rt.outcomes.get("helper-a", child, delivered=False)
    assert "composed_settled" in json.dumps(item) and req.handle not in json.dumps(item)
    # ...and the request router that drew it learns from it.
    rt._deliver_returns()
    state = rt.routers[request_router_key("ProducerReturn")][0]
    assert state.observed.sums["helper-a"] == [pytest.approx(0.6), 1]
    assert state.definitions == {DEF_COMPOSED: 1}


def test_a_requester_that_settles_unscored_leaves_the_child_its_own_verdict(monkeypatch):
    rt = make_runtime()
    _only_helper(rt, monkeypatch)
    req = _requester(rt)
    child = _child(rt, req)
    _verdicts(rt, child, 0.7)
    del rt.pending[req.handle]
    rt.queue.settle(req.handle, channel=CH_VERDICT, score=0.0, status=SettleStatus.CENSORED,
                    definition_version="censored-v1", sampling_ref=None)
    rt._settle_composed()
    (settled,) = rt.queue.history(child)
    assert (settled.definition_version, settled.score) == (DEF_VERDICT, pytest.approx(0.7))


def test_an_unjudged_child_settles_on_its_requesters_credit_alone(monkeypatch):
    rt = make_runtime()
    _only_helper(rt, monkeypatch)
    req = _requester(rt)
    child = _child(rt, req)
    _verdicts(rt, req.handle, 0.9)
    assert rt.queue.get(child).status is SettleStatus.PENDING  # its judges may still come
    rt.ticks_consumed += rt.ev.verdict_timeout_ticks + 1
    rt._censor_stale_judgements()  # a held child is not stale: it is still owed a credit
    rt._settle_composed()
    (settled,) = rt.queue.history(child)
    assert (settled.definition_version, settled.score) == (DEF_COMPOSED, pytest.approx(0.9))


def test_a_child_of_the_requesters_own_lineage_earns_nothing_extra(monkeypatch):
    rt = make_runtime()
    _only_helper(rt, monkeypatch, registered_by="seed-decider")
    assert rt.budget.lineage("helper-a") == rt.budget.lineage("seed-decider")
    req = _requester(rt)
    child = _child(rt, req)
    assert rt.pending[child].requester is None
    withheld = [i for i in rt.ledger._recovery_items() if i["kind"] == "credit.withheld"]
    assert [i["handle"] for i in withheld] == [child]
    _verdicts(rt, child, 0.8)  # it settles at once, on its own verdict
    assert rt.queue.history(child)[0].definition_version == DEF_VERDICT
    _verdicts(rt, req.handle, 0.1)
    assert len(rt.queue.history(child)) == 1


def test_a_held_child_and_its_credit_survive_a_checkpoint(monkeypatch):
    rt = make_runtime()
    _only_helper(rt, monkeypatch)
    req = _requester(rt)
    child = _child(rt, req)
    _verdicts(rt, child, 0.8)
    monkeypatch.undo()
    rt.provider.target.__dict__.pop("complete", None)
    restored = _runtime()
    restored._instantiate(replace(restored.assemblies["seed-decider"].spec, id="helper-a"))
    restore_runtime(restored, runtime_state(rt))
    assert restored.pending[child].requester == req.handle
    _verdicts(restored, req.handle, 0.4)
    assert restored.queue.history(child)[0].score == pytest.approx(0.6)


# --- population tools: the same primitive, credited to the tool's builder -----------


def _tool_world(monkeypatch, builder="seed-observer"):
    rt = make_runtime()
    rt.tool_jail_available = True
    author = parent_request(rt).handle
    rt.handle_to_assembly[author] = builder
    rt._register(author, ToolProposal("doubler", "doubles x",
                                      {"type": "object", "properties": {}}, "", 1))
    monkeypatch.setattr(rt.tool_runner, "run", lambda tool, args: {"y": 2})
    return rt, author


def _call(rt, seat, handle):
    return rt._run_tool(seat, handle, {"tool": "doubler", "args": {}})[0]


def _settle(rt, handle, score):
    rt._settle_priced(handle, channel=CH_VERDICT, score=score, definition_version=DEF_VERDICT,
                      sampling_ref=None, cards="producer")


def test_a_tool_builder_is_credited_once_on_its_registering_handle_after_the_call_settles(
        monkeypatch):
    rt, author = _tool_world(monkeypatch)
    caller = parent_request(rt).handle
    assert _call(rt, "seed-decider", caller) == {"y": 2}
    assert _call(rt, "seed-decider", caller) == {"y": 2}
    assert rt.tool_uses == {caller: {"doubler": 2}}
    assert not [i for i in rt.ledger._recovery_items() if i["kind"] == "credit.tool"]
    _settle(rt, caller, 0.7)
    (credit,) = [i for i in rt.ledger._recovery_items() if i["kind"] == "credit.tool"]
    assert (credit["builder"], credit["registered_by"], credit["calls"], credit["credit"]) == (
        "seed-observer", author, 2, 0.7)
    item = rt.outcomes.get("seed-observer", author, delivered=False)
    assert "tool_use_credit" in json.dumps(item) and caller not in json.dumps(item)
    assert rt.tool_uses == {}
    calls = [i for i in rt.ledger._recovery_items() if i["kind"] == "tool.population_call"]
    assert [i["across_lineage"] for i in calls] == [True, True]


def test_a_seat_calling_its_own_lineages_tool_earns_nothing(monkeypatch):
    rt, _author = _tool_world(monkeypatch)
    caller = parent_request(rt).handle
    _call(rt, "seed-observer", caller)
    assert rt.tool_uses == {}
    _settle(rt, caller, 0.9)
    assert not [i for i in rt.ledger._recovery_items() if i["kind"] == "credit.tool"]


def test_a_call_whose_decision_closes_unscored_credits_nobody(monkeypatch):
    rt, _author = _tool_world(monkeypatch)
    caller = parent_request(rt).handle
    _call(rt, "seed-decider", caller)
    rt.queue.settle(caller, channel=CH_VERDICT, score=0.0, status=SettleStatus.CENSORED,
                    definition_version="censored-v1", sampling_ref=None)
    rt._settle_composed()
    assert rt.tool_uses == {}
    assert not [i for i in rt.ledger._recovery_items() if i["kind"] == "credit.tool"]


def test_a_failed_tool_call_is_not_a_use(monkeypatch):
    rt, _author = _tool_world(monkeypatch)
    monkeypatch.setattr(rt.tool_runner, "run", lambda tool, args: {"error": "timeout"})
    caller = parent_request(rt).handle
    _call(rt, "seed-decider", caller)
    assert rt.tool_uses == {}
