"""Bounded feedback and a kernel-independent learner contract."""

import json
import math
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class FullInfoFeedback:
    """Losses are finite values in [0, 1], copied from the caller's dictionary."""

    losses: dict[str, float]

    def __post_init__(self) -> None:
        assert all(math.isfinite(v) and 0 <= v <= 1 for v in self.losses.values())
        object.__setattr__(self, "losses", dict(self.losses))


@dataclass(frozen=True)
class BanditFeedback:
    """Reward is in [0, 1]; propensity is the recorded positive sampling probability."""

    action: str
    reward: float
    propensity: float

    def __post_init__(self) -> None:
        assert math.isfinite(self.reward) and 0 <= self.reward <= 1
        assert math.isfinite(self.propensity) and 0 < self.propensity <= 1


type Feedback = FullInfoFeedback | BanditFeedback


class ObservedRewards:
    """Running sums of the rewards a learner actually observed, per action.

    Censoring is neutral with it (defect 2). For a gain-based learner such as
    EXP3 a round skipped for want of an outcome is a round credited zero, so an
    arm whose outcomes are censored more often falls behind an arm of the same
    worth whose outcomes are read. ``neutral`` is the learner's best estimate of
    an unobserved reward, the observed mean of the arm that was drawn, else of
    every arm the learner has observed, else nothing: an unknown is imputed at
    what the evidence says, never at zero, and never invented where there is no
    evidence at all (with no observation yet, no arm has moved, so no update is
    already neutral). The imputed value is used for the update only; it never
    enters these sums, so the estimate is built from observations alone.
    """

    def __init__(self, sums: dict[str, list] | None = None) -> None:
        self.sums: dict[str, list] = {a: [float(v[0]), int(v[1])]
                                      for a, v in (sums or {}).items()}

    def record(self, action: str, reward: float) -> None:
        """Add one observed reward in [0, 1] for ``action``."""
        assert math.isfinite(reward) and 0 <= reward <= 1
        total, count = self.sums.get(action, (0.0, 0))
        self.sums[action] = [total + reward, count + 1]

    def neutral(self, action: str) -> float | None:
        """The observed mean for ``action``, else across actions, else None."""
        total, count = self.sums.get(action, (0.0, 0))
        if count:
            return min(1.0, max(0.0, total / count))
        count = sum(c for _t, c in self.sums.values())
        if not count:
            return None
        return min(1.0, max(0.0, sum(t for t, _c in self.sums.values()) / count))

    def state(self) -> dict[str, list]:
        """Plain, JSON-serialisable sums: action -> [sum, count]."""
        return {a: [t, c] for a, (t, c) in self.sums.items()}


@runtime_checkable
class Learner(Protocol):
    """Private learning state round-trips through JSON, independent of kernel records."""

    id: str

    def distribution(self, feasible: Sequence[str]) -> dict[str, float]:
        """Return a probability distribution supported only on feasible actions."""
        ...

    def update(self, feedback: Feedback) -> None:
        """Apply bounded feedback according to the learner's declared feedback model."""
        ...

    def state(self) -> dict:
        """Return complete, detached, JSON-serialisable private state."""
        ...

    @classmethod
    def restore(cls, state: dict) -> "Learner":
        """Return an independent learner with exactly the saved continuation state."""
        ...


def _actions(actions: Sequence[str]) -> tuple[str, ...]:
    result = tuple(actions)
    if not result or len(set(result)) != len(result):
        raise ValueError("actions must be nonempty and unique")
    if any(not isinstance(action, str) or not action for action in result):
        raise ValueError("action IDs must be nonempty strings")
    return result


def _support(feasible: Sequence[str], actions: tuple[str, ...]) -> tuple[str, ...]:
    result = _actions(feasible)
    if not set(result) <= set(actions):
        raise ValueError("feasible actions must belong to the fixed universe")
    return result


def _probabilities(distribution: dict[str, float], support: Sequence[str]) -> None:
    if set(distribution) != set(support):
        raise ValueError("distribution must contain exactly the feasible actions")
    if any(not math.isfinite(p) or not 0 <= p <= 1 for p in distribution.values()):
        raise ValueError("invalid probability")
    if not math.isclose(math.fsum(distribution.values()), 1.0, rel_tol=0, abs_tol=1e-12):
        raise ValueError("probabilities must sum to one")


def _state(**values: object) -> dict:
    # Python's JSON encoder uses repr-round-trippable float numbers, not rounded decimals.
    return json.loads(json.dumps(values, allow_nan=False))


def state_bytes(state: dict) -> bytes:
    """Return canonical JSON bytes without rounding any finite floating-point number."""
    return json.dumps(state, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def restore_learner(state: dict) -> Learner:
    """Restore only the explicitly supported algorithms; never import a ledger-supplied name."""
    from .blum_mansour import BlumMansour
    from .delayed import SnapshotLearner
    from .exp3 import EXP3
    from .hedge import Hedge

    kinds = {c.__name__: c for c in (EXP3, Hedge, BlumMansour, SnapshotLearner)}
    if not isinstance(state, dict) or state.get("algorithm") not in kinds:
        raise ValueError("unknown learner state")
    return kinds[state["algorithm"]].restore(state)


def _restore_weights(state: dict, algorithm: str) -> dict[str, float]:
    if state.get("algorithm") != algorithm:
        raise ValueError("learner algorithm mismatch")
    actions = _actions(state["actions"])
    weights = state["log_weights"]
    if set(weights) != set(actions) or any(
        type(w) not in (int, float) or not math.isfinite(w) for w in weights.values()
    ):
        raise ValueError("invalid saved log weights")
    return {a: weights[a] for a in actions}


def _weights(log_weights: dict[str, float], support: Sequence[str]) -> dict[str, float]:
    offset = max(log_weights[a] for a in support)
    weights = {a: math.exp(log_weights[a] - offset) for a in support}
    total = math.fsum(weights.values())
    return {a: w / total for a, w in weights.items()}


def _center(log_weights: dict[str, float]) -> dict[str, float]:
    offset = max(log_weights.values())
    centered = {a: w - offset for a, w in log_weights.items()}
    if any(not math.isfinite(w) for w in centered.values()):
        raise ValueError("update exceeds finite log-weight range")
    return centered
