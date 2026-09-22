"""Offline checks for the bounded Edition 4 report."""

import json
from copy import deepcopy

import pytest

from factorylab.runtime.loop import Runtime
from factorylab.runtime.worlds import load_manifest
from factorylab.world.scripted import ScriptedProvider
from scripts.edition4_report import (
    ReportInputError,
    _terminated_exports,
    build_behavioral_trace,
    build_report,
    compare_rehearsals,
    load_rows,
    main,
    render_html,
)


def _postmortem_fixture():
    report = {
        "status": "completed",
        "summary": {"terminated": True, "tools": ["venue.close", "world.read"]},
    }
    events = [
        {"seq": 1, "kind": "decision.open", "handle": "p1"},
        {"seq": 2, "kind": "invocation", "handle": "p1", "assembly_id": "producer-a",
         "status": "ok", "stop_reason": "stop"},
        {"seq": 3, "kind": "tool.call", "handle": "p1", "tool": "venue.close",
         "ok": True, "outcome": "ok"},
        {"kind": "tool.call", "handle": "p1", "tool": "missing.tool",
         "ok": False, "outcome": "failed"},
        {"kind": "return.sections_dropped", "handle": "p1", "assembly_id": "producer-a",
         "dropped": [{"section": "tool_calls",
                      "reason": "item 0: malformed arguments"}]},
        {"seq": 4, "kind": "action.classified", "handle": "p1", "action": "order"},
        {"seq": 5, "kind": "event", "event": {"kind": "ProducerReturn", "payload": {
            "about_handle": "p1", "status": "ok", "outputs": {"action": "defer"}}}},
        {"seq": 6, "kind": "event", "event": {"kind": "ProducerReturn", "payload": {
            "about_handle": "p1", "status": "ok", "outputs": {"action": "defer"}}}},
        {"seq": 7, "kind": "consequence.finding", "handle": "p1",
         "judge_handle": "judge-final", "status": "supported", "score": 0.4},
        {"seq": 8, "kind": "decision.settle", "return": {
            "handle": "p1", "status": "settled",
            "definition_version": "realized-consequence-v2", "sampling_ref": "judge-final"}},
        {"seq": 9, "kind": "event", "event": {"kind": "Verdict", "payload": {
            "about_handle": "p1", "evaluator_handle": "judge-final",
            "grounded_consequence": True, "verdict": 0.4}}},
        {"seq": 10, "kind": "decision.open", "handle": "p2"},
        {"seq": 11, "kind": "invocation", "handle": "p2", "assembly_id": "producer-a",
         "status": "malformed", "stop_reason": "reasoning_only"},
        {"seq": 12, "kind": "event", "event": {"kind": "ProducerReturn", "payload": {
            "about_handle": "p2", "status": "malformed", "outputs": {}}}},
        {"seq": 13, "kind": "decision.open", "handle": "p3"},
        {"seq": 14, "kind": "invocation", "handle": "p3", "assembly_id": "producer-b",
         "status": "ok", "stop_reason": "stop"},
        {"seq": 15, "kind": "event", "event": {"kind": "ProducerReturn", "payload": {
            "about_handle": "p3", "status": "ok", "outputs": {"action": "hold"}}}},
        {"seq": 16, "kind": "consequence.finding", "handle": "p3",
         "judge_handle": "judge-3", "status": "unknown", "score": None},
        {"seq": 17, "kind": "outcome.addressed", "handle": "p3",
         "assembly_id": "producer-b", "evidence": "judge-3", "item": 7},
        {"seq": 18, "kind": "decision.settle", "return": {
            "handle": "p3", "status": "censored",
            "definition_version": "realized-consequence-v2-unknown"}},
        {"seq": 19, "kind": "event", "event": {"kind": "Verdict", "payload": {
            "about_handle": "p3", "evaluator_handle": "judge-3",
            "grounded_consequence": True, "verdict": None}}},
        {"seq": 20, "kind": "decision.open", "handle": "p4"},
        {"seq": 21, "kind": "invocation", "handle": "p4", "assembly_id": "producer-b",
         "status": "ok", "stop_reason": "stop"},
        {"seq": 22, "kind": "outcome.ack", "handle": "older-item",
         "assembly_id": "producer-b", "cursor": 9},
        {"seq": 23, "kind": "event", "event": {"kind": "ProducerReturn", "payload": {
            "about_handle": "p4", "status": "ok", "outputs": {"action": "investigate"}}}},
        {"seq": 24, "kind": "invocation", "handle": "valid-length",
         "assembly_id": "judge", "status": "ok", "stop_reason": "length"},
    ]
    return report, events


