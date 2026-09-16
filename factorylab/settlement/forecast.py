"""Forecast commitments are immutable, canonical, and persisted before book admission."""

import hashlib
from dataclasses import dataclass, fields, replace

from factorylab.kernel.ledger import Ledger, canonical
from factorylab.kernel.queue import DecisionQueue, PropensityRecord
from factorylab.kernel.registry import _freeze
from factorylab.settlement.receipts import ReceiptBook
from factorylab.settlement.scoring import _require_id, _require_probability
from factorylab.settlement.vocabulary import _require_event_index, _validate_params


@dataclass(frozen=True)
class Forecast:
    """Valid forecasts bind immutable parameters and probabilities to a future event index."""

    handle: str
    evaluator_id: str
    about_handle: str
    predicate_id: str
    params: dict
    q: float
    made_at_event: int
    due_at_event: int
    seal: str = ""

    def __post_init__(self) -> None:
        for value in (self.handle, self.evaluator_id, self.about_handle):
            _require_id(value)
        _require_probability(self.q, "q")
        _require_event_index(self.made_at_event, "made_at_event")
        _require_event_index(self.due_at_event, "due_at_event")
        if self.due_at_event <= self.made_at_event:
            raise ValueError("due_at_event must exceed made_at_event")
        _validate_params(self.predicate_id, self.params, kernel=True)
        if not isinstance(self.seal, str):
            raise ValueError("seal must be a string")
        object.__setattr__(self, "params", _freeze(self.params))


class ForecastBook:
    """Each handle has one immutable ledger commitment and at most one book settlement."""

    def __init__(self, ledger: Ledger) -> None:
        self.__ledger = ledger
        # The four settlement objects share the book's ledger: a learning receipt
        # about a forecast is written where the forecast's own seal was written.
        self.receipts = ReceiptBook(ledger)
        self.__forecasts: dict[str, Forecast] = {}
        self.__settled: set[str] = set()
        self.__requested: dict[str, int] = {}

    def seal(self, forecast: Forecast) -> Forecast:
        """Persist before admission; identical retries preserve the seal, order and counts."""
        if not isinstance(forecast, Forecast):
            raise TypeError("Forecast required")
        payload = {
            field.name: getattr(forecast, field.name)
            for field in fields(forecast)
            if field.name != "seal"
        }
        digest = hashlib.sha256(canonical(payload)).hexdigest()
        sealed = replace(forecast, seal=digest)
        existing = self.__forecasts.get(forecast.handle)
        if existing is not None:
            if existing != sealed:
                raise ValueError("forecast handle already has a different commitment")
            return existing
        # Ledger.append supplies ts using the ledger's own injected nanosecond clock.
        self.__ledger.append({"kind": "forecast.seal", **payload, "seal": digest})
        self.__forecasts[sealed.handle] = sealed
        self.__requested[sealed.evaluator_id] = self.requested(sealed.evaluator_id) + 1
        return sealed

    def due(self, n: int) -> list[Forecast]:
        """Return only unsettled forecasts due by n, in their original seal order."""
        _require_event_index(n, "n")
        return [
            forecast
            for handle, forecast in self.__forecasts.items()
            if handle not in self.__settled and forecast.due_at_event <= n
        ]

    def mark_settled(self, handle: str) -> None:
        """Permanently remove a known handle from due results; repeated marks are harmless."""
        if handle not in self.__forecasts:
            raise KeyError(handle)
        self.__settled.add(handle)

    def pending(self, *, predicate_id: str | None = None) -> list[Forecast]:
        """Return unsettled commitments in seal order, optionally restricted by predicate."""
        return [
            f for h, f in self.__forecasts.items()
            if h not in self.__settled and (predicate_id is None or f.predicate_id == predicate_id)
        ]

    def record_consequence(self, handle: str, evidence: dict) -> None:
        """Persist kernel outcome evidence, including mark status, before score delivery."""
        if handle not in self.__forecasts:
            raise KeyError(handle)
        self.__ledger.append({"kind": "forecast.consequence", "handle": handle, **evidence})

    def record_objection(self, evidence: dict) -> None:
        """Persist one fidelity objection before anything scores it (edition 3, C3)."""
        self.__ledger.append({"kind": "fidelity.objection", **evidence})

    def outstanding(self) -> int:
        """Return the count of sealed forecasts without a book settlement."""
        return len(self.__forecasts) - len(self.__settled)

    def requested(self, evaluator_id: str, predicate_id: str | None = None) -> int:
        """Return distinct sealed forecasts for this evaluator, including censored ones.

        With a predicate the count is restricted to that predicate's commitments.
        """
        _require_id(evaluator_id)
        if predicate_id is None:
            return self.__requested.get(evaluator_id, 0)
        return sum(
            f.evaluator_id == evaluator_id and f.predicate_id == predicate_id
            for f in self.__forecasts.values()
        )


def open_forecast_decision(
    queue: DecisionQueue,
    *,
    evaluator_id: str,
    event_id: str,
    q: float,
    deadline_ns: int,
    parent_handle: str | None,
    now_event: int,
    horizon: int,
) -> str:
    """Open a zero-cost consequence decision with exactly replayable one-bucket propensity."""
    _require_probability(q, "q")
    _require_event_index(now_event, "now_event")
    _require_event_index(horizon, "horizon", positive=True)
    bucket = str(round(q, 1))
    propensity = PropensityRecord(
        action_ids=(bucket,),
        probs=(1.0,),
        chosen=bucket,
        rng_seed=0,
        learner_id=evaluator_id,
        learner_state_hash=hashlib.sha256(bucket.encode("utf-8")).hexdigest(),
    )
    return queue.open(
        actor=evaluator_id,
        event_id=event_id,
        propensity=propensity,
        channel="consequence",
        deadline_ns=deadline_ns,
        parent_handle=parent_handle,
        cost_ceiling=0,
    )
