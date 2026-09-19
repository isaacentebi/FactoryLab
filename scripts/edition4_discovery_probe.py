"""Bounded paid diagnostic: can a seat discover a capability and use its receipt?

The probe commissions one frozen question per seat on a fake exchange, restricts
the population's callable surface to the catalogue, the institutional read and
the arithmetic primitive, and reads the answer from receipts rather than from
prose. It opens no venue position, makes no transfer, buys no data and reaches
no x402 seller. Paid providers are constructed only under an explicit flag;
without it the probe freezes its preflight and stops.

What it can establish is mechanical: that a seat given no tool names beyond the
world's own published prompt can find a contract, call it, and carry the returned
value into its answer. The question is commissioned, so nothing here is evidence
of a spontaneously chosen objective.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from factorylab.kernel.ledger import canonical
from factorylab.kernel.queue import PropensityRecord
from factorylab.runtime.loop import Runtime
from factorylab.runtime.shared import CH_VERDICT
from factorylab.runtime.worlds import PromptSpec, load_manifest
from factorylab.world.exchange import FakeExchange
from scripts.edition4_rehearsal import (
    Admission,
    DeniedMarket,
    DeniedTransferRail,
    PrepaidProvider,
    RehearsalRefused,
    _safe_exception,
    build_prepaid_provider,
    runner_hash,
    source_hash,
)

DEFAULT_WORLD = Path("worlds/edition3-rehearsal-5.toml")
DEFAULT_OUT = Path("docs/audits/edition4-next/discovery-probe.jsonl")
DEFAULT_FREEZE = Path("docs/audits/edition4-next/discovery-probe.preflight.json")
#: One US dollar for the whole invocation, continuations included, and a call
#: ceiling well under what two decisions of three rounds each can consume.
DEFAULT_CAP_MICRO = 1_000_000
DEFAULT_MAX_CALLS = 12
#: The GLM producer and the Luna producer of the rehearsal-5 roster: two rails,
#: two trainings, both already priced in this manifest.
DEFAULT_SEATS = ("mechanism", "opportunity")
#: The whole callable surface of this diagnostic. Everything else this world
#: publishes - the venue, the treasury, the transport, the web and the x402
#: rails - is refused before dispatch and recorded as refused.
ALLOWED_TOOLS = frozenset({"catalogue.search", "world.read", "calc"})

#: The frozen question. It names no tool, no argument and no capability id: what
#: a seat can find is what the world's own prompt already publishes to it.
TASK = (
    "Report the exact notional value in United States dollars of a position of "
    "3.5 base units at a price of 1234.75 dollars per base unit, to six decimal "
    "places, in the field notional_usd. Obtain that figure from a capability this "
    "world publishes rather than from arithmetic you perform yourself, and name in "
    "the field evidence the capability you used and the exact value it returned. "
    "This question is asked of you directly; it is not a trading instruction, and "
    "no order, transfer or message is wanted."
)
TASK_SIZE = "3.5"
TASK_PRICE = "1234.75"
#: What the arithmetic primitive returns for that position, to six places.
EXPECTED_VALUE = "4321.625000"
OUTCOME_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "notional_usd": {"type": "string"},
        "evidence": {"type": "string"},
    },
    "required": ["notional_usd", "evidence"],
}
COMMISSION_NOTE = (
    "Commissioned diagnostic. A completed chain evidences that the discovery and "
    "continuation interface works, not that any seat chose this objective."
)
#: An admission stop that leaves what was bought uncertain. A run that ends on
#: one of these is incomplete, whatever its seats returned.
UNCERTAIN_STOPS = frozenset({"unknown_bill", "unknown_bill_after_dispatch",
                             "non_authoritative_table_cost", "reported_overrun"})
#: The one field of a rendered prompt that moves without anything else changing:
#: the venue observation's wall-clock freshness stamp. It is masked before the
#: request is digested, and named in the preflight, so what the freeze covers and
#: what it cannot cover are both visible to a reviewer.
FRESHNESS_STAMP = re.compile(r'"observed_at_ns":\s*\d+')
MASKED_FIELDS = ("observed_at_ns",)
#: The probe pins the world's clock so the prompt a reviewer freezes is the
#: prompt a paid call sends. The world runs no events, so nothing in it depends
#: on time passing.
FIXED_NOW_NS = 1_800_000_000_000_000_000


def _mask(text: str) -> str:
    return FRESHNESS_STAMP.sub('"observed_at_ns": "<masked>"', text)


class ProbeRefused(RehearsalRefused):
    """A local guard refused the probe before any paid call was admitted."""


class NullProvider:
    """Render a prompt with no route to a completion, so freezing cannot spend."""

    name = "edition4-discovery-null"

    def catalogue(self) -> list:
        return []

    def balance_micro(self) -> None:
        return None

    def affordable(self, model_id: str, ceiling_micro: int) -> tuple[bool, str]:
        return True, ""

    def complete(self, request: Any) -> Any:
        raise ProbeRefused("null_provider_cannot_complete")


def request_digest(request: Any) -> str:
    """Digest exactly what a provider would receive: system, messages and settings."""
    return hashlib.sha256(canonical({
        "model_id": request.model_id,
        "system": _mask(request.system),
        "messages": [{key: _mask(value) if isinstance(value, str) else value
                      for key, value in dict(message).items()}
                     for message in request.messages],
        "max_tokens": request.max_tokens,
        "effort": request.effort,
        "json_object": request.json_object,
    })).hexdigest()


class RunJournal:
    """Durably record one probe run, from before its first dispatch to its end.

    Guarantees a run is started at most once per frozen preflight: the file is
    created exclusively, so a crashed or half-dispatched run leaves a marker that
    refuses the next attempt rather than letting it be bought again. Guarantees
    every record reaches the disk before the call it describes proceeds, so an
    attempt survives a crash that loses the summary.
    """

    def __init__(self, path: Path, start: dict[str, Any]):
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            self.handle = path.open("x", encoding="utf-8")
        except FileExistsError:
            raise ProbeRefused("run_already_started") from None
        self.path = path
        self.append({"event": "run_start", **start})

    def append(self, record: dict[str, Any]) -> None:
        self.handle.write(json.dumps({"at_utc": _utc(), **record},
                                     sort_keys=True, default=str) + "\n")
        self.handle.flush()
        os.fsync(self.handle.fileno())

    def close(self) -> None:
        self.handle.close()


class JournalingProvider:
    """Verify a seat's frozen initial request and journal every dispatch.

    Guarantees the first request of a seat is the exact rendered request the
    preflight froze, checked before the call is admitted or dispatched, so a
    prompt that drifted from what was reviewed buys nothing. Continuations are
    rendered from what the seat retrieved and cannot be frozen; their digests are
    journaled as they are sent.
    """

    name = "edition4-discovery-journal"

    def __init__(self, inner: Any, journal: RunJournal, expected: dict[str, str],
                 admission: Admission):
        self.inner = inner
        self.journal = journal
        self.expected = expected
        self.admission = admission
        self.pending_seat: str | None = None
        self.dispatched = 0

    def begin_seat(self, seat: str) -> None:
        """Declare that the next dispatch is that seat's frozen initial request."""
        self.pending_seat = seat

    def complete(self, request: Any) -> Any:
        digest = request_digest(request)
        seat, self.pending_seat = self.pending_seat, None
        if seat is not None and self.expected.get(seat) != digest:
            self.journal.append({"event": "dispatch_refused", "seat": seat,
                                 "reason": "rendered_request_mismatch",
                                 "request_sha256": digest,
                                 "frozen_sha256": self.expected.get(seat)})
            raise ProbeRefused("rendered_request_mismatch")
        self.dispatched += 1
        self.journal.append({"event": "attempt", "seat": seat, "initial": seat is not None,
                             "index": self.dispatched, "model_id": request.model_id,
                             "request_sha256": digest,
                             "admission": self.admission.report()})
        try:
            response = self.inner.complete(request)
        except BaseException as exc:
            self.journal.append({"event": "dispatch_failed", "index": self.dispatched,
                                 "request_sha256": digest, "error": _safe_exception(exc),
                                 "admission": self.admission.report()})
            raise
        self.journal.append({"event": "result", "index": self.dispatched,
                             "request_sha256": digest, "cost_micro": response.cost_micro,
                             "stop_reason": response.stop_reason,
                             "admission": self.admission.report()})
        return response

    def catalogue(self) -> Any:
        method = getattr(self.inner, "catalogue", None)
        return [] if method is None else method()

    def balance_micro(self) -> Any:
        method = getattr(self.inner, "balance_micro", None)
        return None if method is None else method()

    def __getattr__(self, name: str) -> Any:
        return getattr(self.inner, name)


