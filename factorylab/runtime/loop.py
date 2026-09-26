"""The event loop dispatches public events through registered accepts/emits contracts.

The loop owns no money, no scores and no rules; it is glue over kernel
physics.

Contracts. Any assembly may accept any event kind and select a declared
output kind. Producer-shaped and custom returns settle on the mean of their
judges' verdicts. A verdict is also a prediction: a judge settles on its grade
from the tier above and on the world's score of its verdict, and a meta the same
way one tier up (ruling R1); exposures earn by how wrong the judges were. The
shipped registrations preserve the seeded evaluation chain. Unjudged returns are
censored, and retirement preserves delayed feedback.

Prices. At each reserve-window boundary the runtime measures the window
that closed using the factory's observation vocabulary — the twenty-three seeds
and whatever measurements the population has registered —
and hands each priced metric card one observation. The price controller
revises a bounded λ per card; verdict and conformity
scores settle net of Σ λ·violation, clipped to [0, 1]. The consequence and
exposure channels, the novelty reserve and router exploration are outside
its authority.

Registration. Any return may carry proposals. Well-formed ones are paid from
the novelty reserve, registered with the proposing decision as provenance,
and announced. Adding an assembly opens a new comparator epoch for every
router whose menu grew.
"""

from __future__ import annotations

import json
from collections import deque
from copy import deepcopy
from typing import Any

from factorylab.cortex.assembly import (
    COUNTERFACTUAL_FIELD,
    DECLINE_FORM,
    FORWARDED_RATIONALE_CHARS,
    JUDGING_FIELDS,
)
from factorylab.cortex.registration import BUILTIN_RETURNS, measured_role
from factorylab.cortex.request import Return, public_return
from factorylab.cortex.sandbox import NoJail, jail_probe
from factorylab.cortex.schematics import SchematicsMixin
from factorylab.kernel.events import Event, EventKind
from factorylab.kernel.ledger import canonical
from factorylab.kernel.queue import SettleStatus
from factorylab.kernel.termination import DORMANT
from factorylab.learners.router import Sample
from factorylab.runtime.bootstrap import BootstrapMixin
from factorylab.runtime.cadence import settle_forecasts
from factorylab.runtime.chaos import ChaosMixin
from factorylab.runtime.composition import CompositionMixin
from factorylab.runtime.compute import ComputeMixin
from factorylab.runtime.feedback import (
    FeedbackMixin,
    PendingJudgement,
)
from factorylab.runtime.governance import GovernanceMixin
from factorylab.runtime.live import LiveClock, Reconciler, wall_paced
from factorylab.runtime.markets import MarketsMixin
from factorylab.runtime.pricing import PricingMixin
from factorylab.runtime.resume import decode, encode, runtime_state
from factorylab.runtime.routing import (
    JUDGING_SHAPES,
    ContractQueue,
    PopulationEvent,
    RoutingMixin,
)
from factorylab.runtime.settled import RELEASED_REFUSAL, SettledMixin
from factorylab.runtime.shared import (
    CH_CONFORMITY,
    CH_EXPOSURE,
    CH_FAST,
    CH_VERDICT,
    DEF_CONFORMITY,
    DEF_COUNTER,
    DEF_FAST,
    DEF_VERDICT,
    NOOP,
    _to_plain,
    assembly_rewards,
    declined_reason,
)
from factorylab.runtime.subscriptions import SubscriptionBook, ThinkingMixin
from factorylab.runtime.summary import SummaryMixin, _as_unit
from factorylab.runtime.uptake import UptakeMixin
from factorylab.runtime.vault import VaultMixin
from factorylab.runtime.venue import VenueMixin
from factorylab.runtime.worlds import WorldManifest
from factorylab.settlement.vocabulary import commission_block
from factorylab.world.clock import ClockSource, merge_sources
from factorylab.world.events import WorldEvent, WorldEventKind, funding_instant
from factorylab.world.market import X402Provider

#: What a return carries that is not the work under judgement: its propensity, which
#: the request's PROPENSITY block renders once (P8).
UNJUDGED_OUTPUT_FIELDS = frozenset({"propensity"})
#: The seed kinds a judge reads through the producer view (the machine view of
#: essay II.I.b): the one kind a producer emits and the one an antagonist emits.
PRODUCING_KINDS = frozenset({"ProducerReturn", "Exposure"})


def judged_outputs(outputs: Any) -> Any:
    """A return's outputs as a judge reads them: the work, without its sealed side-claims."""
    if not isinstance(outputs, dict):
        return outputs
    return {k: v for k, v in outputs.items() if k not in UNJUDGED_OUTPUT_FIELDS}


def judge_view(payload: dict[str, Any]) -> dict[str, Any]:
    """An event payload as a judge reads it.

    Guarantees judged outputs lose ``UNJUDGED_OUTPUT_FIELDS`` and the payload's own
    ``propensity`` is left to the PROPENSITY block. Everything else is the event as
    emitted: a judge should "simply look at it like a machine: input, output"
    (essay II.I.b), and no event payload names its author.
    """
    out: dict[str, Any] = {}
    for key, value in payload.items():
        if key == "propensity":
            continue
        if key in ("outputs", "producer_outputs"):
            value = judged_outputs(value)
        out[key] = value
    return out


#: What a judge's INPUTS says in place of the fold the judged decision was shown: the
#: event is never rendered hollow (Chapter II §I.b, a self-describing request).
FOLD_WITHHELD = ("not carried to judges: the world changes the deciding seat was shown "
                 "since its last paid wake")


def forwarded_rationale(outputs: Any) -> dict[str, Any]:
    """A judgement's rationale as its published event carries it, the cut stated.

    Guarantees the first ``FORWARDED_RATIONALE_CHARS`` characters, and, when the
    rationale was longer, its full length beside them: the limit the judging
    contract publishes, never applied in silence (Chapter II §II.b).
    """
    text = str(outputs.get("rationale", "")) if isinstance(outputs, dict) else ""
    out: dict[str, Any] = {"rationale": text[:FORWARDED_RATIONALE_CHARS]}
    if len(text) > FORWARDED_RATIONALE_CHARS:
        out["rationale_chars"] = len(text)
        out["rationale_forwarded_chars"] = FORWARDED_RATIONALE_CHARS
    return out


