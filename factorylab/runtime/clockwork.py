"""The factory's clock: every loop counted in world ticks consumed, each period a ratio.

Essay II.IV.b: the factory's temporal unit is the loop, analysed by its
periodicity, phase and gain. Essay II.IV.c: an inner loop must resolve itself
several times faster than the outer loop that commands it (3:1 at a minimum),
"this exact rule needs to be applied through the full cascade of nested loops",
and the Stackelberg move encodes "the relational dynamics", not "the actual
frequency bands themselves".

So this module encodes relations and nothing else:

* **One clock domain.** A loop's period is counted in world ticks consumed. The
  delivered tick interval (the slower of the measured and the declared gap)
  converts ticks to wall time only for display and for money rails
  (``tick_ns``).
* **Measured loops.** A meter holds the latest closures of one loop, in ticks,
  and reports their p90 (never below its floor). A pooled sample of quick
  closures reports a quick loop; it never reports a slow one as fast, because
  every loop that consumes a meter also floors it.
* **Derived loops.** An outer loop's next period is drawn, with its own jitter,
  as ``min_ratio × inner × (1 + jitter_fraction × u)``, where ``inner`` is the
  measured period of the loop it commands and ``u`` is a continuous draw that
  belongs to this loop alone (the world seed, the loop's name and how often it
  has fired). The jitter only lengthens, so the ratio is a floor; distinct
  loops never share a boundary by construction ("this temporal deferral should
  also be diversified (jittered) to intentionally obfuscate entrainment").
* **Checked twice.** A loop is due only when its drawn period has elapsed *and*
  the ticks since it last fired are still at least ``min_ratio`` times the
  inner period measured now: an inner loop that slowed after the draw pushes
  its outer loop out (the runtime half of the 3:1 rule); the load half is
  ``WorldManifest.validate``.
"""

from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass, field

#: The ledger kind a derived loop's firing is recorded under.
LOOP_ENTRY = "clock.loop"


