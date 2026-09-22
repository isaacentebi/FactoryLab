"""Exact rolling card measurements shared by pricing, admission and policy liability."""

from __future__ import annotations

from collections import defaultdict
from copy import deepcopy
from dataclasses import asdict, dataclass, field
from statistics import fmean, median, pstdev
from types import SimpleNamespace

from factorylab.charter.charter import MetricCard

RETURN_OBSERVATIONS = frozenset({
    "cost_per_return", "cost_per_attempt", "well_formed_rate", "noop_share", "revision_rate",
    "tool_calls",
})
# The two cost selections: per successful response, and per attempt (failed
# responses included). Both add retained-storage rent to what the selected
# responses cost and never count a charge as one of them.
COST_OBSERVATIONS = frozenset({"cost_per_return", "cost_per_attempt"})
# The runtime keeps per-decision attribution on the same window object;
# measurement never observes it.
ATTRIBUTION_FIELDS = ("decisions", "closed_values", "closed_regions", "closed_cards",
                      "closed_prices", "closed_scopes", "closed_holdouts", "series_discarded")
FORECAST_OBSERVATIONS = frozenset({
    "forecast_skill", "verdict_mean", "verdict_std", "consequence_paid_off_rate", "censored_share",
    "avoidably_unresolved_share",
})


def measurement_catalogue(observations=None) -> list[dict]:
    """Public card metadata states selector semantics separately from raw window diagnostics.

    A population-registered observation appears here beside the seeds, with
    its declared units and range, so a card can name it the same way.
    """
    from factorylab.runtime.observations import seed_book

    descriptions = {
        "cost_per_return": "Mean successful response cost in the selected rows; global closed "
        "windows use successful producer returns. A retained-storage charge adds to what those "
        "responses cost and is never counted as one of them.",
        "cost_per_attempt": "Mean cost over every selected response, failed ones included; "
        "global closed windows use every return the window made. A retained-storage charge "
        "adds to what those responses cost and is never counted as one of them.",
        "well_formed_rate": "Successful responses over selected invocation responses, "
        "including ballots.",
        "tool_calls": "Mean attempted tool calls per selected response, failures included; "
        "global closed windows divide the window's attempted calls by its invocations.",
        "forecast_skill": "Mean selected forecast Brier minus its paired pre-outcome "
        "prevalence-baseline Brier.",
        "noop_share": "Share of selected responses declaring noop or hold; global closed "
        "windows use producer returns.",
        "revision_rate": "Share of selected responses with accepted registrations or activated "
        "amendments; global closed windows use their revision counters.",
        "verdict_mean": "Mean evaluator verdict; forecast selectors use the verdicts attached "
        "to resolved forecasts, grouped by the judged return's assembly or role.",
        "verdict_std": "Population standard deviation of evaluator verdicts; forecast "
        "selectors group verdicts by the judged return's assembly or role.",
        "consequence_paid_off_rate": "Positive return_paid_off outcomes over selected settled "
        "consequences; forecast selectors restrict this to selected forecast records.",
        "censored_share": "Censored outcomes over resolved outcomes; forecast selectors use "
        "forecast records, global closed windows also include judgements and exposures.",
        "avoidably_unresolved_share": "Attributable, avoidably unresolved accepted commitments "
        "over the eligible commitments due in the responsible scope. A commitment not yet due "
        "is not in the sample; one the owner documented as externally unobservable without its "
        "own fault, and an event the seat never committed to observe, are excluded. No eligible "
        "sample is unmeasured, never zero.",
    }
    result = (observations or seed_book()).catalogue()
    for row in result:
        observation = row["id"]
        row["description"] = descriptions.get(observation, row["description"])
        row["window_kinds"] = ["windows"]
        if observation in RETURN_OBSERVATIONS:
            row["window_kinds"].append("returns")
        if observation in FORECAST_OBSERVATIONS:
            row["window_kinds"].append("forecasts")
        # Charter audit C3: a registered observation runs per role or per assembly on
        # that scope's share of the window facts, so it groups like the seeds do.
        row["groupable"] = (observation in RETURN_OBSERVATIONS | FORECAST_OBSERVATIONS
                            or row.get("provenance") not in (None, "seed"))
    return result


