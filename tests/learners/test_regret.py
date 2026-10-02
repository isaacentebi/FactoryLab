"""The learners are no-regret on a fixed menu, and the frontier stays mean-based.

Audit s06 #2: constant exploration gave both classes linear regret (about T/30 on
the constant-gap game, growing x16 over a 16x horizon). These read expected regret
from the learners' own policies (tests/learners/games.py), synchronous feedback.
"""

import math

import pytest

from factorylab.learners.blum_mansour import BlumMansour
from factorylab.learners.delayed import SnapshotLearner
from factorylab.learners.exp3 import EXP3
from tests.learners.games import ACTIONS, constant_gap, investment_trap, play

N = len(ACTIONS)


def frontier():
    return SnapshotLearner(EXP3(ACTIONS), id="frontier")


def core():
    return SnapshotLearner(BlumMansour(ACTIONS), id="core")


@pytest.mark.gate
def test_frontier_regret_sqrt_growth():
    """Proposition 1: E[R_T] <= (N ln N + 4) sqrt(T) + 3; growth over 16x is sqrt-like."""
    small, _ = play(frontier(), constant_gap, 2_000)
    large, _ = play(frontier(), constant_gap, 32_000)
    for regret, horizon in ((small, 2_000), (large, 32_000)):
        assert regret <= (N * math.log(N) + 4) * math.sqrt(horizon) + 3
    assert large / small <= 16 ** 0.6


@pytest.mark.gate
def test_core_epochs_swap_regret_within_theorem_11_bound():
    """Proposition 2 for T >= H_0: max_F E[swap] <= 8.98 kappa N sqrt(T N ln N)."""
    regrets = {}
    for horizon in (1_000, 16_000):
        learner = core()
        assert horizon >= learner.inner.first_epoch
        _, swap = play(learner, constant_gap, horizon)
        assert swap <= 8.98 * N * math.sqrt(horizon * N * math.log(N))
        regrets[horizon] = swap
    assert regrets[16_000] / regrets[1_000] <= 16 ** 0.65


@pytest.mark.gate
def test_frontier_is_mean_based_core_is_not_trapped():
    """Deng-Schneider-Sivan's trap: a mean-based learner keeps about 3T/16 swap regret;
    the no-swap-regret core escapes it."""
    horizon = 16_000
    _, mean_based = play(frontier(), investment_trap, horizon)
    _, swap_core = play(core(), investment_trap, horizon)
    assert mean_based / horizon >= 0.12
    assert swap_core / horizon <= 0.02


@pytest.mark.gate
def test_frontier_stays_sublinear_with_delayed_feedback():
    """Not a theorem (research item R2): a measured check that a 120-round delay keeps
    regret within Proposition 1's synchronous bound on this game."""
    for horizon in (2_000, 16_000):
        regret, _ = play(frontier(), constant_gap, horizon, delay=120)
        assert regret <= (N * math.log(N) + 4) * math.sqrt(horizon) + 3
