"""One offline model-facing journey crosses the capability and feedback contracts.

The commissioned objective and actor selection are deterministic test scaffold.  Every
response is nevertheless derived from the prompt the runtime rendered: schemas come from
``catalogue.search``, the correction comes from the owning seat's rejection receipt, the
artifact is admitted through a producer return, and the final judge cites the execution
receipt it was supplied.  This evidences interface composition, not autonomous purpose,
usefulness, or real-model competence.
"""

from __future__ import annotations

import json
import re
import socket
from dataclasses import replace
from types import SimpleNamespace
from typing import Any

import pytest

from factorylab.kernel.events import Event, EventKind
from factorylab.kernel.queue import PropensityRecord, SettleStatus
from factorylab.runtime.loop import Runtime
from factorylab.runtime.shared import CH_CONFORMITY, CH_VERDICT
from factorylab.runtime.worlds import PromptSpec, load_manifest
from factorylab.world.events import WorldEvent, WorldEventKind
from factorylab.world.exchange import FakeExchange
from factorylab.world.models import ModelRequest, ModelResponse
from factorylab.world.scripted import _description_from_prompt, _inputs_from_prompt

ARTIFACT_ID = "journey-verifier"
ARTIFACT_SCHEMA = {
    "type": "object",
    "properties": {"sample": {"type": "integer"}},
    "required": ["sample"],
    "additionalProperties": False,
    "examples": [{"sample": 7}],
}
ARTIFACT_CODE = (
    "import json, sys\n"
    "item = json.load(sys.stdin)\n"
    "print(json.dumps({'value': item['sample'] * 2}))\n"
)


def _tool_results(inputs: dict[str, Any]) -> list[dict[str, Any]]:
    """Return only result records the current prompt actually supplied."""
    rows = inputs.get("tool_results", inputs.get("seen_tool_results", []))
    return rows if isinstance(rows, list) else []


def _example(spec: dict[str, Any]) -> dict[str, Any]:
    """Build arguments solely from a published schema, preferring its accepted example."""
    schema = spec["args_schema"]
    examples = schema.get("examples", [])
    if examples:
        return dict(examples[0])
    args: dict[str, Any] = {}
    for name in schema.get("required", []):
        prop = schema["properties"][name]
        if prop.get("enum"):
            args[name] = prop["enum"][0]
        elif prop.get("type") == "integer":
            args[name] = max(1, prop.get("minimum", 1))
        elif prop.get("type") == "number":
            args[name] = max(1, prop.get("minimum", 1))
        elif prop.get("type") == "boolean":
            args[name] = False
        else:
            args[name] = "fixture"
    return args


