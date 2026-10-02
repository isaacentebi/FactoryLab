"""Handle-addressed feedback: each opened round is frozen until it is learned or closed.

A round is opened under a handle at an ordinal the caller issues in nondecreasing
order (the runtime's event number, which never rewinds). Its snapshot freezes what
the round was drawn from: for the frontier (``EXP3``) the rates it was drawn at and
the executed policy; for the core (``BlumMansour``) its epoch, master policy p and
executed policy, O(N) because the row proposal cancels from the update
(docs/architecture/learners-noregret.md §2.2). Current weights are never rolled back.

A snapshot leaves exactly one way:
* ``update_for`` trains on it (or finds its core epoch closed: orphaned, trains nothing);
* ``discard_for`` closes it untrained (censored, expired, orphaned by the runtime);
* ``withdraw_for`` uncounts it: the draw never became a decision (a quiet tick).
A failed update keeps it for retry. state() keeps the highest ordinal issued and the
handles opened at it, so process recovery cannot reopen a spent handle, and what is
kept is bounded by the outstanding rounds and one ordinal's handles, never by the
rounds ever opened (essay II.II.b, "memory").
"""

import math
from collections.abc import Sequence

from .base import (
    Feedback,
    Learner,
    ObservedRewards,
    _probabilities,
    _state,
    _support,
    restore_learner,
)
from .blum_mansour import BlumMansour, CoreRound, Orphaned
from .exp3 import EXP3