def _utc() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _self_hash() -> str:
    return hashlib.sha256(Path(__file__).resolve().read_bytes()).hexdigest()


def effective_manifest(base: Any) -> Any:
    """Return the rehearsal roster under a compact prompt with its rails neutered.

    Guarantees the charter, the norms and the seed roster are the manifest's own:
    only the prompt mode, the world name and the treasury's rail configuration
    differ, and the exchange object this probe hands the runtime is a fake.
    """
    treasury = replace(base.treasury, reserve_address=None, cctp_forwarding="never",
                       hyperevm_gas_budget_wei=0, base_gas_budget_wei=0)
    manifest = replace(base, name=f"{base.name}-edition4-discovery-probe",
                       prompt=PromptSpec(mode="compact"), treasury=treasury)
    manifest.validate()
    return manifest


def restrict_tools(runtime: Runtime, refusals: list[dict], receipts: list[dict]) -> None:
    """Refuse every capability outside ALLOWED_TOOLS before it is dispatched.

    Guarantees no venue write, treasury transfer, paid fetch, web search, x402
    call or addressed message can leave this diagnostic: the refusal is returned
    in the shape a failed tool returns, so it costs nothing, earns no
    continuation, and reaches the seat as a refusal rather than as silence.
    """
    inner = runtime._run_tool

    def guarded(action_id: str, handle: str, call: dict, *, slot: Any) -> tuple[dict, int]:
        tool = str(call.get("tool"))
        if tool not in ALLOWED_TOOLS:
            refusals.append({"tool": tool, "assembly_id": action_id, "handle": handle})
            return {"error": "capability refused: diagnostic restriction"}, 0
        result, cost = inner(action_id, handle, call, slot=slot)
        if tool == "calc" and isinstance(result, dict):
            # The receipt's own value, kept so the answer can be checked against
            # what the tool returned rather than against a restatement of it.
            receipts.append({"tool": tool, "assembly_id": action_id, "handle": handle,
                             "notional_usd": result.get("notional_usd"),
                             "error": result.get("error")})
        return result, cost

    runtime._run_tool = guarded


