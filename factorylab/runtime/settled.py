"""Release of fully settled decisions (wave 17b).

Essay II.I.b: "The return channel must therefore keep actions addressable over
time ... assign each sampled action a persistent handle that a later score can be
attached to ... a managed queue of outstanding decisions awaiting their reward."
Essay II.IV.c: "a verdict is consumed as a reward signal in the scored agent's
propensity update and then discarded"; what persists is aggregates, "a running
distribution of judge scores per metric over a time window". Essay II: configuration
state is "expensive and likely very ephemeral ... forensic metadata".

So a decision stays addressable exactly while a score is still owed to it
(``SettledMixin._score_owed``, the one predicate), and once none is it is released
at the next checkpoint boundary: the kernel queue keeps a tombstone (then, past a
horizon, a count and a range of ordinals), the consequence table keeps counts, and
every per-decision entry of the runtime's own books is dropped. A judge that names a
released handle is refused, factually ("settled and released").

Release runs where wave 17's pruning runs, at the checkpoint boundary a live run and
its replay share (``Runtime._prune_retained``), so a resumed world releases exactly
what the uninterrupted one released.
"""

from __future__ import annotations

from collections import Counter, deque
from collections.abc import Mapping
from dataclasses import fields, is_dataclass
from typing import Any

from factorylab.kernel.events import EventKind
from factorylab.kernel.queue import SettleStatus
from factorylab.runtime.shared import CH_CONSEQUENCE, CH_VERDICT, DEF_EVALUATION

#: Why a judgement naming a released decision is refused: a fact, not advice.
RELEASED_REFUSAL = "judgement names a decision that settled and was released"

#: The runtime's live books: open obligations, windows still measured, motions still
#: voted, registrations whose provenance is still read. A decision any of them names,
#: anywhere inside, is still read by its owner, so a score (or a reading that decides
#: one) is still owed to it and it is not released. Each is named with its reader.
LIVE_BOOKS = (
    "internal",               # events still to be routed: their subjects are judged next
    "cascade",                # judgements held or carried to their backstop (II.IV.c)
    "pending",                # judgements awaiting verdicts, grades or consequences
    "arrived_verdicts",       # verdicts collected while an event is routed
    "pending_exposure", "exposure_scores", "declined_exposures",  # antagonists open
    "pending_counters",       # counter-verdicts awaiting the world's measurement
    "forecast_returns",       # forecast returns awaiting their forecasts
    "noop_credits",           # abstention credits owed to a router (II.II.b niche)
    "assembly_rounds",        # an assembly's own learner round awaiting its decision
    "tool_uses", "tool_holds",  # tool builders held for their callers' scores (W4)
    "uptake",                 # registrations whose uptake is still being realized
    "pending_votes", "lambda_posts",  # ballots and lambda posts awaiting settlement
    "retirement_proposals", "challenges",  # motions whose proposer is still read
    "population_tools", "tool_specs",  # a tool's provenance is read at every use
    "registered_observations", "registered_predicates",  # registrations' provenance
    "price_windows", "price_origins", "window",  # the price loop's open measurement
    "margin_windows", "measured_consequences",   # the charter's margin windows (M5)
    # Wave 16: a named trade's frozen mids until its outcome is fixed at its horizon
    # (D2, R10-j: judged or not); a settlement whose penalty waits for its origin
    # window's close (D5, R-I); a raw score until the router that drew it learns it
    # (D4); the venue's own marks and funding prints a horizon is priced from.
    "reference_mids", "deferred_settlements", "raw_scores", "venue_marks",
    "funding_prints",
)
# The venue books are live only where they are not terminal (``_live_venue_books``).
# Not live books, though they name handles: ``card_samples`` rows carry their own
# measured values and use a handle only to join a row to another row of the same
# samples (``charter.measurement``), never to read a decision; ``vote_handles`` is read
# by event id only (a ballot opened once); ``outcomes`` addresses a seat's inbox items
# by handle as text for that seat; and the simulated venue's own books are the world.

