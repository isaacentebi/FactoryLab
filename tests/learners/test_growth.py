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
    gamma, eta = core.gamma(), core.rate()
    rows = core.state()["rows"]
    core.add_actions(["c"])
    state = core.state()
    assert core.actions == ("a", "b", "c")
    assert (state["epoch"], state["epoch_rounds"]) == (0, 5)  # no restart
    assert core.gamma() == gamma == epoch_gamma(2, 8)  # frozen at the N it opened with
    for old, new in zip(rows, state["rows"][:2], strict=True):
        # The old row's own gain estimates are kept; the new column is at their mean
        # weight at the pre-growth rate.
        assert {a: new[a] for a in old} == old
        assert math.exp(eta * new["c"]) == pytest.approx(
            math.fsum(math.exp(eta * v) for v in old.values()) / 2)
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
    each of ``marks`` rounds after it, and (a core's) worst row exponent eta_t * x_hat."""
    rewards = {"a": 0.7, "b": 0.2, "NOOP": 0.5, "c": 1.0}
    rng = Random(seed)
    regret, seen, worst = 0.0, {}, 0.0
    for t in range(horizon):
        if t == arrival:
            learner.add_actions(["c"])
        menu = learner.inner.actions
        p = learner.distribution_for(f"r{t}", menu, ordinal=t)
        if t >= arrival:
            regret += rewards["c"] - math.fsum(p[a] * rewards[a] for a in menu)
        k = rng.choices(menu, weights=[p[a] for a in menu], k=1)[0]
        feedback = BanditFeedback(k, rewards[k], p[k])
        if learner.core:
            saved = learner._snapshots[f"r{t}"]
            if saved.epoch == learner.inner.epoch:
                worst = max(worst, _row_exponent(learner.inner, saved, feedback))
        learner.update_for(f"r{t}", feedback)
        if t + 1 - arrival in marks:
            seen[t + 1 - arrival] = regret
    return [seen[m] for m in marks], worst


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
    (short, long), worst = _arrival_regret(learner, arrival + marks[1], arrival, marks)
    assert 0 < short and long / short <= 4 ** 0.65
    assert worst <= 1.0  # Auer's premise eta_t * x_hat <= 1 held on every core update
    horizon = arrival + marks[1]
    bound = (8.98 * n * math.sqrt(horizon * n * math.log(n)) if core
             else (n * math.log(n) + 4) * math.sqrt(horizon) + 3)
    assert long <= bound


# --- the core's row step under growth (Sol on #191; the advisor's fix) ------------------


def _row_exponent(core, saved, feedback):
    """The largest exponent eta_t * x_hat an on-policy update of ``saved`` applies: the
    rate in force now times the round's gain estimate p_i * r / (kappa * pi_k)."""
    p = dict(saved.p)
    x = max(p.get(a, 0.0) for a in core.actions) * feedback.reward / (
        core.coverage * feedback.propensity)
    return core.rate() * x


def test_core_row_step_never_exceeds_one_after_growth():
    """Sol's probe on #191: two arms, H0 = 1000, grown to ten, 999 rewards on a, then a
    reward on b. At ca655ca4 the step divided by the frozen N while exploration used the
    grown one, and round 1000's exponent was 1.614: Auer's premise eta * x_hat <= 1 broke."""
    core = BlumMansour(("a", "b"), first_epoch=1000)
    core.add_actions([f"n{i}" for i in range(8)])
    menu = core.actions
    worst = 0.0
    for t in range(1000):
        p, saved = core.open_round(menu)
        feedback = BanditFeedback("a" if t < 999 else "b", 1.0, p["a" if t < 999 else "b"])
        worst = max(worst, _row_exponent(core, saved, feedback))
        core.update_round(saved, feedback)
    assert core.epoch == 0 and worst <= 1.0


def test_core_rate_is_nonincreasing_across_growth():
    """eta_t = gamma_k / N_t: gamma_k frozen for the epoch, N_t the menu now, so the rate
    only falls when the menu grows, and the floor gamma/K and the step read the same N."""
    core = BlumMansour(("a", "b", "NOOP"), first_epoch=50)
    for _ in range(5):
        core.open_round(core.actions)
    before, gamma = core.rate(), core.gamma()
    core.add_actions(["c", "d"])
    assert core.gamma() == gamma and core.rate() == gamma / 5 <= before
    p = core.distribution(core.actions)
    assert min(p.values()) >= core.rate() * (1 - 1e-12)  # floor gamma/K = gamma/N here


def test_singleton_core_grows_into_a_positive_gamma_and_restores():
    """Sol on #191: a [NOOP] core (epoch_n 1, gamma 0) grew with gamma frozen at 0; a
    round drawn then saved gamma 0.0, and restore refused it ("invalid saved gamma")."""
    learner = SnapshotLearner(BlumMansour(("NOOP",), first_epoch=1068), id="core")
    learner.add_actions(["s1"])
    p = learner.distribution_for("h1", ("s1", "NOOP"), ordinal=1)
    assert learner.exploration("h1") > 0 and p["s1"] > 0
    restored = SnapshotLearner.restore(json.loads(json.dumps(learner.state())))
    assert restored.state() == learner.state()
    assert restored.update_for("h1", BanditFeedback("s1", 0.8, p["s1"])) is True


def test_core_growth_from_singleton_restarts_the_epoch():
    """A singleton epoch learned nothing (gamma 0, every draw NOOP): growth restarts it at
    the grown N, the same epoch index, fresh rows, no rounds counted."""
    core = BlumMansour(("NOOP",), first_epoch=20)
    for _ in range(25):
        core.open_round(core.actions)
    epoch = core.epoch
    assert core.epoch_rounds > 0
    core.add_actions(["s1", "s2"])
    assert (core.epoch, core.epoch_rounds, core.epoch_n) == (epoch, 0, 3)
    assert all(set(row.values()) == {0.0} for row in core.state()["rows"])
    assert core.gamma() == epoch_gamma(3, core.horizon()) > 0


class _PreFixCore(BlumMansour):
    """The core as it was before the fix, on a fixed menu: rows in log-weight units, the
    step gamma_k / N applied at update time."""

    def _solve(self, support, epoch, rows):
        from factorylab.learners.blum_mansour import stationary_distribution

        gamma = self.gamma(epoch)
        by_action = {}
        for action, logw in zip(self.actions, rows, strict=True):
            high = max(logw[a] for a in support)
            w = {a: math.exp(logw[a] - high) for a in support}
            total = math.fsum(w.values())
            by_action[action] = {a: (1 - gamma) * w[a] / total + gamma / len(support)
                                 for a in support}
        matrix = [[by_action[a][b] for b in support] for a in support]
        return dict(zip(support, stationary_distribution(matrix), strict=True))

    def update_round(self, saved, feedback):
        from factorylab.learners.base import _center

        p = dict(saved.p)
        n, k = len(self.actions), feedback.action
        updated = []
        for action, logw in zip(self.actions, self._rows, strict=True):
            row = dict(logw)
            row[k] += saved.gamma / n * (p.get(action, 0.0) * feedback.reward
                                         / (self.coverage * feedback.propensity))
            updated.append(_center(row))
        self._rows = updated


def test_core_fixed_menu_policy_unchanged_by_the_fix():
    """Regression guard: without growth the rate gamma_k / N is the old step, so the fixed
    menu's policy sequence is the pre-fix learner's to 1e-12 (the runtime regret test's
    Tick game: a seat and NOOP pay 1, the antagonist 0, kappa = 1/0.15)."""
    menu = ("antagonist-a", "seed-decider", "NOOP")
    rewards = {"antagonist-a": 0.0, "seed-decider": 1.0, "NOOP": 1.0}
    new = BlumMansour(menu, first_epoch=300, coverage=1 / 0.15)
    old = _PreFixCore(menu, first_epoch=300, coverage=1 / 0.15)
    rng = Random(0)
    for _ in range(700):  # past the first epoch boundary
        p_new, s_new = new.open_round(menu)
        p_old, s_old = old.open_round(menu)
        assert max(abs(p_new[a] - p_old[a]) for a in menu) <= 1e-12
        k = rng.choices(menu, weights=[p_new[a] for a in menu], k=1)[0]
        new.update_round(s_new, BanditFeedback(k, rewards[k], p_new[k]))
        old.update_round(s_old, BanditFeedback(k, rewards[k], p_old[k]))
    assert new.epoch == old.epoch == 1