@pytest.mark.parametrize("rejection_kind", ["return.sections_dropped", "return.validation_failed"])
def test_postmortem_trace_separates_replays_execution_and_mechanical_failures(rejection_kind):
    report, events = _postmortem_fixture()
    for row in events:
        if row.get("kind") == "return.sections_dropped":
            row["kind"] = rejection_kind
    trace = build_behavioral_trace(report, events)

    work = trace["producer_work"]
    assert (work["unique_decisions"], work["emissions"], work["re_emitted_contracts"]) == (4, 5, 1)
    first = work["decisions"][0]
    assert first["declared_action"] == "defer"
    assert first["classified_action"] == "order"
    assert first["executed_tool_calls"] == ["venue.close"]
    assert first["pre_dispatch_rejected_sections"] == [{"section": "tool_calls"}]
    assert work["valid_unique_decisions"] == 3
    assert trace["model_output_failures"] == {"reasoning_only": 1}
    assert trace["pre_dispatch_rejections"] == {
        "sections": 1,
        "by_section": {"tool_calls": 1},
        "tool_call_sections_without_tool_identity": 1,
    }
    assert trace["capabilities"]["world.read"] == {
        "available": True, "attempted": 0, "executed": 0,
        "failed": 0, "uncertain": 0, "refused": 0,
    }
    assert trace["capabilities"]["missing.tool"] == {
        "available": False, "attempted": 1, "executed": 0,
        "failed": 1, "uncertain": 0, "refused": 0,
    }
    assert trace["interpretation"]["status"] == "mechanically_inconclusive"


def test_postmortem_trace_follows_runtime_finding_address_settle_publish_ack_order():
    report, events = _postmortem_fixture()
    trace = build_behavioral_trace(report, events)
    chains = trace["final_feedback_chains"]

    missing, delivered = chains
    assert missing["delivery_pathway"] == "missing_or_malformed_chain_order"
    assert missing["chain_ordered"] is False
    assert missing["model_exposure"] == "unknown"
    assert missing["next_return"] == {
        "handle": "p2", "status": "malformed",
        "declared_action": None, "classified_action": None,
    }
    assert missing["next_return_valid"] is False
    assert delivered["delivery_pathway"] == "addressed_and_acknowledged"
    assert delivered["chain_ordered"] is True
    assert delivered["acknowledged_receipt"] is True
    assert delivered["model_exposure"] == "claimed_received"
    assert delivered["finding_status"] == "unknown"
    assert delivered["finding_score"] is None
    assert delivered["settlement_status"] == "censored"
    assert delivered["next_return"] == {
        "handle": "p4", "status": "ok",
        "declared_action": "investigate", "classified_action": None,
    }
    assert delivered["next_return_valid"] is True
    assert "does not establish" in trace["interpretation"]["caveat"]


@pytest.mark.parametrize("ack_seq", [20, 24])
def test_postmortem_ack_must_occur_inside_selected_invocation(ack_seq):
    report, events = _postmortem_fixture()
    ack = next(row for row in events if row.get("kind") == "outcome.ack")
    ack["seq"] = ack_seq

    delivered = build_behavioral_trace(report, events)["final_feedback_chains"][1]

    assert delivered["chain_ordered"] is True
    assert delivered["acknowledged_receipt"] is False
    assert delivered["model_exposure"] == "unknown"
    assert delivered["delivery_pathway"] == "addressed_without_ack"