#: Per-decision entries the runtime keeps only for a decision's own readers: dropped
#: with the decision. Each reader reads them for a retained decision (the predicate
#: holds every decision any reader can still reach), so none reads them again.
DROPPED_WITH_DECISION = (
    "return_events",          # read only by a judgement naming it: refused once released
    "return_bindings",        # the channel a retained decision's returns map through
    "return_kinds",           # the kind a decision emitted: its cards and scopes
    "handle_to_assembly",     # its author: the tombstone keeps the author
    "decision_subjects",      # what it judged: its own ancestry, read while retained
    "consequence_scores",     # what a meta grading it predicts: none can grade it now
    "world_outcomes",         # its measured outcome
    "verdict_views",          # the world a counter re-judging its verdict reads
    "venue_deltas",           # custody effects reported at its payoff, already fixed
    "decision_ticks",         # its tick cutoff: it is final and learned
    "snapshot_keys",          # a router's frozen round: learned, or never delivered
    "thrash_charges",         # a core round's charge: learned at delivery
)


def _names_in(roots, handles: set[str]) -> dict[str, None]:
    """Every string in ``handles`` that the data under ``roots`` names, anywhere inside.

    Walks mappings (keys and values), sequences, sets, dataclass fields and plain
    objects' attributes, each object once; never calls anything.
    """
    found: dict[str, None] = {}
    seen: set[int] = set()
    stack = list(roots)
    while stack:
        value = stack.pop()
        if isinstance(value, str):
            if value in handles:
                found[value] = None
            continue
        if value is None or isinstance(value, (int, float, bool, bytes)):
            continue
        if id(value) in seen:
            continue
        seen.add(id(value))
        if isinstance(value, Mapping):
            for key, item in value.items():
                stack.append(key)
                stack.append(item)
        elif isinstance(value, (list, tuple, set, frozenset, deque)):
            stack.extend(value)
        elif is_dataclass(value) and not isinstance(value, type):
            stack.extend(getattr(value, f.name, None) for f in fields(value))
        elif hasattr(value, "__dict__") and not callable(value):
            stack.extend(vars(value).values())
    return found