def _catalogue_tools(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    tools: list[dict[str, Any]] = []
    for row in rows:
        result = row.get("result", {})
        if row.get("tool") == "catalogue.search" and isinstance(result, dict):
            tools.extend(result.get("tools", []))
    return tools


class JourneyProvider:
    """A deterministic policy that reacts only to public prompt inputs and receipts."""

    name = "offline-journey"

    def __init__(self) -> None:
        self.turns: list[dict[str, Any]] = []

    def catalogue(self) -> list[Any]:
        return []

    def balance_micro(self) -> int:
        return 10_000_000

    def affordable(self, model_id: str, ceiling_micro: int) -> tuple[bool, str]:
        return True, ""

    def complete(self, request: ModelRequest) -> ModelResponse:
        text = "\n".join(str(message.get("content", "")) for message in request.messages)
        inputs = _inputs_from_prompt(text)
        description = _description_from_prompt(text)
        reply = self._reply(description, inputs)
        self.turns.append({"description": description, "inputs": inputs, "reply": reply})
        return ModelResponse(request.model_id, json.dumps(reply), 10, 10, "stop")

    def _reply(self, description: str, inputs: dict[str, Any]) -> dict[str, Any]:
        if description.startswith("Evaluate"):
            return self._judge(inputs)
        if description.startswith("Independently discover"):
            return self._caller(inputs)
        return self._maker(inputs)

    def _maker(self, inputs: dict[str, Any]) -> dict[str, Any]:
        rows = _tool_results(inputs)
        unread = inputs.get("unread_outcomes", {})
        items = unread.get("items", []) if isinstance(unread, dict) else []

        grounded = next((item for item in items
                         if item.get("kind") == "grounded_evaluation"), None)
        if grounded is not None and not rows:
            return {"tool_calls": [{"tool": "outcome.get",
                                     "args": {"outcome_id": grounded["outcome_id"]}}]}
        grounded_body = next(
            (row.get("result", {}).get("outcome", {}) for row in rows
             if row.get("tool") == "outcome.get"
             and row.get("result", {}).get("outcome", {}).get("kind")
             == "grounded_evaluation"),
            None,
        )
        if grounded_body is not None:
            return {
                "action": "defer",
                "rationale": (
                    f"Read final {grounded_body['status']} finding from judge "
                    f"{grounded_body['judge_handle']}; defer one tick before revising."
                ),
                "defer": 1,
            }

        rejection = next((item for item in items if item.get("rejection_reason")), None)
        if rejection is not None and not rows:
            return {"tool_calls": [{"tool": "outcome.get",
                                     "args": {"outcome_id": rejection["outcome_id"]}}]}

        rejection_body = next(
            (row.get("result", {}).get("outcome", {}) for row in rows
             if row.get("tool") == "outcome.get"
             and row.get("result", {}).get("outcome", {}).get("rejection_reason")),
            None,
        )
        if rejection_body is not None:
            tool = re.search(r"venue\.[a-z_]+", rejection_body["rejection_reason"])
            assert tool is not None
            return {"tool_calls": [{"tool": "catalogue.search",
                                     "args": {"substring": tool.group(0)}}]}

        tools = _catalogue_tools(rows)
        venue = next((spec for spec in tools if spec["id"].startswith("venue.")), None)
        if venue is not None:
            args = _example(venue)
            if rejection is None and rejection_body is None:
                omitted = venue["args_schema"]["required"][-1]
                args.pop(omitted)
            return {"action": "investigate",
                    "tool_calls": [{"tool": venue["id"], "args": args}]}

        if any(row.get("tool", "").startswith("venue.") for row in rows):
            answer = {"action": "investigate",
                      "rationale": "The corrected public-data request succeeded.",
                      "working_state": {"phase": "ready_to_build"}}
            if rejection is not None:
                answer["ack_through"] = rejection["outcome_id"]
            return answer

        state = inputs.get("your_state", {})
        state_value = state.get("state", {}) if isinstance(state, dict) else {}
        if state_value.get("phase") == "ready_to_build":
            if not rows:
                return {"tool_calls": [{"tool": "catalogue.search",
                                         "args": {"substring": "tool"}}]}
            proposals = next(
                (row.get("result", {}).get("proposal_shapes", {}) for row in rows
                 if row.get("tool") == "catalogue.search"),
                {},
            )
            assert "tool" in proposals
            return {
                "action": "build",
                "rationale": "Publish a deterministic verifier for independent use.",
                "register": [{
                    "kind": "tool",
                    "id": ARTIFACT_ID,
                    "description": "Journey verifier doubles one integer sample",
                    "args_schema": ARTIFACT_SCHEMA,
                    "code": ARTIFACT_CODE,
                    "timeout_s": 2,
                }],
            }

        if not rows:
            return {"tool_calls": [{"tool": "catalogue.search",
                                     "args": {"substring": "venue.order_book"}}]}
        raise AssertionError(f"unhandled maker prompt inputs: {inputs}")

    def _caller(self, inputs: dict[str, Any]) -> dict[str, Any]:
        rows = _tool_results(inputs)
        tools = _catalogue_tools(rows)
        artifact = next((spec for spec in tools if spec["id"] == ARTIFACT_ID), None)
        if artifact is not None:
            return {"tool_calls": [{"tool": artifact["id"],
                                     "args": _example(artifact)}]}
        result = next((row.get("result") for row in rows
                       if row.get("tool") == ARTIFACT_ID), None)
        if result is not None:
            return {"used": True, "value": result["value"]}
        return {"tool_calls": [{"tool": "catalogue.search",
                                 "args": {"substring": "journey verifier"}}]}

    @staticmethod
    def _judge(inputs: dict[str, Any]) -> dict[str, Any]:
        grounded = inputs.get("realized_consequence")
        if grounded is None:
            return {"verdict": 0.5, "rationale": "provisional opinion", "forecasts": []}
        evidence = [
            row for row in grounded["evidence"]
            if row.get("kind") == "ExecutionReceipt:program_result"
            and row.get("payload", {}).get("facts", {}).get("lineage_relation")
            == "cross_lineage"
        ]
        assert evidence
        return {
            "verdict": 0.8,
            "rationale": "The supplied receipt records independent execution.",
            "realized_consequence": {
                "status": "supported",
                "score": 0.8,
                "evidence": [row["ref"] for row in evidence],
                "reason": "A cross-lineage participant executed the registered artifact.",
            },
        }


def _runtime(provider: JourneyProvider) -> Runtime:
    manifest = load_manifest("scripted")
    manifest = replace(
        manifest,
        prompt=PromptSpec(mode="compact"),
        evaluation=replace(
            manifest.evaluation,
            producer_feedback="realized",
            grounded_horizon_ticks=2,
            verdict_timeout_events=4,
        ),
    )
    return Runtime(
        manifest,
        events=0,
        seed=1,
        initial_balance_micro=None,
        ledger_path=None,
        router_gamma=0.2,
        provider=provider,
        exchange=FakeExchange(coins=manifest.exchange.coins),
    )


def _decision(runtime: Runtime, seat: str, channel: str = CH_VERDICT) -> str:
    return runtime.queue.open(
        actor="journey-router",
        event_id=f"journey-{runtime.n}-{seat}",
        propensity=PropensityRecord((seat,), (1.0,), seat, 0, "journey-router", "fixture"),
        channel=channel,
        deadline_ns=runtime.clock.now_ns + 10**12,
        parent_handle=None,
        cost_ceiling=runtime.wallet.available,
    )


def _produce(runtime: Runtime, seat: str = "seed-decider") -> tuple[str, Event]:
    runtime.n += 1
    handle = _decision(runtime, seat)
    runtime._producer_step(
        Event(f"journey-tick-{runtime.n}", EventKind.TICK, runtime.clock.now_ns,
              {"index": runtime.n}, "fixture"),
        handle,
        SimpleNamespace(chosen=seat),
        runtime.queue.get(handle).deadline_ns,
    )
    event = next(
        item for item in reversed(runtime.internal)
        if item.kind == EventKind.PRODUCER_RETURN
        and item.payload.get("about_handle") == handle
    )
    # Actor order is fixture-authored. Remove the routed return from the local
    # scheduler after retaining it; all production effects of the return have
    # already crossed validation and remain in the runtime.
    runtime.internal.remove(event)
    return handle, event


def _advance_ticks(runtime: Runtime, count: int, monkeypatch) -> None:
    """Run normal world Tick transitions while the fixture owns actor scheduling."""
    target = runtime.ticks_consumed + count
    initial_ns = runtime.clock.now_ns
    initial_tick = runtime.ticks_consumed
    stream = iter(
        WorldEvent(
            WorldEventKind.TICK,
            initial_ns + (index + 1) * runtime.tick_clock.interval_ns,
            "offline-journey",
            {"index": initial_tick + index + 1},
        )
        for index in range(count)
    )
    monkeypatch.setattr(runtime, "_route", lambda _event: None)
    while runtime.ticks_consumed < target:
        event = runtime._next_event(stream)
        assert event is not None
        assert runtime._process_event(event)


def _caller_request(runtime: Runtime, seat: str) -> tuple[str, Any]:
    handle = _decision(runtime, seat)
    runtime.consequences.start(handle, runtime.n)
    schema = {
        "type": "object",
        "properties": {"used": {"type": "boolean"}, "value": {"type": "integer"}},
        "required": ["used", "value"],
    }
    request = runtime._request(
        handle,
        "Independently discover the journey verifier, execute it, and report its receipt.",
        {"kind": "Commission", "payload": {}, "world": runtime._world_block()},
        schema,
        runtime.queue.get(handle).deadline_ns,
        CH_VERDICT,
    )
    return handle, replace(request, cost_ceiling=runtime.wallet.available)


@pytest.mark.gate
def test_offline_prompt_contract_artifact_consequence_journey(monkeypatch):
    """A prompt-visible error is corrected before an artifact affects a later decision."""
    monkeypatch.setattr(socket.socket, "connect",
                        lambda *args, **kwargs: pytest.fail("network is forbidden"))
    monkeypatch.setattr(socket, "create_connection",
                        lambda *args, **kwargs: pytest.fail("network is forbidden"))
    monkeypatch.setattr(socket, "getaddrinfo",
                        lambda *args, **kwargs: pytest.fail("network is forbidden"))
    provider = JourneyProvider()
    runtime = _runtime(provider)
    runtime._manage_reserve_window()

    first, _ = _produce(runtime)
    first_return = next(row for row in runtime.ledger._recovery_items()
                        if row["kind"] == "invocation" and row["handle"] == first)
    assert first_return["status"] == "ok"
    assert not [row for row in runtime.ledger._recovery_items()
                if row["kind"] == "tool.call" and row["handle"] == first
                and row["tool"] == "venue.order_book"]
    fault = next(row for row in runtime.ledger._recovery_items()
                 if row["kind"] == "return.sections_dropped" and row["handle"] == first)
    exact_error = fault["dropped"][0]["reason"]
    assert "venue.order_book" in exact_error and "missing argument" in exact_error

    recovered, _ = _produce(runtime)
    corrected = [row for row in runtime.ledger._recovery_items()
                 if row["kind"] == "tool.call" and row["handle"] == recovered
                 and row["tool"] == "venue.order_book"]
    assert len(corrected) == 1 and corrected[0]["outcome"] == "ok"
    recovery_prompts = [turn["inputs"] for turn in provider.turns
                        if turn["inputs"].get("unread_outcomes", {}).get("items")]
    assert any(any(item.get("rejection_reason") == exact_error
                   for item in prompt["unread_outcomes"]["items"])
               for prompt in recovery_prompts)
    fetched = [turn for turn in provider.turns
               if any(row.get("tool") == "outcome.get"
                      and row.get("result", {}).get("outcome", {}).get("rejection_reason")
                      == exact_error for row in _tool_results(turn["inputs"]))]
    assert fetched, "the provider must read the owned rejection body before correcting it"

    maker, _ = _produce(runtime)
    assert ARTIFACT_ID in runtime.population_tools
    assert runtime.tool_owner[ARTIFACT_ID] == "seed-decider"
    accepted = [row for row in runtime.ledger._recovery_items()
                if row["kind"] == "registry.register" and row.get("handle") == maker]
    assert accepted
    assert maker in runtime.grounded_pending

    if not runtime.tool_jail_available:
        pytest.fail("the host cannot execute population code in an OS jail")
    caller, request = _caller_request(runtime, "seed-observer")
    result = runtime._invoke("seed-observer", request, "producer")
    assert result.status == "ok" and result.outputs == {"used": True, "value": 14}, (
        result.status, result.outputs, result.dropped)
    use = [row for row in runtime.ledger._recovery_items()
           if row["kind"] == "tool.call" and row["handle"] == caller
           and row["tool"] == ARTIFACT_ID]
    assert len(use) == 1 and use[0]["outcome"] == "ok"

    contract = runtime.grounded_pending[maker]
    _advance_ticks(runtime, contract.due_tick - runtime.ticks_consumed, monkeypatch)
    commission = next(
        event for event in reversed(runtime.internal)
        if event.payload.get("grounded_consequence")
        and event.payload.get("about_handle") == maker
    )
    evidence = commission.payload["evidence"]
    execution = next(row for row in evidence
                     if row["kind"] == "ExecutionReceipt:program_result")
    facts = execution["payload"]["facts"]
    assert facts["maker_handle"] == maker and facts["caller_handle"] == caller
    assert facts["lineage_relation"] == "cross_lineage" and facts["status"] == "executed"

    judge = _decision(runtime, "eval-b", CH_CONFORMITY)
    runtime._evaluator_step(
        commission,
        judge,
        SimpleNamespace(chosen="eval-b"),
        runtime.queue.get(judge).deadline_ns,
    )
    settlement = runtime.queue.history(maker)[-1]
    assert settlement.status is SettleStatus.SETTLED
    assert settlement.score == pytest.approx(0.8) and settlement.sampling_ref == judge

    after, _ = _produce(runtime)
    after_return = next(row for row in runtime.ledger._recovery_items()
                        if row["kind"] == "invocation" and row["handle"] == after)
    outputs = json.loads(after_return["outputs"])
    assert outputs["action"] == "defer"
    assert "supported" in outputs["rationale"] and judge in outputs["rationale"]
    final_inputs = [turn["inputs"] for turn in provider.turns
                    if any(row.get("tool") == "outcome.get"
                           and row.get("result", {}).get("outcome", {}).get("kind")
                           == "grounded_evaluation"
                           for row in _tool_results(turn["inputs"]))]
    assert final_inputs
    assert runtime.exchange.fills(0) == []