class Runtime(
    # Anticipatory settlement of registrations (time audit T18) reads registrations,
    # tool calls and invocations, and posts through the markets' refusals.
    UptakeMixin,
    # The charter's markets (charter audit M1, M2) read and publish through the
    # governance, pricing and schematics methods below them, so they come first.
    MarketsMixin,
    # The chaos actuator (evaluations M1) only withholds or ages what seats are shown.
    ChaosMixin,
    SchematicsMixin,
    ThinkingMixin,
    RoutingMixin,
    CompositionMixin,
    GovernanceMixin,
    VenueMixin,
    VaultMixin,
    PricingMixin,
    FeedbackMixin,
    SummaryMixin,
    # Release of fully settled decisions (wave 17b) reads every book above it.
    SettledMixin,
    BootstrapMixin,
    ComputeMixin,
):
    """One world, from launch to the end of its event budget or its death."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.queue = ContractQueue(self.queue, self)
        # Edition 3, C2: who is awake, what each seat has not read yet, and the
        # watcher predicates the kernel settles without a model call.
        self.subscription_book = SubscriptionBook()
        self._init_fidelity()

    def _invoke(self, action_id, req, role, *, child=False):
        """Retain one typed measurement sample for each completed return."""
        tool_calls = self.window.tool_calls
        ret = super()._invoke(action_id, req, role, child=child)
        self.card_samples.returned(handle=req.handle, assembly=action_id,
                                   role=measured_role(
                                       self.return_kinds.get(req.handle)
                                       or self.assemblies[action_id].spec.emits),
                                   window=self.window.index, ret=ret)
        self.card_samples.returns[-1]["tool_calls"] = self.window.tool_calls - tool_calls
        self._record_reading(req.handle, ret)
        return ret

    def _record_reading(self, handle: str, ret) -> None:
        """File a reader's INPUTS bytes under the author of the return it was commissioned on.

        Essay II.IV.a (the metrics layer is ceded) and II.I.b (minimal disclosure):
        a decision routed on a published return reads that return in its INPUTS, so
        those bytes are a fact about the return as much as about the reader. The
        kernel files them under the author's scope in the window the reading was
        metered; the reader's request is not touched, so nothing about the author
        reaches it. A decision with no subject, a subject no assembly authored, an
        invocation for which the runtime rendered no prompt, or one
        whose request never reached its executor (refused over its ceiling, its
        reservation refused, the world terminal: ``Return.delivered`` is False),
        records nothing: no reader read those bytes.
        """
        sections = getattr(ret, "prompt_sections", None)
        subject = self.decision_subjects.get(handle)
        author = self.handle_to_assembly.get(subject) if subject is not None else None
        if (not sections or not getattr(ret, "delivered", False) or author is None
                or subject == handle):
            return
        read = int(sections.get("inputs", 0))
        self.window.downstream_read_bytes += read
        self.card_samples.read(handle=subject, assembly=author,
                               role=self._decision_role(subject),
                               window=self.window.index, read_bytes=read)

    def _settle_due_forecasts(self) -> None:
        """A6 sampling and A2 cadence openings both wrap the one settlement implementation."""
        from copy import deepcopy

        pending = {f.handle: f for f in self.book.pending()}
        baseline = deepcopy(self.baseline)
        settle_forecasts(self, super()._settle_due_forecasts)
        self._record_card_forecasts(pending, baseline)

    def _settle_priced(self, handle, *, cards, **kwargs):
        """Custom emitted kinds answer for their own cards on every reward shape.

        A decision that settles here carries its raw (pre-penalty) score to what it
        composed: the settlement hook every path shares (``ContractQueue.settle``,
        ``CompositionMixin._settled``) reads it from ``raw_scores``, so each decision
        answers for its own cards and credit is never charged the requester's. The raw
        score stays until the router and the seat's own learner have read it (wave 16,
        D4 and R10-g; ``PricingMixin._prune_price_evidence``).
        """
        emitted = self.return_kinds.get(handle)
        if emitted and emitted not in BUILTIN_RETURNS:
            cards = measured_role(emitted)
        self.raw_scores[handle] = kwargs["score"]
        return super()._settle_priced(handle, cards=cards, **kwargs)

    def _settle_exchange_effects(self, events, *, observe_positions=True,
                                 broadcast_mids=True) -> None:
        super()._settle_exchange_effects(events, observe_positions=observe_positions,
                                         broadcast_mids=broadcast_mids)
        self._record_pricing_fills(events)

    def _universe_for(self, kind: str, ev: Event | None = None) -> list[str]:
        """An assembly that can judge never sits on the router for its own subject.

        A contract that emits only producing kinds may still process its own
        event (it continues its own work); one whose contract includes Verdict or
        MetaVerdict would be woken, paid, and refused at ``_judged_event``, so it
        is excluded before the draw whatever else it emits.
        """
        universe = super()._universe_for(kind, ev)
        if ev is None:
            return universe
        excluded = self._subject_authors(kind, ev)
        return [a for a in universe
                if a == NOOP or a not in excluded
                or not set(assembly_rewards(self.assemblies[a].spec).values())
                & JUDGING_SHAPES]

    def _novelty_compute(self, handle: str, reason: str) -> bool:
        """A requested child spends its parent's money, never the protected share.

        The novelty reserve funds unhistoried actions the router chose; a decision
        nested under another decision (a child request) is the parent's
        subcontracting and is classified like the parent's ordinary spending.
        Policy ballots keep their own classification: a committee seat is the
        kernel's request, not the proposer's.
        """
        try:
            decision = self.queue.get(handle)
        except KeyError:
            return False
        if decision.parent_handle is not None and decision.channel != "policy":
            return False
        return super()._novelty_compute(handle, reason)

    def run(self) -> dict[str, Any]:
        """Keep exclusive ledger ownership through the last runtime action or process death."""
        from factorylab.runtime import polymarket

        try:
            # A live Polymarket reader is admitted, and holds the host's IP, before the
            # world's first event; an offline one takes nothing.
            polymarket.arm(self)
            return self._run()
        finally:
            self._ledger_lock.close()
            polymarket.disarm(self)

    def _run(self) -> dict[str, Any]:
        """Continue the original source budget; restored internal events keep their ordering."""
        if self.termination.final or (self.started and self._check_termination()):
            return self._summary()
        self.ledger.active = True
        if self.clock_source is None:
            self.tick_clock.count = self.events_budget
        if self.clock_source is not None and not hasattr(self.clock_source, "events"):
            sources = [self.clock_source]
        else:
            sources = [self.tick_clock.events()]
        if (
            isinstance(self.tick_clock, ClockSource)
            and self.clock_source is None
            and len(sources) > 1
        ):
            stream = self.tick_clock.events(sources[1])
        else:
            stream = merge_sources(*sources)
        if not self.started:
            if self._snapshot("launch") is False:
                raise ValueError("launch snapshot unavailable")
            self._launch()
        # A world funded at or below its floor is already dead: its first event would
        # drip into a dead wallet and abandon a persistent ledger unsealed instead.
        if self._check_termination():
            return self._summary()
        while True:
            ev = self._next_event(stream)
            if ev is None or not self._process_event(ev):
                break
        if self.kill_at_end and not self.termination.final:
            # a budgeted rehearsal world ends by explicit kill so its diary becomes readable
            self._finish_budget()
        return self._summary()

    def _launch(self) -> None:
        """Publish Launch only after a recoverable pre-launch snapshot exists."""
        self.bus.publish(
            Event(
                "launch",
                EventKind.LAUNCH,
                self.clock.now_ns,
                {"manifest_hash": self.m.manifest_hash(),
                 "launch_nonce": self.launch_nonce,
                 # A checkpoint written before release identity replays the exact
                 # historical Launch, which carried no digest.
                 **({"release_digest": self.release_digest}
                    if self.release_digest is not None else {}),
                 # The facilitator every paid service call settles through; a
                 # checkpoint from before the pin replays its historical Launch.
                 **({"facilitator_url": self.facilitator_url}
                    if getattr(self, "facilitator_url", None) is not None else {}),
                 # Whether a death witness receiver was configured at launch, and
                 # which one (the hash of its URL). Part of the launch identity, so
                 # it cannot be removed afterwards by unsetting a variable (R3-C);
                 # a world launched without one ledgers nothing, exactly as before.
                 **({"witness_required": True, "witness_receiver": self.witness_receiver}
                    if getattr(self, "witness_required", False) else {}),
                 "manifest": json.loads(self.m.canonical_json())},
                "kernel",
            )
        )
        # Release offsets count from the Launch just ledgered (C1).
        self.wallet.launch(self.clock.now_ns)
        self._refresh_settlement_references()
        self.started = True

    def _process_event(self, ev: Event) -> bool:
        """Normal execution and recovery use identical transitions after a durable input item."""
        previous_window = self.reserve_window_start
        self.n += 1
        self.clock.now_ns = max(self.clock.now_ns, ev.ts_ns)
        # The safety path's own clock and latch restart with every event (time audit T8).
        self._safety_ns = self.clock.now_ns
        self._safety_stop = None
        self.bus.publish(ev)
        self.stats.events += 1
        self.events_log.append({"kind": str(ev.kind), "payload": _to_plain(ev.payload)})
        self._fold_world_event(ev)
        if ev.kind is EventKind.MARKET_MID:
            coin = str(ev.payload.get("coin"))
            dq = self.recent_mids.setdefault(coin, deque(maxlen=20))
            dq.append({"t_s": ev.ts_ns // 1_000_000_000, "mid": str(ev.payload.get("mid"))})
            if self._fee_schedule_due():
                self._read_fee_schedule()
            # The venue's clock: a named trade is measured at the first mid at or after
            # its horizon (wave 16, D2).
            self._observe_mid(coin, ev.ts_ns, str(ev.payload.get("mid")))
        elif ev.kind is EventKind.FUNDING and ev.payload.get("rate") is not None:
            # At the funding time the rate is for, never the instant the venue reported
            # it (a tape reports a crossed boundary at its advance time).
            self._observe_funding(str(ev.payload.get("coin")),
                                  funding_instant(ev.payload, ev.ts_ns),
                                  str(ev.payload.get("rate")), ev.payload.get("mark"))

        # Due tranches are mandatory even while dormant; each released tranche is then
        # classified (C10): base_share across live seats, the remainder unallocated.
        released = self.wallet.release_due(self.clock.now_ns)
        if released:
            self.budget.on_release(released)
        self._manage_reserve_window()
        # The Venice and forwarding-fee caps count their own wall-clock windows, never
        # the pricing window (time audit T1, T13).
        self.treasury.open_window(self._cap_window())
        # At each window boundary: when no live seat can act from its entitlement,
        # release the commons to the seats, or, with nothing left to release, let
        # the termination rule below see the starvation (P1-04).
        starved = (previous_window != self.reserve_window_start) and self._commons_check()
        if previous_window is not None and previous_window != self.reserve_window_start:
            self._sampling_actuator()
        self._observe_delivered_event(ev)
        if ev.kind is EventKind.TICK:
            # Every world fact through the previous tick was delivered before this one
            # (the internal queue drains first): the floor of what is known complete.
            self.tick_through_ns, self.last_tick_ns = self.last_tick_ns, ev.ts_ns
            self.consequences.tick_through_ns = self.tick_through_ns
            self._open_pending_epochs()
            self._assign_waiting_readers()
            self._prune_read_use()
            self._chaos_tick()  # seat-facing faults only (runtime.chaos), drawn per tick
            self._reconcile_orders()
            if getattr(self, "polymarket", None) is not None:
                from factorylab.runtime import polymarket

                polymarket.tick(self)  # its own intents, fills and resolutions
            self._collect_income()  # C10: each receipt credits its owning seat before the tick
            self._tick_treasury()
            self._classify_financing()  # a conversion confirmed this tick is spendable now
            self._reconcile_x402()
            if self.venue is not None:
                observed = [
                    we
                    for we in self.venue.on_tick(self.clock.now_ns, include_fills=False)
                    if we.kind is not WorldEventKind.FILL
                ]
                observed.extend(
                    WorldEvent(
                        WorldEventKind.FILL, max(self.clock.now_ns, ts), self.exchange.name, payload
                    )
                    for ts, payload in self.consequence_fills.poll(
                        self.exchange, now_ns=self.clock.now_ns)
                )
                self._settle_exchange_effects(observed)
                if self.reconciler.due():
                    snap = Reconciler.snapshot(
                        self.wallet.balance,
                        self.provider,
                        self.exchange,
                        pots_view=self.treasury.refresh_pots(),
                        ledger=self.ledger,
                    )
                    self.stats.reconciliations += 1
                    self._emit(EventKind.RECONCILED, snap, source="kernel")
            else:
                self._settle_exchange_effects(self._advance_venue(self.clock.now_ns))
            # C2: the kernel settles every registered watcher from world state, then
            # offers one coalesced update to the seats that asked
            # for one. Both are queued behind this tick's own routing.
            self._evaluate_watchers()
            self._emit_world_update()
        if self._check_termination(starved=starved):
            return False

        if self.dormancy is None:
            self._compute_routed = False
            self._compute_unaffordable = False
            self._route(ev)
            self._record_insolvency_event(ev)
            if self._check_termination():
                return False
        # Dormant (C2): no paid cognition is routed; the maintenance below still runs,
        # the fills, treasury and releases above already did.

        # Every verdict this event's routing produced is in: each return it judged
        # settles on their mean (ruling R1).
        self._settle_arrived_verdicts()
        self._settle_due_forecasts()
        self._censor_stale_judgements()
        # A requested child settles once its requester has (the collaboration credit).
        self._settle_composed()
        self.stats.timeouts += len(self.queue.expire_due())
        self._deliver_returns()
        self.ledger.append({"kind": "runtime.event_done", "n": self.n, **self._pace_record()})
        self.balance_at.append(self.wallet.balance)
        if previous_window != self.reserve_window_start:
            self._snapshot("reserve_window")
        return True

    def _pace_record(self) -> dict:
        """What an idle-skipping clock read at the end of this event, for the diary.

        Empty for every other clock, so their diaries are unchanged. On a replay the
        clock adopts the recorded reading and totals first and the recorded values are
        written back as they were, so the replayed tail leaves the clock exactly where
        the recorded run's stood (Chapter II §IV.c: the resumed world keeps the pace
        it ran at, and never counts replayed busy time as idle).
        """
        record = getattr(self.tick_clock, "pace_record", None)
        if record is None:
            return {}
        saved = self.ledger.peek() if getattr(self.ledger, "recovering", False) else None
        if (saved is not None and saved.get("kind") == "runtime.event_done"
                and isinstance(saved.get("clock"), dict)):
            self.tick_clock.adopt(saved["clock"])
            return {"clock": saved["clock"]}
        return {"clock": record()}

    def _paced(self) -> bool:
        """Whether this world's ticks are paced by an environment whose gaps are measured.

        A live world is paced by wall time; a replay of a diary's delivered gaps
        (``fastloop --gaps-from``) by that world's. A virtual clock ticking at
        exactly the declared interval has no environment pace to keep up with.
        """
        return callable(getattr(self.tick_clock, "measured_interval_ns", None))

    def _call_deadline_s(self) -> float | None:
        """A model call's deadline: ``min_ratio`` delivered ticks, in seconds (time audit T8).

        Chapter II §IV.c: "the factory is expected to outrun the world", and it
        "cannot move slower than its environment". Where the environment's pace is
        measured (``_paced``) a call may take no longer than one period of the
        fastest outer loop over the delivered tick, read through the journal so a
        replay states the same deadline. An unpaced world has no environment pace:
        there only the adapter's finite ceiling applies (None).
        """
        if not self._paced():
            return None
        return self.m.timing.min_ratio * self.wall.tick_ns() / 1_000_000_000

    def _call_expired(self, handle: str, timeout_s: float | None) -> None:
        """A call that outlived its deadline is its decision timing out, in tick terms.

        The decision's cutoff is reached now: the queue records the timeout, its
        router is credited at the cutoff like any other (zero consequence, T4), and
        a return that settles it later settles it without training a learner twice.
        """
        try:
            pending = self.queue.get(handle).status is SettleStatus.PENDING
        except KeyError:
            return
        self.ledger.append({"kind": "decision.call_expired", "handle": handle,
                            "timeout_s": timeout_s, "tick": self.ticks_consumed,
                            "ts": self.clock.now_ns})
        if pending:
            self.stats.timeouts += len(self.queue.time_out([handle], self.clock.now_ns))

    def _safety_pass(self) -> None:
        """Fills, order state, watchers and a terminal stop never wait behind a model call.

        Chapter II §IV.c (requisite velocity); time audit T8. Before every model
        call (and so between every tool round), once a delivered tick of wall time
        has passed since the event began or since the last pass, the kernel reads
        the venue's fills and settles them, reconciles resting orders and settles
        every watcher from world state, without a model call and without a thread.
        A terminal state the pass sees is latched: every later call in this event is
        refused unbilled, so the wind-down the event's termination check starts is
        not held behind them. A simulated world's clock does not move inside an
        event, so it never needs one.
        """
        from factorylab.world.metering import UnbilledFailure

        if self._safety_stop is not None:
            raise UnbilledFailure(f"world is terminal ({self._safety_stop}): no further calls")
        if self.live:
            if self.venue is None:
                return
        elif not (wall_paced(self.tick_clock) and getattr(self.exchange, "opens_ns", None)):
            # A simulated world whose clock does not move inside an event never needs a
            # pass; one paced by the wall (an idle-skipping replay) does, when its venue
            # prices by time (a recorded tape) and can be read at any instant.
            return
        now = self.wall.now_ns()
        ended = self._tape_ended(now)
        if not ended and now - self._safety_ns < self.wall.tick_ns():
            return
        self._safety_ns = now
        closes = None if self.live else getattr(self.exchange, "closes_ns", None)
        if closes is not None:
            now = min(now, int(closes))  # a recorded world's time ends with its tape
        self.clock.now_ns = max(self.clock.now_ns, now)
        if self.live:
            fills = [WorldEvent(WorldEventKind.FILL, max(now, ts), self.exchange.name, payload)
                     for ts, payload in self.consequence_fills.poll(self.exchange,
                                                                     now_ns=now)]
        else:
            # The recorded market moved while a model thought: whatever it filled,
            # refused or charged by now settles here, and its mids are accounted; the
            # seats read mids afresh at the next tick, as on the live path.
            fills = self._advance_venue(now)
        # Every fact of the advance is accounted (the watermark covers it), and the
        # pass broadcasts no mid of its own to the seats (Codex on #152).
        self._settle_exchange_effects(fills, broadcast_mids=False)
        self._reconcile_orders()
        self._evaluate_watchers(sweep=f"safety-{now}")
        terminal = self.termination.check(self.wallet, self.clock.now_ns,
                                          cheapest_seat_micro=self._cheapest_seat_micro())
        terminal = terminal if terminal not in (None, DORMANT) else None
        if terminal is None and ended:
            from factorylab.world.tape import TAPE_ENDED

            terminal = TAPE_ENDED
        self.ledger.append({"kind": "safety.pass", "fills": len(fills), "tick": self.ticks_consumed,
                            "terminal": terminal, "ts": now})
        if terminal is not None:
            self._safety_stop = terminal
            raise UnbilledFailure(f"world is terminal ({terminal}): no further calls")

    def _tape_ended(self, now: int | None = None) -> bool:
        """Whether a recorded world's paced clock has reached its tape's end; latched.

        Reads the wall through the journal (a replay reads the same instant). Once it
        has, the world is terminal (``TAPE_ENDED``): every later model call of the event
        is refused unbilled, every venue write is refused with the published fact that
        the recorded market has ended, and the event's termination check kills the
        world, closing it through the tape's end. Never true for a live world, or for
        a world whose clock does not move inside an event.
        """
        from factorylab.world.tape import TAPE_ENDED

        if self._safety_stop == TAPE_ENDED:
            return True
        closes = None if self.live else getattr(self.exchange, "closes_ns", None)
        if closes is None or not wall_paced(self.tick_clock):
            return False
        now = self.wall.now_ns() if now is None else now
        if now < int(closes):
            return False
        self._safety_stop = TAPE_ENDED
        return True

    def _cap_window(self) -> int:
        """The index of the treasury caps' own window: ``treasury.cap_window`` wall time.

        Money rails run in wall time, so a rate cap on them is a wall-clock duration
        counted from launch (time audit T1, T13). The duration is a declared money
        bound, a cast of the Stackelberg move, not a period derived from a measured
        loop: it only no longer borrows the pricing window. A world resumed from a
        checkpoint that predates the anchor continues from the window its treasury
        last opened.
        """
        span = self.m.treasury.cap_window_ns
        if self.cap_anchor_ns is None:
            self.cap_anchor_ns = self.clock.now_ns - max(0, self.treasury.venice_window - 1) * span
        return 1 + max(0, self.clock.now_ns - self.cap_anchor_ns) // span

    def _forward_wait_ticks(self) -> int:
        """The ticks a stalled conversion may wait before it strands (time audit T13).

        ``min_ratio`` times the capital loop's p90 closure once enough conversions
        have finalized to support one (``timing.min_support``); until then the
        manifest's declared floor. A strand is an outer loop over the rail's own
        delivery, so it waits the ratio every other outer loop keeps.
        """
        measured = self.cadence.capital_period_events()
        if measured is None:
            return self.m.treasury.forward_wait_ticks
        return self.m.timing.min_ratio * max(1, measured)

    def _tick_treasury(self) -> None:
        """Advance the treasury one tick on the capital loop's own clock (time audit T13).

        Every conversion that finalizes this tick is one closure of that loop,
        measured in ticks consumed (an outage consumes none), and joins the
        slowest period governance respects once the loop has enough support.
        """
        self.treasury.tick_index = self.ticks_consumed
        self.treasury.forward_wait_ticks = self._forward_wait_ticks()
        for result in self.treasury.tick(self.clock.now_ns) or ():
            ticks = result.get("latency_ticks") if isinstance(result, dict) else None
            if result.get("status") != "confirmed" or ticks is None:
                continue
            self.cadence.record_capital(transfer_id=result["transfer_id"],
                                        latency_ticks=ticks,
                                        latency_ns=result.get("latency_ns"))
            self.clockwork.record("capital", ticks)

    def _snapshot(self, boundary: str) -> bool:
        """Persist a complete continuation at launch and after each boundary event finishes.

        Guarantees (wave 17): the state is one rolling checkpoint file beside the
        diary, durable before the ``snapshot`` item naming its SHA-256 and size is
        appended, and every older checkpoint file is removed only after that item
        is durable (``runtime/sidecar.py``). The item also carries what the
        checkpoint cost, in the clock the world is paced by, against the period
        it serves; a cost above ``1/min_ratio`` of that period is ledgered as a
        ``checkpoint.slow`` alarm (essay II.IV.c: an inner loop settles at least
        ``min_ratio`` times faster than the loop it serves; the apparatus may not be
        slower than its environment). The alarm is a fact, never a kill.
        """
        started = self.wall.now_ns()
        # Retained state no reader can reach is dropped here, at the one boundary a
        # live run and its replay share (``_prune_retained``).
        self._prune_retained()
        try:
            state = runtime_state(self)
            # Router states contain ordinary JSON floats as well as tagged codec values.
            from factorylab.cortex.assembly import _finite_json

            _finite_json(state)
            data = canonical(state)
        except (ValueError, OverflowError, RecursionError):
            self.ledger.append({"kind": "snapshot.refused", "boundary": boundary, "n": self.n,
                                "reason": "invalid checkpoint number or nesting"})
            return False
        reference = self.ledger.checkpoints.write(data)
        cost_ns = max(0, self.wall.now_ns() - started)
        tick = self.wall.tick_ns()
        # The checkpoint serves the price loop: one is written at each of its
        # boundaries. Before the loop's first period (launch) there is none to serve.
        period = (self.clockwork.period("price")
                  if self.clockwork.opened("price") is not None else None)
        cost = {"cost_ns": cost_ns, "tick_ns": tick, "period_ticks": period}
        if period is not None and cost_ns * self.m.timing.min_ratio > period * tick:
            self.ledger.append({"kind": "checkpoint.slow", "boundary": boundary, "n": self.n,
                                **cost, "min_ratio": self.m.timing.min_ratio,
                                "ts": self.clock.now_ns})
        self.ledger.append(
            {"kind": "snapshot", "boundary": boundary, "n": self.n, **reference, **cost}
        )
        # Durable in the chain: no resume can start from an older checkpoint now.
        self.ledger.checkpoints.retire_others(reference)
        self.ledger.io_store.sweep()
        # The checkpoint just made durable names no reference to anything released
        # before it, so those records may now be collected (kernel/artifacts.py).
        self.artifacts.seal_released()
        # The held venue reads are not in the checkpoint — the listing, the mids and
        # the account state are the venue's own facts, and a checkpoint is a
        # continuation, not a cache. Dropping them here is what makes it safe to leave
        # them out: a resume restores this checkpoint holding none of them, and the run
        # that wrote it holds none from this point either, so the replayed tail asks
        # the venue exactly where the recorded tail did.
        self._instruments_memo = None
        self._mids_memo = None
        # The tick's venue answers are not in the checkpoint either: the replayed tail
        # starts with none, and so does the run that wrote it.
        self._tick_reads = None
        self._polymarket_tick_reads = None
        self._account_memo = None
        self._peak_observed = None
        forget = getattr(self.treasury, "forget_observations", None)
        if forget is not None:
            forget()
        return True

    def _prune_retained(self) -> None:
        """Drop retained state no reader can reach; called only at a checkpoint boundary.

        Essay II.II.b: the disk and the memory are the world's own finite machine,
        a hard cast, and essay II.IV.c: the control apparatus may not become slower
        than its environment, as a checkpoint that grows with every event does.
        Pruning is deterministic and happens at the boundary a live run and its
        replay share (before the checkpoint is taken), so a resumed world holds
        exactly what the uninterrupted one holds. Each rule below removes only
        what no reader of that structure can reach, and says why.
        """
        self._prune_event_log()
        self._slim_return_events()
        self._release_read_deliveries()
        # Every decision no score is owed to any more (wave 17b, ``SettledMixin``), and
        # every inbox item acknowledged or past its published retention horizon.
        self._release_settled()
        self._release_inbox()

    def _release_read_deliveries(self) -> None:
        """Release, in the kernel queue, every delivery its one reader has already read.

        Readers of the deliveries addressed to a router learner (``router:<kind>``
        ids, the keys of ``delivered_seen``): ``_deliver_returns`` reads them from its
        cursor ``delivered_seen[lid]`` on, and ``_retain_router`` and
        ``_prune_price_evidence`` compare their count with that cursor. A cursor
        only advances, and a router id starting at 0 is fresh, never used before
        (``_fresh_router_id``). The deliveries of a seat (``assembly:<id>``: its
        ballots, testimony, posts and uptake) are read by its next ballot, from its
        cursor ``policy_seen[lid]`` on (wave 17b), and those delivered longer ago than
        the published retention are released unread. So every delivery before its
        reader's cursor is unreachable; the kernel keeps its count
        (``DecisionQueue.release_delivered``), and a decision whose deliveries are
        all read may be released once no other score is owed to it (``_score_owed``).
        """
        for lid, seen in self.delivered_seen.items():
            self.queue.release_delivered(lid, seen)
        # A seat's policy returns are read by its next ballot, once (wave 17b: a
        # verdict "is consumed as a reward signal ... and then discarded", essay
        # II.IV.c); ``policy_seen`` is that reader's cursor, and it only advances.
        for lid, seen in self.policy_seen.items():
            self.queue.release_delivered(lid, seen)
        # An actor that is no router and no live seat has no reader now: a forecast's
        # evaluator id (``open_forecast_decision``), a retired seat. Its deliveries are
        # discarded as made; the count stays, and a retired seat's cursor moves past
        # them, so an id versioned again reads on from there.
        live_seats = {f"assembly:{aid}" for aid in self.assemblies
                      if aid not in self.retired_assemblies}
        # A live seat that is not balloted within the published retention
        # (``outcome_retention_ticks``, the inbox's rule) is not shown the policy
        # returns delivered to it before then: they are released unread. Each
        # boundary marks how many had been delivered by its tick; a mark older than
        # the retention releases everything delivered by it (the inbox's rule).
        now, retention = self.ticks_consumed, self._inbox_retention_ticks()
        holding = self.queue.delivery_actors()
        for actor in holding:
            if actor in self.delivered_seen:
                continue
            count = self.queue.delivered_count(actor)
            if actor not in live_seats:
                self.queue.release_delivered(actor, count)
                if actor.startswith("assembly:"):
                    # A retired id can be versioned again and inherit its records: its
                    # next ballot reads on from here, never below what was discarded.
                    self.policy_seen[actor] = max(self.policy_seen.get(actor, 0), count)
                continue
            marks = self.policy_marks.setdefault(actor, [])
            if not marks or marks[-1][1] != count:
                marks.append([now, count])
            due = [mark for mark in marks if mark[0] < now - retention]
            if due and due[-1][1] > self.policy_seen.get(actor, 0):
                self.queue.release_delivered(actor, due[-1][1])
                self.policy_seen[actor] = due[-1][1]
            marks[:] = [mark for mark in marks if mark[0] >= now - retention]
        holding = set(self.queue.delivery_actors())
        for actor in [a for a in self.policy_marks if a not in holding]:
            del self.policy_marks[actor]

    def _slim_return_events(self) -> None:
        """Drop the payload of every published return no judgement can accept any more.

        Readers of ``return_events[R]`` (the event that published return R): only
        ``_judged_event`` and ``_hindsight_reason``. They read the event's kind, its
        subject field (``_event_subject``) and its ``tier``, to decide whether a
        judgement may address R and why not; the rest of the payload is read only by
        the caller of a judgement that was *accepted* (the verdict's
        ``producer_outputs``, a meta's judge handle). So once no judgement can ever
        accept R, the payload is unreachable, and the entry keeps exactly the fields
        the refusals read. Every refusal is then decided, and worded, as before.

        R can never be accepted again when all hold:

        * no event waiting in ``internal`` has R as its subject, so no judge will be
          delivered R by the router (the one path with no hindsight check); a later
          event about R replaces the entry whole when it is emitted;
        * R has no open judgement state (``pending``), so its tier is fixed by its
          kind and published tier alone;
        * the hindsight refusal is permanent: for a judged tier, R's consequence
          score is in (``consequence_scores`` never forgets one); for a return,
          its account is voided or its consequence horizon, counted on the world's
          clock from the nanosecond it opened, has passed (the horizon is the
          manifest's, fixed for the world's life, and the clock only advances).

        The key itself stays: a judgement may still name R, and is refused as before.
        """
        from dataclasses import replace

        queued = {self._event_subject(ev) for ev in self.internal}
        for about, event in self.return_events.items():
            key = {"Verdict": "evaluator_handle", "MetaVerdict": "by"}.get(
                str(event.kind), "about_handle")
            # A MetaVerdict is only a valid event with its handles, tier and score.
            kept = ((key, "tier", "about", "score") if event.kind is EventKind.META_VERDICT
                    else (key, "tier"))
            slim = {k: event.payload[k] for k in kept if k in event.payload}
            if event.payload == slim or about in queued or about in self.pending:
                continue
            if self._judged_tier(event, None):
                if about not in self.consequence_scores:
                    continue
            else:
                try:
                    account = self.consequences.table.account(about)
                except KeyError:
                    continue
                if not account.voided and not self._past_horizon(account):
                    continue
            self.return_events[about] = replace(event, payload=slim)

    def _prune_event_log(self) -> None:
        """Keep ``balance_at`` and ``events_log`` from the oldest open forecast's start.

        Readers (the whole list): ``_facts_for`` reads ``balance_at[start:n+1]`` and
        ``events_log[start+1:n+1]`` for a forecast being settled, where ``start`` is
        its ``made_at_event``; ``_chaos_fault`` writes into the current event's entry.
        Only forecasts in the book's unsettled set are ever settled, and every
        forecast sealed later is sealed at ``made_at_event = n`` or after. So the
        rule: entries before ``min(n, made_at_event of every unsettled forecast)``
        are unreachable, and dropped; ``event_log_base`` keeps event numbers exact.
        """
        keep = min([self.n, *(f.made_at_event for f in self.book.pending())])
        drop = keep - self.event_log_base
        if drop <= 0:
            return
        del self.balance_at[:drop]
        del self.events_log[:drop]
        self.event_log_base = keep

    def _resume_at(self, now_ns: int) -> None:
        """Reconcile and ledger outage timeouts before admitting another world event."""
        now_ns = max(now_ns, self.clock.now_ns)
        self.ledger.append({"kind": "resume.begin", "now_ns": now_ns, "n": self.n})
        self.clock.now_ns = now_ns
        self._reconcile_orders()
        available = self.tool_runner.available
        if self.ledger.recovering:
            saved = self.ledger.peek()
            available = (
                saved["available"] if saved and saved.get("kind") == "sandbox.availability"
                else self.tool_jail_available
            )
        if available != self.tool_jail_available:
            self.ledger.append({"kind": "sandbox.availability", "available": available})
            self.tool_jail_available = available
        if self.live:
            observed = [
                WorldEvent(WorldEventKind.FILL, max(now_ns, ts), self.exchange.name, payload)
                for ts, payload in self.consequence_fills.poll(self.exchange, now_ns=now_ns)
            ]
            observed.extend(self.venue.funding_payments(now_ns))
            self._settle_exchange_effects(observed)
            self._collect_income()
            self._tick_treasury()
            self._classify_financing()
        snapshot = Reconciler.snapshot(
            self.wallet.balance,
            self.provider,
            self.exchange,
            pots_view=self.treasury.refresh_pots() if self.live else self.treasury.pots(),
            ledger=self.ledger,
        )
        self.ledger.append({"kind": "resume.reconcile", **snapshot})
        if self.live:
            self.stats.reconciliations += 1
            self._emit(EventKind.RECONCILED, snapshot, source="kernel")
        # A cutoff counts world ticks (time audit T3): an outage consumed none, so only
        # a decision whose tick cutoff had already passed times out here. One restored
        # from a checkpoint that predates the tick record keeps its wall deadline.
        handles = [d.handle for d in self.queue.outstanding()
                   if (d.deadline_ns <= now_ns if self.queue.deadline_tick(d.handle) is None
                       else self.queue.deadline_tick(d.handle) <= self.ticks_consumed)]
        self.ledger.append({"kind": "resume.timeouts", "handles": handles, "n": self.n})
        self.stats.timeouts += len(self.queue.expire_due())
        self._deliver_returns()
        self.ledger.append(
            {
                "kind": "resume",
                "manifest_hash": self.m.manifest_hash(),
                "n": self.n,
                "resumes": self.stats.resumes + 1,
            }
        )
        self.stats.resumes += 1

    def _next_event(self, stream) -> Event | None:
        if self.internal:
            self.ledger.append({"kind": "runtime.input", "internal": encode(self.internal[0])})
            return self.internal.popleft()
        saved = self.ledger.peek()
        we = decode(saved["world"]) if saved and "world" in saved else next(stream, None)
        if we is None:
            return None
        self.ledger.append({"kind": "runtime.input", "world": encode(we)})
        self.world_consumed += 1
        if we.kind is WorldEventKind.TICK:
            self.ticks_consumed += 1
            if isinstance(self.tick_clock, (ClockSource, LiveClock)):
                # A clock that measures its delivered gaps (the wall clock, a replay of a
                # diary's gaps) measures a replayed tick too, so a resumed world converts
                # ticks at the interval the recording measured (time audit T3).
                last = self.tick_clock.last_ns
                gaps = getattr(self.tick_clock, "gaps", None)
                if gaps is not None and last is not None and 0 <= last < we.ts_ns:
                    gaps.append(we.ts_ns - last)
                self.tick_clock.index = self.ticks_consumed
                self.tick_clock.last_ns = we.ts_ns
        if isinstance(self.tick_clock, ClockSource):
            self.tick_clock.last_event_ns = we.ts_ns
        return self._kernel_event(we)

    def _kernel_event(self, we: WorldEvent) -> Event:
        self.emitted += 1
        return Event(
            f"ev-{self.emitted}", EventKind(str(we.kind)), we.ts_ns, dict(we.payload), we.source
        )

    def _emit(self, kind: EventKind | str, payload: dict[str, Any],
              source: str = "runtime") -> None:
        from factorylab.cortex.assembly import validate_schema

        if str(kind) not in {str(k) for k in EventKind}:
            if kind != "Exposure":
                if kind not in self.event_schemas:
                    raise ValueError("population event has no declared schema")
                if payload.get("status") == "ok":
                    # A producing kind's counterfactual is the kernel's field, not the
                    # declaration's (``_contract_schema``).
                    producing = self._kind_rewards().get(kind) in self.PRODUCING_SHAPES
                    validate_schema({k: v for k, v in payload["outputs"].items()
                                      if k not in ("emits", "register", "about_handle",
                                                   "status", "reason")
                                      and not (producing and k == "counterfactual")},
                                     self.event_schemas[kind])
            event_type = PopulationEvent
        else:
            kind = EventKind(kind)
            event_type = Event
        self.emitted += 1
        event = event_type(f"{str(kind).lower()}-{self.emitted}", kind,
                           self.clock.now_ns, payload, source)
        self.internal.append(event)
        subject = self._event_subject(event)
        if subject is not None and source == "runtime":
            self.return_events[subject] = event

    def _check_termination(self, *, starved: bool = False) -> bool:
        """Kill on a terminal reason; pause and resume paid cognition on the budget (C2).

        Dormancy is entered when the kernel reports ``budget_dormant`` or when the
        compute insolvency streak reaches its limit while locked backing and a
        scheduled release remain. It is left once the wallet can afford the
        cheapest seat again and, for an insolvency entry, a release has landed
        since, so a provider shortfall is not retried on the same money. Both
        transitions are ledgered before the state changes.

        ``starved`` is the window boundary's finding that no live seat can act
        from its entitlement and the commons has nothing left to release (P1-04):
        it counts as unaffordability, so the same rule applies, ``budget_dormant``
        (trigger ``entitlement``, left when a tranche lands) while a release is
        still due and terminal ``insolvency:entitlement`` otherwise.
        """
        now_ns = self.clock.now_ns
        reason = self.termination.check(self.wallet, now_ns,
                                        cheapest_seat_micro=self._cheapest_seat_micro())
        if reason == DORMANT:
            self._enter_dormancy(now_ns, trigger="wallet")
            return False
        if reason is None and self.dormancy is not None and (
            self.dormancy["trigger"] == "wallet"
            or self.wallet.released_tranches > self.dormancy["released"]
        ):
            self._exit_dormancy(now_ns)
        if reason is None and starved and self.dormancy is None:
            if self.wallet.locked > 0 and self.wallet.next_release_ns is not None:
                self._enter_dormancy(now_ns, trigger="entitlement")
                return False
            reason = "insolvency:entitlement"
        if reason is None and self._safety_stop is not None:
            # A terminal state the safety pass saw between model calls ends the world
            # through this one kill path, at the end of the event it stopped.
            reason = self._safety_stop
        if reason is None and self.insolvency_count >= self.m.treasury.insolvency_events:
            if self.wallet.locked > 0 and self.wallet.next_release_ns is not None:
                self._enter_dormancy(now_ns, trigger="insolvency")
                return False
            reason = "insolvency:compute"
        if reason is None:
            return False
        self._settle_arrived_verdicts()
        self._settle_due_forecasts()
        self.kill(reason)  # edition 3, C5: every runtime death winds the venue down
        return True

    def _commons_check(self) -> bool:
        """At a reserve-window boundary: can anybody act, and if not, is there commons to give?

        Routing skips a seat whose entitlement cannot cover its call, and that is the
        seat's own state, never the factory's, for as long as some seat can still
        act. When *every* live seat is skipped and at least one of them for its
        entitlement, nobody can think, and the unallocated pool is money nobody may
        spend. Then, once per boundary, the pool is released the way a tranche is,
        one equal share per live lineage to its head (``budget
        op="commons_release"``), and returns False: the heads act again on the
        next event. With the pool empty too, returns True, and the
        termination rule treats it as unaffordability. Nothing here weakens the
        per-seat rule while any seat can act. Dormant worlds route nothing and are
        not examined.
        """
        if self.dormancy is not None or self.termination.final:
            return False
        live = [a for a in self.assemblies if a not in self.retired_assemblies]
        if not live:
            return False
        entitlement_blocked = False
        for action_id in live:
            try:
                feasible, why = self._is_feasible(action_id)
            except Exception:  # noqa: BLE001 - a seat that cannot be probed cannot act
                continue
            if feasible:
                return False
            entitlement_blocked |= why.startswith("entitlement:")
        if not entitlement_blocked:
            return False  # a provider or wallet shortfall: the insolvency streak's business
        if self.budget.unallocated() > 0 and self.budget.commons_release("nobody can act"):
            return False
        return True

    def _cheapest_seat_micro(self) -> int | None:
        """The reserve ceiling of the cheapest live seat, as routing would probe it."""
        from factorylab.world.models import ModelRequest

        ceilings = []
        for action_id, asm in self.assemblies.items():
            if action_id in self.retired_assemblies:
                continue
            probe = ModelRequest(asm.spec.model_id, asm.spec.system_prompt,
                                 ({"role": "user", "content": ""},), asm.spec.max_tokens)
            try:
                ceiling = asm.model.ceiling(probe)
            except Exception:
                continue
            ceilings.append(ceiling * (1 if asm.spec.model_id.startswith("x402:") else 2))
        return min(ceilings) if ceilings else None

    def _enter_dormancy(self, now_ns: int, *, trigger: str) -> None:
        if self.dormancy is not None:
            return
        record = {"since_ns": now_ns, "trigger": trigger,
                  "released": self.wallet.released_tranches,
                  "next_release_ns": self.wallet.next_release_ns}
        self.ledger.append({"kind": "dormant", "state": "entered", "ts": now_ns,
                            "trigger": trigger, "locked": self.wallet.locked,
                            "unlocked": self.wallet.unlocked,
                            "next_release_ns": self.wallet.next_release_ns, "n": self.n})
        self.dormancy = record

    def _exit_dormancy(self, now_ns: int) -> None:
        self.ledger.append({"kind": "dormant", "state": "exited", "ts": now_ns,
                            "since_ns": self.dormancy["since_ns"], "locked": self.wallet.locked,
                            "unlocked": self.wallet.unlocked, "n": self.n})
        self.dormancy = None
        self.insolvency_count = 0

    def _start_return(self, handle: str) -> None:
        """Each decision has one consequence account before compute or effects."""
        try:
            self.consequences.table.account(handle)
        except KeyError:
            self.consequences.start(handle, self.n)

    def _assembly_step(self, ev: Event, handle: str, sample: Sample, deadline: int) -> None:
        """Only an assembly's declared output contract chooses its request and reward path."""
        self._start_return(handle)
        if sample.chosen == NOOP:
            # A router abstention is not an authored producer return.
            self.stats.noops += 1
            # It authored nothing, so it owes nothing: the consequence is voided
            # rather than costed. A costed abstention resolves a `return_paid_off`
            # for a decision no seat made, which can only be ledgered undeliverable.
            self.consequences.void(handle, self.n)
            self.queue.settle(handle, channel=self.queue.get(handle).channel, score=0.0,
                              status=SettleStatus.INAPPLICABLE,
                              definition_version=DEF_VERDICT, sampling_ref=None)
            return
        self.handle_to_assembly[handle] = sample.chosen
        subject = self._event_subject(ev)
        if subject is not None:
            self.decision_subjects[handle] = subject
        emits = self.assemblies[sample.chosen].spec.emits
        if emits == ("Verdict",):
            self._evaluator_step(ev, handle, sample, deadline)
        elif emits == ("MetaVerdict",):
            self._meta_step(ev, handle, sample, deadline)
        elif emits == ("CounterVerdict",):
            self._counter_step(ev, handle, sample, deadline)
        else:
            self._producer_step(ev, handle, sample, deadline)

    def _contract_schema(self, assembly_id: str) -> dict[str, Any]:
        """A polymorphic request publishes all its variants without choosing one for the model."""
        from factorylab.cortex.assembly import reserved_return_fields

        spec = self.assemblies[assembly_id].spec
        schemas = []
        for kind in spec.emits:
            if kind in spec.schemas:
                schema = _to_plain(spec.schemas[kind])
            elif kind in JUDGING_FIELDS:
                # A judging kind's answer form, as ``judging_contract`` builds it; its
                # decline form joins the union once, below.
                schema = self._judging_contract(kind)["anyOf"][0]
            else:
                fields = {"action": {"type": "string"}}
                schema = {"type": "object", "properties": fields, "required": list(fields)}
            # A producing kind's contract carries the declined trade (II.III.b). The
            # field is the kernel's, so it stands over a declaration's own.
            producing = ({"counterfactual": deepcopy(COUNTERFACTUAL_FIELD)}
                         if self._return_shape(spec, kind) in self.PRODUCING_SHAPES else {})
            schemas.append({**schema, "properties": {
                **{k: v for k, v in reserved_return_fields(
                    max_children=self.m.tools.max_children,
                    max_tool_calls=self.m.tools.max_tool_calls).items()
                   if k in ("requests", "tool_calls", "status", "reason")},
                **schema.get("properties", {}), **producing, "emits": {"enum": [kind]},
                "about_handle": {"type": "string"}, "register": self._register_schema(),
            }, "required": [*schema.get("required", []),
                            *(["emits"] if len(spec.emits) > 1 else [])]})
        if any(kind in JUDGING_FIELDS for kind in spec.emits):
            # The judging contract's decline form, published as its request publishes
            # it: the whole union, never its answer form alone (§II.b).
            schemas.append(deepcopy(DECLINE_FORM))
        return schemas[0] if len(schemas) == 1 else {"anyOf": schemas}

    def _hindsight_reason(self, handle: str, about: str) -> str | None:
        """Name why a chosen target cannot carry a prediction, or None if it can.

        A prediction precedes its outcome. The router's subject is not chosen, but a
        return that names an older target instead may not name one whose outcome the
        world has already given: a consequence already fixed, or one past the
        consequence horizon on the venue's clock (``_horizon_ns``; wave 16, D2). The
        judgement's own deadline is not compared: it lives until its target's
        consequence patience by construction, and a verdict is scored when the world
        answers, not when its decision expires. A judgement of an evaluator decision predicts
        that decision's consequence score (ruling R1), so it may not name one whose
        score is already known; its economic account says nothing about it.
        """
        target = self.return_events.get(about)
        if target is not None and self._judged_tier(target, self.pending.get(about)):
            if about in self.consequence_scores:
                return "judgement needs a chosen judgement whose consequence is still open"
            return None
        try:
            account = self.consequences.table.account(about)
        except KeyError:
            return None
        if account.voided:
            return "judgement needs a chosen return a seat authored, not an abstention"
        # A return that acted is answered when its payoff is fixed; one that did not
        # has an account fixed at once that says nothing about it (its measurement, if
        # any, is its declined trade's price), so only its price answers.
        if ((account.payoff is not None and self._acted(about))
                or about in self.world_outcomes):
            return "judgement needs a chosen return whose consequence is still open"
        if self._past_horizon(account):
            return "judgement needs a chosen return before its consequence horizon"
        return None

    def _past_horizon(self, account) -> bool:
        """Whether a return's consequence horizon has passed on the world's clock.

        Counted from the nanosecond the return opened (wave 16, D2); an account opened
        without a clock reading is aged in ticks at the delivered interval.
        """
        from factorylab.runtime.clockwork import tick_ns

        if account.opened_at_ns is not None:
            return self.clock.now_ns >= account.opened_at_ns + self._horizon_ns()
        opened = (account.opened_at_tick if account.opened_at_tick is not None
                  else self.ticks_consumed)
        return (self.ticks_consumed - opened) * tick_ns(self.tick_clock) >= self._horizon_ns()

    CHILD_SUBJECT_REFUSAL = ("a requested judgement may only address the requesting decision "
                             "or its ancestors; judging anyone else's return is the router's")

    def _child_subject_refusal(self, parent_handle: str, subject: Any) -> str | None:
        """Name why a child may not judge ``subject``, or None when it lies in the chain.

        Guarantees a judging child never reaches a stranger's return: the subject
        must be the requesting decision or one of its own ancestors, so the
        router's sampling, the adversarial share and the cascade stay the only
        way a return acquires a judge.
        """
        if isinstance(subject, str) and subject in self._ancestry(parent_handle):
            return None
        return self.CHILD_SUBJECT_REFUSAL

    def _refuse_judgement(self, handle: str, reason: str, about: Any = None) -> None:
        """A refused judgement is ledgered and its reason addressed to its author.

        The paid return is discarded, so the reason reaches the judge's own inbox
        under its handle, the way a refused propensity does.
        """
        self.ledger.append({"kind": "return.refused", "handle": handle, "reason": reason,
                            **({"about_handle": about} if about is not None else {})})
        self._refusal_to_owner(handle, "judgement_refused", reason)

    def _judged_event(self, ev: Event, handle: str, ret: Return,
                      *, predicts: bool = False) -> Event | None:
        """A judgement addresses a public return handle, excluding its complete ancestry.

        A value in about_handle that is not a return handle from the request —
        prose, a handle no public return carries, or the judgement's own decision —
        is not a choice of target: the delivered subject is judged instead, and the
        author is told why (evaluations P2: every meta that named its own handle was
        refused and graded zero). A verdict is a prediction (ruling R1), so one about
        a target anyone chose rather than the delivered subject must precede that
        target's outcome (``predicts``).
        """
        subject = self._event_subject(ev)
        about = ret.outputs.get("about_handle", subject)

        def addressable(value: Any) -> bool:
            try:
                self.queue.get(value)
            except (KeyError, TypeError):
                return False
            return True

        if (about != subject and isinstance(about, str) and about != handle
                and self.queue.is_released(about)):
            # Wave 17b: a decision no score was owed to any more was released; its
            # handle still answers, and the answer is that it settled and was released.
            self._refuse_judgement(handle, RELEASED_REFUSAL, about)
            return None
        if about != subject and not (isinstance(about, str) and about != handle
                                     and about in self.return_events and addressable(about)):
            # A value that names no return is not a choice of target: the judgement
            # stands about the return the router delivered, and its author is told
            # why its about_handle went unread.
            reason = ("about_handle must be a return handle from the request; the "
                      "delivered subject was judged instead")
            self.ledger.append({"kind": "about_handle.ignored", "handle": handle,
                                "about_handle": str(about)[:64], "subject": subject,
                                "reason": reason, "ts": self.clock.now_ns})
            self._refusal_to_owner(handle, "about_handle_ignored", reason)
            about = subject
        if not addressable(about):
            self._refuse_judgement(handle, "judgement needs an addressable return handle")
            return None
        target = self.return_events.get(about)
        if target is None and about == subject:
            target = ev
        author = self.handle_to_assembly.get(handle)
        if target is None or author in self._subject_authors(str(target.kind), target):
            self._refuse_judgement(
                handle, "judgement needs an independent, addressable return", about)
            return None
        parent = self.queue.get(handle).parent_handle
        # A requested judge addresses only the chain that requested it; the router,
        # not a paying parent, chooses who judges anyone else's return.
        if parent is not None and (
                reason := self._child_subject_refusal(parent, about)) is not None:
            self._refuse_judgement(handle, reason, about)
            return None
        # A child cannot judge the requester or any earlier request ancestor either.
        parents = self._ancestry(parent)
        if about in parents or self.handle_to_assembly.get(about) in {
            self.handle_to_assembly.get(p) for p in parents
        }:
            self._refuse_judgement(handle, "self-judgement: ancestor", about)
            return None
        # A verdict on a target anyone chose — the return itself, or the parent
        # that requested it — rather than the one the router delivered is a
        # prediction, so it must precede the outcome it will be scored on.
        if predicts and (about != subject or parent is not None) and (
                reason := self._hindsight_reason(handle, about)) is not None:
            self._refuse_judgement(handle, reason, about)
            return None
        self.decision_subjects[handle] = about
        return target

    def _producer_step(self, ev: Event, handle: str, sample: Sample, deadline: int,
                       *, returned: Return | None = None) -> None:
        self._start_return(handle)
        payload = _to_plain(ev.payload)
        if ev.kind is EventKind.TICK:
            try:
                acct = self._tick_account()
                payload["account"] = {
                    "equity_usd": str(acct.equity_usd),
                    "positions": [
                        {"coin": p.coin, "size": str(p.size), "entry_px": str(p.entry_px)}
                        for p in acct.positions
                    ],
                }
            except RuntimeError as exc:
                # Not "no account yet": an account nobody could read. Filling it
                # with the compute wallet's balance and an empty position list
                # invented equity and denied positions in the same breath
                # (GPT-6 Pro, third reading). It is reported as unavailable, like
                # the mids read below. R3-B landed this shape; R3-F's item 5 is
                # that nothing here substitutes wallet equity, and nothing does.
                payload["account"] = {"status": "unavailable", "reason": type(exc).__name__}
            try:
                payload["mids"] = {c: str(m) for c, m in self._tick_mids().items()}
            except RuntimeError as exc:  # VenueUnavailable and friends
                # A price the venue would not give is weather, not death, and it is
                # reported as unavailable rather than invented: the account read above
                # has always worked this way, and the mids read is the same kind of
                # fact. GPT-6 third reading, §11: "missing data stays unavailable".
                payload["mids_unavailable"] = type(exc).__name__
            self._seat_tick_view(payload)
        if ev.kind is EventKind.WORLD_UPDATE and sample.chosen != NOOP:
            # C2: the event carries the world every subscriber could have read; the
            # seat that was drawn reads its own fold, which reaches back to the last
            # time it woke rather than to the last tick.
            payload["since_you_last_woke"] = self.subscription_book.take(
                sample.chosen, now=self.tick_index)
        description = f"Respond to event {ev.kind} on {ev.source}."
        # What a judge of this return is told it answered: the event, and no clause
        # that names the author's role (information audit C1).
        judged_description = description
        inputs = {
            "kind": str(ev.kind),
            "payload": payload,
            "world": self._world_block(),
            "your_state": self.working_state.render(sample.chosen),
            "unread_outcomes": self.outcomes.unread(sample.chosen),
            **self._action_policy_input(sample.chosen),  # private
        }
        if sample.chosen == NOOP:
            self.stats.noops += 1
            ret = Return(handle, {"action": "noop"}, 0, "ok")
        else:
            # The wake describes itself (Chapter II §I, "the contract has to carry enough
            # self-description"): what its return is and where each answer's settlement
            # is published, as facts; no task and no preferred answer.
            spec = self.assemblies[sample.chosen].spec
            description += " " + self._wake_contract(
                {kind: self._return_shape(spec, kind) or "judged" for kind in spec.emits})
            # Physics, not a menu (smuggling A3): the kernel classifies every answer
            # for the ledger; the seat's own action ids stand beside the classes.
            description += (
                " The kernel classifies each answer for the ledger as hold, investigate, "
                "build, govern, defer or order; a propensity may be declared over those "
                "or over your own action ids. You own what wakes you: subscribe {kinds, "
                "coins, cadence_floor} changes it, defer: <n ticks> sleeps through routine "
                "ticks, and a fill or a safety event wakes you anyway."
            )
            schema = {
                "type": "object",
                "properties": {
                    "action": {"type": "string"},
                    "propensity": {"type": "object"},
                    "subscribe": {"type": "object"},
                    "defer": {"type": "integer", "minimum": 0},
                    # A contract field (II.III.b, the priced road not taken): the
                    # kernel requires it on an answer that executes nothing.
                    "counterfactual": deepcopy(COUNTERFACTUAL_FIELD),
                    "register": self._register_schema(),
                },
                "required": ["action"],
            }
            kinds = self.assemblies[sample.chosen].spec.emits
            if len(kinds) > 1 or kinds[0] in self.event_schemas:
                schema = self._contract_schema(sample.chosen)
                description += " Select one of your declared emits kinds."
            else:
                # The one kind this contract answers as, as the kernel reads ``emits``
                # (§II.b: the published contract is the enforced one).
                schema["properties"]["emits"] = {"enum": [kinds[0]]}
            req = self._request(handle, description, inputs, schema, deadline,
                                self.queue.get(handle).channel)
            ret = (returned if returned is not None
                   else self._invoke(sample.chosen, req, "producer"))
            # R3-F: the fold this seat was handed is read only if the invocation
            # returned ok. This runs before every branch below, because a verdict,
            # a forecast and a meta all leave by their own door.
            self._settle_fold_delivery(sample.chosen, ret)
            emitted = self.return_kinds.get(handle, kinds[0] if len(kinds) == 1 else None)
            if emitted == "Verdict":
                self._evaluator_step(ev, handle, sample, deadline, returned=ret)
                return
            shape = assembly_rewards(self.assemblies[sample.chosen].spec).get(emitted)
            if shape == "forecast":
                self._forecast_step(ev, handle, sample, ret, emitted)
                return
            if shape == "conformity":
                self._meta_step(ev, handle, sample, deadline, returned=ret)
                return
            if shape == "counter":
                self._counter_step(ev, handle, sample, deadline, returned=ret)
                return
            if emitted is None:
                self.consequences.finish(handle, ret.cost)
                self._settle_unselected(handle, ret)
                return
            if self._may_write(handle):
                # Only a producer kind's answer is an order (primitive audit F7).
                self._execute_outputs(ret, emitted)
            self._apply_registrations(handle, ret)
            self._apply_thinking(handle, sample.chosen, ret)
            self.handle_to_assembly[handle] = sample.chosen
            if ret.status == "ok":
                # A declined trade is priced from the mids the world had broadcast
                # when this return was made (ruling R2).
                self._freeze_declined_trade(handle, ret.outputs)
        self.consequences.finish(handle, ret.cost)
        noop = str(ret.outputs.get("action", "")).lower() in ("noop", "hold")
        revision = handle in self.window.revision_handles
        self.ledger.append(
            {
                "kind": "observation.producer",
                "handle": handle,
                "noop": noop,
                "revision": revision,
                "ts": self.clock.now_ns,
            }
        )
        self.window.producer_returns += 1
        self.window.noop_returns += int(noop)
        self.window.revision_returns += int(revision)
        self.window.revision_handles.discard(handle)
        if self.queue.get(handle).channel == CH_EXPOSURE:
            self.pending_exposure[handle] = self.ticks_consumed
            if (reason := declined_reason(ret)) is not None:
                self.declined_exposures[handle] = reason
        else:
            # A refusal is still published and may be judged like any return (II.III.b);
            # only if no judge grades it does it settle as the abstention it is.
            self.pending[handle] = PendingJudgement(handle, CH_VERDICT, self.n,
                                                    opened_at_tick=self.ticks_consumed,
                                                    declined=declined_reason(ret))
        self.stats.producer_returns += 1
        emitted = self.return_kinds.get(handle, "ProducerReturn" if sample.chosen == NOOP
                                        else self.assemblies[sample.chosen].spec.emits[0])
        payload = {
                "about_handle": handle,
                "description": judged_description,
                # Judges see the event the producer answered, never the producer's own
                # state, its inbox or its copy of the world block, and never its name.
                "inputs": {"kind": inputs["kind"], "payload": inputs["payload"]},
                "outputs": public_return(ret.outputs),
                # What the decision actually did at the venue, from its durable
                # intents and the venue's answers: a claim is judged beside its acts.
                "executed_operations": self.executed_operations(handle),
                "cost": ret.cost,
                "status": ret.status,
                # The one private thing the essay directs forward (II.I.b), so the
                # judge can price the roads this return did not take.
                "propensity": self._public_propensity(handle),
            }
        # A return is published as the kind it is, and only as that kind (primitive
        # audit F12): a judge of Exposure says so in its contract, accepts =
        # ["Exposure"], rather than meeting one disguised as a ProducerReturn.
        self._emit(emitted, payload)

    def _forecast_step(self, ev, handle, sample, ret, emitted) -> None:
        """Population forecast work is rewarded only by its future public facts."""
        self.consequences.finish(handle, ret.cost)
        self._apply_registrations(handle, ret)
        forecasts = self._open_forecasts(
            handle, sample.chosen, self._event_subject(ev) or handle,
            ret.outputs.get("forecasts") if ret.status == "ok" else None)
        self.forecast_returns[handle] = {"handles": forecasts, "results": {}}
        if (reason := declined_reason(ret)) is not None:
            # A refusal makes no prediction, so nothing will ever score it: it settles
            # declined now, priced as an abstention, never censored at a free neutral.
            self._settle_declined(handle, reason)
        self._settle_forecast_returns()
        self._emit(emitted, {"about_handle": handle, "outputs": public_return(ret.outputs),
                             "cost": ret.cost, "status": ret.status,
                             "propensity": self._public_propensity(handle)})

    def _run_child(self, parent, item, handle, target, sample, req):
        """A declared work shape answers through its own reward path, like a routed return.

        Guarantees a child whose executor declared a custom kind with a reward shape
        other than ``judged`` (a forecast, a conformity or an exposure) is dispatched
        by that shape through ``_producer_step``, exactly as a routed return of the
        same contract would be; every other child takes the seed path.
        """
        spec = self.assemblies[target].spec
        if not any(k not in BUILTIN_RETURNS
                   and shape != "judged" for k, shape in assembly_rewards(spec).items()):
            return super()._run_child(parent, item, handle, target, sample, req)
        self.handle_to_assembly[handle] = target
        ret = self._invoke(target, req, "child", child=True)
        event = Event(f"child-input-{handle}", EventKind.REGISTERED,
                      self.clock.now_ns, item.inputs, "request")
        self._producer_step(event, handle, sample, parent.deadline_ns, returned=ret)
        return ret

    def _evaluator_step(self, ev: Event, handle: str, sample: Sample, deadline: int,
                        *, returned: Return | None = None) -> None:
        self._start_return(handle)
        payload = _to_plain(ev.payload)
        about = self._event_subject(ev)
        if sample.chosen == NOOP:
            self.stats.noops += 1
            self.queue.settle(
                handle,
                channel=CH_CONFORMITY,
                score=0.0,
                status=SettleStatus.INAPPLICABLE,
                definition_version=DEF_CONFORMITY,
                sampling_ref=None,
            )
            return
        inputs = {
            # The judged return, addressable by its own handle; its kernel status is
            # named so it cannot be read as the answer's refusal flag (II.I.b).
            "producer": {
                "handle": about,
                "description": payload.get("description", f"Return on {ev.kind}"),
                "inputs": payload.get("inputs", {}),
                "outputs": judged_outputs(payload.get("outputs", payload)),
                "cost_micro_usd": payload.get("cost", 0),
                "kernel_status": payload.get("status", "ok"),
            },
            "charter": self._charter_text(),
            "predicates": [
                {"predicate": p.id, "description": p.description, "params": list(p.param_schema)}
                for p in self.predicates.all()
            ],
            "world": self._world_block(),
            "your_state": self.working_state.render(sample.chosen),
            "unread_outcomes": self.outcomes.unread(sample.chosen),
            # Variance, autocorrelation and ensemble disagreement, live (II.III.a):
            # the evaluators' reading of the stack, never a producer's (M2).
            "early_warning": self._early_warning_view(),
            **self._action_policy_input(sample.chosen),  # private
            # Evaluation is a commission, not an obligation (§6.B): a subject, a
            # scope, an evidence horizon and a budget. The decline is a form of the
            # outcome schema, stated there once.
            "commission": commission_block(
                subject=about,
                scope=("the public return named by subject_handle"
                       + ("" if str(ev.kind) in PRODUCING_KINDS else f", judged on {ev.kind}")),
                horizon=self.ev.forecast_horizon_events,
                budget_micro=self.queue.get(handle).cost_ceiling,
            ),
            "subject_handle": about,
        }
        # The two seed producing kinds are judged through one machine view: an
        # Exposure arrives as its own kind (F12), and the kind is routing, never a
        # clause that tells the judge its author was an antagonist (essay II.I.b:
        # the author "should be either irrelevant, or fungible, or private").
        generic = str(ev.kind) not in PRODUCING_KINDS
        # A judge looks at the work like a machine — request, answer, acts,
        # propensity — and never at the whole world the producer was shown
        # (essay II.I.b, after Yan 2026). It keeps its own operating access,
        # its private state and inbox, and the charter it judges against.
        inputs["actor_context"] = self._operating_context(sample.chosen, inputs.pop("world"))
        producer_inputs = inputs["producer"].get("inputs")
        if isinstance(producer_inputs, dict) and isinstance(
                producer_inputs.get("payload"), dict):
            inputs["producer"]["inputs"] = {**producer_inputs, "payload": {
                k: (FOLD_WITHHELD if k == "since_you_last_woke" else v)
                for k, v in producer_inputs["payload"].items()}}
        inputs["producer"]["executed_operations"] = payload.get("executed_operations", [])
        if generic:
            inputs["event"] = {"kind": str(ev.kind), "payload": judge_view(payload)}
        schema = self._judging_contract("Verdict")
        # The request states what the answer is; how a verdict settles is a schematic
        # (world.scoring), carried with the request as its SCORING section, and no
        # rubric says what a good return is (smuggling A7; essay II.III on
        # predefined rubrics).
        instruction = (
            "Give verdict 0-1 on the public return addressed by about_handle "
            "(subject_handle by default) against the charter."
            if generic else
            "Give verdict 0-1 on the return against the charter."
        )
        req = self._request(
            handle,
            instruction,
            inputs,
            schema,
            deadline,
            CH_CONFORMITY,
            propensity=payload.get("propensity"),
            settlement=self._settlement_facts("Verdict"),
        )
        ret = (returned if returned is not None
               else self._invoke(sample.chosen, req, "evaluator"))
        self.consequences.finish(handle, ret.cost)
        self._apply_registrations(handle, ret)
        self.handle_to_assembly[handle] = sample.chosen
        reason = declined_reason(ret)
        if reason is not None:
            # A commission may be declined (§6.B). The seat is charged the call it
            # made; no score, no quota; its learners credit the decline as an
            # abstention, less its role's price (ruling R9), as the schematic says.
            self._settle_declined(handle, reason)
            return
        verdict = _as_unit(ret.outputs.get("verdict")) if ret.status == "ok" else None
        if verdict is None:
            # Malformed, refused or without a verdict: charged, censored, never a grade.
            self._censor_judgement(handle, f"{ret.status}: no verdict in [0, 1]")
            return
        target = self._judged_event(ev, handle, ret, predicts=True)
        if target is None:
            self._censor_judgement(handle, "the judgement's target was refused")
            return
        about = self._event_subject(target)
        payload = _to_plain(target.payload)
        about_decision = self.queue.get(about)
        pend = self.pending.get(about)
        # A tier grades the tier below it (II.III.b): a verdict on a producer return is
        # tier one, and one on an evaluator decision sits one tier above that decision,
        # end to end: it grades it, it is published at that tier, and the world scores
        # it against that decision's consequence, never against a world outcome of a
        # judgement.
        tier = self._judged_tier(target, pend) + 1
        if pend is not None and pend.evaluation:
            self._grade_evaluation(about, verdict, by=handle, tier=tier)
        elif (pend is not None and about_decision.channel == CH_VERDICT
              and about_decision.status is SettleStatus.PENDING):
            # The producer's reward: the mean of its judges' verdicts, settled once
            # routing is done (``_settle_arrived_verdicts``; ruling R1).
            self.arrived_verdicts.setdefault(about, []).append([handle, verdict])
            self._deliver_verdict_to_inbox(about, verdict, judge_handle=handle)
        elif about_decision.channel == CH_EXPOSURE:
            self._deliver_verdict_to_inbox(about, verdict, judge_handle=handle)
        self._open_forecasts(handle, sample.chosen, about, ret.outputs.get("forecasts"))
        # The verdict is also a prediction about the return's measured outcome: the
        # judge's decision waits on that and on the tier above (ruling R1).
        self._open_evaluation(handle, about=about, q=verdict, evaluator_id=sample.chosen,
                              tier=tier)
        if tier == 1 and (about in self.reference_mids or self._acted(about)):
            # What this judge was shown of the world, kept for an adversarial judge
            # that re-judges its verdict (``_counter_step``): the counter reads the
            # same world the verdict was made in, never a later one.
            self.verdict_views[handle] = {
                "world": {k: v for k, v in inputs["actor_context"].items() if k != "seats"},
                "early_warning": inputs["early_warning"], "tick": self.ticks_consumed,
                "ns": self.clock.now_ns}
        self._emit(
            EventKind.VERDICT,
            {
                "about_handle": about,
                "evaluator_handle": handle,
                "verdict": verdict,
                **({"tier": tier} if tier > 1 else {}),
                **forwarded_rationale(ret.outputs),
                "producer_outputs": payload.get("outputs", payload),
                "propensity": self._public_propensity(handle),
            },
        )

    def _judged_tier(self, target: Event, pend: PendingJudgement | None) -> int:
        """The tier of the decision a judgement is about: 0 for a producing return.

        Guarantees an evaluator decision's own tier while it waits on its signals,
        and otherwise the tier its published judgement states (a Verdict is tier one
        unless it says otherwise; a conformity-shaped judgement always says).
        """
        if pend is not None and pend.evaluation:
            return pend.tier
        kind = str(target.kind)
        if target.kind is EventKind.VERDICT:
            return int(target.payload.get("tier", 1))
        if target.kind is EventKind.META_VERDICT or self._kind_rewards().get(kind) == "conformity":
            return int(target.payload.get("tier", 2))
        return 0

    def _meta_step(self, ev: Event, handle: str, sample: Sample, deadline: int,
                   *, returned: Return | None = None) -> None:
        self._start_return(handle)
        payload = _to_plain(ev.payload)
        recursive = (ev.kind is EventKind.META_VERDICT
                     or self._kind_rewards().get(str(ev.kind)) == "conformity")
        about = self._event_subject(ev)
        # A Verdict published above tier one (a judge that graded an evaluator decision)
        # states its tier; the meta reading it sits one above.
        tier = (payload["tier"] + 1 if recursive
                else payload.get("tier", 1) + 1 if ev.kind is EventKind.VERDICT else 2)
        channel = self.queue.get(handle).channel
        definition = DEF_FAST if channel == CH_FAST else DEF_CONFORMITY
        if sample.chosen == NOOP:
            self.stats.noops += 1
            self.queue.settle(
                handle,
                channel=channel,
                score=0.0,
                status=SettleStatus.INAPPLICABLE,
                definition_version=definition,
                sampling_ref=None,
            )
            return
        inputs = {
            "verdict": {
                "verdict": payload.get("score") if recursive else payload.get("verdict"),
                "rationale": payload.get("rationale", ""),
            },
            "producer_outputs": judged_outputs(payload.get("producer_outputs", {})),
            "charter": self._charter_text(),
            "early_warning": self._early_warning_view(),
            # A meta judge reads the verdict like a machine, as a first-tier judge
            # reads a return (C7): its own operating access, not the whole world.
            "actor_context": self._operating_context(sample.chosen, self._world_block()),
        }
        inputs.update(self._action_policy_input(sample.chosen))  # private
        inputs["your_state"] = self.working_state.render(sample.chosen)
        inputs["unread_outcomes"] = self.outcomes.unread(sample.chosen)
        if "window" in payload:
            inputs["window"] = payload["window"]
        if recursive:
            inputs["meta_verdict"] = judge_view(payload)
        generic = ev.kind not in (EventKind.VERDICT, EventKind.META_VERDICT)
        if generic:
            inputs["event"] = {"kind": str(ev.kind), "payload": judge_view(payload)}
        inputs["subject_handle"] = about
        # The root judge of the chain this meta reads, carried upward for the record.
        judge_handle = payload.get("evaluator_handle", about)
        schema = self._judging_contract("MetaVerdict")
        prompt = (
            "Assess the public return addressed by about_handle for conformity with the charter. "
            "The input's subject_handle is the default when present." if generic else
            "Assess the released representative verdict for conformity with the charter, "
            "using its window as context."
        )
        req = self._request(
            handle,
            prompt,
            inputs,
            schema,
            deadline,
            channel,
            propensity=payload.get("propensity"),
            settlement=self._settlement_facts("MetaVerdict"),
        )
        ret = (returned if returned is not None else self._invoke(sample.chosen, req, "meta"))
        self.consequences.finish(handle, ret.cost)
        self.handle_to_assembly[handle] = sample.chosen
        self._apply_registrations(handle, ret)
        reason = declined_reason(ret)
        if reason is not None:
            # Meta work is a commission like any other: it may be declined, at the
            # cost of the call, and is priced as an abstention (ruling R9).
            self._settle_declined(handle, reason)
            return
        conformity = _as_unit(ret.outputs.get("conformity")) if ret.status == "ok" else None
        if conformity is None:
            # Malformed or refused: charged, censored, never a kernel zero (evaluations S2).
            self._censor_judgement(handle, f"{ret.status}: no conformity in [0, 1]")
            return
        # A meta's grade is a prediction of its target's consequence score, so a target
        # it chose must still be open, exactly as a judge's must (``_hindsight_reason``).
        target = self._judged_event(ev, handle, ret, predicts=True)
        if target is None:
            self._censor_judgement(handle, "the judgement's target was refused")
            return
        about = self._event_subject(target)
        if target is not ev:
            payload = _to_plain(target.payload)
            tier = payload.get("tier", 1) + 1
            judge_handle = payload.get("evaluator_handle", about)
        # The grade is also a prediction: of the consequence score of the decision it
        # graded. The meta waits on that and, below the top, on the tier above it.
        self._open_evaluation(handle, about=about, q=conformity, evaluator_id=sample.chosen,
                              tier=tier)
        self.ledger.append({"kind": "meta.pending", "handle": handle, "tier": tier,
                            "about_handle": about, "opened_at_event": self.n,
                            "ts": self.clock.now_ns})
        emitted = self.return_kinds.get(handle, "MetaVerdict")
        self._emit(
            EventKind.META_VERDICT if emitted == "MetaVerdict" else emitted,
            {
                "about": about,
                "tier": tier,
                "score": conformity,
                "by": handle,
                "evaluator_handle": judge_handle,
                "propensity": self._public_propensity(handle),
                **forwarded_rationale(ret.outputs),
                **({"about_handle": handle} if emitted != "MetaVerdict" else {}),
            },
        )

    def _counter_step(self, ev: Event, handle: str, sample: Sample, deadline: int,
                      *, returned: Return | None = None) -> None:
        """An adversarial judge reads a verdict and gives its own on the return it judged.

        Essay II.III.b: realized consequence is "sparse ... and shrinking", so it is
        farmed "by inducing a level of adversarial activity", and the adversarial
        layer "consists not only of evaluators but also of productive workers"
        (evaluations M1). The counter-verdict is a prediction of the same measured
        outcome the verdict it read predicts; it is paid only when the world measures
        that return, on how far it beat that verdict (``_settle_counters``). It never
        reaches the producer's reward or the judge's: the world grades both.

        Guarantees a counter settles censored, never scored, when it read anything but
        a first-tier verdict on a return, or a return whose outcome the world had
        already given (a reading of the answer is not a prediction).
        """
        self._start_return(handle)
        payload = _to_plain(ev.payload)
        channel = self.queue.get(handle).channel
        if sample.chosen == NOOP:
            self.stats.noops += 1
            self.queue.settle(handle, channel=channel, score=0.0,
                              status=SettleStatus.INAPPLICABLE,
                              definition_version=DEF_COUNTER, sampling_ref=None)
            return
        about = payload.get("about_handle")
        view = self.verdict_views.get(payload.get("evaluator_handle"))
        own = self._operating_context(sample.chosen, self._world_block())
        inputs = {
            "verdict": {"verdict": payload.get("verdict"),
                        "rationale": payload.get("rationale", "")},
            "producer_outputs": judged_outputs(payload.get("producer_outputs", {})),
            "charter": self._charter_text(),
            "subject_handle": about,
            # The world exactly as the judge it re-judges was shown it, frozen at that
            # verdict (the #132 review, item 1): its edge may be a better reading of
            # the same evidence, never fresher prices. Its own seat row is its own.
            "early_warning": view["early_warning"] if view is not None else None,
            "actor_context": ({**view["world"], "seats": own["seats"]} if view is not None
                              else own),
        }
        inputs.update(self._action_policy_input(sample.chosen))  # private
        inputs["your_state"] = self.working_state.render(sample.chosen)
        inputs["unread_outcomes"] = self.outcomes.unread(sample.chosen)
        schema = self._judging_contract("CounterVerdict")
        req = self._request(
            handle,
            "Give your own verdict 0-1 on the return this verdict judged, against the "
            "charter.",
            inputs, schema, deadline, channel, propensity=payload.get("propensity"),
            settlement=self._settlement_facts("CounterVerdict"))
        ret = (returned if returned is not None
               else self._invoke(sample.chosen, req, "adversary"))
        self.consequences.finish(handle, ret.cost)
        self.handle_to_assembly[handle] = sample.chosen
        self._apply_registrations(handle, ret)
        reason = declined_reason(ret)
        if reason is not None:
            self._settle_declined(handle, reason)
            return
        q = _as_unit(ret.outputs.get("verdict")) if ret.status == "ok" else None
        if q is None:
            self._censor_judgement(handle, f"{ret.status}: no verdict in [0, 1]")
            return
        judged = _as_unit(payload.get("verdict"))
        if (ev.kind is not EventKind.VERDICT or "tier" in payload or judged is None
                or not isinstance(about, str)):
            self._censor_judgement(handle, "a counter-verdict reads a first-tier verdict "
                                           "on a return")
            return
        if view is None or view["tick"] != self.ticks_consumed:
            # Only a counter made in the tick of the verdict it read, on that verdict's
            # frozen view, is a prediction on equal information.
            self._censor_judgement(handle, "a counter-verdict reads a verdict in the tick "
                                           "it was given")
            return
        self.decision_subjects[handle] = payload.get("evaluator_handle") or about
        if self._consequence_known(about, 1):
            self.ledger.append({"kind": "evaluation.hindsight", "handle": handle,
                                "about_handle": about, "tier": 1, "ts": self.clock.now_ns})
            self._censor_judgement(handle, "the return's outcome was already known")
            return
        self.pending_counters[handle] = {
            "about": about, "q": q, "judge_handle": payload.get("evaluator_handle"),
            "judge_q": judged, "evaluator_id": sample.chosen, "tick": self.ticks_consumed,
            "ns": self.clock.now_ns,
        }
        self.ledger.append({"kind": "counter.opened", "handle": handle, "about_handle": about,
                            "judge_handle": payload.get("evaluator_handle"), "q": q,
                            "judge_q": judged, "ts": self.clock.now_ns})


def run_world(
    manifest: WorldManifest,
    *,
    events: int = 200,
    seed: int | None = None,
    initial_balance_micro: int | None = None,
    ledger_path: str | None = None,
    router_gamma: float = 0.1,
    provider: Any | None = None,
    market: X402Provider | None = None,
    exchange: Any | None = None,
    clock_source: Any | None = None,
    kill_at_end: bool = False,
) -> dict[str, Any]:
    """Run a world for ``events`` world events (plus the internal events they cause).

    Guarantees: every invocation is metered before its result is used; every
    decision has a logged propensity that reproduces its sample; every
    producer decision is judged or censored; forecasts are sealed before any
    outcome is known and settle to their own handle; the world terminates by
    death if the wallet reaches zero and the summary reports the seal state.

    Every world offers population tools, so no world launches on a host where
    the jail cannot start: the world block would promise tools that no proposal
    could ever obtain. Nothing is written before the refusal.
    """
    reason = jail_probe()
    if reason is not None:
        raise NoJail(f"this world offers population tools and the host has no jail: {reason}")
    return Runtime(
        manifest,
        events=events,
        seed=seed,
        initial_balance_micro=initial_balance_micro,
        ledger_path=ledger_path,
        router_gamma=router_gamma,
        provider=provider,
        market=market,
        exchange=exchange,
        clock_source=clock_source,
        kill_at_end=kill_at_end,
    ).run()