class SettledMixin:
    """The one "fully settled" predicate, release at the checkpoint, and the tally it feeds."""

    # -- the predicate -------------------------------------------------------------

    def _live_references(self) -> dict[str, Any]:
        """What the predicate reads, computed once per boundary over retained decisions.

        ``named``: every retained handle a live book names (``LIVE_BOOKS``), the book's
        wallet, treasury, Polymarket and forecast books included. ``referrers``: how
        many retained decisions name each handle as their parent or judged subject
        (their ancestry is read while they are retained). ``order_owners``: the owners
        of the orders a retained ``failure_within`` window reads by order id.
        """
        handles = set(self.queue.retained())
        roots = [getattr(self, name, None) for name in LIVE_BOOKS]
        roots.append(self.wallet.uncertain_bills)
        # The treasury's own state (a conversion names the decision that asked for it),
        # read from the adapter itself: a read through the journal would be a call.
        treasury = getattr(self.treasury, "target", self.treasury)
        snapshot = getattr(treasury, "snapshot", None)
        if snapshot is not None:
            roots.append(snapshot())
        roots.extend(self._live_venue_books())
        # Unsettled forecasts: the decision that sealed each, and what each is about
        # (its card forecast reads the about's kind and author, charter.measurement).
        roots.append([(f.handle, f.about_handle) for f in self.book.pending()])
        # Proposals and motions still on the charter's agenda name their proposers,
        # read for recusal and as a ballot's parent.
        roots.append([getattr(self.charter_book, f"_CharterBook__{name}", None)
                      for name in ("proposals", "committees", "ballots", "activations",
                                   "bindings", "sittings", "deferrals", "voters")])
        named = _names_in(roots, handles)
        subjects = self.decision_subjects
        referrers: Counter = Counter()
        for handle in handles:
            decision = self.queue.get(handle)
            if decision.parent_handle is not None:
                referrers[decision.parent_handle] += 1
            subject = subjects.get(handle)
            if isinstance(subject, str) and subject != handle:
                referrers[subject] += 1
        # ``_independent_failures`` reads, for every OrderRejected and liquidation fill
        # in a retained forecast window, the author of the order's decision.
        owners = set()
        table = self.consequences.table
        for entry in self.events_log:
            payload = entry.get("payload") or {}
            if entry.get("kind") == EventKind.ORDER_REJECTED or (
                    entry.get("kind") == EventKind.FILL and payload.get("liquidation") is True):
                owner = table.order_owner(str(payload.get("order_id")))
                if owner is not None:
                    owners.add(owner)
        return {"named": named, "referrers": referrers, "order_owners": owners}

    def _live_venue_books(self) -> list[Any]:
        """The venue writes still in flight, and the money still to be claimed.

        Guarantees: a Hyperliquid order intent while the venue has not answered it; a
        vault write while it is uncertain, or acknowledged and not yet settled from
        the venue's own ledger (``_reconcile_vault_intents``), unless given up; a
        Polymarket write while uncertain and not given up; and a Polymarket
        decision's realised P&L while part of it is unclaimed (``claim_share``). A
        terminal write names its decision nowhere a reader will look again: its
        fills and its lots are the consequence book's, which pins by itself.
        """
        books: list[Any] = [
            [intent for intent in self.order_intents.values()
             if (intent.get("result") or {}).get("status") == "uncertain"],
            [intent for intent in getattr(self, "vault_intents", {}).values()
             if not intent.get("unresolved") and (
                 intent["result"].get("status") == "uncertain"
                 or (intent["result"].get("status") == "ok" and not intent.get("settled")))],
        ]
        surface = getattr(self, "polymarket", None)
        if surface is not None:
            books.append([intent for intent in surface.intents.values()
                          if intent["result"].get("status") == "uncertain"
                          and not intent.get("unresolved")])
            books.append([handle for handle, exact in surface.realized.items()
                          if exact.numerator // exact.denominator
                          != surface.claimed.get(handle, 0)])
        return books

    def _score_owed(self, handle: str, live: dict[str, Any]) -> str | None:
        """Why a score is still owed to ``handle``, or None: it is fully settled.

        Essay II.I.b: the return channel keeps "a managed queue of outstanding
        decisions awaiting their reward", each with "a persistent handle that a later
        score can be attached to"; II.IV.c: a verdict "is consumed ... and then
        discarded". A decision is fully settled, and may be released, when every
        reader has had what it is owed:

        * the kernel owes it nothing (``DecisionQueue.owed``): it is final, not timed
          out (a late settlement keeps its right), every child it requested is
          released, and every return delivered for it was read by its consumer (a
          router's learning, a seat's ballot);
        * no retained decision names it as its parent or its judged subject, so every
          ancestry, self-judgement and lineage walk from a live decision is whole;
        * no live book names it (``LIVE_BOOKS``): no event about it waits to be
          routed, no judge, grade or counter-verdict about it is pending, no cascade
          window holds or carries a judgement of it to its backstop, no forecast,
          exposure, abstention credit, assembly round, tool hold, uptake, ballot,
          lambda post, motion or registration still reads it, and no price, margin
          or card window still measures it (the charter's measurement, the
          observations and the wake read those windows);
        * its consequence account is closed (``ReturnConsequences.releasable``): its
          outcome is fixed, its realised money is all booked to its owner, it owns no
          open lot, every order it placed was confirmed terminal by the venue's own
          order status (``confirm_terminal``), with nothing filled beyond what was
          accounted, and no intent it sent is unanswered;
        * its realized-consequence horizon has passed on the venue's clock
          (``_past_horizon``; wave 16, D2), so a judgement naming it could no longer be
          a prediction (``_hindsight_reason``);
        * no retained forecast window reads the author of an order it placed.
        """
        debt = self.queue.owed(handle)
        if debt is not None:
            return debt
        if live["referrers"].get(handle):
            return "a retained decision names it as its parent or judged subject"
        if handle in live["named"]:
            return "a live book still reads it"
        if not self.consequences.releasable(handle):
            return "its consequence account is open"
        try:
            account = self.consequences.table.account(handle)
        except KeyError:
            account = None
        if (account is not None and not account.voided
                and (account.opened_at_ns is not None or account.opened_at_tick is not None)
                and not self._past_horizon(account)):
            return "its realized-consequence horizon has not passed"
        if handle in live["order_owners"]:
            return "a retained forecast window reads an order it placed"
        return None

    def _released_owner(self, handle: str) -> str | None:
        """The seat a released decision's late money is booked to: the seat that
        authored it while it is live, else its lineage's root while that is live, else
        None (wave 17b): no persistent holder is left, ``_late_undeliverable``."""
        seat = self.consequences.table.released_author(handle)
        if seat is None:
            return None
        for candidate in (seat, self.budget.lineage(seat)):
            if candidate in self.assemblies and candidate not in self.retired_assemblies:
                return candidate
        return None

    def _late_undeliverable(self, handle: str, micro: int) -> None:
        """Late money no live seat can be credited with, on the record with its amount.

        Guarantees the venue custody that booked the money (``venue.settled``) keeps
        it, unattributed, so custody conserves; ``consequence.late_undeliverable``
        names the decision and the amount, and the inbox's failed delivery is
        ledgered as before. No reward credit moves: the decision's grade was fixed.
        """
        self.ledger.append({"kind": "consequence.late_undeliverable", "handle": handle,
                            "micro": micro, "reason": "no live seat owns that decision",
                            "ts": self.clock.now_ns})
        self._undeliverable("late_realization", handle, "no live seat owns that decision")

    # -- release -------------------------------------------------------------------

    def _release_horizon_ticks(self) -> int:
        """How long a tombstone is kept: the reward chain's own horizon, past which
        "nothing opens on these any more" (``_settle_evaluations``)."""
        return self.ev.consequence_backstop_ticks + self.ev.verdict_timeout_ticks

    def _inbox_retention_ticks(self) -> int:
        """How long an unacknowledged inbox item is kept: ``min_ratio`` reward-chain horizons.

        Published in the world's ``storage`` schematic. A loop period is a ratio,
        never an absolute constant (essay II.IV.c): the inbox is the outer loop of
        the reward chain whose settlements it carries, so it holds an item at least
        ``min_ratio`` times the horizon past which nothing opens on a decision.
        """
        return self.m.timing.min_ratio * self._release_horizon_ticks()

    def _release_inbox(self) -> int:
        """Release every inbox item acknowledged or past ``_inbox_retention_ticks``."""
        return self.outcomes.release_items(
            before_tick=self.ticks_consumed - self._inbox_retention_ticks())

    def _release_settled(self) -> list[str]:
        """Release every fully settled decision; return the handles released.

        Guarantees each released decision satisfied ``_score_owed`` at the moment it
        was released, that decisions are visited newest first (a child or a judge
        before the decision it names), so one pass releases a whole settled chain,
        and that the release is deterministic in the retained state alone.
        """
        self._drain_finalized()
        live = self._live_references()
        released = []
        for handle in reversed(self.queue.retained()):
            if self._score_owed(handle, live) is not None:
                continue
            self._release_one(handle, live)
            released.append(handle)
        if released:
            self._drop_released(released)
        horizon = self._release_horizon_ticks()
        from factorylab.runtime.clockwork import tick_ns

        before_ns = self.clock.now_ns - horizon * tick_ns(self.tick_clock)
        self.queue.compact_released(before_ns)
        # A released order is venue-confirmed terminal; it keeps its owner for the
        # published retention anyway, so a fill the venue reports on it in error is
        # still booked to that owner (``LotTable.fill``).
        self.consequences.forget_released_orders(
            self.ticks_consumed - self._inbox_retention_ticks())
        self._prune_vault_released()
        self._forget_claimed_polymarket()
        # A very late fill on a released order is booked at the venue under its owner
        # (``_order_owner``), which reopens a custody-delta entry nothing reads again.
        for handle in [h for h in self.venue_deltas if self.queue.is_released(h)]:
            del self.venue_deltas[handle]
        return released

    def _prune_vault_released(self) -> None:
        """Keep a released vault write's transaction only while a lookup can return it.

        Guarantees the retained set is bounded by the writes released within the
        lookup window: a vault lookup reads the venue's ledger from its earliest
        still-unbound peer's submission less ``LOOKUP_SKEW_NS`` (``_vault_lookup``),
        and every write still looking up was submitted at or before now. A
        transaction was on the venue's ledger by the time its write was released, at
        most ``LOOKUP_SKEW_NS`` of clock skew later; once that is before every window
        a lookup can still open, no lookup can offer it, so none can bind it again.
        """
        from factorylab.runtime.vault import LOOKUP_SKEW_NS

        if not self.vault_released_hashes:
            return
        looking = [int(i["since_ns"]) for i in getattr(self, "vault_intents", {}).values()
                   if not i.get("unresolved") and (
                       i["result"].get("status") == "uncertain"
                       or (i["result"].get("status") == "ok" and not i.get("settled")))]
        earliest = min([self.clock.now_ns, *looking]) - LOOKUP_SKEW_NS
        self.vault_released_hashes = [[transaction, ns] for transaction, ns
                                      in self.vault_released_hashes
                                      if ns + LOOKUP_SKEW_NS >= earliest]

    def _forget_claimed_polymarket(self) -> None:
        """Drop a released decision's Polymarket claim entries once all of it is claimed.

        A released decision's late realisation (wave 17b) re-opens its entry in the
        pot's claim book (``credit_realized``); once its owner has claimed it
        (``claim_share``), nothing reads the entry again.
        """
        surface = getattr(self, "polymarket", None)
        if surface is None:
            return
        for handle in [h for h, exact in surface.realized.items()
                       if self.queue.is_released(h)
                       and exact.numerator // exact.denominator == surface.claimed.get(h, 0)]:
            surface.realized.pop(handle, None)
            surface.claimed.pop(handle, None)

    def _release_one(self, handle: str, live: dict[str, Any]) -> None:
        """Release one decision from the kernel queue and the consequence table.

        Guarantees a decision a score is still owed to is never released: while
        ``_score_owed`` names a debt this raises ``ValueError`` naming it and changes
        nothing. The caller drops the decision's per-decision entries
        (``_drop_released``) once its pass is done.
        """
        debt = self._score_owed(handle, live)
        if debt is not None:
            raise ValueError(f"a score is still owed to {handle}: {debt}")
        decision = self.queue.get(handle)
        # The tombstone keeps the lineage of the seat that authored it; a router
        # abstention or a forecast bucket was authored by no seat.
        author = self.handle_to_assembly.get(handle)
        self.consequences.release([handle], self.ticks_consumed, authors={handle: author})
        self.queue.release(handle, author=self.budget.lineage(author) if author else None)
        if decision.parent_handle is not None:
            live["referrers"][decision.parent_handle] -= 1
        subject = self.decision_subjects.get(handle)
        if isinstance(subject, str) and subject != handle:
            live["referrers"][subject] -= 1

    def _drop_released(self, handles: list[str]) -> None:
        """Drop every per-decision entry of the released ``handles`` (``DROPPED_WITH_DECISION``),
        folding their order intents into ``released_intents`` and forgetting their
        forecasts, receipts, base-rate questions and tally evidence."""
        gone = set(handles)
        for name in DROPPED_WITH_DECISION:
            book = getattr(self, name, None)
            if book is None:
                continue
            for handle in handles:
                book.pop(handle, None)
        folded = self.released_intents
        for client_id in [c for c, intent in self.order_intents.items()
                          if intent.get("handle") in gone]:
            intent = self.order_intents.pop(client_id)
            folded["intents"] = folded.get("intents", 0) + 1
            status = (intent.get("result") or {}).get("status")
            folded[f"status:{status}"] = folded.get(f"status:{status}", 0) + 1
        # Terminal venue writes (``_live_venue_books``): a vault write's venue
        # transaction stays claimed, so no later write can bind it again.
        vault = getattr(self, "vault_intents", None)
        for client_id in [c for c, i in (vault or {}).items() if i.get("handle") in gone]:
            transaction = vault.pop(client_id)["result"].get("hash")
            if transaction:
                self.vault_released_hashes.append([transaction, self.clock.now_ns])
        surface = getattr(self, "polymarket", None)
        if surface is not None:
            dropped = {c for c, i in surface.intents.items() if i.get("handle") in gone}
            for client_id in dropped:
                del surface.intents[client_id]
            surface.order_ids = {o: c for o, c in surface.order_ids.items() if c not in dropped}
            for handle in handles:
                surface.realized.pop(handle, None)
                surface.claimed.pop(handle, None)
        self.book.release(handles)
        self.book.receipts.release(handles)
        self.consequences.receipts.release(handles)
        self.settler.forget(handles)
        self.outcomes.forget_said(handles)
        self.eligibility_evidence = {pair for pair in self.eligibility_evidence
                                     if pair[0] not in gone}

    # -- committee eligibility, as a running tally ---------------------------------

    def _tally_evidence(self, handle: str, assembly: str | None) -> None:
        """Count one observed consequence of ``handle`` for ``assembly``, once.

        Guarantees the tally equals the scan it replaced (``_committee_eligible_scan``):
        each distinct (handle, assembly) pair of evidence is counted once, and only
        for an independently requested decision (``_independent_decision``).
        """
        if assembly is None:
            return
        pair = (handle, assembly)
        if pair in self.eligibility_evidence:
            return
        self.eligibility_evidence.add(pair)
        if self._independent_decision(handle, assembly):
            self.eligibility_tally[assembly] = self.eligibility_tally.get(assembly, 0) + 1

    def _tally_payoff(self, payoff: Any) -> None:
        """A fixed, observed payoff of a verdict- or exposure-channel return is evidence."""
        if payoff.censored is not None:
            return
        if self.queue.get(payoff.handle).channel in (CH_VERDICT, "exposure"):
            self._tally_evidence(payoff.handle, self.handle_to_assembly.get(payoff.handle))

    def _drain_finalized(self) -> None:
        """Fold every settlement since the last drain into the tally, in order.

        A decision settled on its evaluation reward is evidence for its author; a
        consequence decision that settled observed is evidence for its parent.
        """
        for handle in self.queue.drain_finalized():
            decision = self.queue.get(handle)
            if any(r.status is SettleStatus.SETTLED and r.definition_version == DEF_EVALUATION
                   for r in self.queue.history(handle)):
                self._tally_evidence(handle, self.handle_to_assembly.get(handle))
            if (decision.channel == CH_CONSEQUENCE and decision.status is SettleStatus.SETTLED
                    and decision.parent_handle):
                self._tally_evidence(decision.parent_handle, self.handle_to_assembly.get(
                    decision.parent_handle, decision.actor))

    def _rebuild_eligibility_tally(self) -> None:
        """Rebuild the tally from the full scan, for a checkpoint older than the tally.

        Such a checkpoint released nothing, so the scan sees every decision.
        """
        self.queue.drain_finalized()
        self.eligibility_tally, self.eligibility_evidence = {}, set()
        for handle, assembly in self._eligibility_scan_pairs():
            self._tally_evidence(handle, assembly)
