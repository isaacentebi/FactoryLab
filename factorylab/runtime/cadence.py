"""Governance waits for measured consequence latency, with deterministic ledger evidence."""

from collections import deque
from datetime import UTC, datetime, timedelta
from typing import Protocol

from factorylab.kernel.ledger import Ledger


class TickClock(Protocol):
    interval_ns: int


def tick_intervals(clock: TickClock) -> dict:
    """Publish delivered intervals where measured, declared intervals otherwise."""
    if hasattr(clock, "intervals"):
        return clock.intervals()
    return {"declared_ns": clock.interval_ns, "measured_ns": clock.interval_ns, "samples": 0}


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
        # Essay II.IV.c, time audit T7: the settling times the live versioning measured
        # (``versioning.live``: a charter activation, a change of terms or of behaviour
        # opens a version, and it settles when its distribution stops moving), and the
        # version still open and unsettled, {"version", "opened"}, whose age counts.
        self._settling: deque[int] = deque(maxlen=sample)
        self._unsettled: dict | None = None
        # The version whose age was already recorded as a lower bound (censored).
        self._censored: int | None = None
        # Time audit T13: the capital loop's closures, open to finalized, in ticks.
        self._capital: deque[int] = deque(maxlen=sample)

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

    def is_open(self, handle: str) -> bool:
        """Whether an opening is already recorded for this forecast."""
        return handle in self._outstanding

    def opened_at(self, handle: str, default: int) -> int:
        """The clock index this forecast was first seen open at, or ``default``."""
        return self._outstanding.get(handle, default)

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

    def consequence_period_events(self) -> int:
        """The consequence loop in ticks: the backstop, or the p90 settlement above it.

        A pooled sample of quick completions cannot disprove an unfinished slow
        loop. The backstop remains the conservative floor even after warm-up.
        """
        estimate = self._backstop
        if len(self._latencies) >= self._min_support:
            ordered = sorted(self._latencies)
            estimate = max(estimate, ordered[(9 * len(ordered) + 9) // 10 - 1])
        return estimate

    def slowest_period_events(self) -> int:
        """The slowest loop governance commands, in ticks (essay II.IV.c).

        The largest of: the consequence loop; the oldest outstanding forecast; the
        versions' measured settling times, and the age of the version still
        unsettled (time audit T7; versioning audit M1); and the capital loop's p90
        closure (T13). An unfinished loop is never read as a fast one.
        """
        oldest = max((self._current_event - opened for opened in self._outstanding.values()),
                     default=0)
        settling = max(self._settling, default=0)
        unsettled = (self._current_event - self._unsettled["opened"]) if self._unsettled else 0
        capital = self.capital_period_events() or 0
        return max(self.consequence_period_events(), oldest, settling, unsettled, capital)

    def record_capital(self, *, transfer_id: str, latency_ticks: int, latency_ns: int) -> None:
        """One conversion reached finality: the capital loop closed once (time audit T13)."""
        if type(latency_ticks) is not int or latency_ticks < 0:
            raise ValueError("a closure takes a nonnegative number of ticks")
        self._ledger.append({"kind": "cadence.capital", "transfer_id": transfer_id,
                             "latency_ticks": latency_ticks, "latency_ns": latency_ns,
                             "event": self._current_event})
        self._capital.append(latency_ticks)

    def capital_period_events(self) -> int | None:
        """The capital loop's p90 closure in ticks, or None without enough support.

        The same support floor the consequence loop needs (``timing.min_support``):
        a handful of conversions is not evidence of the rail's period, and one that
        happened to straddle a stall must not set it.
        """
        if len(self._capital) < self._min_support:
            return None
        ordered = sorted(self._capital)
        return ordered[(9 * len(ordered) + 9) // 10 - 1]

    def record_settling(self, *, version: int, cause: str, ticks: int, settled: bool) -> None:
        """One version's settling time joins the slowest period (essay II.IV.c; T7).

        Essay II.IV.c: "inject a small, deliberate intent revision and measure how
        long the output distribution takes to return to a settled distribution ...
        that settling time corresponds to the period of the slowest feedback loop".
        The live versioning measures it for every version, whatever opened it (a
        charter activation, a change of the world's terms, a change of behaviour);
        ``settled`` False is a lower bound, the age at which the version was
        superseded or censored. A version already censored is not recorded again.
        """
        if type(ticks) is not int or ticks < 0:
            raise ValueError("a settling time is a nonnegative number of ticks")
        if version == self._censored:
            return
        self._ledger.append({"kind": "governance.settling", "version": version,
                             "cause": cause, "settled_event": self._current_event,
                             "settling_ticks": ticks, "settled": settled})
        self._settling.append(ticks)

    def track_version(self, *, version: int, opened: int | None, settled: bool) -> None:
        """Hold the open version's age in the slowest period while it is unsettled.

        A version still unsettled after ``min_ratio`` consequence periods is recorded
        with its age as a lower bound and no longer held, so one revision the world
        never settles cannot stop the governance loop for the life of the world; the
        bound still slows it.
        """
        if settled or opened is None or version == self._censored:
            self._unsettled = None
            return
        self._unsettled = {"version": version, "opened": opened}
        age = self._current_event - opened
        if age >= self._min_ratio * self.consequence_period_events():
            self.record_settling(version=version, cause="censored", ticks=age, settled=False)
            self._censored = version
            self._unsettled = None

    def viability(self, *, run_ticks: int | None, world_ticks: int | None) -> dict:
        """Whether a governance tier fits between sampling noise and lagging the world.

        Essay II.IV.c: "A factory whose versions stabilize monthly inside market
        conditions that are comprehensively repriced weekly has no viable
        governance tier." Viable when ``min_ratio × slowest`` fits within both the
        run's whole length and the world's repricing period, in ticks.
        """
        needed = self._min_ratio * self.slowest_period_events()
        bounds = {name: value for name, value in (("run_ticks", run_ticks),
                                                  ("world_ticks", world_ticks))
                  if value is not None}
        limit = min(bounds.values(), default=None)
        return {"viable": limit is None or needed <= limit, "needed_ticks": needed,
                "slowest_ticks": self.slowest_period_events(), **bounds}

    def slowest_period_ns(self, tick_interval_ns: int | TickClock) -> int:
        """Convert at the slower of the delivered gap and the interval now declared.

        A gap sample is evidence that the loop ran slowly, never evidence that
        it may run faster than the charter currently says. An amendment that
        lengthens the tick therefore takes effect immediately, and measurement
        may only push the priced period further out.
        """
        interval = tick_interval_ns
        if not isinstance(interval, int):
            declared = interval.interval_ns
            measured = getattr(interval, "measured_interval_ns", None)
            interval = max(measured(), declared) if measured is not None else declared
        return self.slowest_period_events() * interval

    def earliest_event(self) -> int:
        """Require fresh event evidence since the most recent activation."""
        return self._last_activation_event + self._min_ratio * self.slowest_period_events()

    def earliest_ns(self, tick_interval_ns: int | TickClock) -> int:
        """Return the inclusive activation threshold, recomputed from current observations."""
        return self._last_activation_ns + self._min_ratio * self.slowest_period_ns(tick_interval_ns)

    def boundary(self, boundary: int, *, window: int, now_ns: int,
                 tick_interval_ns: int | TickClock) -> None:
        """Record an open governance boundary and anchor the next one to it.

        Essay II.IV.c: the governing loop revises its command set no faster than
        ``min_ratio`` times the slowest loop it commands. A committee is seated
        at every boundary (charter audit C1), whether or not anything activates,
        so each boundary, not each activation, starts the next separation.
        """
        self._ledger.append({
            "kind": "charter.boundary", "boundary": boundary, "window": window,
            "boundary_ns": now_ns, "previous_ns": self._last_activation_ns,
            "boundary_event": self._current_event,
            "previous_event": self._last_activation_event,
            "slowest_period_ns": self.slowest_period_ns(tick_interval_ns),
            "slowest_period_events": self.slowest_period_events(),
            "outstanding_forecasts": len(self._outstanding),
        })
        self._last_activation_ns = now_ns
        self._last_activation_event = self._current_event

    def approve(self, amendment_id: str) -> None:
        """Keep approved candidates in approval order until their activation is recorded."""
        if amendment_id in self._waiting:
            return
        self._ledger.append({"kind": "charter.approved", "amendment_id": amendment_id})
        self._waiting[amendment_id] = None

    def ready(self, *, now_ns: int, tick_interval_ns: int | TickClock, window: int) -> bool:
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

    def refused(self, amendment_id: str, reason: str) -> None:
        """Remove an unactivated candidate without consuming a consequence boundary."""
        if amendment_id not in self._waiting:
            return
        self._ledger.append({"kind": "charter.cadence_refused", "amendment_id": amendment_id,
                             "reason": reason, "event": self._current_event})
        self._waiting.pop(amendment_id)
        self._deferred.pop(amendment_id, None)

    def activated(self, amendment_id: str, now_ns: int, tick_interval_ns: int | TickClock) -> None:
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
        # The activation's settling is read by the live versioning: the new edition
        # opens a version at the next window close (``versioning.live``).

    def world_block(self, tick_interval_ns: int | TickClock) -> dict:
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
    """Record openings before resolution, including forecasts that close inside one tick.

    The cadence's clock is world ticks consumed, the unit its backstop floor and
    the tick-interval conversion both assume (defect 1). A forecast is first seen
    in the settlement pass of the event that sealed it, so its first-seen tick is
    the tick it was sealed in.
    """
    rt.cadence.advance(rt.ticks_consumed)
    for forecast in rt.book.pending():
        if not rt.cadence.is_open(forecast.handle):
            rt.cadence.record_open(forecast.handle, rt.ticks_consumed)
    settle()
