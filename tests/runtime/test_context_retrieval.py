"""Retrieval closes a real decision without losing evidence or spending twice."""

import json
from dataclasses import replace

import pytest
from test_discovery_continuation import packed, request, rows, runtime, scripted

from factorylab.cortex.request import Return
from factorylab.runtime.compute import _bounded_result_history, _compacted_result
from factorylab.runtime.continuity import HARD_STATE_BYTES


def test_invalid_read_is_answered_in_its_slot_without_dispatch(monkeypatch):
    """A read-only batch keeps its turn: the bad read is refused, not dispatched,
    and its error answers in its own tool_results slot (PR121 seq 11547)."""
    rt = runtime()
    req = request(rt)
    prompts = []
    scripted(rt, monkeypatch, [
        {"tool_calls": [{"tool": "venue.order_book", "args": {"coin": "BTC"}}]},
        {"action": "hold", "rationale": "the book read was malformed; holding"},
    ], prompts)
    ret = rt._invoke("seed-decider", req, "producer")
    assert ret.status == "ok" and len(prompts) == 2
    assert not rows(rt, "tool.call")  # never dispatched
    assert "depth" in prompts[1] and '"tool":"venue.order_book"' in prompts[1]
    fault = rows(rt, "return.sections_dropped") or rows(rt, "return.validation_failed")
    assert fault and "depth" in json.dumps(fault[-1])


@pytest.mark.parametrize("calls,reason", [
    # Over the limit in a batch that writes: whole or not at all. (A read-only
    # batch over the limit is answered in its slots instead.)
    ([{"tool": "venue.cancel", "args": {"coin": "BTC", "order_id": "1"}}] * 5,
     "more than 4 tool_calls"),
])
def test_invalid_tool_only_reply_reaches_own_inbox_without_dispatch(monkeypatch, calls, reason):
    rt = runtime()
    req = request(rt)
    prompts = []
    scripted(rt, monkeypatch, [{"tool_calls": calls}], prompts)
    before = rt.wallet.balance
    ret = rt._invoke("seed-decider", req, "producer")

    assert ret.status == "malformed" and len(prompts) == 1
    assert ret.cost == before - rt.wallet.balance and ret.cost > 0
    assert not ret.tool_calls and not ret.children and not rows(rt, "tool.call")
    fault = rows(rt, "return.validation_failed")[-1]
    assert reason in fault["dropped"][0]["reason"]
    body = rt.outcomes.get("seed-decider", req.handle)
    assert body["outcome"]["status"] == "malformed"
    assert body["outcome"]["dropped"] == fault["dropped"]
    assert "raw" not in body["outcome"]
    assert "error" in rt.outcomes.get("other-seat", body["outcome_id"])


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
        {"action": "hold", "rationale": "Read the retrieved fact."},
    ], prompts)
    before = rt.wallet.balance
    ret = rt._invoke(seat, req, "producer")
    assert ret.status == "ok" and len(prompts) == 4
    assert [r["tool"] for r in rows(rt, "tool.call")] == [
        "world.read", "outcome.list", "outcome.get"]
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


def test_late_public_history_is_exactly_addressable_while_current_facts_stay_inline():
    rt = runtime()
    now_s = rt.clock.now_ns // 1_000_000_000
    rt.recent_mids["BTC"] = [
        {"t_s": now_s - (40 - index), "mid": f"{60_000 + index}.125"}
        for index in range(40)
    ]
    original = request(rt)
    original = replace(original, inputs={**original.inputs,
                                        "frozen_evidence": {"ref": "frozen-proof"}})
    retrieved = {}

    lean = rt._compact_invocation_context(original, retrieved)
    before = original.world_update_block()
    after = lean.world_update_block()

    assert len(lean.prompt_text()) < len(original.prompt_text())
    assert after["charter"]["edition"] == before["charter"]["edition"]
    assert after["charter"]["cards"] == before["charter"]["cards"]
    assert after["charter"]["pending_changes"] == before["charter"]["pending_changes"]
    assert after["observation_window"] == before["observation_window"]
    assert after["public_observations"]["last_closed_window_values"] == (
        before["public_observations"]["last_closed_window_values"]
    )
    # U4: pathology labels are for observers and the wake, never a seat's prompt.
    assert "pathologies" not in before["public_observations"]
    assert "pathologies" not in after["public_observations"]
    assert after["public_observations"]["recent_mids"] == {
        "BTC": [before["public_observations"]["recent_mids"]["BTC"][-1]]
    }
    assert lean.inputs["frozen_evidence"] == {"ref": "frozen-proof"}

    charter_ref = after["charter"]["text"]
    history_ref = after["public_observations"]["full_history"]
    assert json.loads(retrieved[charter_ref["sha"]]) == {
        "section": "world_update.charter.text", "value": before["charter"]["text"]
    }
    assert json.loads(retrieved[history_ref["sha"]]) == {
        "section": "world_update.public_observations",
        "value": before["public_observations"],
    }
    assert charter_ref["read_with"] == {
        "tool": "artifact.get", "args": {"sha": charter_ref["sha"]}
    }
    assert history_ref["read_with"] == {
        "tool": "artifact.get", "args": {"sha": history_ref["sha"]}
    }
    assert "retrieved_earlier" not in charter_ref and "retrieved_earlier" not in history_ref


