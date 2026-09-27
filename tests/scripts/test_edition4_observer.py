"""Focused checks for the opt-in synchronous Edition 4 rehearsal observer."""

import copy
import json

import pytest

from factorylab.runtime.loop import Runtime
from factorylab.runtime.worlds import load_manifest
from factorylab.world.scripted import ScriptedProvider
from scripts.edition4_observer import RehearsalObserver, render_observer_html


class FakeLedger:
    def __init__(self):
        self.entries = []

    def append(self, entry):
        seq = len(self.entries)
        self.entries.append(entry)
        return seq


def _admission():
    return {
        "cap_micro": 100,
        "max_calls": 4,
        "attempted": 1,
        "known_calls": 1,
        "known_micro": 0,
        "uncertain_calls": 0,
        "uncertain_micro": 0,
        "remaining_micro": 100,
    }


def _tick(ledger, index):
    ledger.append(
        {
            "kind": "event",
            "event": {"id": f"tick-{index}", "kind": "Tick", "payload": {"index": index}},
        }
    )


def test_writes_dashboard_at_completed_tick_before_termination(tmp_path):
    ledger = FakeLedger()
    observer = RehearsalObserver(ledger, tmp_path, admission_report=_admission)
    observer.attach()

    assert ledger.append({"kind": "invocation", "handle": "root", "cost_micro": 7}) == 0
    assert not (tmp_path / "report.json").exists()
    _tick(ledger, 0)
    assert ledger.append({"kind": "runtime.event_done", "n": 1}) == 2

    report = json.loads((tmp_path / "report.json").read_text(encoding="utf-8"))
    page = (tmp_path / "index.html").read_text(encoding="utf-8")
    assert report["observer"]["ticks"] == 1
    assert report["costs"]["basis"] == "admission_report"
    assert report["costs"]["known_micro_total"] == 0
    assert page.count("<article>") == 5
    assert "static" in page


def test_private_payload_is_not_retained_and_html_is_escaped(tmp_path):
    ledger = FakeLedger()
    observer = RehearsalObserver(ledger, tmp_path, admission_report=_admission)
    observer.attach()
    private = "private-body-needle <script>alert('x')</script>"
    ledger.append(
        {
            "kind": "invocation",
            "handle": "safe",
            "prompt": private,
            "message": private,
            "private_memory": private,
            "nested": {"body": private},
        }
    )
    _tick(ledger, 0)
    ledger.append({"kind": "runtime.event_done", "n": 1})
    raw_json = (tmp_path / "report.json").read_text(encoding="utf-8")
    page = (tmp_path / "index.html").read_text(encoding="utf-8")
    assert private not in raw_json
    assert private not in page
    assert "<script>" not in page


def test_unknown_admission_billing_remains_unknown_not_zero(tmp_path):
    ledger = FakeLedger()
    observer = RehearsalObserver(
        ledger,
        tmp_path,
        admission_report=lambda: {
            "attempted": 1,
            "known_calls": 0,
            "known_micro": 0,
            "uncertain_calls": 1,
            "uncertain_micro": 9,
        },
    )
    observer.attach()
    ledger.append({"kind": "invocation", "handle": "unknown", "cost_micro": 0})
    _tick(ledger, 0)
    ledger.append({"kind": "runtime.event_done", "n": 1})
    report = json.loads((tmp_path / "report.json").read_text(encoding="utf-8"))
    assert report["costs"]["known_micro_total"] == 0
    assert report["costs"]["unknown_bill_count"] == 1


def test_authoritative_admission_recomputes_or_clears_useful_decision_cost(tmp_path):
    def observe(admission, directory):
        ledger = FakeLedger()
        observer = RehearsalObserver(ledger, directory, admission_report=lambda: admission)
        observer.attach()
        ledger.append({"kind": "invocation", "handle": "root", "cost_micro": 7})
        ledger.append(
            {
                "kind": "evidence",
                "id": "useful-1",
                "useful_decision": True,
                "independently_supported": True,
            }
        )
        _tick(ledger, 0)
        ledger.append({"kind": "runtime.event_done", "n": 1})
        return json.loads((directory / "report.json").read_text(encoding="utf-8"))

    known = observe(
        {"attempted": 1, "known_calls": 1, "known_micro": 100, "uncertain_calls": 0},
        tmp_path / "known",
    )
    assert known["cost_per_useful_decision_micro"] == 100

    unknown = observe(
        {"attempted": 1, "known_calls": 0, "known_micro": 0, "uncertain_calls": 1},
        tmp_path / "unknown",
    )
    assert unknown["cost_per_useful_decision_micro"] is None


