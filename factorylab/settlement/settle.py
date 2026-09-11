"""Due commitments receive one original-handle outcome, with missing facts left unscored."""

from collections.abc import Callable
from dataclasses import dataclass

from factorylab.kernel.queue import DecisionQueue, SettleStatus
from factorylab.settlement.forecast import Forecast, ForecastBook
from factorylab.settlement.scoring import PrevalenceBaseline, brier
from factorylab.settlement.standing import ConsequenceStanding
from factorylab.settlement.vocabulary import Observer, WindowFacts


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


class Settler:
    """Each due forecast is scored at most once and missing facts never become performance."""

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

    def settle_due(
        self, n: int, facts_for: Callable[[Forecast], WindowFacts | None]
    ) -> list[Settled]:
        """Return due outcomes in seal order; only accepted observed settlements train history."""
        results = []
        for forecast in self.__book.due(n):
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
                self.__standing.record(forecast.evaluator_id, score, baseline_score)
            self.__book.mark_settled(forecast.handle)
            self.__standing.set_requested(
                forecast.evaluator_id, self.__book.requested(forecast.evaluator_id)
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
                )
            )
        return results
