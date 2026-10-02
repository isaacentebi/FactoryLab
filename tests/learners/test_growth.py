"""A menu grows in place: a new arm joins the live learner (learners design §2.5).

Mourtada & Maillard (2017), "Efficient tracking of a growing number of experts": an
expert that arrives at the mean weight raises the potential by at most ln(1 + 1/N), so
regret against each arm counts from its arrival. The frontier's new arm enters at the
mean weight of the loss form at the current rate; the core's at a uniform new row and a
mean-weight new column in each old row, its epoch's gamma frozen at the N the epoch
opened with. Nothing in flight is orphaned by growth.
"""

import json
import math
from collections import deque
from random import Random

import pytest

from factorylab.learners.base import BanditFeedback, restore_learner
from factorylab.learners.blum_mansour import BlumMansour, epoch_gamma
from factorylab.learners.delayed import SnapshotLearner
from factorylab.learners.exp3 import EXP3


def _trained_exp3():
    learner = EXP3(("a", "b", "NOOP"))
    for t, (arm, reward, p) in enumerate([("a", 0.9, 0.5), ("b", 0.1, 0.3),
                                         ("NOOP", 0.5, 0.2), ("a", 0.8, 0.4)]):
        learner.open_round()
        learner.update(BanditFeedback(arm, reward, p))
        assert learner.rounds == t + 1
    return learner


def test_frontier_grows_at_the_mean_weight_and_its_rate_never_rises():
    learner = _trained_exp3()
    before = learner.state()
    _gamma, eta = learner.rates()
    weights = [math.exp(-eta * v) for v in before["losses"].values()]
    learner.add_actions(["c"])
    after = learner.state()
    assert learner.actions == ("a", "b", "NOOP", "c")
    assert after["rounds"] == before["rounds"]  # no restart
    assert {a: after["losses"][a] for a in before["losses"]} == before["losses"]
    assert math.exp(-eta * after["losses"]["c"]) == pytest.approx(math.fsum(weights) / 3)
    assert learner.rates()[1] <= eta  # eta_t = gamma_t / N stays nonincreasing
    p = learner.distribution(learner.actions)
    assert p["c"] > 0 and math.isclose(math.fsum(p.values()), 1.0)
    learner.add_actions([])  # nothing new: nothing changes
    assert learner.state() == after
    with pytest.raises(ValueError):
        learner.add_actions(["a"])  # an arm already on the menu is not new


def test_core_grows_a_uniform_row_and_a_mean_weight_column_with_its_gamma_frozen():
    core = BlumMansour(("a", "b"), first_epoch=8)
    for t in range(5):
        p, saved = core.open_round(core.actions)
        core.update_round(saved, BanditFeedback("a" if t % 2 else "b", 0.7, p["a" if t % 2
                                                                            else "b"]))
    gamma = core.gamma()
    rows = core.state()["rows"]
    core.add_actions(["c"])
    state = core.state()
    assert core.actions == ("a", "b", "c")
    assert (state["epoch"], state["epoch_rounds"]) == (0, 5)  # no restart
    assert core.gamma() == gamma == epoch_gamma(2, 8)  # frozen at the N it opened with
    for old, new in zip(rows, state["rows"][:2], strict=True):
        # The old row's own log-weights are kept; the new column is their mean weight.
        assert {a: new[a] for a in old} == old
        assert new["c"] == pytest.approx(
            math.log(math.fsum(math.exp(v) for v in old.values()) / 2))
    assert len(set(state["rows"][2].values())) == 1  # the new row is uniform
    assert core.gamma(1) == epoch_gamma(3, 16)  # the next epoch reads the grown menu
    for _ in range(3):
        core.open_round(core.actions)
    p, _saved = core.open_round(core.actions)  # rolls into epoch 1
    assert core.epoch == 1 and core.gamma() == epoch_gamma(3, 16)
    assert set(p) == {"a", "b", "c"}


