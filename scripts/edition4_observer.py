"""Synchronous, opt-in Edition 4 rehearsal observer.

This module wraps an existing ledger append method for a bounded rehearsal.  It
keeps only whitelisted aggregate facts and writes a small static dashboard at
completed tick boundaries.  It does not read a ledger, start a runtime, accept
operator input, contact a provider, or retain message bodies.
"""

from __future__ import annotations

import html
import json
import math
import re
from collections import deque
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from scripts.edition4_report import build_report

_MAX_ROWS = 512
_MAX_TEXT = 256
_COMMON_KEYS = frozenset(
    {
        "kind", "seq", "ts", "handle", "id", "ref", "reference", "decision_id", "call_id",
        "status", "state", "outcome", "actual_cost_micro", "billed_micro", "cost_micro",
        "cost_usd_micro", "cost", "root_id", "root_handle", "decision_root", "tree_id",
        "parent_handle", "parent_id", "child_handle", "sender_lineage", "source_lineage",
        "recipient_lineage", "target_lineage", "lineage_relation", "reusable", "is_reusable",
        "reusable_call", "consumed_in_subsequent_work", "subsequent_consumption",
        "resolved_evidence_changed_judgment", "changed_judgment", "changed_allocation",
        "criterion_changed_costly_action", "horizon_reached", "resources_exhausted",
        "external_income_funded_operation", "funded_by_income", "child", "is_child", "funded",
        "funding_confirmed", "confirmed", "externally_confirmed", "observed", "evidence_observed",
        "resolved", "delivered", "useful_decision", "useful_outcome", "independently_supported",
        "outcome_supported", "score", "evidence", "amount_micro", "value_micro", "income_micro",
        "amount", "op", "dst",
        "n", "summary_cost",
    }
)
_RECEIPT_KEYS = frozenset(
    {
        "kind", "tool", "maker_handle", "caller_handle", "version_sha256", "result_sha256",
        "lineage_relation", "sender_lineage", "source_lineage", "recipient_lineage",
        "target_lineage", "handle", "id", "status", "actual_cost_micro", "cost_micro",
    }
)
_EVENT_KEYS = frozenset({"id", "kind"})
_VERDICT_KEYS = frozenset({"about_handle", "about", "verdict", "grounded_consequence"})
_FORECAST_KEYS = frozenset({"y", "about_handle", "forecast_handle"})
_ADMISSION_KEYS = (
    "cap_micro", "max_calls", "attempted", "known_calls", "known_micro", "uncertain_calls",
    "uncertain_micro", "overruns", "refusals", "remaining_micro", "stop_reason",
    "call_p50_micro", "call_p90_micro",
)
_STATUS_VALUES = frozenset(
    {
        "attempted", "accepted", "executed", "settled", "refused", "unknown", "delivered",
        "supported", "contrary", "unsupported", "uncertain", "failed", "malformed", "censored",
        "complete", "completed", "ok", "success", "succeeded",
    }
)
_IDENTIFIER_KEYS = frozenset(
    {
        "kind", "handle", "id", "ref", "reference", "decision_id", "call_id", "root_id",
        "root_handle", "decision_root", "tree_id", "parent_handle", "parent_id", "child_handle",
        "sender_lineage", "source_lineage", "recipient_lineage", "target_lineage", "dst", "tool",
        "version_sha256", "result_sha256", "about_handle", "about", "forecast_handle",
    }
)
_NUMERIC_KEYS = frozenset(
    {
        "seq", "ts", "actual_cost_micro", "billed_micro", "cost_micro", "cost_usd_micro", "cost",
        "amount_micro", "value_micro", "income_micro", "amount", "n", "verdict", "y", "score",
    }
)
_OP_VALUES = frozenset({"transfer", "income", "commit", "commons_release", "settle"})
_EVIDENCE_REF = re.compile(
    r"^(?:event:[0-9]+|execution:[A-Za-z0-9][A-Za-z0-9._-]{0,127}|"
    r"economic-outcome:(?:sha256(?::[0-9a-fA-F]{64})?|[A-Za-z0-9][A-Za-z0-9._-]{0,127}))$"
)
_EVIDENCE_KEYS = frozenset(
    {
        "consumed_in_subsequent_work", "subsequent_consumption",
        "resolved_evidence_changed_judgment", "changed_judgment", "changed_allocation",
        "criterion_changed_costly_action", "horizon_reached", "resources_exhausted",
        "external_income_funded_operation", "funded_by_income", "useful_decision",
        "useful_outcome", "independently_supported", "outcome_supported",
    }
)
_MEANINGFUL_EXACT = frozenset(
    {
        "invocation", "metering.uncertain", "request.child", "receipt.execution", "budget",
        "wallet.commit", "wallet.settle", "wallet.initial", "wallet.drip", "consequence.finding",
        "message", "income", "revenue", "earning",
    }
)
_MEANINGFUL_PREFIXES = (
    "provider.", "model.", "address.", "forecast.", "learning.", "income.", "revenue.",
    "wallet.",
)


