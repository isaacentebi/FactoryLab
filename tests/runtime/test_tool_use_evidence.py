"""Only executed, attributable tool use reaches a grounded consequence contract."""

from dataclasses import replace

import pytest

from factorylab.cortex.registration import ToolProposal
from factorylab.cortex.tools import PopulationTool, as_spec
from factorylab.runtime.grounded import freeze_contract, public_evidence
from factorylab.runtime.shared import CH_VERDICT
from factorylab.runtime.worlds import load_manifest
from tests.runtime.test_loop import _consequence_decision, _consequence_runtime


def test_paid_tool_use_is_attributable_without_becoming_reward_or_public_data(monkeypatch):
    manifest = load_manifest("scripted")
    manifest = replace(manifest, evaluation=replace(manifest.evaluation,
                                                   producer_feedback="realized"))
    rt = _consequence_runtime(manifest=manifest)
    maker, caller = "seed-decider", "eval-a"
    creation = _consequence_decision(rt, maker, CH_VERDICT)
    invocation = _consequence_decision(rt, caller, CH_VERDICT)
    contract = freeze_contract(rt, creation, maker, {"action": "build"})
    tool = PopulationTool("made-here", "Compute a value", {"type": "object"},
                          "pass", 1, creation)
    rt.population_tools[tool.id] = tool
    rt.tool_owner[tool.id] = maker
    rt.tool_specs[tool.id] = as_spec(tool, 50)
    monkeypatch.setattr(rt.tool_runner.target, "run", lambda *_: {"secret_result": 42})
    before = rt.budget.entitlement(caller)
    output, cost = rt._run_tool(caller, invocation,
                                {"tool": tool.id, "args": {"private_argument": "hidden"}})
    assert output == {"secret_result": 42} and cost == 50
    assert rt.budget.entitlement(caller) == before - 50
    evidence = public_evidence(rt, contract)
    assert len(evidence) == 1
    facts = evidence[0]["payload"]["facts"]
    assert facts["caller_handle"] == invocation
    assert facts["maker_handle"] == creation
    assert facts["lineage_relation"] == "cross_lineage"
    assert facts["status"] == "executed"
    assert len(facts["version_sha256"]) == len(facts["result_sha256"]) == 64
    assert "hidden" not in str(evidence) and "secret_result" not in str(evidence)
    assert not rt.queue.history(creation)


def test_unaffordable_tool_use_creates_no_execution_evidence(monkeypatch):
    manifest = load_manifest("scripted")
    manifest = replace(manifest, evaluation=replace(manifest.evaluation,
                                                   producer_feedback="realized"))
    rt = _consequence_runtime(manifest=manifest)
    handle = _consequence_decision(rt, "seed-decider", CH_VERDICT)
    contract = freeze_contract(rt, handle, "seed-decider", {})
    tool = PopulationTool("unaffordable", "Compute", {"type": "object"}, "pass", 1, handle)
    rt.population_tools[tool.id] = tool
    rt.tool_owner[tool.id] = "seed-decider"
    rt.tool_specs[tool.id] = as_spec(tool, rt.wallet.available + 1)
    monkeypatch.setattr(rt.tool_runner.target, "run", lambda *_: {"value": 1})
    output, cost = rt._run_tool("seed-decider", handle, {"tool": tool.id, "args": {}})
    assert "error" in output and cost == 0
    assert public_evidence(rt, contract) == []


@pytest.mark.gate
def test_registered_tool_really_executes_in_jail_for_another_participant():
    manifest = load_manifest("scripted")
    manifest = replace(manifest, evaluation=replace(manifest.evaluation,
                                                   producer_feedback="realized"))
    rt = _consequence_runtime(manifest=manifest)
    if not rt.tool_jail_available:
        pytest.skip("the host cannot execute population code in an OS jail")
    rt._manage_reserve_window()
    maker = _consequence_decision(rt, "seed-decider", CH_VERDICT)
    rt.handle_to_assembly[maker] = "seed-decider"
    contract = freeze_contract(rt, maker, "seed-decider", {"action": "build"})
    rt._register(maker, ToolProposal(
        "made-doubler", "Double an integer", {
            "type": "object", "properties": {"n": {"type": "integer"}},
            "required": ["n"], "additionalProperties": False},
        'import json, sys\nx = json.load(sys.stdin)\nprint(json.dumps({"value": x["n"] * 2}))',
        2))
    caller = _consequence_decision(rt, "eval-a", CH_VERDICT)
    result, cost = rt._run_tool("eval-a", caller,
                               {"tool": "made-doubler", "args": {"n": 21}})
    assert result == {"value": 42}
    assert cost == manifest.tools.population_tool_micro_per_call
    rows = public_evidence(rt, contract)
    assert len(rows) == 1 and rows[0]["payload"]["facts"]["lineage_relation"] == "cross_lineage"
    assert not rt.queue.history(maker)
