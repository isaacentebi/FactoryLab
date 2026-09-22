"""Pure, immutable windows for progressively slower evaluatory tiers.

Separation between tiers is separation in **time and completed evidence**, not
in arrival count. GPT-6 Pro's third reading calls the old rule a launch blocker
(§3: "Cascade separation counts arrivals, not time — three messages arriving
together satisfy the separation"; §10: "Thrash: arrival-count cascades apply
feedback at the wrong scale"), and §6.C states the replacement: "Temporal
separation by time and completed evidence, not arrivals; aggregate upward with
precommitted jitter; keep execution facts and safety actions off the slow path."

So a tier's window covers a duration, drawn once when the window opens with the
precommitted jitter that already existed, and it releases only when two things
hold: the duration has elapsed, and some of the evidence inside it has actually
completed. Three verdicts arriving in the same nanosecond are three arrivals in
an empty window; they trigger nothing. The upward report aggregates the
completed evidence — a verdict whose subject has not settled contributes its
handle to the window, so nothing is judged behind its back, but not a score the
world has not produced yet.

Execution facts and safety actions never enter here: only judgement events do,
and they are the only things this slows down.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace
from math import ceil, isfinite
from statistics import fmean

from factorylab.kernel.events import Event, EventKind


def release_threshold(min_ratio: int, jitter_fraction: float, draw: float) -> int:
    """Preserve the minimum separation, with bounded upward jitter from a supplied draw."""
    if type(min_ratio) is not int or min_ratio < 3:
        raise ValueError("cascade min_ratio must be an integer >= 3")
    if not isfinite(jitter_fraction) or jitter_fraction < 0:
        raise ValueError("jitter_fraction must be finite and nonnegative")
    if not isfinite(draw) or not 0 <= draw < 1:
        raise ValueError("draw must be in [0, 1)")
    jitter = ceil(min_ratio * jitter_fraction)
    return min_ratio + int(draw * (jitter + 1))


def release_window_ns(
    min_ratio: int, jitter_fraction: float, draw: float, observation_window_ns: int
) -> int:
    """The precommitted, jittered duration one tier's window covers.

    The unit is the scope's observation window — the interval the world reports
    itself over, from the tick clock the charter fixes — and the count is the
    same jittered minimum separation the cascade always used, drawn once per
    window from the runtime's own reproducible stream. What changes is that the
    number is a duration rather than a number of messages.
    """
    if type(observation_window_ns) is not int or observation_window_ns <= 0:
        raise ValueError("the observation window must be positive integer nanoseconds")
    return release_threshold(min_ratio, jitter_fraction, draw) * observation_window_ns


def event_tier(event: Event) -> int:
    """Only judgement events enter the cascade; producer judgements occupy tier one.

    A judgement of a producer return is the seed ``Verdict`` and occupies tier
    one. Every higher arrival — the seed ``MetaVerdict`` or a population kind
    whose declared reward shape is ``conformity`` — states the tier it judges in
    its own payload, so a window is separated by declared position rather than
    by a fixed pair of kind names. Which kinds are admitted at all is the
    caller's reward-shape decision; anything else has no tier here.
    """
    if event.kind is EventKind.VERDICT:
        return 1
    tier = event.payload.get("tier")
    if type(tier) is not int or tier < 2:
        raise ValueError("cascade arrivals must be Verdict or conformity judgements")
    return tier


@dataclass(frozen=True)
class CascadeGate:
    """A window over a duration; no window releases early or crosses evaluatory tiers."""

    window_ns: int
    opened_ns: int = 0
    arrivals: tuple[Event, ...] = ()

    def __post_init__(self) -> None:
        if type(self.window_ns) is not int or self.window_ns <= 0:
            raise ValueError("window_ns must be positive integer nanoseconds")
        if type(self.opened_ns) is not int or self.opened_ns < 0:
            raise ValueError("opened_ns must be nonnegative integer nanoseconds")
        object.__setattr__(self, "arrivals", tuple(self.arrivals))
        if len({event_tier(e) for e in self.arrivals}) > 1:
            raise ValueError("a gate cannot mix tiers")

    def elapsed(self, now_ns: int) -> int:
        """How much of this window's duration has passed."""
        return max(0, now_ns - self.opened_ns)

    def add(
        self, event: Event, *, complete: Callable[[Event], bool] | None = None
    ) -> tuple[CascadeGate | None, Event | None]:
        """Release the latest completed arrival, enriched with its window's evidence.

        ``complete`` answers whether one arrival's evidence has finished — for a
        verdict, whether the return it judged has an outcome. A window with no
        completed evidence in it has nothing to report upward however long it
        has been open, and an arrival count of any size reports nothing at all
        before the duration is up.
        """
        tier = event_tier(event)
        if self.arrivals and event_tier(self.arrivals[0]) != tier:
            raise ValueError("a gate cannot mix tiers")
        arrivals = (*self.arrivals, event)
        if self.elapsed(event.ts_ns) < self.window_ns:
            return replace(self, arrivals=arrivals), None
        key = "verdict" if tier == 1 else "score"
        handle_key = "evaluator_handle" if tier == 1 else "by"
        finished = [e for e in arrivals if complete is None or complete(e)]
        if not finished:
            # The duration is up and nothing in it has settled. The window stays
            # open rather than reporting an average of unfinished work upward.
            return replace(self, arrivals=arrivals), None
        representative = finished[-1]
        scores = [e.payload[key] for e in finished]
        # Every arrival is named, so no verdict is judged behind its back; only the
        # representative is graded, and only completed evidence is averaged.
        others = [e.payload[handle_key] for e in arrivals if e is not representative]
        window = {
            "count": len(finished),
            "arrivals": len(arrivals),
            "window_ns": self.window_ns,
            "elapsed_ns": self.elapsed(event.ts_ns),
            "mean": fmean(scores),
            "min": min(scores),
            "max": max(scores),
            "handles": [*others, representative.payload[handle_key]],
        }
        return None, replace(representative,
                             payload={**representative.payload, "window": window})