def _safe_scalar(value: Any) -> Any:
    """Return a bounded JSON scalar, dropping objects that could carry private state."""
    if value is None or type(value) is bool or type(value) is int:
        return value
    if type(value) is float:
        return value if math.isfinite(value) else None
    if isinstance(value, str):
        return value[:_MAX_TEXT]
    return None


def _safe_identifier(value: Any) -> str | None:
    """Return a bounded single-line identifier or no retained value."""
    if not isinstance(value, (str, int)):
        return None
    text = str(value)
    if not text or len(text) > _MAX_TEXT or any(char in text for char in "\r\n\x00"):
        return None
    return text


def _safe_mapping(source: Mapping[str, Any], keys: frozenset[str]) -> dict[str, Any]:
    clean: dict[str, Any] = {}
    for key in keys:
        if key not in source:
            continue
        value = _safe_scalar(source[key])
        if key in _NUMERIC_KEYS and (
            type(value) not in {int, float} or (type(value) is float and not math.isfinite(value))
        ):
            continue
        if key in {"status", "state", "outcome", "lineage_relation"}:
            if not isinstance(value, str) or value.lower() not in _STATUS_VALUES | {
                "self", "same_lineage", "cross_lineage", "same-lineage", "cross-lineage",
                "same", "cross", "program_result"
            }:
                continue
            value = value.lower()
        elif key == "op":
            if not isinstance(value, str) or value.lower() not in _OP_VALUES:
                continue
            value = value.lower()
        elif key in _IDENTIFIER_KEYS and isinstance(value, str):
            if not value or any(char in value for char in "\r\n\x00"):
                continue
            value = value[:_MAX_TEXT]
        if value is not None or source[key] is None:
            clean[key] = value
    return clean