def frozen_preflight(*, world: Path, seats: tuple[str, ...], cap_micro: int, max_calls: int,
                     source_root: Path | None) -> dict[str, Any]:
    """Every input that determines what is bought, digested before anything is spent."""
    base = load_manifest(str(world))
    manifest = effective_manifest(base)
    known = {assembly.id for assembly in manifest.assemblies}
    if [seat for seat in seats if seat not in known]:
        raise ProbeRefused("seat_not_in_roster")
    models = {assembly.id: assembly.model_id for assembly in manifest.assemblies
              if assembly.id in seats}
    imported_root, digest = source_hash(source_root or Path.cwd())
    share = seat_ceiling(cap_micro, seats)
    return {
        "source": {"root": imported_root, "sha256": digest, "runner_sha256": runner_hash(),
                   "probe_sha256": _self_hash()},
        "world": {"path": str(world), "original_hash": base.manifest_hash(),
                  "effective_hash": manifest.manifest_hash(),
                  "prompt_mode": manifest.prompt.mode},
        "seats": list(seats),
        "models": models,
        "task": TASK,
        "task_inputs": {"size": TASK_SIZE, "price": TASK_PRICE},
        "expected_value": EXPECTED_VALUE,
        "outcome_schema": OUTCOME_SCHEMA,
        "allowed_tools": sorted(ALLOWED_TOOLS),
        "cap_micro": cap_micro,
        "max_calls": max_calls,
        "seat_ceiling_micro": share,
        # The exact bytes each seat would be sent, rendered offline against a
        # provider that cannot complete. A continuation is rendered from what the
        # seat retrieved, so only the initial request can be frozen.
        "rendered_requests": rendered_requests(manifest, seats, share),
        "rendered_masked_fields": list(MASKED_FIELDS),
        "fixed_clock_ns": FIXED_NOW_NS,
    }


