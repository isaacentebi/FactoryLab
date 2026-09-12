"""Governance waits for measured consequence latency, with deterministic ledger evidence."""

from collections import deque
from datetime import UTC, datetime, timedelta

from factorylab.kernel.ledger import Ledger


class GovernanceCadence:
    """A bounded sample controls activation spacing; every mutation follows ledger evidence."""

    def __init__(
        self, ledger: Ledger, *, sample: int, min_ratio: int, backstop: int,
        min_support: int = 30,
    ) -> None:
        for name, value in (("sample", sample), ("min_ratio", min_ratio),
                            ("backstop", backstop), ("min_support", min_support)):
            if type(value) is not int or value < 1:
                raise ValueError(f"{name} must be a positive integer")
        self._ledger = ledger
        self._latencies: deque[int] = deque(maxlen=sample)
        self._min_ratio = min_ratio
        self._backstop = backstop
        self._min_support = min_support
        self._outstanding: dict[str, int] = {}
        self._current_event = 0
        self._last_activation_event = 0
        self._last_activation_ns = 0
        self._waiting: dict[str, None] = {}
        self._deferred: dict[str, int] = {}

    def configure(self, *, min_support: int) -> None:
        """Bind the immutable manifest support requirement before the runtime starts."""
        if type(min_support) is not int or min_support < 1:
            raise ValueError("min_support must be a positive integer")
        self._min_support = min_support

    def advance(self, event: int) -> None:
        """Advance age only in the delivered event domain; wall-clock timestamps are forensic."""
        if type(event) is not int or event < self._current_event:
            raise ValueError("event clock must be nondecreasing")
        self._current_event = event

    def record_open(self, handle: str, opened_event: int) -> None:
        """An outstanding forecast contributes age before it can contribute a settlement."""
        if type(opened_event) is not int or opened_event < 0:
            raise ValueError("opened_event must be a nonnegative integer")
        if handle in self._outstanding:
            if self._outstanding[handle] != opened_event:
                raise ValueError("forecast opening cannot change")
            return
        self._ledger.append({"kind": "cadence.open", "handle": handle,
                             "opened_event": opened_event})
        self._outstanding[handle] = opened_event

    def launch(self, now_ns: int) -> None:
        """Anchor the first activation to the launch in the world's time domain."""
        self._ledger.append({"kind": "charter.cadence_launch", "launch_ns": now_ns})
        self._last_activation_ns = now_ns
        self._last_activation_event = self._current_event

    def record(
        self, *, handle: str, predicate_id: str, opened_event: int, settled_event: int,
        opened_ns: int, settled_ns: int, status: str,
    ) -> None:
        """Retain event latency and close its outstanding age after recording both time units."""
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
        self._latencies.append(settled_event - opened_event)
        self._outstanding.pop(handle, None)
        self._current_event = max(self._current_event, settled_event)

    def slowest_period_events(self) -> int:
        """Keep the committed consequence horizon beneath p90 and outstanding forecast age.

        A pooled sample of quick completions cannot disprove an unfinished slow
        loop. The backstop remains the conservative floor even after warm-up.
        """
        estimate = self._backstop
        if len(self._latencies) >= self._min_support:
            ordered = sorted(self._latencies)
            estimate = max(estimate, ordered[(9 * len(ordered) + 9) // 10 - 1])
        oldest = max((self._current_event - opened for opened in self._outstanding.values()),
                     default=0)
        return max(estimate, oldest)

    def slowest_period_ns(self, tick_interval_ns: int) -> int:
        """Convert the event estimate using the current tick, never the ledger latency in ns."""
        return self.slowest_period_events() * tick_interval_ns

    def earliest_event(self) -> int:
        """Require fresh event evidence since the most recent activation."""
        return self._last_activation_event + self._min_ratio * self.slowest_period_events()

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
        if now_ns >= earliest and self._current_event >= self.earliest_event():
            return True
        for amendment_id in self._waiting:
            if self._deferred.get(amendment_id) == window:
                continue
            self._ledger.append({
                "kind": "charter.deferred", "amendment_id": amendment_id,
                "earliest_ns": earliest, "earliest_event": self.earliest_event(), "window": window,
            })
            self._deferred[amendment_id] = window
        return False

    def activated(self, amendment_id: str, now_ns: int, tick_interval_ns: int) -> None:
        """Record the measured period before advancing the activation anchor and waiting list."""
        self._ledger.append({
            "kind": "charter.cadence", "amendment_id": amendment_id,
            "activation_ns": now_ns, "previous_activation_ns": self._last_activation_ns,
            "slowest_period_ns": self.slowest_period_ns(tick_interval_ns),
            "slowest_period_events": self.slowest_period_events(),
            "activation_event": self._current_event,
            "previous_activation_event": self._last_activation_event,
            "outstanding_forecasts": len(self._outstanding),
            "earliest_ns": self.earliest_ns(tick_interval_ns),
        })
        self._last_activation_ns = now_ns
        self._last_activation_event = self._current_event
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
            "slowest_period_events": self.slowest_period_events(),
            "outstanding_forecasts": len(self._outstanding),
            "earliest_activation_event": self.earliest_event(),
            "earliest_activation": f"{timestamp:%Y-%m-%dT%H:%M:%S}.{nanos:09d}Z",
            "waiting": list(self._waiting),
        }


def settle_forecasts(rt, settle) -> None:
    """Record openings before resolution, including forecasts that close inside one tick."""
    rt.cadence.advance(rt.n)
    pending = rt.book.pending()
    for forecast in pending:
        rt.cadence.record_open(forecast.handle, forecast.made_at_event)
    settle()
    remaining = {f.handle for f in rt.book.pending()}
    for forecast in pending:
        if (forecast.handle in remaining or forecast.predicate_id != "return_paid_off"
                or rt.queue.get(forecast.handle).status != "settled"):
            continue
        from factorylab.runtime.immune import settle_novelty

        parent = rt.queue.get(forecast.handle).parent_handle
        settle_novelty(rt, (forecast.about_handle, parent))