def _whitelist_row(entry: Mapping[str, Any], seq: int | None) -> dict[str, Any] | None:
    """Copy public aggregate facts from one append without retaining its payload."""
    kind = entry.get("kind")
    if not isinstance(kind, str):
        return None
    clean = _safe_mapping(entry, _COMMON_KEYS)
    clean["kind"] = kind[:_MAX_TEXT]
    if seq is not None:
        clean["seq"] = seq
    evidence = entry.get("evidence")
    if isinstance(evidence, (list, tuple)):
        clean["evidence"] = [
            ref for ref in evidence
            if isinstance(ref, str) and _EVIDENCE_REF.fullmatch(ref)
        ][:16]
    if kind == "consequence.finding":
        if clean.get("evidence"):
            clean["evidence_provenance"] = "cited_typed_reference"
        elif seq is not None:
            clean["evidence_provenance"] = "finding_ledger_append_sequence"
            clean["finding_ledger_seq"] = seq
    if kind == "consequence.finding" and clean.get("status") not in {"supported", "contrary"}:
        # A score without a resolved finding is not enough to manufacture a revision.
        clean.pop("score", None)
    if kind == "income.earned":
        receipt_id = _safe_identifier(entry.get("receipt_id"))
        micro = entry.get("micro")
        if receipt_id is not None and type(micro) is int and micro > 0:
            # Treasury emits this row only after it verifies the external receipt.
            clean.update(
                {
                    "handle": receipt_id,
                    "receipt_id": receipt_id,
                    "amount_micro": micro,
                    "source": "external",
                    "confirmed": True,
                }
            )
    elif kind == "wallet.settle" and entry.get("reason") == "income":
        # The settlement is an internal wallet view until the observer correlates
        # it with the preceding verified receipt.  Reason alone is not proof that
        # an independent party paid the factory.
        clean["income_class"] = "wallet_income_settlement"

    receipt = entry.get("receipt")
    if isinstance(receipt, Mapping):
        receipt_clean = _safe_mapping(receipt, _RECEIPT_KEYS)
        facts = receipt.get("facts")
        if isinstance(facts, Mapping):
            receipt_clean["facts"] = _safe_mapping(facts, _RECEIPT_KEYS)
        clean["receipt"] = receipt_clean

    event = _event_parts(entry)
    if event is not None:
        event_clean = _safe_mapping(event, _EVENT_KEYS)
        event_kind = _event_kind(event.get("kind"))
        if event_kind is not None:
            event_clean["kind"] = event_kind
        payload = event.get("payload")
        allowed = _FORECAST_KEYS if event_kind == "ForecastSettled" else _VERDICT_KEYS
        if isinstance(payload, Mapping):
            event_clean["payload"] = _safe_mapping(payload, allowed)
        clean["event"] = event_clean
    return clean


def _event_kind(value: Any) -> str | None:
    """Normalize enum-like runtime event kinds without copying arbitrary payload text."""
    if isinstance(value, str):
        text = value
    else:
        text = getattr(value, "name", "") or str(getattr(value, "value", ""))
    token = text.rsplit(".", 1)[-1].strip()
    if not token or not token.replace("_", "").isalnum():
        return None
    return token.title().replace("_", "")


def _event_parts(entry: Mapping[str, Any]) -> dict[str, Any] | None:
    """Expose only public event envelope fields from dict or runtime event objects."""
    event = entry.get("event")
    if isinstance(event, Mapping):
        return dict(event)
    if event is None:
        return None
    kind = getattr(event, "kind", None)
    if kind is None:
        return None
    return {
        "id": getattr(event, "id", None),
        "kind": kind,
        "payload": getattr(event, "payload", None),
    }


def _tick_event(entry: Mapping[str, Any]) -> tuple[bool, int | None]:
    """Recognize the public Tick envelope and its payload index."""
    event = _event_parts(entry)
    if isinstance(event, Mapping) and _event_kind(event.get("kind")) == "Tick":
        payload = event.get("payload")
        index = payload.get("index") if isinstance(payload, Mapping) else None
        return True, index if type(index) is int else None
    return False, None


def _meaningful(clean: Mapping[str, Any]) -> bool:
    """Keep only rows that can contribute to a report metric or provenance view."""
    kind = clean.get("kind")
    if not isinstance(kind, str) or kind == "runtime.event_done":
        return False
    if kind == "event":
        event = clean.get("event")
        event_kind = event.get("kind") if isinstance(event, Mapping) else None
        return event_kind in {"Tick", "Verdict", "ForecastSettled"}
    if kind in _MEANINGFUL_EXACT or kind.startswith(_MEANINGFUL_PREFIXES):
        return True
    return bool(_EVIDENCE_KEYS.intersection(clean))


def _admission_snapshot(source: Any) -> dict[str, Any] | None:
    """Read only the public scalar fields from an Admission.report provider."""
    if source is None:
        return None
    try:
        value = source() if callable(source) else source.report()
    except Exception as exc:  # noqa: BLE001 - observer must not break a ledger append
        return {"status": "unavailable", "error": type(exc).__name__}
    if not isinstance(value, Mapping):
        return {"status": "unavailable", "error": "invalid_report"}
    result = _safe_mapping(value, frozenset(_ADMISSION_KEYS))
    result["status"] = "available"
    return result


