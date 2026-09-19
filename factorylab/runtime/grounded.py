"""Frozen contracts and public evidence for delayed producer evaluation."""

from __future__ import annotations

import hashlib
from collections.abc import Iterable, Mapping
from dataclasses import asdict, dataclass, replace
from typing import Any

from factorylab.kernel.ledger import canonical

GROUNDED_DEFINITION = "realized-consequence-v2"
UNKNOWN_REASON = "the frozen consequence horizon produced no assessable public evidence"


def _plain(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(k): _plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(v) for v in value]
    return value


@dataclass(frozen=True)
class GroundedContract:
    """One producer decision's interpretation and observation horizon, frozen ex ante."""

    handle: str
    producer_id: str
    opened_tick: int
    due_tick: int
    close_tick: int
    charter_edition: int
    criteria: tuple[dict[str, Any], ...]
    predicate_versions: tuple[tuple[str, int], ...]
    producer_outputs: dict[str, Any]
    event_cursor: int
    receipt_cursor: int
    initial_judge: str | None = None
    initial_evaluator: str | None = None
    forecast_handles: tuple[str, ...] = ()
    forecasts: tuple[dict[str, Any], ...] = ()
    final_requested: bool = False
    final_attempts: int = 0
    final_evaluators: tuple[str, ...] = ()
    # Added after the first realized-feedback checkpoints. An empty tuple means
    # that historical contract did not freeze norms; it never means current norms.
    norms: tuple[dict[str, str], ...] = ()

    def __post_init__(self) -> None:
        if not self.handle or not self.producer_id:
            raise ValueError("grounded contract needs decision and producer identities")
        if not 0 <= self.opened_tick < self.due_tick < self.close_tick:
            raise ValueError("grounded consequence ticks must be strictly ordered")
        object.__setattr__(self, "criteria", tuple(_plain(v) for v in self.criteria))
        object.__setattr__(self, "predicate_versions", tuple(self.predicate_versions))
        object.__setattr__(self, "producer_outputs", _plain(self.producer_outputs))
        object.__setattr__(self, "forecast_handles", tuple(self.forecast_handles))
        object.__setattr__(self, "forecasts", tuple(_plain(v) for v in self.forecasts))
        object.__setattr__(self, "final_evaluators", tuple(self.final_evaluators))
        object.__setattr__(self, "norms", tuple(_plain(v) for v in self.norms))

    def with_initial(
        self, *, judge_handle: str, evaluator_id: str, forecast_handles: Iterable[str],
        forecasts: Iterable[Mapping] = (),
    ) -> GroundedContract:
        """Attach the independently sampled first interpretation without changing its horizon."""
        return replace(self, initial_judge=judge_handle, initial_evaluator=evaluator_id,
                       forecast_handles=tuple(forecast_handles),
                       forecasts=tuple(dict(v) for v in forecasts))

    def requested(self) -> GroundedContract:
        """Mark the final commission as emitted; retries retain one request."""
        return replace(self, final_requested=True, final_attempts=self.final_attempts + 1)

    def retry_after(self, evaluator_id: str) -> GroundedContract:
        """Allow one later commission while excluding a judge whose answer was unusable."""
        evaluators = tuple(dict.fromkeys((*self.final_evaluators, evaluator_id)))
        return replace(self, final_requested=False, final_evaluators=evaluators)

    def with_outputs(self, outputs: Mapping) -> GroundedContract:
        """Attach the public returned claim without moving its pre-action baseline."""
        return replace(self, producer_outputs=dict(outputs))


def freeze_contract(
    runtime: Any, handle: str, producer_id: str, outputs: Mapping
) -> GroundedContract:
    """Freeze charter norms, pricing constraints and predicates before later evidence exists."""
    norms = tuple({"id": norm.id, "definition": norm.definition}
                  for norm in runtime.charter.norms)
    cards = tuple({
        "id": card.id,
        "norm": card.norm,
        "description": card.description,
        "acceptable_region": card.acceptable_region,
        "observation": card.observation,
        "answers_for": card.answers_for,
    } for card in runtime.charter.cards)
    predicates = tuple((predicate.id, predicate.version) for predicate in runtime.predicates.all())
    opened = runtime.ticks_consumed
    horizon = max(1, int(runtime.ev.forecast_horizon_events))
    close_delay = max(horizon + 1, int(runtime.ev.verdict_timeout_ticks))
    return GroundedContract(
        handle=handle, producer_id=producer_id, opened_tick=opened,
        due_tick=opened + horizon, close_tick=opened + horizon + close_delay,
        charter_edition=runtime.charter.edition, criteria=cards,
        predicate_versions=predicates, producer_outputs=dict(outputs),
        event_cursor=len(runtime.events_log),
        receipt_cursor=runtime.consequences.receipts.execution_count(),
        norms=norms,
    )