def test_postmortem_malformed_chain_order_is_inconclusive():
    report, events = _postmortem_fixture()
    address = next(row for row in events
                   if row.get("kind") == "outcome.addressed" and row.get("handle") == "p3")
    address["seq"] = 19

    trace = build_behavioral_trace(report, events)
    delivered = trace["final_feedback_chains"][1]

    assert delivered["addressed_to_inbox"] is True
    assert delivered["chain_ordered"] is False
    assert delivered["delivery_pathway"] == "missing_or_malformed_chain_order"
    assert trace["interpretation"]["status"] == "mechanically_inconclusive"


def test_postmortem_refuses_before_opening_events(tmp_path):
    report = tmp_path / "report.json"
    report.write_text(json.dumps({"status": "prepared", "summary": {"terminated": False}}))
    absent_events = tmp_path / "must-not-be-opened.json"

    with pytest.raises(ReportInputError, match="not completed and terminated"):
        _terminated_exports(report, absent_events)


def test_postmortem_refuses_cross_directory_events_before_reading_them(tmp_path):
    run = tmp_path / "run-a"
    run.mkdir()
    report = run / "report.json"
    report.write_text(json.dumps({"status": "completed", "summary": {"terminated": True}}))
    other_events = tmp_path / "run-b" / "events.json"

    with pytest.raises(ReportInputError, match="share one resolved run directory"):
        _terminated_exports(report, other_events)


def test_postmortem_without_final_findings_is_unmeasured():
    trace = build_behavioral_trace(
        {"status": "completed", "summary": {"terminated": True, "tools": []}}, []
    )

    assert trace["final_finding_statuses"] == {}
    assert trace["final_feedback_chains"] == []
    assert trace["interpretation"]["status"] == "unmeasured"


def test_empty_data_keeps_unmeasured_metrics_unknown():
    report = build_report([])

    assert report["rows_read"] == 0
    assert report["costs"]["p50_micro"] is None
    assert report["costs"]["p90_micro"] is None
    assert report["costs"]["known_bill_count"] == 0
    assert report["cost_per_useful_decision_micro"] is None
    assert all(q["status"] == "unknown" for q in report["five_questions"].values())
    assert report["income"]["external_confirmed_refs"] == []


def test_unknown_bill_is_not_free_and_zero_bill_is_literal(tmp_path):
    source = tmp_path / "rows.json"
    source.write_text(
        json.dumps(
            [
                {"kind": "model_call", "id": "zero", "cost_micro": 0, "status": "settled"},
                {"kind": "model_call", "id": "missing", "status": "settled"},
                {"kind": "model_call", "id": "priced", "cost_micro": 10, "status": "settled"},
            ]
        )
    )

    report = build_report(load_rows([source]))
    assert report["costs"]["known_bill_count"] == 2
    assert report["costs"]["unknown_bill_count"] == 1
    assert report["costs"]["p50_micro"] == 0
    assert report["costs"]["p90_micro"] == 10
    assert report["costs"]["known_micro_total"] == 10


def test_runner_summary_export_preserves_unknown_cost_coverage(tmp_path):
    source = tmp_path / "report.json"
    source.write_text(
        json.dumps(
            {
                "status": "completed",
                "cost": {
                    "attempted": 3,
                    "known_calls": 2,
                    "known_micro": 12,
                    "uncertain_calls": 1,
                    "uncertain_micro": 8,
                },
                "summary": {"stats": {"events": 4}},
            }
        )
    )

    report = build_report(load_rows([source]))
    assert report["costs"]["calls"] == 3
    assert report["costs"]["known_bill_count"] == 2
    assert report["costs"]["unknown_bill_count"] == 1
    assert report["costs"]["p50_micro"] is None
    assert report["status_counts"]["unknown"] == 0