def test_compaction_preserves_an_unknown_mid_history_value():
    rt = runtime()
    original = request(rt)
    inputs = json.loads(json.dumps(original.inputs))
    inputs["world"]["world_update"]["public_observations"]["recent_mids"] = {
        "BTC": {"status": "unavailable", "reason": "malformed upstream history"},
        "ETH": [{"t_s": 1, "mid": "1"}, {"t_s": 2, "mid": "2"}],
    }
    original = replace(original, inputs=inputs)

    lean = rt._compact_invocation_context(original, {})

    assert lean.world_update_block()["public_observations"]["recent_mids"] == {
        "BTC": {"status": "unavailable", "reason": "malformed upstream history"},
        "ETH": [{"t_s": 2, "mid": "2"}],
    }


def test_zero_tool_call_world_keeps_charter_and_history_inline():
    rt = runtime()
    rt.m = replace(rt.m, tools=replace(rt.m.tools, max_tool_calls=0))
    now_s = rt.clock.now_ns // 1_000_000_000
    rt.recent_mids["BTC"] = [
        {"t_s": now_s - 1, "mid": "90000.0"},
        {"t_s": now_s, "mid": "90001.0"},
    ]
    original = request(rt)
    retrieved = {}

    kept = rt._compact_invocation_context(original, retrieved)

    before = original.world_update_block()
    after = kept.world_update_block()
    assert kept is original and retrieved == {}
    assert isinstance(after["charter"]["text"], str)
    assert after["charter"]["text"] == before["charter"]["text"]
    assert after["public_observations"]["recent_mids"] == (
        before["public_observations"]["recent_mids"]
    )
    assert "full_history" not in after["public_observations"]


def test_program_request_is_not_replaced_with_transient_references(monkeypatch):
    from tests.cortex.test_programs import program

    rt = runtime()
    req = request(rt)
    asm, _, _ = program()
    rt.assemblies["seed-decider"] = asm
    monkeypatch.setattr(rt, "_compact_invocation_context",
                        lambda *_: pytest.fail("program context was compacted"))
    monkeypatch.setattr(rt, "_invoke_compute",
                        lambda *_, **__: Return(req.handle, {"action": "hold"}, 0, "ok"))

    ret = rt._invoke("seed-decider", req, "producer")

    assert ret.status == "ok"


def test_routing_growth_uses_the_same_compact_world_before_and_after_invocation(monkeypatch):
    rt = runtime()
    now_s = rt.clock.now_ns // 1_000_000_000
    rt.recent_mids["BTC"] = [
        {"t_s": now_s - (180 - index), "mid": f"{80_000 + index}.500"}
        for index in range(180)
    ]
    req = request(rt)
    raw_world_chars = len(json.dumps(req.inputs["world"], sort_keys=True, indent=2))
    lean = rt._compact_invocation_context(req, {})
    compact_world_chars = rt._world_chars(req.inputs["world"])
    assert compact_world_chars == rt._world_chars(lean.inputs["world"])
    assert compact_world_chars < raw_world_chars
    prompts = []
    scripted(rt, monkeypatch, [
        {"action": "hold", "rationale": "No action from unchanged history."},
    ], prompts)

    ret = rt._invoke("seed-decider", req, "producer")

    assert ret.status == "ok" and len(prompts) == 1
    record = rt.seat_ceilings["seed-decider"]
    assert record["world_chars"] == compact_world_chars
    # Hold the world at the same projected representation. Removed history
    # is not growth, so routing needs exactly the last model ceiling.
    rt._world_chars_cache = (rt.n, compact_world_chars)
    assert rt._seat_need("seed-decider") == record["ceiling"]

    # More prior rows change the referenced bytes and hash, whose rendered widths
    # are stable; they do not become inline growth on the next routed call.
    base_current_chars = rt._world_chars(rt._world_block())
    current = rt._world_block()
    current["world_update"]["public_observations"]["recent_mids"]["BTC"] = [
        *rt.recent_mids["BTC"][:-1], *rt.recent_mids["BTC"][:-1], rt.recent_mids["BTC"][-1]
    ]
    future_chars = rt._world_chars(current)
    projected_growth = future_chars - base_current_chars
    assert 0 <= projected_growth <= 2  # only the reference byte-count width may grow
    record["world_chars"] = base_current_chars
    rt._world_chars_cache = (rt.n, future_chars)
    model = rt.assemblies["seed-decider"]
    price = rt.prices.price(model.spec.model_id)
    expected = (record["ceiling"]
                + price.cost(int(projected_growth * model.model.input_slack), 0)
                - price.cost(0, 0))
    assert rt._seat_need("seed-decider") == expected


