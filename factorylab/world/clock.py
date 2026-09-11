"""Clock and drip event sources.

Both are pure generators over integer nanosecond time. Neither sleeps; the
runtime decides whether to pace them against wall-clock time. The drip source
only *announces* that a deposit is due. Applying it is the wallet's job, which
keeps the kernel the sole authority on money.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from typing import Protocol

from factorylab.world.events import WorldEvent, WorldEventKind

NS_PER_SECOND = 1_000_000_000


class IntervalClock(Protocol):
    interval_ns: int

    def set_interval(self, interval_ns: int) -> None: ...


class ClockIterator(Iterator[WorldEvent]):
    """An injected event iterator retains access to its clock's mutable interval."""

    def __init__(self, clock: IntervalClock, events: Iterator[WorldEvent]) -> None:
        self._clock = clock
        self._events = events

    @property
    def interval_ns(self) -> int:
        return self._clock.interval_ns

    def set_interval(self, interval_ns: int) -> None:
        """Apply interval validation and next-tick scheduling through the owning clock."""
        self._clock.set_interval(interval_ns)

    def __next__(self) -> WorldEvent:
        return next(self._events)


@dataclass
class ClockSource:
    """Emits one ``Tick`` per ``interval_ns`` from ``start_ns`` for ``count`` ticks.

    Guarantees strictly increasing timestamps and exactly ``count`` events.
    """

    start_ns: int
    interval_ns: int
    count: int
    source: str = "clock"

    def __post_init__(self) -> None:
        self.set_interval(self.interval_ns)
        if self.count < 0:
            raise ValueError("count must be non-negative")

    def set_interval(self, interval_ns: int) -> None:
        """Adopt positive integer nanoseconds for the gap after the last yielded tick."""
        if type(interval_ns) is not int or interval_ns <= 0:
            raise ValueError("interval_ns must be positive integer nanoseconds")
        self.interval_ns = interval_ns

    def events(self, drips: Iterator[WorldEvent] | None = None) -> ClockIterator:
        """Return a deterministic stream that retains access to this clock's interval."""
        return ClockIterator(self, self._events(drips))

    def _events(self, drips: Iterator[WorldEvent] | None) -> Iterator[WorldEvent]:
        """Keep ticks deterministic, including interval changes between a drip and a tick.

        Companion drips end with the tick budget. A shortened interval already due
        at the last drip fires there, never retroactively. Ticks win timestamp ties.
        """
        drip = next(drips, None) if drips is not None else None
        last_tick = None
        last_event = self.start_ns
        for i in range(self.count):
            while True:
                ts = self.start_ns if last_tick is None else last_tick + self.interval_ns
                ts = max(ts, last_event)
                if drip is None or drip.ts_ns >= ts:
                    break
                last_event = drip.ts_ns
                yield drip
                drip = next(drips, None)
            last_tick = last_event = ts
            yield WorldEvent(WorldEventKind.TICK, ts, self.source, {"index": i})


@dataclass(frozen=True)
class DripSource:
    """Emits one ``Drip`` announcement per period inside ``[start_ns, end_ns]``.

    The payload carries the scheduled amount in micro-USD as an int so the
    runtime can cross-check the wallet's own schedule. Guarantees no drip is
    announced after ``end_ns``.
    """

    amount_micro_usd: int
    period_ns: int
    start_ns: int
    end_ns: int
    source: str = "drip"

    def __post_init__(self) -> None:
        if self.period_ns <= 0:
            raise ValueError("period_ns must be positive")
        if self.amount_micro_usd < 0:
            raise ValueError("amount must be non-negative")
        if self.end_ns < self.start_ns:
            raise ValueError("end_ns must not precede start_ns")

    def events(self) -> Iterator[WorldEvent]:
        ts = self.start_ns
        while ts <= self.end_ns:
            yield WorldEvent(
                WorldEventKind.DRIP,
                ts,
                self.source,
                {"amount_micro_usd": self.amount_micro_usd},
            )
            ts += self.period_ns


def merge_sources(*streams: Iterator[WorldEvent]) -> Iterator[WorldEvent]:
    """Merge already-sorted event streams into one stream ordered by ``ts_ns``.

    Ties are broken by argument order, so the merge is deterministic for a
    given call. Guarantees the output is non-decreasing in ``ts_ns``.
    """
    import heapq

    heads: list[tuple[int, int, WorldEvent, Iterator[WorldEvent]]] = []
    for idx, stream in enumerate(streams):
        first = next(stream, None)
        if first is not None:
            heads.append((first.ts_ns, idx, first, stream))
    heapq.heapify(heads)
    while heads:
        ts, idx, event, stream = heapq.heappop(heads)
        yield event
        nxt = next(stream, None)
        if nxt is not None:
            if nxt.ts_ns < ts:
                raise ValueError(f"source {idx} emitted a non-monotonic timestamp")
            heapq.heappush(heads, (nxt.ts_ns, idx, nxt, stream))
