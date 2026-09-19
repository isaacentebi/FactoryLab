"""The paid discrimination probe stays frozen, unspent and free of its own answers.

Nothing here dispatches a request, reads a credential or touches a network: the
tests exercise the capture seam, the preflight freeze and the paid guards only.
"""

import json

import pytest

from factorylab.world.models import ModelResponse
from scripts.edition4_nonfinancial_model_probe import (
    ARMS,
    CAP_MICRO,
    EXPECTED,
    MAX_CALLS,
    MODELS,
    claim_paid_run,
    dispatch_paid,
    main,
    production_judge_spec,
    rendered_text,
    write_preflight,
)
from scripts.edition4_nonfinancial_probe import canonical_norms
from scripts.edition4_rehearsal import PrepaidProvider


@pytest.fixture(scope="module")
def frozen(tmp_path_factory):
    out = tmp_path_factory.mktemp("model-probe")
    assert main(["--out", str(out)]) == 0
    return out, json.loads((out / "preflight.json").read_text())


@pytest.mark.gate
def test_the_default_run_freezes_every_request_and_sends_none(frozen):
    out, preflight = frozen
    assert [path.name for path in sorted(out.iterdir())] == ["preflight.json"]
    assert preflight["max_calls"] == MAX_CALLS == len(MODELS) * len(ARMS)
    assert preflight["cap_micro"] == CAP_MICRO == 1_000_000
    assert len({row["request_sha256"] for row in preflight["inputs"]}) == MAX_CALLS
    assert {row["model"] for row in preflight["inputs"]} == set(MODELS)
    assert {row["arm"] for row in preflight["inputs"]} == set(ARMS)
    assert preflight["source_sha256"] and preflight["norm_source"].endswith(".toml")


@pytest.mark.gate
def test_each_request_is_the_production_commission_for_its_own_arm(frozen):
    _out, preflight = frozen
    norms = {norm["id"]: norm["definition"] for norm in canonical_norms()}
    budgets = {model: production_judge_spec(model).max_tokens for model in MODELS}
    for row in preflight["inputs"]:
        contract_norms = {norm["id"]: norm["definition"] for norm in row["contract"]["norms"]}
        assert contract_norms == norms
        assert all(definition.strip() for definition in contract_norms.values())
        text = rendered_text(row["request"])
        assert "realized_consequence" in text
        assert row["contract"]["producer_outputs"]["observation_claim"]["claim_id"] in text
        for reference in row["evidence_refs"]:
            assert reference in text
        assert row["request"]["model_id"] == row["model"]
        # Each seat keeps its own production budget rather than a probe default.
        assert row["request"]["max_tokens"] == budgets[row["model"]]
    assert len(set(budgets.values())) > 1


@pytest.mark.gate
def test_no_prompt_carries_its_own_expected_answer(frozen):
    _out, preflight = frozen
    assert all(row["expectation_leak"] == 0 for row in preflight["inputs"])
    assert preflight["investigator_only"]["expected"] == EXPECTED
    for row in preflight["inputs"]:
        assert "expected" not in rendered_text(row["request"]).lower().split("expected_result")[0]


@pytest.mark.gate
def test_a_second_run_reproduces_the_freeze_and_a_changed_one_is_refused(frozen, tmp_path):
    out, preflight = frozen
    assert main(["--out", str(out)]) == 0
    assert json.loads((out / "preflight.json").read_text()) == preflight
    stale = dict(preflight)
    stale["inputs"] = preflight["inputs"][:1]
    (tmp_path / "preflight.json").write_text(json.dumps(stale, indent=2, sort_keys=True))
    with pytest.raises(RuntimeError, match="preflight changed"):
        write_preflight(tmp_path)


@pytest.mark.gate
def test_the_paid_marker_is_claimed_once_and_never_reused(tmp_path):
    marker = claim_paid_run(tmp_path)
    assert marker.exists() and json.loads(marker.read_text())["max_calls"] == MAX_CALLS
    with pytest.raises(FileExistsError):
        claim_paid_run(tmp_path)


