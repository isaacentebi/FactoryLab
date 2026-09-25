"""The seed charter, as a test fixture (charter audit S3).

It used to be the kernel's default for any world without a ``[charter]`` table; the
worlds that relied on it now carry the same text themselves. Tests that need a small
charter object without loading a world build it here.
"""

from dataclasses import asdict

from factorylab.charter.charter import Charter, MetricCard
from factorylab.charter.windows import MetricWindow

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


def seed_charter_table() -> dict:
    """The seed charter as a manifest ``[charter]`` table."""
    charter = asdict(seed_charter())
    return {"edition": 1, "norms": list(charter["norms"]), "cards": list(charter["cards"])}