#: Window counters a scope's own samples cannot attribute: a scope's share of them
#: is published as null, never as the window's whole count.
UNSCOPED_COUNTERS = ("notional_micro", "fills", "realized_pnl_micro",
                     "max_position_notional_micro", "exposures_settled", "exposures_won",
                     "meta_verdicts", "registrations", "registration_rejections",
                     "amendments_proposed", "amendments_activated", "market_purchases")
#: Observations whose value is not a mean of its samples: no interval states their error.
NOT_A_MEAN = frozenset({"verdict_std", "evaluator_disagreement"})


def scope_facts(windows: list[dict], returns: list[dict], forecasts: list[dict]) -> dict:
    """One scope's share of the selected closed windows, as anonymous public facts.

    Charter audit C3. The kernel partitions and the population's code measures:
    the caller has already selected the rows of one role or one assembly, and
    this builds the facts ``window_facts`` would publish for a window holding
    only those rows. The world's own series (mids, funding, books, wallet
    balances, tick times) are facts about the world, not the scope, and pass
    through whole. The counters are the scope's own: its responses
    (``invocations``, ``ok``, ``costs`` of its well-formed responses, ``tool_calls``,
    ``noop_returns``, ``revision_returns``, ``producer_returns`` as its response
    count, ``storage_cost_micro``, ``compute_spend_micro``), the verdicts its
    responses gave, and its own settled forecasts (``forecast_skills``,
    ``outcomes``, ``censored``, ``consequences_settled``, ``consequences_paid_off``).
    A counter no row attributes (``UNSCOPED_COUNTERS``) is null. The result
    passes through ``window_facts``, so no handle, assembly id or role name
    survives into it: the scope is the kernel's to know.
    """
    from factorylab.runtime.observations import window_facts

    merged: dict = {}
    for record in windows:
        for key in ("mids", "funding", "books", "wallet_balance_micro", "tick_timestamps_ns"):
            merged.setdefault(key, []).extend(deepcopy(record.get(key) or []))
    merged["index"] = windows[-1]["index"] if windows else 0
    merged["equity_start_micro"] = windows[0].get("equity_start_micro") if windows else None
    responses = [row for row in returns if not row.get("storage")]
    storage = [row for row in returns if row.get("storage")]
    settled = [row for row in forecasts if row.get("predicate") == "return_paid_off"
               and row.get("status") == "settled"]
    merged.update({
        "invocations": len(responses),
        "ok": sum(bool(row["ok"]) for row in responses),
        "costs": [row["cost"] for row in responses if row["ok"]],
        "tool_calls": sum(row["tool_calls"] for row in responses),
        "producer_returns": len(responses),
        "noop_returns": sum(bool(row["noop"]) for row in responses),
        "revision_returns": sum(bool(row["revision"]) for row in responses),
        "revision_handles": {row["handle"] for row in responses if row["revision"]},
        "storage_cost_micro": sum(row["cost"] for row in storage),
        "compute_spend_micro": sum(row["cost"] for row in returns),
        "verdicts": {row["handle"]: {"judge": [row["verdict"]]} for row in responses
                     if row.get("verdict") is not None},
        "forecast_skills": [row["skill"] for row in forecasts if row.get("skill") is not None],
        "outcomes": len(forecasts),
        "censored": sum(row.get("status") == "censored" for row in forecasts),
        "consequences_settled": len(settled),
        "consequences_paid_off": sum(row.get("y") == 1 for row in settled),
        **{key: None for key in UNSCOPED_COUNTERS},
    })
    return window_facts(merged)


def _sample_values(observation: str, rows: list[dict]) -> list[float] | None:
    """The per-sample quantities whose mean a row-measured observation is, or None."""
    observation = observation.strip().lower()
    if observation in NOT_A_MEAN:
        return None
    if observation in COST_OBSERVATIONS:
        return [float(row["cost"]) for row in _cost_responses(observation, rows)]
    if observation in ("well_formed_rate", "noop_share", "revision_rate"):
        key = {"well_formed_rate": "ok", "noop_share": "noop", "revision_rate": "revision"}[
            observation]
        return [float(bool(row[key])) for row in rows if not row.get("storage")]
    if observation == "tool_calls":
        return [float(row["tool_calls"]) for row in rows if not row.get("storage")]
    if observation == "censored_share":
        return [float(row["status"] == "censored") for row in rows]
    if observation == "avoidably_unresolved_share":
        return [float(row["status"] == "censored") for row in rows
                if row.get("excluded") is None]
    if observation == "consequence_paid_off_rate":
        return [float(row["y"]) for row in rows if row["predicate"] == "return_paid_off"
                and row["status"] == "settled"]
    if observation == "forecast_skill":
        return [float(row["skill"]) for row in rows if row["skill"] is not None]
    return [float(row["verdict"]) for row in rows if row.get("verdict") is not None]


