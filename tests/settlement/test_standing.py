import json

import pytest

from factorylab.settlement import ConsequenceStanding


def test_defaults_and_censored_only_evaluator_have_neutral_weight(standing):
    assert standing.skill("new") == standing.coverage("new") == 0.0
    assert standing.weight("new") == 0.5
    assert standing.snapshot() == {}
    standing.set_requested("new", 3)
    assert standing.weight("new") == 0.5
    assert standing.coverage("new") == standing.skill("new") == 0.0
    assert standing.snapshot()["new"]["n"] == 0
    assert standing.snapshot()["new"]["settled"] == 0


def test_skill_sign_and_mean_arithmetic_are_per_evaluator(standing):
    standing.set_requested("good", 2)
    standing.record("good", 1.0, 0.75)
    standing.record("good", 0.8, 0.5)
    standing.set_requested("bad", 1)
    standing.record("bad", 0.0, 0.75)
    assert standing.skill("good") == pytest.approx(0.275)
    assert standing.weight("good") == pytest.approx(0.775)
    assert standing.skill("bad") == -0.75
    assert standing.weight("bad") == 0.0
    snapshot = standing.snapshot()
    assert snapshot["good"]["n"] == snapshot["good"]["settled"] == 2
    assert snapshot["good"]["sum_brier"] == 1.8
    assert snapshot["good"]["sum_baseline_brier"] == 1.25
    assert snapshot["good"]["mean_brier"] == 0.9
    assert snapshot["good"]["mean_baseline_brier"] == 0.625
    assert snapshot["good"]["coverage"] == 1.0
    assert ConsequenceStanding(0.75).weight("good") == 0.5


def test_coverage_cap_lifts_exactly_at_minimum_and_never_boosts_bad_skill(standing):
    standing.set_requested("good", 4)
    for _ in range(2):
        standing.record("good", 1.0, 0.5)
    assert standing.coverage("good") == 0.5
    assert standing.weight("good") == 0.5
    standing.record("good", 1.0, 0.5)
    assert standing.coverage("good") == 0.75
    assert standing.weight("good") == 1.0
    standing.set_requested("bad", 4)
    standing.record("bad", 0.5, 0.75)
    assert standing.coverage("bad") == 0.25
    assert standing.weight("bad") == 0.25


@pytest.mark.parametrize(("score", "baseline", "expected"), [(1, 0, 1.0), (0, 1, 0.0)])
def test_weight_clips_at_both_ends(score, baseline, expected):
    standing = ConsequenceStanding(0)
    standing.set_requested("judge", 1)
    standing.record("judge", score, baseline)
    assert standing.weight("judge") == expected


def test_snapshot_is_json_serialisable_detached_and_ledger_ready(standing, ledger):
    standing.set_requested("judge", 2)
    standing.record("judge", 0.9, 0.75)
    snapshot = standing.snapshot()
    assert json.loads(json.dumps(snapshot, allow_nan=False)) == snapshot
    ledger.append({"kind": "standing", "standing": snapshot})
    snapshot["judge"]["requested"] = 0
    assert standing.coverage("judge") == 0.5
    assert ledger.verify()


@pytest.mark.parametrize("value", [-0.1, 1.1, float("nan"), float("inf"), True, "0.5"])
def test_invalid_coverage_threshold_and_scores_are_rejected(value, standing):
    with pytest.raises(ValueError):
        ConsequenceStanding(value)
    for score, baseline in ((value, 0.5), (0.5, value)):
        with pytest.raises(ValueError):
            standing.record("judge", score, baseline)
    assert standing.snapshot() == {}


@pytest.mark.parametrize("value", [-1, 1.5, True, "1", None])
def test_invalid_requested_counts_are_rejected(value, standing):
    with pytest.raises(ValueError):
        standing.set_requested("judge", value)
    assert standing.snapshot() == {}
