"""Clock and drip event sources.

Both are pure generators over integer nanosecond time. Neither sleeps; the
runtime decides whether to pace them against wall-clock time. The drip source
only *announces* that a deposit is due. Applying it is the wallet's job, which
keeps the kernel the sole authority on money.
"""

from __future__ import annotations

from collections import deque
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
    index: int = 0
    last_ns: int | None = None
    last_event_ns: int | None = None

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
        while self.index < self.count:
            while True:
                ts = self.start_ns if self.last_ns is None else self.last_ns + self.interval_ns
                if self.last_event_ns is not None:
                    ts = max(ts, self.last_event_ns)
                if drip is None or drip.ts_ns >= ts:
                    break
                self.last_event_ns = drip.ts_ns
                yield drip
                drip = next(drips, None)
            self.last_ns = self.last_event_ns = ts
            i = self.index
            self.index += 1
            yield WorldEvent(WorldEventKind.TICK, ts, self.source, {"index": i})

    def state(self) -> dict:
        """Retain the amended interval, budget and exact tick/drip continuation point."""
        return {"start_ns": self.start_ns, "interval_ns": self.interval_ns,
                "count": self.count, "source": self.source, "index": self.index,
                "last_ns": self.last_ns, "last_event_ns": self.last_event_ns}

    @classmethod
    def restore(cls, state: dict) -> ClockSource:
        """Continue the saved clock without replaying already delivered ticks."""
        return cls(**state)


class ReplayClock(ClockSource):
    """The simulated clock, ticking at a real diary's delivered gaps in order.

    It cycles through the recorded gaps, so a run longer than the diary keeps the
    same distribution. Like the wall clock it reports the mean of its latest
    delivered gaps as ``measured_interval_ns`` while ``interval_ns`` stays the
    declared tick, so every conversion sees what a live world would (Chapter II
    §IV.b-c; time audit T3: the declared tick hid every timing failure the wall
    clock produced).

    Guarantees its checkpoint carries the recorded gaps and the delivered sample, so
    a resumed world continues the same gaps at the same place and measures the same
    interval, never a bare clock at the declared tick.
    """

    def __init__(self, start_ns: int, interval_ns: int, count: int, recorded: list[int],
                 *, gaps: Iterator[int] | list[int] = (), **continuation) -> None:
        super().__init__(start_ns, interval_ns, count, **continuation)
        self.recorded = list(recorded)
        if not self.recorded or any(type(g) is not int or g <= 0 for g in self.recorded):
            raise ValueError("a replay clock needs positive integer recorded gaps")
        self.gaps: deque[int] = deque(gaps, maxlen=64)

    def _events(self, drips=None):
        while self.index < self.count:
            gap = self.recorded[(self.index - 1) % len(self.recorded)]
            ts = self.start_ns if self.last_ns is None else self.last_ns + gap
            if self.last_ns is not None:
                self.gaps.append(ts - self.last_ns)
            self.last_ns = self.last_event_ns = ts
            i = self.index
            self.index += 1
            yield WorldEvent(WorldEventKind.TICK, ts, self.source, {"index": i})

    def measured_interval_ns(self) -> int:
        """The mean of the latest delivered gaps, the declared interval before any."""
        if not self.gaps:
            return self.interval_ns
        return max(1, sum(self.gaps) // len(self.gaps))

    def intervals(self) -> dict:
        return {"declared_ns": self.interval_ns, "measured_ns": self.measured_interval_ns(),
                "samples": len(self.gaps)}

    def state(self) -> dict:
        """The simulated clock's continuation, the recorded gaps and the delivered sample."""
        return {**super().state(), "recorded": list(self.recorded), "gaps": list(self.gaps)}

    @classmethod
    def restore(cls, state: dict) -> ReplayClock:
        """Continue the saved replay at its next recorded gap, with its measured sample."""
        return cls(**state)


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