@dataclass
class CardSamples:
    """Private samples preserve order, ownership and window membership across resume."""

    returns: list[dict] = field(default_factory=list)
    forecasts: list[dict] = field(default_factory=list)
    windows: list[dict] = field(default_factory=list)
    values: dict[str, float] = field(default_factory=dict)
    scopes: dict[str, dict[str, float]] = field(default_factory=dict)
    medians: dict[str, float] = field(default_factory=dict)
    # The violation each card's failed holdouts added at the last close (M3).
    holdouts: dict[str, float] = field(default_factory=dict)

    def returned(self, *, handle: str, assembly: str, role: str, window: int, ret) -> None:
        """One completed invocation, including its continuation costs, is one return sample."""
        self.returns.append({
            "handle": handle, "assembly": assembly, "role": role, "window": window,
            "cost": ret.cost, "ok": ret.status == "ok",
            "noop": str(ret.outputs.get("action", "")).lower() in ("noop", "hold"),
            "revision": False, "tool_calls": len(ret.tool_calls),
            "verdict": ret.outputs.get("verdict"),
        })

    def stored(self, *, handle: str, assembly: str | None, role: str, window: int,
               cost: int) -> None:
        """One metered retained-storage charge is a cost sample of the decision that holds it.

        The cost cards and their penalty shares are measured from these rows, so
        a charge that falls due in a window its decision never responded in is
        still measured there: it joins that decision's own row when it has one
        in the window, and otherwise enters as its own successful cost row. It
        is a cost and not a response, so a selection that counts responses drops
        it before its horizon is applied and it never occupies a response slot,
        and a cost selection adds it to what the selected responses cost instead
        of dividing that total by it.
        """
        for sample in reversed(self.returns):
            if sample["handle"] == handle and sample["window"] == window:
                sample["cost"] += cost
                return
        self.returns.append({
            "handle": handle, "assembly": assembly, "role": role, "window": window,
            "cost": cost, "ok": True, "noop": False, "revision": False, "tool_calls": 0,
            "verdict": None, "storage": True,
        })

    def revised(self, handle: str) -> None:
        """Accepted registrations mark their own return, including pre-continuation proposals."""
        for sample in reversed(self.returns):
            if sample["handle"] == handle and not sample.get("storage"):
                sample["revision"] = True
                return

    def resolved_forecast(
        self, *, forecast, role: str, window: int, skill: float | None,
        y: int | None, status: str, source: dict, subject: dict,
        excluded: str | None = None,
    ) -> None:
        """Resolved rows retain separate forecaster and judged-return identities.

        ``excluded`` names the documented reason this due commitment is not an
        eligible sample for accountable resolution (C3): external unobservability
        the owner is not at fault for. It is a reason, never a silent zero.
        """
        self.forecasts.append({
            "handle": forecast.handle, "assembly": forecast.evaluator_id, "role": role,
            "subject_handle": forecast.about_handle,
            "subject_assembly": subject.get("assembly"), "subject_role": subject.get("role"),
            "window": window, "skill": skill, "predicate": forecast.predicate_id,
            "y": y, "status": status, "verdict": source.get("verdict"),
            "excluded": excluded,
        })

    def closed(self, window) -> None:
        """A closed window enters the record once, detached from the runtime's counters."""
        if not self.windows or self.windows[-1]["index"] != window.index:
            record = deepcopy(asdict(window))
            for key in ATTRIBUTION_FIELDS:
                # Per-decision attribution is not a window observation.
                record.pop(key, None)
            self.windows.append(record)

    def prune(self, cards, *, pending_handles=frozenset()) -> None:
        """Retain only the sample horizons still required by cards or outstanding policy votes."""
        cards = tuple(cards)
        windows_n = max((c.window.n for c in cards if c.window.kind == "windows"), default=1)
        self.windows[:] = self.windows[-windows_n:] if windows_n else []
        first_window = self.windows[0]["index"] if self.windows else None
        for kind in ("returns", "forecasts"):
            rows = getattr(self, kind)
            keep = set()
            for card in cards:
                if card.window.kind != kind:
                    continue
                for group in _groups(card, _selected(card.observation, rows)).values():
                    # Retain the horizon measurement would select, including a
                    # partly filled one: a charge never evicts a response from
                    # it, and a charge outside its window span is not kept for
                    # a horizon that will never read it.
                    keep.update(id(row) for row in _horizon(
                        card.observation, group, card.window.n, partial=True))
            rows[:] = [row for row in rows if (
                row["handle"] in pending_handles or id(row) in keep
                or (first_window is not None and row["window"] >= first_window)
            )]


