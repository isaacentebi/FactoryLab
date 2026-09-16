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


#: A predicate whose base rate is this far from even, over at least
#: ``UNINFORMATIVE_SUPPORT`` observations, is a question whose answer is already
#: known: a forecast on it earns no standing (GPT-6 third reading, §3 further:
#: "forecast-shaped returns can reward easy questions"; §10: "easy forecasts
#: dominate"). The bound is on the question, not on the forecaster.
UNINFORMATIVE_HIGH = 0.95
UNINFORMATIVE_LOW = 0.05
UNINFORMATIVE_SUPPORT = 20


class PrevalenceBaseline:
    """Each predicate's base rate depends only on its previously recorded outcomes."""

    def __init__(self) -> None:
        self.__counts: dict[str, tuple[int, float]] = {}

    def baseline_q(self, predicate_id: str) -> float:
        """Return the observed positive fraction, or 0.5 before any observations."""
        _require_id(predicate_id)
        count, positives = self.__counts.get(predicate_id, (0, 0))
        return positives / count if count else 0.5

    def baseline_brier(self, predicate_id: str, y: int) -> float:
        """Score y against the current base rate without recording the observation."""
        return brier(self.baseline_q(predicate_id), y)

    def support(self, predicate_id: str) -> int:
        """How many observations this predicate's base rate rests on."""
        _require_id(predicate_id)
        return self.__counts.get(predicate_id, (0, 0))[0]

    def uninformative(self, predicate_id: str) -> bool:
        """Whether this predicate's base rate already answers the question.

        True only with real support: a predicate whose first observation happened
        to be positive has a base rate of 1.0 and has settled nothing. Below
        ``UNINFORMATIVE_SUPPORT`` observations the prevalence is not yet evidence
        that the question is easy.
        """
        if self.support(predicate_id) < UNINFORMATIVE_SUPPORT:
            return False
        q = self.baseline_q(predicate_id)
        return q >= UNINFORMATIVE_HIGH or q <= UNINFORMATIVE_LOW

    def record(self, predicate_id: str, y: int) -> None:
        """Include one valid observation in this predicate's future base rate."""
        _require_id(predicate_id)
        _require_outcome(y)
        count, positives = self.__counts.get(predicate_id, (0, 0))
        self.__counts[predicate_id] = (count + 1, positives + y)

    def record_fraction(self, predicate_id: str, target: float) -> None:
        """Include one unit-interval target in this predicate's future base rate.

        A normative outcome is a fraction, not a binary event: a return that
        carries a tenth of its window's blame has the target 0.9, and the base
        rate a verdict about it is scored against must learn that same 0.9,
        not whether the blame was exactly zero.
        """
        _require_id(predicate_id)
        _require_probability(target, "target")
        count, positives = self.__counts.get(predicate_id, (0, 0))
        self.__counts[predicate_id] = (count + 1, positives + target)