def _canned_text(row):
    """One well-formed answer for an arm, matching its investigator-only expectation."""
    expected = EXPECTED[row["arm"]]
    finding = {"status": "unknown", "reason": "no eligible fact decides the claim",
               "evidence": []}
    if expected == "contrary":
        finding = {"status": "contrary", "score": 0, "evidence": list(row["evidence_refs"]),
                   "reason": "the independent execution contradicts the claim"}
    elif expected == "supported":
        finding = {"status": "supported", "score": 0.6,
                   "evidence": list(row["evidence_refs"]),
                   "reason": "an independent lineage executed the frozen version"}
    return json.dumps({"rationale": "canned", "verdict": 0.5, "realized_consequence": finding})


class _CannedInner:
    """A provider that answers from a fixed script and never opens a connection."""

    name = "canned"

    def __init__(self, rows, costs):
        self.texts = [_canned_text(row) for row in rows]
        self.costs = costs
        self.calls = 0

    def affordable(self, _model_id, _ceiling_micro):
        return True, ""

    def complete(self, req):
        index = self.calls
        self.calls += 1
        return ModelResponse(req.model_id, self.texts[index], 100, 50, "stop",
                             cost_micro=self.costs[index])


def _canned_run(tmp_path, preflight, costs):
    """Dispatch the frozen preflight against canned bills, through the real admission."""
    (tmp_path / "preflight.json").write_text(json.dumps(preflight, indent=2, sort_keys=True))
    inner = _CannedInner(preflight["inputs"], costs)
    return dispatch_paid(
        tmp_path,
        preflight,
        provider_factory=lambda manifest, admission: PrepaidProvider(
            inner, manifest, admission
        ),
    )


@pytest.mark.gate
def test_a_clean_canned_run_completes_and_keeps_every_answer_on_disk(frozen, tmp_path):
    _out, preflight = frozen
    report = _canned_run(tmp_path, preflight, [1000] * MAX_CALLS)
    assert report["status"] == "completed" and report["bills_settled"] is True
    assert report["completed_calls"] == MAX_CALLS and report["failure"] is None
    assert report["matched"] == MAX_CALLS
    lines = [json.loads(line) for line in (tmp_path / "calls.jsonl").read_text().splitlines()]
    responses = [line for line in lines if line["stage"] == "response"]
    assert len(responses) == MAX_CALLS
    assert all(line["text"] and "cost_micro" in line for line in responses)


@pytest.mark.gate
def test_an_unknown_bill_on_the_final_call_is_not_a_completed_run(frozen, tmp_path):
    """Ten responses arrived, but the last bill is unreadable, so nothing is settled."""
    _out, preflight = frozen
    report = _canned_run(tmp_path, preflight, [1000] * (MAX_CALLS - 1) + [None])
    assert report["completed_calls"] == MAX_CALLS
    assert report["status"] == "incomplete" and report["bills_settled"] is False
    assert report["cost"]["stop_reason"] == "unknown_bill"
    assert report["cost"]["attempted"] == MAX_CALLS
    assert report["cost"]["uncertain_calls"] == 1
    assert report["cost"]["known_calls"] == MAX_CALLS - 1


@pytest.mark.gate
def test_a_provider_that_cannot_be_built_still_leaves_a_report(frozen, tmp_path):
    """A credential or rail failure is evidence too, and carries no provider body."""
    _out, preflight = frozen

    def refuse(_manifest, _admission):
        raise RuntimeError("credential unavailable")

    report = dispatch_paid(tmp_path, preflight, provider_factory=refuse)
    assert report["status"] == "incomplete" and report["failure"] == "RuntimeError"
    assert report["completed_calls"] == 0 and report["records"] == []
    assert json.loads((tmp_path / "report.json").read_text())["failure"] == "RuntimeError"
    failed = [json.loads(line) for line in (tmp_path / "calls.jsonl").read_text().splitlines()]
    assert failed == [{"stage": "failed", "exception": "RuntimeError", "completed_calls": 0,
                       "admission": report["cost"]}]
    assert "credential unavailable" not in (tmp_path / "calls.jsonl").read_text()
