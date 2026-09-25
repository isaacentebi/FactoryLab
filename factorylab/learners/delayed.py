"""Handle-addressed feedback retains the actual decision round until settlement.

BlumMansour snapshots freeze the feasible support, solved master policy p, and
all base proposal rows Q through its explicit public extension. A full-info
base receives that round's p_i*loss[j]; SR_MAB uses that round's p_i, q_i,k and
logged master propensity p_k. Current base weights are never rolled back.
For Hedge bases, each full-info log-weight increment depends only on its own
round's p_i and losses. These increments commute mathematically, with ordinary
floating-point rounding, for the SAME snapshots delivered in any order. This
is not a guarantee for arbitrary stateful base algorithms or for the policies
of a run that receives feedback earlier and therefore learns while sampling.

Other learners retain only the returned distribution: Hedge already consumes
a full loss vector, and EXP3 uses the feedback's logged propensity. Custom
learners must likewise accept feedback without a pending-round dependency.
Handles cannot be reopened, even after settlement. Failed updates retain their
snapshot for retry; successful updates discard it. A spent-handle tombstone is
kept in state(), including after the last outstanding snapshot is consumed, so
process recovery cannot reopen a spent handle.
"""

from collections.abc import Sequence
from dataclasses import replace

from .base import (
    Feedback,
    Learner,
    ObservedRewards,
    _probabilities,
    _state,
    _support,
    restore_learner,
)
from .blum_mansour import BlumMansour, BlumMansourSnapshot


class SnapshotLearner:
    """Each handle trains its captured round at most once after a successful update."""

    def __init__(self, inner: Learner, *, id: str | None = None) -> None:
        """Retain the inner learner and inherit its identity unless explicitly overridden."""
        self.inner = inner
        self.id = inner.id if id is None else id
        self._snapshots: dict[str, dict[str, float] | BlumMansourSnapshot] = {}
        self._used_handles: set[str] = set()
        # The rewards this learner's rounds actually observed, for neutral censoring.
        self.observed = ObservedRewards()

    def distribution(self, feasible: Sequence[str]) -> dict[str, float]:
        """Delegate the plain protocol query without creating a handle snapshot."""
        return self.inner.distribution(feasible)

    def distribution_for(self, handle: str, feasible: Sequence[str]) -> dict[str, float]:
        """Return the current policy and freeze its round under a previously unused handle."""
        if not isinstance(handle, str):
            raise TypeError("handle must be a string")
        if handle in self._used_handles:
            raise KeyError(handle)
        distribution = self.inner.distribution(feasible)
        snapshot = (
            self.inner.snapshot() if isinstance(self.inner, BlumMansour) else distribution.copy()
        )
        self._snapshots[handle] = snapshot
        self._used_handles.add(handle)
        return distribution

    def update_for(self, handle: str, feedback: Feedback) -> None:
        """Apply feedback to its saved round; consume the handle only on success."""
        snapshot = self._snapshots[handle]
        if isinstance(self.inner, BlumMansour):
            self.inner.update_from_snapshot(snapshot, feedback)
        else:
            self.inner.update(feedback)
        del self._snapshots[handle]

    def record_executed(self, handle: str, distribution: dict[str, float]) -> None:
        """Freeze the actual sampling policy while retaining the learner's own row weights."""
        snapshot = self._snapshots[handle]
        support = snapshot.support if isinstance(snapshot, BlumMansourSnapshot) else tuple(snapshot)
        _probabilities(distribution, support)
        if isinstance(snapshot, BlumMansourSnapshot):
            if any(dict(snapshot.p)[a] > 0 and distribution[a] <= 0 for a in support):
                raise ValueError("executed policy must cover the learner's support")
            self._snapshots[handle] = replace(snapshot, executed=tuple(distribution.items()))
        else:
            self._snapshots[handle] = dict(distribution)

    def discard_for(self, handle: str) -> None:
        """Close a censored round without fabricating reward or permitting handle reuse."""
        del self._snapshots[handle]

    def take_for(self, handle: str) -> tuple[dict[str, float], dict[str, float]]:
        """Close a round to hand it to a successor: (owning policy p, executed policy).

        The handle stays spent here, so the round trains this learner never again;
        the returned pair is everything ``update_carried`` needs to train another.
        """
        saved = self._snapshots.pop(handle)
        if isinstance(saved, BlumMansourSnapshot):
            p = dict(saved.p)
            return p, dict(saved.executed) if saved.executed is not None else p
        return dict(saved), dict(saved)

    def update_carried(self, p: dict[str, float], executed: dict[str, float],
                       feedback: Feedback) -> None:
        """Train this learner on a round a predecessor drew, sampled from ``executed``.

        A swap learner credits each row its share of ``p`` (``BlumMansour.update_carried``);
        any other inner learner takes the bandit feedback, whose logged propensity is
        the executed one. No handle is opened or spent.
        """
        if isinstance(self.inner, BlumMansour):
            self.inner.update_carried(p, executed, feedback)
        else:
            self.inner.update(feedback)

    def update(self, feedback: Feedback) -> None:
        """Reject unaddressed feedback, which cannot identify a delayed decision."""
        raise TypeError("SnapshotLearner requires update_for(handle, feedback)")

    def state(self) -> dict:
        """Include exact inner state, frozen rounds, identity and all spent-handle tombstones."""
        snapshots = {
            handle: {"support": saved.support, "p": saved.p, "rows": saved.rows,
                     "executed": saved.executed}
            if isinstance(saved, BlumMansourSnapshot)
            else saved
            for handle, saved in self._snapshots.items()
        }
        return _state(
            algorithm="SnapshotLearner",
            id=self.id,
            inner=self.inner.state(),
            snapshots=snapshots,
            used_handles=sorted(self._used_handles),
            observed=self.observed.state(),
        )

    @classmethod
    def restore(cls, state: dict) -> "SnapshotLearner":
        """Rebind every delayed round to its restored owner and preserve one-use handles."""
        if state.get("algorithm") != "SnapshotLearner":
            raise ValueError("learner algorithm mismatch")
        inner = restore_learner(state["inner"])
        learner = cls(inner, id=state["id"])
        used = state["used_handles"]
        if any(not isinstance(h, str) for h in used) or len(set(used)) != len(used):
            raise ValueError("invalid saved handles")
        if not set(state["snapshots"]) <= set(used):
            raise ValueError("snapshot without a handle tombstone")
        learner._used_handles = set(used)
        learner.observed = ObservedRewards(state.get("observed"))
        for handle, saved in state["snapshots"].items():
            if isinstance(inner, BlumMansour):
                support = _support(saved["support"], inner.actions)
                _probabilities(dict(saved["p"]), support)
                if len(saved["rows"]) != len(inner.actions):
                    raise ValueError("invalid saved proposal rows")
                for row in saved["rows"]:
                    _probabilities(dict(row), support)
                saved = BlumMansourSnapshot(
                    support, tuple(tuple(pair) for pair in saved["p"]),
                    tuple(tuple(tuple(pair) for pair in row) for row in saved["rows"]), inner,
                    tuple(tuple(pair) for pair in saved["executed"])
                    if saved.get("executed") is not None else None,
                )
                if saved.executed is not None:
                    _probabilities(dict(saved.executed), support)
            else:
                _probabilities(saved, tuple(saved))
                saved = dict(saved)
            learner._snapshots[handle] = saved
        return learner
