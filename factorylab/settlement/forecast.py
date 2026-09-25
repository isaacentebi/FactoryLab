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
    #: The world tick this forecast comes due at (essay II.IV.b-c; time audit T3): a
    #: horizon counts ticks, never internal events. ``made_at_event`` still indexes
    #: the event record its facts are read from. None (a forecast sealed before the
    #: tick clock) comes due at ``due_at_event``.
    due_at_tick: int | None = None

    def __post_init__(self) -> None:
        for value in (self.handle, self.evaluator_id, self.about_handle):
            _require_id(value)
        _require_probability(self.q, "q")
        _require_event_index(self.made_at_event, "made_at_event")
        _require_event_index(self.due_at_event, "due_at_event")
        if self.due_at_event <= self.made_at_event:
            raise ValueError("due_at_event must exceed made_at_event")
        if self.due_at_tick is not None:
            _require_event_index(self.due_at_tick, "due_at_tick")
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
        # Wave 17b: forecasts released from the book, per [evaluator, predicate]: what
        # ``requested`` still counts once their commitments are gone.
        self.__released: dict[tuple[str, str], int] = {}
        self.__open_cache: tuple | None = None

    def _open(self) -> dict[str, None]:
        """The unsettled handles in seal order, kept as forecasts are sealed and settled.

        Rebuilt whenever the forecast map or the settled set is a different object
        than the one it was built from (a checkpoint restore replaces both), so it
        can never describe a book it was not built from.
        """
        cache = self.__open_cache
        if cache is None or cache[0] is not self.__forecasts or cache[1] is not self.__settled:
            settled = self.__settled
            cache = (self.__forecasts, settled,
                     {handle: None for handle in self.__forecasts if handle not in settled})
            self.__open_cache = cache
        return cache[2]

    def seal(self, forecast: Forecast) -> Forecast:
        """Persist before admission; identical retries preserve the seal, order and counts."""
        if not isinstance(forecast, Forecast):
            raise TypeError("Forecast required")
        payload = {
            field.name: getattr(forecast, field.name)
            for field in fields(forecast)
            # A forecast without a tick due date seals exactly as it always did.
            if field.name != "seal"
            and not (field.name == "due_at_tick" and forecast.due_at_tick is None)
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
        self._open()[sealed.handle] = None
        self.__requested[sealed.evaluator_id] = self.requested(sealed.evaluator_id) + 1
        return sealed

    def due(self, n: int, tick: int | None = None) -> list[Forecast]:
        """Return only unsettled forecasts due by event n or world tick ``tick``, in seal order.

        A forecast with a tick due date is due by the tick alone (time audit T3);
        one without is due by the event index, as it was sealed.
        """
        _require_event_index(n, "n")
        if tick is not None:
            _require_event_index(tick, "tick")
        forecasts = self.__forecasts
        return [
            forecast
            for forecast in map(forecasts.__getitem__, self._open())
            if (forecast.due_at_event <= n if forecast.due_at_tick is None or tick is None
                else forecast.due_at_tick <= tick)
        ]

    def mark_settled(self, handle: str) -> None:
        """Permanently remove a known handle from due results; repeated marks are harmless."""
        if handle not in self.__forecasts:
            raise KeyError(handle)
        self.__settled.add(handle)
        self._open().pop(handle, None)

    def pending(self, *, predicate_id: str | None = None) -> list[Forecast]:
        """Return unsettled commitments in seal order, optionally restricted by predicate."""
        forecasts = self.__forecasts
        return [
            f for f in map(forecasts.__getitem__, self._open())
            if predicate_id is None or f.predicate_id == predicate_id
        ]

    def record_consequence(self, handle: str, evidence: dict) -> None:
        """Persist kernel outcome evidence, including mark status, before score delivery."""
        if handle not in self.__forecasts:
            raise KeyError(handle)
        self.__ledger.append({"kind": "forecast.consequence", "handle": handle, **evidence})

    def outstanding(self) -> int:
        """Return the count of sealed forecasts without a book settlement."""
        return len(self.__forecasts) - len(self.__settled)

    def release(self, handles) -> int:
        """Forget settled forecasts whose decisions were released; return how many.

        Wave 17b (essay II.IV.c: a verdict is "consumed ... and then discarded"; what
        persists is aggregates): every reader of the book reads its unsettled
        forecasts or the per-evaluator counts, which are kept. Guarantees an
        unsettled forecast is never forgotten (``ValueError``, nothing changes) and
        that ``due``, ``pending``, ``outstanding`` and ``requested`` answer exactly
        as before.
        """
        gone = [h for h in dict.fromkeys(handles) if h in self.__forecasts]
        if any(h not in self.__settled for h in gone):
            raise ValueError("an unsettled forecast is never released")
        for handle in gone:
            forecast = self.__forecasts.pop(handle)
            self.__settled.discard(handle)
            key = (forecast.evaluator_id, forecast.predicate_id)
            self.__released[key] = self.__released.get(key, 0) + 1
        return len(gone)

    def requested(self, evaluator_id: str, predicate_id: str | None = None) -> int:
        """Return distinct sealed forecasts for this evaluator, including censored ones.

        With a predicate the count is restricted to that predicate's commitments.
        """
        _require_id(evaluator_id)
        if predicate_id is None:
            return self.__requested.get(evaluator_id, 0)
        return self.__released.get((evaluator_id, predicate_id), 0) + sum(
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
