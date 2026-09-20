"""Build a read-only, sealed-diary-compatible Edition 4 rehearsal report.

The report accepts only explicitly named JSON exports.  It aggregates whitelisted
facts and references; it never copies a ledger payload or starts a world.  Missing
receipts remain ``unknown`` so that activity cannot be mistaken for usefulness,
causality, or external income.
"""

from __future__ import annotations

import argparse
import html
import json
import math
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any

KNOWN_STATUSES = (
    "attempted", "accepted", "executed", "settled", "refused", "unknown",
    "uncertain", "failed", "malformed",
)
QUESTION_NAMES = (
    "subsequent_consumption",
    "resolved_evidence_changed_decision",
    "criterion_changed_costly_action",
    "exploration_reached_horizon",
    "external_income_funded_operation",
)
_CONTAINER_KEYS = ("rows", "records", "events", "calls", "receipts", "items")
_BODY_KEYS = frozenset(
    {
        "body",
        "content",
        "message",
        "messages",
        "payload",
        "prompt",
        "response",
        "text",
        "private_memory",
        "reasoning",
    }
)
_INVOCATION_KINDS = frozenset(("invocation",))
_PROVIDER_CALL_KINDS = frozenset(
    (
        "provider.complete", "provider.call", "model.complete", "model.call",
        "model_call", "completion", "call",
    )
)
_STATUS_FAMILY_EXACT = frozenset(
    {
        "invocation", "message", "budget", "income", "revenue", "earning", "order",
        "decision", "consequence", "receipt",
    }
)
_STATUS_FAMILY_PREFIXES = (
    "provider.", "model.", "io.", "address.", "order.", "decision.", "consequence.",
    "receipt.", "forecast.", "learning.", "income.", "revenue.", "wallet.",
)


class ReportInputError(ValueError):
    """Raised when an explicitly supplied report export cannot be read safely."""


def _as_rows(value: Any) -> list[dict[str, Any]]:
    """Extract rows from one export without recursively scanning unrelated objects."""
    if isinstance(value, list):
        return [row for row in value if isinstance(row, dict)]
    if not isinstance(value, dict):
        raise ReportInputError("JSON export must be an object or a list of row objects")
    for key in _CONTAINER_KEYS:
        rows = value.get(key)
        if isinstance(rows, list):
            return [row for row in rows if isinstance(row, dict)]
    # The rehearsal runner's report.json is an explicit aggregate export.  Keep
    # only its cost summary as a synthetic fact; never recursively scan summary
    # or world details for pseudo-events.
    if isinstance(value.get("cost"), dict):
        return [{"kind": "report.summary", "summary_cost": value["cost"]}]
    # A summary export may contain one aggregate row.  Its nested candidate/tree
    # details are not ledger rows and are deliberately not broad-scanned.
    return [value]


def load_rows(paths: Sequence[str | Path]) -> list[dict[str, Any]]:
    """Read only the explicitly named JSON files and return their top-level rows."""
    if not paths:
        raise ReportInputError("at least one explicit --input path is required")
    result: list[dict[str, Any]] = []
    for supplied in paths:
        path = Path(supplied)
        if path.is_dir():
            raise ReportInputError(f"input must be a file, not a directory: {path}")
        if not path.exists():
            raise ReportInputError(f"input does not exist: {path}")
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ReportInputError(f"cannot read JSON export {path}: {type(exc).__name__}") from exc
        for row in _as_rows(value):
            # Keep the path as provenance, but never retain an untrusted body.
            clean = {key: item for key, item in row.items() if key not in _BODY_KEYS}
            clean["_source"] = str(path)
            result.append(clean)
    return result


def _value(row: Mapping[str, Any], *keys: str) -> Any:
    """Return a direct, whitelisted fact from a row."""
    for key in keys:
        if key in row:
            return row[key]
    receipt = row.get("receipt")
    if isinstance(receipt, Mapping):
        for key in keys:
            if key in receipt:
                return receipt[key]
        facts = receipt.get("facts")
        if isinstance(facts, Mapping):
            for key in keys:
                if key in facts:
                    return facts[key]
    return None


def _truth(row: Mapping[str, Any], *keys: str) -> bool | None:
    value = _value(row, *keys)
    return value if type(value) is bool else None


def _ref(row: Mapping[str, Any]) -> str | None:
    value = _value(row, "handle", "ref", "reference", "id", "decision_id", "call_id")
    if value is None or not isinstance(value, (str, int)):
        return None
    return str(value)


def _kind(row: Mapping[str, Any]) -> str:
    value = _value(row, "kind", "op", "type", "event")
    return str(value).lower() if value is not None else "unknown"


def _receipt_kind(row: Mapping[str, Any]) -> str | None:
    """Return an execution receipt's inner kind without exposing its facts."""
    if _kind(row) != "receipt.execution":
        return None
    receipt = row.get("receipt")
    value = receipt.get("kind") if isinstance(receipt, Mapping) else None
    return str(value).lower() if value is not None else None


def _event_info(row: Mapping[str, Any]) -> tuple[str | None, Mapping[str, Any] | None]:
    """Read the public kind/payload envelope used by the ledger event bus."""
    if _kind(row) != "event":
        return None, None
    event = row.get("event")
    if not isinstance(event, Mapping):
        return None, None
    payload = event.get("payload")
    return str(event.get("kind")) if event.get("kind") is not None else None, (
        payload if isinstance(payload, Mapping) else None
    )


def _event_ref(row: Mapping[str, Any], index: int) -> str:
    """Return a stable provenance label without copying an event payload."""
    seq = row.get("seq")
    if type(seq) is int:
        return f"event:{seq}"
    event = row.get("event")
    if isinstance(event, Mapping) and isinstance(event.get("id"), (str, int)):
        return f"event:{event['id']}"
    return f"event:{index}"


def _status(row: Mapping[str, Any]) -> str:
    value = _value(row, "status", "state", "outcome")
    if value is None:
        return "unknown"
    value = str(value).lower().strip()
    if value in KNOWN_STATUSES:
        return value
    if value in {"delivered", "complete", "completed", "ok", "success", "succeeded"}:
        return "executed"
    return "unknown"


def _explicit_status(row: Mapping[str, Any]) -> str | None:
    """Normalize an explicit action status without treating an absent field as uncertainty."""
    value = _value(row, "status", "state", "outcome")
    if value is None:
        return None
    if not isinstance(value, str):
        return "malformed"
    value = value.lower().strip()
    if value in KNOWN_STATUSES:
        return value
    if value in {"delivered", "complete", "completed", "ok", "success", "succeeded"}:
        return "executed"
    if value in {"error", "failure"}:
        return "failed"
    return "malformed"


