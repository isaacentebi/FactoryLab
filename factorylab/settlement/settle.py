"""Due commitments receive one original-handle outcome, with missing facts left unscored."""

from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass

from factorylab.kernel.ledger import canonical
from factorylab.kernel.queue import DecisionQueue, SettleStatus
from factorylab.kernel.registry import _freeze
from factorylab.settlement.fidelity import FidelityObjection, parse_objection
from factorylab.settlement.forecast import Forecast, ForecastBook
from factorylab.settlement.lots import Payoff
from factorylab.settlement.receipts import Adjudication, LearningReceipt, ReceiptBook
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

# The verdict's own outside anchor: the base rate of returns the charter did not blame.
VERDICT_NOT_BLAMED = "verdict_not_blamed"

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
                           self.params, self.q, self.made_at_event, self.due_at_event, self.seal)
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
    """One verdict scored against the charter's realised blame on the return it judged."""

    evaluator_id: str
    about_handle: str
    q: float
    share: float
    outcome: float
    brier: float
    baseline_brier: float
    # A fidelity objection the same return carried, scored the same way against
    # the same fact. It settles nothing on its own.
    objection: FidelityObjection | None = None
    objection_brier: float | None = None
    objection_baseline_brier: float | None = None


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


def normative_brier(q: float, outcome: float) -> float:
    """Return 1 - (q - outcome)^2 in [0, 1] for a probability and a unit-interval outcome."""
    _require_probability(q, "q")
    _require_probability(outcome, "outcome")
    return 1.0 - (q - outcome) ** 2


