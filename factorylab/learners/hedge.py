"""Hedge preserves w[a] <- w[a] exp(-eta * loss[a]) in log space.

Feasible menus condition the original weights; they do not erase history or
establish a sleeping-experts regret guarantee. Full feedback must cover the
fixed universe, including currently unavailable actions.
"""

import math
from collections.abc import Sequence
from dataclasses import dataclass

from .base import Feedback, FullInfoFeedback, _actions, _center, _state, _support, _weights


@dataclass(frozen=True)
class Support:
    """Feasible order and unavailable universe members describe the last query."""

    feasible: tuple[str, ...]
    unavailable: tuple[str, ...]


class Hedge:
    """Fixed-universe exponential weights accept only full-information losses."""

    def __init__(self, actions: Sequence[str], eta: float, *, id: str = "hedge") -> None:
        """Start uniform weights with a finite positive learning rate."""
        if not math.isfinite(eta) or eta <= 0:
            raise ValueError("eta must be finite and positive")
        self.actions = _actions(actions)
        self.id = id
        self.eta = eta
        self._log_weights = dict.fromkeys(self.actions, 0.0)
        self._last_support = Support(self.actions, ())

    def distribution(self, feasible: Sequence[str]) -> dict[str, float]:
        """Condition original weights on feasible actions, preserving their order."""
        support = _support(feasible, self.actions)
        self._last_support = Support(support, tuple(a for a in self.actions if a not in support))
        return _weights(self._log_weights, support)

    def last_support(self) -> Support:
        """Return immutable feasible and unavailable IDs from the last distribution."""
        return self._last_support

    def update(self, feedback: Feedback) -> None:
        """Multiply each weight by exp(-eta * loss), rejecting incomplete feedback."""
        if not isinstance(feedback, FullInfoFeedback):
            raise TypeError("Hedge requires FullInfoFeedback")
        # Revalidate because the public feedback dictionary can be edited after construction.
        losses = FullInfoFeedback(feedback.losses).losses
        if set(losses) != set(self.actions):
            raise ValueError("full feedback must cover the fixed action universe")
        self._log_weights = _center(
            {a: self._log_weights[a] - self.eta * losses[a] for a in self.actions}
        )

    def state(self) -> bytes:
        """Return deterministic weights, parameters, identity, and last support."""
        return _state(
            algorithm="Hedge", id=self.id, actions=self.actions, eta=self.eta,
            log_weights=self._log_weights, feasible=self._last_support.feasible,
            unavailable=self._last_support.unavailable,
        )