def test_verified_income_receipt_and_wallet_view_are_counted_once(tmp_path):
    ledger = FakeLedger()
    observer = RehearsalObserver(ledger, tmp_path, admission_report=_admission)
    observer.attach()
    ledger.append(
        {
            "kind": "income.earned",
            "service": "doubler",
            "tx": "tx-1",
            "micro": 25,
            "receipt_id": "income-receipt-1",
            "reason": "private arbitrary text",
            "payload": "private body",
        }
    )
    ledger.append(
        {
            "kind": "wallet.settle",
            "handle": "income:doubler:tx-1",
            "reason": "income",
            "amount": 25,
            "payload": "private settlement body",
        }
    )
    _tick(ledger, 0)
    ledger.append({"kind": "runtime.event_done", "n": 1})

    report = json.loads((tmp_path / "report.json").read_text(encoding="utf-8"))
    assert report["income"]["external_confirmed_refs"] == ["income-receipt-1"]
    assert report["income"]["external_confirmed_count"] == 1
    assert report["income"]["external_confirmed_micro"] == 25
    assert report["income"]["unknown_provenance"] == 0
    assert report["five_questions"]["external_income_funded_operation"]["status"] == "unknown"
    raw = json.dumps(report)
    assert "private arbitrary text" not in raw
    assert "private body" not in raw
    assert "private settlement body" not in raw


def test_unmatched_wallet_income_settlement_is_not_independent_income(tmp_path):
    ledger = FakeLedger()
    observer = RehearsalObserver(ledger, tmp_path, admission_report=_admission)
    observer.attach()
    ledger.append(
        {
            "kind": "wallet.settle",
            "handle": "income:self-funded:tx-1",
            "reason": "income",
            "amount": 25,
        }
    )
    _tick(ledger, 0)
    ledger.append({"kind": "runtime.event_done", "n": 1})

    report = json.loads((tmp_path / "report.json").read_text(encoding="utf-8"))
    assert report["income"]["external_confirmed_count"] == 0
    assert report["income"]["unknown_provenance"] == 1


def test_assessment_finding_score_and_typed_evidence_reach_question_card(tmp_path):
    ledger = FakeLedger()
    observer = RehearsalObserver(ledger, tmp_path, admission_report=_admission)
    observer.attach()
    ledger.append(
        {
            "kind": "event",
            "event": {
                "id": "verdict-1",
                "kind": "Verdict",
                "payload": {
                    "about_handle": "decision-1",
                    "verdict": 0.2,
                    "private": "do-not-retain",
                },
            },
        }
    )
    _tick(ledger, 0)
    ledger.append(
        {
            "kind": "consequence.finding",
            "handle": "decision-1",
            "status": "contrary",
            "score": 0.8,
            "evidence": [
                "event:11",
                "execution:exec-hash",
                "economic-outcome:sha256:" + "a" * 64,
                "private body",
            ],
            "nested": {"body": "do-not-retain"},
        }
    )
    ledger.append({"kind": "runtime.event_done", "n": 1})
    report = json.loads((tmp_path / "report.json").read_text(encoding="utf-8"))
    change = report["assessment_changes"][0]
    assert change["initial_verdict"] == 0.2
    assert change["consequence_score"] == 0.8
    assert change["changed"] is True
    finding = next(row for row in observer._rows if row.get("kind") == "consequence.finding")
    assert finding["status"] == "contrary"
    assert finding["evidence"] == [
        "event:11", "execution:exec-hash", "economic-outcome:sha256:" + "a" * 64
    ]
    raw = json.dumps(report)
    assert "do-not-retain" not in raw
    assert "private body" not in raw


def test_unknown_finding_score_does_not_fake_assessment_change(tmp_path):
    ledger = FakeLedger()
    observer = RehearsalObserver(ledger, tmp_path, admission_report=_admission)
    observer.attach()
    ledger.append(
        {
            "kind": "event",
            "event": {
                "id": "verdict-unknown",
                "kind": "Verdict",
                "payload": {"about_handle": "decision-unknown", "verdict": 0.2},
            },
        }
    )
    _tick(ledger, 0)
    ledger.append(
        {
            "kind": "consequence.finding",
            "handle": "decision-unknown",
            "status": "unknown",
            "score": 0.9,
        }
    )
    ledger.append({"kind": "runtime.event_done", "n": 1})
    report = json.loads((tmp_path / "report.json").read_text(encoding="utf-8"))
    assert report["assessment_changes"] == []
    assert report["five_questions"]["resolved_evidence_changed_decision"]["status"] == "unknown"


def test_append_return_and_entry_are_unchanged(tmp_path):
    ledger = FakeLedger()
    observer = RehearsalObserver(ledger, tmp_path, admission_report=_admission)
    observer.attach()
    entry = {"kind": "invocation", "handle": "h", "prompt": "do not mutate"}
    before = copy.deepcopy(entry)
    assert ledger.append(entry) == 0
    assert entry == before
    assert ledger.entries[0] is entry


def test_rendered_page_uses_five_readable_question_cards():
    report = {
        "report_kind": "observer",
        "costs": {"calls": 2, "known_micro_total": 4},
        "observer": {"ticks": 3},
        "five_questions": {
            name: {"status": "unknown", "reason": "unmeasured"}
            for name in (
                "subsequent_consumption", "resolved_evidence_changed_decision",
                "criterion_changed_costly_action", "exploration_reached_horizon",
                "external_income_funded_operation",
            )
        },
        "caveats": ["activity is not usefulness"],
    }
    page = render_observer_html(report)
    assert page.count("<article>") == 5
    assert "activity is not usefulness" in page
    assert "Governance lower bound" not in page
    assert "None ns per tick" not in page
    assert "<input" not in page


