"""Paid discrimination diagnostic for the Edition 4 nonfinancial gate.

The offline gate (scripts/edition4_nonfinancial_probe.py) shows that a
nonfinancial finding travels from a real execution receipt to real producer
learning.  It cannot show that a model reaches the finding, because its final
evaluator is a deterministic stand-in.  This script captures the requests a
paid evaluator would actually receive for the same five arms and, only when
explicitly told to, dispatches them under the rehearsal's admission cap.

Nothing here is new runtime machinery.  The arms come from
prepare_commission, the request is rendered by the production _evaluator_step
against a capture provider, and the paid rail is the rehearsal's own
Admission and PrepaidProvider.  The seat is instantiated from the edition-3
rehearsal-5 evaluator seat that already runs each model, so the prompt, budget
and effort are production's rather than this script's.

Default mode writes a preflight and makes no call.  Paid dispatch needs --paid,
an unused output directory and an exclusive marker; a partially spent directory
is refused rather than resumed.

The arms remain fixtures: actor selection is forced, the claim is
investigator-authored and assigned to the producer seat, and the expected
answers live in the preflight for the investigator, never in a prompt.  What
this can measure is whether a model's finding tracks the supplied facts.  It
cannot measure spontaneous production, usefulness or autonomy.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from dataclasses import asdict, replace
from pathlib import Path
from typing import Any

from factorylab.kernel.ledger import canonical
from factorylab.learners.router import Sample
from factorylab.runtime.shared import CH_CONFORMITY
from factorylab.runtime.worlds import load_manifest
from scripts.edition4_nonfinancial_probe import (
    ARMS,
    FINAL_JUDGE,
    NORM_SOURCE,
    _forced_draw,
    prepare_commission,
)

MODELS = ("venice:z-ai-glm-5-3-flash", "openai/gpt-5.6-luna")

#: Investigator-only. Never rendered into a request; asserted against the
#: captured prompts before anything is dispatched.
EXPECTED = {
    "independent_use": "supported",
    "contrary_result": "contrary",
    "no_evidence": "unknown",
    "self_use": "unknown",
    "version_mismatch": "unknown",
}
HYPOTHESIS = (
    "Under one frozen investigator-authored claim and the same canonical norms, a paid "
    "evaluator's realized_consequence tracks the supplied execution facts: matching "
    "independent execution is supported, a contradicted result is contrary, and absent, "
    "same-lineage or off-version facts are unknown. This measures finding discrimination "
    "only. It is not evidence of spontaneous production, usefulness, demand or autonomy."
)

CAP_MICRO = 1_000_000
REPEATS = 1
MAX_CALLS = len(MODELS) * len(ARMS) * REPEATS

#: The answer vocabulary. A prompt names all three the same number of times for
#: every arm, or it is telling one arm what to say.
STATUSES = ("supported", "contrary", "unknown")


class Captured(Exception):
    """Raised by the capture provider once the rendered request is in hand."""


class CaptureProvider:
    """Record the request the loop rendered and refuse to complete it."""

    name = "capture"

    def __init__(self) -> None:
        self.request: Any = None
        self.calls = 0

    def affordable(self, _model_id: str, _ceiling_micro: int) -> tuple[bool, str]:
        return True, ""

    def complete(self, req: Any) -> Any:
        self.request = req
        self.calls += 1
        raise Captured()


def production_judge_spec(model_id: str) -> Any:
    """Return the rehearsal-5 evaluator seat that already runs this model."""
    manifest = load_manifest(str(NORM_SOURCE))
    for seat in manifest.assemblies:
        if seat.role == "evaluator" and seat.model_id == model_id:
            return seat
    raise ValueError(f"no rehearsal-5 evaluator seat runs {model_id}")


def capture_request(arm: str, model_id: str) -> dict[str, Any]:
    """Render, without sending, the request a paid evaluator receives for one arm.

    The arm is built by the offline gate's own preparation, the judge seat is
    instantiated through the runtime's own _instantiate with the production
    seat's prompt, budget and effort, and the request is rendered by
    _evaluator_step itself.
    """
    prepared = prepare_commission(arm)
    runtime = prepared["runtime"]
    seat = production_judge_spec(model_id)
    manifest = load_manifest(str(NORM_SOURCE))
    runtime.prices.register(model_id, manifest.price_table().price(model_id))
    capture = CaptureProvider()
    runtime.provider = capture
    existing = runtime.assemblies[FINAL_JUDGE].spec
    runtime._instantiate(
        replace(
            existing,
            model_id=model_id,
            max_tokens=seat.max_tokens,
            effort=seat.effort,
            system_prompt=seat.system_prompt,
        )
    )
    handle = _forced_draw(runtime, prepared["judge_router"], FINAL_JUDGE, CH_CONFORMITY)
    sample = Sample(
        (FINAL_JUDGE,), (1.0,), FINAL_JUDGE, 0, prepared["judge_router"].learner.id, "probe", ()
    )
    try:
        runtime._evaluator_step(prepared["commission"], handle, sample, 10**15)
    except Captured:
        pass
    if capture.request is None:
        raise RuntimeError(f"no request was rendered for {arm} on {model_id}")
    request = asdict(capture.request)
    return {
        "arm": arm,
        "model": model_id,
        "repeat": 1,
        "request": request,
        "request_sha256": hashlib.sha256(canonical(request)).hexdigest(),
        "contract": asdict(prepared["contract"]),
        "evidence_refs": [row["ref"] for row in prepared["evidence"]],
    }


def rendered_text(request: dict[str, Any]) -> str:
    """Everything the model will read, as one string."""
    messages = "".join(str(m.get("content", "")) for m in request.get("messages", []))
    return f"{request.get('system', '')}{messages}"


def expectation_leaks(rows: list[dict[str, Any]]) -> list[int]:
    """Count answer words one arm's prompt carries that its siblings' do not.

    A word-count heuristic and nothing more. The status vocabulary belongs to
    the shared instruction and answer schema, which every arm receives
    identically, so an arm naming a status more often than its siblings is
    evidence of a leak. The converse does not hold: equal counts cannot show
    that no label leaked. A hint carried by phrasing, ordering, or the evidence
    text itself would pass this check untouched, and only reading the frozen
    prompts can rule that out.
    """
    counts = [
        {status: rendered_text(row["request"]).count(status) for status in STATUSES}
        for row in rows
    ]
    floor = {status: min(count[status] for count in counts) for status in STATUSES}
    return [sum(count[status] - floor[status] for status in STATUSES) for count in counts]


def build_preflight() -> dict[str, Any]:
    """Freeze source identity, rendered requests, contracts and expectations."""
    import factorylab
    from scripts.edition4_rehearsal import source_hash

    root = Path(factorylab.__file__).resolve().parents[1]
    tree, digest = source_hash(root)
    inputs: list[dict[str, Any]] = []
    for _repeat in range(REPEATS):
        for model in MODELS:
            rows = [capture_request(arm, model) for arm in ARMS]
            for row, leak in zip(rows, expectation_leaks(rows), strict=True):
                row["expectation_leak"] = leak
            inputs.extend(rows)
    return {
        "probe": "edition4-nonfinancial-model-discrimination-v1",
        "source_tree": tree,
        "source_sha256": digest,
        "norm_source": NORM_SOURCE.name,
        "models": list(MODELS),
        "arms": list(ARMS),
        "repeats": REPEATS,
        "cap_micro": CAP_MICRO,
        "max_calls": MAX_CALLS,
        "investigator_only": {"expected": dict(EXPECTED), "hypothesis": HYPOTHESIS},
        "fixtures": {
            "actor_selection": "forced, under a recorded propensity, without the router's sampler",
            "claim_authorship": "investigator-authored, assigned to the producer seat",
            "judge_seat": "rehearsal-5 evaluator seat prompt, budget and effort",
        },
        "inputs": inputs,
    }


def write_preflight(out: Path) -> dict[str, Any]:
    """Write the preflight once; a later run must reproduce it exactly."""
    out.mkdir(parents=True, exist_ok=True)
    path = out / "preflight.json"
    preflight = json.loads(json.dumps(build_preflight(), sort_keys=True))
    if path.exists():
        if json.loads(path.read_text()) != preflight:
            raise RuntimeError("preflight changed; refusing to overwrite a frozen commission")
    else:
        path.write_text(json.dumps(preflight, indent=2, sort_keys=True) + "\n")
    leaks = sum(row["expectation_leak"] for row in preflight["inputs"])
    if leaks:
        raise RuntimeError("an expected answer appears in a rendered prompt")
    return preflight


def _journal(handle: Any, record: dict[str, Any]) -> None:
    """Persist one journal line before the next action can begin."""
    handle.write(json.dumps(record, sort_keys=True) + "\n")
    handle.flush()
    os.fsync(handle.fileno())


def claim_paid_run(out: Path) -> Path:
    """Take the exclusive paid marker, refusing a repeated or partial directory."""
    marker = out / "paid.marker"
    with marker.open("x") as fh:
        _journal(fh, {"claimed": True, "max_calls": MAX_CALLS, "cap_micro": CAP_MICRO})
    return marker


def default_provider(manifest: Any, admission: Any) -> Any:
    """Build the rehearsal's own prepaid provider under this admission ledger."""
    from scripts.edition4_rehearsal import PrepaidProvider, build_prepaid_provider

    return PrepaidProvider(build_prepaid_provider(manifest), manifest, admission)