def _admission_costs(report: dict[str, Any], admission: dict[str, Any] | None) -> None:
    """Replace cost totals with the authoritative supplied admission view."""
    report["cost_per_useful_decision_micro"] = None
    if admission is None:
        report["costs"] = {
            **report["costs"],
            "basis": "unavailable",
            "label": "provided Admission.report unavailable; billing unknown",
            "known_micro_total": None,
            "known_bill_count": None,
            "unknown_bill_count": None,
            "p50_micro": None,
            "p90_micro": None,
        }
        return
    if admission.get("status") != "available":
        _admission_costs(report, None)
        report["costs"]["label"] = "provided Admission.report unavailable; billing unknown"
        return
    attempted = admission.get("attempted")
    known_calls = admission.get("known_calls")
    uncertain_calls = admission.get("uncertain_calls")
    if type(uncertain_calls) is not int and type(attempted) is int and type(known_calls) is int:
        uncertain_calls = max(0, attempted - known_calls)
    report["costs"] = {
        **report["costs"],
        "basis": "admission_report",
        "label": "provided Admission.report; coverage retained",
        "calls": attempted,
        "known_bill_count": known_calls,
        "unknown_bill_count": uncertain_calls,
        "known_micro_total": admission.get("known_micro"),
        "call_p50_micro": admission.get("call_p50_micro"),
        "call_p90_micro": admission.get("call_p90_micro"),
        "p50_micro": admission.get("call_p50_micro"),
        "p90_micro": admission.get("call_p90_micro"),
        "call_cost_basis": "admission_report",
        "call_cost_label": "provided Admission.report",
        "root_cost_basis": "unavailable",
        "root_p50_micro": None,
        "root_p90_micro": None,
    }


def _authoritative_cost_per_useful(
    report: dict[str, Any], rows: list[Mapping[str, Any]]
) -> None:
    """Derive useful-decision cost only from complete authoritative billing."""
    useful = sum(
        1
        for row in rows
        if (row.get("useful_decision") is True or row.get("useful_outcome") is True)
        and (
            row.get("independently_supported") is True
            or row.get("outcome_supported") is True
        )
    )
    costs = report.get("costs")
    if not useful or not isinstance(costs, Mapping):
        report["cost_per_useful_decision_micro"] = None
        return
    known = costs.get("known_micro_total")
    unknown = costs.get("unknown_bill_count")
    report["cost_per_useful_decision_micro"] = (
        known // useful if type(known) is int and unknown == 0 else None
    )


def _runtime_configuration(runtime: Any) -> dict[str, Any]:
    """Return only manifest facts needed to interpret this rehearsal's diagnostics."""
    manifest = runtime.m
    backstop = manifest.evaluation.consequence_backstop_ticks
    min_ratio = manifest.timing.min_ratio
    activation = min_ratio * backstop
    intervals = {"declared_ns": manifest.tick_interval_ns, "measured_ns": None, "samples": 0}
    clock = getattr(runtime, "tick_clock", None)
    method = getattr(clock, "intervals", None)
    if callable(method):
        observed = method()
        if isinstance(observed, Mapping):
            intervals = {
                "declared_ns": observed.get("declared_ns", manifest.tick_interval_ns),
                "measured_ns": observed.get("measured_ns"),
                "samples": observed.get("samples", 0),
            }
    delivered = intervals["measured_ns"]
    if type(delivered) is not int or delivered <= 0:
        delivered = None
    nominal = intervals["declared_ns"]
    if type(nominal) is not int or nominal <= 0:
        nominal = manifest.tick_interval_ns
    total = activation + backstop
    return {
        "source": "runtime_manifest",
        "address_enabled": manifest.tools.address_enabled,
        "governance_runway": {
            "basis": "manifest lower bound plus delivered tick interval evidence",
            "first_activation_lower_bound_ticks": activation,
            "post_activation_consequence_window_ticks": backstop,
            "minimum_ticks_through_one_consequence_window": total,
            "nominal_interval_ns": nominal,
            "delivered_interval_ns": delivered,
            "delivered_interval_samples": intervals["samples"],
            "nominal_duration_lower_bound_ns": total * nominal,
            "delivered_duration_estimate_ns": (
                total * max(nominal, delivered) if delivered is not None else None
            ),
            "assurance": (
                "none; elapsed ticks do not assure a proposal, approval, activation, "
                "or observed consequence"
            ),
        },
    }