def test_runtime_manifest_labels_capability_and_governance_runway(tmp_path):
    runtime = Runtime(
        load_manifest("scripted"),
        events=0,
        seed=1,
        initial_balance_micro=None,
        ledger_path=None,
        router_gamma=0.1,
        provider=ScriptedProvider(),
    )
    observer = RehearsalObserver.for_runtime(runtime, tmp_path)
    observer._write(0)
    report = observer.last_report

    assert report["configuration"]["source"] == "runtime_manifest"
    assert "messages" not in report
    assert "address_enabled" not in report["configuration"]
    runway = report["configuration"]["governance_runway"]
    backstop = runtime.m.evaluation.consequence_backstop_ticks
    assert type(runtime.m.timing.min_ratio) is int
    assert type(backstop) is int
    assert runway["first_activation_lower_bound_ticks"] == runtime.m.timing.min_ratio * backstop
    assert runway["minimum_ticks_through_one_consequence_window"] == (
        runtime.m.timing.min_ratio + 1
    ) * backstop
    assert runway["remaining_lower_bound_ticks"] == runway[
        "minimum_ticks_through_one_consequence_window"
    ]
    assert runway["remaining_nominal_duration_lower_bound_ns"] == (
        runway["remaining_lower_bound_ticks"] * runtime.m.tick_interval_ns
    )
    assert runway["delivered_duration_estimate_ns"] is None
    assert runway["remaining_delivered_duration_estimate_ns"] is None
    assert runway["assurance"].startswith("none;")


def test_recent_window_updates_after_meaningful_buffer_is_full(tmp_path):
    ledger = FakeLedger()
    observer = RehearsalObserver(ledger, tmp_path, admission_report=_admission, max_rows=3)
    observer.attach()
    for index in range(4):
        _tick(ledger, index)
        ledger.append({"kind": "io.call", "payload": "private body"})
        ledger.append({"kind": "invocation", "handle": f"late-{index}", "cost_micro": index})
        ledger.append({"kind": "runtime.event_done", "n": 100 + index})
    report = observer.last_report
    assert report is not None
    assert report["observer"]["ticks"] == 4
    assert report["observer"]["coverage"]["discarded_meaningful_rows"] > 0
    assert report["rows_read"] == 3
    assert any(row.get("handle") == "late-3" for row in observer._rows)
    assert "private body" not in json.dumps(report)


def test_cumulative_cost_is_not_divided_by_a_truncated_useful_window(tmp_path):
    ledger = FakeLedger()
    observer = RehearsalObserver(
        ledger, tmp_path, max_rows=2,
        admission_report=lambda: {"attempted": 2, "known_calls": 2,
                                  "known_micro": 100, "uncertain_calls": 0},
    )
    observer.attach()
    for handle in ("useful-first", "useful-second"):
        ledger.append({"kind": "invocation", "handle": handle, "cost_micro": 7,
                       "useful_decision": True, "independently_supported": True})
    observer._write(0)
    assert observer.last_report["cost_per_useful_decision_micro"] == 50
    ledger.append({"kind": "invocation", "handle": "later", "cost_micro": 0})
    observer._write(1)
    assert observer.last_report["costs"]["known_micro_total"] == 100
    assert observer.last_report["observer"]["coverage"]["status"] == "window_truncated"
    assert observer.last_report["cost_per_useful_decision_micro"] is None


def test_unwritable_output_cannot_change_append_semantics(tmp_path):
    output_file = tmp_path / "already-a-file"
    output_file.write_text("sealed", encoding="utf-8")
    ledger = FakeLedger()
    observer = RehearsalObserver(ledger, output_file, admission_report=_admission)
    observer.attach()
    _tick(ledger, 0)
    assert ledger.append({"kind": "runtime.event_done", "n": 1}) == 1
    assert ledger.entries[-1]["kind"] == "runtime.event_done"


@pytest.mark.gate
def test_runtime_tick_boundaries_write_before_termination(tmp_path):
    runtime = Runtime(
        load_manifest("scripted"),
        events=5,
        seed=1,
        initial_balance_micro=None,
        ledger_path=None,
        router_gamma=0.1,
        provider=ScriptedProvider(),
        kill_at_end=True,
    )
    observer = RehearsalObserver(runtime.ledger, tmp_path, max_rows=32)
    observer.attach()
    original_finish = runtime._finish_budget
    checked_before_termination = []

    def finish_budget():
        checked_before_termination.append((tmp_path / "report.json").exists())
        return original_finish()

    runtime._finish_budget = finish_budget
    runtime.run()
    report = json.loads((tmp_path / "report.json").read_text(encoding="utf-8"))
    assert runtime.ticks_consumed == 5
    assert report["observer"]["ticks"] == runtime.ticks_consumed
    assert checked_before_termination == [True]
    assert report["observer"]["coverage"]["window_rows"] <= 32