def seat_ceiling(cap_micro: int, seats: tuple[str, ...]) -> int:
    """The per-seat request ceiling, which the prompt itself carries and so is frozen."""
    return max(1, cap_micro // max(1, len(seats)))


def _probe_runtime(manifest: Any, provider: Any, refusals: list[dict],
                   receipts: list[dict]) -> Runtime:
    """One fresh world on a fake exchange, with every rail this probe denies removed."""
    runtime = Runtime(
        manifest, events=0, seed=manifest.seed, initial_balance_micro=None,
        ledger_path=None, drip=False, router_gamma=0.1, provider=provider,
        market=DeniedMarket(), exchange=FakeExchange(coins=manifest.exchange.coins),
    )
    runtime.clock.now_ns = FIXED_NOW_NS
    runtime.treasury.rail.target = DeniedTransferRail(runtime.treasury.rail.target)
    restrict_tools(runtime, refusals, receipts)
    return runtime


def rendered_requests(manifest: Any, seats: tuple[str, ...], ceiling: int) -> dict[str, str]:
    """Digest each seat's initial model request without a provider that could answer."""
    digests: dict[str, str] = {}
    for seat in seats:
        runtime = _probe_runtime(manifest, NullProvider(), [], [])
        request = _decision(runtime, seat, ceiling)
        digests[seat] = request_digest(runtime.assemblies[seat].build_model_request(request))
    return digests


def preflight_digest(preflight: dict[str, Any]) -> str:
    return hashlib.sha256(canonical(preflight)).hexdigest()


def _read_freeze(path: Path) -> dict[str, Any] | None:
    return json.loads(path.read_text()) if path.is_file() else None


def _spent_already(out: Path, digest: str) -> bool:
    """A frozen preflight buys one execution; a second is refused, never repeated."""
    if not out.is_file():
        return False
    for line in out.read_text().splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if row.get("preflight_digest") == digest and row.get("executed"):
            return True
    return False


def _append(out: Path | None, record: dict[str, Any]) -> None:
    if out is None:
        return
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, sort_keys=True, default=str) + "\n")


def _decision(runtime: Runtime, seat: str, ceiling_micro: int) -> Any:
    # A deadline this diagnostic cannot reach, measured from the world's own clock
    # rather than from a constant: rehearsal-5 runs on wall-clock nanoseconds.
    deadline = runtime.clock.now_ns + 24 * 3600 * 10**9
    handle = runtime.queue.open(
        actor=seat, event_id="discovery-probe",
        propensity=PropensityRecord((seat,), (1.0,), seat, 0, seat, "diagnostic"),
        channel=CH_VERDICT, deadline_ns=deadline, parent_handle=None,
        cost_ceiling=ceiling_micro,
    )
    runtime.consequences.start(handle, 0)
    request = runtime._request(
        handle, TASK,
        {"kind": "WorldUpdate", "payload": {}, "world": runtime._world_block()},
        OUTCOME_SCHEMA, deadline, CH_VERDICT,
    )
    return replace(request, cost_ceiling=ceiling_micro)


def _seat_result(runtime: Runtime, seat: str, ret: Any, refusals: list[dict],
                 receipts: list[dict], handle: str) -> dict[str, Any]:
    """Read the chain from receipts: what was looked up, what was called, what came back."""
    rows = [row for row in runtime.ledger._recovery_items()
            if row.get("kind") == "tool.call" and row.get("handle") == handle]
    discovery = [row for row in rows
                 if row["tool"] in ("catalogue.search", "world.read") and row["outcome"] == "ok"]
    invocation = [row for row in rows if row["tool"] == "calc" and row["outcome"] == "ok"]
    receipt_value = next((row["notional_usd"] for row in receipts if row["handle"] == handle),
                         None)
    answered = ret.outputs.get("notional_usd") if ret.status == "ok" else None
    return {
        "seat": seat,
        "model_id": runtime.assemblies[seat].spec.model_id,
        "handle": handle,
        "status": ret.status,
        "cost_micro": ret.cost,
        "tool_calls": [{"tool": row["tool"], "outcome": row["outcome"], "cost": row["cost"]}
                       for row in rows],
        "refused_calls": [row for row in refusals if row["handle"] == handle],
        "discovered": bool(discovery),
        "invoked": bool(invocation),
        "receipt_value": receipt_value,
        "answered_value": answered,
        "answer_matches_receipt": receipt_value is not None and answered == receipt_value,
        "answer_matches_expected": answered == EXPECTED_VALUE,
        "chain_complete": bool(discovery) and bool(invocation)
        and receipt_value == EXPECTED_VALUE and answered == EXPECTED_VALUE,
        "evidence_text": ret.outputs.get("evidence") if ret.status == "ok" else None,
        "dropped": list(ret.dropped),
    }