def record_card_forecasts(runtime, pending, baseline) -> None:
    """Settled forecast events retain the actual forecaster and judged contract scopes."""
    from factorylab.cortex.registration import measured_role
    from factorylab.kernel.events import EventKind

    samples = runtime.card_samples
    returns = {row["handle"]: row for row in samples.returns if not row.get("storage")}
    for event in runtime.internal:
        if event.kind is not EventKind.FORECAST_SETTLED:
            continue
        row = event.payload
        forecast = pending.get(row["handle"])
        if forecast is None:
            continue
        skill = None
        if row["brier"] is not None:
            skill = row["brier"] - baseline.baseline_brier(row["predicate"], row["y"])
            baseline.record(row["predicate"], row["y"])
        parent = runtime.queue.get(forecast.handle).parent_handle
        source = returns.get(parent, {})
        assembly = forecast.evaluator_id
        role = source.get("role") or (
            measured_role(runtime.return_kinds.get(parent)
                          or runtime.assemblies[assembly].spec.emits)
            if assembly in runtime.assemblies else "evaluator"
        )
        subject = returns.get(forecast.about_handle)
        if subject is None:
            # A subject can leave the rolling return horizon before its forecast
            # resolves. Its selected contract and assembly remain runtime facts.
            subject_assembly = runtime.handle_to_assembly.get(forecast.about_handle)
            kind = runtime.return_kinds.get(forecast.about_handle)
            subject = {"assembly": subject_assembly,
                       "role": measured_role(kind) if kind is not None else None}
        samples.resolved_forecast(
            forecast=forecast, role=role, window=runtime.window.index, skill=skill,
            y=row["y"], status=row["status"], source=source, subject=subject,
            excluded=row.get("excluded") or _excluded(runtime, row["handle"]),
        )


def _excluded(runtime, handle: str) -> str | None:
    """The settler's documented exclusion for this settlement, if it recorded one."""
    settler = getattr(runtime, "settler", None)
    return settler.excluded(handle) if settler is not None else None


def preflight_card(card: MetricCard, observations=None) -> None:
    """Unmeasurable observations, scopes and regions are rejected before a vote."""
    from factorylab.runtime.cards import parses, region_for
    from factorylab.runtime.observations import seed_book

    book = observations or seed_book()
    observation = book.get(card.observation)
    if observation is None:
        raise ValueError(f"card {card.id} observation: unregistered observation")
    if not parses(card):
        raise ValueError(f"card {card.id} acceptable_region: no finite usable bounds")
    region_for(card, rolling={f"{card.id}_prev_median": 1.0}, observations=book)
    kind = card.window.kind
    supported = RETURN_OBSERVATIONS if kind == "returns" else FORECAST_OBSERVATIONS
    if kind != "windows" and observation.id not in supported:
        raise ValueError(f"card {card.id} window: {observation.id} cannot be measured over {kind}")
    if kind == "windows" and card.window.per is not None and observation.id not in (
        RETURN_OBSERVATIONS | FORECAST_OBSERVATIONS
    ) and not observation.registered:
        raise ValueError(f"card {card.id} window: {observation.id} has no role/assembly samples")
    if card.window.interval is not None and observation.id in NOT_A_MEAN:
        raise ValueError(f"card {card.id} window: {observation.id} is not a mean, so no "
                         "interval states its error")


