"""Consequence weights reflect forecast skill subject to an observed-coverage cap."""

from dataclasses import dataclass

from factorylab.settlement.scoring import _require_id, _require_probability


@dataclass
class _Standing:
    n: int = 0
    sum_brier: float = 0.0
    sum_baseline_brier: float = 0.0
    settled: int = 0
    requested: int = 0


class ConsequenceStanding:
    """Each evaluator's consequence weight is bounded and insufficient coverage limits gains."""

    def __init__(self, min_coverage: float) -> None:
        _require_probability(min_coverage, "min_coverage")
        self.__min_coverage = min_coverage
        self.__evaluators: dict[str, _Standing] = {}

    def record(self, evaluator_id: str, brier: float, baseline_brier: float) -> None:
        """Add one observed score pair to this evaluator's skill and settled count."""
        _require_id(evaluator_id)
        _require_probability(brier, "brier")
        _require_probability(baseline_brier, "baseline_brier")
        standing = self.__evaluators.setdefault(evaluator_id, _Standing())
        standing.n += 1
        standing.sum_brier += brier
        standing.sum_baseline_brier += baseline_brier
        standing.settled += 1

    def set_requested(self, evaluator_id: str, requested: int) -> None:
        """Use the book's nonnegative sealed count as this evaluator's coverage denominator."""
        _require_id(evaluator_id)
        if type(requested) is not int or requested < 0:
            raise ValueError("requested must be a nonnegative integer")
        self.__evaluators.setdefault(evaluator_id, _Standing()).requested = requested

    def skill(self, evaluator_id: str) -> float:
        """Return mean Brier minus matched baseline mean, or zero without observed scores."""
        _require_id(evaluator_id)
        standing = self.__evaluators.get(evaluator_id)
        if standing is None or not standing.n:
            return 0.0
        return standing.sum_brier / standing.n - standing.sum_baseline_brier / standing.n

    def coverage(self, evaluator_id: str) -> float:
        """Return scored settlements divided by requested forecasts, or zero with no requests."""
        _require_id(evaluator_id)
        standing = self.__evaluators.get(evaluator_id)
        if standing is None or not standing.requested:
            return 0.0
        return standing.settled / standing.requested

    def weight(self, evaluator_id: str) -> float:
        """Return clipped 0.5 + skill, capped at 0.5 below minimum coverage; default to 0.5."""
        weight = min(1.0, max(0.0, 0.5 + self.skill(evaluator_id)))
        if self.coverage(evaluator_id) < self.__min_coverage:
            return min(0.5, weight)
        return weight

    def snapshot(self) -> dict:
        """Return detached JSON-ready counts, score means, skill, coverage and weights by id."""
        return {
            evaluator_id: {
                **vars(standing),
                "mean_brier": standing.sum_brier / standing.n if standing.n else 0.0,
                "mean_baseline_brier": (
                    standing.sum_baseline_brier / standing.n if standing.n else 0.0
                ),
                "skill": self.skill(evaluator_id),
                "coverage": self.coverage(evaluator_id),
                "weight": self.weight(evaluator_id),
            }
            for evaluator_id, standing in self.__evaluators.items()
        }
