"""The charter: norms and metric cards, rendered for evaluators.

Phase 2 keeps the charter immutable. Norms are the read-only layer the essay
reserves for the architect; metric cards are the operational interpretations
that a later phase lets the population amend. Nothing here is enforced by
code: the charter is what evaluators are asked to judge against, and the
consequence channel is what keeps that judging honest.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class MetricCard:
    """One operational interpretation of a norm. Text fields are what evaluators read."""

    id: str
    norm: str
    description: str
    units: str
    window: str
    acceptable_region: str
    observation: str  # where the number would come from; informational in this phase

    def __post_init__(self) -> None:
        for name in ("id", "norm", "description", "units", "window", "acceptable_region"):
            if not getattr(self, name):
                raise ValueError(f"metric card needs {name}")


@dataclass(frozen=True)
class Charter:
    """Immutable in phase 2. ``render()`` is the exact text evaluators see."""

    edition: int
    norms: tuple[str, ...]
    cards: tuple[MetricCard, ...]

    def __post_init__(self) -> None:
        if self.edition < 1:
            raise ValueError("charter edition starts at 1")
        if not self.norms:
            raise ValueError("a charter needs at least one norm")
        ids = [c.id for c in self.cards]
        if len(set(ids)) != len(ids):
            raise ValueError("metric card ids must be unique")
        for c in self.cards:
            if c.norm not in self.norms:
                raise ValueError(f"card {c.id} references an unknown norm")

    def render(self) -> str:
        lines = [f"CHARTER (edition {self.edition})", "", "NORMS"]
        lines += [f"- {n}" for n in self.norms]
        lines += ["", "METRIC CARDS"]
        for c in self.cards:
            lines += [
                f"- {c.id} (norm: {c.norm})",
                f"  {c.description}",
                f"  units: {c.units}; window: {c.window}; acceptable: {c.acceptable_region}",
            ]
        return "\n".join(lines)


SEED_NORMS = (
    "truthful commitments",
    "care with scarce resources",
    "useful inquiry",
    "the capacity to revise inadequate practices",
)


def seed_charter() -> Charter:
    """The phase 2 charter: four norms, three cards, edition 1."""
    return Charter(
        edition=1,
        norms=SEED_NORMS,
        cards=(
            MetricCard(
                id="cost_per_return",
                norm="care with scarce resources",
                description="Wallet cost of producing one well-formed return.",
                units="micro-USD per return",
                window="rolling 100 returns",
                acceptable_region="below the median of the previous window",
                observation="cost_per_return",
            ),
            MetricCard(
                id="well_formed_rate",
                norm="truthful commitments",
                description="Share of returns that satisfy their declared outcome schema.",
                units="fraction",
                window="rolling 100 returns",
                acceptable_region="at least 0.9",
                observation="well_formed_rate",
            ),
            MetricCard(
                id="forecast_skill",
                norm="useful inquiry",
                description="Mean Brier score of settled forecasts minus the prevalence baseline.",
                units="score difference in [-1, 1]",
                window="rolling 50 settled forecasts per evaluator",
                acceptable_region="above zero",
                observation="forecast_skill",
            ),
        ),
    )
