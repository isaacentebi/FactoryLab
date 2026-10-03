"""The core learner: Blum--Mansour SR_MAB with Auer EXP3 rows over doubling epochs.

Source: Blum & Mansour (2007), "From External to Internal Regret", JMLR 8,
1307--1324, §5 and Theorem 11; rows are EXP3 (Auer et al. 2002), which satisfy
Lemma 10. Each round the N rows propose q_i over the feasible menu, the master
plays the stationary p = pQ, and when action k is played with logged probability
pi_k and pays r, row i adds X_ik to its cumulative gain estimate G_ik, with

    X_ik = g_ik / q_ik = p_i * r / (kappa * pi_k)          (on-policy)

and proposes q_i proportional to exp(eta_t * G_i), mixed with gamma_k / K, at the rate
eta_t = gamma_k / N_t, N_t the menu's size at the draw (EXP3 in FTRL form).

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

A menu grows in place (§2.5): a new action gets a uniform new row and, in each old
row, a gain estimate at that row's mean weight at the pre-growth rate. The epoch's
gamma_k stays frozen at the N the epoch opened with (its exploration mass), while the
rate eta_t = gamma_k / N_t reads the menu now, so it is nonincreasing within the epoch
and eta_t * X_ik <= (gamma_k / N_t)(K_t / gamma_k) <= 1 on every update (Auer's
premise: q_ik >= gamma_k / K_t gives X_ik <= K_t / gamma_k, and K_t <= N_t). The next
epoch retunes gamma to the grown N. A singleton epoch (N = 1, gamma 0) learned
nothing, so growth from one restarts it at the grown N under the same epoch index.
Within a grown epoch each row's regret is Auer's with N_T for N (Cesa-Bianchi &
Lugosi 2006, Thm 2.3, for a time-varying rate in loss form; the gain form and the
mean-weight entry term are our own argument), so Lemma 10's constant loosens by at
most sqrt(N_T ln N_T / (N_0 ln N_0)).

A quiet draw is withdrawn exactly: withdrawing the round that rolled the epoch, while it
is the new epoch's only round, restores the closed epoch's position and rows, so its
outstanding rounds still train.

Every row keeps q_ik >= gamma_k / K, so Q is strictly positive and the stationary
solve is unique. An off-policy learner (trained on a seat's declared propensities)
uses X_ik = p_i * r / (pi_k + beta_k), beta_k = gamma_k / (2N): one update moves a
logit eta_t * G_ik by at most 2, and no guarantee is claimed.
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
        # The menu size the current epoch opened with: its gamma and row step are
        # frozen at it, whatever the menu grows to before the epoch closes.
        self.epoch_n = len(self.actions)
        self._rows = [dict.fromkeys(self.actions, 0.0) for _ in self.actions]
        # (epoch, epoch_rounds, rows, epoch_n) before the last open_round rolled the
        # epoch, kept only until anything else changes the learner: what withdrawing
        # that round restores.
        self._rolled: tuple[int, int, list[dict[str, float]], int] | None = None

    def horizon(self, epoch: int | None = None) -> int:
        """H_k = H_0 * 2^k opened rounds."""
        return self.first_epoch * 2 ** (self.epoch if epoch is None else epoch)

    def _next(self) -> tuple[int, list[dict[str, float]]]:
        """The epoch and rows the next opened round is drawn from (no mutation)."""
        if self.epoch_rounds >= self.horizon():
            return self.epoch + 1, [dict.fromkeys(self.actions, 0.0) for _ in self.actions]
        return self.epoch, self._rows

    def rate(self, epoch: int | None = None) -> float:
        """eta_t = gamma_k / N_t: ``epoch``'s gamma over the menu's size now, the rate the
        rows' gain estimates are played at; nonincreasing within an epoch."""
        return self.gamma(epoch) / len(self.actions)

    def gamma(self, epoch: int | None = None) -> float:
        """gamma_k of ``epoch`` (default the current one): the current epoch's is frozen
        at the N it opened with; a later epoch's reads the menu as it is now."""
        n = self.epoch_n if epoch is None or epoch == self.epoch else len(self.actions)
        return epoch_gamma(n, self.horizon(epoch))

    def _solve(self, support: tuple[str, ...], epoch: int,
               rows: list[dict[str, float]]) -> dict[str, float]:
        gamma, eta = self.gamma(epoch), self.rate(epoch)
        by_action = {}
        for action, gains in zip(self.actions, rows, strict=True):
            high = max(gains[a] for a in support)
            w = {a: math.exp(eta * (gains[a] - high)) for a in support}
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
        gamma = self.gamma(epoch)
        self._rolled = None
        if epoch != self.epoch:
            self._rolled = (self.epoch, self.epoch_rounds, self._rows, self.epoch_n)
            self.epoch, self.epoch_rounds, self._rows = epoch, 0, rows
            self.epoch_n = len(self.actions)
        self.epoch_rounds += 1
        return p, CoreRound(epoch, support, tuple(p.items()), gamma)

    def withdraw_round(self, saved: CoreRound) -> None:
        """Uncount a round that never became a decision (a quiet draw).

        Guarantees that withdrawing the round that rolled the epoch, while nothing else
        has changed the learner since, restores the closed epoch exactly (Sol on
        #189/#190, P1 4): its outstanding rounds are not orphaned by a draw that
        never happened.
        """
        if saved.epoch == self.epoch and self.epoch_rounds > 0:
            self.epoch_rounds -= 1
            rolled = self._rolled
            if (not self.epoch_rounds and rolled is not None
                    and rolled[0] == self.epoch - 1):
                self.epoch, self.epoch_rounds, self._rows, self.epoch_n = rolled
        self._rolled = None

    def add_actions(self, new: Sequence[str]) -> None:
        """Grow the menu in place without restarting the epoch (learners design §2.5).

        Guarantees every old row keeps its gain estimates and gains each new action at
        the row's mean weight at the pre-growth rate, each new action's row is uniform,
        and the epoch, its count and its gamma are unchanged; except that growth from a
        singleton epoch (gamma 0, nothing learned) restarts that epoch at the grown
        menu: fresh rows, no rounds counted, the same index, a positive gamma. An empty
        ``new`` changes nothing; an action already on the menu raises ValueError and
        changes nothing.
        """
        if not tuple(new):
            return
        added = _actions(new)
        if set(added) & set(self.actions):
            raise ValueError("an action already on the menu is not new")
        if self.epoch_n < 2:
            # Sol on #191: a gamma frozen at 0 learned nothing and froze snapshots no
            # restore accepts. Every draw of a singleton menu is NOOP, so nothing it
            # opened can train.
            self.actions = (*self.actions, *added)
            self.epoch_n, self.epoch_rounds = len(self.actions), 0
            self._rows = [dict.fromkeys(self.actions, 0.0) for _ in self.actions]
            self._rolled = None
            return
        eta = self.rate()  # the pre-growth rate, as the frontier's entry uses
        rows = []
        for gains in self._rows:
            high = max(gains.values())
            mean = high + math.log(math.fsum(
                math.exp(eta * (v - high)) for v in gains.values()) / len(gains)) / eta
            rows.append(_center({**gains, **dict.fromkeys(added, mean)}))
        self.actions = (*self.actions, *added)
        rows.extend(dict.fromkeys(self.actions, 0.0) for _ in added)
        self._rows = rows
        self._rolled = None

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
        if self.off_policy:
            denominator = feedback.propensity + saved.gamma / (2 * self.epoch_n)
        else:
            if p[k] > self.coverage * feedback.propensity * (1 + 1e-12):
                raise ValueError("executed propensity below the coverage bound")
            denominator = self.coverage * feedback.propensity
        updated = []
        for action, gains in zip(self.actions, self._rows, strict=True):
            # The gain estimate alone: the rate is applied at play time (``_solve``).
            x = p.get(action, 0.0) * feedback.reward / denominator
            row = dict(gains)
            row[k] += x
            updated.append(_center(row))
        self._rows = updated
        self._rolled = None

    def state(self) -> dict:
        """Parameters, epoch position, the N it opened with and every row's exact gain
        estimates (and the closed epoch a withdrawal would restore, while kept)."""
        rolled = {} if self._rolled is None else {"rolled": {
            "epoch": self._rolled[0], "epoch_rounds": self._rolled[1],
            "rows": self._rolled[2], "epoch_n": self._rolled[3]}}
        return _state(algorithm="BlumMansour", schedule=SCHEDULE, id=self.id,
                      actions=self.actions, first_epoch=self.first_epoch,
                      coverage=self.coverage, off_policy=self.off_policy,
                      epoch=self.epoch, epoch_rounds=self.epoch_rounds,
                      epoch_n=self.epoch_n, rows=self._rows, **rolled)

    @classmethod
    def restore(cls, state: dict) -> "BlumMansour":
        """Restore rows bit for bit; refuse a state from another schedule."""
        if state.get("algorithm") != "BlumMansour" or state.get("schedule") != SCHEDULE:
            raise ValueError("learner state from another algorithm or schedule")
        learner = cls(state["actions"], id=state["id"], first_epoch=state["first_epoch"],
                      coverage=state["coverage"], off_policy=state["off_policy"])
        epoch_n = state["epoch_n"]
        if not _count(epoch_n) or not 1 <= epoch_n <= len(learner.actions):
            raise ValueError("invalid saved epoch menu size")
        # H_0 is fixed for life, read at the menu it was built on: a menu grown since
        # may have raised the least horizon of its N, never the saved H_0.
        if not _count(state["first_epoch"]) or state["first_epoch"] < 1:
            raise ValueError("invalid saved first epoch")
        learner.first_epoch = state["first_epoch"]
        epoch, used = state["epoch"], state["epoch_rounds"]
        if not _count(epoch) or not _count(used):
            raise ValueError("invalid saved epoch position")
        learner.epoch, learner.epoch_rounds, learner.epoch_n = epoch, used, epoch_n
        learner._rows = _saved_rows(state["rows"], learner.actions)
        rolled = state.get("rolled")
        if rolled is not None:
            if (not _count(rolled["epoch"]) or rolled["epoch"] != epoch - 1
                    or not _count(rolled["epoch_rounds"])
                    or not _count(rolled["epoch_n"])
                    or not 1 <= rolled["epoch_n"] <= len(learner.actions)):
                raise ValueError("invalid saved rolled epoch")
            learner._rolled = (rolled["epoch"], rolled["epoch_rounds"],
                               _saved_rows(rolled["rows"], learner.actions),
                               rolled["epoch_n"])
        return learner


def _count(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _saved_rows(rows: list, actions: tuple[str, ...]) -> list[dict[str, float]]:
    if len(rows) != len(actions) or any(
        set(row) != set(actions)
        or any(type(v) not in (int, float) or not math.isfinite(v) for v in row.values())
        for row in rows
    ):
        raise ValueError("invalid saved rows")
    return [{a: float(row[a]) for a in actions} for row in rows]
