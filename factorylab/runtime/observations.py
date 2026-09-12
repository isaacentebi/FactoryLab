"""Immutable catalogue of supported observations; absent evidence never becomes a score."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from statistics import fmean, pstdev
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from factorylab.runtime.loop import MeasureWindow


@dataclass(frozen=True)
class Observation:
    """A stable public identity binds units and meaning to a pure window calculation."""

    id: str
    description: str
    units: str
    measure: Callable[[MeasureWindow], float | None]


def _ratio(numerator: int, denominator: int) -> float | None:
    return numerator / denominator if denominator > 0 else None


def _mean(values: list[float] | list[int]) -> float | None:
    return sum(values) / len(values) if values else None


def _disagreement(w: MeasureWindow) -> float | None:
    groups = [
        pstdev([fmean(scores) for scores in judges.values()])
        for judges in w.verdicts.values() if len(judges) > 1
    ]
    return _mean(groups)


def _verdicts(w: MeasureWindow) -> list[float]:
    return [v for judges in w.verdicts.values() for scores in judges.values() for v in scores]


def _verdict_std(w: MeasureWindow) -> float | None:
    values = _verdicts(w)
    return pstdev(values) if values else None


CATALOGUE: tuple[Observation, ...] = (
    Observation(
        "cost_per_return",
        "Mean cost of well-formed producer returns.",
        "micro-USD per return",
        lambda w: _mean(w.costs),
    ),
    Observation(
        "well_formed_rate",
        "Well-formed returns over runtime invocations (excluding votes).",
        "fraction",
        lambda w: _ratio(w.ok, w.invocations),
    ),
    Observation(
        "forecast_skill",
        "Mean cumulative consequence skill of evaluators with settlements.",
        "score difference",
        lambda w: _mean(w.forecast_skills),
    ),
    Observation(
        "turnover",
        "Filled notional over equity at window start; zero without fills.",
        "ratio",
        lambda w: 0.0 if not w.notional_micro else _ratio(w.notional_micro, w.equity_start_micro),
    ),
    Observation(
        "noop_share",
        "Producer returns with action noop or hold over all producer returns.",
        "fraction",
        lambda w: _ratio(w.noop_returns, w.producer_returns),
    ),
    Observation(
        "revision_rate",
        "Producer returns carrying registrations or tool calls, counted once.",
        "fraction",
        lambda w: _ratio(w.revision_returns, w.producer_returns),
    ),
    Observation(
        "registrations",
        "Accepted population registrations, including amendment proposals.",
        "count",
        lambda w: float(w.registrations),
    ),
    Observation(
        "registration_rejections",
        "Rejected population registration proposals.",
        "count",
        lambda w: float(w.registration_rejections),
    ),
    Observation(
        "amendments_proposed",
        "Amendments admitted to the charter proposal book.",
        "count",
        lambda w: float(w.amendments_proposed),
    ),
    Observation(
        "amendments_activated",
        "Amendments activated in this window.",
        "count",
        lambda w: float(w.amendments_activated),
    ),
    Observation(
        "verdict_mean",
        "Mean raw evaluator verdict delivered in the window.",
        "score",
        lambda w: _mean(_verdicts(w)),
    ),
    Observation(
        "verdict_std",
        "Population standard deviation of delivered raw evaluator verdicts.",
        "score standard deviation",
        _verdict_std,
    ),
    Observation(
        "evaluator_disagreement",
        "Mean population std across judges of the same return; "
        "at least two judges in this window, averaging repeats per judge.",
        "score standard deviation",
        _disagreement,
    ),
    Observation(
        "consequence_paid_off_rate",
        "Positive outcomes over settled return consequences.",
        "fraction",
        lambda w: _ratio(w.consequences_paid_off, w.consequences_settled),
    ),
    Observation("fills", "Venue fills processed in the window.", "count", lambda w: float(w.fills)),
    Observation(
        "realized_pnl_usd",
        "Realized fill P&L before fees and funding.",
        "USD",
        lambda w: w.realized_pnl_micro / 1_000_000,
    ),
    Observation(
        "position_concentration",
        "Peak observed absolute marked notional on one coin over "
        "starting equity; unsupported without positions or marks.",
        "ratio",
        lambda w: (
            None
            if w.max_position_notional_micro is None
            else _ratio(w.max_position_notional_micro, w.equity_start_micro)
        ),
    ),
    Observation(
        "exposure_win_rate",
        "Winning antagonist exposure settlements over settled exposures.",
        "fraction",
        lambda w: _ratio(w.exposures_won, w.exposures_settled),
    ),
    Observation(
        "meta_verdict_mean",
        "Mean raw meta verdict delivered, across all tiers.",
        "score",
        lambda w: _mean(w.meta_verdicts),
    ),
    Observation(
        "censored_share",
        "Censored judgements and forecasts over their settled or censored "
        "outcomes, including exposures; excludes inapplicable and timeout penalties.",
        "fraction",
        lambda w: _ratio(w.censored, w.outcomes),
    ),
    Observation(
        "tool_calls",
        "Attempted tool calls, including failures; excludes ignored calls.",
        "count",
        lambda w: float(w.tool_calls),
    ),
    Observation(
        "market_purchases",
        "Paid x402 requests with a recorded result; excludes free or unresolved requests.",
        "count",
        lambda w: float(w.market_purchases),
    ),
)


def observation_for(name: str) -> Observation | None:
    """Only a complete catalogue id matches, ignoring surrounding whitespace and case."""
    key = name.strip().lower()
    return next((observation for observation in CATALOGUE if observation.id == key), None)


def catalogue() -> list[dict[str, str]]:
    """Return independent public metadata without exposing implementation callables."""
    return [{"id": o.id, "description": o.description, "units": o.units} for o in CATALOGUE]
