"""Replay the edition-4 oversized investigation batches without network or spend.

This is a mechanical diagnostic.  The repaired replies are authored fixtures that
split calls into valid batches; they are not population outputs and say nothing about
whether a model would learn the repair.  The production return validator, continuation
loop, catalogue, outcome store, wallet, and ledger remain the system under test.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from dataclasses import asdict, replace
from pathlib import Path
from typing import Any
from unittest.mock import patch

from factorylab.kernel.queue import PropensityRecord
from factorylab.runtime.loop import Runtime
from factorylab.runtime.shared import CH_VERDICT
from factorylab.runtime.worlds import PromptSpec, load_manifest
from factorylab.world.exchange import FakeExchange
from factorylab.world.models import ModelRequest, ModelResponse
from factorylab.world.scripted import ScriptedProvider

DEFAULT_SOURCE = Path("work/coverage-60-r2/live/events.json")
DEFAULT_REPORT = Path("work/coverage-60-r2/live/report.json")
FAILURE = "more than 4 tool_calls"
REPAIR_NOTE = (
    "Mechanical diagnostic fixture: the original reads were split into bounded batches. "
    "No population learning or behavioral improvement is claimed."
)


def _digest(value: str | bytes | object) -> str:
    if not isinstance(value, (str, bytes)):
        value = json.dumps(value, sort_keys=True, separators=(",", ":"))
    if isinstance(value, str):
        value = value.encode()
    return hashlib.sha256(value).hexdigest()


def _json_objects(text: str) -> list[dict[str, Any]]:
    """Return top-level JSON objects embedded in one provider answer, in order."""
    decoder = json.JSONDecoder()
    objects: list[dict[str, Any]] = []
    cursor = 0
    while cursor < len(text):
        start = text.find("{", cursor)
        if start < 0:
            break
        try:
            value, end = decoder.raw_decode(text, start)
        except json.JSONDecodeError:
            cursor = start + 1
            continue
        if isinstance(value, dict):
            objects.append(value)
        cursor = end
    return objects


def _oversized(row: dict[str, Any]) -> bool:
    return any(
        isinstance(item, dict) and FAILURE in str(item.get("reason", ""))
        for item in row.get("dropped", [])
    )


def source_cases(events_path: Path) -> list[dict[str, Any]]:
    """Recover each exact rejected answer from the public, post-run event export."""
    events = json.loads(events_path.read_text())
    failures = [row for row in events if isinstance(row, dict) and _oversized(row)]
    cases: list[dict[str, Any]] = []
    for failure in failures:
        handle = failure["handle"]
        invocation = next(
            row for row in events
            if isinstance(row, dict) and row.get("kind") == "invocation"
            and row.get("handle") == handle
        )
        result = max(
            (
                row for row in events
                if isinstance(row, dict) and row.get("kind") == "io.result"
                and row.get("seq", -1) < invocation["seq"]
                and isinstance(row.get("result"), dict)
                and isinstance(row["result"].get("fields"), dict)
                and isinstance(row["result"]["fields"].get("text"), str)
            ),
            key=lambda row: row["seq"],
        )
        raw = result["result"]["fields"]["text"]
        objects = _json_objects(raw)
        calls = next(
            (obj["tool_calls"] for obj in objects if isinstance(obj.get("tool_calls"), list)),
            None,
        )
        if calls is None or len(calls) <= 4:
            raise RuntimeError(f"{handle}: source no longer contains an oversized batch")
        cases.append({
            "source_handle": handle,
            "assembly_id": failure["assembly_id"],
            "source_failure_kind": failure["kind"],
            "source_failure_seq": failure["seq"],
            "source_failure_ledger_hash": failure["hash"],
            "source_io_result_seq": result["seq"],
            "source_io_result_ledger_hash": result["hash"],
            "source_invocation_status": invocation["status"],
            "source_cost_micro": invocation["cost"],
            "raw_answer": raw,
            "raw_answer_sha256": _digest(raw),
            "tool_calls": calls,
        })
    if len(cases) != 9:
        raise RuntimeError(f"expected 9 oversized source batches, found {len(cases)}")
    return cases


def _runtime() -> Runtime:
    manifest = replace(load_manifest("scripted"), prompt=PromptSpec(mode="compact"))
    return Runtime(
        manifest,
        events=0,
        seed=1,
        initial_balance_micro=None,
        ledger_path=None,
        drip=False,
        router_gamma=0.1,
        provider=ScriptedProvider(),
        exchange=FakeExchange(coins=manifest.exchange.coins),
    )


def _request(runtime: Runtime):
    seat = "seed-decider"
    handle = runtime.queue.open(
        actor=seat,
        event_id="edition4-investigation-replay",
        propensity=PropensityRecord((seat,), (1.0,), seat, 0, seat, "audit"),
        channel=CH_VERDICT,
        deadline_ns=10**15,
        parent_handle=None,
        cost_ceiling=1_000_000,
    )
    runtime.consequences.start(handle, 0)
    return replace(
        runtime._request(
            handle,
            "Replay one bounded investigation decision.",
            {"kind": "Tick", "payload": {}, "world": runtime._world_block()},
            {},
            10**15,
            CH_VERDICT,
        ),
        cost_ceiling=1_000_000,
    )


def _rows(runtime: Runtime, kind: str) -> list[dict[str, Any]]:
    return [row for row in runtime.ledger._recovery_items() if row["kind"] == kind]


def _response(request: ModelRequest, text: str) -> ModelResponse:
    return ModelResponse(request.model_id, text, 1, 1, "stop")


def _invoke(runtime: Runtime, request, replies: list[str]) -> tuple[Any, list[dict[str, Any]]]:
    captured: list[dict[str, Any]] = []

    def complete(model_request: ModelRequest) -> ModelResponse:
        index = len(captured)
        if index >= len(replies):
            raise RuntimeError("runtime requested an unplanned diagnostic continuation")
        captured.append(asdict(model_request))
        return _response(model_request, replies[index])

    with patch.object(runtime.provider.target, "complete", complete):
        result = runtime._invoke("seed-decider", request, "producer")
    return result, captured


def _seed_outcomes(runtime: Runtime, highest: int) -> None:
    for index in range(1, highest + 1):
        runtime.outcomes.append(
            "seed-decider",
            handle=f"source-outcome-{index}",
            outcome={"fixture": index, "source": "mechanical diagnostic"},
            evidence={"network": False, "provider_spend": False},
        )


def _repair_replies(calls: list[dict[str, Any]]) -> list[str]:
    batches = [calls[index:index + 4] for index in range(0, len(calls), 4)]
    replies: list[dict[str, Any]] = [
        {"tool_calls": [{"tool": "catalogue.search", "args": {"substring": "outcome.get"}}]},
        *({"tool_calls": batch} for batch in batches),
        {"action": "hold", "rationale": REPAIR_NOTE},
    ]
    return [json.dumps(reply, sort_keys=True, separators=(",", ":")) for reply in replies]


def replay_case(case: dict[str, Any], include_requests: bool) -> dict[str, Any]:
    original_runtime = _runtime()
    original_request = _request(original_runtime)
    before = original_runtime.wallet.balance
    original, original_requests = _invoke(
        original_runtime, original_request, [case["raw_answer"]]
    )
    original_faults = [
        row for kind in ("return.sections_dropped", "return.validation_failed")
        for row in _rows(original_runtime, kind)
        if _oversized(row)
    ]
    if len(original_faults) != 1 or _rows(original_runtime, "tool.call"):
        raise RuntimeError(f"{case['source_handle']}: oversized batch was not atomically rejected")
    expected_status = (
        "malformed" if case["source_failure_kind"] == "return.validation_failed" else "ok"
    )
    if original.status != expected_status:
        raise RuntimeError(
            f"{case['source_handle']}: replay status {original.status!r}, "
            f"expected {expected_status!r}"
        )

    repaired_runtime = _runtime()
    repaired_request = _request(repaired_runtime)
    outcome_numbers = [
        int(call["args"]["outcome_id"].split(":", 1)[1])
        for call in case["tool_calls"]
        if call.get("tool") == "outcome.get"
    ]
    _seed_outcomes(repaired_runtime, max(outcome_numbers, default=0))
    replies = _repair_replies(case["tool_calls"])
    repaired_before = repaired_runtime.wallet.balance
    repaired, repaired_requests = _invoke(repaired_runtime, repaired_request, replies)
    tool_rows = _rows(repaired_runtime, "tool.call")
    expected_tools = ["catalogue.search", *(call["tool"] for call in case["tool_calls"])]
    observed_tools = [row["tool"] for row in tool_rows]
    if repaired.status != "ok" or repaired.outputs.get("action") != "hold":
        raise RuntimeError(f"{case['source_handle']}: repaired chain did not finish validly")
    if observed_tools != expected_tools or not all(row["ok"] for row in tool_rows):
        raise RuntimeError(f"{case['source_handle']}: repaired reads did not all traverse tools")
    if not all(row["handle"] == repaired_request.handle for row in tool_rows):
        raise RuntimeError(f"{case['source_handle']}: continuation changed decision handle")
    if any(len(json.loads(reply).get("tool_calls", [])) > 4 for reply in replies):
        raise RuntimeError(f"{case['source_handle']}: authored repair still exceeds the limit")
    if not (0 < repaired.cost <= repaired_request.cost_ceiling):
        raise RuntimeError(f"{case['source_handle']}: repaired chain escaped its money bound")
    prompt_texts = [
        "\n".join(str(message.get("content", "")) for message in request["messages"])
        for request in repaired_requests
    ]
    if '"id":"outcome.get"' not in prompt_texts[1].replace(" ", ""):
        raise RuntimeError(f"{case['source_handle']}: catalogue schema was not retrieved")
    if case["tool_calls"][0]["args"]["outcome_id"] not in prompt_texts[2]:
        raise RuntimeError(f"{case['source_handle']}: first tool result did not reach continuation")

    result = {
        **{key: value for key, value in case.items() if key not in ("raw_answer", "tool_calls")},
        "source_tool_call_count": len(case["tool_calls"]),
        "original_replay": {
            "status": original.status,
            "cost_micro": original.cost,
            "wallet_delta_micro": before - original_runtime.wallet.balance,
            "tool_calls_dispatched": 0,
            "fault_kind": original_faults[0]["kind"],
            "fault_reason": next(
                item["reason"] for item in original_faults[0]["dropped"]
                if FAILURE in item["reason"]
            ),
            "request_sha256": _digest(original_requests[0]),
        },
        "mechanical_repair": {
            "authorship": "audit fixture, not population output",
            "reply_sha256": [_digest(reply) for reply in replies],
            "batch_sizes": [len(json.loads(reply).get("tool_calls", [])) for reply in replies],
            "status": repaired.status,
            "final_action": repaired.outputs.get("action"),
            "cost_micro": repaired.cost,
            "wallet_delta_micro": repaired_before - repaired_runtime.wallet.balance,
            "cost_ceiling_micro": repaired_request.cost_ceiling,
            "same_handle": all(row["handle"] == repaired_request.handle for row in tool_rows),
            "replay_handle": repaired_request.handle,
            "tool_calls": observed_tools,
            "all_tool_calls_ok": all(row["ok"] for row in tool_rows),
            "catalogue_schema_delivered": True,
            "tool_results_delivered": True,
            "continuation_count": len(repaired_requests) - 1,
            "request_sha256": [_digest(request) for request in repaired_requests],
        },
    }
    if include_requests:
        result["capture"] = {
            "source_raw_answer": case["raw_answer"],
            "source_tool_calls": case["tool_calls"],
            "authored_replies": replies,
            "model_requests": repaired_requests,
        }
    return result


def run(source: Path, report_path: Path, include_requests: bool) -> dict[str, Any]:
    report = json.loads(report_path.read_text())
    summary = report.get("summary", {})
    if summary.get("terminated") is not True:
        raise RuntimeError("source report is not a terminated world")
    cases = source_cases(source)
    results = [replay_case(case, include_requests) for case in cases]
    stripped = sum(case["source_failure_kind"] == "return.sections_dropped" for case in cases)
    malformed = sum(case["source_failure_kind"] == "return.validation_failed" for case in cases)
    frozen = {
        "source_events_sha256": _digest(source.read_bytes()),
        "source_report_sha256": _digest(report_path.read_bytes()),
        "source_case_hashes": [case["raw_answer_sha256"] for case in cases],
        "mechanical_reply_hashes": [
            result["mechanical_repair"]["reply_sha256"] for result in results
        ],
    }
    return {
        "schema_version": 1,
        "diagnostic": "edition4 oversized investigation replay",
        "interpretation": REPAIR_NOTE,
        "source": {
            "events": str(source),
            "report": str(report_path),
            "world": summary.get("world"),
            "terminated": summary.get("terminated"),
            "termination_reason": summary.get("termination_reason"),
            "oversized_batches": len(cases),
            "valid_answers_with_atomic_batch_stripped": stripped,
            "tool_only_answers_malformed": malformed,
        },
        "isolation": {
            "provider": "ScriptedProvider",
            "exchange": "FakeExchange",
            "network_calls": False,
            "credentials_read": False,
            "provider_spend_micro": 0,
        },
        "frozen_fixture": frozen,
        "cases": results,
        "verdict": {
            "exact_oversized_answers_rejected_by_runtime": True,
            "all_authored_repairs_valid": True,
            "all_repairs_used_catalogue_then_reads_then_final_answer": True,
            "all_repairs_kept_one_handle": True,
            "all_repairs_within_decision_money_bound": True,
            "population_learning_shown": False,
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--output", type=Path)
    parser.add_argument(
        "--capture-requests",
        action="store_true",
        help="include exact raw answers, authored replies, and ModelRequest payloads",
    )
    args = parser.parse_args()
    result = run(args.source, args.report, args.capture_requests)
    rendered = json.dumps(result, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.write_text(rendered)
    else:
        print(rendered, end="")


if __name__ == "__main__":
    main()
