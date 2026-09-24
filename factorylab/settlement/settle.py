"""Due commitments receive one original-handle outcome, with missing facts left unscored."""

from collections.abc import Callable, Mapping
from dataclasses import dataclass

from factorylab.kernel.ledger import canonical
from factorylab.kernel.queue import DecisionQueue, SettleStatus
from factorylab.kernel.registry import _freeze
from factorylab.settlement.forecast import Forecast, ForecastBook
from factorylab.settlement.receipts import LearningReceipt, ReceiptBook
from factorylab.settlement.scoring import PrevalenceBaseline, _require_probability, brier
from factorylab.settlement.standing import ConsequenceStanding
from factorylab.settlement.vocabulary import (
    RETURN_PAID_OFF,
    UNOBSERVABLE,
    Observer,
    Predicate,
    WindowFacts,
    _validate_params,
)

# A commitment whose question its own base rate already answers settles under this
# definition: observed, recorded in the base rate, and worth no standing.
UNINFORMATIVE_DEFINITION = "uninformative-baseline-v1"
UNINFORMATIVE_REASON = "uninformative_baseline"


@dataclass(frozen=True)
class PredicateForecast(Forecast):
    """A population forecast seals its exact predicate definition alongside its parameters.

    ``window_cursor`` seals how much of the open measurement window had already
    happened when the claim was made, so resolution reads only what the window
    accumulated after it.
    """

    predicate: Predicate | None = None
    window_cursor: dict | None = None

    def __post_init__(self) -> None:
        if self.predicate is None or self.predicate.code is None:
            raise ValueError("population forecast requires a registered predicate")
        _validate_params(self.predicate_id, self.params, predicate=self.predicate)
        # Reuse the seed record's identity, probability, event and immutable-parameter checks.
        checked = Forecast(self.handle, self.evaluator_id, self.about_handle, "wallet_up",
                           self.params, self.q, self.made_at_event, self.due_at_event, self.seal,
                           self.due_at_tick)
        object.__setattr__(self, "params", checked.params)
        if self.window_cursor is not None:
            if not isinstance(self.window_cursor, Mapping):
                raise ValueError("window_cursor must mark a position in the public window")
            object.__setattr__(self, "window_cursor", _freeze(self.window_cursor))


@dataclass(frozen=True)
class Settled:
    """A result preserves original attribution; censored outcomes carry no y or score."""

    handle: str
    evaluator_id: str
    about_handle: str
    predicate_id: str
    y: int | None
    brier: float | None
    baseline_brier: float | None
    status: SettleStatus
    marked: bool = False
    # The documented reason this due commitment is not an eligible sample for
    # ``avoidably_unresolved_share``: never a silent zero, always a reason.
    excluded: str | None = None
    # The learning receipt this settlement wrote (§6.A): one assessment of one
    # decision, addressable on its own.
    receipt: str | None = None


@dataclass(frozen=True)
class SettledVerdict:
    """One verdict scored against the measured outcome of the decision it judged."""

    evaluator_id: str
    about_handle: str
    q: float
    outcome: float
    brier: float
    baseline_brier: float


def baseline_key(forecast: Forecast) -> str:
    """Name the base rate a forecast is scored against, separating population versions.

    A seed predicate has one fixed meaning, so its id is its base rate. A
    population definition can be replaced, and the replacement is a different
    claim: it starts its own prevalence history rather than inheriting the rate
    its predecessor accumulated.
    """
    if isinstance(forecast, PredicateForecast):
        return f"{forecast.predicate_id}@{forecast.predicate.version}"
    return forecast.predicate_id


def _question(forecast: Forecast, key: str) -> str:
    """Name the one question a forecast answers: its predicate version, subject,
    parameters and sealed interval (and, for a population predicate, the window
    position it was sealed at). Two judges forecasting it make one observation."""
    cursor = getattr(forecast, "window_cursor", None)
    return canonical({"key": key, "about": forecast.about_handle,
                      "params": dict(forecast.params), "made": forecast.made_at_event,
                      "due": forecast.due_at_event,
                      "cursor": dict(cursor) if cursor is not None else None}).decode()


class FactsDeferred(Exception):
    """The world cannot read a due forecast's facts now, only later: nothing is settled.

    Raised by a ``facts_for`` whose read the world's own limits defer (a request
    budget spent for this minute). Deferral is not an absence: the forecast stays due
    and is settled on a later pass, never censored for it.
    """


