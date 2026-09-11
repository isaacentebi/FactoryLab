"""Blum--Mansour full-information reduction and partial-information SR_MAB.

Source: Blum & Mansour (2007), "From External to Internal Regret", JMLR 8,
1307--1324, https://www.jmlr.org/papers/volume8/blum07a/blum07a.pdf.
Section 3 solves p=pQ and supplies loss p_i*loss[j] to row i (equation (1)).
Section 5's SR_MAB uses the unnumbered equations on pp. 1316--1317:
  q_ij = (1-gamma_i)*w_ij/sum_j(w_ij) + gamma_i/N,
  p = pQ,
  g_i,k = p_i * reward[k] * q_i,k / p_k,
  X_i,k = g_i,k / q_i,k, with X_i,j=0 for j != k (Lemma 10).
Thus E[X_i,j] = p_i*reward[j]. EXP3 updates w_i,k by
exp((gamma_i/N)*X_i,k). Exploration is inside every EXP3 row; SR_MAB adds
no outer exploration or post-solve clipping. The master denominator p_k is
the actual logged propensity, checked against the saved round. The separate
EXP3 observed-gain interface uses q_i,k as required by Lemma 10, not p_k again.
Bandit bases are restricted to EXP3, whose update satisfies that lemma.

base_factory(actions) must return a fresh learner for each universe action.
Feedback closes one synchronous round: repeat queries of the same menu are
idempotent, but changing the menu before feedback is rejected. Delayed or
out-of-order training requires a separate adapter/algorithm with decision
snapshots; the thin Feedback type cannot identify earlier rounds.

The explicit snapshot()/update_from_snapshot() extension supports that adapter.
snapshot() detaches the pending round, freezing its support, solved p and every
base row Q. Later feedback trains the current base weights with that round's
p_i-scaled losses, or its p_i, q_i,k and logged p_k for SR_MAB. It never restores
old weights. Full-information Hedge updates add -eta*p_i*loss[j] in log space,
so delivery order for a fixed collection of snapshots is immaterial apart from
floating-point rounding. This does not imply identical policies when feedback
is delayed during play, or commutativity for arbitrary custom base learners.

For filtering, all N rows propose over the feasible menu and the stationary
system uses the feasible rows/columns. Unavailable actions have p_i=0. This
preserves stationarity on the actual menu, but no dynamic-menu regret theorem
is claimed. The exact rational solve normalizes rows before solving to remove
floating-point sum error; returned p is never clipped or renormalized.
"""

import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from fractions import Fraction

from .base import (
    BanditFeedback,
    Feedback,
    FullInfoFeedback,
    Learner,
    _actions,
    _probabilities,
    _state,
    _support,
)
from .exp3 import EXP3


@dataclass(frozen=True)
class BlumMansourSnapshot:
    """Immutable round data belongs to the learner that produced it.

    Rows follow the fixed universe order; each row's entries and p retain the
    feasible order. Weight state is deliberately excluded so late updates can
    accumulate on current weights. Exactly-once delivery belongs to the adapter.
    """

    support: tuple[str, ...]
    p: tuple[tuple[str, float], ...]
    rows: tuple[tuple[tuple[str, float], ...], ...]
    _owner: object = field(repr=False, compare=False)


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


