import pytest

from factorylab.settlement import PrevalenceBaseline, brier


@pytest.mark.parametrize(
    ("q", "y", "expected"),
    [
        (0.0, 0, 1.0),
        (1.0, 1, 1.0),
        (0.0, 1, 0.0),
        (1.0, 0, 0.0),
        (0.5, 0, 0.75),
        (0.5, 1, 0.75),
        (0.8, 1, 0.96),
        (0.8, 0, 0.36),
    ],
)
def test_brier_arithmetic_and_range(q, y, expected):
    score = brier(q, y)
    assert type(score) is float and 0 <= score <= 1
    assert score == pytest.approx(expected)


@pytest.mark.parametrize("q", [-0.01, 1.01, float("nan"), float("inf"), True, "0.5", None])
def test_brier_rejects_invalid_probability(q):
    with pytest.raises(ValueError):
        brier(q, 1)


@pytest.mark.parametrize("y", [-1, 2, 1.0, True, "1", None])
def test_scoring_and_baseline_reject_non_binary_integer_outcomes(y):
    baseline = PrevalenceBaseline()
    for operation in (
        lambda: brier(0.5, y),
        lambda: baseline.baseline_brier("wallet_up", y),
        lambda: baseline.record("wallet_up", y),
    ):
        with pytest.raises(ValueError):
            operation()
    assert baseline.baseline_q("wallet_up") == 0.5


def test_prevalence_uses_only_prior_outcomes_and_is_per_predicate():
    baseline = PrevalenceBaseline()
    assert baseline.baseline_q("wallet_up") == 0.5
    assert baseline.baseline_brier("wallet_up", 1) == 0.75
    assert baseline.baseline_q("wallet_up") == 0.5
    baseline.record("wallet_up", 1)
    assert baseline.baseline_q("wallet_up") == 1.0
    assert baseline.baseline_brier("wallet_up", 0) == 0.0
    assert baseline.baseline_q("wallet_up") == 1.0
    baseline.record("wallet_up", 0)
    baseline.record("wallet_up", 1)
    assert baseline.baseline_q("wallet_up") == pytest.approx(2 / 3)
    assert baseline.baseline_brier("wallet_up", 1) == pytest.approx(8 / 9)
    assert baseline.baseline_q("fill_within") == 0.5
    baseline.record("fill_within", 0)
    assert baseline.baseline_q("fill_within") == 0.0
    assert baseline.baseline_q("wallet_up") == pytest.approx(2 / 3)
    assert PrevalenceBaseline().baseline_q("wallet_up") == 0.5