def _question_title(name: str) -> str:
    return name.replace("_", " ").capitalize()


def render_observer_html(report: Mapping[str, Any]) -> str:
    """Render a readable inert rehearsal dashboard with escaped aggregate values."""
    questions = report.get("five_questions")
    cards: list[str] = []
    if isinstance(questions, Mapping):
        for name in (
            "subsequent_consumption", "resolved_evidence_changed_decision",
            "criterion_changed_costly_action", "exploration_reached_horizon",
            "external_income_funded_operation",
        ):
            value = questions.get(name, {})
            status = value.get("status", "unknown") if isinstance(value, Mapping) else "unknown"
            reason = (
                value.get("reason", "unavailable")
                if isinstance(value, Mapping)
                else "unavailable"
            )
            cards.append(
                "<article><h2>"
                + html.escape(_question_title(name))
                + "</h2><strong>"
                + html.escape(str(status))
                + "</strong><p>"
                + html.escape(str(reason))
                + "</p></article>"
            )
    costs = report.get("costs", {})
    observer = report.get("observer", {})
    status_counts = report.get("status_counts", {})
    status_text = ", ".join(
        f"{html.escape(str(key))}: {html.escape(str(value))}"
        for key, value in status_counts.items()
    ) if isinstance(status_counts, Mapping) else "unavailable"
    summary = (
        f"<p>Ticks completed: {html.escape(str(observer.get('ticks', 0)))} · "
        f"Calls: {html.escape(str(costs.get('calls', 'unknown')))} · "
        f"Known cost (micro-USD): {html.escape(str(costs.get('known_micro_total', 'unknown')))}</p>"
        f"<p>Status counts: {status_text}</p>"
    )
    configuration = report.get("configuration", {})
    messages = report.get("messages", {})
    runway = (
        configuration.get("governance_runway", {})
        if isinstance(configuration, Mapping)
        else {}
    )
    if isinstance(messages, Mapping):
        summary += (
            "<p>Addressing: "
            + html.escape(str(messages.get("capability_label", "metadata unavailable")))
            + "</p>"
        )
    if isinstance(runway, Mapping) and runway:
        summary += (
            "<p>Governance lower bound: "
            + html.escape(str(runway.get("minimum_ticks_through_one_consequence_window")))
            + " ticks through one post-activation consequence window · nominal/delivered "
            + html.escape(str(runway.get("nominal_interval_ns")))
            + "/"
            + html.escape(str(runway.get("delivered_interval_ns")))
            + " ns per tick. "
            + html.escape(str(runway.get("assurance", "No assurance.")))
            + "</p>"
        )
    caveats = report.get("caveats", [])
    caveat_html = "".join(f"<li>{html.escape(str(item))}</li>" for item in caveats)
    title = html.escape(str(report.get("report_kind", "Edition 4 rehearsal observer")))
    return (
        "<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">"
        f"<title>{title}</title><style>body{{font:16px system-ui;max-width:70rem;"
        "margin:2rem auto;padding:0 1rem;background:#fafafa;color:#222}}"
        "main{display:grid;grid-template-columns:repeat(auto-fit,minmax(14rem,1fr));gap:1rem}"
        "article{background:white;border:1px solid #ddd;border-radius:.6rem;padding:1rem}"
        "h2{font-size:1rem;margin-top:0}small{color:#555}</style></head><body>"
        f"<h1>{title}</h1>{summary}<main>{''.join(cards)}</main>"
        f"<h2>Caveats</h2><ul>{caveat_html}</ul>"
        "<small>Opt-in synchronous rehearsal report; this page is static and not a live "
        "production observer.</small>"
        "</body></html>\n"
    )


