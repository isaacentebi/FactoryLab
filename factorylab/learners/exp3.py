"""EXP3 uses q[a]=(1-gamma)w[a]/sum(w)+gamma/K and eta=gamma/N.

N is the fixed universe size; K is the feasible menu size. On a fixed menu this
is the standard EXP3 algorithm of Auer et al. (2002), Figure 1:
https://cseweb.ucsd.edu/~yfreund/papers/bandits.pdf
The executed action's estimator is reward / logged propensity. Menu changes
retain weights but carry no fixed-menu regret guarantee. Gamma is fixed for an
epoch; the caller chooses it for the horizon (or starts a new epoch).
"""

import math
from collections.abc import Sequence

from .base import BanditFeedback, Feedback, _actions, _center, _state, _support, _weights


class EXP3:
    """Exploration stays positive and bandit estimates use supplied propensities."""

    def __init__(self, actions: Sequence[str], gamma: float, *, id: str = "exp3") -> None:
        """Start uniform weights with exploration gamma in (0, 1]."""
        if not math.isfinite(gamma) or not 0 < gamma <= 1:
            raise ValueError("gamma must be in (0, 1]")
        self.actions = _actions(actions)
        self.id = id
        self.gamma = gamma
        self._log_weights = dict.fromkeys(self.actions, 0.0)

    def distribution(self, feasible: Sequence[str]) -> dict[str, float]:
        """Mix conditioned weights with uniform exploration on the feasible menu."""
        support = _support(feasible, self.actions)
        weights = _weights(self._log_weights, support)
        return {a: (1 - self.gamma) * weights[a] + self.gamma / len(support) for a in support}

    def update(self, feedback: Feedback) -> None:
        """Increase only the observed action's log weight by gamma/N * reward/propensity."""
        if not isinstance(feedback, BanditFeedback):
            raise TypeError("EXP3 requires BanditFeedback")
        if feedback.action not in self._log_weights:
            raise ValueError("unknown action")
        weights = self._log_weights.copy()
        weights[feedback.action] += (
            self.gamma / len(self.actions) * feedback.reward / feedback.propensity
        )
        self._log_weights = _center(weights)

    def expand(self, actions: Sequence[str]) -> "EXP3":
        """Return a new learner over ``actions`` (a superset) for a new comparator epoch.

        Existing actions carry their log-weights; new actions start at the carried
        mean. No regret guarantee spans the epoch boundary.
        """
        new = _actions(actions)
        if not set(self.actions) <= set(new):
            raise ValueError("an epoch may only add actions")
        carried = [self._log_weights[a] for a in self.actions]
        mean = sum(carried) / len(carried)
        learner = EXP3(new, self.gamma, id=self.id)
        learner._log_weights = _center({a: self._log_weights.get(a, mean) for a in new})
        return learner

    def state(self) -> bytes:
        """Return deterministic weights, parameters, and identity."""
        return _state(
            algorithm="EXP3",
            id=self.id,
            actions=self.actions,
            gamma=self.gamma,
            log_weights=self._log_weights,
        )

    def update_observed_gain(self, action: str, gain: float, proposal_probability: float) -> None:
        """Apply the SR_MAB observed gain using its row proposal denominator (Lemma 10).

        This is the paper's off-proposal feedback interface, not an assertion
        that the master action was drawn from this row's distribution.
        """
        self.update(BanditFeedback(action, gain, proposal_probability))