def preflight_measurement(card: MetricCard, observations=None, *,
                          registered_kinds: frozenset[str] = frozenset()) -> None:
    """Execute the pricing measurement with one synthetic unit of the proposed selector."""
    from dataclasses import replace

    from factorylab.cortex.registration import measured_role
    from factorylab.cortex.request import Return
    from factorylab.runtime.pricing import MeasureWindow
    from factorylab.settlement.forecast import Forecast

    card.validate_answers_for(registered_kinds)
    preflight_card(card, observations)
    unit = replace(card, window=replace(card.window, n=1))
    samples = CardSamples()
    kinds = ("ProducerReturn", "Verdict", "MetaVerdict", "Exposure")
    if card.answers_for not in ("producer", "evaluator", "meta", "antagonist", "all"):
        kinds += (card.answers_for,)
    for kind in kinds:
        role = measured_role(kind)
        samples.returned(handle=kind, assembly=kind, role=role, window=1,
                         ret=Return(kind, {"verdict": 0.0} if kind == "Verdict" else {}, 1, "ok"))
    # Use the real row constructor, with judge and subject separated. The card
    # cannot manufacture either identity by assigning its answers_for to a row.
    for source in samples.returns:
        for subject in samples.returns:
            forecast = Forecast(
                f"forecast-{source['handle']}-{subject['handle']}", source["assembly"],
                subject["handle"], "return_paid_off", {"horizon_events": 1}, 0.5, 0, 1,
            )
            samples.resolved_forecast(
                forecast=forecast, role=source["role"], window=1, skill=0.0,
                y=0, status="settled", source=source, subject=subject,
            )
    window = MeasureWindow(1, 1, costs=[1], invocations=1, ok=1, producer_returns=1,
                           consequences_settled=1, exposures_settled=1, outcomes=1,
                           meta_verdicts=[0.0], max_position_notional_micro=0,
                           verdicts={"sample": {"a": [0.0], "b": [0.0]}})
    if unit.id not in measure_cards((unit,), samples, window, observations=observations):
        raise ValueError(f"card {card.id} window: measurement preflight produced no value")


def _selected(observation: str, rows: list[dict]) -> list[dict]:
    """Drop retained-storage charges from every selection but a cost one.

    Only cost is measured over a charge: it is money spent, not a response, so
    it neither answers a schema nor declares an action. Every other observation
    loses it here, before any grouping or horizon; a cost selection keeps it for
    `_horizon`, which admits it as mass and never as a slot.
    """
    if observation.strip().lower() in COST_OBSERVATIONS:
        return rows
    return [row for row in rows if not row.get("storage")]


def _horizon(observation: str, group: list[dict], n: int, *,
             partial: bool = False) -> list[dict] | None:
    """Select the latest `n` responses, then re-admit the rent those responses cover.

    The horizon is chosen over responses alone: a retained-storage charge is a
    cost and not a response, so it never occupies one of the `n` slots, never
    pushes a real response out of a full horizon, and never counts toward the
    support a scope needs. The charges that belong to a selected horizon are the
    ones metered in the same measurement windows as its selected responses —
    the same closed span a windows selector reads its rows over — so rent paid
    while those responses were being measured adds to what they cost and rent
    from outside their span does not. `partial` keeps a horizon that has not
    filled yet, which retention needs and measurement refuses.
    """
    responses = [row for row in group if not row.get("storage")]
    if observation.strip().lower() == "avoidably_unresolved_share":
        # The horizon is the last `n` *eligible* due commitments: a documented
        # exclusion never occupies a slot, so external unobservability cannot
        # push an accountable commitment out of the sample it answers for.
        responses = [row for row in responses if row.get("excluded") is None]
    if len(responses) < n and not partial:
        return None
    responses = responses[-n:]
    keep = {id(row) for row in responses}
    if responses and observation.strip().lower() in COST_OBSERVATIONS:
        first, last = responses[0]["window"], responses[-1]["window"]
        keep.update(id(row) for row in group
                    if row.get("storage") and first <= row["window"] <= last)
    return [row for row in group if id(row) in keep]


def _groups(card: MetricCard, rows: list[dict]) -> dict[str, list[dict]]:
    groups = defaultdict(list)
    subject = card.observation.strip().lower() in ("verdict_mean", "verdict_std")
    for row in rows:
        if subject and row.get("verdict") is None:
            continue
        prefix = "subject_" if subject else ""
        if card.window.per is not None and card.answers_for != "all" and (
            row.get(prefix + "role") != card.answers_for
        ):
            continue
        key = row.get(prefix + card.window.per) if card.window.per is not None else "all"
        if key is None:
            continue
        groups[key].append(row)
    return dict(groups)