def test_first_call_can_read_transient_world_history_through_its_own_handle(monkeypatch):
    rt = runtime()
    now_s = rt.clock.now_ns // 1_000_000_000
    rt.recent_mids["BTC"] = [
        {"t_s": now_s - (24 - index), "mid": f"{70_000 + index}.250"}
        for index in range(24)
    ]
    req = request(rt)
    preview_retrieved = {}
    preview = rt._compact_invocation_context(req, preview_retrieved)
    update = preview.world_update_block()
    refs = [update["charter"]["text"],
            update["public_observations"]["full_history"]]
    calls = [ref["read_with"] for ref in refs]
    before_artifacts = len(rt.artifacts.index)
    prompts = []
    scripted(rt, monkeypatch, [
        {"tool_calls": calls},
        {"action": "hold", "rationale": "The exact history was read."},
    ], prompts)

    ret = rt._invoke("seed-decider", req, "producer")

    assert ret.status == "ok" and len(prompts) == 2
    assert "artifact.get" in prompts[0] and '"sha"' in prompts[0]
    oldest = json.dumps(rt.recent_mids["BTC"][0], sort_keys=True)
    latest = json.dumps(rt.recent_mids["BTC"][-1], sort_keys=True)
    assert packed(oldest) not in packed(prompts[0])
    assert packed(latest) in packed(prompts[0])
    assert update["charter"]["text"]["sha"] in prompts[0]
    input_at = prompts[1].index("INPUTS\n") + len("INPUTS\n")
    continued, _ = json.JSONDecoder().raw_decode(prompts[1][input_at:])
    bodies = [json.loads(row["result"]["text"]) for row in continued["tool_results"]]
    assert bodies == [
        {"section": "world_update.charter.text",
         "value": req.world_update_block()["charter"]["text"]},
        {"section": "world_update.public_observations",
         "value": req.world_update_block()["public_observations"]},
    ]
    assert len(rt.artifacts.index) == before_artifacts
    reads = [row for row in rows(rt, "artifact.get") if row.get("scope") == "invocation"]
    assert [row["sha"] for row in reads] == [ref["sha"] for ref in refs]


def test_recent_results_carry_four_plus_two_facts_without_working_state(monkeypatch):
    rt = runtime()
    req = request(rt)
    facts = [f"unknown-fact-{letter}" for letter in "abcdef"]
    for index, fact in enumerate(facts):
        rt.outcomes.append("seed-decider", handle=f"fact-{index}",
                           outcome={"unknown": fact})
    prompts = []
    scripted(rt, monkeypatch, [
        {"tool_calls": [
            {"tool": "outcome.get", "args": {"outcome_id": f"outcome:{index}"}}
            for index in range(1, 5)
        ]},
        {"tool_calls": [
            {"tool": "outcome.get", "args": {"outcome_id": f"outcome:{index}"}}
            for index in range(5, 7)
        ]},
        {"action": "hold", "rationale": "All six retained facts were considered."},
    ], prompts)
    before = rt.wallet.balance

    ret = rt._invoke("seed-decider", req, "producer")

    assert ret.status == "ok" and len(prompts) == 3
    assert all(fact in prompts[1] for fact in facts[:4])
    assert all(fact not in prompts[1] for fact in facts[4:])
    assert all(prompts[2].count(fact) == 1 for fact in facts)
    assert rt.working_state.head("seed-decider") is None
    assert rows(rt, "state.put") == []
    assert ret.cost == before - rt.wallet.balance and ret.cost > 0