def _run_seats(manifest: Any, seats: tuple[str, ...], provider: JournalingProvider,
               admission: Admission, journal: RunJournal,
               ceiling: int) -> list[dict[str, Any]]:
    """Ask each seat the frozen question once, under one shared admission bound."""
    results: list[dict[str, Any]] = []
    for seat in seats:
        if admission.stop_reason is not None:
            results.append({"seat": seat, "status": "skipped", "reason": admission.stop_reason})
            journal.append({"event": "seat_skipped", "seat": seat,
                            "reason": admission.stop_reason})
            continue
        refusals: list[dict] = []
        receipts: list[dict] = []
        runtime = _probe_runtime(manifest, provider, refusals, receipts)
        request = _decision(runtime, seat, ceiling)
        journal.append({"event": "seat_start", "seat": seat, "handle": request.handle,
                        "cost_ceiling_micro": request.cost_ceiling})
        provider.begin_seat(seat)
        try:
            ret = runtime._invoke(seat, request, "producer")
        except Exception as exc:
            failure = {"seat": seat, "status": "exception", "error": _safe_exception(exc),
                       "refused_calls": refusals}
            results.append(failure)
            journal.append({"event": "seat_failed", **failure,
                            "admission": admission.report()})
            break
        result = _seat_result(runtime, seat, ret, refusals, receipts, request.handle)
        results.append(result)
        journal.append({"event": "seat_result", **result})
    return results


def _outcome(seats: tuple[str, ...], results: list[dict[str, Any]],
             admission: Admission) -> tuple[str, str | None]:
    """A run is completed only when every seat answered and the bills are known."""
    if len(results) != len(seats) or any(row.get("status") in ("exception", "skipped")
                                         for row in results):
        return "incomplete", "seat_did_not_complete"
    if any(row.get("status") == "failed" for row in results):
        # A failed return is a call this probe could not complete, not an answer.
        return "incomplete", "invocation_failed"
    if admission.stop_reason in UNCERTAIN_STOPS:
        return "incomplete", admission.stop_reason
    return "completed", None