def test_status_counts_skip_unreported_routine_rows_and_preserve_explicit_quality():
    report = build_report(
        [
            {"kind": "io.call", "id": "io-1"},
            {"kind": "forecast.seal", "handle": "forecast-1"},
            {"kind": "invocation", "handle": "bad", "status": {"private": "body"}},
            {"kind": "order.submit", "handle": "uncertain", "status": "uncertain"},
            {"kind": "invocation", "handle": "failed", "status": "failed"},
            {"kind": "invocation", "handle": "unknown", "status": "unknown"},
        ]
    )

    counts = report["status_counts"]
    assert counts["malformed"] == 1
    assert counts["uncertain"] == 1
    assert counts["failed"] == 1
    assert counts["unknown"] == 1
    assert sum(counts.values()) == 4
    assert "routine rows without status are excluded" in report["status_count_basis"]


def test_request_child_links_invocations_to_root_cost_tree():
    report = build_report(
        [
            {"kind": "invocation", "handle": "root", "cost": 10, "status": "ok"},
            {
                "kind": "request.child",
                "handle": "child",
                "resource_liability": "root",
            },
            {"kind": "invocation", "handle": "child", "cost": 5, "status": "ok"},
            {
                "kind": "evidence",
                "id": "useful-1",
                "useful_decision": True,
                "independently_supported": True,
            },
        ]
    )

    assert report["costs"]["basis"] == "root_decision_trees"
    assert report["costs"]["root_count"] == 1
    assert report["costs"]["root_known_micro"] == {"root": 10}
    assert report["costs"]["known_micro_total"] == 10
    assert report["costs"]["p50_micro"] == 10
    assert report["cost_per_useful_decision_micro"] == 10


def test_linked_invocations_do_not_substitute_child_cost_for_unknown_root_bill():
    report = build_report(
        [
            {
                "kind": "request.child",
                "handle": "child",
                "resource_liability": "root",
            },
            {"kind": "invocation", "handle": "child", "cost": 5, "status": "ok"},
        ]
    )

    assert report["costs"]["known_micro_total"] is None
    assert report["costs"]["known_bill_count"] == 0
    assert report["costs"]["unknown_bill_count"] == 1
    assert report["costs"]["roots_with_unknown_bills"] == ["root"]


def test_linked_cost_total_keeps_unlinked_top_level_invocations_once():
    report = build_report(
        [
            {"kind": "invocation", "handle": "root", "cost": 10, "status": "ok"},
            {"kind": "request.child", "handle": "child", "resource_liability": "root"},
            {"kind": "invocation", "handle": "child", "cost": 5, "status": "ok"},
            {"kind": "invocation", "handle": "standalone", "cost": 7, "status": "ok"},
        ]
    )

    assert report["costs"]["known_micro_total"] == 17
    assert report["costs"]["known_bill_count"] == 2
    assert report["costs"]["unknown_bill_count"] == 0
    assert report["costs"]["unlinked_call_count"] == 1


def test_provider_journal_and_admission_summary_precede_root_invocation_costs():
    linked = [
        {"kind": "invocation", "handle": "root", "cost": 10, "status": "ok"},
        {"kind": "request.child", "handle": "child", "resource_liability": "root"},
        {"kind": "invocation", "handle": "child", "cost": 5, "status": "ok"},
        {"kind": "model_call", "id": "provider-1", "cost_micro": 70, "status": "settled"},
    ]

    provider_report = build_report(linked)
    assert provider_report["costs"]["known_micro_total"] == 70
    assert provider_report["costs"]["unknown_unit_count"] == 0

    admission_report = build_report(
        linked
        + [
            {
                "kind": "report.summary",
                "summary_cost": {
                    "attempted": 1,
                    "known_calls": 1,
                    "known_micro": 100,
                    "uncertain_calls": 0,
                },
            }
        ]
    )
    assert admission_report["costs"]["known_micro_total"] == 100


