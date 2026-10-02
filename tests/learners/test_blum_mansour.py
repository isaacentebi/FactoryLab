import math

import pytest

from factorylab.learners.base import BanditFeedback, FullInfoFeedback
from factorylab.learners.blum_mansour import (
    BlumMansour,
    Orphaned,
    epoch_gamma,
    minimum_epoch,
    stationary_distribution,
)


@pytest.mark.parametrize("matrix", [
    ((1.0,),),
    ((0.9, 0.1), (0.4, 0.6)),
    ((0.0, 1.0, 0.0), (0.0, 0.0, 1.0), (1.0, 0.0, 0.0)),
    ((1.0, 0.0), (0.0, 1.0)),
    ((0.0, 0.5, 0.5), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0)),
    ((1.0, 1e-100), (1e-200, 1.0)),
])
def test_stationarity_including_periodic_reducible_and_slow_mixing(matrix):
    p = stationary_distribution(matrix)
    assert all(v >= 0 for v in p)
    assert math.fsum(p) == pytest.approx(1, abs=1e-12)
    for j in range(len(p)):
        assert math.fsum(p[i] * matrix[i][j] for i in range(len(p))) == pytest.approx(
            p[j], abs=1e-10
        )


def test_known_stationary_solution_without_clipping():
    assert stationary_distribution(((0.9, 0.1), (0.4, 0.6))) == pytest.approx((0.8, 0.2))
    p = stationary_distribution(((1.0, 1e-100), (1e-200, 1.0)))
    assert p[0] == pytest.approx(1e-100, rel=1e-12, abs=0)


@pytest.mark.parametrize("matrix", [
    (), ((1, 0),), ((-0.1, 1.1), (0.5, 0.5)),
    ((0.2, 0.2), (0.5, 0.5)), ((math.nan, 0), (0, 1)),
])
def test_invalid_matrices_fail(matrix):
    with pytest.raises(ValueError):
        stationary_distribution(matrix)


def test_epochs_double_and_gamma_is_horizon_tuned():
    core = BlumMansour(("a", "b", "NOOP"), first_epoch=5)
    h0 = max(minimum_epoch(3), 5)
    assert core.first_epoch == h0
    seen = []
    for _ in range(h0 * 7):
        _, saved = core.open_round(core.actions)
        seen.append(saved.epoch)
        assert saved.gamma == epoch_gamma(3, h0 * 2 ** saved.epoch)
    assert seen == [0] * h0 + [1] * 2 * h0 + [2] * 4 * h0
    assert epoch_gamma(3, h0) > epoch_gamma(3, 2 * h0)
    # The first epoch never falls below the least horizon with gamma <= 1.
    assert BlumMansour(("a", "b")).first_epoch == minimum_epoch(2)


def test_row_update_is_lemma_10_with_the_row_proposal_cancelled():
    core = BlumMansour(("a", "b", "c"), coverage=2.0)
    p, saved = core.open_round(core.actions)
    executed = {"a": 0.5, "b": 0.25, "c": 0.25}
    saved = type(saved)(saved.epoch, saved.support, saved.p, saved.gamma,
                        tuple(executed.items()))
    core.update_round(saved, BanditFeedback("b", 0.8, 0.25))
    gamma = saved.gamma
    for i, row in zip(core.actions, core.state()["rows"], strict=True):
        x = p[i] * 0.8 / (2.0 * 0.25)
        # Rows hold gain estimates; the logit is eta * G, eta = gamma / N.
        assert row["b"] - row["a"] == pytest.approx(x)
        assert core.rate() * (row["b"] - row["a"]) == pytest.approx(gamma / 3 * x)
        assert row["a"] == row["c"]


def test_coverage_bound_is_enforced_and_failure_changes_nothing():
    core = BlumMansour(("a", "b"), coverage=1.5)
    p, saved = core.open_round(core.actions)
    starved = type(saved)(saved.epoch, saved.support, saved.p, saved.gamma,
                          (("a", 0.9), ("b", 0.1)))
    before = core.state()
    with pytest.raises(ValueError, match="coverage"):
        core.update_round(starved, BanditFeedback("b", 1.0, 0.1))
    with pytest.raises(ValueError, match="executed propensity"):
        core.update_round(saved, BanditFeedback("b", 1.0, 0.3))
    with pytest.raises(TypeError):
        core.update_round(saved, FullInfoFeedback({"a": 0, "b": 1}))
    assert core.state() == before


def test_off_policy_rare_propensity_step_bounded():
    """The review's P0 probe: one declared 1e-6 must not lock the master at (1, 0)."""
    core = BlumMansour(("a", "b"), off_policy=True)
    p, saved = core.open_round(core.actions)
    declared = type(saved)(saved.epoch, saved.support, saved.p, saved.gamma,
                           (("a", 1 - 1e-6), ("b", 1e-6)))
    core.update_round(declared, BanditFeedback("b", 1.0, 1e-6))
    for row in core.state()["rows"]:
        assert core.rate() * (row["b"] - row["a"]) <= 2 + 1e-12  # the logit's move
    master = core.distribution(core.actions)
    assert 0 < master["a"] < 1 and 0 < master["b"] < 1


def test_rows_stay_strictly_positive_so_the_master_is_interior():
    core = BlumMansour(("a", "b", "c"))
    for t in range(200):
        p, saved = core.open_round(core.actions)
        assert all(v > 0 for v in p.values())
        k = "a" if t % 2 else "c"
        core.update_round(saved, BanditFeedback(k, 1.0, p[k]))
    assert all(v >= core.gamma() / 3 * 0.999 for v in core.distribution(core.actions).values())


def test_a_closed_epochs_round_is_orphaned_and_trains_nothing():
    core = BlumMansour(("a", "b"))
    p, old = core.open_round(core.actions)
    for _ in range(core.first_epoch):
        core.open_round(core.actions)
    assert core.epoch == 1
    before = core.state()
    with pytest.raises(Orphaned):
        core.update_round(old, BanditFeedback("a", 1.0, p["a"]))
    assert core.state() == before


def test_feasible_submenu_solves_on_its_own_rows_and_columns():
    core = BlumMansour(("a", "b", "NOOP"))
    p = core.distribution(("a", "NOOP"))
    assert set(p) == {"a", "NOOP"} and p == pytest.approx({"a": 0.5, "NOOP": 0.5})
    assert core.state()["epoch_rounds"] == 0  # a query opens nothing


def test_restore_is_exact_and_refuses_another_schedule():
    core = BlumMansour(("z", "a"), first_epoch=4, coverage=3.0, id="core")
    for t in range(9):
        p, saved = core.open_round(core.actions)
        core.update_round(saved, BanditFeedback("z", t / 10, p["z"]))
    restored = BlumMansour.restore(core.state())
    assert restored.state() == core.state()
    assert restored.distribution(core.actions) == core.distribution(core.actions)
    with pytest.raises(ValueError):
        BlumMansour.restore({"algorithm": "BlumMansour", "bases": []})