class BlumMansour:
    """N independent external-regret learners supply one stationary master policy."""

    def __init__(
        self,
        base_factory: Callable[[Sequence[str]], Learner],
        actions: Sequence[str],
        *,
        id: str = "blum_mansour",
    ) -> None:
        """Create a fresh base per action; reject shared base objects and mixed EXP3 modes."""
        self.actions = _actions(actions)
        self.id = id
        self._bases = tuple(base_factory(self.actions) for _ in self.actions)
        if any(a is b for i, a in enumerate(self._bases) for b in self._bases[i + 1 :]):
            raise ValueError("base_factory must return independent learners")
        bandit = tuple(isinstance(base, EXP3) for base in self._bases)
        if any(bandit) and not all(bandit):
            raise TypeError("EXP3 bases cannot be mixed with full-information bases")
        self._bandit = all(bandit)
        self._pending: (
            tuple[tuple[str, ...], dict[str, float], tuple[dict[str, float], ...]] | None
        ) = None

    def distribution(self, feasible: Sequence[str]) -> dict[str, float]:
        """Solve on feasible rows/columns and preserve the saved round until feedback."""
        support = _support(feasible, self.actions)
        if self._pending is not None:
            if support != self._pending[0]:
                raise RuntimeError("close the pending round before changing support")
            return self._pending[1].copy()
        rows = tuple(base.distribution(support) for base in self._bases)
        for row in rows:
            _probabilities(row, support)
        by_action = dict(zip(self.actions, rows, strict=True))
        matrix = [[by_action[a][b] for b in support] for a in support]
        p = dict(zip(support, stationary_distribution(matrix), strict=True))
        self._pending = support, p, rows
        return p.copy()

    def update(self, feedback: Feedback) -> None:
        """Deliver scaled losses or SR_MAB observed gains from the saved decision round."""
        if self._pending is None:
            raise RuntimeError("distribution must open a round before feedback")
        support, p, rows = self._pending
        if isinstance(feedback, FullInfoFeedback):
            if self._bandit:
                raise TypeError("EXP3 bases require bandit feedback")
            losses = FullInfoFeedback(feedback.losses).losses
            if set(losses) != set(self.actions):
                raise ValueError("full feedback must cover the fixed action universe")
            updates = [
                FullInfoFeedback({b: p.get(a, 0.0) * losses[b] for b in self.actions})
                for a in self.actions
            ]
            for base, update in zip(self._bases, updates, strict=True):
                base.update(update)
        elif isinstance(feedback, BanditFeedback):
            if not self._bandit:
                raise TypeError("SR_MAB requires EXP3 bases satisfying Lemma 10")
            k = feedback.action
            if k not in support or not math.isclose(
                feedback.propensity,
                p[k],
                rel_tol=1e-12,
                abs_tol=0,
            ):
                raise ValueError("feedback must carry the saved round's executed propensity")
            gains = [
                p.get(a, 0.0) * feedback.reward * row[k] / feedback.propensity
                for a, row in zip(self.actions, rows, strict=True)
            ]
            assert all(math.isfinite(g) and 0 <= g <= 1 for g in gains)
            for base, row, gain in zip(self._bases, rows, gains, strict=True):
                base.update_observed_gain(k, gain, row[k])
        else:
            raise TypeError("unsupported feedback")
        self._pending = None

    def snapshot(self) -> BlumMansourSnapshot:
        """Detach the pending round into immutable data, permitting another query.

        No weights change. Calling without a pending distribution raises
        RuntimeError. Ordinary distribution/update calls remain synchronous.
        """
        if self._pending is None:
            raise RuntimeError("distribution must open a round before snapshot")
        support, p, rows = self._pending
        snapshot = BlumMansourSnapshot(
            support,
            tuple(p.items()),
            tuple(tuple(row.items()) for row in rows),
            self,
        )
        self._pending = None
        return snapshot

    def update_from_snapshot(self, snapshot: BlumMansourSnapshot, feedback: Feedback) -> None:
        """Train current weights using this learner's frozen round and existing validation.

        Any ordinary pending round survives success or failure unchanged. The
        caller owns snapshot consumption; invalid feedback can be retried.
        """
        if not isinstance(snapshot, BlumMansourSnapshot) or snapshot._owner is not self:
            raise ValueError("snapshot must belong to this BlumMansour learner")
        pending = self._pending
        self._pending = snapshot.support, dict(snapshot.p), tuple(map(dict, snapshot.rows))
        try:
            self.update(feedback)
        finally:
            self._pending = pending

    def state(self) -> bytes:
        """Return parameters, every base state, and the pending decision snapshot."""
        return _state(
            algorithm="BlumMansour",
            id=self.id,
            actions=self.actions,
            bases=[base.state().hex() for base in self._bases],
            pending=self._pending,
        )