@pytest.mark.parametrize("core", [False, True])
def test_outstanding_snapshots_across_growth_train_and_are_not_orphaned(core):
    inner = BlumMansour(("a", "NOOP"), first_epoch=50) if core else EXP3(("a", "NOOP"))
    learner = SnapshotLearner(inner, id="L")
    p = learner.distribution_for("before", ("a", "NOOP"), ordinal=1)
    learner.add_actions(["c"])
    q = learner.distribution_for("after", ("a", "c", "NOOP"), ordinal=2)
    assert q["c"] > 0
    restored = restore_learner(json.loads(json.dumps(learner.state())))
    assert restored.update_for("before", BanditFeedback("a", 0.9, p["a"])) is True
    assert restored.update_for("after", BanditFeedback("c", 0.4, q["c"])) is True
    assert restored.outstanding() == []


@pytest.mark.parametrize("core", [False, True])
def test_checkpoint_and_resume_across_growth_continue_bit_for_bit(core):
    def make():
        return SnapshotLearner(BlumMansour(("z", "a", "NOOP"), first_epoch=6) if core
                               else EXP3(("z", "a", "NOOP")), id="L")

    def run(learner, restore_at):
        rng, pending, log = Random(5), deque(), []
        for t in range(60):
            if t == 9:
                learner.add_actions(["n1"])
            if t == 23:
                learner.add_actions(["n2", "n3"])
            if t in restore_at:
                learner = restore_learner(json.loads(json.dumps(learner.state())))
            while pending and pending[0][0] <= t:
                _, handle, feedback = pending.popleft()
                log.append((handle, learner.update_for(handle, feedback)))
            actions = learner.inner.actions
            p = learner.distribution_for(f"h{t}", actions, ordinal=t)
            log.append(tuple(sorted(p.items())))
            k = rng.choices(actions, weights=[p[a] for a in actions], k=1)[0]
            pending.append((t + 7, f"h{t}", BanditFeedback(k, rng.random(), p[k])))
        return learner, log

    straight, straight_log = run(make(), ())
    resumed, resumed_log = run(make(), (10, 24, 30))
    assert resumed_log == straight_log
    assert resumed.state() == straight.state()
    trained = [ok for x in straight_log if isinstance(x[1], bool) for ok in (x[1],)]
    assert trained and (all(trained) or core)  # growth orphans nothing


def _arrival_regret(learner, horizon, arrival, marks, *, seed=0):
    """Expected regret against the arriving arm ``c`` from its arrival, synchronous, at
    each of ``marks`` rounds after it."""
    rewards = {"a": 0.7, "b": 0.2, "NOOP": 0.5, "c": 1.0}
    rng = Random(seed)
    regret, seen = 0.0, {}
    for t in range(horizon):
        if t == arrival:
            learner.add_actions(["c"])
        menu = learner.inner.actions
        p = learner.distribution_for(f"r{t}", menu, ordinal=t)
        if t >= arrival:
            regret += rewards["c"] - math.fsum(p[a] * rewards[a] for a in menu)
        k = rng.choices(menu, weights=[p[a] for a in menu], k=1)[0]
        learner.update_for(f"r{t}", BanditFeedback(k, rewards[k], p[k]))
        if t + 1 - arrival in marks:
            seen[t + 1 - arrival] = regret
    return [seen[m] for m in marks]


@pytest.mark.gate
@pytest.mark.parametrize("core", [False, True])
def test_regret_with_an_arm_added_mid_run_is_sublinear_from_its_arrival(core):
    """An arm founded at round 4k: regret against it, counted from its arrival, grows
    like the square root of the rounds since (x4 rounds, well under x4 regret), and
    stays inside the class's fixed-menu bound at the grown N (Proposition 1 for the
    frontier, Proposition 2's 8.98 kappa N sqrt(T N ln N) for the core, kappa = 1).
    The core's doubling epochs need the longer window to reach their sqrt regime."""
    arrival, n = 4_000, 4
    marks = (16_000, 64_000) if core else (8_000, 32_000)
    learner = SnapshotLearner(BlumMansour(("a", "b", "NOOP")) if core
                              else EXP3(("a", "b", "NOOP")), id="L")
    short, long = _arrival_regret(learner, arrival + marks[1], arrival, marks)
    assert 0 < short and long / short <= 4 ** 0.65
    horizon = arrival + marks[1]
    bound = (8.98 * n * math.sqrt(horizon * n * math.log(n)) if core
             else (n * math.log(n) + 4) * math.sqrt(horizon) + 3)
    assert long <= bound