class SnapshotLearner:
    """Each handle trains its captured round at most once."""

    def __init__(self, inner: Learner, *, id: str | None = None) -> None:
        """Wrap a frontier or core learner, inheriting its identity unless overridden."""
        if not isinstance(inner, (EXP3, BlumMansour)):
            raise TypeError("SnapshotLearner wraps EXP3 or BlumMansour")
        self.inner = inner
        self.id = inner.id if id is None else id
        self._snapshots: dict[str, dict | CoreRound] = {}
        # The issuance high-water mark and the handles opened at it: any handle opened
        # before is spent or outstanding, and an ordinal below the mark is refused.
        self._issued: int | None = None
        self._at_issued: set[str] = set()
        # The rewards this learner's rounds actually observed, for neutral censoring.
        self.observed = ObservedRewards()

    @property
    def core(self) -> bool:
        return isinstance(self.inner, BlumMansour)

    def distribution(self, feasible: Sequence[str]) -> dict[str, float]:
        """The next round's policy, without opening a round."""
        return self.inner.distribution(feasible)

    def outstanding(self) -> list[str]:
        """Handles whose rounds are open, in opening order."""
        return list(self._snapshots)

    def distribution_for(self, handle: str, feasible: Sequence[str], *,
                         ordinal: int) -> dict[str, float]:
        """Open a round under a previously unused handle and return its policy.

        Guarantees no handle is ever opened twice, provided the caller never names a
        handle again at a later ``ordinal``: an ordinal below the highest issued, or a
        handle already opened at that ordinal or still outstanding, raises KeyError and
        changes nothing.
        """
        if not isinstance(handle, str):
            raise TypeError("handle must be a string")
        if not isinstance(ordinal, int) or isinstance(ordinal, bool):
            raise TypeError("ordinal must be an integer")
        if (handle in self._snapshots or (self._issued is not None and (
                ordinal < self._issued or (ordinal == self._issued
                                           and handle in self._at_issued)))):
            raise KeyError(handle)
        if self.core:
            distribution, saved = self.inner.open_round(feasible)
        else:
            distribution = self.inner.distribution(feasible)
            rates = self.inner.open_round()
            saved = {**rates, "executed": dict(distribution)}
        self._snapshots[handle] = saved
        if ordinal != self._issued:
            self._issued, self._at_issued = ordinal, set()
        self._at_issued.add(handle)
        return dict(distribution)

    def exploration(self, handle: str) -> float:
        """The exploration gamma the round under ``handle`` was drawn at."""
        saved = self._snapshots[handle]
        return saved.gamma if isinstance(saved, CoreRound) else saved["gamma"]

    def record_executed(self, handle: str, distribution: dict[str, float]) -> None:
        """Freeze the policy actually sampled (after the world's draw transforms)."""
        saved = self._snapshots[handle]
        if isinstance(saved, CoreRound):
            _probabilities(distribution, saved.support)
            self._snapshots[handle] = CoreRound(
                saved.epoch, saved.support, saved.p, saved.gamma,
                tuple((a, float(distribution[a])) for a in saved.support))
        else:
            _probabilities(distribution, tuple(saved["executed"]))
            self._snapshots[handle] = {**saved, "executed": dict(distribution)}

    def update_for(self, handle: str, feedback: Feedback) -> bool:
        """Train on the round's snapshot and close it: True, or False when orphaned.

        A failed update raises and keeps the snapshot for retry.
        """
        saved = self._snapshots[handle]
        if isinstance(saved, CoreRound):
            try:
                self.inner.update_round(saved, feedback)
            except Orphaned:
                del self._snapshots[handle]
                return False
        else:
            executed = saved["executed"]
            if feedback.action not in executed or not math.isclose(
                    feedback.propensity, executed[feedback.action], rel_tol=1e-12, abs_tol=0):
                raise ValueError("feedback must carry the saved round's executed propensity")
            self.inner.update(feedback, eta=saved["eta"])
        del self._snapshots[handle]
        return True

    def discard_for(self, handle: str) -> None:
        """Close a round untrained; the handle stays spent."""
        del self._snapshots[handle]

    def withdraw_for(self, handle: str) -> None:
        """Uncount a draw that never became a decision; the handle stays spent."""
        saved = self._snapshots.pop(handle)
        if isinstance(saved, CoreRound):
            self.inner.withdraw_round(saved)
        else:
            self.inner.withdraw_round()

    def update(self, feedback: Feedback) -> None:
        """Reject unaddressed feedback, which cannot identify a delayed decision."""
        raise TypeError("SnapshotLearner requires update_for(handle, feedback)")

    def state(self) -> dict:
        """Exact inner state, frozen rounds, identity and the issuance mark."""
        snapshots = {handle: saved.state() if isinstance(saved, CoreRound) else saved
                     for handle, saved in self._snapshots.items()}
        return _state(
            algorithm="SnapshotLearner",
            id=self.id,
            inner=self.inner.state(),
            snapshots=snapshots,
            issued=self._issued,
            at_issued=sorted(self._at_issued),
            observed=self.observed.state(),
        )

    @classmethod
    def restore(cls, state: dict) -> "SnapshotLearner":
        """Rebind every open round and preserve one-use handles."""
        if state.get("algorithm") != "SnapshotLearner":
            raise ValueError("learner algorithm mismatch")
        inner = restore_learner(state["inner"])
        learner = cls(inner, id=state["id"])
        issued = state.get("issued")
        at_issued = state.get("at_issued", [])
        if issued is not None and (not isinstance(issued, int) or isinstance(issued, bool)):
            raise ValueError("invalid issuance mark")
        if (any(not isinstance(h, str) for h in at_issued)
                or len(set(at_issued)) != len(at_issued) or (issued is None and at_issued)):
            raise ValueError("invalid saved handles")
        if state["snapshots"] and issued is None:
            raise ValueError("snapshot without an issuance mark")
        learner._issued, learner._at_issued = issued, set(at_issued)
        learner.observed = ObservedRewards(state.get("observed"))
        for handle, saved in state["snapshots"].items():
            if isinstance(inner, BlumMansour):
                saved = CoreRound.restore(saved, inner.actions)
            else:
                executed = saved["executed"]
                _support(tuple(executed), inner.actions)
                _probabilities(executed, tuple(executed))
                for key in ("gamma", "eta"):
                    if type(saved[key]) not in (int, float) or not 0 < saved[key] <= 1:
                        raise ValueError("invalid saved rates")
                saved = {"gamma": float(saved["gamma"]), "eta": float(saved["eta"]),
                         "executed": {a: float(p) for a, p in executed.items()}}
            learner._snapshots[handle] = saved
        return learner
