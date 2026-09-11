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
kept for this instance's lifetime, but excluded from state() so an adapter with
no outstanding snapshots returns exactly inner.state(). State is for hashing,
not serialization of the adapter's handle lifecycle.
"""

from collections.abc import Sequence

from .base import Feedback, Learner, _state
from .blum_mansour import BlumMansour, BlumMansourSnapshot


class SnapshotLearner:
    """Each handle trains its captured round at most once after a successful update."""

    def __init__(self, inner: Learner, *, id: str | None = None) -> None:
        """Retain the inner learner and inherit its identity unless explicitly overridden."""
        self.inner = inner
        self.id = inner.id if id is None else id
        self._snapshots: dict[str, dict[str, float] | BlumMansourSnapshot] = {}
        self._used_handles: set[str] = set()

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

    def update(self, feedback: Feedback) -> None:
        """Reject unaddressed feedback, which cannot identify a delayed decision."""
        raise TypeError("SnapshotLearner requires update_for(handle, feedback)")

    def state(self) -> bytes:
        """Include inner state and frozen rounds; return inner bytes when none remain."""
        if not self._snapshots:
            return self.inner.state()
        snapshots = {
            handle: {"support": saved.support, "p": saved.p, "rows": saved.rows}
            if isinstance(saved, BlumMansourSnapshot)
            else saved
            for handle, saved in self._snapshots.items()
        }
        return _state(
            algorithm="SnapshotLearner",
            id=self.id,
            inner=self.inner.state().hex(),
            snapshots=snapshots,
        )
