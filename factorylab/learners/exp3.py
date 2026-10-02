"""The frontier learner: anytime, mean-based EXP3 in follow-the-regularised-leader form.

Chapter II §I.a asks for "at least some naive, mean-based no-regret learning
somewhere": a chaser of running averages. This learner keeps one cumulative loss
estimate per action and, at its t-th opened round, plays

    gamma_t = min(1, t^(-1/2)),  eta_t = gamma_t / N,
    q_t = (1 - gamma_t) * softmax(-eta_t * L) + gamma_t / K   (K = feasible menu)

with no restart (docs/architecture/learners-noregret.md §2.1). On a fixed menu with
synchronous on-policy feedback its expected regret is at most
(N ln N + 4) sqrt(T) + 3, and the decaying exploration keeps it mean-based
(Braverman, Mao, Schneider & Weinberg 2018, Thm D.3, adapted to an anytime schedule).

The estimate is the truthful logged propensity's: a round that played action k with
probability pi_k and lost l adds l / pi_k to L[k] (on-policy). An off-policy learner
(one trained on another agent's declared propensities) adds l / (pi_k + beta) with
beta = eta/2 of the round it opened (implicit exploration, Neu 2015): one update moves
a logit by at most 2, and its bias is optimistic for actions the behaviour rarely
takes. No regret is claimed for an off-policy learner (§2.4).
"""

import math
from collections.abc import Sequence

from .base import (
    BanditFeedback,
    Feedback,
    _actions,
    _state,
    _support,
)

SCHEDULE = "t^-1/2"


def exploration(t: int) -> float:
    """``gamma_t = min(1, t^(-1/2))`` for round ``t >= 1``, from correctly rounded sqrt."""
    if not isinstance(t, int) or isinstance(t, bool) or t < 1:
        raise ValueError("a round index is a positive integer")
    return min(1.0, 1.0 / math.sqrt(t))


class EXP3:
    """The distribution is a pure function of (losses, rounds); opening a round counts it."""

    def __init__(self, actions: Sequence[str], *, id: str = "exp3",
                 off_policy: bool = False) -> None:
        """Start with zero losses and no rounds opened."""
        self.actions = _actions(actions)
        self.id = id
        self.off_policy = bool(off_policy)
        self._losses = dict.fromkeys(self.actions, 0.0)
        self._rounds = 0

    @property
    def rounds(self) -> int:
        """Rounds opened and not withdrawn."""
        return self._rounds

    def rates(self) -> tuple[float, float]:
        """(gamma, eta) of the next round to be opened."""
        gamma = exploration(self._rounds + 1)
        return gamma, gamma / len(self.actions)

    def distribution(self, feasible: Sequence[str]) -> dict[str, float]:
        """The next round's policy on ``feasible``; changes nothing."""
        support = _support(feasible, self.actions)
        gamma, eta = self.rates()
        low = min(self._losses[a] for a in support)
        weights = {a: math.exp(-eta * (self._losses[a] - low)) for a in support}
        total = math.fsum(weights.values())
        return {a: (1 - gamma) * weights[a] / total + gamma / len(support) for a in support}

    def open_round(self) -> dict[str, float]:
        """Count one round; return the rates it was drawn at (frozen in its snapshot)."""
        gamma, eta = self.rates()
        self._rounds += 1
        return {"gamma": gamma, "eta": eta}

    def withdraw_round(self) -> None:
        """Uncount one opened round that never became a decision (a quiet draw)."""
        if self._rounds < 1:
            raise RuntimeError("no round to withdraw")
        self._rounds -= 1

    def estimate(self, feedback: BanditFeedback, eta: float | None = None) -> float:
        """The loss estimate this round adds to its action; raises if it is not finite."""
        if not isinstance(feedback, BanditFeedback):
            raise TypeError("EXP3 requires BanditFeedback")
        if feedback.action not in self._losses:
            raise ValueError("unknown action")
        beta = 0.0
        if self.off_policy:
            if eta is None or not math.isfinite(eta) or eta <= 0:
                raise ValueError("an off-policy update needs its round's eta")
            beta = eta / 2
        value = (1.0 - feedback.reward) / (feedback.propensity + beta)
        if not math.isfinite(value) or not math.isfinite(self._losses[feedback.action] + value):
            raise ValueError("loss estimate is not finite")
        return value

    def update(self, feedback: Feedback, *, eta: float | None = None) -> None:
        """Add the round's loss estimate to its action; change nothing on failure."""
        value = self.estimate(feedback, eta)
        self._losses[feedback.action] += value

    def state(self) -> dict:
        """Return the exact losses, round count, identity and estimator mode."""
        return _state(algorithm="EXP3", schedule=SCHEDULE, id=self.id, actions=self.actions,
                      off_policy=self.off_policy, rounds=self._rounds, losses=self._losses)

    @classmethod
    def restore(cls, state: dict) -> "EXP3":
        """Preserve losses bit for bit; refuse a state from another schedule."""
        if state.get("algorithm") != "EXP3" or state.get("schedule") != SCHEDULE:
            raise ValueError("learner state from another algorithm or schedule")
        learner = cls(state["actions"], id=state["id"], off_policy=state["off_policy"])
        losses, rounds = state["losses"], state["rounds"]
        if set(losses) != set(learner.actions) or any(
            type(v) not in (int, float) or not math.isfinite(v) or v < 0
            for v in losses.values()
        ):
            raise ValueError("invalid saved losses")
        if not isinstance(rounds, int) or isinstance(rounds, bool) or rounds < 0:
            raise ValueError("invalid saved round count")
        learner._losses = {a: float(losses[a]) for a in learner.actions}
        learner._rounds = rounds
        return learner
