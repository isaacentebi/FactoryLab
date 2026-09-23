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
from factorylab.kernel.queue import PropensityRecord, SettleStatus
from factorylab.runtime.feedback import PendingJudgement, composed_reward
from factorylab.runtime.resume import restore_runtime, runtime_state
from factorylab.runtime.shared import (
    CH_EXPOSURE,
    CH_VERDICT,
    DEF_COMPOSED,
    DEF_EXPOSURE,
    DEF_VERDICT,
    request_router_key,
)
from factorylab.world.models import ModelResponse
from tests.conftest import make_runtime as _runtime

TASK = ChildRequest("ProducerReturn", "helper task", {"q": 1}, {"type": "object"})
FAR = 10**18


def make_runtime():
    rt = _runtime()
    rt._manage_reserve_window()
    return rt


def _answering(rt, monkeypatch):
    monkeypatch.setattr(rt.provider.target, "complete", lambda request: ModelResponse(
        request.model_id, json.dumps({"action": "hold"}), 1, 1, "stop"))


def _decision(rt, seat, *, channel=CH_VERDICT, parent=None, judged=True):
    """A decision of ``seat``'s, opened as a routed one would be, and its request."""
    lid = f"router:test-{seat}"
    handle = rt.queue.open(actor=lid, event_id=f"test-{rt.n}-{seat}",
                           propensity=PropensityRecord((seat,), (1.,), seat, 0, lid, "t"),
                           channel=channel, deadline_ns=FAR,
                           parent_handle=parent, cost_ceiling=3_000_000)
    rt.n += 1
    rt.consequences.start(handle, 0)
    rt.handle_to_assembly[handle] = seat
    if judged:
        rt.pending[handle] = PendingJudgement(handle, channel, rt.n,
                                              opened_at_tick=rt.ticks_consumed)
    return replace(rt._request(handle, "parent task", {}, {"type": "object"}, FAR, channel),
                   cost_ceiling=3_000_000)


def _registered(rt, aid, by):
    """Register ``aid`` as a ProducerReturn seat from a decision of ``by``'s lineage."""
    author = _decision(rt, by, judged=False).handle
    rt._register(author, AssemblyProposal(aid, "producer", "fake-haiku", "Reply with JSON.",
                                          ("Tick",), 128, "low"))


def _only(rt, monkeypatch, aid="helper-a", *, registered_by=None):
    """``aid`` is the one executor besides seed-decider for ProducerReturn."""
    if registered_by is None:
        rt._instantiate(replace(rt.assemblies["seed-decider"].spec, id=aid))
    else:
        _registered(rt, aid, registered_by)
    if "seed-observer" not in rt.retired_assemblies:
        rt._retire_assembly("seed-observer", "test")
    _answering(rt, monkeypatch)


def _child(rt, req, requester="seed-decider"):
    rt._invoke_child(requester, req, TASK, req.cost_ceiling)
    return [i for i in rt.ledger._recovery_items() if i["kind"] == "request.child"][-1]


def _verdicts(rt, about, *scores):
    rt.arrived_verdicts[about] = [[f"judge-{i}", s] for i, s in enumerate(scores)]
    rt._settle_arrived_verdicts()
    rt._settle_composed()


def test_credit_reaches_the_executors_handle_only_after_its_requester_settles(monkeypatch):
    rt = make_runtime()
    _only(rt, monkeypatch)
    req = _decision(rt, "seed-decider")
    child = _child(rt, req)["handle"]
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
    item = rt.outcomes.get("helper-a", child, delivered=False)
    assert "composed_settled" in json.dumps(item) and req.handle not in json.dumps(item)
    # The request router that drew it learns from it.
    rt._deliver_returns()
    state = rt.routers[request_router_key("ProducerReturn")][0]
    assert state.observed.sums["helper-a"] == [pytest.approx(0.6), 1]
    assert state.definitions == {DEF_COMPOSED: 1}