def run_probe(
    *,
    world: Path = DEFAULT_WORLD,
    out: Path | None = DEFAULT_OUT,
    freeze: Path | None = DEFAULT_FREEZE,
    seats: tuple[str, ...] = DEFAULT_SEATS,
    cap_micro: int = DEFAULT_CAP_MICRO,
    max_calls: int = DEFAULT_MAX_CALLS,
    paid: bool = False,
    provider: Any | None = None,
    source_root: Path | None = None,
) -> dict[str, Any]:
    """Freeze the diagnostic, and run it only when a provider was explicitly supplied.

    Guarantees no paid provider is constructed unless paid is true, that a changed
    or already-spent preflight refuses before any call, and that an attempt record
    carrying the admission report is appended whatever happens.
    """
    if type(cap_micro) is not int or not 0 < cap_micro <= DEFAULT_CAP_MICRO:
        raise ValueError("cap_micro must be a positive integer of at most 1000000")
    if type(max_calls) is not int or not 0 < max_calls <= DEFAULT_MAX_CALLS:
        raise ValueError("max_calls must be a positive integer of at most 12")
    admission = Admission(cap_micro, max_calls)
    record: dict[str, Any] = {
        "kind": "edition4.discovery_probe",
        "at_utc": _utc(),
        "paid": bool(paid),
        "executed": False,
        "commission": COMMISSION_NOTE,
        "restriction": {"allowed_tools": sorted(ALLOWED_TOOLS),
                        "enforced": "diagnostic wrapper refuses before dispatch",
                        "denied_rails": ["venue", "treasury", "x402", "web", "address"],
                        "exchange": "FakeExchange"},
        "status": "refused",
    }
    journal = None
    try:
        preflight = frozen_preflight(world=world, seats=seats, cap_micro=cap_micro,
                                     max_calls=max_calls, source_root=source_root)
        digest = preflight_digest(preflight)
        record["preflight"] = preflight
        record["preflight_digest"] = digest
        stored = _read_freeze(freeze) if freeze is not None else None
        if stored is not None and stored.get("preflight_digest") != digest:
            raise ProbeRefused("preflight_changed")
        if out is not None and (paid or provider is not None) and _spent_already(out, digest):
            raise ProbeRefused("preflight_already_spent")
        if stored is None and freeze is not None:
            freeze.parent.mkdir(parents=True, exist_ok=True)
            freeze.write_text(json.dumps(
                {"preflight_digest": digest, "frozen_at_utc": _utc(), "preflight": preflight},
                indent=2, sort_keys=True) + "\n")
        if not paid and provider is None:
            record["status"] = "frozen"
            record["reason"] = "dry_preflight_no_provider"
            return record
        # The marker is created before a provider exists, so a crash between here
        # and the summary still refuses a second purchase of the same preflight.
        journal_path = _journal_path(out, digest)
        record["journal"] = str(journal_path)
        journal = RunJournal(journal_path, {"preflight_digest": digest, "paid": bool(paid),
                                            "seats": list(seats), "cap_micro": cap_micro,
                                            "max_calls": max_calls})
        manifest = effective_manifest(load_manifest(str(world)))
        if paid and provider is None:
            provider = build_prepaid_provider(manifest)
        prepaid = provider if isinstance(provider, PrepaidProvider) else PrepaidProvider(
            provider, manifest, admission)
        journaling = JournalingProvider(prepaid, journal, preflight["rendered_requests"],
                                        admission)
        record["executed"] = True
        record["seats"] = _run_seats(manifest, seats, journaling, admission, journal,
                                     preflight["seat_ceiling_micro"])
        record["status"], reason = _outcome(seats, record["seats"], admission)
        if reason is not None:
            record["reason"] = reason
        record["chain_complete_seats"] = [row["seat"] for row in record["seats"]
                                          if row.get("chain_complete")]
    except Exception as exc:
        record["status"] = "failed"
        record["error"] = _safe_exception(exc)
        record["reason"] = getattr(exc, "reason", None)
    finally:
        record["cost"] = admission.report()
        if journal is not None:
            journal.append({"event": "run_end", "status": record["status"],
                            "executed": record["executed"], "cost": admission.report()})
            journal.close()
        _append(out, record)
    return record


def _journal_path(out: Path | None, digest: str) -> Path:
    """One journal per frozen preflight, beside the attempt log it cannot depend on."""
    base = out if out is not None else DEFAULT_OUT
    return base.parent / f"{base.stem}.{digest[:16]}.journal.jsonl"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--world", type=Path, default=DEFAULT_WORLD)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--freeze", type=Path, default=DEFAULT_FREEZE)
    parser.add_argument("--seat", action="append", dest="seats", default=None)
    parser.add_argument("--cap-micro", type=int, default=DEFAULT_CAP_MICRO)
    parser.add_argument("--max-calls", type=int, default=DEFAULT_MAX_CALLS)
    parser.add_argument("--source-root", type=Path, default=None)
    parser.add_argument("--paid", action="store_true",
                        help="construct the prepaid providers and buy the frozen calls")
    args = parser.parse_args(argv)
    record = run_probe(
        world=args.world, out=args.out, freeze=args.freeze,
        seats=tuple(args.seats) if args.seats else DEFAULT_SEATS,
        cap_micro=args.cap_micro, max_calls=args.max_calls, paid=args.paid,
        source_root=args.source_root,
    )
    print(json.dumps({key: value for key, value in record.items() if key != "preflight"},
                     indent=2, sort_keys=True, default=str))
    return 0 if record["status"] in ("completed", "frozen") else 1


if __name__ == "__main__":  # pragma: no cover - CLI entry
    raise SystemExit(main())