def test_large_prior_result_history_is_bounded_and_exactly_retrievable():
    entries = [
        {"tool": "outcome.get", "args": {"outcome_id": f"outcome:{index}"},
         "result": {"unknown": f"fact-{index}", "padding": "x" * 3000}}
        for index in range(12)
    ]
    retrieved = {}

    carried, references = _bounded_result_history(
        entries, retrieved, byte_limit=16 * 1024)

    exact_bytes = sum(reference["bytes"]
                      for row, reference in zip(carried, references, strict=True)
                      if "result" in row)
    assert exact_bytes <= 16 * 1024
    assert 0 < sum("result" in row for row in carried) < len(entries)
    assert all("result" in row for row in carried[-5:])
    assert all("result" not in row for row in carried[:-5])
    assert [json.loads(retrieved[reference["sha"]]) for reference in references] == entries
    assert all(not ({"result", "sha"} <= row.keys()) for row in carried)


def test_oversize_intermediate_state_is_refused_without_changing_the_head(monkeypatch):
    rt = runtime()
    seat = "seed-decider"
    rt.working_state.put(seat, {"keep": "baseline"}, handle="before")
    before_head = dict(rt.working_state.head(seat))
    req = request(rt)
    req = replace(req, inputs={**req.inputs, "your_state": rt.working_state.render(seat)})
    prompts = []
    too_large = {"body": "Z" * HARD_STATE_BYTES}
    scripted(rt, monkeypatch, [
        {"working_state": too_large,
         "tool_calls": [{"tool": "world.read", "args": {"section": "composition"}}]},
        {"action": "hold", "rationale": "The prior state remains."},
    ], prompts)

    ret = rt._invoke(seat, req, "producer")

    assert ret.status == "ok" and len(prompts) == 2
    assert rt.working_state.head(seat) == before_head
    assert '"keep":"baseline"' in prompts[1]
    assert "Z" * 1000 not in prompts[1]
    refused = rows(rt, "state.refused")
    assert len(refused) == 1 and str(HARD_STATE_BYTES) in refused[0]["reason"]


def test_intermediate_state_commit_survives_a_later_final_failure(monkeypatch):
    rt = runtime()
    req = request(rt)
    prompts = []
    committed = {"finding": "kept even if the final answer fails"}
    scripted(rt, monkeypatch, [
        {"working_state": committed,
         "tool_calls": [{"tool": "world.read", "args": {"section": "composition"}}]},
        ["not", "a", "return object"],
    ], prompts)

    ret = rt._invoke("seed-decider", req, "producer")

    assert ret.status == "malformed" and len(prompts) == 2
    assert rt.working_state.render("seed-decider")["state"] == committed
    puts = rows(rt, "state.put")
    assert len(puts) == 1 and puts[0]["handle"] == req.handle


@pytest.mark.parametrize("state,accepted", [
    ({"after_action": "remembered"}, True),
    ({"body": "Z" * HARD_STATE_BYTES}, False),
])
def test_loop_ending_tool_return_handles_working_state_only_once(
        monkeypatch, state, accepted):
    rt = runtime()
    req = request(rt)
    prompts = []
    scripted(rt, monkeypatch, [
        {"tool_calls": [{"tool": "venue.set_leverage",
                         "args": {"coin": "ETH", "leverage": 1}}]},
        {"working_state": state,
         "tool_calls": [{"tool": "world.read", "args": {"section": "composition"}}]},
    ], prompts)

    ret = rt._invoke("seed-decider", req, "producer")

    assert ret.status == "ok" and len(prompts) == 2
    if accepted:
        assert rt.working_state.render("seed-decider")["state"] == state
        assert len(rows(rt, "state.put")) == 1
        assert rows(rt, "state.refused") == []
    else:
        assert rt.working_state.head("seed-decider") is None
        assert rows(rt, "state.put") == []
        assert len(rows(rt, "state.refused")) == 1


def test_connector_body_cannot_become_intermediate_working_state():
    rt = runtime()
    body = "outside private body that must remain transient"
    rt.ledger.protect_connector_body(body)

    accepted = rt._write_working_state(
        "seed-decider", "decision-private", {"working_state": {"memory": body}})

    assert accepted is False and rt.working_state.head("seed-decider") is None
    refused = rows(rt, "state.refused")
    assert refused[-1]["reason"] == "connector body in working_state"
    assert body not in json.dumps(refused[-1])


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