def test_invocation_cost_is_authoritative_and_tool_rows_are_not_double_counted():
    report = build_report(
        [
            {"kind": "invocation", "handle": "d1", "cost": 30, "status": "ok"},
            {"kind": "tool.call", "handle": "d1", "cost": 7, "status": "executed"},
            {"kind": "metering.uncertain", "handle": "d2", "provisional_micro": 40},
        ]
    )

    assert report["costs"]["calls"] == 2
    assert report["costs"]["known_micro_total"] == 30
    assert report["costs"]["known_bill_count"] == 1
    assert report["costs"]["unknown_bill_count"] == 1


def test_execution_receipts_are_deduplicated_by_program_use_facts_without_bodies():
    facts = {
        "tool": "made-here",
        "maker": "maker",
        "caller": "caller",
        "maker_handle": "creation-1",
        "caller_handle": "call-1",
        "lineage_relation": "cross_lineage",
        "version_sha256": "v" * 64,
        "result_sha256": "r" * 64,
        "status": "executed",
        "cost_micro": 50,
        "private_result": "must not be rendered",
    }
    report = build_report(
        [
            {"kind": "receipt.execution", "id": "exec-maker", "receipt": {
                "kind": "program_result", "handle": "creation-1", "facts": facts,
            }},
            {"kind": "receipt.execution", "id": "exec-caller", "receipt": {
                "kind": "program_result", "handle": "call-1", "facts": facts,
            }},
        ]
    )

    assert report["reusable_calls"]["cross_lineage_refs"] == ["exec-maker"]
    assert report["reusable_calls"]["same_lineage_refs"] == []
    assert "private_result" not in json.dumps(report)


def test_trial_assembly_transfer_is_funded_child_not_external_income():
    report = build_report(
        [{
            "kind": "budget",
            "op": "transfer",
            "src": "founder",
            "dst": "child-1",
            "amount": 100,
            "reason": "trial:assembly",
        }]
    )

    assert report["funded_children"]["children"] == 1
    assert report["funded_children"]["funded_confirmed"] == 1
    assert report["income"]["external_confirmed_refs"] == []


def test_income_views_are_not_added_to_the_authoritative_external_receipt():
    report = build_report(
        [
            {
                "kind": "wallet.settle",
                "handle": "income:service:tx-1",
                "reason": "income",
                "amount": 25,
            },
            {
                "kind": "budget",
                "op": "income",
                "assembly_id": "seller",
                "reason": "income:service:tx-1",
                "amount": 25,
            },
        ]
    )

    assert report["income"]["external_confirmed_count"] == 1
    assert report["income"]["external_confirmed_micro"] == 25
    assert report["income"]["unknown_provenance"] == 1
    assert report["income"]["unverified_records"] == ["unreferenced"]


def test_nested_event_bus_rows_surface_observed_assessment_change_and_forecast_state():
    rows = [
        {
            "seq": 10,
            "kind": "event",
            "event": {
                "id": "v-initial",
                "kind": "Verdict",
                "payload": {"about_handle": "decision-1", "verdict": 0.2},
            },
        },
        {
            "seq": 11,
            "kind": "event",
            "event": {
                "id": "forecast-observed",
                "kind": "ForecastSettled",
                "payload": {"handle": "forecast-1", "y": 0},
            },
        },
        {
            "seq": 12,
            "kind": "consequence.finding",
            "handle": "decision-1",
            "status": "supported",
            "score": 0.8,
            "evidence": ["event:11"],
        },
        {
            "seq": 13,
            "kind": "event",
            "event": {
                "id": "v-grounded",
                "kind": "Verdict",
                "payload": {
                    "about_handle": "decision-1",
                    "verdict": 0.8,
                    "grounded_consequence": True,
                },
            },
        },
        {
            "seq": 14,
            "kind": "event",
            "event": {
                "id": "forecast-censored",
                "kind": "ForecastSettled",
                "payload": {"handle": "forecast-2", "y": None},
            },
        },
    ]
    report = build_report(rows)

    assert report["forecast_learning"]["forecast"] == {
        "settled_observed": 1,
        "unmeasured": 1,
    }
    assert report["assessment_changes"][0]["grounded_final"] is True
    assert report["assessment_changes"][0]["changed"] is True
    question = report["five_questions"]["resolved_evidence_changed_decision"]
    assert question["status"] == "evidence"
    assert question["evidence_refs"] == ["event:10", "decision-1"]
    assert "not causal proof" in question["reason"]
    assert "payload" not in json.dumps(report)