def test_a_requester_that_settles_unscored_leaves_the_child_its_own_verdict(monkeypatch):
    rt = make_runtime()
    _only(rt, monkeypatch)
    req = _decision(rt, "seed-decider")
    child = _child(rt, req)["handle"]
    _verdicts(rt, child, 0.7)
    del rt.pending[req.handle]
    rt.queue.settle(req.handle, channel=CH_VERDICT, score=0.0, status=SettleStatus.CENSORED,
                    definition_version="censored-v1", sampling_ref=None)
    rt._settle_composed()
    (settled,) = rt.queue.history(child)
    assert (settled.definition_version, settled.score) == (DEF_VERDICT, pytest.approx(0.7))


@pytest.mark.parametrize("definition,credited", [
    (DEF_VERDICT, True), (DEF_COMPOSED, True),
    (DEF_EXPOSURE, False), ("evaluation-v1", False), ("forecast-mean-v1", False)])
def test_credit_flows_only_from_a_requester_settled_on_a_producer_verdict(
        monkeypatch, definition, credited):
    """Review item 2: an exposure score or an evaluation reward is not a verdict on
    whether composed work paid, so it never reaches a child as composed credit."""
    rt = make_runtime()
    _only(rt, monkeypatch)
    req = _decision(rt, "seed-decider")
    child = _child(rt, req)["handle"]
    _verdicts(rt, child, 0.7)
    del rt.pending[req.handle]
    rt._settle_priced(req.handle, channel=CH_VERDICT, score=0.1,
                      definition_version=definition, sampling_ref=None, cards="producer")
    rt._settle_composed()
    (settled,) = rt.queue.history(child)
    if credited:
        assert settled.definition_version == DEF_COMPOSED
        assert settled.score == pytest.approx(composed_reward(0.7, 0.1))
    else:
        assert (settled.definition_version, settled.score) == (DEF_VERDICT, pytest.approx(0.7))


def test_an_unjudged_child_settles_on_its_requesters_credit_alone(monkeypatch):
    rt = make_runtime()
    _only(rt, monkeypatch)
    req = _decision(rt, "seed-decider")
    child = _child(rt, req)["handle"]
    _verdicts(rt, req.handle, 0.9)
    assert rt.queue.get(child).status is SettleStatus.PENDING  # its judges may still come
    rt.ticks_consumed += rt.ev.verdict_timeout_ticks + 1
    rt._censor_stale_judgements()  # a held child is not stale: it is still owed a credit
    rt._settle_composed()
    (settled,) = rt.queue.history(child)
    assert (settled.definition_version, settled.score) == (DEF_COMPOSED, pytest.approx(0.9))


def test_a_child_of_the_requesters_own_lineage_earns_nothing_extra(monkeypatch):
    rt = make_runtime()
    _only(rt, monkeypatch, registered_by="seed-decider")
    assert rt.budget.lineage("helper-a") == rt.budget.lineage("seed-decider")
    req = _decision(rt, "seed-decider")
    child = _child(rt, req)["handle"]
    assert rt.pending[child].requester is None
    withheld = [i for i in rt.ledger._recovery_items() if i["kind"] == "credit.withheld"]
    assert [i["handle"] for i in withheld] == [child]
    _verdicts(rt, child, 0.8)  # it settles at once, on its own verdict
    assert rt.queue.history(child)[0].definition_version == DEF_VERDICT
    _verdicts(rt, req.handle, 0.1)
    assert len(rt.queue.history(child)) == 1