def test_unaffordable_recent_working_set_falls_back_to_exact_references(monkeypatch):
    rt = runtime()
    seat = "seed-decider"
    for index in range(2):
        rt.outcomes.append(seat, handle=f"fact-{index}",
                           outcome={"unknown": f"private-fact-{index}"})
    req = request(rt)
    quote = rt._call_reserve
    quoted_exact_history = []

    def price(assembly, current):
        seen = current.inputs.get("seen_tool_results", ())
        if any(isinstance(row, dict) and "result" in row for row in seen):
            quoted_exact_history.append(current)
            return current.cost_ceiling + 1
        return quote(assembly, current)

    monkeypatch.setattr(rt, "_call_reserve", price)
    prompts = []
    scripted(rt, monkeypatch, [
        {"tool_calls": [{"tool": "outcome.get",
                         "args": {"outcome_id": "outcome:1"}}]},
        {"tool_calls": [{"tool": "outcome.get",
                         "args": {"outcome_id": "outcome:2"}}]},
        {"action": "hold", "rationale": "The exact bodies did not fit."},
    ], prompts)

    ret = rt._invoke(seat, req, "producer")

    assert ret.status == "ok" and len(prompts) == 3
    assert len(quoted_exact_history) == 1
    assert "Tool bodies were not loaded" in prompts[2]
    assert all(f"private-fact-{index}" not in prompts[2] for index in range(2))
    continued = quoted_exact_history[0].inputs
    assert "result" in continued["seen_tool_results"][0]
    assert "result" in continued["tool_results"][0]
    assert prompts[2].count('"read_with"') >= 2


def test_a_program_s_tool_rounds_are_bounded_by_rounds_not_by_money(monkeypatch):
    """Wave 11: a program call costs nothing (its jail pays no one), so its next answer
    needs no reserve and a zero cost ceiling does not refuse its tool round; the
    kernel's round limit is what bounds it."""
    from tests.cortex.test_programs import program

    rt = runtime()
    req = request(rt)
    asm, _, _ = program()
    rt.assemblies["seed-decider"] = asm
    assert rt._call_reserve(asm, req) == 0
    monkeypatch.setattr(rt, "_invoke_compute", lambda *_, **__: Return(
        req.handle, {}, 0, "ok", tool_calls=(
            {"tool": "outcome.list", "args": {}},)))
    ret = rt._invoke("seed-decider", replace(req, cost_ceiling=0), "producer")
    assert ret.cost == 0
    assert rows(rt, "tool.call")
    assert all(row.get("reserve", 0) == 0 for row in rows(rt, "tool.rounds_exhausted"))


def test_routing_bridge_does_not_fund_the_seats_retrieval_chain(monkeypatch):
    rt = runtime()
    req = request(rt)
    seat = "seed-decider"
    monkeypatch.setattr(rt, "_novelty_protection", lambda *_: 0)
    quote = rt._call_reserve(rt.assemblies[seat], req)
    rt.budget.debit(seat, rt.budget.entitlement(seat) - quote // 2, "test")
    prompts = []
    scripted(rt, monkeypatch, [
        {"tool_calls": [{"tool": "world.read", "args": {"section": "composition"}}]},
        {"action": "hold"},
    ], prompts)
    ret = rt._invoke(seat, req, "producer")
    bridges = [r for r in rows(rt, "budget") if r.get("op") == "bridge"]
    assert len(bridges) == 1 and len(prompts) == 1
    assert ret.status == "failed" and not rows(rt, "tool.call")


def test_continuation_cannot_claim_a_routing_bridge_directly(monkeypatch):
    rt = runtime()
    req = request(rt)
    seat = "seed-decider"
    rt.handle_to_assembly[req.handle] = seat
    monkeypatch.setattr(rt, "_novelty_protection", lambda *_: 0)
    rt.budget.debit(seat, rt.budget.entitlement(seat) - 1, "test")
    ret = rt._invoke_compute(seat, req.continuation(
        inputs={**req.inputs, "continuation": "answer"}, cost_ceiling=req.cost_ceiling))
    assert ret.status == "failed" and ret.cost == 0
    assert not [r for r in rows(rt, "budget") if r.get("op") == "bridge"]
