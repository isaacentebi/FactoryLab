"""Consequence weights reflect forecast and verdict skill subject to an observed-coverage cap."""

from dataclasses import dataclass

from factorylab.settlement.scoring import _require_id, _require_probability


@dataclass
class _Standing:
    n: int = 0
    sum_brier: float = 0.0
    sum_baseline_brier: float = 0.0
    settled: int = 0
    requested: int = 0
    # Verdicts scored against the charter's realised blame on the returns they endorsed.
    verdict_n: int = 0
    sum_verdict_brier: float = 0.0
    sum_verdict_baseline_brier: float = 0.0


class ConsequenceStanding:
    """Each evaluator's consequence weight is bounded and insufficient coverage limits gains.

    Two kinds of score train it, each against its own matched baseline: the
    mandatory payoff forecast against the realised consequence, and the verdict
    against the charter's realised blame on the judged return. Skill pools both;
    coverage counts payoff forecasts only, so a judge cannot earn coverage by
    issuing verdicts.
    """

    def __init__(self, min_coverage: float) -> None:
        _require_probability(min_coverage, "min_coverage")
        self.__min_coverage = min_coverage
        self.__evaluators: dict[str, _Standing] = {}

    def record(self, evaluator_id: str, brier: float, baseline_brier: float) -> None:
        """Add one observed payoff score pair to this evaluator's skill and settled count."""
        _require_id(evaluator_id)
        _require_probability(brier, "brier")
        _require_probability(baseline_brier, "baseline_brier")
        standing = self.__evaluators.setdefault(evaluator_id, _Standing())
        standing.n += 1
        standing.sum_brier += brier
        standing.sum_baseline_brier += baseline_brier
        standing.settled += 1

    def record_verdict(self, evaluator_id: str, brier: float, baseline_brier: float) -> None:
        """Add one settled verdict score pair to this evaluator's skill, never to coverage."""
        _require_id(evaluator_id)
        _require_probability(brier, "brier")
        _require_probability(baseline_brier, "baseline_brier")
        standing = self.__evaluators.setdefault(evaluator_id, _Standing())
        standing.verdict_n += 1
        standing.sum_verdict_brier += brier
        standing.sum_verdict_baseline_brier += baseline_brier

    def set_requested(self, evaluator_id: str, requested: int) -> None:
        """Use the book's nonnegative sealed count as this evaluator's coverage denominator."""
        _require_id(evaluator_id)
        if type(requested) is not int or requested < 0:
            raise ValueError("requested must be a nonnegative integer")
        self.__evaluators.setdefault(evaluator_id, _Standing()).requested = requested

    def skill(self, evaluator_id: str) -> float:
        """Return mean Brier minus matched baseline mean over payoff and verdict scores pooled,
        or zero without observed scores."""
        _require_id(evaluator_id)
        standing = self.__evaluators.get(evaluator_id)
        if standing is None:
            return 0.0
        count = standing.n + standing.verdict_n
        if not count:
            return 0.0
        scored = standing.sum_brier + standing.sum_verdict_brier
        baseline = standing.sum_baseline_brier + standing.sum_verdict_baseline_brier
        return scored / count - baseline / count

    def payoff_skill(self, evaluator_id: str) -> float:
        """Return the payoff-forecast part of skill alone, or zero without settled forecasts."""
        _require_id(evaluator_id)
        standing = self.__evaluators.get(evaluator_id)
        if standing is None or not standing.n:
            return 0.0
        return standing.sum_brier / standing.n - standing.sum_baseline_brier / standing.n

    def verdict_skill(self, evaluator_id: str) -> float:
        """Return the verdict part of skill alone, or zero without settled verdicts."""
        _require_id(evaluator_id)
        standing = self.__evaluators.get(evaluator_id)
        if standing is None or not standing.verdict_n:
            return 0.0
        return (standing.sum_verdict_brier / standing.verdict_n
                - standing.sum_verdict_baseline_brier / standing.verdict_n)

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
        """Return detached JSON-ready counts, score means, skills, coverage and weights by id."""
        return {
            evaluator_id: {
                **vars(standing),
                "mean_brier": standing.sum_brier / standing.n if standing.n else 0.0,
                "mean_baseline_brier": (
                    standing.sum_baseline_brier / standing.n if standing.n else 0.0
                ),
                "mean_verdict_brier": (
                    standing.sum_verdict_brier / standing.verdict_n if standing.verdict_n
                    else 0.0
                ),
                "mean_verdict_baseline_brier": (
                    standing.sum_verdict_baseline_brier / standing.verdict_n
                    if standing.verdict_n else 0.0
                ),
                "skill": self.skill(evaluator_id),
                "payoff_skill": self.payoff_skill(evaluator_id),
                "verdict_skill": self.verdict_skill(evaluator_id),
                "coverage": self.coverage(evaluator_id),
                "weight": self.weight(evaluator_id),
            }
            for evaluator_id, standing in self.__evaluators.items()
        }
