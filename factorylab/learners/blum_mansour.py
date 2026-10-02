"""The core learner: Blum--Mansour SR_MAB with Auer EXP3 rows over doubling epochs.

Source: Blum & Mansour (2007), "From External to Internal Regret", JMLR 8,
1307--1324, §5 and Theorem 11; rows are EXP3 (Auer et al. 2002), which satisfy
Lemma 10. Each round the N rows propose q_i over the feasible menu, the master
plays the stationary p = pQ, and when action k is played with logged probability
pi_k and pays r, row i adds (gamma_k / N) * X_ik to its log-weight for k, with

    X_ik = g_ik / q_ik = p_i * r / (kappa * pi_k)          (on-policy)

The row proposal q_ik cancels, so a round's snapshot is O(N): its epoch, the
master p and the executed pi (docs/architecture/learners-noregret.md §2.2).
``kappa`` is the phase-wide coverage bound of §2.3: the draw transforms guarantee
pi_k >= p_k / kappa, so g_ik <= 1 as Lemma 10 requires; an update that finds
p_k > kappa * pi_k is refused (the coverage claim failed) and changes nothing.

Schedule: epoch k lasts H_k = H_0 * 2^k opened rounds at the fixed
gamma_k = min(1, sqrt(N ln N / ((e - 1) H_k))); every row restarts at each epoch
boundary. H_0 is at least ceil(N ln N / (e - 1)) and at least the caller's
``first_epoch`` (the delivery bound in draws), so an epoch is at least as long as its
feedback takes to arrive. A round from a closed epoch is orphaned: it trains nothing.
For T >= H_0 and synchronous feedback, max_F E[swap regret_F] <= 8.98 kappa N
sqrt(T N ln N); E[max_F] and delayed feedback are not claimed.

Every row keeps q_ik >= gamma_k / K, so Q is strictly positive and the stationary
solve is unique. An off-policy learner (trained on a seat's declared propensities)
uses X_ik = p_i * r / (pi_k + beta_k), beta_k = gamma_k / (2N): one update moves a
log-weight by at most 2, and no guarantee is claimed.
"""

import math
from collections.abc import Sequence
from dataclasses import dataclass
from fractions import Fraction

from .base import (
    BanditFeedback,
    Feedback,
    _actions,
    _center,
    _probabilities,
    _state,
    _support,
)

SCHEDULE = "doubling"


class Orphaned(LookupError):
    """The round belongs to an epoch that has closed: it trains nothing."""


@dataclass(frozen=True)
class CoreRound:
    """One opened round: its epoch, support, master policy and executed policy."""

    epoch: int
    support: tuple[str, ...]
    p: tuple[tuple[str, float], ...]
    gamma: float
    executed: tuple[tuple[str, float], ...] | None = None

    def state(self) -> dict:
        return {"epoch": self.epoch, "support": list(self.support),
                "p": [list(x) for x in self.p], "gamma": self.gamma,
                "executed": None if self.executed is None
                else [list(x) for x in self.executed]}

    @classmethod
    def restore(cls, saved: dict, actions: tuple[str, ...]) -> "CoreRound":
        support = _support(saved["support"], actions)
        p = tuple((str(a), float(v)) for a, v in saved["p"])
        _probabilities(dict(p), support)
        executed = saved.get("executed")
        if executed is not None:
            executed = tuple((str(a), float(v)) for a, v in executed)
            _probabilities(dict(executed), support)
        epoch, gamma = saved["epoch"], saved["gamma"]
        if not isinstance(epoch, int) or isinstance(epoch, bool) or epoch < 0:
            raise ValueError("invalid saved epoch")
        if type(gamma) not in (int, float) or not 0 < gamma <= 1:
            raise ValueError("invalid saved gamma")
        return cls(epoch, support, p, float(gamma), executed)


