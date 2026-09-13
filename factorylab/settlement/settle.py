"""Due commitments receive one original-handle outcome, with missing facts left unscored."""

from collections.abc import Callable
from dataclasses import asdict, dataclass

from factorylab.kernel.queue import DecisionQueue, SettleStatus
from factorylab.settlement.forecast import Forecast, ForecastBook
from factorylab.settlement.lots import Payoff
from factorylab.settlement.scoring import PrevalenceBaseline, _require_probability, brier
from factorylab.settlement.standing import ConsequenceStanding
from factorylab.settlement.vocabulary import RETURN_PAID_OFF, Observer, WindowFacts

# The verdict's own outside anchor: the base rate of returns the charter did not blame.
VERDICT_NOT_BLAMED = "verdict_not_blamed"


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


def normative_brier(q: float, outcome: float) -> float:
    """Return 1 - (q - outcome)^2 in [0, 1] for a probability and a unit-interval outcome."""
    _require_probability(q, "q")
    _require_probability(outcome, "outcome")
    return 1.0 - (q - outcome) ** 2


class Settler:
    """Each due forecast is scored at most once and missing facts never become performance.

    Only the kernel payoff commitment (``return_paid_off``) trains consequence
    standing; optional public-predicate forecasts settle to their handles and the
    prevalence baseline but never enter a judge's selection weight.
    """

    def __init__(
        self,
        book: ForecastBook,
        queue: DecisionQueue,
        standing: ConsequenceStanding,
        baseline: PrevalenceBaseline,
        observer: Observer,
    ) -> None:
        self.__book = book
        self.__queue = queue
        self.__standing = standing
        self.__baseline = baseline
        self.__observer = observer
        # about_handle -> baseline q before that return's outcome entered the base rate
        self.__snapshots: dict[str, float] = {}
        # about_handle -> the outcome already counted in the base rate, once per return
        self.__recorded: dict[str, int] = {}

    def settle_due(
        self, n: int, facts_for: Callable[[Forecast], WindowFacts | None]
    ) -> list[Settled]:
        """Return due outcomes in seal order; only accepted observed settlements train history."""
        results = []
        for forecast in self.__book.due(n):
            if forecast.predicate_id == RETURN_PAID_OFF.id:
                continue
            facts = facts_for(forecast)
            y = score = baseline_score = None
            status = SettleStatus.CENSORED
            if facts is not None:
                y = self.__observer.observe(forecast.predicate_id, forecast.params, facts)
                baseline_score = self.__baseline.baseline_brier(forecast.predicate_id, y)
                score = brier(forecast.q, y)
                status = SettleStatus.SETTLED
            # A rejected queue/ledger write must not contaminate history on a later retry.
            self.__queue.settle(
                forecast.handle,
                channel="consequence",
                score=0.0 if score is None else score,
                status=status,
                definition_version="brier-v1",
                sampling_ref=None,
            )
            if y is not None:
                self.__baseline.record(forecast.predicate_id, y)
            self.__book.mark_settled(forecast.handle)
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
                )
            )
        return results

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
                self.__standing.record(forecast.evaluator_id, score, baseline)
                self.__book.mark_settled(forecast.handle)
                self.__standing.set_requested(
                    forecast.evaluator_id,
                    self.__book.requested(forecast.evaluator_id, RETURN_PAID_OFF.id),
                )
                results.append(
                    Settled(forecast.handle, forecast.evaluator_id, forecast.about_handle,
                            forecast.predicate_id, payoff.y, score, baseline,
                            SettleStatus.SETTLED, payoff.marked)
                )
        finally:
            # Scored outcomes reach the base rate even if a later forecast's write fails:
            # a settled forecast is never rescored, so its return's observation is due now.
            for about_handle, y in outcomes.items():
                self.__recorded[about_handle] = y
                self.__baseline.record(RETURN_PAID_OFF.id, y)
        return results

    def settle_verdict(
        self, *, evaluator_id: str, about_handle: str, q: float, share: float
    ) -> SettledVerdict:
        """Score one verdict against the charter's realised blame on the return it judged.

        The realised normative outcome is 1 minus the return's attributed share
        of its window's charter blame (1 when nothing was attributed). Every
        verdict about one return is scored against the same pre-outcome base
        rate of unblamed returns, whether it settles in this call or a later
        one, and the return's blame enters that base rate once. The score trains
        the judge's verdict skill; coverage is untouched.
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
        if key not in self.__recorded:
            unblamed = int(share == 0)
            self.__recorded[key] = unblamed
            self.__baseline.record(VERDICT_NOT_BLAMED, unblamed)
        return SettledVerdict(evaluator_id, about_handle, q, share, outcome, score,
                              baseline_score)

    def __baseline_before(self, about_handle: str) -> float:
        """The payoff base rate as it stood before this return's outcome was first scored,
        fixed on first use so later forecasts about the same outcome share it."""
        q = self.__snapshots.get(about_handle)
        if q is None:
            q = self.__snapshots[about_handle] = self.__baseline.baseline_q(RETURN_PAID_OFF.id)
        return q