def p90(values: list[int]) -> int:
    """The nearest-rank 90th percentile of a nonempty sample (the cadence's rank)."""
    ordered = sorted(values)
    return ordered[(9 * len(ordered) + 9) // 10 - 1]


def jitter_draw(seed: int, name: str, count: int) -> float:
    """A continuous draw in [0, 1) owned by one loop's ``count``-th period.

    Seeded by the world, the loop and its firing count, never by a shared
    stream: two loops cannot fall into step by drawing from one source, and a
    resumed world draws exactly what the original would have.
    """
    digest = hashlib.sha256(f"{seed}:{name}:{count}".encode()).digest()
    return int.from_bytes(digest[:8], "big") / 2**64


def derived_period(min_ratio: int, jitter_fraction: float, inner: int, draw: float) -> float:
    """``min_ratio × inner`` lengthened by up to ``jitter_fraction`` of itself, in ticks."""
    if type(min_ratio) is not int or min_ratio < 3:
        raise ValueError("min_ratio must be an integer at least 3")
    if type(inner) is not int or inner < 1:
        raise ValueError("an inner period is a positive number of ticks")
    if not math.isfinite(jitter_fraction) or jitter_fraction < 0:
        raise ValueError("jitter_fraction must be finite and nonnegative")
    if not 0 <= draw < 1:
        raise ValueError("draw must be in [0, 1)")
    return min_ratio * inner * (1.0 + jitter_fraction * draw)


def deadline_ticks(horizon: int, min_ratio: int) -> int:
    """A decision's cutoff: its own horizon plus a ratio slack of ``ceil(horizon/min_ratio)``.

    The horizon is the loop the decision waits on (a verdict timeout, a
    consequence backstop, a forecast's own horizon). The slack is the part of
    that horizon one ``min_ratio`` step resolves, at least one tick, so a return
    settling on the tick its horizon closes is never cut off by the same tick.
    """
    if type(horizon) is not int or horizon < 0:
        raise ValueError("a horizon is a nonnegative number of ticks")
    return horizon + max(1, -(-horizon // min_ratio))


def tick_ns(clock) -> int:
    """The delivered tick interval: the slower of the measured and the declared gap.

    Used only to convert a tick count into wall time (a deadline shown to a seat,
    a money rail's latency). A measured gap is evidence the loop ran slowly,
    never that it may run faster than declared.
    """
    declared = clock.interval_ns
    measured = getattr(clock, "measured_interval_ns", None)
    return max(measured(), declared) if callable(measured) else declared


def ticks_for(span_ns: int, clock) -> int:
    """How many delivered ticks a wall-clock span covers, rounded up, at least one."""
    return max(1, -(-max(0, span_ns) // tick_ns(clock)))


@dataclass
class Clockwork:
    """Meters for the measured loops and schedules for the derived ones.

    ``latencies`` maps a measured loop to its latest closures in ticks (bounded
    by ``sample``). ``loops`` maps a derived loop to its schedule: the tick it
    last fired (``opened``), the tick its drawn period ends (``due``), the
    period drawn (``period``, in ticks, continuous), the inner period it was
    drawn from (``inner``) and how often it has fired (``fires``).
    """

    min_ratio: int = 3
    jitter_fraction: float = 0.2
    seed: int = 0
    sample: int = 200
    latencies: dict[str, list[int]] = field(default_factory=dict)
    loops: dict[str, dict] = field(default_factory=dict)

    # -- measured loops ----------------------------------------------------------

    def record(self, name: str, ticks: int) -> None:
        """One closure of loop ``name`` took ``ticks`` world ticks."""
        if type(ticks) is not int or ticks < 0:
            raise ValueError("a closure takes a nonnegative number of ticks")
        rows = self.latencies.setdefault(name, [])
        rows.append(ticks)
        del rows[:-self.sample]

    def measured(self, name: str, floor: int = 1) -> int:
        """The p90 closure of loop ``name`` in ticks, never below ``floor`` (at least one)."""
        rows = self.latencies.get(name)
        floor = max(1, floor)
        return max(floor, p90(rows)) if rows else floor

    def mean(self, name: str) -> float | None:
        """The mean closure of loop ``name`` in ticks, or None before its first closure."""
        rows = self.latencies.get(name)
        return sum(rows) / len(rows) if rows else None

    def support(self, name: str) -> int:
        """How many closures of loop ``name`` the meter holds."""
        return len(self.latencies.get(name, ()))

    # -- derived loops -----------------------------------------------------------

    def fire(self, name: str, now: int, inner: int) -> dict:
        """Close one period of loop ``name`` at tick ``now`` and draw the next one.

        Guarantees the next period is at least ``min_ratio × inner`` ticks and
        is drawn from this loop's own continuous jitter.
        """
        previous = self.loops.get(name, {})
        fires = previous.get("fires", 0) + 1
        period = derived_period(self.min_ratio, self.jitter_fraction, max(1, inner),
                                jitter_draw(self.seed, name, fires))
        schedule = {"opened": now, "due": now + math.ceil(period), "period": period,
                    "inner": max(1, inner), "fires": fires}
        self.loops[name] = schedule
        return schedule

    def due(self, name: str, now: int, inner: int) -> bool:
        """Whether loop ``name`` may fire at tick ``now`` against the inner period measured now.

        A loop that never fired is due at once (it opens its first period). A
        fired one is due when its drawn period has elapsed and the ticks since
        it fired are still at least ``min_ratio × inner``.
        """
        schedule = self.loops.get(name)
        if schedule is None:
            return True
        elapsed = now - schedule["opened"]
        return now >= schedule["due"] and elapsed >= self.min_ratio * max(1, inner)

    def opened(self, name: str) -> int | None:
        """The tick loop ``name`` last fired at, or None before it first fired."""
        schedule = self.loops.get(name)
        return None if schedule is None else schedule["opened"]

    def period(self, name: str, default: int = 1) -> int:
        """The last drawn period of loop ``name`` in whole ticks, or ``default``."""
        schedule = self.loops.get(name)
        return math.ceil(schedule["period"]) if schedule else max(1, default)

    def force(self, name: str, now: int) -> None:
        """Make loop ``name`` due at ``now`` whatever its schedule (a test's lever)."""
        schedule = self.loops.get(name)
        if schedule is not None:
            self.loops[name] = {**schedule, "due": now,
                                "opened": min(schedule["opened"],
                                              now - self.min_ratio * schedule["inner"])}

    def table(self) -> dict[str, dict]:
        """Every loop in ticks: the measured ones' periods, the derived ones' schedules."""
        measured = {name: {"period_ticks": self.measured(name), "support": len(rows)}
                    for name, rows in sorted(self.latencies.items())}
        derived = {name: {"period_ticks": round(s["period"], 3), "inner_ticks": s["inner"],
                          "ratio": round(s["period"] / s["inner"], 3), "due_tick": s["due"],
                          "fires": s["fires"]}
                   for name, s in sorted(self.loops.items())}
        return {"measured": measured, "derived": derived}