class RehearsalObserver:
    """Collect bounded rehearsal evidence and write a dashboard at completed ticks."""

    def __init__(
        self,
        ledger: Any,
        output_dir: str | Path,
        *,
        admission_report: Callable[[], Mapping[str, Any]] | Any | None = None,
        configuration_report: Callable[[], Mapping[str, Any]] | None = None,
        write_every_ticks: int = 1,
        max_rows: int = _MAX_ROWS,
    ) -> None:
        if type(write_every_ticks) is not int or write_every_ticks < 1:
            raise ValueError("write_every_ticks must be positive")
        if type(max_rows) is not int or max_rows < 1:
            raise ValueError("max_rows must be positive")
        self.ledger = ledger
        self.output_dir = Path(output_dir)
        self.admission_report = admission_report
        self.configuration_report = configuration_report
        self.write_every_ticks = write_every_ticks
        self.max_rows = max_rows
        self._rows: deque[dict[str, Any]] = deque(maxlen=max_rows)
        self._original_append: Callable[..., Any] | None = None
        self._ticks = 0
        self._written_ticks = 0
        self._dropped_rows = 0
        self._total_entries = 0
        self._ignored_rows = 0
        self._income_receipts: dict[str, str] = {}
        self._pending_tick: tuple[int | None, int] | None = None
        self._last_report: dict[str, Any] | None = None
        self._write_error: str | None = None

    @classmethod
    def for_runtime(
        cls, runtime: Any, output_dir: str | Path, **kwargs: Any
    ) -> RehearsalObserver:
        """Attach only when explicitly requested by a rehearsal caller."""
        observer = cls(
            runtime.ledger,
            output_dir,
            configuration_report=lambda: _runtime_configuration(runtime),
            **kwargs,
        )
        observer.attach()
        return observer

    def attach(self) -> RehearsalObserver:
        """Wrap ledger.append while preserving its return value and sequence behavior."""
        if self._original_append is not None:
            return self
        original = self.ledger.append

        def append(entry: Any, *args: Any, **kwargs: Any) -> Any:
            result = original(entry, *args, **kwargs)
            try:
                self._observe(entry, result)
            except Exception as exc:  # noqa: BLE001 - diagnostics never alter world behavior
                self._write_error = type(exc).__name__
            return result

        self._original_append = original
        self.ledger.append = append
        return self

    def detach(self) -> None:
        """Restore the original append method without changing the ledger."""
        if self._original_append is not None:
            self.ledger.append = self._original_append
            self._original_append = None

    def _observe(self, entry: Any, result: Any) -> None:
        if not isinstance(entry, Mapping):
            return
        self._total_entries += 1
        seq = result if type(result) is int else None
        clean = _whitelist_row(entry, seq)
        kind = entry.get("kind")
        if clean is not None and kind == "income.earned":
            service = _safe_identifier(entry.get("service"))
            tx = _safe_identifier(entry.get("tx"))
            receipt_id = _safe_identifier(entry.get("receipt_id"))
            if (
                service is not None
                and tx is not None
                and receipt_id is not None
                and clean.get("receipt_id") == receipt_id
            ):
                settlement_handle = f"income:{service}:{tx}"
                if len(settlement_handle) <= _MAX_TEXT:
                    if len(self._income_receipts) >= self.max_rows:
                        self._income_receipts.pop(next(iter(self._income_receipts)))
                    self._income_receipts[settlement_handle] = receipt_id
        elif clean is not None and kind == "wallet.settle" and entry.get("reason") == "income":
            handle = entry.get("handle")
            receipt_id = (
                self._income_receipts.pop(handle, None) if isinstance(handle, str) else None
            )
            if receipt_id is not None:
                clean["handle"] = receipt_id
                clean["reason"] = "income"
        if clean is not None and _meaningful(clean):
            if len(self._rows) == self.max_rows:
                self._rows.popleft()
                self._dropped_rows += 1
            self._rows.append(clean)
        else:
            self._ignored_rows += 1

        is_tick, index = _tick_event(entry)
        if is_tick:
            self._pending_tick = (index, self._total_entries)
            return
        if entry.get("kind") != "runtime.event_done" or self._pending_tick is None:
            return
        index, _ = self._pending_tick
        self._pending_tick = None
        self._ticks += 1
        if self._ticks % self.write_every_ticks == 0:
            self._write(index if index is not None else self._ticks - 1)

    def _write(self, boundary: int) -> None:
        try:
            configuration = (
                self.configuration_report() if self.configuration_report is not None else None
            )
            report = build_report(list(self._rows), configuration=configuration)
            admission = _admission_snapshot(self.admission_report)
            _admission_costs(report, admission)
            _authoritative_cost_per_useful(report, list(self._rows))
            if self._dropped_rows:
                # Admission bills cover the whole run; the bounded window can no
                # longer supply a matching lifetime useful-decision denominator.
                report["cost_per_useful_decision_micro"] = None
            report["report_kind"] = "edition4_rehearsal_observer_report"
            report["status"] = "rehearsal_observer_static_snapshot"
            coverage = {
                "window_limit": self.max_rows,
                "window_rows": len(self._rows),
                "discarded_meaningful_rows": self._dropped_rows,
                "ignored_unmeasured_rows": self._ignored_rows,
                "status": "window_truncated" if self._dropped_rows else "complete_within_window",
            }
            report["observer"] = {
                "mode": "opt_in_synchronous_rehearsal",
                "ticks": self._ticks,
                "boundary": boundary,
                "written_ticks": self._written_ticks + 1,
                "bounded_rows": len(self._rows),
                "dropped_rows": self._dropped_rows,
                "ignored_rows": self._ignored_rows,
                "total_entries_seen": self._total_entries,
                "admission_report": admission,
                "coverage": coverage,
                "write_cost": "bounded recent-window report at tick boundary",
            }
            runway = report.get("configuration", {}).get("governance_runway")
            if isinstance(runway, dict):
                runway["completed_ticks"] = self._ticks
                remaining = max(
                    0,
                    runway["minimum_ticks_through_one_consequence_window"] - self._ticks,
                )
                runway["remaining_lower_bound_ticks"] = remaining
                runway["remaining_nominal_duration_lower_bound_ns"] = (
                    remaining * runway["nominal_interval_ns"]
                )
                delivered = runway["delivered_interval_ns"]
                runway["remaining_delivered_duration_estimate_ns"] = (
                    remaining * max(runway["nominal_interval_ns"], delivered)
                    if delivered is not None
                    else None
                )
            for question in report.get("five_questions", {}).values():
                if not isinstance(question, dict):
                    continue
                question["coverage"] = dict(coverage)
                if self._dropped_rows:
                    question["reason"] = (
                        str(question.get("reason", "unavailable"))
                        + "; earlier evidence may be outside the bounded window"
                    )
            self.output_dir.mkdir(parents=True, exist_ok=True)
            report_text = json.dumps(report, indent=2, sort_keys=True) + "\n"
            page = render_observer_html(report)
            (self.output_dir / "report.json").write_text(
                report_text, encoding="utf-8"
            )
            (self.output_dir / "index.html").write_text(
                page, encoding="utf-8"
            )
            self._written_ticks += 1
            self._last_report = report
        except Exception as exc:  # noqa: BLE001 - diagnostics never alter world behavior
            self._write_error = type(exc).__name__

    @property
    def last_report(self) -> Mapping[str, Any] | None:
        """Return the last in-memory aggregate snapshot, never raw ledger rows."""
        return self._last_report


def attach_rehearsal_observer(
    runtime: Any,
    output_dir: str | Path,
    *,
    admission_report: Callable[[], Mapping[str, Any]] | Any | None = None,
    write_every_ticks: int = 1,
    max_rows: int = _MAX_ROWS,
) -> RehearsalObserver:
    """Attach an observer to a supplied rehearsal runtime; ordinary runs remain untouched."""
    return RehearsalObserver.for_runtime(
        runtime,
        output_dir,
        admission_report=admission_report,
        write_every_ticks=write_every_ticks,
        max_rows=max_rows,
    )
