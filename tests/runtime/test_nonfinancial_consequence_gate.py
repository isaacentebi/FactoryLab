"""Edition 4 gate: a nonfinancial consequence reaches real producer learning.

Registration, jailed execution, receipts, commissions, settlement and learning
use production machinery. Actor selection and claim authorship are fixtures;
the evaluator's answer is supplied to _evaluator_step through returned= instead
of a paid model call. These tests evidence the path, not autonomous choice,
usefulness or judgment quality.
"""

import pytest

from factorylab.runtime.grounded import GROUNDED_DEFINITION
from scripts.edition4_nonfinancial_probe import INITIAL_VERDICT, SUPPORTED_SCORE, run_arm


@pytest.fixture(scope="module")
def arms():
    return {
        arm: run_arm(arm)
        for arm in ("independent_use", "contrary_result", "no_evidence", "self_use")
    }


@pytest.mark.gate
def test_an_independent_execution_receipt_settles_the_producer_and_trains_its_router(arms):
    """The receipt the runtime wrote, cited by the final judge, becomes a real score."""
    supported = arms["independent_use"]
    facts = supported["evidence"][0]["payload"]["facts"]
    assert facts["lineage_relation"] == "cross_lineage" and facts["status"] == "executed"
    assert supported["evidence"][0]["kind"] == "ExecutionReceipt:program_result"
    assert supported["settlement"] == [
        {
            "channel": "verdict",
            "score": SUPPORTED_SCORE,
            "status": "settled",
            "definition_version": GROUNDED_DEFINITION,
        }
    ]
    assert supported["producer_router"]["changed"] is True


@pytest.mark.gate
def test_the_emitted_commission_survives_the_loops_own_thaw_into_settlement(arms):
    """The loop emits frozen evidence and thaws it itself; citations are accepted.

    _settle_due_grounded emits its evidence inside an event payload, which
    freezes the list into a tuple of mapping proxies.  _evaluator_step calls
    _to_plain on that payload before rendering the judge's inputs, so the
    references a judge cites are the references settlement validates.  Every
    arm crosses that boundary and no arm records a refused finding.
    """
    for row in arms.values():
        assert row["emitted_evidence_frozen"] is True
        assert row["finding_refusals"] == []
    supported = arms["independent_use"]
    assert supported["final_finding"]["evidence"] == [supported["evidence"][0]["ref"]]
    assert supported["settlement"][0]["score"] == SUPPORTED_SCORE


@pytest.mark.gate
def test_contrary_evidence_settles_a_different_score_than_supporting_evidence(arms):
    """Opposite facts under one frozen claim reach learning as different outcomes."""
    supported, contrary = arms["independent_use"], arms["contrary_result"]
    assert supported["contract_fingerprint"] == contrary["contract_fingerprint"]
    assert supported["initial_verdict"] == contrary["initial_verdict"] == INITIAL_VERDICT
    assert contrary["settlement"][0]["definition_version"] == GROUNDED_DEFINITION
    assert contrary["settlement"][0]["status"] == "settled"
    assert contrary["settlement"][0]["score"] == 0.0
    # EXP3 is gain-based, so an observed zero leaves the arm's weight where it
    # was. The learning difference is between the two arms, not inside one.
    assert supported["producer_router"]["after"] != contrary["producer_router"]["after"]


@pytest.mark.gate
def test_unobserved_and_off_claim_facts_censor_without_a_score(arms):
    """Absent and same-lineage facts settle unknown and train nothing."""
    for arm in ("no_evidence", "self_use"):
        row = arms[arm]
        assert row["final_finding"]["status"] == "unknown"
        assert "score" not in row["final_finding"]
        assert row["settlement"][0]["definition_version"] == f"{GROUNDED_DEFINITION}-unknown"
        assert row["settlement"][0]["status"] == "censored"
        assert row["producer_router"]["changed"] is False
    assert arms["self_use"]["evidence"][0]["payload"]["facts"]["lineage_relation"] == "self"


@pytest.mark.gate
def test_no_arm_moves_money_across_settlement_or_learning(arms):
    """A nonfinancial consequence is scored without the wallet taking part."""
    for row in arms.values():
        assert row["money"]["before_settlement_micro"] == row["money"]["after_learning_micro"]
    assert arms["independent_use"]["money"]["tool_charge_micro"] == 50
    assert arms["no_evidence"]["money"]["tool_charge_micro"] == 0


@pytest.mark.gate
def test_receipt_only_execution_does_not_claim_downstream_consumption(arms):
    """The strongest current receipt stops at invocation and result provenance."""
    supported = arms["independent_use"]
    facts = supported["evidence"][0]["payload"]["facts"]
    assert set(facts) == {
        "tool", "maker", "caller", "maker_handle", "caller_handle", "slot",
        "lineage_relation", "version_sha256", "result_sha256", "status", "cost_micro",
    }
    assert "usefulness" in supported["final_finding"]["reason"]