def _cost(row: Mapping[str, Any]) -> int | None:
    """Return an actual integer micro-USD bill; absent and non-integer bills are unknown."""
    value = _value(row, "actual_cost_micro", "billed_micro", "cost_micro", "cost_usd_micro")
    if value is None and type(_value(row, "cost")) is int:
        value = _value(row, "cost")
    return value if type(value) is int and value >= 0 else None


def _amount_micro(row: Mapping[str, Any]) -> int | None:
    value = _value(row, "amount_micro", "value_micro", "income_micro", "amount")
    return value if type(value) is int and value >= 0 else None


def _percentile(values: list[int], percentile: float) -> int | None:
    if not values:
        return None
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, math.ceil(len(ordered) * percentile) - 1))
    return ordered[index]


def _call_rows(rows: Iterable[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    """Return one row per provider attempt, preferring explicit provider journals."""
    provider = [row for row in rows if _kind(row) in _PROVIDER_CALL_KINDS]
    if provider:
        return provider + [row for row in rows if _kind(row) == "metering.uncertain"]
    return [
        row
        for row in rows
        if _kind(row) in _INVOCATION_KINDS
        or _kind(row) == "metering.uncertain"
    ]


def _invocation_rows(rows: Iterable[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    """Return inclusive invocation rows used for root decision-tree accounting."""
    return [row for row in rows if _kind(row) in _INVOCATION_KINDS]


def _summary_cost(rows: Iterable[Mapping[str, Any]]) -> Mapping[str, Any] | None:
    for row in rows:
        summary = _value(row, "summary_cost")
        if isinstance(summary, Mapping):
            return summary
    return None


def _cost_report(rows: list[Mapping[str, Any]]) -> dict[str, Any]:
    calls = _call_rows(rows)
    invocations = _invocation_rows(rows)
    provider_journal = any(_kind(row) in _PROVIDER_CALL_KINDS for row in rows)
    uncertain_metering = sum(_kind(row) == "metering.uncertain" for row in rows)
    summary = _summary_cost(rows)
    if not calls and summary is not None:
        known_calls = summary.get("known_calls")
        unknown_calls = summary.get("uncertain_calls")
        known_micro = summary.get("known_micro")
        return {
            "basis": "call_only",
            "label": "call-only; root linkage unavailable",
            "call_cost_basis": "summary_only",
            "call_cost_label": "summary totals; p50/p90 unavailable",
            "root_cost_basis": "unavailable",
            "calls": summary.get("attempted"),
            "known_bill_count": known_calls,
            "unknown_bill_count": unknown_calls,
            "known_unit_count": known_calls,
            "unknown_unit_count": unknown_calls,
            "known_micro_total": known_micro,
            "call_p50_micro": None,
            "call_p90_micro": None,
            "root_p50_micro": None,
            "root_p90_micro": None,
            "p50_micro": None,
            "p90_micro": None,
            "unlinked_call_count": None,
            "root_count": None,
            "roots_with_unknown_bills": None,
            "root_known_micro": None,
        }
    known = [cost for row in calls if (cost := _cost(row)) is not None]
    unknown = len(calls) - len(known)
    parents: dict[str, str] = {}
    for row in rows:
        if _kind(row) != "request.child":
            continue
        child = _value(row, "handle", "child_handle")
        parent = _value(row, "parent_handle", "resource_liability", "parent_id")
        if isinstance(child, (str, int)) and isinstance(parent, (str, int)):
            parents[str(child)] = str(parent)

    def root_for(row: Mapping[str, Any]) -> str | None:
        explicit = _value(row, "root_id", "root_handle", "decision_root", "tree_id")
        handle = _value(row, "handle", "id", "call_id")
        current = str(explicit) if explicit not in (None, "") else None
        if current is None and isinstance(handle, (str, int)):
            handle = str(handle)
            if handle in parents or handle in parents.values():
                current = handle
        seen: set[str] = set()
        while current in parents and current not in seen:
            seen.add(current)
            current = parents[current]
        return current

    roots = [root_for(row) for row in invocations]
    linked = [str(root) for root in roots if root not in (None, "")]
    basis = "root_decision_trees" if linked else "call_only"
    tree_costs: dict[str, int] = defaultdict(int)
    tree_unknown: Counter[str] = Counter()
    linked_root_rows: set[str] = set()
    unlinked_costs: list[int] = []
    unlinked_unknown = 0
    if linked:
        for row, root in zip(invocations, roots, strict=False):
            if root in (None, ""):
                cost = _cost(row)
                if cost is None:
                    unlinked_unknown += 1
                else:
                    unlinked_costs.append(cost)
                continue
            cost = _cost(row)
            handle = _value(row, "handle", "id", "call_id")
            is_root_invocation = handle is not None and str(handle) == str(root)
            if not is_root_invocation:
                continue
            linked_root_rows.add(str(root))
            if cost is None:
                tree_unknown[str(root)] += 1
            else:
                tree_costs[str(root)] += cost
        for root in set(linked) - linked_root_rows:
            tree_unknown[root] += 1
    unit_costs = known
    unknown_units = unknown
    call_p50 = _percentile(known, 0.50)
    call_p90 = _percentile(known, 0.90)
    root_p50 = None
    root_p90 = None
    if linked:
        unit_costs = [
            cost for root, cost in tree_costs.items() if root not in tree_unknown
        ] + unlinked_costs
        unknown_units = (
            len(set(tree_unknown)) + unlinked_unknown + uncertain_metering
        )
        root_p50 = _percentile(unit_costs, 0.50)
        root_p90 = _percentile(unit_costs, 0.90)
    summary_known = summary.get("known_calls") if summary is not None else None
    summary_unknown = summary.get("uncertain_calls") if summary is not None else None
    summary_total = summary.get("known_micro") if summary is not None else None
    if type(summary_total) is int:
        known_micro_total = summary_total
    elif provider_journal:
        known_micro_total = sum(known) if known else None
    elif linked:
        # Return.cost is inclusive of child invocations.  Linked trees contribute
        # only their roots; unlinked top-level invocations still contribute once.
        known_micro_total = sum(unit_costs) if unit_costs else None
    else:
        known_micro_total = sum(known) if known else None
    fallback_known_count = len(unit_costs) if linked and not provider_journal else len(known)
    fallback_unknown_count = unknown_units if linked and not provider_journal else unknown
    return {
        "basis": basis,
        "label": (
            "root decision trees"
            if basis == "root_decision_trees"
            else "call-only; root linkage unavailable"
        ),
        "calls": summary.get("attempted", len(calls)) if summary is not None else len(calls),
        "known_bill_count": (
            summary_known if type(summary_known) is int else fallback_known_count
        ),
        "unknown_bill_count": (
            summary_unknown if type(summary_unknown) is int else fallback_unknown_count
        ),
        "known_unit_count": len(unit_costs),
        "unknown_unit_count": unknown_units,
        "known_micro_total": known_micro_total,
        "call_p50_micro": call_p50,
        "call_p90_micro": call_p90,
        "call_cost_basis": "provider_complete" if provider_journal else "invocation_inclusive",
        "call_cost_label": (
            "provider-complete journal"
            if provider_journal
            else "invocation-only; inclusive of tools and children"
        ),
        "root_p50_micro": root_p50,
        "root_p90_micro": root_p90,
        "root_cost_basis": "root_invocation_inclusive" if linked else "unavailable",
        "p50_micro": root_p50 if linked else call_p50,
        "p90_micro": root_p90 if linked else call_p90,
        "unlinked_call_count": sum(root is None for root in roots),
        "orphan_child_count": sum(
            root is not None
            and _value(row, "handle", "id", "call_id") is not None
            and str(_value(row, "handle", "id", "call_id")) != str(root)
            for row, root in zip(invocations, roots, strict=False)
        ),
        "root_count": len(set(linked)) if linked else None,
        "roots_with_unknown_bills": sorted(tree_unknown) if linked else None,
        "root_known_micro": dict(sorted(tree_costs.items())) if linked else None,
    }


def _status_report(rows: Iterable[Mapping[str, Any]]) -> dict[str, int]:
    """Count only explicit statuses on action/outcome rows.

    Routine I/O, forecasts, and bookkeeping without a status are not economic
    uncertainty.  Their missing status is omitted; an explicit ``unknown`` or
    malformed status remains visible as such.
    """
    result = {status: 0 for status in KNOWN_STATUSES}
    for row in rows:
        if _kind(row) == "report.summary":
            continue
        has_status = any(key in row for key in ("status", "state", "outcome"))
        receipt = row.get("receipt")
        facts = receipt.get("facts") if isinstance(receipt, Mapping) else None
        has_status |= isinstance(receipt, Mapping) and any(
            key in receipt for key in ("status", "state", "outcome")
        )
        has_status |= isinstance(facts, Mapping) and any(
            key in facts for key in ("status", "state", "outcome")
        )
        if not has_status:
            continue
        kind = _kind(row)
        if kind not in _STATUS_FAMILY_EXACT and not kind.startswith(_STATUS_FAMILY_PREFIXES):
            continue
        status = _explicit_status(row)
        if status is not None:
            result[status] += 1
    return result


def _internal_metered_report(rows: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    """Report wallet-side commits separately from provider-complete evidence."""
    commits = [row for row in rows if _kind(row) == "wallet.commit"]
    basis = "wallet.commit"
    if not commits:
        commits = [
            row for row in rows
            if _kind(row) == "budget" and _value(row, "op") == "commit"
        ]
        basis = "budget.commit" if commits else "unavailable"
    amounts = [
        amount for row in commits
        if (amount := _value(row, "amount", "actual_micro", "cost_micro"))
        is not None and type(amount) is int and amount >= 0
    ]
    return {
        "basis": basis,
        "rows": len(commits),
        "known_micro_total": sum(amounts) if amounts else None,
        "unknown_amount_rows": len(commits) - len(amounts),
    }


def _message_report(
    rows: Iterable[Mapping[str, Any]], address_enabled: bool | None = None
) -> dict[str, Any]:
    messages = [
        row for row in rows
        if "message" in _kind(row)
        or _kind(row) in {"address.delivered", "address.refused", "address.replayed"}
    ]
    delivered = [
        row for row in messages
        if _kind(row) == "address.delivered"
        or ("message" in _kind(row) and _truth(row, "delivered") is True)
        or ("message" in _kind(row) and _status(row) == "executed")
    ]
    replayed = [row for row in messages if _kind(row) == "address.replayed"]
    refused = [row for row in messages if _kind(row) == "address.refused"]
    explicit_unknown = [
        row for row in messages
        if _kind(row) not in {"address.delivered", "address.refused", "address.replayed"}
        and _truth(row, "delivered") is None and _status(row) == "unknown"
    ]
    if address_enabled is False:
        capability_status = "disabled"
        capability_label = "address capability not published in this run"
    elif address_enabled is True and not messages:
        capability_status = "enabled_but_unused"
        capability_label = (
            "address capability published but no use observed in the supplied/recent "
            "evidence window"
        )
    elif address_enabled is True:
        capability_status = "enabled_and_used"
        capability_label = "address capability published and use observed"
    elif messages:
        capability_status = "observed_use_metadata_unknown"
        capability_label = "address use observed; configuration metadata unavailable"
    else:
        capability_status = "unknown_metadata"
        capability_label = "address configuration metadata unavailable; zero is not non-use"
    return {
        "attempted": len(messages),
        "delivered": len(delivered),
        "replayed": len(replayed),
        "refused": len(refused),
        "unknown_delivery": len(messages) - len(delivered) - len(replayed) - len(refused),
        "refs": [_ref(row) for row in messages if _ref(row) is not None],
        "delivery_evidence_refs": [_ref(row) for row in delivered if _ref(row) is not None],
        "explicit_unknown_refs": [_ref(row) for row in explicit_unknown if _ref(row) is not None],
        "capability_status": capability_status,
        "capability_label": capability_label,
    }


def _reusable_report(rows: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    explicit = [
        row for row in rows
        if _truth(row, "reusable", "is_reusable", "reusable_call") is True
        and "message" not in _kind(row)
    ]
    receipts = [
        row for row in rows
        if _receipt_kind(row) == "program_result"
        and _value(row, "lineage_relation") in {"self", "same_lineage", "cross_lineage"}
    ]
    # The maker and caller each receive a receipt for one execution.  Facts and
    # hashes identify that execution, so count it once when both rows are present.
    seen_receipts: set[tuple[Any, ...]] = set()
    reusable = []
    for row in [*explicit, *receipts]:
        is_receipt = _receipt_kind(row) == "program_result"
        key = (
            _value(row, "tool"), _value(row, "maker_handle"), _value(row, "caller_handle"),
            _value(row, "version_sha256"), _value(row, "result_sha256"),
        )
        if is_receipt and key in seen_receipts:
            continue
        if is_receipt:
            seen_receipts.add(key)
        reusable.append(row)
    same: list[str] = []
    cross: list[str] = []
    unknown = 0
    for row in reusable:
        relation = str(_value(row, "lineage_relation", "relation") or "").lower()
        sender = _value(row, "sender_lineage", "source_lineage", "lineage")
        recipient = _value(row, "recipient_lineage", "target_lineage")
        ref = _ref(row)
        if relation in {"same", "same_lineage", "same-lineage"} or (
            sender is not None and recipient is not None and sender == recipient
        ):
            if ref is not None:
                same.append(ref)
        elif relation in {"cross", "cross_lineage", "cross-lineage"} or (
            sender is not None and recipient is not None and sender != recipient
        ):
            if ref is not None:
                cross.append(ref)
        else:
            unknown += 1
    return {"same_lineage_refs": same, "cross_lineage_refs": cross, "unknown_relation": unknown}


def _forecast_learning(rows: Iterable[Mapping[str, Any]]) -> dict[str, dict[str, int]]:
    result = {
        "forecast": {"settled_observed": 0, "unmeasured": 0},
        "learning": {"settled_observed": 0, "unmeasured": 0},
    }
    for row in rows:
        event_kind, payload = _event_info(row)
        if event_kind == "ForecastSettled" and payload is not None:
            state = "settled_observed" if payload.get("y") is not None else "unmeasured"
            result["forecast"][state] += 1
            continue
        kind = _kind(row)
        target = "forecast" if "forecast" in kind else "learning" if "learn" in kind else None
        if target is None:
            continue
        observed = _truth(row, "observed", "evidence_observed", "resolved") is True
        settled = _status(row) == "settled"
        result[target]["settled_observed" if settled and observed else "unmeasured"] += 1
    return result


def _assessment_changes(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Compare an initial Verdict with its later consequence finding by handle.

    The output is an observed sequence with provenance.  It deliberately does not
    claim that the evidence caused the changed assessment.
    """
    initial: dict[str, tuple[float, str]] = {}
    grounded: dict[str, str] = {}
    findings: dict[str, tuple[float | None, str, str]] = {}
    for index, row in enumerate(rows):
        event_kind, payload = _event_info(row)
        if event_kind == "Verdict" and payload is not None:
            about = payload.get("about_handle") or payload.get("about")
            score = payload.get("verdict")
            if isinstance(about, (str, int)) and type(score) in (int, float):
                ref = _event_ref(row, index)
                if payload.get("grounded_consequence") is True:
                    grounded[str(about)] = ref
                elif str(about) not in initial:
                    initial[str(about)] = (float(score), ref)
        if _kind(row) == "consequence.finding":
            about = _value(row, "handle", "about_handle")
            if isinstance(about, (str, int)):
                score = _value(row, "score")
                numeric = float(score) if type(score) in (int, float) else None
                ref = _ref(row) or f"finding:{about}"
                findings[str(about)] = (numeric, ref, str(_value(row, "status") or "unknown"))
    changes: list[dict[str, Any]] = []
    for about, (before, initial_ref) in initial.items():
        finding = findings.get(about)
        if finding is None or finding[0] is None:
            continue
        after, finding_ref, status = finding
        changes.append({
            "about_handle": about,
            "initial_verdict": before,
            "consequence_score": after,
            "changed": before != after,
            "finding_status": status,
            "initial_grounded": False,
            "grounded_final": about in grounded,
            "evidence_refs": [initial_ref, finding_ref],
            "grounded_final_ref": grounded.get(about),
            "interpretation": (
                "assessment changed after evidence; not causal proof"
                if before != after
                else "assessment unchanged after evidence; not causal proof"
            ),
        })
    return changes


def _funded_children(rows: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    children = [
        row
        for row in rows
        if _truth(row, "child", "is_child") is True
        or _value(row, "parent_id", "parent_handle") is not None
    ]
    funded = [
        row for row in rows
        if _kind(row) == "budget"
        and _value(row, "op") == "transfer"
        and str(_value(row, "reason") or "") == "trial:assembly"
    ]
    funded += [row for row in children if _truth(row, "funded", "funding_confirmed") is True]
    child_refs = []
    for row in funded:
        ref = _ref(row)
        if ref is None and _kind(row) == "budget":
            destination = _value(row, "dst")
            ref = str(destination) if isinstance(destination, (str, int)) else None
        if ref is not None:
            child_refs.append(ref)
    child_count = max(len(children), len(funded))
    return {
        "children": child_count,
        "funded_confirmed": len(funded),
        "funded_refs": child_refs,
        "funding_unknown": max(0, child_count - len(funded)),
    }


def _income_report(rows: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    buckets = {"external_confirmed": [], "internal": [], "funding_injection": [], "unknown": []}
    amounts = {key: [] for key in buckets}
    counts = {key: 0 for key in buckets}
    unverified: list[str] = []
    seen_external: set[str] = set()
    for row in rows:
        kind = _kind(row)
        source = str(_value(row, "source", "income_source", "provenance") or "").lower()
        reason = str(_value(row, "reason") or "").lower()
        operation = str(_value(row, "op") or "").lower()
        if kind == "budget" and operation == "income":
            ref = _ref(row)
            unverified.append(ref or "unreferenced")
            continue
        if "income" not in kind and "revenue" not in kind and "earn" not in kind:
            if kind not in {"wallet.settle", "wallet.initial", "wallet.drip"}:
                continue
        if kind == "wallet.settle" and reason == "income":
            source = "external"
        elif kind == "wallet.settle" and reason == "funding":
            source = "funding"
        elif kind in {"wallet.initial", "wallet.drip"}:
            source = "funding"
        confirmed = (
            _truth(row, "confirmed", "externally_confirmed") is True
            or _status(row) == "settled"
            or (kind in {"wallet.settle", "budget"} and reason == "income")
        )
        if (
            source in {"external", "customer", "service", "x402", "seller"}
            and confirmed
            and (_amount_micro(row) or 0) > 0
        ):
            bucket = "external_confirmed"
        elif source in {"internal", "wash", "participant", "commons"}:
            bucket = "internal"
        elif source in {"funding", "injection", "principal", "endowment", "founder"}:
            bucket = "funding_injection"
        else:
            bucket = "unknown"
        identity = _ref(row)
        if bucket == "external_confirmed" and identity is not None:
            if identity in seen_external:
                continue
            seen_external.add(identity)
        counts[bucket] += 1
        ref = _ref(row)
        if ref is not None:
            buckets[bucket].append(ref)
        elif bucket == "unknown":
            buckets[bucket].append(None)
        amount = _amount_micro(row)
        if amount is not None:
            amounts[bucket].append(amount)
    return {
        "external_confirmed_refs": buckets["external_confirmed"],
        "external_confirmed_count": counts["external_confirmed"],
        "external_confirmed_micro": (
            sum(amounts["external_confirmed"]) if amounts["external_confirmed"] else None
        ),
        "internal_refs": buckets["internal"],
        "internal_count": counts["internal"],
        "internal_micro": sum(amounts["internal"]) if amounts["internal"] else None,
        "funding_injection_refs": buckets["funding_injection"],
        "funding_injection_count": counts["funding_injection"],
        "funding_injection_micro": (
            sum(amounts["funding_injection"]) if amounts["funding_injection"] else None
        ),
        "unknown_provenance": len(buckets["unknown"]) + len(unverified),
        "unverified_records": unverified,
    }


def _question(status: str, refs: list[str], reason: str) -> dict[str, Any]:
    return {"status": status, "evidence_refs": refs, "reason": reason}


def _cost_per_useful_decision(
    rows: list[Mapping[str, Any]], costs: Mapping[str, Any]
) -> int | None:
    """Return a cost only when each qualifying outcome has explicit independent support."""
    useful = [
        row
        for row in rows
        if _truth(row, "useful_decision", "useful_outcome") is True
        and _truth(row, "independently_supported", "outcome_supported") is True
    ]
    if not useful or costs["unknown_bill_count"] or costs["known_micro_total"] is None:
        return None
    return costs["known_micro_total"] // len(useful)


def _questions(
    rows: list[Mapping[str, Any]], income: dict[str, Any], assessments: list[dict[str, Any]],
    reusable: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    result = {
        name: _question("unknown", [], "no independently resolved evidence supplied")
        for name in QUESTION_NAMES
    }
    for row in rows:
        ref = _ref(row)
        refs = [ref] if ref is not None else []
        if _truth(row, "consumed_in_subsequent_work", "subsequent_consumption") is True:
            result["subsequent_consumption"] = _question(
                "evidence", refs, "explicit subsequent consumer receipt"
            )
        if _truth(
            row, "resolved_evidence_changed_judgment", "changed_judgment", "changed_allocation"
        ) is True:
            result["resolved_evidence_changed_decision"] = _question(
                "evidence", refs, "explicit resolved evidence and changed decision"
            )
        if _truth(row, "criterion_changed_costly_action") is True:
            result["criterion_changed_costly_action"] = _question(
                "evidence", refs, "explicit criterion and costly action receipt"
            )
        if _truth(row, "horizon_reached") is True and _truth(row, "resources_exhausted") is False:
            result["exploration_reached_horizon"] = _question(
                "evidence", refs, "explicit horizon receipt before exhaustion"
            )
        if _truth(row, "external_income_funded_operation", "funded_by_income") is True:
            result["external_income_funded_operation"] = _question(
                "evidence", refs, "explicit external-income funding receipt"
            )
    if (
        income["external_confirmed_refs"]
        and result["external_income_funded_operation"]["status"] == "unknown"
    ):
        result["external_income_funded_operation"]["reason"] = (
            "external income exists but later operation funding is unmeasured"
        )
    if assessments:
        changed = any(change["changed"] for change in assessments)
        result["resolved_evidence_changed_decision"] = {
            "status": "evidence",
            "evidence_refs": [
                ref for change in assessments for ref in change["evidence_refs"]
            ],
            "reason": (
                "assessment changed after evidence; not causal proof"
                if changed
                else "assessment unchanged after evidence; not causal proof"
            ),
            "assessment_changes": assessments,
        }
    if reusable["cross_lineage_refs"]:
        result["subsequent_consumption"]["candidate_refs"] = reusable["cross_lineage_refs"]
        result["subsequent_consumption"]["reason"] = (
            "cross-lineage execution is candidate evidence; subsequent consumption is unmeasured"
        )
    return result


def build_report(
    rows: Sequence[Mapping[str, Any]], *, configuration: Mapping[str, Any] | None = None
) -> dict[str, Any]:
    """Aggregate exported facts into a static report without making causal claims."""
    rows = [row for row in rows if isinstance(row, Mapping)]
    configuration = dict(configuration) if isinstance(configuration, Mapping) else None
    address_enabled = (
        configuration.get("address_enabled")
        if configuration is not None and type(configuration.get("address_enabled")) is bool
        else None
    )
    income = _income_report(rows)
    costs = _cost_report(rows)
    reusable = _reusable_report(rows)
    assessments = _assessment_changes(rows)
    return {
        "report_kind": "edition4_static_rehearsal_report",
        "status": "static_report_not_live_observer",
        "rows_read": len(rows),
        "costs": costs,
        "internal_metered_costs": _internal_metered_report(rows),
        "status_counts": _status_report(rows),
        "status_count_basis": (
            "explicit normalized status fields on supplied action/outcome rows; "
            "routine rows without status are excluded"
        ),
        "configuration": configuration or {
            "source": "unavailable",
            "address_enabled": None,
        },
        "messages": _message_report(rows, address_enabled),
        "reusable_calls": reusable,
        "assessment_changes": assessments,
        "forecast_learning": _forecast_learning(rows),
        "funded_children": _funded_children(rows),
        "income": income,
        "cost_per_useful_decision_micro": _cost_per_useful_decision(rows, costs),
        "five_questions": _questions(rows, income, assessments, reusable),
        "caveats": [
            "activity is not usefulness; references establish provenance only",
            "correlation is not causality; causal questions require explicit resolved receipts",
            "unknown bills, delivery, settlement, and income provenance are retained as unknown",
        ],
    }


def _terminated_exports(
    report_path: str | Path, events_path: str | Path
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Open events only after termination and same resolved run-directory binding.

    The rehearsal writes both exports into one newly created output directory. This
    rejects accidental cross-run pairing; it is not a cryptographic authenticity
    guarantee against replacement of a trusted local artifact.
    """
    report_path = Path(report_path).resolve()
    events_path = Path(events_path).resolve()
    try:
        report = json.loads(report_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ReportInputError(f"cannot read postmortem report: {type(exc).__name__}") from exc
    if not isinstance(report, dict):
        raise ReportInputError("postmortem report must be a JSON object")
    summary = report.get("summary")
    if (report.get("status") != "completed" or not isinstance(summary, Mapping)
            or summary.get("terminated") is not True):
        raise ReportInputError("postmortem report is not completed and terminated")
    if report_path.parent != events_path.parent:
        raise ReportInputError("postmortem report and events must share one resolved run directory")
    try:
        events = json.loads(events_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ReportInputError(f"cannot read terminated events: {type(exc).__name__}") from exc
    if not isinstance(events, list) or not all(isinstance(row, dict) for row in events):
        raise ReportInputError("terminated events must be a JSON array of objects")
    return report, events


def build_behavioral_trace(
    report: Mapping[str, Any], events: Sequence[Mapping[str, Any]]
) -> dict[str, Any]:
    """Join mechanical evidence from one terminated diary without inferring causality."""
    summary = report.get("summary") if isinstance(report.get("summary"), Mapping) else {}
    if report.get("status") != "completed" or summary.get("terminated") is not True:
        raise ReportInputError("behavioral trace requires a completed, terminated report")

    invocations = [row for row in events if row.get("kind") == "invocation"]
    invocation_by_handle = {
        str(row["handle"]): row for row in invocations if isinstance(row.get("handle"), str)
    }
    opens = {
        str(row["handle"]): row for row in events
        if row.get("kind") == "decision.open" and isinstance(row.get("handle"), str)
    }
    classified = {
        str(row["handle"]): row.get("action") for row in events
        if row.get("kind") == "action.classified" and isinstance(row.get("handle"), str)
    }
    tool_calls: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    dropped_sections: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in events:
        if row.get("kind") == "tool.call" and isinstance(row.get("handle"), str):
            tool_calls[str(row["handle"])].append(row)
        if (row.get("kind") == "return.sections_dropped"
                and isinstance(row.get("handle"), str)
                and isinstance(row.get("dropped"), list)):
            for dropped in row["dropped"]:
                if not isinstance(dropped, Mapping) or not isinstance(dropped.get("section"), str):
                    continue
                item = {"section": dropped["section"]}
                if isinstance(dropped.get("index"), int):
                    item["index"] = dropped["index"]
                dropped_sections[str(row["handle"])].append(item)

    emissions: list[dict[str, Any]] = []
    for row in events:
        event_kind, payload = _event_info(row)
        if event_kind != "ProducerReturn" or payload is None:
            continue
        handle = payload.get("about_handle")
        if not isinstance(handle, str):
            continue
        outputs = payload.get("outputs") if isinstance(payload.get("outputs"), Mapping) else {}
        invocation = invocation_by_handle.get(handle, {})
        emissions.append({
            "handle": handle,
            "seq": row.get("seq"),
            "open_seq": opens.get(handle, {}).get("seq"),
            "invocation_seq": invocation.get("seq"),
            "seat": invocation.get("assembly_id"),
            "status": payload.get("status"),
            "declared_action": outputs.get("action"),
        })
    unique: dict[str, dict[str, Any]] = {}
    for emission in emissions:
        unique.setdefault(emission["handle"], emission)
    decisions = []
    for handle, decision in unique.items():
        calls = tool_calls.get(handle, [])
        decisions.append({
            **decision,
            "classified_action": classified.get(handle),
            "executed_tool_calls": [row.get("tool") for row in calls
                                    if row.get("outcome") == "ok"],
            "failed_tool_calls": [row.get("tool") for row in calls
                                  if row.get("outcome") == "failed"],
            "uncertain_tool_calls": [row.get("tool") for row in calls
                                     if row.get("outcome") == "uncertain"],
            "refused_tool_calls": [row.get("tool") for row in calls
                                   if row.get("outcome") == "refused"],
            "pre_dispatch_rejected_sections": dropped_sections.get(handle, []),
        })

    published: dict[tuple[str, Any], Mapping[str, Any]] = {}
    for row in events:
        event_kind, payload = _event_info(row)
        if (
            event_kind != "Verdict"
            or payload is None
            or payload.get("grounded_consequence") is not True
        ):
            continue
        about = payload.get("about_handle")
        if isinstance(about, str):
            published[(about, payload.get("evaluator_handle"))] = row
    final_findings = [row for row in events
                      if row.get("kind") == "consequence.finding"
                      and isinstance(row.get("handle"), str)
                      and isinstance(row.get("judge_handle"), str)]
    settlement_rows = [row for row in events
                       if row.get("kind") == "decision.settle"
                       and isinstance(row.get("return"), Mapping)]
    addressed = [row for row in events if row.get("kind") == "outcome.addressed"]
    acknowledgements = [row for row in events if row.get("kind") == "outcome.ack"]
    chains = []
    ordered_decisions = sorted(
        decisions,
        key=lambda row: (
            row.get("open_seq") if isinstance(row.get("open_seq"), int) else -1
        ),
    )
    for finding in final_findings:
        handle = str(finding["handle"])
        judge_handle = finding.get("judge_handle")
        produced = unique.get(handle)
        settled = next((row for row in settlement_rows
                        if row["return"].get("handle") == handle
                        and (row["return"].get("sampling_ref") == judge_handle
                             or (finding.get("status") == "unknown"
                                 and str(row["return"].get("definition_version", ""))
                                 .endswith("-unknown")))), None)
        seat = None if produced is None else produced.get("seat")
        publication = published.get((handle, judge_handle))
        chain_sequences = [
            seq for seq in (
                finding.get("seq"), None if settled is None else settled.get("seq"),
                None if publication is None else publication.get("seq"),
            ) if isinstance(seq, int)
        ]
        after = max(chain_sequences, default=-1)
        delivery = next((row for row in addressed
                         if row.get("handle") == handle
                         and row.get("assembly_id") == seat
                         and row.get("evidence") == judge_handle), None)
        next_return = next((row for row in ordered_decisions
                            if row.get("seat") == seat and row.get("handle") != handle
                            and isinstance(row.get("open_seq"), int)
                            and row["open_seq"] > after), None)
        next_return_seq = next_return.get("seq") if next_return is not None else None
        next_invocation_seq = (
            next_return.get("invocation_seq") if next_return is not None else None
        )
        delivered_item = delivery.get("item") if delivery is not None else None
        finding_seq = finding.get("seq")
        delivery_seq = None if delivery is None else delivery.get("seq")
        settlement_seq = None if settled is None else settled.get("seq")
        publication_seq = None if publication is None else publication.get("seq")
        chain_ordered = (
            all(isinstance(seq, int) for seq in (
                finding_seq, delivery_seq, settlement_seq, publication_seq
            ))
            and finding_seq < delivery_seq < settlement_seq < publication_seq
        )
        acknowledgement = None if delivery is None else next((row for row in acknowledgements
            if row.get("assembly_id") == seat
            and isinstance(row.get("seq"), int)
            and isinstance(delivery_seq, int)
            and isinstance(next_invocation_seq, int)
            and isinstance(next_return_seq, int)
            and delivery_seq < next_invocation_seq < row["seq"] < next_return_seq
            and isinstance(row.get("cursor"), int) and isinstance(delivered_item, int)
            and row["cursor"] >= delivered_item), None)
        if not chain_ordered:
            pathway = "missing_or_malformed_chain_order"
        elif acknowledgement is not None:
            pathway = "addressed_and_acknowledged"
        elif next_return is not None:
            pathway = "addressed_without_ack"
        else:
            pathway = "ended_before_next_wake_without_ack"
        chains.append({
            "producer_handle": handle,
            "producer_seat": seat,
            "settlement_observed": settled is not None,
            "settlement_status": (
                settled.get("return", {}).get("status") if settled is not None else None
            ),
            "finding_status": finding.get("status"),
            "finding_score": finding.get("score"),
            "final_judge_handle": judge_handle,
            "published_verdict": publication is not None,
            "addressed_to_inbox": delivery is not None,
            "chain_ordered": chain_ordered,
            "acknowledged_receipt": acknowledgement is not None,
            "model_exposure": "claimed_received" if acknowledgement is not None else "unknown",
            "next_return": None if next_return is None else {
                key: next_return.get(key) for key in
                ("handle", "status", "declared_action", "classified_action")
            },
            "next_return_valid": next_return is not None and next_return.get("status") == "ok",
            "delivery_pathway": pathway,
        })

    stop_failures = Counter(
        str(row.get("stop_reason")) for row in invocations
        if row.get("status") != "ok"
        and row.get("stop_reason") in {"reasoning_only", "length"}
    )
    available = summary.get("tools") if isinstance(summary.get("tools"), list) else []
    attempted_tools = [row.get("tool") for calls in tool_calls.values() for row in calls]
    capability = {
        tool: {
            "available": tool in available,
            "attempted": attempted_tools.count(tool),
            "executed": sum(row.get("tool") == tool and row.get("outcome") == "ok"
                            for calls in tool_calls.values() for row in calls),
            "failed": sum(row.get("tool") == tool and row.get("outcome") == "failed"
                          for calls in tool_calls.values() for row in calls),
            "uncertain": sum(row.get("tool") == tool and row.get("outcome") == "uncertain"
                             for calls in tool_calls.values() for row in calls),
            "refused": sum(row.get("tool") == tool and row.get("outcome") == "refused"
                           for calls in tool_calls.values() for row in calls),
        }
        for tool in sorted(
            set(available) | {str(tool) for tool in attempted_tools if tool is not None}
        )
    }
    reasons = []
    invalid = sum(row.get("status") != "ok" for row in decisions)
    if invalid:
        reasons.append(f"{invalid} unique producer returns were invalid")
    if stop_failures:
        reasons.append("model calls ended before a valid final answer")
    if any(
        not chain["chain_ordered"]
        or not chain["acknowledged_receipt"]
        or not chain["next_return_valid"]
        for chain in chains
    ):
        reasons.append("final feedback exposure or a later valid decision is incomplete")
    if not chains:
        reasons.append("no final grounded findings were observed")
    finding_statuses = Counter(str(row.get("status", "missing")) for row in final_findings)
    rejected_section_counts = Counter(
        item["section"] for items in dropped_sections.values() for item in items
    )
    return {
        "report_kind": "edition4_terminated_behavioral_trace",
        "producer_work": {
            "unique_decisions": len(decisions), "emissions": len(emissions),
            "re_emitted_contracts": len(emissions) - len(decisions),
            "valid_unique_decisions": len(decisions) - invalid, "decisions": decisions,
        },
        "model_output_failures": dict(sorted(stop_failures.items())),
        "pre_dispatch_rejections": {
            "sections": sum(rejected_section_counts.values()),
            "by_section": dict(sorted(rejected_section_counts.items())),
            "tool_call_sections_without_tool_identity": rejected_section_counts["tool_calls"],
        },
        "capabilities": capability,
        "final_finding_statuses": dict(sorted(finding_statuses.items())),
        "final_feedback_chains": chains,
        "interpretation": {
            "status": (
                "unmeasured" if not chains else
                "mechanically_inconclusive" if reasons else
                "observed_chains_complete"
            ),
            "reasons": reasons,
            "caveat": (
                "Trace completeness covers observed chains only; it is not global correctness, "
                "and sequence does not establish that feedback caused a later action. CLI inputs "
                "are bound by one resolved run directory, not a cryptographic events digest."
            ),
        },
    }


def compare_rehearsals(
    control: Mapping[str, Any], treatment: Mapping[str, Any], *, factors: Sequence[str]
) -> dict[str, Any]:
    """Reject unmatched screens rather than attribute their differences to a treatment."""
    paths = {
        "prompt": "prompt.mode",
        "feedback": "evaluation.producer_feedback",
        "address": "tools.address_enabled",
        "reasoning": "models.*.reasoning",
    }
    if not factors or any(factor not in paths for factor in factors):
        raise ReportInputError("name at least one factor: prompt, feedback, address, reasoning")

    def flatten(value, prefix=""):
        if isinstance(value, Mapping):
            return {path: item for key, child in value.items()
                    for path, item in flatten(child, f"{prefix}.{key}".strip(".")).items()}
        if isinstance(value, list):
            return {path: item for index, child in enumerate(value)
                    for path, item in flatten(child, f"{prefix}.{index}").items()}
        return {prefix: value}

    problems = []
    manifests = []
    for label, report in (("control", control), ("treatment", treatment)):
        manifest = report.get("world", {}).get("manifest")
        if not isinstance(manifest, Mapping) or not manifest:
            problems.append(f"{label}: missing effective manifest")
            manifest = {}
        manifests.append(flatten(manifest))
        if report.get("status") != "completed":
            problems.append(f"{label}: run did not complete")
        cost = report.get("cost", {})
        if (type(cost.get("known_micro")) is not int
                or type(cost.get("attempted")) is not int
                or cost.get("attempted") != cost.get("known_calls")
                or cost.get("uncertain_calls") != 0 or cost.get("uncertain_micro") != 0
                or cost.get("overruns", 0) != 0):
            problems.append(f"{label}: billing is incomplete or uncertain")
        for phase in ("venue_before", "venue_after"):
            venue = report.get(phase, {})
            if venue.get("positions") != [] or venue.get("open_orders") != []:
                problems.append(f"{label}: {phase} is not confirmed flat")
    for key in ("sha256", "runner_sha256"):
        left, right = (r.get("source", {}).get(key) for r in (control, treatment))
        if not isinstance(left, str) or not left or left != right:
            problems.append(f"source {key} missing or different")
    for key in ("cap_micro", "max_calls"):
        left, right = (r.get("cost", {}).get(key) for r in (control, treatment))
        if type(left) is not int or left <= 0 or left != right:
            problems.append(f"admission {key} missing or different")
    common_protocol = ("duration_ns", "planned_tick_ceiling", "minimum_delivered_ticks",
                       "no_live_parameter_changes", "no_horizon_extension")
    if "feedback" not in factors:
        common_protocol += ("minimum_assessed_grounded_samples",
                            "minimum_contrary_grounded_samples")
    for key in common_protocol:
        left, right = (r.get("protocol", {}).get(key) for r in (control, treatment))
        if left is None or left != right:
            problems.append(f"protocol {key} missing or different")
    ignored = {"name", "exchange.client_namespace"}
    missing = object()
    differences = sorted(path for path in manifests[0].keys() | manifests[1].keys()
                         if path not in ignored
                         and manifests[0].get(path, missing) != manifests[1].get(path, missing))

    def matches(path, factor):
        if factor == "reasoning":
            parts = path.split(".")
            return len(parts) >= 3 and parts[0] == "models" and parts[2] == "reasoning"
        return path == paths[factor]

    unexpected = [path for path in differences
                  if not any(matches(path, factor) for factor in factors)]
    if unexpected:
        problems.append("effective manifests differ outside declared factors")
    for factor in dict.fromkeys(factors):
        if not any(matches(path, factor) for path in differences):
            problems.append(f"declared factor {factor} did not change")
    if not differences:
        problems.append("no treatment difference")
    sufficient = all(r.get("behavioral_screen", {}).get("status") == "sufficient"
                     for r in (control, treatment))
    return {
        "report_kind": "edition4_matched_screen",
        "status": "unmatched" if problems else "screen_complete" if sufficient else "inconclusive",
        "factors": list(dict.fromkeys(factors)), "manifest_difference_paths": differences,
        "unexpected_difference_paths": unexpected, "problems": problems,
        "arms": {label: {"cost": report.get("cost"), "timing": report.get("timing"),
                         "behavioral_screen": report.get("behavioral_screen")}
                 for label, report in (("control", control), ("treatment", treatment))},
        "caveat": ("A sequential screening pair is not a randomized causal estimate; "
                   "venue conditions differ."),
    }


def render_html(report: Mapping[str, Any]) -> str:
    """Render aggregate report values as inert escaped HTML with no controls or inputs."""
    data = json.dumps(report, indent=2, sort_keys=True, ensure_ascii=False)
    escaped = html.escape(data, quote=True)
    title = html.escape(str(report.get("report_kind", "Edition 4 report")), quote=True)
    return (
        "<!doctype html>\n<html lang=\"en\"><head><meta charset=\"utf-8\">"
        f"<title>{title}</title><style>body{{font:16px system-ui;max-width:70rem;"
        "margin:2rem auto;padding:0 1rem}}"
        "pre{white-space:pre-wrap;background:#f5f5f5;padding:1rem;border-radius:.5rem}</style>"
        f"</head><body><h1>{title}</h1><p>Static report; not a live production observer.</p>"
        f"<pre>{escaped}</pre></body></html>\n"
    )


def write_report(report: Mapping[str, Any], output_dir: str | Path) -> tuple[Path, Path]:
    """Write only aggregate JSON and escaped static HTML into an explicit output directory."""
    directory = Path(output_dir)
    directory.mkdir(parents=True, exist_ok=True)
    json_path = directory / "report.json"
    html_path = directory / "index.html"
    json_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    html_path.write_text(render_html(report), encoding="utf-8")
    return json_path, html_path


def main(argv: Sequence[str] | None = None) -> int:
    """Build one offline report from explicit input files and exit successfully."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--input", action="append", type=Path, help="explicit JSON export"
    )
    parser.add_argument(
        "--out", required=True, type=Path,
        help="output directory for report.json and index.html",
    )
    parser.add_argument("--control", type=Path, help="completed control rehearsal report")
    parser.add_argument("--treatment", type=Path, help="completed treatment rehearsal report")
    parser.add_argument("--postmortem-report", type=Path,
                        help="completed and terminated rehearsal report")
    parser.add_argument("--events", type=Path, help="events export for --postmortem-report")
    parser.add_argument("--factor", action="append",
                        choices=("prompt", "feedback", "address", "reasoning"))
    args = parser.parse_args(argv)
    if args.postmortem_report or args.events:
        if args.input or args.control or args.treatment or args.factor or not (
            args.postmortem_report and args.events
        ):
            parser.error("postmortem requires --postmortem-report and --events only")
        completed, events = _terminated_exports(args.postmortem_report, args.events)
        report = build_behavioral_trace(completed, events)
    elif args.control or args.treatment or args.factor:
        if args.input or not (args.control and args.treatment and args.factor):
            parser.error("comparison requires --control, --treatment and --factor, without --input")
        reports = []
        for path in (args.control, args.treatment):
            if path.suffix != ".json":
                parser.error("comparison inputs must be explicit JSON reports")
            value = json.loads(path.read_text())
            if not isinstance(value, dict):
                parser.error("comparison reports must be JSON objects")
            reports.append(value)
        report = compare_rehearsals(*reports, factors=args.factor)
    else:
        if not args.input:
            parser.error("provide --input or a comparison pair")
        report = build_report(load_rows(args.input))
    paths = write_report(report, args.out)
    print(json.dumps({"json": str(paths[0]), "html": str(paths[1])}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