def normative_brier(q: float, outcome: float) -> float:
    """Return 1 - (q - outcome)^2 in [0, 1] for a probability and a unit-interval outcome."""
    _require_probability(q, "q")
    _require_probability(outcome, "outcome")
    return 1.0 - (q - outcome) ** 2


class Settler:
    """Each due forecast is scored at most once and missing facts never become performance.

    Every registered predicate a judge forecast trains consequence standing, and
    every settled claim counts once. No charter card weights it: the realized
    consequence signal "sits outside the factory's input entirely" (essay
    II.III.b; ruling R1).
    """

    def __init__(
        self,
        book: ForecastBook,
        queue: DecisionQueue,
        standing: ConsequenceStanding,
        baseline: PrevalenceBaseline,
        observer: Observer,
        receipts: ReceiptBook | None = None,
    ) -> None:
        self.__book = book
        # Where the four settlement objects are written. A settler built without
        # a book (a unit test scoring one verdict) simply records nothing.
        self.__receipts = receipts if receipts is not None else getattr(book, "receipts", None)
        self.__queue = queue
        self.__standing = standing
        self.__baseline = baseline
        self.__observer = observer
        # forecast handle -> the documented reason its settlement is an excluded
        # sample, read once by the measurement pass that records the sample row.
        self.__excluded: dict[str, str] = {}
        # about_handle -> baseline q before that return's outcome entered the base rate
        self.__snapshots: dict[str, float] = {}
        # about_handle -> the outcome already counted in the base rate, once per return
        # (a binary payoff, or a verdict key's fractional unblamed target)
        self.__recorded: dict[str, float] = {}

    def settle_due(
        self, n: int, facts_for: Callable[[Forecast], WindowFacts | None], *,
        tick: int | None = None, on_deferred: Callable[[int], None] | None = None,
    ) -> list[Settled]:
        """Return due outcomes in seal order; only accepted observed settlements train history.

        Snapshot before record, as ``settle_verdict`` does: every forecast of
        one question is scored against the base rate as it stood before that
        question's answer entered it, and one question is one observation, entering
        the base rate once. A question is one predicate version over one subject,
        one parameter set and one sealed interval (and, for a population predicate,
        one sealed window position); its forecasts are all due together, so the
        snapshot lives for this pass only.

        A ``facts_for`` that raises ``FactsDeferred`` stops the pass: that forecast and
        every later one stay due, untouched, for a later pass, and ``on_deferred`` is
        told how many were left.
        """
        results = []
        # question -> (baseline q, uninformative) as it stood before its answer
        before: dict[str, tuple[float, bool]] = {}
        answered: set[str] = set()  # questions whose one observation is recorded
        due = self.__book.due(n, tick)
        for index, forecast in enumerate(due):
            if forecast.predicate_id == RETURN_PAID_OFF.id:
                continue
            try:
                facts = facts_for(forecast)
            except FactsDeferred:
                if on_deferred is not None:
                    on_deferred(sum(1 for f in due[index:]
                                    if f.predicate_id != RETURN_PAID_OFF.id))
                break
            excluded = None
            if facts is UNOBSERVABLE:
                # The owner documented that the world never offered the fact and
                # is not at fault for it. The commitment still settles censored;
                # it is simply not an eligible sample for accountable resolution.
                facts, excluded = None, "external_unobservable"
            y = score = baseline_score = None
            status = SettleStatus.CENSORED
            if facts is not None:
                if isinstance(forecast, PredicateForecast):
                    y = self.__observer.observe(forecast.predicate_id, forecast.params, facts,
                                                version=forecast.predicate.version)
                else:
                    y = self.__observer.observe(forecast.predicate_id, forecast.params, facts)
            key = baseline_key(forecast)
            question = _question(forecast, key)
            if question not in before:
                before[question] = (self.__baseline.baseline_q(key),
                                    self.__baseline.uninformative(key))
            baseline_q, easy = before[question]
            # Easy questions do not pay. A predicate the world has already
            # answered — a base rate at or beyond the bound over real support —
            # is not a claim anyone can be right about, so it is observed,
            # recorded in the base rate, and worth no standing at all.
            uninformative = y is not None and easy
            definition = "brier-v1"
            if y is not None and not uninformative:
                baseline_score = brier(baseline_q, y)
                score = brier(forecast.q, y)
                status = SettleStatus.SETTLED
            elif uninformative:
                status, definition = SettleStatus.INAPPLICABLE, UNINFORMATIVE_DEFINITION
                excluded = UNINFORMATIVE_REASON
            # A rejected queue/ledger write must not contaminate history on a later retry.
            self.__queue.settle(
                forecast.handle,
                channel="consequence",
                score=0.0 if score is None else score,
                status=status,
                definition_version=definition,
                sampling_ref=None,
            )
            if y is not None and question not in answered:
                self.__baseline.record(key, y)
                answered.add(question)
            if score is not None:
                self.__standing.record(forecast.evaluator_id, score, baseline_score)
                self.__standing.set_requested(
                    forecast.evaluator_id, self.__book.requested(forecast.evaluator_id))
            self.__book.mark_settled(forecast.handle)
            receipt = self.__learning_receipt(
                forecast, y=y, score=score, baseline=baseline_score, definition=definition,
                reason=(UNINFORMATIVE_REASON if uninformative else
                        excluded if score is None else None),
            )
            results.append(
                Settled(
                    forecast.handle,
                    forecast.evaluator_id,
                    forecast.about_handle,
                    forecast.predicate_id,
                    y,
                    score,
                    baseline_score,
                    status,
                    excluded=excluded,
                    receipt=receipt,
                )
            )
            if excluded is not None:
                self.__excluded[forecast.handle] = excluded
        return results

    def __learning_receipt(self, forecast: Forecast, *, y, score, baseline, definition,
                           reason: str | None, sampling_ref: str | None = None) -> str | None:
        """Write this settlement's assessment as its own addressable object (§6.A)."""
        if self.__receipts is None:
            return None
        return self.__receipts.record(LearningReceipt(
            handle=forecast.handle,
            assessed=forecast.evaluator_id,
            scoring_rule="brier",
            rule_version=definition,
            horizon=forecast.due_at_event - forecast.made_at_event,
            outcome=y,
            score=score,
            baseline=baseline,
            sampling_ref=sampling_ref,
            reason=(reason or "no observed fact") if score is None else None,
        ))

    def receipts(self) -> ReceiptBook | None:
        """The book the four settlement objects are written to, if this settler has one."""
        return self.__receipts

    def excluded(self, handle: str) -> str | None:
        """Take the documented exclusion recorded for one settlement, once.

        ``charter.measurement`` reads it when it writes that settlement's sample
        row, in the same pass that settled it; nothing else needs it afterwards,
        so it is not retained and cannot grow without bound.
        """
        return self.__excluded.pop(handle, None)

    def score_verdict(self, *, about_handle: str, q: float, outcome: float,
                      key: str) -> tuple[float, float]:
        """Score one verdict against a provisional outcome, recording nothing.

        Anticipatory settlement (essay II.IV.b): a verdict's reward is scored on its
        return's mark before the world fixes the outcome. Guarantees the same
        pre-outcome base rate ``settle_verdict`` will use for this decision (the
        snapshot is taken here if it was not), and that neither standing nor the base
        rate moves: the final measurement records both, once. Returns
        (brier, baseline_brier), higher is better.
        """
        _require_probability(outcome, "outcome")
        question = f"{key}:{about_handle}"
        baseline_q = self.__snapshots.get(question)
        if baseline_q is None:
            baseline_q = self.__snapshots[question] = self.__baseline.baseline_q(key)
        return normative_brier(q, outcome), normative_brier(baseline_q, outcome)

    def settle_verdict(
        self, *, evaluator_id: str, about_handle: str, q: float, outcome: float, key: str
    ) -> SettledVerdict:
        """Score one verdict against the measured outcome of the decision it judged.

        Ruling R1: a verdict in [0, 1] is also a prediction. ``outcome`` is what the
        world measured (``return_paid_off`` as 0 or 1, a declined trade's
        opportunity price, or the consequence score of an evaluator decision a meta
        graded), and ``key`` names that kind of outcome's base rate. Every verdict
        about one decision is scored against the base rate as it stood before that
        decision's outcome entered it, and the outcome enters it once: a judge that
        only repeats the base rate has no excess skill. The score trains the judge's
        verdict skill; coverage is untouched.
        """
        _require_probability(outcome, "outcome")
        question = f"{key}:{about_handle}"
        baseline_q = self.__snapshots.get(question)
        if baseline_q is None:
            baseline_q = self.__snapshots[question] = self.__baseline.baseline_q(key)
        score = normative_brier(q, outcome)
        baseline_score = normative_brier(baseline_q, outcome)
        self.__standing.record_verdict(evaluator_id, score, baseline_score)
        if question not in self.__recorded:
            self.__recorded[question] = outcome
            self.__baseline.record_fraction(key, outcome)
        return SettledVerdict(evaluator_id, about_handle, q, outcome, score, baseline_score)
