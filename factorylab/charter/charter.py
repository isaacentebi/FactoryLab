"""Immutable charter editions expose norms and executable measurement contracts."""

from __future__ import annotations

from dataclasses import dataclass

from factorylab.charter.windows import MetricWindow
from factorylab.cortex.registration import CONTRACT_ROLES, ROLES, event_name


@dataclass(frozen=True)
class MetricCard:
    """One norm interpretation binds a typed window and an accountable emitted kind."""

    id: str
    norm: str
    description: str
    units: str
    window: MetricWindow
    acceptable_region: str
    observation: str
    answers_for: str

    def __post_init__(self) -> None:
        for name in ("id", "norm", "description", "units", "acceptable_region", "observation"):
            if not isinstance(getattr(self, name), str) or not getattr(self, name).strip():
                raise ValueError(f"metric card needs {name}")
        object.__setattr__(self, "window", MetricWindow.parse(self.window))
        try:
            scope = event_name(self.answers_for.strip() if isinstance(self.answers_for, str)
                               else self.answers_for)
        except ValueError as exc:
            raise ValueError(f"card {self.id} answers_for: {exc}") from None
        # A role alias is its own exact lower-case spelling, and a seed kind names
        # the alias of the population that emits it. Every other scope keeps the
        # emitted kind's exact name: ``Producer`` is a kind the registry would
        # refuse, never the producer population under a different capitalisation.
        if scope not in (*ROLES, "all"):
            scope = CONTRACT_ROLES.get(scope, scope)
        object.__setattr__(self, "answers_for", scope)

    def validate_answers_for(self, registered_kinds: frozenset[str]) -> None:
        """Admission refuses an accountability scope absent from the public kind catalogue."""
        if self.answers_for not in (*ROLES, "all") and self.answers_for not in registered_kinds:
            raise ValueError(f"card {self.id} answers_for: unregistered emitted kind")


@dataclass(frozen=True)
class Charter:
    """Each edition retains its exact norms and checked cards."""

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
            duplicate = next(card_id for card_id in ids if ids.count(card_id) > 1)
            raise ValueError(f"card {duplicate} id: metric card ids must be unique")
        for c in self.cards:
            if c.norm not in self.norms:
                raise ValueError(f"card {c.id} references an unknown norm")

    def render(self, prices: dict[str, float] | None = None) -> str:
        """Expose the full charter, including each window, role and current supplied price."""
        lines = [f"CHARTER (edition {self.edition})", "", "NORMS"]
        lines += [f"- {n}" for n in self.norms]
        lines += ["", "METRIC CARDS"]
        for c in self.cards:
            lines += [
                f"- {c.id} (norm: {c.norm})",
                f"  {c.description}",
                f"  units: {c.units}; window: {c.window}; acceptable: {c.acceptable_region}",
                f"  observation: {c.observation}; answers_for: {c.answers_for}",
                f"  lambda: {prices.get(c.id, 0.0) if prices is not None else 'unassigned'}",
            ]
        return "\n".join(lines)


SEED_NORMS = (
    "truthful commitments",
    "care with scarce resources",
    "useful inquiry",
    "the capacity to revise inadequate practices",
)


def seed_charter() -> Charter:
    """The seed charter: four norms, three cards, edition 1."""
    return Charter(
        edition=1,
        norms=SEED_NORMS,
        cards=(
            MetricCard(
                id="cost_per_return",
                norm="care with scarce resources",
                description="Wallet cost of producing one well-formed return.",
                units="micro-USD per return",
                window=MetricWindow("returns", 100, "role"),
                acceptable_region="below the median of the previous window",
                observation="cost_per_return",
                answers_for="producer",
            ),
            MetricCard(
                id="well_formed_rate",
                norm="truthful commitments",
                description="Share of returns that satisfy their declared outcome schema.",
                units="fraction",
                window=MetricWindow("returns", 100, "role"),
                acceptable_region="at least 0.9",
                observation="well_formed_rate",
                answers_for="all",
            ),
            MetricCard(
                id="forecast_skill",
                norm="useful inquiry",
                description="Mean Brier score of settled forecasts minus the prevalence baseline.",
                units="score difference in [-1, 1]",
                window=MetricWindow("forecasts", 50, "assembly"),
                acceptable_region="above zero",
                observation="forecast_skill",
                answers_for="evaluator",
            ),
        ),
    )
