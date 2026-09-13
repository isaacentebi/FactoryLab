"""Deterministic ledger-first, single-threaded event delivery."""

from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum

from factorylab.kernel.ledger import Ledger
from factorylab.kernel.registry import _freeze


class EventKind(StrEnum):
    LAUNCH = "Launch"
    TICK = "Tick"
    DRIP = "Drip"
    MARKET_MID = "MarketMid"
    FUNDING = "Funding"
    FILL = "Fill"
    ORDER_REJECTED = "OrderRejected"
    VERDICT = "Verdict"
    META_VERDICT = "MetaVerdict"
    PRODUCER_RETURN = "ProducerReturn"
    FORECAST_SETTLED = "ForecastSettled"
    REGISTERED = "Registered"
    ROUTER_REPLACED = "RouterReplaced"
    RECONCILED = "Reconciled"
    TRANSFER_INTENT = "TransferIntent"
    TERMINATED = "Terminated"


@dataclass(frozen=True)
class Event:
    """Subscribers observe the same immutable, valid event that the ledger recorded."""

    id: str
    kind: EventKind
    ts_ns: int
    payload: dict
    source: str

    def __post_init__(self) -> None:
        if not isinstance(self.id, str) or not self.id or not isinstance(self.source, str):
            raise ValueError("event id and source are required")
        if not self.source or type(self.ts_ns) is not int or self.ts_ns < 0:
            raise ValueError("invalid event source or timestamp")
        if not isinstance(self.payload, dict):
            raise TypeError("event payload must be a dict")
        object.__setattr__(self, "kind", EventKind(self.kind))
        if self.kind is EventKind.META_VERDICT:
            if any(not isinstance(self.payload.get(k), str) or not self.payload[k]
                   for k in ("about", "by")):
                raise ValueError("MetaVerdict requires about and by decision handles")
            tier, score = self.payload.get("tier"), self.payload.get("score")
            if type(tier) is not int or tier < 2:
                raise ValueError("MetaVerdict tier must be an integer >= 2")
            if isinstance(score, bool) or not isinstance(score, int | float) or not 0 <= score <= 1:
                raise ValueError("MetaVerdict score must be in [0, 1]")
        object.__setattr__(self, "payload", _freeze(self.payload))


class Bus:
    """Every accepted event is logged before callbacks, which run in subscription order."""

    def __init__(self, ledger: Ledger) -> None:
        self.__ledger = ledger
        self.__subscribers: dict[EventKind, list[Callable[[Event], None]]] = {}
        self.__pending: deque[Event] = deque()
        self.__delivering = False

    @property
    def ledger(self) -> Ledger:
        """Expose the ledger used before every delivery."""
        return self.__ledger

    def subscribe(self, kind: str, handler: Callable[[Event], None]) -> None:
        """Append one callback to this kind's ordered subscriber list."""
        kind = EventKind(kind)
        if self.__ledger.final:
            raise RuntimeError("world is final")
        if not callable(handler):
            raise TypeError("handler must be callable")
        self.__subscribers.setdefault(kind, []).append(handler)

    @staticmethod
    def _entry(event: Event) -> dict:
        return {"kind": "event", "event": event, "ts": event.ts_ns}

    def publish(self, event: Event) -> None:
        """Log before delivering; reentrant publishes wait until all current subscribers finish."""
        if not isinstance(event, Event):
            raise TypeError("Event required")
        if event.kind == EventKind.TERMINATED:
            raise PermissionError("only Termination may publish Terminated")
        self.__ledger.append(self._entry(event))
        self._deliver(event)

    def _publish_terminal(self, event: Event, authority) -> None:
        self.__ledger._terminate(authority, self._entry(event))
        self._deliver(event)

    def _deliver(self, event: Event) -> None:
        self.__pending.append(event)
        if self.__delivering:
            return
        self.__delivering = True
        errors = []
        try:
            while self.__pending:
                current = self.__pending.popleft()
                for handler in tuple(self.__subscribers.get(current.kind, ())):
                    try:
                        handler(current)
                    except Exception as error:
                        errors.append(error)
        finally:
            self.__delivering = False
        if errors:
            raise ExceptionGroup("event subscribers failed", errors)
