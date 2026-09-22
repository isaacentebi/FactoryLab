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
from typing import Any

from factorylab.cortex.registration import measured_role
from factorylab.cortex.request import Request, Return, public_return
from factorylab.cortex.sandbox import NoJail, jail_probe
from factorylab.cortex.schematics import SchematicsMixin
from factorylab.kernel.events import Event, EventKind
from factorylab.kernel.queue import PropensityRecord, SettleStatus
from factorylab.kernel.termination import DORMANT
from factorylab.learners.router import Sample
from factorylab.runtime.bootstrap import BootstrapMixin
from factorylab.runtime.cadence import settle_forecasts
from factorylab.runtime.compute import ComputeMixin
from factorylab.runtime.feedback import (
    FeedbackMixin,
    PendingJudgement,
)
from factorylab.runtime.governance import GovernanceMixin
from factorylab.runtime.live import LiveClock, Reconciler
from factorylab.runtime.pricing import PricingMixin
from factorylab.runtime.resume import decode, encode, runtime_state
from factorylab.runtime.routing import ContractQueue, PopulationEvent, RoutingMixin
from factorylab.runtime.shared import (
    CH_CONFORMITY,
    CH_EXPOSURE,
    CH_FAST,
    CH_VERDICT,
    DEF_CONFORMITY,
    DEF_FAST,
    DEF_VERDICT,
    NOOP,
    _to_plain,
    assembly_rewards,
)
from factorylab.runtime.subscriptions import SubscriptionBook, ThinkingMixin
from factorylab.runtime.summary import SummaryMixin, _as_unit
from factorylab.runtime.vault import VaultMixin
from factorylab.runtime.venue import VenueMixin
from factorylab.runtime.worlds import WorldManifest
from factorylab.settlement.vocabulary import (
    commission_block,
    evaluator_answer_schema,
)
from factorylab.world.clock import ClockSource, merge_sources
from factorylab.world.events import WorldEvent, WorldEventKind
from factorylab.world.market import X402Provider

#: What a return carries that is not the work under judgement: its propensity, which
#: the request's PROPENSITY block renders once (P8).
UNJUDGED_OUTPUT_FIELDS = frozenset({"propensity"})


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


