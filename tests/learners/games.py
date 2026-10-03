"""Bandit reference games: expected regret computed from the learners' own policies.

Each game gives a reward vector per round; the learner sees only the drawn action's
reward and its truthful propensity. Regret is read from the policy, not the draw:
external regret is max_a sum_t r_t,a - sum_t p_t . r_t, and swap regret is
sum_i max_j sum_t p_t,i (r_t,j - r_t,i) (max_F of the expectation, the published
Blum-Mansour form). Delivery goes through SnapshotLearner, delayed by ``delay``
rounds, exactly as the runtime delivers it.
"""

import math
from collections import deque
from collections.abc import Callable, Sequence
from random import Random

from factorylab.learners.base import BanditFeedback
from factorylab.learners.delayed import SnapshotLearner

ACTIONS = ("a", "b", "NOOP")


def constant_gap(_t: int, _horizon: int) -> dict[str, float]:
    """The auditor's game: a and NOOP pay 1, b pays 0, forever."""
    return {"a": 1.0, "b": 0.0, "NOOP": 1.0}


def investment_trap(t: int, horizon: int) -> dict[str, float]:
    """Deng, Schneider & Sivan (2019), Table 1, as rewards: Top for half, then Bottom.

    A mean-based learner keeps playing R after the switch while M recovers its
    deficit; replacing those R rounds by M gains about 3T/16 (swap regret)."""
    losses = ({"a": 3 / 8, "b": 1.0, "NOOP": 0.5} if t < horizon // 2
              else {"a": 1.0, "b": 0.0, "NOOP": 0.5})
    return {k: 1 - v for k, v in losses.items()}


def play(learner: SnapshotLearner, game: Callable[[int, int], dict[str, float]],
         horizon: int, *, seed: int = 0, delay: int = 0,
         menu: Sequence[str] = ACTIONS) -> tuple[float, float]:
    """Run ``horizon`` rounds; return (expected external regret, expected swap regret)."""
    rng = Random(seed)
    pending: deque = deque()
    totals = dict.fromkeys(menu, 0.0)
    own = 0.0
    swap = {i: dict.fromkeys(menu, 0.0) for i in menu}
    for t in range(horizon):
        while pending and pending[0][0] <= t:
            _, handle, feedback = pending.popleft()
            learner.update_for(handle, feedback)
        rewards = game(t, horizon)
        handle = f"r{t}"
        p = learner.distribution_for(handle, menu, ordinal=t)
        own += math.fsum(p[a] * rewards[a] for a in menu)
        for i in menu:
            totals[i] += rewards[i]
            for j in menu:
                swap[i][j] += p[i] * rewards[j]
        k = rng.choices(menu, weights=[p[a] for a in menu], k=1)[0]
        feedback = BanditFeedback(k, rewards[k], p[k])
        if delay:
            pending.append((t + delay, handle, feedback))
        else:
            learner.update_for(handle, feedback)
    external = max(totals.values()) - own
    swap_regret = math.fsum(max(swap[i][j] - swap[i][i] for j in menu) for i in menu)
    return external, swap_regret
