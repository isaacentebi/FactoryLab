"""Binary forecasts are scored against a baseline using only earlier observations."""

import math


def _require_probability(value: float, name: str) -> None:
    if type(value) not in (int, float) or not 0 <= value <= 1 or not math.isfinite(value):
        raise ValueError(f"{name} must be finite and in [0, 1]")


def _require_outcome(y: int) -> None:
    if type(y) is not int or y not in (0, 1):
        raise ValueError("y must be the integer 0 or 1")


def _require_id(value: str) -> None:
    if not isinstance(value, str) or not value:
        raise ValueError("a nonempty id is required")


def brier(q: float, y: int) -> float:
    """Return 1 - (q - y)^2 in [0, 1] for a finite probability and binary outcome."""
    _require_probability(q, "q")
    _require_outcome(y)
    return 1.0 - (q - y) ** 2


class PrevalenceBaseline:
    """Each predicate's base rate depends only on its previously recorded outcomes."""

    def __init__(self) -> None:
        self.__counts: dict[str, tuple[int, int]] = {}

    def baseline_q(self, predicate_id: str) -> float:
        """Return the observed positive fraction, or 0.5 before any observations."""
        _require_id(predicate_id)
        count, positives = self.__counts.get(predicate_id, (0, 0))
        return positives / count if count else 0.5

    def baseline_brier(self, predicate_id: str, y: int) -> float:
        """Score y against the current base rate without recording the observation."""
        return brier(self.baseline_q(predicate_id), y)

    def record(self, predicate_id: str, y: int) -> None:
        """Include one valid observation in this predicate's future base rate."""
        _require_id(predicate_id)
        _require_outcome(y)
        count, positives = self.__counts.get(predicate_id, (0, 0))
        self.__counts[predicate_id] = (count + 1, positives + y)