def _cost_responses(observation: str, rows: list[dict]) -> list[dict]:
    """The responses a cost selection divides over: successful ones per return, all per attempt."""
    if observation == "cost_per_return":
        return [row for row in rows if row["ok"] and not row.get("storage")]
    return [row for row in rows if not row.get("storage")]


def _measure_rows(observation: str, rows: list[dict]) -> float | None:
    if not rows:
        return None
    if observation in COST_OBSERVATIONS:
        # A retained-storage charge is cost without a response: it is added to
        # what the selected responses cost and never divided into as one of
        # them, so paying rent can only raise a cost per response. Per attempt,
        # a failed response is one of the responses and its cost is spent.
        values = [row["cost"] for row in _cost_responses(observation, rows)]
        rent = sum(row["cost"] for row in rows if row["ok"] and row.get("storage"))
        return (sum(values) + rent) / len(values) if values else None
    if observation in ("well_formed_rate", "noop_share", "revision_rate"):
        key = {"well_formed_rate": "ok", "noop_share": "noop", "revision_rate": "revision"}[
            observation
        ]
        return fmean(row[key] for row in rows)
    if observation == "tool_calls":
        # The card's unit is calls per return: ten responses of one call each
        # measure one, not ten.
        return fmean(row["tool_calls"] for row in rows)
    if observation == "censored_share":
        return fmean(row["status"] == "censored" for row in rows)
    if observation == "avoidably_unresolved_share":
        # Eligible: a commitment this scope accepted and that came due, minus the
        # exclusions. A commitment still open has no row here at all, so a promise
        # whose horizon has not arrived can never be counted against its owner.
        eligible = [row for row in rows if row.get("excluded") is None]
        if not eligible:
            return None  # unmeasured; a scope with no due commitment is not compliant
        return fmean(row["status"] == "censored" for row in eligible)
    if observation == "consequence_paid_off_rate":
        values = [row["y"] for row in rows if row["predicate"] == "return_paid_off"
                  and row["status"] == "settled"]
    elif observation == "forecast_skill":
        values = [row["skill"] for row in rows if row["skill"] is not None]
    else:
        values = [row["verdict"] for row in rows if row.get("verdict") is not None]
    if not values:
        return None
    return pstdev(values) if observation == "verdict_std" else fmean(values)


def measure_card(card: MetricCard, samples: CardSamples, observations=None) -> dict[str, float]:
    """Return each fully supported scope's measurement without pooling its sample selector."""
    from factorylab.runtime.observations import seed_book

    book = observations or seed_book()
    preflight_card(card, book)
    observation = book.get(card.observation)
    window = card.window
    if window.kind == "windows":
        selected = samples.windows[-window.n:]
        if len(selected) < window.n:
            return {}
        if window.per is None:
            # Recompute from the selected windows' raw sufficient statistics;
            # rates use their real denominators rather than an average of rates.
            merged = deepcopy(selected[0])
            for other in selected[1:]:
                for key, value in other.items():
                    if key in ("index", "equity_start_micro"):
                        continue
                    if key == "max_position_notional_micro":
                        present = [v for v in (merged[key], value) if v is not None]
                        merged[key] = max(present) if present else None
                    elif isinstance(value, dict):
                        for handle, judges in value.items():
                            for judge, scores in judges.items():
                                target = merged[key].setdefault(handle, {}).setdefault(judge, [])
                                target.extend(scores)
                    elif isinstance(value, list):
                        merged[key].extend(value)
                    elif isinstance(value, set):
                        merged[key].update(value)
                    elif isinstance(value, int | float):
                        merged[key] += value
            if observation.id in ("forecast_skill", "cost_per_attempt"):
                # A closed record keeps no per-response attribution, so these
                # are measured from the samples the selected windows retained.
                source = samples.forecasts if observation.id == "forecast_skill" else (
                    _selected(observation.id, samples.returns))
                rows = [r for r in source if selected[0]["index"] <= r["window"]
                        <= selected[-1]["index"]]
                value = _measure_rows(observation.id, rows)
                spread = _sample_values(observation.id, rows)
            else:
                # A registered observation is measured by its own code here.
                value = book.value(observation, SimpleNamespace(**merged))
                spread = None
                if window.interval is not None:
                    # Each selected window is one sample of the pooled measurement.
                    spread = [v for record in selected if (v := book.value(
                        observation, SimpleNamespace(**record))) is not None]
            if window.interval is not None and not window.interval.satisfied(spread or []):
                return {}
            return {"all": value} if value is not None else {}
        if observation.registered:
            return _measure_scoped(card, observation, book, samples, selected)
        kind = "returns" if observation.id in RETURN_OBSERVATIONS else "forecasts"
        rows = [r for r in getattr(samples, kind)
                if selected[0]["index"] <= r["window"] <= selected[-1]["index"]]
    else:
        rows = getattr(samples, window.kind)
    rows = _selected(observation.id, rows)
    result = {}
    for scope, group in _groups(card, rows).items():
        if window.kind != "windows":
            group = _horizon(observation.id, group, window.n)
            if group is None:
                continue
        if window.interval is not None and not window.interval.satisfied(
                _sample_values(observation.id, group) or []):
            continue  # its mean is not yet known to the card's required precision
        value = _measure_rows(observation.id, group)
        if value is not None:
            result[scope] = value
    return result


