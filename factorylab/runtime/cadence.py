"""Governance waits for measured consequence latency, with deterministic ledger evidence."""

from collections import deque
from datetime import UTC, datetime, timedelta

from factorylab.kernel.ledger import Ledger


class GovernanceCadence:
    """A bounded sample controls activation spacing; every mutation follows ledger evidence."""

    def __init__(self, ledger: Ledger, *, sample: int, min_ratio: int, backstop: int) -> None:
        for name, value in (("sample", sample), ("min_ratio", min_ratio), ("backstop", backstop)):
            if type(value) is not int or value < 1:
                raise ValueError(f"{name} must be a positive integer")
        self._ledger = ledger
        self._latencies: deque[int] = deque(maxlen=sample)
        self._min_ratio = min_ratio
        self._backstop = backstop
        self._last_activation_ns = 0
        self._waiting: dict[str, None] = {}
        self._deferred: dict[str, int] = {}

    def launch(self, now_ns: int) -> None:
        """Anchor the first activation to the launch in the world's time domain."""
        self._ledger.append({"kind": "charter.cadence_launch", "launch_ns": now_ns})
        self._last_activation_ns = now_ns

    def record(
        self, *, handle: str, predicate_id: str, opened_event: int, settled_event: int,
        opened_ns: int, settled_ns: int, status: str,
    ) -> None:
        """Retain one terminal forecast's elapsed nanoseconds after recording its provenance."""
        if settled_ns < opened_ns or settled_event < opened_event:
            raise ValueError("settlement must not precede opening")
        latency_ns = settled_ns - opened_ns
        self._ledger.append({
            "kind": "cadence.settlement", "handle": handle, "predicate_id": predicate_id,
            "opened_event": opened_event, "settled_event": settled_event,
            "opened_ns": opened_ns, "settled_ns": settled_ns,
            "latency_events": settled_event - opened_event, "latency_ns": latency_ns,
            "status": status,
        })
        self._latencies.append(latency_ns)

    def slowest_period_ns(self, tick_interval_ns: int) -> int:
        """Return nearest-rank p90 capped by current backstop time, or the cap without data."""
        cap = self._backstop * tick_interval_ns
        if not self._latencies:
            return cap
        ordered = sorted(self._latencies)
        return min(cap, ordered[(9 * len(ordered) + 9) // 10 - 1])

    def earliest_ns(self, tick_interval_ns: int) -> int:
        """Return the inclusive activation threshold, recomputed from current observations."""
        return self._last_activation_ns + self._min_ratio * self.slowest_period_ns(tick_interval_ns)

    def approve(self, amendment_id: str) -> None:
        """Keep approved candidates in approval order until their activation is recorded."""
        if amendment_id in self._waiting:
            return
        self._ledger.append({"kind": "charter.approved", "amendment_id": amendment_id})
        self._waiting[amendment_id] = None

    def ready(self, *, now_ns: int, tick_interval_ns: int, window: int) -> bool:
        """Block early activation and emit at most one deferral per waiting candidate per window."""
        earliest = self.earliest_ns(tick_interval_ns)
        if now_ns >= earliest:
            return True
        for amendment_id in self._waiting:
            if self._deferred.get(amendment_id) == window:
                continue
            self._ledger.append({
                "kind": "charter.deferred", "amendment_id": amendment_id,
                "earliest_ns": earliest, "window": window,
            })
            self._deferred[amendment_id] = window
        return False

    def activated(self, amendment_id: str, now_ns: int, tick_interval_ns: int) -> None:
        """Record the measured period before advancing the activation anchor and waiting list."""
        self._ledger.append({
            "kind": "charter.cadence", "amendment_id": amendment_id,
            "activation_ns": now_ns, "previous_activation_ns": self._last_activation_ns,
            "slowest_period_ns": self.slowest_period_ns(tick_interval_ns),
            "earliest_ns": self.earliest_ns(tick_interval_ns),
        })
        self._last_activation_ns = now_ns
        self._waiting.pop(amendment_id, None)
        self._deferred.pop(amendment_id, None)

    def world_block(self, tick_interval_ns: int) -> dict:
        """Expose measured duration, UTC activation timestamp and approved waiting ids only."""
        seconds, nanos = divmod(self.earliest_ns(tick_interval_ns), 1_000_000_000)
        timestamp = datetime(1970, 1, 1, tzinfo=UTC) + timedelta(seconds=seconds)
        period = self.slowest_period_ns(tick_interval_ns)
        whole, fraction = divmod(period, 1_000_000_000)
        duration = f"{whole}.{fraction:09d}".rstrip("0").rstrip(".") if fraction else str(whole)
        return {
            "slowest_period": f"{duration}s",
            "earliest_activation": f"{timestamp:%Y-%m-%dT%H:%M:%S}.{nanos:09d}Z",
            "waiting": list(self._waiting),
        }