@pytest.mark.gate
def test_offline_runtime_export_uses_invocation_rows_once():
    runtime = Runtime(
        load_manifest("scripted"),
        events=1,
        seed=1,
        initial_balance_micro=None,
        ledger_path=None,
        router_gamma=0.1,
        provider=ScriptedProvider(),
    )
    runtime.run()
    events = [row for row in runtime.ledger._recovery_items() if row.get("kind") != "snapshot"]
    invocations = [row for row in events if row.get("kind") == "invocation"]
    report = build_report(events)

    assert invocations
    assert report["costs"]["calls"] == len(invocations)
    assert report["costs"]["known_micro_total"] == sum(row["cost"] for row in invocations)


def test_wash_activity_does_not_answer_usefulness_or_income():
    report = build_report(
        [
            {
                "kind": "reusable_call",
                "id": "wash-1",
                "reusable": True,
                "lineage_relation": "same_lineage",
                "status": "executed",
                "cost_micro": 1,
            },
            {
                "kind": "message",
                "id": "wash-message",
                "reusable": True,
                "sender_lineage": "lineage-a",
                "recipient_lineage": "lineage-a",
                "status": "delivered",
            },
            {"kind": "income", "id": "internal-1", "source": "internal", "status": "settled"},
        ]
    )

    assert report["reusable_calls"]["same_lineage_refs"] == ["wash-1"]
    assert report["reusable_calls"]["cross_lineage_refs"] == []
    assert report["five_questions"]["subsequent_consumption"]["status"] == "unknown"
    assert report["income"]["external_confirmed_refs"] == []
    assert report["income"]["internal_refs"] == ["internal-1"]


def test_explicit_evidence_and_external_income_are_linked_without_claiming_causality():
    report = build_report(
        [
            {
                "kind": "invocation",
                "handle": "decision-1",
                "cost": 7,
                "status": "settled",
            },
            {
                "kind": "request.child",
                "handle": "child-1",
                "resource_liability": "decision-1",
            },
            {
                "kind": "invocation",
                "handle": "child-1",
                "cost": 3,
                "status": "ok",
            },
            {
                "kind": "reusable_call",
                "id": "cross-1",
                "reusable": True,
                "sender_lineage": "a",
                "recipient_lineage": "b",
                "status": "executed",
            },
            {
                "kind": "message",
                "id": "message-1",
                "delivered": True,
            },
            {
                "kind": "income",
                "id": "customer-1",
                "source": "external",
                "amount_micro": 5,
                "confirmed": True,
                "status": "settled",
            },
            {
                "kind": "evidence",
                "id": "receipt-1",
                "consumed_in_subsequent_work": True,
                "resolved_evidence_changed_judgment": True,
            },
        ]
    )

    assert report["costs"]["basis"] == "root_decision_trees"
    assert "messages" not in report
    assert report["reusable_calls"]["cross_lineage_refs"] == ["cross-1"]
    assert report["income"]["external_confirmed_refs"] == ["customer-1"]
    assert report["five_questions"]["subsequent_consumption"]["status"] == "evidence"
    assert report["five_questions"]["resolved_evidence_changed_decision"]["status"] == "evidence"
    assert "causal" in " ".join(report["caveats"])


def test_html_escapes_refs_and_has_no_operator_controls():
    report = build_report(
        [{"kind": "income", "id": '<script>alert("x")</script>', "source": "external",
          "amount_micro": 5, "confirmed": True, "status": "settled"}]
    )
    rendered = render_html(report)

    assert "<script>alert" not in rendered
    assert "&lt;script&gt;" in rendered
    assert "<input" not in rendered
    assert "<button" not in rendered
    assert "onclick" not in rendered
    assert "static report" in rendered.lower()