def stationary_distribution(matrix: Sequence[Sequence[float]]) -> tuple[float, ...]:
    """Return nonnegative unit-mass p with max |pQ-p| <= 1e-10, without clipping.

    Exact rational Gauss--Jordan elimination solves Q^T p=p and sum(p)=1.
    Free variables are zero: a reducible chain deterministically selects one
    closed class. Periodic chains need no iteration. Cost is cubic in N with
    arbitrary-precision arithmetic, appropriate for the finite reference menus.
    """
    n = len(matrix)
    if not n or any(len(row) != n for row in matrix):
        raise ValueError("matrix must be nonempty and square")
    for row in matrix:
        _probabilities(dict(enumerate(row)), range(n))
    q = [[Fraction(v) for v in row] for row in matrix]
    q = [[v / sum(row) for v in row] for row in q]
    system = [[q[j][i] - int(i == j) for j in range(n)] + [Fraction(0)] for i in range(n)]
    system.append([Fraction(1)] * (n + 1))
    pivots = []
    for col in range(n):
        start = len(pivots)
        pivot = next((r for r in range(start, n + 1) if system[r][col]), None)
        if pivot is None:
            continue
        system[start], system[pivot] = system[pivot], system[start]
        divisor = system[start][col]
        system[start] = [v / divisor for v in system[start]]
        for r in range(n + 1):
            if r != start and system[r][col]:
                factor = system[r][col]
                system[r] = [v - factor * w for v, w in zip(system[r], system[start], strict=True)]
        pivots.append(col)
    exact = [Fraction(0)] * n
    for row, col in enumerate(pivots):
        exact[col] = system[row][-1]
    if any(v < 0 for v in exact) or sum(exact) != 1:
        raise ArithmeticError("stationary solve failed positivity or unit-mass invariant")
    result = tuple(float(v) for v in exact)
    residual = max(
        abs(math.fsum(result[i] * matrix[i][j] for i in range(n)) - result[j]) for j in range(n)
    )
    if residual > 1e-10:
        raise ArithmeticError("stationary solve exceeded residual tolerance")
    return result


def minimum_epoch(n: int) -> int:
    """The least horizon at which gamma_H <= 1: ceil(N ln N / (e - 1)), at least 1."""
    return max(1, math.ceil(n * math.log(n) / (math.e - 1)))


def epoch_gamma(n: int, horizon: int) -> float:
    """gamma_H = min(1, sqrt(N ln N / ((e - 1) H))) (Auer et al. 2002, Cor. 3.2)."""
    return min(1.0, math.sqrt(n * math.log(n) / ((math.e - 1) * horizon)))


