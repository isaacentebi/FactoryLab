"""Run one bounded arm of the Edition 4 investigation diagnostic.

The default freezes four actual ``Runtime._invoke`` requests without constructing
a paid provider. ``--paid`` executes that frozen arm once. Run baseline and
candidate in separate processes; the lead compares their reports.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import statistics
from dataclasses import replace
from pathlib import Path
from typing import Any

from factorylab.cortex.assembly import reserved_return_fields
from factorylab.kernel.queue import PropensityRecord
from factorylab.runtime.loop import Runtime
from factorylab.runtime.shared import CH_VERDICT
from factorylab.runtime.worlds import load_manifest
from factorylab.world.exchange import FakeExchange
from scripts import edition4_discovery_probe as discovery_helper
from scripts import edition4_rehearsal as rehearsal_helper
from scripts.edition4_discovery_probe import (
    JournalingProvider,
    NullProvider,
    ProbeRefused,
    RunJournal,
    actual_model_request,
    effective_manifest,
    preflight_digest,
    request_digest,
)
from scripts.edition4_rehearsal import (
    Admission,
    DeniedMarket,
    DeniedTransferRail,
    PrepaidProvider,
    build_prepaid_provider,
    source_hash,
)

DEFAULT_WORLD = Path("work/coverage-60-r2/world.toml")
DEFAULT_OUT = Path("work/investigation-probe-arm")
CAP_MICRO, MAX_CALLS = 480_000, 40
SEATS = ("mechanism", "opportunity")
CASES = ("catalogue_calc", "private_outcomes")
FIXED_NOW_NS = 1_800_000_000_000_000_000
ALLOWED_TOOLS = frozenset({"artifact.get", "calc", "catalogue.search", "outcome.get",
                           "outcome.list", "world.read"})
COMMISSION = ("Commissioned diagnostic: receipt success tests a supplied retrieval task, "
              "not spontaneous objective formation or institutional emergence.")
CALC_EXPECTED = "4321.625000"
TASKS = {
    "catalogue_calc": (
        "Return the exact notional in United States dollars for 3.5 base units at "
        "1234.75 dollars per unit, to six decimal places, as notional_usd. Obtain the "
        "number from a capability published by this world and identify its receipt in "
        "evidence. Do not trade, transfer, write, or send a message."
    ),
    "private_outcomes": (
        "Six private outcome bodies in your inbox each contain one exact string at "
        "outcome.payload.fact. Retrieve all six and return the strings in inbox order "
        "as facts. Bodies, not index summaries, are authoritative. Use multiple read "
        "batches when required. You may inspect the catalogue for the retrieval schema. "
        "You may use private working_state solely to carry retrieved facts across this "
        "decision's continuation rounds. Do not trade, transfer, acknowledge, write a "
        "public note, or send a message."
    ),
}
SCHEMAS: dict[str, dict[str, Any]] = {
    "catalogue_calc": {
        "type": "object",
        "properties": {"notional_usd": {"type": "string"},
                       "evidence": {"type": "string"}},
        "required": ["notional_usd", "evidence"],
    },
    "private_outcomes": {
        "type": "object",
        "properties": {"facts": {"type": "array", "items": {"type": "string"},
                                  "minItems": 6, "maxItems": 6},
                       "evidence": {"type": "string"}},
        "required": ["facts", "evidence"],
    },
}


class StableCaptureProvider:
    """Hide changing provider state and privately retain paid request/response bodies."""

    name = "edition4-investigation-capture"

    def __init__(self, inner: Any):
        self.inner, self.rows = inner, []

    def catalogue(self) -> list:
        return []

    def balance_micro(self) -> None:
        return None

    def balance_of(self, _model_id: str) -> None:
        return None

    def affordable(self, model_id: str, ceiling_micro: int) -> tuple[bool, str]:
        method = getattr(self.inner, "affordable", None)
        return (True, "") if method is None else method(model_id, ceiling_micro)

    def complete(self, request: Any) -> Any:
        response = self.inner.complete(request)
        request_bytes = len(request.system.encode()) + sum(
            len(str(dict(message).get("content", "")).encode())
            for message in request.messages)
        self.rows.append({
            "request": {"model_id": request.model_id, "system": request.system,
                        "messages": [dict(m) for m in request.messages],
                        "max_tokens": request.max_tokens, "effort": request.effort,
                        "json_object": request.json_object, "bytes": request_bytes},
            "response": {"model_id": response.model_id, "text": response.text,
                         "input_tokens": response.input_tokens,
                         "output_tokens": response.output_tokens,
                         "stop_reason": response.stop_reason,
                         "cost_micro": response.cost_micro, "refused": response.refused,
                         "raw": response.raw},
        })
        return response

    def __getattr__(self, name: str) -> Any:
        return getattr(self.inner, name)


def _facts(seat: str) -> list[str]:
    return [hashlib.sha256(f"investigation-v1:{seat}:{i}".encode()).hexdigest()[:24]
            for i in range(1, 7)]


def _runtime(manifest: Any, provider: Any, receipts: list[dict[str, Any]]) -> Runtime:
    runtime = Runtime(manifest, events=0, seed=manifest.seed, initial_balance_micro=None,
                      ledger_path=None, router_gamma=0.1, provider=provider,
                      market=DeniedMarket(),
                      exchange=FakeExchange(coins=manifest.exchange.coins))
    runtime.clock.now_ns = FIXED_NOW_NS
    runtime.treasury.rail.target = DeniedTransferRail(runtime.treasury.rail.target)
    inner = runtime._run_tool

    def guarded(action_id: str, handle: str, call: dict[str, Any], *, slot: Any,
                version: int | None = None):
        tool = str(call.get("tool"))
        if tool not in ALLOWED_TOOLS:
            receipts.append({"tool": tool, "slot": str(slot), "refused": True})
            return {"error": "capability refused: investigation restriction"}, 0
        result, cost = inner(action_id, handle, call, slot=slot, version=version)
        receipts.append({"tool": tool, "slot": str(slot), "result": result,
                         "cost_micro": cost, "refused": False})
        return result, cost

    runtime._run_tool = guarded
    return runtime


def _seed_outcomes(runtime: Runtime, seat: str) -> list[str]:
    facts = _facts(seat)
    for i, fact in enumerate(facts, 1):
        runtime.outcomes.append(
            seat, handle=f"private-synthetic-{i}",
            outcome={"kind": "diagnostic.private", "payload": {"fact": fact}},
            evidence={"scope": "commissioned", "ordinal": i},
            observed_at_ns=FIXED_NOW_NS + i)
    return facts


def _decision(runtime: Runtime, seat: str, case: str, ceiling: int) -> Any:
    deadline = runtime.clock.now_ns + 24 * 3600 * 10**9
    handle = runtime.queue.open(
        actor=seat, event_id=f"investigation-{case}",
        propensity=PropensityRecord((seat,), (1.0,), seat, 0, seat, "commissioned"),
        channel=CH_VERDICT, deadline_ns=deadline, parent_handle=None,
        cost_ceiling=ceiling)
    runtime.consequences.start(handle, 0)
    inputs = {"kind": "WorldUpdate", "payload": {}, "world": runtime._world_block(),
              "your_state": runtime.working_state.render(seat),
              "unread_outcomes": runtime.outcomes.unread(seat)}
    reserved = reserved_return_fields(max_tool_calls=runtime.m.tools.max_tool_calls)
    schema = {**SCHEMAS[case], "properties": {
        **SCHEMAS[case]["properties"],
        **{key: reserved[key] for key in ("tool_calls", "working_state")},
    }}
    request = runtime._request(handle, TASKS[case], inputs, schema, deadline, CH_VERDICT)
    return replace(request, cost_ceiling=ceiling)


def freeze_arm(world: Path, source_root: Path) -> dict[str, Any]:
    imported_root, source_sha = source_hash(source_root)
    manifest = effective_manifest(load_manifest(str(world)))
    ceiling, decisions = CAP_MICRO // 4, {}
    for seat in SEATS:
        for case in CASES:
            runtime = _runtime(manifest, NullProvider(), [])
            if case == "private_outcomes":
                _seed_outcomes(runtime, seat)
            actual, model = actual_model_request(
                runtime, seat, _decision(runtime, seat, case, ceiling))
            rendered = replace(actual, inputs={**actual.inputs, "you": seat})
            decisions[f"{seat}:{case}"] = {
                "model_id": model.model_id, "request_sha256": request_digest(model),
                "request_bytes": len(model.system.encode()) + sum(
                    len(str(dict(m).get("content", "")).encode()) for m in model.messages),
                "section_bytes": rendered.section_bytes(),
                "quote_micro": runtime.assemblies[seat].model.ceiling(model),
                "schema_sha256": hashlib.sha256(
                    json.dumps(actual.outcome_schema, sort_keys=True).encode()).hexdigest(),
                "continuity_fields": ["unread_outcomes", "your_state"],
            }
    return {
        "schema": "factorylab.edition4.investigation.preflight.v1",
        "source": {"root": imported_root, "sha256": source_sha},
        "runner_sha256": hashlib.sha256(Path(__file__).resolve().read_bytes()).hexdigest(),
        "helper_sha256": {
            "edition4_discovery_probe.py": hashlib.sha256(
                Path(discovery_helper.__file__).resolve().read_bytes()).hexdigest(),
            "edition4_rehearsal.py": hashlib.sha256(
                Path(rehearsal_helper.__file__).resolve().read_bytes()).hexdigest(),
        },
        "world": {"path": str(world.resolve()), "manifest_sha256": manifest.manifest_hash(),
                  "prompt_mode": manifest.prompt.mode},
        "models": {a.id: a.model_id for a in manifest.assemblies if a.id in SEATS},
        "cap_micro": CAP_MICRO, "max_calls": MAX_CALLS,
        "decision_ceiling_micro": ceiling, "allowed_tools": sorted(ALLOWED_TOOLS),
        "decisions": decisions, "commission": COMMISSION,
    }


def _result(seat: str, case: str, ret: Any, receipts: list[dict[str, Any]],
            expected: list[str]) -> dict[str, Any]:
    ok = [r for r in receipts if not r["refused"]
          and not (isinstance(r.get("result"), dict) and r["result"].get("error") is not None)]
    target = "calc" if case == "catalogue_calc" else "outcome.get"
    discovered = any(any(t.get("id") == target for t in r["result"].get("tools", ()))
                     for r in ok if r["tool"] == "catalogue.search"
                     and isinstance(r.get("result"), dict))
    base = {"seat": seat, "case": case, "status": ret.status, "cost_micro": ret.cost,
            "catalogue_schema_discovered": discovered,
            "calls": [{"tool": r["tool"], "slot": r["slot"], "ok": r in ok}
                      for r in receipts]}
    if case == "catalogue_calc":
        receipt = CALC_EXPECTED in [r["result"].get("notional_usd") for r in ok
                                    if r["tool"] == "calc"]
        exact = ret.status == "ok" and ret.outputs.get("notional_usd") == CALC_EXPECTED
        return {**base, "receipt_success": receipt, "exact_final_answer": exact,
                "success": receipt and exact}
    rows = [r for r in ok if r["tool"] == "outcome.get"]
    fact_rows = [(r, r["result"].get("outcome", {}).get("payload", {}).get("fact"))
                 for r in rows]
    retrieved = {fact for _row, fact in fact_rows} == set(expected)
    valid_batch = len({row["slot"].split(":", 1)[0] for row, fact in fact_rows
                       if fact in expected}) >= 2
    exact = ret.status == "ok" and ret.outputs.get("facts") == expected
    return {**base, "receipt_success": retrieved, "retrieved_all_facts": retrieved,
            "valid_batch": valid_batch, "exact_final_answer": exact,
            "success": retrieved and valid_batch and exact}


def _median(rows: list[dict[str, Any]], group: str, field: str) -> int | float | None:
    values = [row[group].get(field) for row in rows]
    values = [value for value in values if type(value) in (int, float)]
    return statistics.median(values) if values else None


def _private_capture(out: Path, capture: Any, cases: dict[str, Any]) -> dict[str, str]:
    path = out / "captures.private.json"
    path.write_text(json.dumps({"provider": getattr(capture, "rows", []), "cases": cases},
                               indent=2, sort_keys=True, default=str) + "\n")
    path.chmod(0o600)
    return {"path": str(path.resolve()),
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}


def run_paid(preflight: dict[str, Any], world: Path, out: Path) -> dict[str, Any]:
    manifest = effective_manifest(load_manifest(str(world)))
    admission = Admission(CAP_MICRO, MAX_CALLS)
    journal_path = out / f"paid.{preflight_digest(preflight)[:16]}.journal.jsonl"
    journal = RunJournal(journal_path, {"preflight_digest": preflight_digest(preflight),
                                        "cap_micro": CAP_MICRO, "max_calls": MAX_CALLS})
    capture, raw_cases, results = None, {}, []
    try:
        capture = StableCaptureProvider(build_prepaid_provider(manifest))
        provider = JournalingProvider(
            PrepaidProvider(capture, manifest, admission), journal,
            {k: v["request_sha256"] for k, v in preflight["decisions"].items()}, admission)
        for seat in SEATS:
            for case in CASES:
                key = f"{seat}:{case}"
                if admission.stop_reason is not None:
                    results.append({"seat": seat, "case": case, "status": "skipped",
                                    "reason": admission.stop_reason, "success": False})
                    continue
                receipts: list[dict[str, Any]] = []
                runtime = _runtime(manifest, provider, receipts)
                expected = _seed_outcomes(runtime, seat) if case == "private_outcomes" else []
                request = _decision(runtime, seat, case, preflight["decision_ceiling_micro"])
                raw_cases[key] = {"tool_receipts": receipts}
                provider.begin_seat(key)
                ret = runtime._invoke(seat, request, "producer")
                raw_cases[key].update({"final_status": ret.status,
                                       "final_outputs": ret.outputs})
                results.append(_result(seat, case, ret, receipts, expected))
        costs = [r["response"]["cost_micro"] for r in capture.rows
                 if type(r["response"].get("cost_micro")) is int]
        model_metrics = {}
        for seat, model_id in preflight["models"].items():
            rows = [row for row in capture.rows if row["request"]["model_id"] == model_id]
            model_metrics[seat] = {
                "model_id": model_id, "calls": len(rows),
                "cost_micro": sum(row["response"].get("cost_micro") or 0 for row in rows),
                "median_cost_micro": _median(rows, "response", "cost_micro"),
                "median_request_bytes": _median(rows, "request", "bytes"),
                "median_input_tokens": _median(rows, "response", "input_tokens"),
                "median_output_tokens": _median(rows, "response", "output_tokens"),
            }
        status = "completed" if admission.stop_reason is None else "incomplete"
        return {"status": status, "executed": True, "cases": results,
                "all_four_success": len(results) == 4 and all(r["success"] for r in results),
                "calls": len(capture.rows), "cost": admission.report(),
                "median_cost_micro": statistics.median(costs) if costs else None,
                "model_metrics": model_metrics,
                "journal": str(journal_path.resolve()),
                "private_captures": _private_capture(out, capture, raw_cases),
                "commission": COMMISSION}
    except Exception as exc:
        attempted = admission.report()["attempted"]
        return {"status": "incomplete" if attempted else "failed",
                "executed": attempted > 0, "reason": getattr(exc, "reason", type(exc).__name__),
                "cases": results, "calls": attempted, "cost": admission.report(),
                "journal": str(journal_path.resolve()),
                "private_captures": _private_capture(out, capture, raw_cases),
                "commission": COMMISSION}
    finally:
        journal.append({"event": "run_end", "cost": admission.report()})
        journal.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--world", type=Path, default=DEFAULT_WORLD)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--paid", action="store_true")
    args = parser.parse_args(argv)
    args.out.mkdir(parents=True, exist_ok=True)
    args.out.chmod(0o700)
    try:
        preflight = freeze_arm(args.world, args.source_root)
        digest, freeze = preflight_digest(preflight), args.out / "preflight.json"
        frozen = {"preflight_digest": digest, "preflight": preflight}
        if args.paid and not freeze.is_file():
            raise ProbeRefused("paid_requires_existing_preflight")
        if freeze.is_file() and json.loads(freeze.read_text()) != frozen:
            raise ProbeRefused("preflight_changed")
        if not freeze.is_file():
            freeze.write_text(json.dumps(frozen, indent=2, sort_keys=True) + "\n")
            freeze.chmod(0o600)
        report = ({"status": "frozen", "executed": False, "preflight_digest": digest,
                   "preflight": str(freeze.resolve()), "commission": COMMISSION}
                  if not args.paid else run_paid(preflight, args.world, args.out))
        report.update({"source": preflight["source"], "models": preflight["models"]})
    except Exception as exc:
        report = {"status": "failed", "executed": False,
                  "reason": getattr(exc, "reason", type(exc).__name__)}
    path = args.out / "report.json"
    path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    path.chmod(0o600)
    print(json.dumps(report, sort_keys=True))
    return 0 if report["status"] in ("frozen", "completed") else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