def test_cli_requires_explicit_files_and_writes_only_static_outputs(tmp_path):
    with pytest.raises(ReportInputError):
        load_rows([tmp_path])

    source = tmp_path / "export.json"
    source.write_text(json.dumps({"rows": [{"kind": "call", "cost_micro": 0}]}))
    output = tmp_path / "report"
    assert main(["--input", str(source), "--out", str(output)]) == 0
    assert sorted(path.name for path in output.iterdir()) == ["index.html", "report.json"]
    assert "payload" not in (output / "report.json").read_text()


def _screen_pair():
    control = {
        "status": "completed", "source": {"sha256": "code", "runner_sha256": "runner"},
        "world": {"manifest": {"name": "control", "exchange": {"client_namespace": "one"},
                               "evaluation": {"producer_feedback": "verdict"},
                               "charter": {"norms": ["truth"]}}},
        "cost": {"cap_micro": 3_000_000, "max_calls": 500, "attempted": 20,
                 "known_calls": 20, "known_micro": 5000,
                 "uncertain_calls": 0, "uncertain_micro": 0},
        "venue_before": {"positions": [], "open_orders": []},
        "venue_after": {"positions": [], "open_orders": []},
        "protocol": {"duration_ns": 1800, "planned_tick_ceiling": 180,
                     "minimum_delivered_ticks": 60, "no_live_parameter_changes": True,
                     "no_horizon_extension": True},
        "behavioral_screen": {"status": "sufficient"},
    }
    treatment = deepcopy(control)
    treatment["world"]["manifest"]["evaluation"]["producer_feedback"] = "realized"
    treatment["world"]["manifest"]["exchange"]["client_namespace"] = "two"
    return control, treatment


def test_comparison_requires_matching_physics_and_known_bills():
    control, treatment = _screen_pair()
    result = compare_rehearsals(control, treatment, factors=["feedback"])
    assert result["status"] == "screen_complete"
    assert result["manifest_difference_paths"] == ["evaluation.producer_feedback"]
    result = compare_rehearsals(control, treatment, factors=["feedback", "prompt"])
    assert result["status"] == "unmatched"
    assert "declared factor prompt did not change" in result["problems"]
    treatment["world"]["manifest"]["charter"]["norms"] = ["profit"]
    result = compare_rehearsals(control, treatment, factors=["feedback"])
    assert result["status"] == "unmatched"
    assert result["unexpected_difference_paths"] == ["charter.norms.0"]
    assert "profit" not in json.dumps(result)  # no population-facing text exported
    control, treatment = _screen_pair()
    treatment["cost"]["known_calls"] = 19
    assert compare_rehearsals(control, treatment, factors=["feedback"])["status"] == "unmatched"


def test_comparison_distinguishes_unfinished_evidence_from_unmatched_source():
    control, treatment = _screen_pair()
    treatment["behavioral_screen"]["status"] = "inconclusive"
    assert compare_rehearsals(control, treatment, factors=["feedback"])["status"] == "inconclusive"
    treatment["source"]["sha256"] = "different"
    assert compare_rehearsals(control, treatment, factors=["feedback"])["status"] == "unmatched"
    control, treatment = _screen_pair()
    treatment["venue_after"]["positions"] = [{"size": "1"}]
    assert compare_rehearsals(control, treatment, factors=["feedback"])["status"] == "unmatched"
    control, treatment = _screen_pair()
    del treatment["cost"]["cap_micro"]
    assert compare_rehearsals(control, treatment, factors=["feedback"])["status"] == "unmatched"

    control, treatment = _screen_pair()
    treatment["protocol"]["duration_ns"] *= 2
    result = compare_rehearsals(control, treatment, factors=["feedback"])
    assert result["status"] == "unmatched"
    assert "protocol duration_ns missing or different" in result["problems"]
