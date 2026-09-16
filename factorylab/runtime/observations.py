"""Supported observations; absent evidence never becomes a score.

The twenty-four seed observations below are the factory's starting measurement
vocabulary. They are not the whole of it: the population may also register
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

# The per-decision attribution the runtime keeps on the same window object
# is not a public window fact and never reaches a registered observation.
PRIVATE_WINDOW_FIELDS = ("decisions", "closed_values", "closed_regions", "closed_shares",
                         "closed_cards", "closed_prices", "series_discarded")
MAX_WORLD_SAMPLES = 1024
# Fields holding a public quantity filed under a private identity: a decision
# handle, an evaluator's assembly id. The quantity is disclosed, the identity is
# not, so these are rebuilt by hand rather than copied through.
ANONYMISED_WINDOW_FIELDS = ("verdicts", "revision_handles")
# Facts fixed when the window opens rather than accumulated inside it: a
# since-a-forecast view of the window carries them unchanged.
WINDOW_IDENTITY_FACTS = ("index", "equity_start_micro")


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
        """Return the declared unit width, the fallback scale for zero-bound cards."""
        return self.unit_range[1] - self.unit_range[0]

    @property
    def registered(self) -> bool:
        """A registered observation is measured by population code, not by the kernel."""
        return self.code is not None


def _ratio(numerator: int, denominator: int | None) -> float | None:
    """An unknown denominator leaves the ratio unmeasured rather than inventing one.

    A window whose venue equity could not be read opens with ``None``, not with
    the compute wallet's balance: every ratio against it is honestly unmeasured.
    """
    return numerator / denominator if denominator is not None and denominator > 0 else None


def _mean(values: list[float] | list[int]) -> float | None:
    return sum(values) / len(values) if values else None


def _cost_per_return(w: MeasureWindow) -> float | None:
    """Mean cost of the window's well-formed producer returns, rent included.

    Retained-storage rent is cost the window spent without a return to carry
    it, so it is added to what those returns cost and never counted as one of
    them: a window paying rent measures a higher cost per return, not a lower
    one. Rent alone is therefore unmeasurable — a window with no well-formed
    producer return has no per-return cost, however much storage it paid for.
    """
    costs = w.costs
    if not costs:
        return None
    return (sum(costs) + getattr(w, "storage_cost_micro", 0)) / len(costs)


def _cost_per_attempt(w: MeasureWindow) -> float | None:
    """Mean cost of every invocation the window made, failed ones and rent included.

    A tolerated failure is compute the window spent: nine cheap successes and
    one expensive failure cost what all ten cost, not what the nine did. The
    live window's per-decision costs already carry retained-storage rent, so
    it is in the numerator and never a divisor. A closed record keeps no
    attribution, so it is measured from the window's return samples instead
    (``charter.measurement``), and a window with no invocation has no cost
    per attempt.
    """
    decisions = getattr(w, "decisions", None)
    if not decisions or not w.invocations:
        return None
    return sum(d["cost"] for d in decisions.values()) / w.invocations


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
        "Mean cost of well-formed producer returns, including retained-storage rent.",
        "micro-USD per return",
        _cost_per_return,
        (0.0, 1_000_000.0),
    ),
    Observation(
        "cost_per_attempt",
        "Mean cost of every selected return, failed ones included, plus retained-storage rent.",
        "micro-USD per attempt",
        _cost_per_attempt,
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
        "avoidably_unresolved_share",
        "Attributable, avoidably unresolved accepted commitments over the eligible "
        "commitments due in the responsible scope. Excludes commitments not yet due, "
        "documented external unobservability without owner fault, and events the seat "
        "never committed to observe; no eligible sample is unmeasured, never zero. It "
        "is measured over a scope's due commitments, so a raw window count cannot "
        "stand in for it.",
        "fraction",
        lambda w: None,
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
        "Mean attempted tool calls per return, including failures; excludes ignored calls.",
        "count per return",
        lambda w: _ratio(w.tool_calls, w.invocations),
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
    """Return the public per-window facts as JSON, carrying no identity of any kind.

    Sets become sorted lists and the per-decision attribution fields are dropped.
    The two fields that index a real quantity *by identity* are republished with
    the identity removed rather than kept: ``verdicts`` becomes one unlabelled
    group of per-judge score lists per judged return, and ``revision_handles``
    becomes the count ``revised_decisions``. No assembly sees the whole topology
    (AGENTS.md), and population-authored ``observe(facts)`` code is an assembly:
    it gets the numbers, never the handles, assembly ids, model ids or evaluator
    ids they were filed under.

    World series retain the latest 1024 samples in each window (across coins).
    ``mids`` and ``funding`` map public coin symbols to [timestamp_ns, value]
    pairs; mids are integer micro-USD and funding rates are dimensionless.
    Wallet balances are integer micro-USD sampled at delivered ticks, alongside
    ``tick_timestamps_ns``. Multi-window observations retain at most 1024 latest
    samples per series too. Coin symbols are the only allowed text values.

    Every other public window fact is a
    number (or a list of numbers), so a field that arrives carrying text — now or
    after some later field is added to the window — is withheld rather than
    disclosed, because a name is the one thing an identity can hide in.
    """
    raw = dict(vars(window)) if not isinstance(window, dict) else dict(window)
    facts: dict[str, Any] = {}
    facts["books"] = {}
    for key, value in raw.items():
        if key in PRIVATE_WINDOW_FIELDS or key in ANONYMISED_WINDOW_FIELDS:
            continue
        if key == "books":
            for row in value[-MAX_WORLD_SAMPLES:]:
                facts["books"].setdefault(row["coin"], []).append({
                    "ts_ns": row["ts_ns"], "bids": row["bids"], "asks": row["asks"]})
            continue
        if key in ("mids", "funding"):
            series = {}
            for row in value[-MAX_WORLD_SAMPLES:]:
                series.setdefault(row["coin"], []).append([row["ts_ns"], row["value"]])
            facts[key] = series
            continue
        if key in ("wallet_balance_micro", "tick_timestamps_ns"):
            value = value[-MAX_WORLD_SAMPLES:]
        if key.startswith("_"):
            continue
        plain = _plain(value)
        if _carries_text(plain):
            continue
        facts[key] = plain
    facts["verdicts"] = _anonymous_verdicts(raw.get("verdicts"))
    facts["revised_decisions"] = len(raw.get("revision_handles") or ())
    return facts


# A retained series that no longer reaches back to a sealed mark supplies no evidence.
_DISCARDED = object()


def trim_series(window: Any, key: str, *, per_coin: bool = False) -> None:
    """Bound one public series in place, counting every sample it discards.

    A bounded series keeps only its latest ``MAX_WORLD_SAMPLES`` samples, so the
    position of a retained sample within the window's whole history is its index
    plus the number already dropped from in front of it. That count is what a
    sealed cursor is compared against; it only ever grows, for the life of the
    window. Per-coin series are counted under the coin they are published by.
    """
    series = getattr(window, key)
    excess = len(series) - MAX_WORLD_SAMPLES
    if excess <= 0:
        return
    discarded = window.series_discarded
    for row in series[:excess]:
        path = f"{key}/{row['coin']}" if per_coin else key
        discarded[path] = discarded.get(path, 0) + 1
    del series[:excess]


def series_offsets(window: Any) -> dict:
    """Shape the retained-prefix offsets like the facts the series appear in."""
    offsets: dict[str, Any] = {}
    for path, count in getattr(window, "series_discarded", {}).items():
        key, _, coin = path.partition("/")
        if coin:
            offsets.setdefault(key, {})[coin] = count
        else:
            offsets[key] = count
    return offsets


def window_cursor(window: Any) -> dict:
    """Mark one position in the public window: each counter's value, each series' position.

    Sealed when a forecast is made, so the claim it opened can later be resolved
    over what the window accumulated *after* it and never over what was already
    there. Carries no window content, only how much of it had happened. A series
    position counts every sample the window ever took, including the ones its
    bound has since discarded, so the mark does not slide when the series rolls.
    A path whose samples were all discarded before the mark is marked too, so an
    unmarked path is unambiguously one that stood at zero: anything the window
    later discards of it was discarded after the claim was sealed.
    """
    return _cursor(window_facts(window), series_offsets(window))


def window_facts_since(window: Any, cursor: Mapping | None) -> dict | None:
    """Return the public facts the window accumulated strictly after a sealed cursor.

    Counters arrive as their increase since the mark and series as the samples
    appended after it, so a fact that was already true when the cursor was
    sealed cannot resolve anything sealed against it. ``index`` and
    ``equity_start_micro`` describe the window itself and are fixed when it
    opens, so they pass through unchanged. A cursor from an earlier window (or
    no cursor at all) marks nothing in this one: the window opened after the
    mark, so every sample it holds is already after it — and so is every sample
    it has since discarded, which is read exactly like a path that stood at zero.

    Returns ``None`` when a bounded series has discarded a sample the cursor
    still needs: the interval the claim was sealed over is no longer evidence,
    and absent evidence is never a resolution.
    """
    facts = window_facts(window)
    if not isinstance(cursor, Mapping) or cursor.get("index") != facts.get("index"):
        cursor = {}  # A window opened after the mark stands wholly after it.
    offsets = series_offsets(window)
    result = {}
    for key, value in facts.items():
        if key in WINDOW_IDENTITY_FACTS:
            result[key] = value
            continue
        since = _since(value, cursor.get(key), offsets.get(key))
        if since is _DISCARDED:
            return None
        result[key] = since
    return result


def _cursor(value: Any, offset: Any = None) -> Any:
    """A counter marks its value, a series its position, and anything else nothing."""
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int | float):
        return value
    if isinstance(value, list):
        return len(value) + (offset if type(offset) is int else 0)
    if isinstance(value, dict):
        offsets = offset if isinstance(offset, Mapping) else {}
        marks = {key: _cursor(item, offsets.get(key)) for key, item in value.items()}
        for key, discarded in offsets.items():
            # A path whose samples have all been discarded already is absent from
            # the facts but is still a position in the window. Marking it keeps
            # an unmarked path unambiguous: it stood at zero and has lost nothing.
            if key not in marks and type(discarded) is int:
                marks[key] = discarded
        return marks
    return None


def _since(value: Any, mark: Any, offset: Any = None) -> Any:
    """Subtract a counter's mark, drop a series' retained prefix, and pass the rest through."""
    if isinstance(value, list):
        discarded = offset if type(offset) is int else 0
        if type(mark) is not int:
            # An unmarked path stood at position zero when the cursor was sealed,
            # so every sample this one has discarded was discarded after the mark.
            return _DISCARDED if discarded > 0 else value
        start = mark - discarded
        return value[start:] if start >= 0 else _DISCARDED
    if isinstance(value, dict):
        marks = mark if isinstance(mark, Mapping) else {}
        offsets = offset if isinstance(offset, Mapping) else {}
        result = {}
        for key, item in value.items():
            since = _since(item, marks.get(key), offsets.get(key))
            if since is _DISCARDED:
                return _DISCARDED
            result[key] = since
        for key, discarded in offsets.items():
            # A series the window no longer publishes at all — one coin's samples
            # evicted by another's, including a coin first seen after the mark —
            # hides its discarded tail behind an absence.
            seen = marks.get(key)
            if key not in value and type(discarded) is int and discarded > (
                seen if type(seen) is int else 0
            ):
                return _DISCARDED
        return result
    if isinstance(value, bool) or value is None:
        return value
    if isinstance(value, int | float) and type(mark) in (int, float):
        return value - mark
    return value


def window_fact_names() -> list[str]:
    """The names a registered observation may read, taken from the facts themselves.

    Built by rendering an empty window, so the published vocabulary cannot drift
    from what ``window_facts`` actually discloses.
    """
    from factorylab.runtime.pricing import MeasureWindow

    return sorted(window_facts(MeasureWindow(index=0, equity_start_micro=0)))


def record_venue_facts(window, tool: str, args: dict, result: dict, now_ns: int) -> None:
    """Paid public reads add bounded numeric facts without account or author identities.

    A venue-wide read observes every instrument it names, so the whole batch is
    appended and ``trim_series`` applies the bound. Slicing the batch first would
    drop samples the window took without counting them, and an uncounted
    eviction is exactly what lets a sealed cursor read a moved position as a
    still one.
    """
    from decimal import Decimal

    from factorylab.kernel.money import usd_to_micro

    if result.get("error"):
        return
    try:
        if tool == "venue.order_book":
            row = {"coin": args["coin"], "ts_ns": int(result["ts_ns"])}
            for side in ("bids", "asks"):
                row[side] = [[usd_to_micro(Decimal(str(level["price"])), rounding="nearest"),
                              float(Decimal(str(level["size"])))]
                             for level in result[side][:20]]
                if any(price <= 0 or not math.isfinite(size) or size < 0
                       for price, size in row[side]):
                    return
            window.books.append(row)
            trim_series(window, "books", per_coin=True)
        elif tool in ("venue.funding", "venue.funding_history"):
            key = "funding" if tool == "venue.funding" else "funding_history"
            rows = []
            for item in result[key]:
                rate = float(item["rate"])
                if math.isfinite(rate):
                    rows.append({"coin": item["coin"], "ts_ns": int(item["ts_ns"]),
                                 "value": rate})
            window.funding.extend(rows)
            trim_series(window, "funding", per_coin=True)
        elif tool == "venue.mids":
            rows = [{"coin": coin, "ts_ns": now_ns,
                     "value": usd_to_micro(Decimal(str(value)), rounding="nearest")}
                    for coin, value in result["mids"].items()]
            window.mids.extend(rows)
            trim_series(window, "mids", per_coin=True)
    except (KeyError, TypeError, ValueError, ArithmeticError, OverflowError):
        return  # Malformed or unavailable venue data supplies no measurement.


def _anonymous_verdicts(verdicts: Any) -> list[list[list[float]]]:
    """Verdict scores grouped by judged return and by judge, under no name at all.

    The grouping is what the seed observations compute from — the spread across
    the judges of one return, the spread across all of them — so a registered
    observation can compute the same quantities. Which return and which judge it
    cannot: a position in a list is a position, not a handle and not an id.
    """
    if not isinstance(verdicts, Mapping):
        return []
    return [
        [_plain(list(scores)) for scores in judges.values()]
        for judges in verdicts.values()
        if isinstance(judges, Mapping)
    ]


def _carries_text(value: Any) -> bool:
    """True when a plain value holds a string anywhere, in a key or in a leaf."""
    if isinstance(value, str):
        return True
    if isinstance(value, dict):
        return any(_carries_text(k) or _carries_text(v) for k, v in value.items())
    if isinstance(value, list):
        return any(_carries_text(v) for v in value)
    return False


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
        return None if entry is None or entry.get("retired") else _from_entry(key, entry)

    def all(self) -> list[Observation]:
        """Return every observation the factory can currently make, seeds first."""
        return list(CATALOGUE) + [
            _from_entry(key, entry) for key, entry in sorted(self.registered.items())
            if not entry.get("retired")
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
