"""Supported observations; absent evidence never becomes a score.

The twenty-two seed observations below are the factory's starting measurement
vocabulary. They are not the whole of it: spec A11 lets the population register
its own observation — a pure ``observe(facts) -> float`` over the public
per-window facts, run in the tool jail — and a card may then name it. Seed and
registered observations are read through one :class:`ObservationBook`, so every
consumer (cards, pricing, the immune organ, the world block) sees the same
vocabulary.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from statistics import fmean, pstdev
from types import MappingProxyType
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from factorylab.runtime.pricing import MeasureWindow

# The per-decision attribution the runtime keeps on the same window object (A4)
# is not a public window fact and never reaches a registered observation.
PRIVATE_WINDOW_FIELDS = ("decisions", "closed_values", "closed_regions")


@dataclass(frozen=True)
class Observation:
    """A stable public identity binds units and meaning to a pure window calculation.

    A seed observation carries ``measure``, a native pure function of the window.
    A registered observation carries ``code`` instead: population Python defining
    ``observe(facts)`` over the same window facts as JSON, run in the tool jail.
    """

    id: str
    description: str
    units: str
    measure: Callable[[MeasureWindow], float | None] | None
    unit_range: tuple[float, float]
    code: str | None = None
    version: int = 1
    provenance: str = "seed"

    @property
    def scale(self) -> float:
        """The declared unit interval fixes normalization independently of a card."""
        return self.unit_range[1] - self.unit_range[0]

    @property
    def registered(self) -> bool:
        """A registered observation is measured by population code, not by the kernel."""
        return self.code is not None


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
        (0.0, 1_000_000.0),
    ),
    Observation(
        "well_formed_rate",
        "Well-formed returns over runtime invocations (excluding votes).",
        "fraction",
        lambda w: _ratio(w.ok, w.invocations),
        (0.0, 1.0),
    ),
    Observation(
        "forecast_skill",
        "Mean cumulative consequence skill of evaluators with settlements.",
        "score difference",
        lambda w: _mean(w.forecast_skills),
        (-1.0, 1.0),
    ),
    Observation(
        "turnover",
        "Filled notional over equity at window start; zero without fills.",
        "ratio",
        lambda w: 0.0 if not w.notional_micro else _ratio(w.notional_micro, w.equity_start_micro),
        (0.0, 1.0),
    ),
    Observation(
        "noop_share",
        "Producer returns with action noop or hold over all producer returns.",
        "fraction",
        lambda w: _ratio(w.noop_returns, w.producer_returns),
        (0.0, 1.0),
    ),
    Observation(
        "revision_rate",
        "Producer returns whose registration was accepted, counted once, plus amendments "
        "activated in the window, over producer returns; refused proposals and tool calls "
        "do not count.",
        "fraction",
        lambda w: _ratio(w.revision_returns, w.producer_returns),
        (0.0, 1.0),
    ),
    Observation(
        "registrations",
        "Accepted population registrations, including amendment proposals.",
        "count",
        lambda w: float(w.registrations),
        (0.0, 1.0),
    ),
    Observation(
        "registration_rejections",
        "Rejected population registration proposals.",
        "count",
        lambda w: float(w.registration_rejections),
        (0.0, 1.0),
    ),
    Observation(
        "amendments_proposed",
        "Amendments admitted to the charter proposal book.",
        "count",
        lambda w: float(w.amendments_proposed),
        (0.0, 1.0),
    ),
    Observation(
        "amendments_activated",
        "Amendments activated in this window.",
        "count",
        lambda w: float(w.amendments_activated),
        (0.0, 1.0),
    ),
    Observation(
        "verdict_mean",
        "Mean raw evaluator verdict delivered in the window.",
        "score",
        lambda w: _mean(_verdicts(w)),
        (0.0, 1.0),
    ),
    Observation(
        "verdict_std",
        "Population standard deviation of delivered raw evaluator verdicts.",
        "score standard deviation",
        _verdict_std,
        (0.0, 0.5),
    ),
    Observation(
        "evaluator_disagreement",
        "Mean population std across judges of the same return; "
        "at least two judges in this window, averaging repeats per judge.",
        "score standard deviation",
        _disagreement,
        (0.0, 0.5),
    ),
    Observation(
        "consequence_paid_off_rate",
        "Positive outcomes over settled return consequences.",
        "fraction",
        lambda w: _ratio(w.consequences_paid_off, w.consequences_settled),
        (0.0, 1.0),
    ),
    Observation("fills", "Venue fills processed in the window.", "count",
                lambda w: float(w.fills), (0.0, 1.0)),
    Observation(
        "realized_pnl_usd",
        "Realized fill P&L before fees and funding.",
        "USD",
        lambda w: w.realized_pnl_micro / 1_000_000,
        (-1.0, 1.0),
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
        (0.0, 1.0),
    ),
    Observation(
        "exposure_win_rate",
        "Winning antagonist exposure settlements over settled exposures.",
        "fraction",
        lambda w: _ratio(w.exposures_won, w.exposures_settled),
        (0.0, 1.0),
    ),
    Observation(
        "meta_verdict_mean",
        "Mean raw meta verdict delivered, across all tiers.",
        "score",
        lambda w: _mean(w.meta_verdicts),
        (0.0, 1.0),
    ),
    Observation(
        "censored_share",
        "Censored judgements and forecasts over their settled or censored "
        "outcomes, including exposures; excludes inapplicable and timeout penalties.",
        "fraction",
        lambda w: _ratio(w.censored, w.outcomes),
        (0.0, 1.0),
    ),
    Observation(
        "tool_calls",
        "Attempted tool calls, including failures; excludes ignored calls.",
        "count",
        lambda w: float(w.tool_calls),
        (0.0, 1.0),
    ),
    Observation(
        "market_purchases",
        "Paid x402 requests with a recorded result; excludes free or unresolved requests.",
        "count",
        lambda w: float(w.market_purchases),
        (0.0, 1.0),
    ),
)


SEED_IDS = frozenset(o.id for o in CATALOGUE)
# The only observation state at module scope is this immutable seed vocabulary; a
# book that can be registered into belongs to one runtime and is built per runtime.
SEEDS: Mapping[str, Observation] = MappingProxyType({o.id: o for o in CATALOGUE})
MAX_OBSERVATION_CODE_CHARS = 8000
MAX_OBSERVATION_DESCRIPTION_CHARS = 500
# A registered observation runs under the tool jail's own ceilings (cortex/sandbox.py):
# the longest timeout a population tool may declare, and the same CPU bound.
OBSERVATION_TIMEOUT_S = 5
OBSERVATION_CPU_S = 2


def normalise(name: str) -> str:
    """Observation ids are matched whole, ignoring surrounding whitespace and case."""
    return name.strip().lower()


def window_facts(window: Any) -> dict:
    """Return the public per-window facts as JSON, the same facts the seeds compute from.

    Sets become sorted lists and the per-decision attribution fields are dropped:
    what a registered observation reads is exactly what a card's window discloses.
    """
    raw = dict(vars(window)) if not isinstance(window, dict) else dict(window)
    facts: dict[str, Any] = {}
    for key, value in raw.items():
        if key in PRIVATE_WINDOW_FIELDS or key.startswith("_"):
            continue
        facts[key] = _plain(value)
    return facts


def window_fact_names() -> list[str]:
    """The names a registered observation may read, without building the values."""
    from dataclasses import fields

    from factorylab.runtime.pricing import MeasureWindow

    return sorted(f.name for f in fields(MeasureWindow) if f.name not in PRIVATE_WINDOW_FIELDS)


def _plain(value: Any) -> Any:
    if isinstance(value, set | frozenset):
        return sorted(value)
    if isinstance(value, dict):
        return {str(k): _plain(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [_plain(v) for v in value]
    if isinstance(value, bool) or value is None or isinstance(value, str | int):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    return str(value)


class ObservationBook:
    """The factory's live measurement vocabulary: the seeds plus what was registered.

    ``registered`` maps an id to its public definition (``description``, ``units``,
    ``unit_range``, ``code``, ``version``, ``provenance``); ``run`` executes one
    registered observation's code against a window's facts in the tool jail and
    returns ``(value, error)``. Without a runner a registered observation is
    simply unmeasured, exactly like a quantity without support.

    Guarantees one book owns exactly one registration dict: a book built without
    one starts empty and stays private to its owner, so registrations never cross
    from one runtime to another.
    """

    def __init__(
        self,
        registered: dict[str, dict] | None = None,
        *,
        run: Callable[[str, dict], tuple[float | None, str | None]] | None = None,
        reject: Callable[[Observation, float], None] | None = None,
    ) -> None:
        self.registered = registered if registered is not None else {}
        self._run = run
        self._reject = reject

    def get(self, name: str) -> Observation | None:
        """Return the seed or registered observation a card's prose names."""
        key = normalise(name)
        seed = SEEDS.get(key)
        if seed is not None:
            return seed
        entry = self.registered.get(key)
        return None if entry is None else _from_entry(key, entry)

    def all(self) -> list[Observation]:
        """Return every observation the factory can currently make, seeds first."""
        return list(CATALOGUE) + [
            _from_entry(key, entry) for key, entry in sorted(self.registered.items())
        ]

    def value(self, observation: Observation, window: Any) -> float | None:
        """Measure one window. A registered observation runs its code in the jail.

        Guarantees a returned value lies inside the observation's declared unit
        range: a registered measurement outside it is unsupported for that window,
        reported to the owner of the book, and never clamped into range.
        """
        if not observation.registered:
            return observation.measure(window)
        if self._run is None:
            return None
        value, _error = self._run(observation.code, window_facts(window))
        if value is None:
            return None
        lo, hi = observation.unit_range
        if not lo <= value <= hi:
            if self._reject is not None:
                self._reject(observation, value)
            return None
        return value

    def catalogue(self) -> list[dict]:
        """Return independent public metadata without exposing implementation callables."""
        return [
            {"id": o.id, "description": o.description, "units": o.units,
             "unit_range": list(o.unit_range), "scale": o.scale,
             "provenance": o.provenance, "version": o.version}
            for o in self.all()
        ]


def _from_entry(key: str, entry: dict) -> Observation:
    lo, hi = entry["unit_range"]
    return Observation(
        key, entry["description"], entry["units"], None, (float(lo), float(hi)),
        code=entry["code"], version=int(entry.get("version", 1)),
        provenance=entry.get("provenance", "population"),
    )


def seed_book() -> ObservationBook:
    """Return a fresh book of the seed vocabulary alone, registrable by nobody else.

    Guarantees every caller that did not supply a book gets its own: there is no
    shared book to register into, so one runtime's measurements cannot appear in
    another's vocabulary.
    """
    return ObservationBook()


def observation_for(name: str) -> Observation | None:
    """Only a complete seed id matches, ignoring surrounding whitespace and case."""
    return SEEDS.get(normalise(name))


def catalogue() -> list[dict]:
    """Return independent public metadata without exposing implementation callables."""
    return seed_book().catalogue()
