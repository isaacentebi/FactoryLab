"""Clock and drip event sources.

Both are pure generators over integer nanosecond time. Neither sleeps; the
runtime decides whether to pace them against wall-clock time. The drip source
only *announces* that a deposit is due. Applying it is the wallet's job, which
keeps the kernel the sole authority on money.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass

from factorylab.world.events import WorldEvent, WorldEventKind

NS_PER_SECOND = 1_000_000_000


@dataclass(frozen=True)
class ClockSource:
    """Emits one ``Tick`` per ``interval_ns`` from ``start_ns`` for ``count`` ticks.

    Guarantees strictly increasing timestamps and exactly ``count`` events.
    """

    start_ns: int
    interval_ns: int
    count: int
    source: str = "clock"

    def __post_init__(self) -> None:
        if self.interval_ns <= 0:
            raise ValueError("interval_ns must be positive")
        if self.count < 0:
            raise ValueError("count must be non-negative")

    def events(self) -> Iterator[WorldEvent]:
        for i in range(self.count):
            ts = self.start_ns + i * self.interval_ns
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
