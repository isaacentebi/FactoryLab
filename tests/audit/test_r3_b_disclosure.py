"""Round three, group B: the population can name assemblies, and a child's score trains a
router that exists (triage rows T7 and T23). Nothing here touches a network."""

import json

from factorylab.cortex.request import ChildRequest, Return
from factorylab.kernel.queue import PropensityRecord, SettleStatus
from factorylab.world.models import ModelResponse
from factorylab.world.scripted import _inputs_from_prompt
from tests.conftest import make_runtime
from tests.runtime.test_child_requests import parent_request
from tests.runtime.test_loop import _consequence_judge, _consequence_produce


def _items(rt, kind):
    return [i for i in rt.ledger._recovery_items() if i["kind"] == kind]


# --- T7: ids are public schematics; every request says who it is ------------------


def test_the_world_block_publishes_a_contract_catalogue_with_ids_and_nothing_sealed():
    rt = make_runtime()
    block = rt._world_block()
    catalogue = {row["id"]: row for row in block["catalogue"]}
    assert set(catalogue) == set(rt.assemblies)
    for aid, asm in rt.assemblies.items():
        row = catalogue[aid]
        assert set(row) == {"id", "version", "accepts", "emits"}
        assert row["version"] == asm.spec.version
        assert row["accepts"] == sorted(asm.spec.accepts)
        assert row["emits"] == list(asm.spec.emits)
    assert "catalogue" in block["addressing"] and "inputs.you" in block["addressing"]
    text = json.dumps(block)
    assert '"menu"' not in text and "PRIVATE" not in text
    for row in block["catalogue"]:
        assert "model" not in row and "prompt" not in json.dumps(row)
    assert "world.catalogue" in block["proposal_shapes"]["retire"]["assembly_id"]
    assert "catalogue" in block["a_return_may_include"]["requests"]
    assert "catalogue" in block["a_return_may_include"]["register"]


def test_a_retired_assembly_leaves_the_catalogue():
    rt = make_runtime()
    rt.retired_assemblies.add("eval-b")
    assert "eval-b" not in {row["id"] for row in rt._world_block()["catalogue"]}


def test_every_request_tells_its_executor_its_own_id(monkeypatch):
    rt = make_runtime()
    rt._manage_reserve_window()
    seen = []

    def complete(request):
        text = request.messages[-1]["content"]
        seen.append(_inputs_from_prompt(text))
        body = (
            {"verdict": 0.5, "payoff": 0.5, "rationale": "r", "forecasts": []}
            if "Evaluate" in text
            else {"action": "hold"}
        )
        return ModelResponse(request.model_id, json.dumps(body), 1, 1, "stop")

    monkeypatch.setattr(rt.provider.target, "complete", complete)
    _handle, event = _consequence_produce(rt, "seed-observer")
    assert seen[-1]["you"] == "seed-observer"
    _consequence_judge(rt, event, "eval-a")
    assert seen[-1]["you"] == "eval-a"
    # The judge saw the producer's return, never the producer's name (the catalogue in
    # the world block names every assembly; it maps none of them to this return).
    assert "you" not in seen[-1]["producer"]["inputs"]
    assert "seed-observer" not in json.dumps(seen[-1]["producer"])
    req = parent_request(rt)
    rt.handle_to_assembly[req.handle] = "seed-decider"
    rt._invoke_child(
        "seed-decider",
        req,
        ChildRequest("seed-observer", "task", {"you": "forged"}, {"type": "object"}),
        req.cost_ceiling,
    )
    assert seen[-1]["you"] == "seed-observer"  # a parent cannot forge its child's identity


def test_a_scripted_assembly_can_retire_a_seed_it_did_not_register(monkeypatch):
    rt = make_runtime()
    rt._manage_reserve_window()
    monkeypatch.setattr(
        rt,
        "_committee_eligible",
        lambda: {"seed-observer": "producer", "eval-b": "evaluator", "meta-a": "meta"},
    )
    req = parent_request(rt)  # a decision of seed-decider, which registered nothing
    rt.handle_to_assembly[req.handle] = "seed-decider"
    target = next(row["id"] for row in rt._world_block()["catalogue"] if row["id"] == "eval-a")
    rt._apply_registrations(
        req.handle,
        Return(
            req.handle,
            {
                "register": [
                    {
                        "kind": "retire",
                        "assembly_id": target,
                        "predicted_effect": {
                            "card_id": "forecast_skill",
                            "direction": "increase",
                            "window": 1,
                        },
                    }
                ]
            },
            0,
            "ok",
        ),
    )
    proposed = _items(rt, "retirement.proposed")
    assert proposed and proposed[-1]["assembly_id"] == "eval-a"
    assert not rt.registration_feedback


# --- T23: a child's score reaches the router that woke its parent ------------------


def test_a_childs_verdict_trains_the_router_that_woke_its_parent(monkeypatch):
    rt = make_runtime()
    state = rt.routers["Tick"][0]
    lid = state.learner.id
    assert "seed-decider" in state.universe
    parent = rt.queue.open(
        actor=lid,
        event_id="tick-1",
        channel="verdict",
        deadline_ns=10**15,
        parent_handle=None,
        cost_ceiling=10_000_000,
        propensity=PropensityRecord(("seed-decider",), (1.0,), "seed-decider", 0, lid, "t"),
    )
    rt.handle_to_assembly[parent] = "seed-decider"
    rt.consequences.start(parent, 0)
    req = rt._request(parent, "parent task", {}, {"type": "object"}, 10**15, "verdict")
    monkeypatch.setattr(
        rt.provider.target,
        "complete",
        lambda request: ModelResponse(
            request.model_id, json.dumps({"action": "hold"}), 1, 1, "stop"
        ),
    )
    before = state.learner.distribution(state.universe)["seed-decider"]
    rt._invoke_child(
        "seed-decider", req, ChildRequest("self", "task", {}, {"type": "object"}), req.cost_ceiling
    )
    child = _items(rt, "request.child")[-1]["handle"]
    assert rt.queue.get(child).actor == lid
    assert rt.queue.get(child).propensity.chosen == "seed-decider"
    rt.queue.settle(
        child,
        channel="verdict",
        score=1.0,
        status=SettleStatus.SETTLED,
        definition_version="test",
        sampling_ref=None,
    )
    rt._deliver_returns()
    assert state.learner.distribution(state.universe)["seed-decider"] > before
