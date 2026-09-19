"""The offline nonfinancial probe stays offline, frozen and honest about itself."""

import pytest

from scripts.edition4_nonfinancial_probe import ARMS, canonical_norms, run_probe


@pytest.fixture(scope="module")
def probe():
    return run_probe()


@pytest.mark.gate
def test_the_probe_runs_the_real_runtime_without_any_paid_or_networked_call(probe):
    assert probe["mode"] == "offline_real_runtime"
    assert probe["exchange"] == "FakeExchange" and probe["provider"] == "ScriptedProvider"
    assert probe["paid_model_calls"] == probe["network_calls"] == 0
    assert probe["final_evaluator"]["is_model"] is False
    assert [row["arm"] for row in probe["results"]] == list(ARMS)


@pytest.mark.gate
def test_the_probe_names_every_fixture_it_stands_in_for(probe):
    """Forced actor selection, assigned claim and stand-in judgement are all declared."""
    scaffold = probe["scaffold"]
    assert "forced" in scaffold["actor_selection"]
    assert "investigator-authored" in scaffold["claim_authorship"]
    assert "stand-in" in scaffold["final_judgement"]
    for row in probe["results"]:
        assert "not an observed participant choice" in row["frozen_claim"]["provenance"]


@pytest.mark.gate
def test_every_arm_freezes_one_claim_under_the_production_charter_norms(probe):
    norms = {norm["id"]: norm["definition"] for norm in canonical_norms()}
    assert len(norms) >= 4 and all(definition.strip() for definition in norms.values())
    fingerprints = {row["contract_fingerprint"] for row in probe["results"]}
    claims = {row["frozen_claim"]["claim_id"] for row in probe["results"]}
    assert len(fingerprints) == len(claims) == 1
    for row in probe["results"]:
        assert {norm["id"]: norm["definition"] for norm in row["frozen_norms"]} == norms
        claim = row["frozen_claim"]
        assert claim["claim_kind"] == "execution_observation"
        assert "does not claim that the execution helped anyone" in claim["not_claimed"]


@pytest.mark.gate
def test_the_arms_differ_only_in_the_facts_the_runtime_produced(probe):
    findings = {row["arm"]: row["final_finding"]["status"] for row in probe["results"]}
    assert findings == {
        "independent_use": "supported",
        "contrary_result": "contrary",
        "no_evidence": "unknown",
        "self_use": "unknown",
        "version_mismatch": "unknown",
    }
    for row in probe["results"]:
        for reference in row["final_finding"]["evidence"]:
            assert reference in {item["ref"] for item in row["evidence"]}
        assert row["money"]["before_settlement_micro"] == row["money"]["after_learning_micro"]


@pytest.mark.gate
def test_dependency_witness_refuses_to_relabel_invocation_as_consumption(probe):
    witness = probe["dependency_witness"]
    assert witness["downstream_consumed"]["status"] == "not_expressible"
    missing = witness["downstream_consumed"]["missing_observation"]
    assert "later caller decision, output or independently observed outcome" in missing
    assert witness["receipt_only_arm"] == "independent_use"
    receipt_only = next(row for row in probe["results"] if row["arm"] == "independent_use")
    facts = receipt_only["evidence"][0]["payload"]["facts"]
    assert facts["status"] == "executed" and facts["lineage_relation"] == "cross_lineage"
    assert "consumed" not in facts and "useful" not in facts


@pytest.mark.gate
def test_delayed_evidence_reports_maturity_snapshot_and_timeout_without_policy_change(probe):
    witness = probe["delayed_evidence_witness"]
    assert witness["learner_policy_changed"] is False
    cases = {row["timing"]: row for row in witness["cases"]}
    before = cases["before_maturity"]
    assert before["at_tick"] < witness["maturity_tick"] < witness["timeout_tick"]
    assert before["eligible_in_frozen_commission"] is True
    assert before["finding"] == "supported" and before["settlement_status"] == "settled"
    assert before["router_changed"] is True
    late = cases["after_maturity_snapshot_before_timeout"]
    assert late["at_tick"] == witness["maturity_tick"]
    assert late["frozen_commission_refs"] == []
    assert len(late["currently_observable_refs"]) == 1
    assert late["eligible_in_frozen_commission"] is False
    assert late["finding"] == "unknown" and late["settlement_status"] == "censored"
    assert late["router_changed"] is False
    timeout = cases["assessment_timeout"]
    assert timeout["at_tick"] == witness["timeout_tick"]
    assert timeout["finding"] == "unknown" and timeout["settlement_status"] == "censored"
    assert timeout["router_changed"] is False