def test_self_dealing_through_an_intermediary_earns_nothing(monkeypatch):
    """Review item 4, A -> B -> A2: A requests B's work, and B, as a child, requests
    work that A's own lineage executes. A2 is not credited from B's settlement."""
    rt = make_runtime()
    rt._instantiate(replace(rt.assemblies["seed-decider"].spec, id="helper-b",
                            emits=("Relay",), schemas={"Relay": {"type": "object"}}))
    _registered(rt, "helper-a2", "seed-decider")  # A's lineage, emits ProducerReturn
    rt._retire_assembly("seed-observer", "test")
    _answering(rt, monkeypatch)
    root = _decision(rt, "seed-decider")                               # A
    middle = _decision(rt, "helper-b", parent=root.handle)             # B, A's child
    grandchild = _child(rt, middle, requester="helper-b")              # A2, B's child
    # A itself or A2: either way the executor is of the root requester's lineage.
    assert grandchild["target"] in ("seed-decider", "helper-a2")
    assert rt.budget.lineage(grandchild["target"]) == rt.budget.lineage("seed-decider")
    assert rt.budget.lineage("helper-b") != rt.budget.lineage("seed-decider")
    assert rt.pending[grandchild["handle"]].requester is None
    (withheld,) = [i for i in rt.ledger._recovery_items() if i["kind"] == "credit.withheld"]
    assert withheld["handle"] == grandchild["handle"]


def test_a_held_child_and_its_credit_survive_a_checkpoint(monkeypatch):
    rt = make_runtime()
    _only(rt, monkeypatch)
    req = _decision(rt, "seed-decider")
    child = _child(rt, req)["handle"]
    _verdicts(rt, child, 0.8)
    monkeypatch.undo()
    rt.provider.target.__dict__.pop("complete", None)
    restored = _runtime()
    restored._instantiate(replace(restored.assemblies["seed-decider"].spec, id="helper-a"))
    restore_runtime(restored, runtime_state(rt))
    assert restored.pending[child].requester == req.handle
    _verdicts(restored, req.handle, 0.4)
    assert restored.queue.history(child)[0].score == pytest.approx(0.6)


# --- the adversary cannot be hired (review item 1) ----------------------------------


def test_no_request_can_draw_the_antagonist(monkeypatch):
    rt = make_runtime()
    _answering(rt, monkeypatch)
    assert "antagonist-a" not in rt._request_universe("Exposure")
    assert "antagonist-a" not in rt._request_universe("Tick")
    assert "antagonist-a" not in rt._request_universe("MarketMid")
    for kind in ("Exposure", "Tick", "MarketMid", "ProducerReturn"):
        req = _decision(rt, "seed-observer")
        rt._invoke_child("seed-observer", req, replace(TASK, target=kind), req.cost_ceiling)
    targets = {i["target"] for i in rt.ledger._recovery_items() if i["kind"] == "request.child"}
    assert "antagonist-a" not in targets
    refused = [i["reason"] for i in rt.ledger._recovery_items()
               if i["kind"] == "requests.refused"]
    assert any("adversarial contract cannot be commissioned" in r for r in refused)
    # Nor can the antagonist commission itself.
    own = _decision(rt, "antagonist-a", channel=CH_EXPOSURE)
    result, cost = rt._invoke_child("antagonist-a", own, replace(TASK, target="self"), 10**9)
    assert cost == 0 and "adversarial" in result["result"]["error"]


def test_no_requested_child_places_an_order_through_an_exposure_answer(monkeypatch):
    """Defence in depth: even a child whose return is an Exposure never trades by it."""
    rt = make_runtime()
    monkeypatch.setattr(rt.provider.target, "complete", lambda request: ModelResponse(
        request.model_id, json.dumps({"action": "order", "coin": "BTC", "side": "buy",
                                      "size": "0.001"}), 1, 1, "stop"))
    monkeypatch.setattr(rt, "_request_universe", lambda kind: ["antagonist-a"])
    req = _decision(rt, "seed-decider")
    rt._invoke_child("seed-decider", req, replace(TASK, target="Exposure"), req.cost_ceiling)
    assert not [i for i in rt.ledger._recovery_items() if i["kind"] == "order.intent"]
    assert not rt.order_intents


def test_the_request_draw_caps_adversarial_mass():
    rt = make_runtime()
    dist = {"antagonist-a": 0.9, "seed-observer": 0.1, "NOOP": 0.0}
    capped = rt._cap_adversarial(dist)
    assert capped["antagonist-a"] <= rt.ev.adversarial_share + 1e-12