def _scope_rows(card: MetricCard, rows: list[dict]) -> dict[str, list[dict]]:
    """Rows partitioned by the card's scope, filtered to its role unless it answers for all."""
    groups: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        if card.answers_for != "all" and row.get("role") != card.answers_for:
            continue
        key = row.get(card.window.per)
        if key is not None:
            groups[key].append(row)
    return dict(groups)


def _measure_scoped(card: MetricCard, observation, book, samples: CardSamples,
                    selected: list[dict]) -> dict[str, float]:
    """A registered observation measured once per scope, on that scope's anonymous facts.

    Charter audit C3. The kernel knows each scope; the population's code is run
    once per scope on ``scope_facts`` and returns one number, which the kernel
    files under the scope. A scope whose code returns nothing is unmeasured.
    """
    first, last = selected[0]["index"], selected[-1]["index"]
    returns = _scope_rows(card, [r for r in samples.returns if first <= r["window"] <= last])
    forecasts = _scope_rows(card, [r for r in samples.forecasts
                                   if first <= r["window"] <= last])
    result = {}
    for scope in sorted(set(returns) | set(forecasts), key=str):
        own_returns, own_forecasts = returns.get(scope, []), forecasts.get(scope, [])
        if card.window.interval is not None:
            per_window = [value for record in selected if (value := book.value_of_facts(
                observation, scope_facts(
                    [record], [r for r in own_returns if r["window"] == record["index"]],
                    [r for r in own_forecasts if r["window"] == record["index"]])))
                is not None]
            if not card.window.interval.satisfied(per_window):
                continue
        value = book.value_of_facts(observation, scope_facts(selected, own_returns,
                                                             own_forecasts))
        if value is not None:
            result[scope] = value
    return result


def measure_cards(cards, samples: CardSamples, window, observations=None) -> dict[str, float]:
    """Pricing observes equal-scope means; private scope values remain available for attribution."""
    samples.closed(window)
    samples.scopes = {}
    for card in cards:
        try:
            samples.scopes[card.id] = measure_card(card, samples, observations)
        except ValueError:
            # Admission refuses these; legacy/manually supplied cards stay unpriced.
            samples.scopes[card.id] = {}
    samples.values = {cid: fmean(scopes.values())
                      for cid, scopes in samples.scopes.items() if scopes}
    samples.medians = {}
    for card in cards:
        if card.id not in samples.values:
            continue
        observation = card.observation.strip().lower()
        if observation in COST_OBSERVATIONS and (
            card.window.kind == "returns" or card.window.per is not None
            or observation == "cost_per_attempt"
        ):
            rows = samples.returns
            if card.window.kind == "windows":
                first = samples.windows[-card.window.n]["index"]
                last = samples.windows[-1]["index"]
                rows = [r for r in rows if first <= r["window"] <= last]
            scope_medians = []
            for group in _groups(card, rows).values():
                if card.window.kind == "returns":
                    group = _horizon(observation, group, card.window.n)
                    if group is None:
                        continue
                costs = [r["cost"] for r in _cost_responses(observation, group)]
                if costs:
                    scope_medians.append(median(costs))
            if scope_medians:
                samples.medians[card.id] = fmean(scope_medians)
        elif observation == "cost_per_return":
            costs = [value for w in samples.windows[-card.window.n:] for value in w["costs"]]
            if costs:
                samples.medians[card.id] = median(costs)
        else:
            samples.medians[card.id] = samples.values[card.id]
    return dict(samples.values)