class Settler:
    """Each due forecast is scored at most once and missing facts never become performance.

    Every registered predicate a judge forecast trains consequence standing, at
    the weight the charter's cards give a claim about that return's scope
    (``settlement.weights``). ``return_paid_off`` is still a kernel fact — cash
    settlement is immutable — and is now an ordinary forecastable predicate with
    no standing privilege of its own: with no card naming a scope every claim
    counts equally, and removing a card removes its effect on standing entirely.
    """

    def __init__(
        self,
        book: ForecastBook,
        queue: DecisionQueue,
        standing: ConsequenceStanding,
        baseline: PrevalenceBaseline,
        observer: Observer,
        weight_for: Callable[[Forecast], float] | None = None,
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
        # The charter's weight on one settled claim; without one every claim
        # counts equally, which is what a charter with no scoped card means.
        self.__weight_for = weight_for
        # judge return handle -> the fidelity objection that return carried
        self.__objections: dict[str, FidelityObjection] = {}
        # forecast handle -> the documented reason its settlement is an excluded
        # sample, read once by the measurement pass that records the sample row.
        self.__excluded: dict[str, str] = {}
        # about_handle -> baseline q before that return's outcome entered the base rate
        self.__snapshots: dict[str, float] = {}
        # about_handle -> the outcome already counted in the base rate, once per return
        # (a binary payoff, or a verdict key's fractional unblamed target)
        self.__recorded: dict[str, float] = {}

    def settle_due(
        self, n: int, facts_for: Callable[[Forecast], WindowFacts | None]
    ) -> list[Settled]:
        """Return due outcomes in seal order; only accepted observed settlements train history.

        Snapshot before record, as ``settle_consequences`` does: every forecast of
        one question is scored against the base rate as it stood before that
        question's answer entered it, and one question is one observation, entering
        the base rate once. A question is one predicate version over one subject,
        one parameter set and one sealed interval (and, for a population predicate,
        one sealed window position); its forecasts are all due together, so the
        snapshot lives for this pass only.
        """
        results = []
        # question -> (baseline q, uninformative) as it stood before its answer
        before: dict[str, tuple[float, bool]] = {}
        answered: set[str] = set()  # questions whose one observation is recorded
        for forecast in self.__book.due(n):
            if forecast.predicate_id == RETURN_PAID_OFF.id:
                continue
            facts = facts_for(forecast)
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
                # No predicate is privileged: this claim trains the judge's
                # standing at the charter's weight, like any other.
                self.__standing.record(forecast.evaluator_id, score, baseline_score,
                                       self.__weight(forecast))
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

    def __weight(self, forecast: Forecast) -> float:
        """The charter's weight on this claim, or an equal 1.0 without a charter."""
        return 1.0 if self.__weight_for is None else float(self.__weight_for(forecast))

    def settle_consequences(
        self, payoff_for: Callable[[str], Payoff | None]
    ) -> list[Settled]:
        """Score kernel commitments as soon as their immutable return outcome is available.

        Every forecast about one payoff outcome is scored against the same
        pre-outcome prevalence baseline, whether it is scored in this call or a
        later one: a judge sealed on the handle of an antagonist's self-forecast
        is never compared against a base rate that already holds the outcome.
        One return is one observation: however many forecasts share an outcome,
        it enters the prevalence rate once, after the forecasts scored here.
        """
        results = []
        outcomes: dict[str, int] = {}
        try:
            for forecast in self.__book.pending(predicate_id=RETURN_PAID_OFF.id):
                payoff = payoff_for(forecast.about_handle)
                if payoff is None:
                    continue
                if payoff.handle != forecast.about_handle:
                    raise ValueError("consequence belongs to a different return")
                if payoff.censored is not None:
                    # The world was asked and did not answer, and the owner could
                    # not have made it answer (R4-C). The OUTCOME CONTRACT's third
                    # answer is "unknown: the necessary observation is
                    # unavailable", so there is no fact here for anyone to be
                    # right or wrong about: every commitment on this return
                    # settles censored, trains no standing, enters no base rate,
                    # and carries the documented reason that keeps it out of the
                    # accountable-resolution sample.
                    self.__queue.settle(
                        forecast.handle, channel="consequence", score=0.0,
                        status=SettleStatus.CENSORED, definition_version="censored-v1",
                        sampling_ref=None,
                    )
                    self.__book.mark_settled(forecast.handle)
                    self.__excluded[forecast.handle] = payoff.censored
                    results.append(Settled(
                        forecast.handle, forecast.evaluator_id, forecast.about_handle,
                        forecast.predicate_id, None, None, None, SettleStatus.CENSORED,
                        False, excluded=payoff.censored,
                        receipt=self.__learning_receipt(
                            forecast, y=None, score=None, baseline=None,
                            definition="censored-v1", reason=payoff.censored,
                            sampling_ref=payoff.handle),
                    ))
                    continue
                score = brier(forecast.q, payoff.y)
                baseline = brier(self.__baseline_before(payoff.handle), payoff.y)
                self.__book.record_consequence(
                    forecast.handle,
                    {**asdict(payoff), "handle": forecast.handle, "about_handle": payoff.handle,
                     "predicate_id": forecast.predicate_id, "q": forecast.q,
                     "brier": score, "baseline_brier": baseline},
                )
                self.__queue.settle(
                    forecast.handle,
                    channel="consequence",
                    score=score,
                    status=SettleStatus.SETTLED,
                    definition_version="brier-v1",
                    sampling_ref=None,
                )
                if payoff.handle not in self.__recorded:
                    outcomes[payoff.handle] = payoff.y
                self.__standing.record(forecast.evaluator_id, score, baseline,
                                       self.__weight(forecast))
                self.__book.mark_settled(forecast.handle)
                self.__standing.set_requested(
                    forecast.evaluator_id, self.__book.requested(forecast.evaluator_id),
                )
                results.append(
                    Settled(forecast.handle, forecast.evaluator_id, forecast.about_handle,
                            forecast.predicate_id, payoff.y, score, baseline,
                            SettleStatus.SETTLED, payoff.marked,
                            receipt=self.__learning_receipt(
                                forecast, y=payoff.y, score=score, baseline=baseline,
                                definition="brier-v1", reason=None,
                                sampling_ref=payoff.handle))
                )
        finally:
            # Scored outcomes reach the base rate even if a later forecast's write fails:
            # a settled forecast is never rescored, so its return's observation is due now.
            for about_handle, y in outcomes.items():
                self.__recorded[about_handle] = y
                self.__baseline.record(RETURN_PAID_OFF.id, y)
        return results

    def record_objection(self, judge_handle: str, entries, charter=None, *,
                         evaluator_id: str | None = None,
                         about_handle: str | None = None) -> FidelityObjection | None:
        """Validate and ledger the fidelity objection one judged return carried, if any.

        ``entries`` is any sequence of the judge's own answers as
        ``{"handle", "outputs"}`` mappings; the objection is read from the entry
        whose handle is this judge's return. A malformed or unplaceable objection
        is refused, and the refusal is ledgered with its reason: the verdict
        itself is untouched and nothing is scored from a claim the charter
        cannot place. Recording is idempotent per judge handle.
        """
        if judge_handle in self.__objections:
            return self.__objections[judge_handle]
        raw = None
        for entry in entries or ():
            if not isinstance(entry, Mapping) or entry.get("handle") != judge_handle:
                continue
            outputs = entry.get("outputs")
            if isinstance(outputs, Mapping):
                raw = outputs.get("fidelity_objection")
            break
        if raw is None:
            return None
        common = {"handle": judge_handle, "evaluator_id": evaluator_id,
                  "about_handle": about_handle}
        try:
            objection = parse_objection(raw, charter=charter)
        except ValueError as exc:
            self.__book.record_objection({**common, "accepted": False, "reason": str(exc)})
            return None
        self.__book.record_objection({**common, "accepted": True, **objection.as_dict()})
        self.__objections[judge_handle] = objection
        # An accepted objection is an open adjudication from the moment it is
        # made: a contestable interpretation with no finding on it yet. The
        # challenged proxy never scores it (§7); an independent adjudicator does.
        if self.__receipts is not None and evaluator_id and about_handle:
            self.__receipts.record(Adjudication(
                value=objection.value, measurement=objection.measurement,
                evidence=objection.evidence, objector=evaluator_id,
                objection_handle=judge_handle, about_handle=about_handle,
                uncertainty=objection.uncertainty,
            ))
        return objection

    def adjudication_for(self, judge_handle: str) -> Adjudication | None:
        """The open or resolved adjudication this judge's objection became, if any."""
        if self.__receipts is None:
            return None
        return next((a for a in self.__receipts.all("adjudication")
                     if a.objection_handle == judge_handle), None)

    def resolve_adjudication(self, adjudication: Adjudication, *, adjudicator: str,
                             upheld: bool, finding: str) -> tuple[Adjudication, str | None]:
        """Record an independent finding and the objector's own learning receipt.

        The objector stated a probability that its objection was right; the
        adjudicator's finding is the fact that claim is scored against, by the
        same proper score every other claim is scored by. It trains nothing by
        itself: the receipt is the assessment, and what the card is worth is the
        population's to decide through a challenge.
        """
        resolved = adjudication.resolved(adjudicator=adjudicator, upheld=upheld, finding=finding)
        if self.__receipts is None:
            return resolved, None
        self.__receipts.record(resolved)
        outcome = 1 if upheld else 0
        receipt = self.__receipts.record(LearningReceipt(
            handle=adjudication.objection_handle,
            assessed=adjudication.objector,
            scoring_rule="brier",
            rule_version="adjudication-v1",
            horizon=None,
            outcome=outcome,
            score=brier(adjudication.confidence, outcome),
            baseline=None,
            sampling_ref=resolved.id,
            reason=None,
        ))
        return resolved, receipt

    def objection(self, judge_handle: str) -> FidelityObjection | None:
        """The accepted objection this judge's return carried, if it carried one."""
        return self.__objections.get(judge_handle)

    def settle_verdict(
        self, *, evaluator_id: str, about_handle: str, q: float, share: float,
        judge_handle: str | None = None
    ) -> SettledVerdict:
        """Score one verdict against the charter's realised blame on the return it judged.

        The realised normative outcome is 1 minus the return's attributed share
        of its window's charter blame (1 when nothing was attributed). Every
        verdict about one return is scored against the same pre-outcome base
        rate, whether it settles in this call or a later one, and the return's
        fractional outcome enters that base rate once: the baseline learns the
        same quantity the judge is scored on, so a judge that only repeats the
        constant share of blame every return carries has no excess skill. The
        score trains the judge's verdict skill; coverage is untouched.
        """
        _require_probability(share, "share")
        outcome = 1.0 - share
        key = f"{VERDICT_NOT_BLAMED}:{about_handle}"
        baseline_q = self.__snapshots.get(key)
        if baseline_q is None:
            baseline_q = self.__snapshots[key] = self.__baseline.baseline_q(VERDICT_NOT_BLAMED)
        score = normative_brier(q, outcome)
        baseline_score = normative_brier(baseline_q, outcome)
        self.__standing.record_verdict(evaluator_id, score, baseline_score)
        # The challenged proxy cannot certify or refute its own fidelity.
        # Keep the claim in the returned evidence; await independent adjudication.
        objection = self.__objections.pop(judge_handle, None) if judge_handle else None
        objection_score = objection_baseline = None
        if key not in self.__recorded:
            self.__recorded[key] = outcome
            self.__baseline.record_fraction(VERDICT_NOT_BLAMED, outcome)
        return SettledVerdict(evaluator_id, about_handle, q, share, outcome, score,
                              baseline_score, objection, objection_score, objection_baseline)

    def __baseline_before(self, about_handle: str) -> float:
        """The payoff base rate as it stood before this return's outcome was first scored,
        fixed on first use so later forecasts about the same outcome share it."""
        q = self.__snapshots.get(about_handle)
        if q is None:
            q = self.__snapshots[about_handle] = self.__baseline.baseline_q(RETURN_PAID_OFF.id)
        return q