def public_evidence(runtime: Any, contract: GroundedContract) -> list[dict[str, Any]]:
    """Return only public, attributable facts after the frozen baseline, with stable references."""
    evidence: list[dict[str, Any]] = []
    try:
        payoff = runtime.consequences.payoff(contract.handle)
    except KeyError:
        payoff = None
    if payoff is not None:
        payload = {
            **_plain(asdict(payoff)),
            "status": "censored_unknown" if payoff.censored is not None else "fixed",
            "observed": payoff.censored is None,
            "amount_unit": "micro_usd",
            "net_is_signed": True,
            "cash_realized": payoff.censored is None and not payoff.marked,
            "fill_is_profit": False,
            "y_is_observation": payoff.censored is None,
        }
        digest = hashlib.sha256(canonical(payload)).hexdigest()
        evidence.append({
            "ref": f"economic-outcome:{digest}",
            "kind": "EconomicOutcome",
            "payload": payload,
        })
    receipts = runtime.consequences.receipts.executions_since(
        contract.handle, contract.receipt_cursor)
    for receipt in receipts:
        evidence.append({
            "ref": f"execution:{receipt.id}",
            "kind": f"ExecutionReceipt:{receipt.kind}",
            "payload": _plain(asdict(receipt)),
        })
    forecast_handles = set(contract.forecast_handles)
    for index, row in enumerate(runtime.events_log[contract.event_cursor:], contract.event_cursor):
        payload = row.get("payload") if isinstance(row, dict) else None
        if not isinstance(payload, Mapping):
            continue
        kind = str(row.get("kind", ""))
        # A resolved, ex-ante forecast is a public observation. Producer returns,
        # registrations and verdicts are claims or opinions and cannot ground themselves.
        if kind != "ForecastSettled" or payload.get("handle") not in forecast_handles:
            continue
        # Private working state never enters events_log; copy again so the judge cannot
        # mutate checkpointed evidence through a provider adapter.
        evidence.append({"ref": f"event:{index}", "kind": kind, "payload": _plain(payload)})
    return evidence


def observed_evidence_refs(evidence: Any) -> set[str]:
    """Name supplied facts that independently observed an effect rather than its absence."""
    if not isinstance(evidence, (list, tuple)):
        return set()
    observed = set()
    for row in evidence:
        if not isinstance(row, Mapping) or not isinstance(row.get("ref"), str):
            continue
        kind = row.get("kind")
        payload = row.get("payload")
        if not isinstance(payload, Mapping):
            continue
        eligible = str(kind).startswith("ExecutionReceipt:")
        if kind == "EconomicOutcome":
            eligible = (
                payload.get("status") == "fixed"
                and payload.get("observed") is True
                and payload.get("censored") is None
            )
        elif kind == "ForecastSettled":
            eligible = (
                payload.get("y") is not None
                and payload.get("status", "settled") == "settled"
            )
        if eligible:
            observed.add(row["ref"])
    return observed


def parse_finding(
    raw: Any, available_refs: set[str], observed_refs: set[str] | None = None,
) -> tuple[str, float | None, tuple[str, ...], str]:
    """Validate a final judge's grounded finding against evidence actually supplied."""
    if not isinstance(raw, Mapping):
        raise ValueError("realized_consequence must be an object")
    status = raw.get("status")
    if status not in ("supported", "contrary", "unknown"):
        raise ValueError("realized consequence status is supported, contrary or unknown")
    reason = raw.get("reason")
    if not isinstance(reason, str) or not reason.strip():
        raise ValueError("realized consequence finding needs a reason")
    refs = raw.get("evidence", [])
    if not isinstance(refs, list) or any(not isinstance(ref, str) for ref in refs):
        raise ValueError("realized consequence evidence must be reference strings")
    refs = tuple(dict.fromkeys(refs))
    if any(ref not in available_refs for ref in refs):
        raise ValueError("realized consequence cites evidence outside its commission")
    score = raw.get("score")
    if status == "unknown":
        if score is not None:
            raise ValueError("unknown consequence carries no numeric score")
        return status, None, refs, reason.strip()
    if not refs:
        raise ValueError("an observed consequence must cite supplied evidence")
    eligible = available_refs if observed_refs is None else observed_refs
    if not set(refs) & eligible:
        raise ValueError("numeric consequence needs cited independently observed evidence")
    if status == "contrary":
        if score not in (None, 0, 0.0):
            raise ValueError("contrary consequence has score zero")
        return status, 0.0, refs, reason.strip()
    if isinstance(score, bool) or not isinstance(score, (int, float)) or not 0 < score <= 1:
        raise ValueError("supported consequence score must be in (0, 1]")
    return status, float(score), refs, reason.strip()