class Runtime(
    SchematicsMixin,
    ThinkingMixin,
    RoutingMixin,
    GovernanceMixin,
    VenueMixin,
    VaultMixin,
    PricingMixin,
    FeedbackMixin,
    SummaryMixin,
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
        return ret

    def _settle_due_forecasts(self) -> None:
        """A6 sampling and A2 cadence openings both wrap the one settlement implementation."""
        from copy import deepcopy

        pending = {f.handle: f for f in self.book.pending()}
        baseline = deepcopy(self.baseline)
        settle_forecasts(self, super()._settle_due_forecasts)
        self._record_card_forecasts(pending, baseline)

    def _settle_priced(self, handle, *, cards, **kwargs):
        """Custom emitted kinds answer for their own cards on every reward shape."""
        emitted = self.return_kinds.get(handle)
        if emitted and emitted not in ("ProducerReturn", "Verdict", "MetaVerdict", "Exposure"):
            cards = measured_role(emitted)
        return super()._settle_priced(handle, cards=cards, **kwargs)

    def _settle_exchange_effects(self, events, *, observe_positions=True) -> None:
        super()._settle_exchange_effects(events, observe_positions=observe_positions)
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
                & {"forecast", "conformity"}]

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
        try:
            return self._run()
        finally:
            self._ledger_lock.close()

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
        self.bus.publish(ev)
        self.stats.events += 1
        self.events_log.append({"kind": str(ev.kind), "payload": _to_plain(ev.payload)})
        self._fold_world_event(ev)
        if ev.kind is EventKind.MARKET_MID:
            coin = str(ev.payload.get("coin"))
            dq = self.recent_mids.setdefault(coin, deque(maxlen=20))
            dq.append({"t_s": ev.ts_ns // 1_000_000_000, "mid": str(ev.payload.get("mid"))})

        # Due tranches are mandatory even while dormant; each released tranche is then
        # classified (C10): base_share across live seats, the remainder unallocated.
        released = self.wallet.release_due(self.clock.now_ns)
        if released:
            self.budget.on_release(released)
        self._manage_reserve_window()
        self.treasury.open_window(self.stats.reserve_windows)  # reserve-window top-up cap
        # At each window boundary: when no live seat can act from its entitlement,
        # release the commons to the seats, or, with nothing left to release, let
        # the termination rule below see the starvation (P1-04).
        starved = (previous_window != self.reserve_window_start) and self._commons_check()
        if previous_window is not None and previous_window != self.reserve_window_start:
            self._sampling_actuator()
        self._observe_delivered_event(ev)
        if ev.kind is EventKind.TICK:
            self._reconcile_orders()
            if getattr(self, "polymarket", None) is not None:
                from factorylab.runtime import polymarket

                polymarket.tick(self)  # its own intents, fills and resolutions
            self._collect_income()  # C10: each receipt credits its owning seat before the tick
            self.treasury.tick(self.clock.now_ns)
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
                    for ts, payload in self.consequence_fills.poll(self.exchange)
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
                self._settle_exchange_effects(self.exchange.advance(self.clock.now_ns))
            # C2: the kernel settles every registered watcher from world state at the
            # program price, then offers one coalesced update to the seats that asked
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
        self.stats.timeouts += len(self.queue.expire(self.clock.now_ns))
        self._deliver_returns()
        self.ledger.append({"kind": "runtime.event_done", "n": self.n})
        self.balance_at.append(self.wallet.balance)
        if previous_window != self.reserve_window_start:
            self._snapshot("reserve_window")
        return True

    def _snapshot(self, boundary: str) -> bool:
        """Persist a complete continuation at launch and after each boundary event finishes."""
        try:
            state = runtime_state(self)
            # Router states contain ordinary JSON floats as well as tagged codec values.
            from factorylab.cortex.assembly import _finite_json

            _finite_json(state)
        except (ValueError, OverflowError, RecursionError):
            self.ledger.append({"kind": "snapshot.refused", "boundary": boundary, "n": self.n,
                                "reason": "invalid checkpoint number or nesting"})
            return False
        self.ledger.append(
            {"kind": "snapshot", "boundary": boundary, "n": self.n, "state": state}
        )
        # The held venue reads are not in the checkpoint — the listing, the mids and
        # the account state are the venue's own facts, and a checkpoint is a
        # continuation, not a cache. Dropping them here is what makes it safe to leave
        # them out: a resume restores this checkpoint holding none of them, and the run
        # that wrote it holds none from this point either, so the replayed tail asks
        # the venue exactly where the recorded tail did.
        self._instruments_memo = None
        self._mids_memo = None
        self._account_memo = None
        self._peak_observed = None
        forget = getattr(self.treasury, "forget_observations", None)
        if forget is not None:
            forget()
        return True

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
                for ts, payload in self.consequence_fills.poll(self.exchange)
            ]
            observed.extend(self.venue.funding_payments(now_ns))
            self._settle_exchange_effects(observed)
            self._collect_income()
            self.treasury.tick(now_ns)
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
        handles = [d.handle for d in self.queue.outstanding() if d.deadline_ns <= now_ns]
        self.ledger.append({"kind": "resume.timeouts", "handles": handles, "n": self.n})
        self.stats.timeouts += len(self.queue.expire(now_ns))
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
                if (isinstance(self.tick_clock, LiveClock)
                        and 0 <= self.tick_clock.last_ns < we.ts_ns):
                    self.tick_clock.gaps.append(we.ts_ns - self.tick_clock.last_ns)
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
                    validate_schema({k: v for k, v in payload["outputs"].items()
                                      if k not in ("emits", "register", "about_handle",
                                                   "status", "reason")},
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
        else:
            self._producer_step(ev, handle, sample, deadline)

    def _contract_schema(self, assembly_id: str) -> dict[str, Any]:
        """A polymorphic request publishes all its variants without choosing one for the model."""
        from factorylab.cortex.assembly import reserved_return_fields

        spec = self.assemblies[assembly_id].spec
        schemas = []
        unit = {"type": "number", "minimum": 0, "maximum": 1}
        for kind in spec.emits:
            if kind in spec.schemas:
                schema = _to_plain(spec.schemas[kind])
            else:
                fields = ({"verdict": unit, "payoff": unit,
                           "rationale": {"type": "string"}, "forecasts": self._forecast_schema()}
                          if kind == "Verdict" else
                          {"conformity": unit} if kind == "MetaVerdict" else
                          {"action": {"type": "string"}})
                schema = {"type": "object", "properties": fields, "required": list(fields)}
            schemas.append({**schema, "properties": {
                **{k: v for k, v in reserved_return_fields(
                    max_children=self.m.tools.max_children,
                    max_tool_calls=self.m.tools.max_tool_calls).items()
                   if k in ("requests", "tool_calls", "status", "reason")},
                **schema.get("properties", {}), "emits": {"enum": [kind]},
                "about_handle": {"type": "string"}, "register": self._register_schema(),
            }, "required": [*schema.get("required", []),
                            *(["emits"] if len(spec.emits) > 1 else [])]})
        return schemas[0] if len(schemas) == 1 else {"anyOf": schemas}

    def _hindsight_reason(self, handle: str, about: str) -> str | None:
        """Name why a chosen target cannot carry a payoff forecast, or None if it can.

        A forecast precedes its outcome. The router's subject is not chosen, but a
        return that names an older target instead may not name one whose
        consequence is already fixed, one at or past its backstop, or one whose
        backstop falls before this judgement's own decision deadline.
        """
        try:
            account = self.consequences.table.account(about)
        except KeyError:
            return None
        if account.voided:
            return "judgement needs a chosen return a seat authored, not an abstention"
        if account.payoff is not None:
            return "judgement needs a chosen return whose consequence is still open"
        # The backstop counts world ticks consumed since the return opened (defect 1).
        opened = (account.opened_at_tick if account.opened_at_tick is not None
                  else self.ticks_consumed)
        due = opened + self.consequences.backstop
        if self.ticks_consumed >= due:
            return "judgement needs a chosen return inside its consequence backstop"
        backstop_ns = (self.clock.now_ns
                       + (due - self.ticks_consumed) * self.tick_clock.interval_ns)
        if self.queue.get(handle).deadline_ns > backstop_ns:
            return "judgement would settle after the chosen return's consequence backstop"
        return None

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
                    "counterfactual": {
                        "type": "object",
                        "description": "a declined trade, coin and side",
                        "properties": {"coin": {"type": "string"},
                                       "side": {"enum": ["buy", "sell"]}},
                        "required": ["coin", "side"],
                    },
                    "register": self._register_schema(),
                },
                "required": ["action"],
            }
            kinds = self.assemblies[sample.chosen].spec.emits
            if len(kinds) > 1 or kinds[0] in self.event_schemas:
                schema = self._contract_schema(sample.chosen)
                description += " Select one of your declared emits kinds."
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
            if emitted is None:
                self.consequences.finish(handle, ret.cost)
                self.queue.settle(handle, channel=self.queue.get(handle).channel, score=0.0,
                                  status=SettleStatus.CENSORED,
                                  definition_version="unselected-return-v1", sampling_ref=None)
                return
            if self._may_write(handle):
                self._execute_outputs(ret)
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
        else:
            self.pending[handle] = PendingJudgement(handle, CH_VERDICT, self.n,
                                                    opened_at_tick=self.ticks_consumed)
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
        # Exposure retains its producer-shaped judgment route for the shipped seeds;
        # assemblies may also subscribe to its explicit kind.
        self._emit(EventKind.PRODUCER_RETURN if emitted == "Exposure" else emitted, payload)
        if emitted == "Exposure" and self.routers.get("Exposure"):
            self._emit("Exposure", payload)

    def _forecast_step(self, ev, handle, sample, ret, emitted) -> None:
        """Population forecast work is rewarded only by its future public facts."""
        self.consequences.finish(handle, ret.cost)
        self._apply_registrations(handle, ret)
        forecasts = self._open_forecasts(
            handle, sample.chosen, self._event_subject(ev) or handle,
            ret.outputs.get("forecasts") if ret.status == "ok" else None)
        self.forecast_returns[handle] = {"handles": forecasts, "results": {}}
        self._settle_forecast_returns()
        self._emit(emitted, {"about_handle": handle, "outputs": public_return(ret.outputs),
                             "cost": ret.cost, "status": ret.status,
                             "propensity": self._public_propensity(handle)})

    def _invoke_child(self, action_id, parent, item, ceiling):
        """New work shapes use the same bounded child admission and declared-shape dispatch."""
        target = action_id if item.target == "self" else item.target
        if (reason := self._commissioned_judge_refusal(target)) is not None:
            return self._refuse_commissioned_judge(parent, item, target, reason)
        spec = self.assemblies[target].spec if target in self.assemblies else None
        if (spec is None or target in self.retired_assemblies
                or not any(k not in ("ProducerReturn", "Verdict", "MetaVerdict", "Exposure")
                           and shape != "judged" for k, shape in assembly_rewards(spec).items())):
            return super()._invoke_child(action_id, parent, item, ceiling)
        depth, cursor = 0, parent.handle
        while self.queue.get(cursor).parent_handle is not None:
            depth += 1
            cursor = self.queue.get(cursor).parent_handle
        if depth >= self.m.tools.max_depth:
            reason = "tools.max_depth reached"
            self.ledger.append({"kind": "requests.refused", "handle": parent.handle,
                                "reason": reason, "depth": depth})
            return {"tool": f"assembly:{target}", "args": item.inputs,
                    "result": {"error": reason}}, 0
        ceiling = min(ceiling, max(0, self._compute_available(parent.handle)))
        actor = self.queue.get(parent.handle).actor
        channels = self._return_channels(target)
        channel = next(iter(channels.values()))
        handle = self.queue.open(
            actor=actor, event_id=f"child-{parent.handle}",
            propensity=PropensityRecord((target,), (1.,), target, 0, actor, "parent-selected"),
            channel=channel, deadline_ns=parent.deadline_ns, parent_handle=parent.handle,
            cost_ceiling=ceiling, return_channels=channels)
        self.ledger.append({"kind": "request.child", "handle": handle, "target": target,
                            "resource_liability": parent.handle, "cost_ceiling": ceiling,
                            "description": item.description, "inputs": item.inputs,
                            "outcome_schema": item.outcome_schema})
        self.stats.decisions += 1
        self.consequences.start(handle, self.n)
        self.handle_to_assembly[handle] = target
        req = Request(handle, item.description, {**item.inputs, "world": self._world_block()},
                      {}, item.outcome_schema, parent.deadline_ns, ceiling, parent.handle,
                      "a JSON object satisfying the outcome schema", channel, parent.handle)
        ret = self._invoke(target, req, "child", child=True)
        event = Event(f"child-input-{handle}", EventKind.REGISTERED,
                      self.clock.now_ns, item.inputs, "request")
        sample = Sample((target,), (1.,), target, 0, actor, "parent-selected", ())
        self._producer_step(event, handle, sample, parent.deadline_ns, returned=ret)
        return {"tool": f"assembly:{target}", "args": item.inputs,
                "result": {"outputs": public_return(ret.outputs), "status": ret.status,
                           "cost_micro": ret.cost}}, ret.cost

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
            "producer": {
                "description": payload.get("description", f"Return on {ev.kind}"),
                "inputs": payload.get("inputs", {}),
                "outputs": judged_outputs(payload.get("outputs", payload)),
                "cost_micro_usd": payload.get("cost", 0),
                "status": payload.get("status", "ok"),
            },
            "charter": self._charter_text(),
            "predicates": [
                {"predicate": p.id, "description": p.description, "params": list(p.param_schema)}
                for p in self.predicates.all()
            ],
            "world": self._world_block(),
            "your_state": self.working_state.render(sample.chosen),
            "unread_outcomes": self.outcomes.unread(sample.chosen),
            **self._action_policy_input(sample.chosen),  # private
            # Evaluation is a commission, not an obligation (§6.B): a subject, a
            # scope, an evidence horizon and a budget, which may be declined.
            "commission": commission_block(
                subject=about,
                scope=f"the public return addressed by about_handle, judged on {ev.kind}",
                horizon=self.ev.forecast_horizon_events,
                budget_micro=self.queue.get(handle).cost_ceiling,
            ),
        }
        generic = ev.kind is not EventKind.PRODUCER_RETURN
        # A judge looks at the work like a machine — request, answer, acts,
        # propensity — and never at the whole world the producer was shown
        # (essay II.I.b, after Yan 2026). It keeps its own operating access,
        # its private state and inbox, and the charter it judges against.
        inputs["actor_context"] = self._operating_context(sample.chosen, inputs.pop("world"))
        producer_inputs = inputs["producer"].get("inputs")
        if isinstance(producer_inputs, dict) and isinstance(
                producer_inputs.get("payload"), dict):
            inputs["producer"]["inputs"] = {**producer_inputs, "payload": {
                k: v for k, v in producer_inputs["payload"].items()
                if k != "since_you_last_woke"}}
        inputs["producer"]["executed_operations"] = payload.get("executed_operations", [])
        if generic:
            inputs["event"] = {"kind": str(ev.kind), "payload": judge_view(payload)}
            inputs["subject_handle"] = about
        schema = evaluator_answer_schema(self._forecast_schema(), self._register_schema())
        # The request states what the answer is and what may be done with the
        # commission; how a verdict is scored is a schematic (world.scoring), and no
        # rubric says what a good return is (smuggling A7; essay II.III on
        # predefined rubrics).
        instruction = (
            "Give verdict 0-1 on the public return addressed by about_handle "
            "(subject_handle by default) against the charter; you may decline."
            if generic else
            "Give verdict 0-1 on the return against the charter; you may decline."
        )
        req = self._request(
            handle,
            instruction,
            inputs,
            schema,
            deadline,
            CH_CONFORMITY,
            propensity=payload.get("propensity"),
        )
        ret = (returned if returned is not None
               else self._invoke(sample.chosen, req, "evaluator"))
        self.consequences.finish(handle, ret.cost)
        self._apply_registrations(handle, ret)
        self.handle_to_assembly[handle] = sample.chosen
        answered = str(ret.outputs.get("status", "")).strip().lower()
        reason = str(ret.outputs.get("reason", ""))[:500]
        if ret.status == "ok" and answered == "cannot":
            # A commission may be declined. The seat is charged the call it made
            # and nothing else: no score, no penalty, no quota (§6.B).
            self._settle_declined(handle, CH_CONFORMITY,
                                  reason or "the seat declined this commission")
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
        if pend is not None and pend.evaluation:
            # A verdict on an evaluator decision is its grade from the tier above.
            self._grade_evaluation(about, verdict, by=handle, tier=pend.tier + 1)
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
                              tier=1)
        self._emit(
            EventKind.VERDICT,
            {
                "about_handle": about,
                "evaluator_handle": handle,
                "verdict": verdict,
                "rationale": str(ret.outputs.get("rationale", ""))[:2000],
                "producer_outputs": payload.get("outputs", payload),
                "propensity": self._public_propensity(handle),
            },
        )

    def _meta_step(self, ev: Event, handle: str, sample: Sample, deadline: int,
                   *, returned: Return | None = None) -> None:
        self._start_return(handle)
        payload = _to_plain(ev.payload)
        recursive = (ev.kind is EventKind.META_VERDICT
                     or self._kind_rewards().get(str(ev.kind)) == "conformity")
        about = self._event_subject(ev)
        tier = payload["tier"] + 1 if recursive else 2
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
        schema = {
            "type": "object",
            "properties": {
                "conformity": {"type": "number"},
                "rationale": {"type": "string"},
                "propensity": {"type": "object"},
                "register": self._register_schema(),
                "about_handle": {"type": "string"},
            },
            "required": ["conformity"],
        }
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
        )
        ret = (returned if returned is not None else self._invoke(sample.chosen, req, "meta"))
        self.consequences.finish(handle, ret.cost)
        self.handle_to_assembly[handle] = sample.chosen
        self._apply_registrations(handle, ret)
        answered = str(ret.outputs.get("status", "")).strip().lower()
        if ret.status == "ok" and answered == "cannot":
            # Meta work is a commission like any other: it may be declined, at the
            # cost of the call.
            self._settle_declined(
                handle, channel, str(ret.outputs.get("reason", ""))[:500] or
                "the seat declined this commission")
            return
        conformity = _as_unit(ret.outputs.get("conformity")) if ret.status == "ok" else None
        if conformity is None:
            # Malformed or refused: charged, censored, never a kernel zero (evaluations S2).
            self._censor_judgement(handle, f"{ret.status}: no conformity in [0, 1]")
            return
        target = self._judged_event(ev, handle, ret)
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
                "rationale": str(ret.outputs.get("rationale", ""))[:2000],
                **({"about_handle": handle} if emitted != "MetaVerdict" else {}),
            },
        )


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

    A world whose manifest prices population tools does not launch on a host
    where the jail cannot start: the world block would promise tools that no
    proposal could ever obtain. Nothing is written before the refusal.
    """
    if manifest.tools.population_tool_micro_per_call > 0:
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
