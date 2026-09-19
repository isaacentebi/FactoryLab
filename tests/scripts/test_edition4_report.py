"""Offline checks for the bounded Edition 4 report."""

import json
from dataclasses import replace

import pytest

from factorylab.runtime.loop import Runtime
from factorylab.runtime.worlds import load_manifest
from factorylab.world.exchange import FakeExchange
from factorylab.world.scripted import ScriptedProvider
from scripts.edition4_report import (
    ReportInputError,
    build_report,
    load_rows,
    main,
    render_html,
)


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


def test_actual_address_rows_count_replays_as_attempts_but_not_new_delivery():
    report = build_report(
        [
            {"kind": "address.delivered", "handle": "m1", "message_id": "msg-1"},
            {"kind": "address.replayed", "handle": "m1", "message_id": "msg-1"},
            {"kind": "address.refused", "handle": "m2", "reason": "unknown recipient"},
        ]
    )

    assert report["messages"]["attempted"] == 3
    assert report["messages"]["delivered"] == 1
    assert report["messages"]["replayed"] == 1
    assert report["messages"]["refused"] == 1
    assert report["messages"]["unknown_delivery"] == 0


def test_address_zero_is_labeled_from_configuration_not_inferred_as_non_use():
    unknown = build_report([])
    disabled = build_report(
        [], configuration={"source": "runtime_manifest", "address_enabled": False}
    )
    enabled = build_report(
        [], configuration={"source": "runtime_manifest", "address_enabled": True}
    )

    assert unknown["messages"]["capability_status"] == "unknown_metadata"
    assert disabled["messages"]["capability_status"] == "disabled"
    assert enabled["messages"]["capability_status"] == "enabled_but_unused"
    assert "supplied/recent evidence window" in enabled["messages"]["capability_label"]
    assert "zero is not non-use" in unknown["messages"]["capability_label"]


def test_offline_runtime_address_export_reaches_the_dashboard():
    manifest = load_manifest("worlds/scripted.toml")
    manifest = replace(manifest, tools=replace(manifest.tools, address_enabled=True))
    runtime = Runtime(
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
    sender, recipient = list(runtime.assemblies)[:2]
    args = {"tool": "address.send", "args": {"recipient": recipient, "text": "x"}}
    runtime._run_tool(sender, "decision-1", args, slot="tool:0")
    runtime._run_tool(sender, "decision-1", args, slot="tool:0")
    runtime._run_tool(
        sender, "decision-2",
        {"tool": "address.send", "args": {"recipient": "missing", "text": "x"}},
        slot="tool:0",
    )
    events = [row for row in runtime.ledger._recovery_items() if row["kind"].startswith("address.")]
    report = build_report(events)

    assert report["messages"]["attempted"] == 3
    assert report["messages"]["delivered"] == 1
    assert report["messages"]["replayed"] == 1
    assert report["messages"]["refused"] == 1


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
        drip=False,
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
    assert report["messages"]["attempted"] == 1
    assert report["messages"]["delivered"] == 1
    assert report["reusable_calls"]["cross_lineage_refs"] == ["cross-1"]
    assert report["income"]["external_confirmed_refs"] == ["customer-1"]
    assert report["five_questions"]["subsequent_consumption"]["status"] == "evidence"
    assert report["five_questions"]["resolved_evidence_changed_decision"]["status"] == "evidence"
    assert "causal" in " ".join(report["caveats"])


def test_html_escapes_refs_and_has_no_operator_controls():
    report = build_report(
        [{"kind": "message", "id": '<script>alert("x")</script>', "status": "unknown"}]
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