# --- population tools: the same primitive, credited to the tool's builder -----------


def _tool_world(monkeypatch, builder="seed-observer"):
    rt = make_runtime()
    rt.tool_jail_available = True
    author = _decision(rt, builder)
    rt._register(author.handle, ToolProposal("doubler", "doubles x",
                                             {"type": "object", "properties": {}}, "", 1))
    monkeypatch.setattr(rt.tool_runner, "run", lambda tool, args: {"y": 2})
    return rt, author.handle


def _call(rt, seat, handle):
    return rt._run_tool(seat, handle, {"tool": "doubler", "args": {}})[0]


def _close_window(rt, author):
    rt.ticks_consumed = rt.tool_holds[author]["until"]
    rt._settle_composed()


def test_the_builders_registering_decision_settles_once_on_its_verdict_and_its_use(
        monkeypatch):
    """Review item 5: the builder is credited through its own one settlement, which
    trains the router that woke it."""
    rt, author = _tool_world(monkeypatch)
    assert author in rt.tool_holds
    _verdicts(rt, author, 0.8)
    assert rt.queue.get(author).status is SettleStatus.PENDING  # held for its window
    caller = _decision(rt, "seed-decider").handle
    assert _call(rt, "seed-decider", caller) == {"y": 2}
    assert _call(rt, "seed-decider", caller) == {"y": 2}
    _verdicts(rt, caller, 0.4)
    assert rt.tool_holds[author]["scores"] == [0.4]  # one decision, one score
    _close_window(rt, author)
    (settled,) = rt.queue.history(author)
    assert settled.definition_version == DEF_COMPOSED
    assert settled.score == pytest.approx(composed_reward(0.8, None, 0.4))
    assert author not in rt.tool_holds
    # A later use settles nothing a second time.
    late = _decision(rt, "seed-decider").handle
    _call(rt, "seed-decider", late)
    _verdicts(rt, late, 0.9)
    assert len(rt.queue.history(author)) == 1
    applied = [i["applied"] for i in rt.ledger._recovery_items() if i["kind"] == "credit.tool"]
    assert applied == ["hold", "late"]
    # The router that woke the builder learns from that one settlement.
    lid = rt.queue.get(author).actor
    assert [r.definition_version for r in rt.queue.returns_for(lid)] == [DEF_COMPOSED]


def test_the_hold_is_bounded_by_its_window_and_its_deadline(monkeypatch):
    rt, author = _tool_world(monkeypatch)
    hold = rt.tool_holds[author]
    assert hold["until"] == (rt.ticks_consumed + rt.ev.verdict_timeout_ticks
                             + rt.ev.consequence_backstop_ticks)
    _verdicts(rt, author, 0.8)
    rt.ticks_consumed = hold["until"] - 1
    rt._settle_composed()
    assert rt.queue.get(author).status is SettleStatus.PENDING
    rt.ticks_consumed = hold["until"]
    rt._settle_composed()
    (settled,) = rt.queue.history(author)
    assert (settled.definition_version, settled.score) == (DEF_VERDICT, pytest.approx(0.8))
    # A decision whose tick cutoff comes first settles on the last tick before it.
    rt2, author2 = _tool_world(monkeypatch)
    _verdicts(rt2, author2, 0.6)
    rt2.decision_ticks[author2][1] = rt2.ticks_consumed + 3  # sooner than the window
    rt2.ticks_consumed += 1
    rt2._settle_composed()
    assert rt2.queue.get(author2).status is SettleStatus.PENDING
    rt2.ticks_consumed += 1
    rt2._settle_composed()
    assert rt2.queue.history(author2)[0].score == pytest.approx(0.6)


def test_a_seat_calling_its_own_lineages_tool_earns_nothing(monkeypatch):
    rt, author = _tool_world(monkeypatch)
    _verdicts(rt, author, 0.8)
    caller = _decision(rt, "seed-observer").handle
    _call(rt, "seed-observer", caller)
    assert rt.tool_uses == {}
    _verdicts(rt, caller, 0.9)
    _close_window(rt, author)
    assert rt.queue.history(author)[0].definition_version == DEF_VERDICT


