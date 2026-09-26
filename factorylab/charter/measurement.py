"""Exact rolling card measurements shared by pricing, admission and policy liability."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping
from copy import deepcopy
from dataclasses import asdict, dataclass, field
from statistics import fmean, median, pstdev
from types import MappingProxyType, SimpleNamespace

from factorylab.charter.charter import MetricCard

RETURN_OBSERVATIONS = frozenset({
    "cost_per_return", "cost_per_attempt", "well_formed_rate", "noop_share", "revision_rate",
    "tool_calls", "prompt_bytes", "you_bytes", "inputs_bytes", "downstream_read_bytes",
})
#: The context-size observations (essay II.IV.a): each a mean, per invocation, of one
#: ledgered prompt byte count carried on the invocation's own sample row.
#: Read-only: no global mutable state (AGENTS.md).
PROMPT_OBSERVATIONS: Mapping[str, str] = MappingProxyType({
    "prompt_bytes": "prompt_bytes", "you_bytes": "you_bytes", "inputs_bytes": "inputs_bytes"})
#: What a return's readers were rendered: reading rows filed under the return's author
#: in the window the reading was metered, over that scope's responses there.
READ_OBSERVATION = "downstream_read_bytes"
# The two cost selections: per successful response, and per attempt (failed
# responses included).
COST_OBSERVATIONS = frozenset({"cost_per_return", "cost_per_attempt"})
# The runtime keeps per-decision attribution on the same window object;
# measurement never observes it.
ATTRIBUTION_FIELDS = ("decisions", "closed_values", "closed_regions", "closed_cards",
                      "closed_prices", "closed_scopes", "closed_holdouts", "series_discarded",
                      # Wave 16, D5: who relieved a rate, by decision handle.
                      "closed_relief", "paid_off_settled", "paid_off_handles",
                      # The price loop's own schedule is the clock's, not an observation.
                      "opened_tick", "due_tick")
FORECAST_OBSERVATIONS = frozenset({
    "forecast_skill", "verdict_mean", "verdict_std", "consequence_paid_off_rate", "censored_share",
    "avoidably_unresolved_share",
})
#: Observations a whole-window card still measures from settled forecast rows.
FORECAST_ROWS = frozenset({"forecast_skill", "avoidably_unresolved_share"})
#: The closed window's own counter that says a whole-window seed observation has a new
#: settled sample in it (time audit T2).
_WINDOW_SUPPORT = {
    "verdict_mean": lambda w: bool(w.verdicts),
    "verdict_std": lambda w: bool(w.verdicts),
    "consequence_paid_off_rate": lambda w: w.consequences_settled > 0,
    "censored_share": lambda w: w.outcomes > 0,
    "non_acting_informative_share": lambda w: (w.non_acting_outcomes or 0) > 0,
    "non_acting_paid_off_rate": lambda w: (w.non_acting_informative or 0) > 0,
}


def fresh_sample(card: MetricCard, samples: CardSamples, window) -> bool:
    """Whether the window that just closed added a settled sample to this card's scope.

    Time audit T2: a price moves on new evidence, never on the same rolling
    selection read again. A card over responses or settled forecasts has one when
    a row of its own scope (its role, its per) was recorded in that window; a
    whole-window seed observation when the window's own counter for it moved. A
    window-level observation (a registered measurement, turnover, the window's
    facts) is sampled by the closed window itself.
    """
    observation = card.observation.strip().lower()
    kind = card.window.kind
    if kind == "windows" and card.window.per is None and observation not in FORECAST_ROWS:
        if observation in PROMPT_OBSERVATIONS:
            # Measured over the window's rendered prompts: a window with none (a
            # ballot no assembly answered, a request that could not be
            # rendered) has no prompt to measure.
            # A record closed before prompts were measured measured none.
            return (getattr(window, "prompts", 0) or 0) > 0
        if observation == READ_OBSERVATION:
            # Measured over the invocations whose readings are metered; a record closed
            # before they were metered measured none.
            return (getattr(window, "read_measured", 0) or 0) > 0
        if observation in RETURN_OBSERVATIONS:
            return window.invocations > 0 or bool(window.decisions)
        support = _WINDOW_SUPPORT.get(observation)
        return True if support is None else support(window)
    if kind in ("returns", "forecasts"):
        rows = _rows(samples, kind, observation)
    elif observation in RETURN_OBSERVATIONS:
        rows = _rows(samples, "returns", observation)
    elif observation in FORECAST_OBSERVATIONS:
        rows = samples.forecasts
    else:
        return True
    if observation in PROMPT_OBSERVATIONS or observation == READ_OBSERVATION:
        return _fresh_context(card, samples, observation, _selected(observation, rows),
                              window)
    rows = [row for row in _selected(observation, rows) if row["window"] == window.index]
    return bool(_groups(card, rows))


def _fresh_context(card: MetricCard, samples: CardSamples, observation: str,
                   rows: list[dict], window) -> bool:
    """Whether the closed window added a row that ``measure_card`` now measures.

    Guarantees freshness admits exactly the rows measurement does (time audit
    T2): the same ``_selected`` rows (a prompt-size row only if it carries that
    observation's byte field), grouped by the same scope, cut to the same full
    returns horizon or to the same selected closed windows, in a scope that is
    measured at all. A row outside that selection moves no measurement, and
    calling it new evidence would let the controller integrate the unchanged
    value twice. So a reading metered after its author's latest response is
    retained (a later horizon may span it) but is not fresh until a horizon
    selects it.
    """
    if card.window.kind == "windows" and len(samples.windows) < card.window.n:
        return False  # the card measures nothing until its windows are closed
    selected = {record["index"] for record in samples.windows[-card.window.n:]}
    for group in _groups(card, rows).values():
        if card.window.kind == "returns":
            group = _horizon(observation, group, card.window.n) or []
        else:
            group = [row for row in group if row["window"] in selected]
            if all(row.get("reading") for row in group):
                continue  # no response to measure over: the scope has no value
        if any(row["window"] == window.index for row in group):
            return True
    return False


#: What ``_measure_rows`` reads from each selected sample row, and nothing else, per
#: observation measured over rows. Chapter II §I.b ("the structures of requests and
#: rewards" are public) and §II.b: the catalogue's input clause is rendered from this
#: declaration and ``observations.WINDOW_INPUTS``, and a test holds each calculator to
#: it (Codex on #152), so a description cannot name an input its calculator does not
#: read. The row keys that select or group rows (role, assembly, window) are selection.
ROW_INPUTS: Mapping[str, tuple[str, ...]] = MappingProxyType({
    "cost_per_return": ("cost", "ok"),
    "cost_per_attempt": ("invoked", "cost"),
    "well_formed_rate": ("ok",),
    "noop_share": ("noop",),
    "revision_rate": ("revision",),
    "tool_calls": ("tool_calls",),
    "prompt_bytes": ("prompt_bytes",),
    "you_bytes": ("you_bytes",),
    "inputs_bytes": ("inputs_bytes",),
    "downstream_read_bytes": ("reading", "read_bytes"),
    "forecast_skill": ("skill",),
    "verdict_mean": ("verdict",),
    "verdict_std": ("verdict",),
    "consequence_paid_off_rate": ("predicate", "status", "subject_acted", "y"),
    "censored_share": ("status",),
    "avoidably_unresolved_share": ("excluded", "status"),
})
#: What each sample row key a calculator reads is, as the catalogue states it.
ROW_KEY_MEANINGS: Mapping[str, str] = MappingProxyType({
    "cost": "the response's metered cost, continuations included",
    "invoked": "whether the response was an invocation (a ballot whose assembly was "
               "unavailable is none; a row sampled before this was recorded was one)",
    "ok": "whether the response was well formed",
    "noop": "whether the response declared action noop or hold",
    "revision": "whether the response's registration was accepted or its amendment "
                "activated",
    "tool_calls": "the tool calls the response attempted, failures included",
    "prompt_bytes": "the UTF-8 bytes of its opening prompt, as its ledger row records them",
    "you_bytes": "the UTF-8 bytes of that prompt's YOU section",
    "inputs_bytes": "the UTF-8 bytes of that prompt's INPUTS section",
    "reading": "whether the row is a reading (the INPUTS bytes of an invocation "
               "commissioned on a published return, filed under its author) rather "
               "than a response",
    "read_bytes": "a reading's INPUTS bytes",
    "skill": "a settled forecast's score 1 - (q - y)^2 minus the same score at its "
             "pre-outcome prevalence base rate b, 1 - (b - y)^2",
    "verdict": "the evaluator verdict the row carries: a response's own, or the one "
               "attached to a resolved forecast",
    "predicate": "the forecast's predicate",
    "status": "the forecast record's status: settled or censored",
    "subject_acted": "whether the return the forecast is about acted",
    "y": "the forecast's measured outcome",
    "excluded": "why a due commitment is excluded from its owner's sample, when it is",
})
#: Seeds a card over whole closed windows measures from the selected windows' sample
#: rows rather than the windows' counters: a closed record keeps no per-response
#: attribution (``measure_card``).
ROWS_ON_CLOSED_WINDOWS = frozenset({"forecast_skill", "cost_per_attempt"})
#: Seeds whose card over closed windows computes a different number from the
#: window's own published value (``price.window`` observations), each with its reason.
#: Empty: rule 3 (published = enforced) and Codex on #152, one name, one formula. A
#: test holds every seed not named here to the same number on the same window.
CLOSED_WINDOW_DIFFERS: Mapping[str, str] = MappingProxyType({})


def window_forecast_skills(samples, index: int) -> list[float]:
    """The skill of each forecast settled in window ``index``, in settlement order.

    Guarantees the one sample list both ``forecast_skill`` readings average: a closed
    window's own value (the runtime sets it as the window's ``forecast_skills``) and a
    card over closed windows (``measure_card`` reads the same rows).
    """
    return [row["skill"] for row in samples.forecasts
            if row["window"] == index and row["skill"] is not None]

#: What each row-measured seed computes from its inputs. Its inputs are stated only by
#: the rendered clause (``_input_clause``), never here.
_FORMULAS: Mapping[str, str] = MappingProxyType({
    "cost_per_return": "Mean metered cost of the well-formed responses.",
    "cost_per_attempt": "Mean metered cost of every attempt, failed ones included: an "
    "attempt is an invocation, so a response that was none (a ballot whose assembly was "
    "unavailable) is not one.",
    "well_formed_rate": "Well-formed responses over responses, ballots included.",
    "tool_calls": "Mean attempted tool calls per response, failures included.",
    "forecast_skill": "Mean forecast skill, each the score 1 - (q - y)^2 minus the "
    "same score at the pre-outcome prevalence base rate b, 1 - (b - y)^2; positive when "
    "forecasts beat the base rate. Settled forecasts only: a verdict's consequence score "
    "is never included.",
    "noop_share": "Share of responses declaring action noop or hold.",
    "revision_rate": "Share of responses whose registration was accepted, amendments "
    "activated included.",
    "verdict_mean": "Mean evaluator verdict; forecast selectors group the verdicts by the "
    "judged return's assembly or role.",
    "verdict_std": "Population standard deviation of evaluator verdicts; forecast "
    "selectors group them by the judged return's assembly or role.",
    "consequence_paid_off_rate": "Positive return_paid_off outcomes over the settled "
    "consequences of acting returns.",
    "censored_share": "censored / outcomes over a closed window: the settlements it "
    "resolved censored over every settlement it resolved. Over forecast rows: the rows "
    "whose status is censored over the selected rows.",
    "avoidably_unresolved_share": "Attributable, avoidably unresolved accepted "
    "commitments over the eligible commitments due in the responsible scope. A "
    "commitment not yet due is not in the sample; one the owner documented as "
    "externally unobservable without its own fault, and an event the seat never "
    "committed to observe, are excluded. No eligible sample is unmeasured, never zero.",
    "prompt_bytes": "Mean UTF-8 bytes of the opening prompt rendered for each "
    "invocation, every section included; tool-round continuations are not counted.",
    "you_bytes": "Mean UTF-8 bytes of the YOU section of the opening prompt rendered for "
    "each invocation.",
    "inputs_bytes": "Mean UTF-8 bytes of the INPUTS section of the opening prompt "
    "rendered for each invocation.",
    "downstream_read_bytes": "INPUTS bytes of the invocations commissioned on a published "
    "return, filed under that return's author in the window each reading was metered, "
    "over the author scope's responses in the same windows; a scope whose returns no "
    "invocation read measures zero, and a response sampled before readings were metered "
    "is in neither the numerator nor the denominator.",
})


def _named(names, meanings) -> str:
    return "; ".join(f"{name} ({meanings[name]})" for name in names) or "nothing"


def _input_clause(observation: str) -> str:
    """The published statement of a seed's inputs, rendered from the declarations
    (``observations.WINDOW_INPUTS``, ``ROW_INPUTS``) and nothing else."""
    from factorylab.runtime.observations import WINDOW_FIELD_MEANINGS, WINDOW_INPUTS

    window = _named(WINDOW_INPUTS[observation], WINDOW_FIELD_MEANINGS)
    parts = [f"Inputs. A closed window's value reads its {window}."]
    rows = ROW_INPUTS.get(observation)
    if rows is not None:
        read = _named(rows, ROW_KEY_MEANINGS)
        if observation in ROWS_ON_CLOSED_WINDOWS:
            parts.append(f"A card over closed windows reads each of their sample rows' {read}"
                         ", which gives the window's own value for one window.")
        parts.append(f"A card over returns or forecasts reads each selected row's {read}.")
    if observation in CLOSED_WINDOW_DIFFERS:
        parts.append("A card over closed windows computes a different number from the "
                     f"window's own value: {CLOSED_WINDOW_DIFFERS[observation]}")
    return " ".join(parts)


def measurement_catalogue(observations=None) -> list[dict]:
    """Public card metadata states selector semantics separately from raw window diagnostics.

    A population-registered observation appears here beside the seeds, with
    its declared units and range, so a card can name it the same way. Guarantees
    every seed's description ends in its input clause, rendered from the same
    declarations its calculators are held to (``_input_clause``), and its row
    carries them as ``inputs``: no description names an input some other way.
    """
    from factorylab.runtime.observations import SEED_IDS, WINDOW_INPUTS, seed_book

    result = (observations or seed_book()).catalogue()
    for row in result:
        observation = row["id"]
        if observation in SEED_IDS:
            row["description"] = (f"{_FORMULAS.get(observation, row['description'])} "
                                  f"{_input_clause(observation)}")
            row["inputs"] = {"windows": list(WINDOW_INPUTS[observation]),
                             "rows": list(ROW_INPUTS.get(observation, ()))}
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
                     "amendments_proposed", "amendments_activated", "market_purchases",
                     "non_acting_outcomes", "non_acting_informative",
                     "non_acting_paid_off")
#: Observations whose value is not a mean of its samples: no interval states their error.
NOT_A_MEAN = frozenset({"verdict_std", "evaluator_disagreement",
                        # Reading bytes over responses: no reading is one of the responses.
                        READ_OBSERVATION})


def scope_facts(windows: list[dict], returns: list[dict], forecasts: list[dict],
                readings: list[dict] | tuple = ()) -> dict:
    """One scope's share of the selected closed windows, as anonymous public facts.

    Charter audit C3. The kernel partitions and the population's code measures:
    the caller has already selected the rows of one role or one assembly, and
    this builds the facts ``window_facts`` would publish for a window holding
    only those rows. The world's own series (mids, funding, books, wallet
    balances, tick times) are facts about the world, not the scope, and pass
    through whole. The counters are the scope's own, with the window's meaning.
    ``invocations`` counts the scope's invocations exactly as ``window.invocations``
    counts the window's: a response that was no invocation (a ballot whose assembly
    was unavailable, rendered no prompt) is not one, so the scope's summed prompt
    bytes over its invocations are a mean per rendered prompt, as they are
    globally. Its responses give (``ok``, ``costs`` of its well-formed responses,
    ``tool_calls``,
    ``noop_returns``, ``revision_returns``, ``producer_returns`` as its response
    count, ``compute_spend_micro``, the summed
    ``prompt_bytes``, ``you_bytes`` and ``inputs_bytes`` of its prompts, and the
    ``downstream_read_bytes`` its returns' readers were rendered), the verdicts its
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
    responses = returns
    settled = [row for row in forecasts if row.get("predicate") == "return_paid_off"
               and row.get("status") == "settled"]
    merged.update({
        # The window's meaning: every invocation the runtime made, and not a response
        # that was none (a ballot no assembly was there to answer). A row sampled
        # before the marker existed was a real invocation.
        "invocations": sum(bool(row.get("invoked", True)) for row in responses),
        # The prompt means' denominator, as the window's ``prompts`` is.
        "prompts": sum(row.get("prompt_bytes") is not None for row in responses),
        "ok": sum(bool(row["ok"]) for row in responses),
        "costs": [row["cost"] for row in responses if row["ok"]],
        "tool_calls": sum(row["tool_calls"] for row in responses),
        "producer_returns": len(responses),
        "noop_returns": sum(bool(row["noop"]) for row in responses),
        "revision_returns": sum(bool(row["revision"]) for row in responses),
        "revision_handles": {row["handle"] for row in responses if row["revision"]},
        "compute_spend_micro": sum(row["cost"] for row in returns),
        **{key: sum(row.get(key) or 0 for row in responses)
           for key in PROMPT_OBSERVATIONS.values()},
        "downstream_read_bytes": sum(row["read_bytes"] for row in readings),
        # The reading mean's denominator, as the window's ``read_measured`` is: the
        # scope's invocations sampled since readings were metered.
        "read_measured": sum(row.get("invoked") is True for row in responses),
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
        return [float(bool(row[key])) for row in rows]
    if observation == "tool_calls":
        return [float(row["tool_calls"]) for row in rows]
    if observation in PROMPT_OBSERVATIONS:
        key = PROMPT_OBSERVATIONS[observation]
        return [float(row[key]) for row in rows if row.get(key) is not None]
    if observation == "censored_share":
        return [float(row["status"] == "censored") for row in rows]
    if observation == "avoidably_unresolved_share":
        return [float(row["status"] == "censored") for row in rows
                if row.get("excluded") is None]
    if observation == "consequence_paid_off_rate":
        return [float(row["y"]) for row in rows if row["predicate"] == "return_paid_off"
                and row["status"] == "settled" and row.get("subject_acted") is not False]
    if observation == "forecast_skill":
        return [float(row["skill"]) for row in rows if row["skill"] is not None]
    return [float(row["verdict"]) for row in rows if row.get("verdict") is not None]


@dataclass
class CardSamples:
    """Private samples preserve order, ownership and window membership across resume."""

    returns: list[dict] = field(default_factory=list)
    forecasts: list[dict] = field(default_factory=list)
    windows: list[dict] = field(default_factory=list)
    # Reading rows: a reader's INPUTS bytes filed under the author of the return it
    # was commissioned on. Kept apart from ``returns``: a reading is neither a response
    # nor a cost, so no other observation can select one.
    readings: list[dict] = field(default_factory=list)
    values: dict[str, float] = field(default_factory=dict)
    scopes: dict[str, dict[str, float]] = field(default_factory=dict)
    medians: dict[str, float] = field(default_factory=dict)
    # The violation each card's failed holdouts added at the last close (M3).
    holdouts: dict[str, float] = field(default_factory=dict)

    def returned(self, *, handle: str, assembly: str, role: str, window: int, ret,
                 invoked: bool = True) -> None:
        """One completed invocation, including its continuation costs, is one return sample.

        Its prompt byte counts are the ones its ledger row records, or None when the
        runtime rendered it no prompt: an unmeasured prompt is never a zero-byte one.
        ``invoked`` is False for a response that was no invocation (a ballot whose
        assembly was unavailable): it is still a response, but it is not among the
        invocations its scope publishes, exactly as the window does not count it.
        """
        sections = getattr(ret, "prompt_sections", None) or {}
        self.returns.append({
            "handle": handle, "assembly": assembly, "role": role, "window": window,
            "cost": ret.cost, "ok": ret.status == "ok",
            "noop": str(ret.outputs.get("action", "")).lower() in ("noop", "hold"),
            "revision": False, "tool_calls": len(ret.tool_calls),
            "verdict": ret.outputs.get("verdict"),
            "prompt_bytes": sections.get("total"), "you_bytes": sections.get("you"),
            "inputs_bytes": sections.get("inputs"), "invoked": bool(invoked),
        })

    def read(self, *, handle: str, assembly: str, role: str, window: int,
             read_bytes: int) -> None:
        """One reading of the return ``handle`` is a reading row of its author's scope.

        Filed in the window the reading was metered, and under the author
        (``assembly``, ``role``), never the reader: the reader's identity is not in
        the row at all.
        """
        self.readings.append({"handle": handle, "assembly": assembly, "role": role,
                              "window": window, "read_bytes": int(read_bytes),
                              "reading": True})

    def revised(self, handle: str) -> None:
        """Accepted registrations mark their own return, including pre-continuation proposals."""
        for sample in reversed(self.returns):
            if sample["handle"] == handle:
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
            # Whether the judged return acted (wave 16, R-H): consequence_paid_off_rate
            # reads acting returns only. Unknown (None) counts as acting, as before.
            "subject_acted": subject.get("acted"),
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
        """Retain only the sample horizons still required by cards or outstanding policy votes.

        Guarantees a reading row survives exactly as long as some returns horizon,
        present or future, can still select it, and no longer. The bound follows
        from the horizon's definition: a card's horizon is its scope's latest ``n``
        responses, so its first response only ever moves forward, and it selects
        the readings metered from that first response's window on. A reading metered
        at or after the window of the current horizon's first response is kept:
        it lies in today's horizon, or after the latest response and so inside the
        next horizon if the scope responds again. One metered before that window
        can never be selected again, and is dropped. What is kept per scope is
        therefore at most the readings metered since its ``n``-th latest response;
        a scope with no response retained keeps only what the retained-window floor
        keeps of every row.
        """
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
                for group in _groups(card, rows).values():
                    # Retain the horizon measurement would select, including a
                    # partly filled one.
                    keep.update(id(row) for row in _horizon(
                        card.observation, group, card.window.n, partial=True))
            rows[:] = [row for row in rows if (
                row["handle"] in pending_handles or id(row) in keep
                or (first_window is not None and row["window"] >= first_window)
            )]
        # Readings: kept from the window of each horizon's first response on (above).
        keep = set()
        rows = _selected(READ_OBSERVATION, _rows(self, "returns", READ_OBSERVATION))
        for card in cards:
            if card.window.kind != "returns" or (
                    card.observation.strip().lower() != READ_OBSERVATION):
                continue
            for group in _groups(card, rows).values():
                horizon = [row for row in group if not row.get("reading")][-card.window.n:]
                if horizon:
                    start = horizon[0]["window"]
                    keep.update(id(row) for row in group
                                if row.get("reading") and row["window"] >= start)
        self.readings[:] = [row for row in self.readings if (
            id(row) in keep or (first_window is not None and row["window"] >= first_window))]


def record_card_forecasts(runtime, pending, baseline) -> None:
    """Settled forecast events retain the actual forecaster and judged contract scopes."""
    from factorylab.cortex.registration import measured_role
    from factorylab.kernel.events import EventKind

    samples = runtime.card_samples
    returns = {row["handle"]: row for row in samples.returns}
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
        acted = getattr(runtime, "_acted", None)
        subject = {**subject, "acted": acted(forecast.about_handle) if acted else None}
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
    kinds = ("ProducerReturn", "Verdict", "MetaVerdict", "Exposure", "CounterVerdict")
    if card.answers_for not in ("producer", "evaluator", "meta", "antagonist", "adversary",
                                "all"):
        kinds += (card.answers_for,)
    for kind in kinds:
        role = measured_role(kind)
        samples.returned(handle=kind, assembly=kind, role=role, window=1,
                         ret=Return(kind, {"verdict": 0.0} if kind in ("Verdict", "CounterVerdict")
                                    else {}, 1, "ok",
                                    prompt_sections={"total": 1, "you": 1, "inputs": 1}))
        samples.read(handle=kind, assembly=kind, role=role, window=1, read_bytes=1)
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
                           non_acting_outcomes=1, non_acting_informative=1,
                           meta_verdicts=[0.0], max_position_notional_micro=0,
                           verdicts={"sample": {"a": [0.0], "b": [0.0]}},
                           prompts=1, prompt_bytes=1, you_bytes=1, inputs_bytes=1,
                           downstream_read_bytes=1, read_measured=1)
    if unit.id not in measure_cards((unit,), samples, window, observations=observations):
        raise ValueError(f"card {card.id} window: measurement preflight produced no value")


def _selected(observation: str, rows: list[dict]) -> list[dict]:
    """The rows an observation selects: reading rows only for the read observation.

    A reading row is a reading of a return, not a response, so every observation
    but ``downstream_read_bytes`` loses it here, before any grouping or horizon.

    A context-size observation selects only the responses the runtime rendered a
    prompt for (a ballot whose assembly was unavailable was rendered none). An
    unmeasured row is dropped here, before freshness, grouping or horizon, so it
    is never new evidence and never takes a measured response's horizon slot.
    """
    observation = observation.strip().lower()
    if observation == READ_OBSERVATION:
        # Its responses are the invocations whose readings are metered, as the global
        # window's ``read_measured`` are: a ballot no assembly answered is none (a
        # request that failed to render still is one), and a row sampled before
        # readings were metered carries no ``invoked`` marker and is none either.
        return [row for row in rows if row.get("reading") or row.get("invoked") is True]
    if observation in PROMPT_OBSERVATIONS:
        key = PROMPT_OBSERVATIONS[observation]
        return [row for row in rows if not row.get("reading") and row.get(key) is not None]
    return [row for row in rows if not row.get("reading")]


def _rows(samples: CardSamples, kind: str, observation: str) -> list[dict]:
    """The sample rows an observation selects from: reading rows join only its own."""
    rows = getattr(samples, kind)
    if kind == "returns" and observation.strip().lower() == READ_OBSERVATION:
        return rows + samples.readings
    return rows


def _horizon(observation: str, group: list[dict], n: int, *,
             partial: bool = False) -> list[dict] | None:
    """Select the latest `n` responses of a group, or None while it has fewer.

    `partial` keeps a horizon that has not filled yet, which retention needs and
    measurement refuses.
    """
    responses = [row for row in group if not row.get("reading")]
    if observation.strip().lower() == "avoidably_unresolved_share":
        # The horizon is the last `n` *eligible* due commitments: a documented
        # exclusion never occupies a slot, so external unobservability cannot
        # push an accountable commitment out of the sample it answers for.
        responses = [row for row in responses if row.get("excluded") is None]
    if len(responses) < n and not partial:
        return None
    responses = responses[-n:]
    keep = {id(row) for row in responses}
    if responses and observation.strip().lower() == READ_OBSERVATION:
        # A reading row joins the horizon metered in the windows of the selected
        # responses, never as one of them.
        first, last = responses[0]["window"], responses[-1]["window"]
        keep.update(id(row) for row in group
                    if row.get("reading") and first <= row["window"] <= last)
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
    """The responses a cost selection divides over: successful ones per return, every
    attempt per attempt.

    Guarantees an attempt is an invocation, as the window's own ``invocations`` counts
    one: a response that was none (a ballot whose assembly was unavailable: nothing was
    rendered or called) is no attempt, so the cost per attempt of a closed window's rows
    is the window's own value (Codex on #152). A row sampled before the ``invoked``
    marker existed was a real invocation.
    """
    if observation == "cost_per_return":
        return [row for row in rows if row["ok"]]
    return [row for row in rows if row.get("invoked") is not False]


def _measure_rows(observation: str, rows: list[dict]) -> float | None:
    if not rows:
        return None
    if observation in COST_OBSERVATIONS:
        # Per attempt, a failed response is one of the responses and its cost
        # is spent.
        values = [row["cost"] for row in _cost_responses(observation, rows)]
        return sum(values) / len(values) if values else None
    if observation in ("well_formed_rate", "noop_share", "revision_rate"):
        key = {"well_formed_rate": "ok", "noop_share": "noop", "revision_rate": "revision"}[
            observation
        ]
        return fmean(row[key] for row in rows)
    if observation == "tool_calls":
        # The card's unit is calls per return: ten responses of one call each
        # measure one, not ten.
        return fmean(row["tool_calls"] for row in rows)
    if observation in PROMPT_OBSERVATIONS:
        # Only a prompt the runtime rendered and measured is a sample of its size.
        key = PROMPT_OBSERVATIONS[observation]
        sizes = [row[key] for row in rows if row.get(key) is not None]
        return fmean(sizes) if sizes else None
    if observation == READ_OBSERVATION:
        # What the scope's returns were read for, over the responses it made in the
        # same windows: a reading is added to them and never counted as one.
        responses = [row for row in rows if not row.get("reading")]
        if not responses:
            return None
        return sum(row["read_bytes"] for row in rows if row.get("reading")) / len(responses)
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
                  and row["status"] == "settled" and row.get("subject_acted") is not False]
    elif observation == "forecast_skill":
        values = [row["skill"] for row in rows if row["skill"] is not None]
    else:
        values = [row["verdict"] for row in rows if row.get("verdict") is not None]
    if not values:
        return None
    return pstdev(values) if observation == "verdict_std" else fmean(values)


def _merge_counts(into: dict, other: dict) -> None:
    """Guarantees ``into`` holds both windows' statistics: counters summed, lists joined.

    A window keeps nested score lists (``{handle: {judge: [scores]}}``) beside flat
    counters (``calls_by_family``, ``calls_by_provider``: ``{name: count}``); both are
    raw sufficient statistics, merged at whatever depth they sit.
    """
    for name, item in other.items():
        if isinstance(item, dict):
            _merge_counts(into.setdefault(name, {}), item)
        elif isinstance(item, list):
            into.setdefault(name, []).extend(item)
        elif isinstance(item, int | float) and not isinstance(item, bool):
            into[name] = into.get(name, 0) + item
        else:
            raise TypeError(f"window statistic {name!r} cannot be merged")


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
                    if key in ("ews_variance", "ews_autocorrelation"):
                        # A statistic of the latest close, not a quantity to add.
                        merged[key] = value if value is not None else merged[key]
                        continue
                    if key == "max_position_notional_micro":
                        present = [v for v in (merged[key], value) if v is not None]
                        merged[key] = max(present) if present else None
                    elif isinstance(value, dict):
                        _merge_counts(merged[key], value)
                    elif isinstance(value, list):
                        merged[key].extend(value)
                    elif isinstance(value, set):
                        merged[key].update(value)
                    elif isinstance(value, int | float):
                        # A record closed before a counter existed lacks it: zero.
                        merged[key] = (merged.get(key) or 0) + value
            if observation.id in ROWS_ON_CLOSED_WINDOWS:
                # A closed record keeps no per-response attribution, so these
                # are measured from the samples the selected windows retained.
                source = samples.forecasts if observation.id == "forecast_skill" else (
                    samples.returns)
                rows = [r for r in source if selected[0]["index"] <= r["window"]
                        <= selected[-1]["index"]]
                value = _measure_rows(observation.id, rows)
                spread = _sample_values(observation.id, rows)
            else:
                # A registered observation is measured by its own code here.
                if getattr(observation, "per_window", False):
                    # A quantity of one window is measured window by window and averaged:
                    # summed statistics would add amounts, or divide by the first
                    # window's base alone (the #139 review).
                    each = [book.value(observation, SimpleNamespace(**w)) for w in selected]
                    each = [v for v in each if v is not None]
                    value = fmean(each) if each else None
                else:
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
        rows = [r for r in _rows(samples, kind, observation.id)
                if selected[0]["index"] <= r["window"] <= selected[-1]["index"]]
    else:
        rows = _rows(samples, window.kind, observation.id)
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
    readings = _scope_rows(card, [r for r in samples.readings
                                  if first <= r["window"] <= last])
    result = {}
    # A reading can be its author's only row in the selected windows: the scope is
    # still one the population's code measures, on facts that carry that reading.
    for scope in sorted(set(returns) | set(forecasts) | set(readings), key=str):
        own_returns, own_forecasts = returns.get(scope, []), forecasts.get(scope, [])
        own_readings = readings.get(scope, [])
        if card.window.interval is not None:
            per_window = [value for record in selected if (value := book.value_of_facts(
                observation, scope_facts(
                    [record], [r for r in own_returns if r["window"] == record["index"]],
                    [r for r in own_forecasts if r["window"] == record["index"]],
                    [r for r in own_readings if r["window"] == record["index"]])))
                is not None]
            if not card.window.interval.satisfied(per_window):
                continue
        value = book.value_of_facts(observation, scope_facts(selected, own_returns,
                                                             own_forecasts, own_readings))
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