class BlumMansour:
    """N Auer EXP3 rows supply one stationary master policy; epochs double."""

    def __init__(self, actions: Sequence[str], *, id: str = "blum_mansour",
                 first_epoch: int = 0, coverage: float = 1.0,
                 off_policy: bool = False) -> None:
        """Start epoch 0 with uniform rows; ``coverage`` is kappa >= 1, fixed for life."""
        self.actions = _actions(actions)
        self.id = id
        if (not isinstance(first_epoch, int) or isinstance(first_epoch, bool)
                or first_epoch < 0):
            raise ValueError("first_epoch must be a nonnegative integer")
        if type(coverage) not in (int, float) or not math.isfinite(coverage) or coverage < 1:
            raise ValueError("coverage must be finite and at least 1")
        self.first_epoch = max(minimum_epoch(len(self.actions)), first_epoch)
        self.coverage = float(coverage)
        self.off_policy = bool(off_policy)
        self.epoch = 0
        self.epoch_rounds = 0
        self._rows = [dict.fromkeys(self.actions, 0.0) for _ in self.actions]

    def horizon(self, epoch: int | None = None) -> int:
        """H_k = H_0 * 2^k opened rounds."""
        return self.first_epoch * 2 ** (self.epoch if epoch is None else epoch)

    def _next(self) -> tuple[int, list[dict[str, float]]]:
        """The epoch and rows the next opened round is drawn from (no mutation)."""
        if self.epoch_rounds >= self.horizon():
            return self.epoch + 1, [dict.fromkeys(self.actions, 0.0) for _ in self.actions]
        return self.epoch, self._rows

    def gamma(self, epoch: int | None = None) -> float:
        return epoch_gamma(len(self.actions), self.horizon(epoch))

    def _solve(self, support: tuple[str, ...], epoch: int,
               rows: list[dict[str, float]]) -> dict[str, float]:
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

    def distribution(self, feasible: Sequence[str]) -> dict[str, float]:
        """The next round's master policy on ``feasible``; changes nothing."""
        support = _support(feasible, self.actions)
        epoch, rows = self._next()
        return self._solve(support, epoch, rows)

    def open_round(self, feasible: Sequence[str]) -> tuple[dict[str, float], CoreRound]:
        """Count one round (rolling the epoch when it is spent) and freeze it."""
        support = _support(feasible, self.actions)
        epoch, rows = self._next()
        p = self._solve(support, epoch, rows)
        if epoch != self.epoch:
            self.epoch, self.epoch_rounds, self._rows = epoch, 0, rows
        self.epoch_rounds += 1
        return p, CoreRound(epoch, support, tuple(p.items()), self.gamma(epoch))

    def withdraw_round(self, saved: CoreRound) -> None:
        """Uncount a round that never became a decision (a quiet draw)."""
        if saved.epoch == self.epoch and self.epoch_rounds > 0:
            self.epoch_rounds -= 1

    def update_round(self, saved: CoreRound, feedback: Feedback) -> None:
        """Train the current rows on a round of the current epoch.

        Raises Orphaned (changing nothing) for a closed epoch's round, ValueError for
        feedback that does not carry the round's executed propensity or that breaks
        the coverage bound, and TypeError for full-information feedback.
        """
        if not isinstance(feedback, BanditFeedback):
            raise TypeError("SR_MAB requires BanditFeedback")
        if saved.epoch != self.epoch:
            raise Orphaned(saved.epoch)
        p = dict(saved.p)
        executed = dict(saved.executed) if saved.executed is not None else p
        k = feedback.action
        if k not in saved.support or not math.isclose(
                feedback.propensity, executed[k], rel_tol=1e-12, abs_tol=0):
            raise ValueError("feedback must carry the saved round's executed propensity")
        gamma = saved.gamma
        n = len(self.actions)
        if self.off_policy:
            denominator = feedback.propensity + gamma / (2 * n)
        else:
            if p[k] > self.coverage * feedback.propensity * (1 + 1e-12):
                raise ValueError("executed propensity below the coverage bound")
            denominator = self.coverage * feedback.propensity
        updated = []
        for action, logw in zip(self.actions, self._rows, strict=True):
            x = p.get(action, 0.0) * feedback.reward / denominator
            row = dict(logw)
            row[k] += gamma / n * x
            updated.append(_center(row))
        self._rows = updated

    def state(self) -> dict:
        """Parameters, epoch position and every row's exact log-weights."""
        return _state(algorithm="BlumMansour", schedule=SCHEDULE, id=self.id,
                      actions=self.actions, first_epoch=self.first_epoch,
                      coverage=self.coverage, off_policy=self.off_policy,
                      epoch=self.epoch, epoch_rounds=self.epoch_rounds, rows=self._rows)

    @classmethod
    def restore(cls, state: dict) -> "BlumMansour":
        """Restore rows bit for bit; refuse a state from another schedule."""
        if state.get("algorithm") != "BlumMansour" or state.get("schedule") != SCHEDULE:
            raise ValueError("learner state from another algorithm or schedule")
        learner = cls(state["actions"], id=state["id"], first_epoch=state["first_epoch"],
                      coverage=state["coverage"], off_policy=state["off_policy"])
        if learner.first_epoch != state["first_epoch"]:
            raise ValueError("invalid saved first epoch")
        epoch, used = state["epoch"], state["epoch_rounds"]
        for value in (epoch, used):
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                raise ValueError("invalid saved epoch position")
        rows = state["rows"]
        if len(rows) != len(learner.actions) or any(
            set(row) != set(learner.actions)
            or any(type(v) not in (int, float) or not math.isfinite(v) for v in row.values())
            for row in rows
        ):
            raise ValueError("invalid saved rows")
        learner.epoch, learner.epoch_rounds = epoch, used
        learner._rows = [{a: float(row[a]) for a in learner.actions} for row in rows]
        return learner