def bills_settled(admission: Any) -> bool:
    """Every admitted call reported an authoritative bill within its quote.

    An unknown or table-sourced bill, a reported overrun, a refusal or a stop
    for any reason other than exhausting the run's own call or cost budget
    leaves the evidence uncertain, whatever the call count says.
    """
    return (
        admission.unknown_bills == 0
        and admission.uncertain_bills == 0
        and admission.overruns == 0
        and admission.refusals == 0
        and admission.stop_reason in (None, "max_calls", "cap_exhausted")
    )


def dispatch_paid(out: Path, preflight: dict[str, Any], *,
                  provider_factory: Any = default_provider) -> dict[str, Any]:
    """Send every frozen request once under the rehearsal's own admission cap.

    A run is completed only when every request was sent and every bill came
    back authoritative and within its quote; a final unknown bill leaves the
    run incomplete even though the tenth response arrived. Provider
    construction happens inside the guarded region, so a credential or rail
    failure still produces a report. Each response is journalled with its bill
    before it is parsed, so a crash between dispatch and the records rewrite
    still leaves the model's own answer on disk.
    """
    from factorylab.cortex.assembly import _parse_json_object
    from factorylab.runtime.grounded import observed_evidence_refs, parse_finding
    from factorylab.world.models import ModelRequest
    from scripts.edition4_rehearsal import Admission

    claim_paid_run(out)
    manifest = load_manifest(str(NORM_SOURCE))
    admission = Admission(CAP_MICRO, MAX_CALLS)
    records: list[dict[str, Any]] = []
    status = "incomplete"
    failure: str | None = None
    journal = (out / "calls.jsonl").open("x")
    try:
        provider = provider_factory(manifest, admission)
        for row in preflight["inputs"]:
            _journal(journal, {"stage": "attempt", "arm": row["arm"],
                               "model": row["model"],
                               "request_sha256": row["request_sha256"]})
            request = dict(row["request"])
            request["messages"] = tuple(request.get("messages", ()))
            response = provider.complete(ModelRequest(**request))
            bill = {"served_by": response.model_id, "cost_micro": response.cost_micro,
                    "stop_reason": response.stop_reason}
            # The answer and its bill are durable before anything interprets them.
            _journal(journal, {"stage": "response", "arm": row["arm"], "model": row["model"],
                               "request_sha256": row["request_sha256"],
                               "text": response.text, **bill})
            record = {"arm": row["arm"], "model": row["model"],
                      "request_sha256": row["request_sha256"], "bill": bill}
            try:
                parsed = _parse_json_object(response.text)
                evidence = [{"ref": ref, "kind": "ExecutionReceipt:program_result",
                             "payload": {}} for ref in row["evidence_refs"]]
                finding = parse_finding(
                    parsed.get("realized_consequence"),
                    set(row["evidence_refs"]),
                    observed_evidence_refs(evidence),
                )
                record["finding"] = {"status": finding[0], "score": finding[1],
                                     "evidence": list(finding[2]), "reason": finding[3]}
                record["matches_expectation"] = finding[0] == EXPECTED[row["arm"]]
            except Exception as exc:
                record["parse_failure"] = type(exc).__name__
                record["matches_expectation"] = False
            records.append(record)
            (out / "records.json").write_text(
                json.dumps(records, indent=2, sort_keys=True) + "\n")
        status = ("completed" if len(records) == MAX_CALLS and bills_settled(admission)
                  else "incomplete")
    except BaseException as exc:
        # The class name only: a provider body or credential never enters evidence.
        failure = type(exc).__name__
        _journal(journal, {"stage": "failed", "exception": failure,
                           "completed_calls": len(records),
                           "admission": admission.report()})
    finally:
        journal.close()
        report = {
            "status": status,
            "failure": failure,
            "bills_settled": bills_settled(admission),
            "cost": admission.report(),
            "completed_calls": len(records),
            "matched": sum(bool(r.get("matches_expectation")) for r in records),
            "records": records,
            "scope": (
                "Paid finding discrimination only. No runtime learning, no settlement, no "
                "external transaction, and no evidence of production, usefulness or autonomy."
            ),
        }
        (out / "report.json").write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    return report


def main(argv: list[str] | None = None) -> int:
    """Freeze the commission by default; dispatch only when told to, once."""
    parser = argparse.ArgumentParser(description="Nonfinancial model discrimination probe")
    parser.add_argument("--out", required=True, help="output directory for the frozen run")
    parser.add_argument("--paid", action="store_true",
                        help="dispatch the frozen requests; without it nothing is sent")
    args = parser.parse_args(argv)
    out = Path(args.out)
    preflight = write_preflight(out)
    if not args.paid:
        print(json.dumps({"mode": "preflight", "out": str(out),
                          "requests": len(preflight["inputs"]),
                          "cap_micro": CAP_MICRO, "paid_calls": 0}, sort_keys=True))
        return 0
    report = dispatch_paid(out, preflight)
    print(json.dumps({"mode": "paid", "out": str(out), "status": report["status"],
                      "completed_calls": report["completed_calls"],
                      "matched": report["matched"], "cost": report["cost"]}, sort_keys=True))
    return 0 if report["status"] == "completed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
