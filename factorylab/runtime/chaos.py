"""The chaos actuator: bounded, real operational faults in what the seats experience.

Essay II.III.b: the adversarial layer acts "like a chaos monkey inside the continuous
software delivery arm"; a realized-consequence score "requires true regressions and
real failures", which "cannot be staged in an artificial environment - they have to
be real and they have to keep coming" (evaluations M1). A fault here is real: a seat
that reads the venue in a faulted tick gets no answer, a seat shown the mids in a
stale tick is shown the tick before's, a population tool's result or a connector's
fetch does not arrive. The population must survive them and the judges must price
them, and a ``failure_within`` forecast settles on them.

The blast radius is kernel physics, not policy. A fault only ever withholds or ages
what a seat is *shown*, and it is decided before anything is metered:

* it never runs, charges, credits or refunds anything: a faulted call returns before
  the meter reserves, so no wallet, budget, entitlement or provider balance moves;
* it never touches a venue write, the pre-submission collateral check
  (``VenueMixin._tick_mids`` and the account read behind it), a fill, the
  reconciler, settlement, custody, the treasury or the kill path: those read the
  venue themselves and never consult this module;
* it never touches the world's own record: ``recent_mids`` (what a declined trade is
  priced from) and the window's public facts keep every print.

Each fault is drawn from the runtime's seeded stream at the manifest's ``[chaos]``
rate (a rate of zero draws nothing, so a world without chaos keeps its stream),
counted, ledgered as ``chaos.fault`` and marked on the event it happened in.
"""

from __future__ import annotations

from typing import Any

#: The per-tick faults, and the per-call ones.
TICK_FAULTS = ("venue_unavailable", "stale_mids")
CALL_FAULTS = ("tool_withheld", "connector_timeout")
#: What a seat's faulted venue read answers.
VENUE_UNAVAILABLE = {"status": "unavailable", "error": "VenueUnavailable"}
#: What a seat's faulted population-tool call answers.
TOOL_WITHHELD = {"status": "unavailable", "error": "result withheld"}


class ChaosMixin:
    """Draw, record and apply the chaos actuator's faults (module docstring)."""

    def _chaos_tick(self) -> None:
        """Draw this tick's per-tick faults, once, when the tick is delivered.

        Guarantees ``chaos_tick`` names this tick and the faults drawn for it, and a
        rate of zero draws nothing from the stream.
        """
        spec = self.m.chaos
        faults: dict[str, Any] = {"tick": self.ticks_consumed}
        if spec.venue_unavailable > 0 and self.rng.random() < spec.venue_unavailable:
            faults["venue_unavailable"] = True
            self._chaos_fault("venue_unavailable")
        if spec.stale_mids > 0 and self.rng.random() < spec.stale_mids:
            # Prints delivered from this tick on stay out of the seats' view until the
            # next tick: what they are shown is the tick before's.
            faults["stale_mids"] = self.clock.now_ns
            self._chaos_fault("stale_mids")
        self.chaos_tick = faults

    def _chaos_active(self, fault: str) -> bool:
        """Whether a per-tick fault was drawn for the tick now running."""
        faults = getattr(self, "chaos_tick", None) or {}
        return faults.get("tick") == self.ticks_consumed and fault in faults

    def _chaos_call(self, fault: str, **fields: Any) -> bool:
        """Draw one per-call fault; True (and recorded) when it strikes."""
        rate = getattr(self.m.chaos, fault)
        if rate <= 0 or self.rng.random() >= rate:
            return False
        self._chaos_fault(fault, **fields)
        return True

    def _chaos_fault(self, fault: str, **fields: Any) -> None:
        """Count, ledger and mark one fault on the event it happened in."""
        self.stats.chaos_faults[fault] = self.stats.chaos_faults.get(fault, 0) + 1
        self.ledger.append({"kind": "chaos.fault", "fault": fault, "tick": self.ticks_consumed,
                            "n": self.n, **fields, "ts": self.clock.now_ns})
        if 0 <= self.n < len(self.events_log):
            # A ``failure_within`` forecast reads the events of its window
            # (``settlement.vocabulary``): a fault is a failure in the world of the factory.
            self.events_log[self.n].setdefault("faults", []).append(fault)

    def _chaos_tool_fault(self, tool_id: str, spec: dict, handle: str) -> dict | None:
        """The answer a faulted seat tool call gets instead of running, or None.

        Guarantees a venue *read* in a tick with ``venue_unavailable``, or a
        population tool call struck by ``tool_withheld``, answers unavailable before
        it is metered; a venue write, a treasury transfer and every kernel tool are
        never faulted here.
        """
        if (spec.get("kind") == "venue" and tool_id not in self.CONSEQUENCE_WRITES
                and self._chaos_active("venue_unavailable")):
            self.ledger.append({"kind": "chaos.applied", "fault": "venue_unavailable",
                                "handle": handle, "tool": tool_id, "ts": self.clock.now_ns})
            return dict(VENUE_UNAVAILABLE)
        if tool_id in self.population_tools and self._chaos_call(
                "tool_withheld", handle=handle, tool=tool_id):
            return dict(TOOL_WITHHELD)
        return None

    def _seat_recent_mids(self) -> dict[str, Any]:
        """The mid prints a seat is shown: every print, less this tick's in a stale tick."""
        if not self._chaos_active("stale_mids"):
            return self.recent_mids
        since = self.chaos_tick["stale_mids"]
        return {coin: [p for p in prints if int(p["t_s"]) * 1_000_000_000 < since]
                for coin, prints in self.recent_mids.items()}

    def _seat_tick_view(self, payload: dict[str, Any]) -> None:
        """Apply this tick's faults to a Tick payload a seat is about to be shown."""
        if self._chaos_active("venue_unavailable"):
            payload["account"] = {"status": "unavailable", "reason": "VenueUnavailable"}
            payload.pop("mids", None)
            payload["mids_unavailable"] = "VenueUnavailable"
        elif self._chaos_active("stale_mids") and "mids" in payload:
            shown = {coin: prints[-1]["mid"]
                     for coin, prints in self._seat_recent_mids().items() if prints}
            if shown:
                payload["mids"] = shown
            else:
                # Nothing older was ever broadcast: a stale view is no view.
                payload.pop("mids")
                payload["mids_unavailable"] = "StaleMids"
