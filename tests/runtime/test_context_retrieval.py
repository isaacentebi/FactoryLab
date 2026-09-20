"""Retrieval closes a real decision without losing evidence or spending twice."""

import json
from dataclasses import replace

import pytest
from test_discovery_continuation import request, rows, runtime, scripted

from factorylab.cortex.request import Return
from factorylab.runtime.compute import _compacted_result


def test_discover_page_read_and_act_in_one_budget(monkeypatch):
    rt = runtime()
    req = request(rt)
    seat = "seed-decider"
    for i in range(12):
        rt.outcomes.append(seat, handle=f"old-{i}", outcome={"fact": f"exact-{i}"})
    prompts = []
    scripted(rt, monkeypatch, [
        {"tool_calls": [{"tool": "world.read", "args": {"section": "composition"}}]},
        {"tool_calls": [{"tool": "outcome.list", "args": {"after": 8}}]},
        {"tool_calls": [{"tool": "outcome.get", "args": {"outcome_id": "outcome:12"}}]},
        {"tool_calls": [{"tool": "note.put", "args": {"key": "evidence", "text": "exact-11"}}]},
        {"action": "hold", "rationale": "Saved the retrieved fact."},
    ], prompts)
    before = rt.wallet.balance
    ret = rt._invoke(seat, req, "producer")
    assert ret.status == "ok" and len(prompts) == 5
    assert [r["tool"] for r in rows(rt, "tool.call")] == [
        "world.read", "outcome.list", "outcome.get", "note.put"]
    assert all(r["ok"] for r in rows(rt, "tool.call"))
    assert "exact-11" in prompts[3]
    assert ret.cost == before - rt.wallet.balance
    assert 0 <= ret.cost <= req.cost_ceiling
    assert rt.outcomes.cursors.get(seat, 0) == 0


def test_old_read_recovers_original_bytes_after_source_changes():
    rt = runtime()
    old = {"tool": "venue.positions", "args": {"context": "x" * 700},
           "result": {"positions": [{"size": "1"}]}}
    exact = json.loads(json.dumps(old))
    retrieved = {}
    before = len(rt.artifacts.index)
    ref = _compacted_result(old, retrieved)
    old["result"]["positions"][0]["size"] = "2"
    assert json.loads(retrieved[ref["sha"]]) == exact
    assert len(rt.artifacts.index) == before
    assert "error" in rt.artifacts.read(ref["sha"], reader="seed-decider")
    assert "error" in rt.artifacts.read(ref["sha"], reader="other-seat")


@pytest.mark.parametrize("size", [100, 70_000])
def test_large_and_external_results_remain_transient_and_exact(size):
    entry = {"tool": "web.search", "args": {}, "result": "x" * size}
    retrieved = {}
    ref = _compacted_result(entry, retrieved)
    assert json.loads(retrieved[ref["sha"]]) == entry
    assert ref["expires"] == "end of this decision"


def test_unpriced_retrieval_finishes_instead_of_buying_another_round(monkeypatch):
    rt = runtime()
    req = request(rt)
    monkeypatch.setattr(rt, "_call_reserve", lambda *_: None)
    prompts = []
    scripted(rt, monkeypatch, [
        {"tool_calls": [{"tool": "world.read", "args": {"section": "composition"}}]},
        {"action": "hold"},
    ], prompts)
    ret = rt._invoke("seed-decider", req, "producer")
    assert ret.status == "ok" and len(prompts) == 2
    assert "Return the final answer" in prompts[1]
    assert rows(rt, "tool.rounds_exhausted")[0]["priced"] is False


def test_large_unaffordable_result_preserves_answer_without_claiming_delivery(monkeypatch):
    rt = runtime()
    seat = "seed-decider"
    rt.outcomes.append(seat, handle="large", outcome={"evidence": "x" * 250_000})
    base = request(rt)
    reserve = rt._call_reserve(rt.assemblies[seat], base)
    req = replace(base, cost_ceiling=3 * reserve)
    prompts = []
    scripted(rt, monkeypatch, [
        {"tool_calls": [{"tool": "outcome.get", "args": {"outcome_id": "outcome:1"}}]},
        {"action": "hold", "ack_through": "outcome:1"},
    ], prompts)
    ret = rt._invoke(seat, req, "producer")
    assert ret.status == "ok" and ret.cost <= req.cost_ceiling
    assert "Tool bodies were not loaded" in prompts[1]
    assert "x" * 1000 not in prompts[1]
    assert rt.outcomes.cursors.get(seat, 0) == 0


def test_program_cannot_run_tools_with_its_last_answer_budget(monkeypatch):
    from tests.cortex.test_programs import program

    rt = runtime()
    req = request(rt)
    asm, _, _ = program()
    rt.assemblies["seed-decider"] = asm
    monkeypatch.setattr(rt, "_invoke_compute", lambda *_: Return(
        req.handle, {}, asm.price, "ok", tool_calls=(
            {"tool": "outcome.list", "args": {}},)))
    ret = rt._invoke("seed-decider", replace(req, cost_ceiling=2 * asm.price - 1), "producer")
    assert ret.status == "failed" and ret.cost == asm.price
    assert not rows(rt, "tool.call")
    assert rows(rt, "tool.rounds_exhausted")[0]["reserve"] == asm.price