def test_a_tool_reached_through_an_intermediary_earns_its_builder_nothing(monkeypatch):
    """Review item 4: the builder's lineage requests B, and B calls the builder's tool."""
    rt, _author = _tool_world(monkeypatch)
    root = _decision(rt, "seed-observer")                        # the builder's lineage
    middle = _decision(rt, "seed-decider", parent=root.handle)   # another lineage's child
    _call(rt, "seed-decider", middle.handle)
    assert rt.tool_uses == {}
    calls = [i for i in rt.ledger._recovery_items() if i["kind"] == "tool.population_call"]
    assert [i["across_lineage"] for i in calls] == [False]


def test_a_call_whose_decision_closes_unscored_credits_nobody(monkeypatch):
    rt, author = _tool_world(monkeypatch)
    caller = _decision(rt, "seed-decider", judged=False).handle
    _call(rt, "seed-decider", caller)
    rt.queue.settle(caller, channel=CH_VERDICT, score=0.0, status=SettleStatus.CENSORED,
                    definition_version="censored-v1", sampling_ref=None)
    rt._settle_composed()
    assert rt.tool_uses == {} and rt.tool_holds[author]["scores"] == []
    assert not [i for i in rt.ledger._recovery_items() if i["kind"] == "credit.tool"]


def test_a_failed_tool_call_is_not_a_use(monkeypatch):
    rt, _author = _tool_world(monkeypatch)
    monkeypatch.setattr(rt.tool_runner, "run", lambda tool, args: {"error": "timeout"})
    caller = _decision(rt, "seed-decider").handle
    _call(rt, "seed-decider", caller)
    assert rt.tool_uses == {}


def test_a_committee_ballot_that_used_a_tool_credits_its_builder_when_scored(monkeypatch):
    """Codex review P2: ``_settle_policy`` settles a ballot straight through the queue;
    the one settlement hook still credits the tool its voter used."""
    from factorylab.kernel.queue import SettleStatus as S

    rt, author = _tool_world(monkeypatch)
    _verdicts(rt, author, 0.8)
    ballot = _decision(rt, "seed-decider", channel="policy", judged=False).handle
    _call(rt, "seed-decider", ballot)
    assert rt.tool_uses == {ballot: {"doubler": 1}}
    rt._settle_policy(ballot, 1.0, S.SETTLED)
    assert rt.tool_holds[author]["scores"] == [1.0] and rt.tool_uses == {}
    _close_window(rt, author)
    assert rt.queue.history(author)[0].score == pytest.approx(composed_reward(0.8, None, 1.0))


def test_a_forecast_score_is_on_another_scale_and_credits_no_tool(monkeypatch):
    rt, author = _tool_world(monkeypatch)
    caller = _decision(rt, "seed-decider", judged=False).handle
    _call(rt, "seed-decider", caller)
    rt.queue.settle(caller, channel=CH_VERDICT, score=0.9, status=SettleStatus.SETTLED,
                    definition_version="forecast-mean-v1", sampling_ref=None)
    assert rt.tool_uses == {} and rt.tool_holds[author]["scores"] == []


def test_a_hold_survives_a_checkpoint_mid_window(monkeypatch):
    rt, author = _tool_world(monkeypatch)
    _verdicts(rt, author, 0.8)
    caller = _decision(rt, "seed-decider").handle
    _call(rt, "seed-decider", caller)
    _verdicts(rt, caller, 0.4)
    monkeypatch.undo()
    restored = _runtime()
    restore_runtime(restored, runtime_state(rt))
    assert restored.tool_holds[author]["scores"] == [0.4]
    assert restored.pending[author].verdicts == [["judge-0", 0.8]]
    _close_window(restored, author)
    (settled,) = restored.queue.history(author)
    assert settled.score == pytest.approx(0.6)
