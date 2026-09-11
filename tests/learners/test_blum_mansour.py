import json
import math

import pytest

from factorylab.learners.base import BanditFeedback, FullInfoFeedback
from factorylab.learners.blum_mansour import BlumMansour, stationary_distribution
from factorylab.learners.exp3 import EXP3
from factorylab.learners.hedge import Hedge


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


class RecordingHedge(Hedge):
    def update(self, feedback):
        self.received = feedback
        super().update(feedback)


def test_full_information_scales_each_row_and_preserves_round_state():
    bases = []

    def factory(actions):
        base = RecordingHedge(actions, 0.4)
        base.update(FullInfoFeedback(dict(zip(actions, (0, len(bases) / 2, 1), strict=True))))
        bases.append(base)
        return base

    learner = BlumMansour(factory, ("a", "b", "c"))
    p = learner.distribution(("a", "b"))
    before = learner.state()
    assert learner.distribution(("a", "b")) == p
    assert learner.state() == before
    p["a"] = -1  # Returned dictionaries cannot corrupt the saved round.
    p = learner.distribution(("a", "b"))
    with pytest.raises(RuntimeError):
        learner.distribution(("a", "b", "c"))
    losses = {"a": 0.2, "b": 0.8, "c": 1.0}
    learner.update(FullInfoFeedback(losses))
    for action, base in zip(learner.actions, bases, strict=True):
        assert base.received.losses == pytest.approx(
            {a: p.get(action, 0) * loss for a, loss in losses.items()}
        )
    assert bases[2].received.losses == {"a": 0, "b": 0, "c": 0}
    assert learner.state() != before
    with pytest.raises(RuntimeError):
        learner.update(FullInfoFeedback(losses))
    assert set(learner.distribution(("a", "b", "c"))) == {"a", "b", "c"}


class RecordingEXP3(EXP3):
    def update_observed_gain(self, action, gain, proposal_probability):
        self.received = (action, gain, proposal_probability)
        super().update_observed_gain(action, gain, proposal_probability)


def bandit_reduction():
    bases = []

    def factory(actions):
        base = RecordingEXP3(actions, 0.3)
        base.update(BanditFeedback(actions[len(bases)], 1, 0.05 * (len(bases) + 1)))
        bases.append(base)
        return base

    return BlumMansour(factory, ("a", "b", "c")), bases


@pytest.mark.parametrize("chosen", ["a", "b", "c"])
def test_sr_mab_gain_split_estimator_and_exploration_match_paper(chosen):
    learner, bases = bandit_reduction()
    actions = learner.actions
    rows = [base.distribution(actions) for base in bases]
    logs = [json.loads(base.state())["log_weights"] for base in bases]
    p = learner.distribution(actions)
    # Distinct nonuniform rows expose a missing q factor or a second division by p.
    assert rows[0] != rows[1] and p["a"] != pytest.approx(1 / 3)
    reward = 0.8
    learner.update(BanditFeedback(chosen, reward, p[chosen]))
    assert sum(base.received[1] for base in bases) == pytest.approx(reward)
    for i, base in enumerate(bases):
        _, gain, denominator = base.received
        assert all(prob >= base.gamma / len(actions) for prob in rows[i].values())
        assert gain == pytest.approx(p[actions[i]] * reward * rows[i][chosen] / p[chosen])
        assert denominator == rows[i][chosen]
        estimate = gain / denominator
        assert p[chosen] * estimate == pytest.approx(p[actions[i]] * reward)
        after = json.loads(base.state())["log_weights"]
        other = next(a for a in actions if a != chosen)
        delta = after[chosen] - after[other] - (logs[i][chosen] - logs[i][other])
        assert delta == pytest.approx(base.gamma / len(actions) * estimate)


def test_master_denominator_is_the_logged_float_not_a_recomputed_value():
    learner, bases = bandit_reduction()
    rows = [base.distribution(learner.actions) for base in bases]
    p = learner.distribution(learner.actions)
    logged = p["a"] * (1 + 5e-13)
    learner.update(BanditFeedback("a", 0.8, logged))
    assert bases[0].received[1] == p["a"] * 0.8 * rows[0]["a"] / logged


def test_bandit_feasibility_solves_on_the_feasible_submatrix():
    learner, bases = bandit_reduction()
    p = learner.distribution(("c", "a"))
    assert tuple(p) == ("c", "a")
    rows = [base.distribution(("c", "a")) for base in bases]
    assert p["a"] == pytest.approx(p["a"] * rows[0]["a"] + p["c"] * rows[2]["a"])
    learner.update(BanditFeedback("a", 1, p["a"]))
    assert bases[1].received[1] == 0


def test_feedback_mode_and_wrong_propensity_are_rejected_before_updates():
    full = BlumMansour(lambda a: Hedge(a, 0.1), ("a", "b"))
    full.distribution(full.actions)
    before = full.state()
    with pytest.raises(TypeError):
        full.update(BanditFeedback("a", 1, 0.5))
    assert full.state() == before
    bandit, _ = bandit_reduction()
    bandit.distribution(bandit.actions)
    before = bandit.state()
    with pytest.raises(TypeError):
        bandit.update(FullInfoFeedback({a: 0 for a in bandit.actions}))
    with pytest.raises(ValueError):
        bandit.update(BanditFeedback("a", 1, 0.01))
    with pytest.raises(ValueError):
        bandit.update(BanditFeedback("unavailable", 1, 0.5))
    assert bandit.state() == before


def test_bases_must_be_independent_and_state_is_reproducible():
    base = Hedge(("a", "b"), 0.1)
    with pytest.raises(ValueError):
        BlumMansour(lambda _: base, base.actions)
    first, _ = bandit_reduction()
    second, _ = bandit_reduction()
    assert first.state() == second.state()
    for learner in (first, second):
        p = learner.distribution(learner.actions)
        learner.update(BanditFeedback("a", 0.4, p["a"]))
    assert first.state() == second.state()
