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
from math import isfinite
from statistics import fmean

from factorylab.kernel.events import Event, EventKind


def release_window(min_ratio: int, jitter_fraction: float, draw: float, inner_ticks: int) -> float:
    """The precommitted, jittered duration one tier's window covers, in world ticks.

    Essay II.IV.c: the queue enforces "a minimum cascade control ratio (e.g.,
    3:1+) before returning verdicts into the next evaluatory tier", and the
    deferral is "diversified (jittered)". The ratio is taken against the measured
    period of the loop the window gates (time audit T10): how long a judged
    return takes to reach its outcome, in ticks, never below one tick. The
    jitter is continuous and only lengthens, so ``min_ratio × inner`` is a floor.
    """
    from factorylab.runtime.clockwork import derived_period

    return derived_period(min_ratio, jitter_fraction, inner_ticks, draw)


def event_tier(event: Event) -> int:
    """Only judgement events enter the cascade; producer judgements occupy tier one.

    A judgement of a producer return is the seed ``Verdict`` and occupies tier
    one; a ``Verdict`` on an evaluator decision states the tier it sits at. Every
    higher arrival — the seed ``MetaVerdict`` or a population kind
    whose declared reward shape is ``conformity`` — states the tier it judges in
    its own payload, so a window is separated by declared position rather than
    by a fixed pair of kind names. Which kinds are admitted at all is the
    caller's reward-shape decision; anything else has no tier here.
    """
    if event.kind is EventKind.VERDICT and "tier" not in event.payload:
        return 1
    tier = event.payload.get("tier")
    if type(tier) is not int or tier < 2:
        raise ValueError("cascade arrivals must be Verdict or conformity judgements")
    return tier


@dataclass(frozen=True)
class CascadeGate:
    """A window over a duration in world ticks; none releases early or crosses tiers.

    ``window`` is the drawn duration in ticks (continuous), ``opened`` the tick
    the window opened at. A gate saved before the tick clock carries neither
    and restores as a one-tick window open since tick zero: due at its next
    completed arrival. ``carried`` counts the first arrivals that an earlier
    window of the tier released before their subjects settled and carried into
    this one (essay II.IV.c: withheld "until it settles"); a gate saved before
    carrying restores with none.
    """

    window: float = 1.0
    opened: int = 0
    arrivals: tuple[Event, ...] = ()
    carried: int = 0

    def __post_init__(self) -> None:
        if (type(self.window) not in (int, float) or not isfinite(self.window)
                or self.window <= 0):
            raise ValueError("window must be a positive number of ticks")
        if type(self.opened) is not int or self.opened < 0:
            raise ValueError("opened must be a nonnegative tick")
        object.__setattr__(self, "arrivals", tuple(self.arrivals))
        if type(self.carried) is not int or not 0 <= self.carried <= len(self.arrivals):
            raise ValueError("carried must count some of the gate's first arrivals")
        if len({event_tier(e) for e in self.arrivals}) > 1:
            raise ValueError("a gate cannot mix tiers")

    def rank(self, priority: Callable[[Event], int] | None = None) -> Callable[[Event], tuple]:
        """The order in which a window's completed arrivals are read, highest first.

        Guarantees ``priority`` first, then an arrival carried in before one that
        arrived in this window: a carried judgement was withheld only until its
        subject settled, so it is read before the window's own arrivals of equal
        priority. Among equals, the caller's order decides (the latest first).
        """
        carried = {e.id for e in self.arrivals[:self.carried]}
        first = priority or (lambda _e: 0)
        return lambda e: (first(e), e.id in carried)

    def elapsed(self, now: int) -> int:
        """How many ticks of this window's duration have passed."""
        return max(0, now - self.opened)

    def add(
        self, event: Event, *, now: int, complete: Callable[[Event], bool] | None = None,
        priority: Callable[[Event], int] | None = None,
    ) -> tuple[CascadeGate | None, Event | None]:
        """Release one completed arrival, enriched with its window's evidence.

        ``complete`` answers whether one arrival's evidence has finished — for a
        verdict, whether the return it judged has an outcome. A window with no
        completed evidence in it has nothing to report upward however long it
        has been open, and an arrival count of any size reports nothing at all
        before the duration is up. The representative is the completed arrival
        ranked first by ``rank``.
        """
        tier = event_tier(event)
        if self.arrivals and event_tier(self.arrivals[0]) != tier:
            raise ValueError("a gate cannot mix tiers")
        arrivals = (*self.arrivals, event)
        if self.elapsed(now) < self.window:
            return replace(self, arrivals=arrivals), None
        verdicts = event.kind is EventKind.VERDICT
        key = "verdict" if verdicts else "score"
        handle_key = "evaluator_handle" if verdicts else "by"
        finished = [e for e in arrivals if complete is None or complete(e)]
        if not finished:
            # The duration is up and nothing in it has settled. The window stays
            # open rather than reporting an average of unfinished work upward.
            return replace(self, arrivals=arrivals), None
        representative = max(reversed(finished), key=self.rank(priority))
        scores = [e.payload[key] for e in finished]
        # Every arrival is named, so no verdict is judged behind its back; only the
        # representative is graded, and only completed evidence is averaged.
        others = [e.payload[handle_key] for e in arrivals if e is not representative]
        window = {
            "count": len(finished),
            "arrivals": len(arrivals),
            "window_ticks": self.window,
            "elapsed_ticks": self.elapsed(now),
            "mean": fmean(scores),
            "min": min(scores),
            "max": max(scores),
            "handles": [*others, representative.payload[handle_key]],
        }
        return None, replace(representative,
                             payload={**representative.payload, "window": window})
