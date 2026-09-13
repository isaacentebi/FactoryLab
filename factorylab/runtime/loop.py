"""The event loop dispatches public events through registered accepts/emits contracts.

The loop owns no money, no scores and no rules; it is glue over kernel
physics.

Contracts. Any assembly may accept any event kind and select a declared
output kind. Producer-shaped and custom returns receive verdict feedback;
verdicts carry independent quality and payoff values; meta verdicts receive
conformity or terminal consequence feedback; exposures retain their exposure
channel. The shipped registrations preserve the seeded evaluation chain.
Unjudged returns are censored, and retirement preserves delayed feedback.

Prices. At each reserve-window boundary the runtime measures the window
that closed using the factory's observation vocabulary — the twenty-two seeds
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
from itertools import islice
from typing import Any

from factorylab.cortex.registration import measured_role
from factorylab.cortex.request import Return
from factorylab.cortex.sandbox import NoJail, jail_probe
from factorylab.cortex.schematics import SchematicsMixin
from factorylab.kernel.events import Event, EventKind
from factorylab.kernel.money import money_to_usd
from factorylab.kernel.queue import SettleStatus
from factorylab.learners.router import Sample
from factorylab.runtime.bootstrap import BootstrapMixin
from factorylab.runtime.cadence import settle_forecasts
from factorylab.runtime.compute import ComputeMixin
from factorylab.runtime.feedback import FeedbackMixin, PendingJudgement
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
)
from factorylab.runtime.summary import SummaryMixin, _as_unit
from factorylab.runtime.venue import VenueMixin
from factorylab.runtime.worlds import WorldManifest
from factorylab.settlement import SEED_VOCABULARY
from factorylab.world.clock import ClockSource, DripSource, merge_sources
from factorylab.world.events import WorldEvent, WorldEventKind
from factorylab.world.market import X402Provider


class Runtime(
    SchematicsMixin,
    RoutingMixin,
    GovernanceMixin,
    VenueMixin,
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

    def _settle_exchange_effects(self, events) -> None:
        super()._settle_exchange_effects(events)
        self._record_pricing_fills(events)

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
        tick_ns = self.m.tick_interval_ns
        if self.clock_source is None:
            self.tick_clock.count = self.events_budget
        if self.clock_source is not None and not hasattr(self.clock_source, "events"):
            sources = [self.clock_source]
        else:
            sources = [self.tick_clock.events()]
        if self.use_drip and self.m.drip is not None:
            d = self.m.drip
            drips = DripSource(
                d.amount_micro,
                d.period_ns,
                max(d.start_ns, tick_ns),
                min(d.end_ns, (self.events_budget + 1) * self.m.max_tick_ns),
            ).events()
            sources.append(islice(drips, self.drips_consumed, None))
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
        while True:
            ev = self._next_event(stream)
            if ev is None or not self._process_event(ev):
                break
        if self.kill_at_end and not self.termination.final:
            # a budgeted rehearsal world ends by explicit kill so its diary becomes readable
            self.termination.kill("explicit_kill:budget")
        return self._summary()

    def _launch(self) -> None:
        """Publish Launch only after a recoverable pre-launch snapshot exists."""
        self.bus.publish(
            Event(
                "launch",
                EventKind.LAUNCH,
                self.clock.now_ns,
                {"manifest_hash": self.m.manifest_hash(),
                 "manifest": json.loads(self.m.canonical_json())},
                "kernel",
            )
        )
        self.started = True

    def _process_event(self, ev: Event) -> bool:
        """Normal execution and recovery use identical transitions after a durable input item."""
        previous_window = self.reserve_window_start
        self.n += 1
        self.clock.now_ns = max(self.clock.now_ns, ev.ts_ns)
        self.bus.publish(ev)
        self.cadence.advance(self.n)
        self.stats.events += 1
        self.events_log.append({"kind": str(ev.kind), "payload": _to_plain(ev.payload)})
        if ev.kind is EventKind.MARKET_MID:
            coin = str(ev.payload.get("coin"))
            dq = self.recent_mids.setdefault(coin, deque(maxlen=20))
            dq.append({"t_s": ev.ts_ns // 1_000_000_000, "mid": str(ev.payload.get("mid"))})

        self.wallet.drip(self.clock.now_ns)
        self._manage_reserve_window()
        self.treasury.open_window(self.stats.reserve_windows)  # reserve-window top-up cap
        if previous_window is not None and previous_window != self.reserve_window_start:
            self._sampling_actuator()
        self._observe_delivered_event(ev)
        if ev.kind is EventKind.TICK:
            self._reconcile_orders()
            self.treasury.tick(self.clock.now_ns)
            self._reconcile_x402()
            if self.venue is not None:
                observed = [
                    we
                    for we in self.venue.on_tick(self.clock.now_ns)
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
        if self._check_termination():
            return False

        self._compute_routed = False
        self._compute_unaffordable = False
        self._route(ev)
        self._record_insolvency_event(ev)
        if self._check_termination():
            return False

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
            self.treasury.tick(now_ns)
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
                self.tick_clock.index = self.ticks_consumed
                self.tick_clock.last_ns = we.ts_ns
        elif we.kind is WorldEventKind.DRIP:
            self.drips_consumed += 1
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

    def _check_termination(self) -> bool:
        reason = self.termination.check(self.wallet, self.clock.now_ns)
        if reason is None and self.insolvency_count >= self.m.treasury.insolvency_events:
            reason = "insolvency:compute"
        if reason is None:
            return False
        self._settle_due_forecasts()
        self.termination.kill(reason)
        return True

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
            if self._event_subject(ev) is not None:
                self.stats.noops += 1
                self.consequences.finish(handle, 0)
                self.queue.settle(handle, channel=self.queue.get(handle).channel, score=0.0,
                                  status=SettleStatus.INAPPLICABLE,
                                  definition_version=DEF_VERDICT, sampling_ref=None)
            else:
                self._producer_step(ev, handle, sample, deadline)
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
        if account.payoff is not None:
            return "judgement needs a chosen return whose consequence is still open"
        due = account.opened_at_event + self.consequences.backstop
        if self.n >= due:
            return "judgement needs a chosen return inside its consequence backstop"
        backstop_ns = self.clock.now_ns + (due - self.n) * self.tick_clock.interval_ns
        if self.queue.get(handle).deadline_ns > backstop_ns:
            return "judgement would settle after the chosen return's consequence backstop"
        return None

    def _judged_event(self, ev: Event, handle: str, ret: Return,
                      *, seals_payoff: bool = False) -> Event | None:
        """A judgement may address a public return handle, excluding its complete ancestry."""
        subject = self._event_subject(ev)
        about = ret.outputs.get("about_handle", subject)
        try:
            self.queue.get(about)
        except (KeyError, TypeError):
            self.ledger.append({"kind": "return.refused", "handle": handle,
                                "reason": "judgement needs an addressable return handle"})
            return None
        target = self.return_events.get(about)
        if target is None and about == subject:
            target = ev
        author = self.handle_to_assembly.get(handle)
        if target is None or author in self._subject_authors(str(target.kind), target):
            self.ledger.append({"kind": "return.refused", "handle": handle,
                                "about_handle": about,
                                "reason": "judgement needs an independent, addressable return"})
            return None
        # A child cannot judge the requester or any earlier request ancestor either.
        parents = self._ancestry(self.queue.get(handle).parent_handle)
        if about in parents or self.handle_to_assembly.get(about) in {
            self.handle_to_assembly.get(p) for p in parents
        }:
            self.ledger.append({"kind": "return.refused", "handle": handle,
                                "about_handle": about, "reason": "self-judgement: ancestor"})
            return None
        # A payoff forecast on a target this return chose for itself, rather than the
        # one the router delivered, must still be sealed before the outcome is fixed.
        if seals_payoff and about != subject and (
                reason := self._hindsight_reason(handle, about)) is not None:
            self.ledger.append({"kind": "return.refused", "handle": handle,
                                "about_handle": about, "reason": reason})
            return None
        self.decision_subjects[handle] = about
        return target

    def _producer_step(self, ev: Event, handle: str, sample: Sample, deadline: int,
                       *, returned: Return | None = None) -> None:
        self._start_return(handle)
        payload = _to_plain(ev.payload)
        if ev.kind is EventKind.TICK:
            try:
                acct = self.exchange.account()
                payload["account"] = {
                    "equity_usd": str(acct.equity_usd),
                    "positions": [
                        {"coin": p.coin, "size": str(p.size), "entry_px": str(p.entry_px)}
                        for p in acct.positions
                    ],
                }
            except RuntimeError:  # read-only live venue: no account yet
                payload["account"] = {
                    "equity_usd": str(money_to_usd(self.wallet.balance)),
                    "positions": [],
                }
            payload["mids"] = {c: str(m) for c, m in self.exchange.mids().items()}
        description = f"Respond to event {ev.kind} on {ev.source}."
        adversarial = (sample.chosen != NOOP
                       and self.assemblies[sample.chosen].spec.emits == ("Exposure",))
        if adversarial:
            description += (
                " You may include payoff: your probability that return_paid_off, the "
                "kernel's consequence predicate, resolves true for this return; it is "
                "sealed as your forecast about your own return."
            )
        inputs = {
            "kind": str(ev.kind),
            "payload": payload,
            "world": self._world_block(),
            "your_recent_returns": list(self.memory.get(sample.chosen, ())),
            "your_action_policy": self._action_policy(sample.chosen),  # private
        }
        if sample.chosen == NOOP:
            self.stats.noops += 1
            ret = Return(handle, {"action": "noop"}, 0, "ok")
        else:
            schema = {
                "type": "object",
                "properties": {
                    "action": {"type": "string"},
                    "propensity": {"type": "object"},
                    "register": self._register_schema(),
                    **({"payoff": {"type": "number", "minimum": 0, "maximum": 1}}
                       if adversarial else {}),
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
            emitted = self.return_kinds.get(handle, kinds[0] if len(kinds) == 1 else None)
            if emitted == "Verdict":
                self._evaluator_step(ev, handle, sample, deadline, returned=ret)
                return
            if emitted == "MetaVerdict":
                self._meta_step(ev, handle, sample, deadline, returned=ret)
                return
            if emitted is None:
                self.consequences.finish(handle, ret.cost)
                self.queue.settle(handle, channel=self.queue.get(handle).channel, score=0.0,
                                  status=SettleStatus.CENSORED,
                                  definition_version="unselected-return-v1", sampling_ref=None)
                return
            adversarial = emitted == "Exposure"
            if self._may_write(handle):
                self._execute_outputs(ret)
            self._apply_registrations(handle, ret)
            self.handle_to_assembly[handle] = sample.chosen
            self.memory.setdefault(sample.chosen, deque(maxlen=3)).append(
                {"handle": handle, "outputs": ret.outputs, "verdict": None}
            )
            payoff = _as_unit(ret.outputs.get("payoff")) if ret.status == "ok" else None
            if adversarial and payoff is not None:
                self.consequences.seal_self_forecast(
                    self.book, self.queue, handle=handle, assembly_id=sample.chosen,
                    payoff=payoff, event=self.n, now_ns=self.clock.now_ns,
                    tick_ns=self.tick_clock.interval_ns,
                )
                self.stats.forecasts_sealed += 1
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
            self.pending_exposure[handle] = self.n
        else:
            self.pending[handle] = PendingJudgement(handle, CH_VERDICT, self.n)
        self.stats.producer_returns += 1
        emitted = self.return_kinds.get(handle, "ProducerReturn" if sample.chosen == NOOP
                                        else self.assemblies[sample.chosen].spec.emits[0])
        payload = {
                "about_handle": handle,
                "description": description,
                # Judges see the event the producer answered, never the producer's private
                # memory or its copy of the world block, and never its name.
                "inputs": {"kind": inputs["kind"], "payload": inputs["payload"]},
                "outputs": ret.outputs,
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
                "outputs": payload.get("outputs", payload),
                "cost_micro_usd": payload.get("cost", 0),
                "status": payload.get("status", "ok"),
                # what it says it was choosing among, and what it chose.
                "propensity": payload.get("propensity"),
            },
            "charter": self._charter_text(),
            "predicates": [
                {"predicate": p.id, "description": p.description, "params": list(p.param_schema)}
                for p in SEED_VOCABULARY
            ],
            "forecast_example": {
                "predicate": "wallet_up",
                "params": {"horizon_events": self.ev.forecast_horizon_events},
                "q": 0.4,
            },
            "world": self._world_block(),
            "your_recent_returns": list(self.memory.get(sample.chosen, ())),
            "your_consequence_standing": self._standing_for(sample.chosen),
            "your_action_policy": self._action_policy(sample.chosen),  # private
        }
        generic = ev.kind is not EventKind.PRODUCER_RETURN
        if generic:
            inputs["event"] = {"kind": str(ev.kind), "payload": payload}
            inputs["subject_handle"] = about
        schema = {
            "type": "object",
            "properties": {
                "verdict": {"type": "number", "minimum": 0, "maximum": 1},
                "payoff": {"type": "number", "minimum": 0, "maximum": 1},
                "rationale": {"type": "string"},
                "propensity": {"type": "object"},
                "forecasts": self._forecast_schema(),
                "register": self._register_schema(),
                "about_handle": {"type": "string"},
            },
            "required": ["verdict", "payoff", "rationale", "forecasts"],
        }
        req = self._request(
            handle,
            ("Evaluate the public return addressed by about_handle. The input's subject_handle "
             "is the default when present. Give two numbers: verdict = its quality against "
             if generic else
             "Evaluate a producer return. Give two numbers: verdict = its quality against ") +
            "the charter (0 to 1); payoff = your probability that return_paid_off, the "
            "kernel's consequence predicate, resolves true for the return. Then give "
            f"{self.ev.max_forecasts_per_verdict} forecasts: for each, a predicate from the "
            "list and q = your probability it happens within its horizon.",
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
        self.memory.setdefault(sample.chosen, deque(maxlen=3)).append(
            {"handle": handle, "outputs": ret.outputs, "verdict": None}
        )
        verdict = _as_unit(ret.outputs.get("verdict")) if ret.status == "ok" else None
        payoff = _as_unit(ret.outputs.get("payoff")) if ret.status == "ok" else None
        target = (self._judged_event(ev, handle, ret, seals_payoff=True)
                  if verdict is not None else None)
        if target is not None:
            about = self._event_subject(target)
            payload = _to_plain(target.payload)
        else:
            verdict = None
        if verdict is None or payoff is None:
            # a malformed verdict is objectively non-conforming; the producer stays unjudged
            self.queue.settle(
                handle,
                channel=CH_CONFORMITY,
                score=0.0,
                status=SettleStatus.SETTLED,
                definition_version=DEF_CONFORMITY,
                sampling_ref=None,
            )
            self.stats.conformities += 1
            self.window.outcomes += 1
            return
        pend = self.pending.pop(about, None)
        about_decision = self.queue.get(about)
        if about_decision.channel == CH_EXPOSURE:
            owner = self.handle_to_assembly.get(about)
            if owner is not None:
                for entry in self.memory.get(owner, ()):
                    if entry["handle"] == about:
                        entry["verdict"] = verdict
        if (
            pend is not None
            and about_decision.channel in (CH_VERDICT, CH_CONFORMITY)
            and about_decision.status is SettleStatus.PENDING
        ):
            self._settle_priced(
                about,
                channel=about_decision.channel,
                score=verdict,
                definition_version=(DEF_VERDICT if about_decision.channel == CH_VERDICT
                                    else DEF_CONFORMITY),
                sampling_ref=handle,
                cards="producer" if about_decision.channel == CH_VERDICT else "evaluator",
            )
            self.stats.verdicts += 1
            self.stats.max_settlement_latency_events = max(
                self.stats.max_settlement_latency_events, self.n - pend.opened_at_event
            )
            owner = self.handle_to_assembly.get(about)
            if owner is not None:
                for entry in self.memory.get(owner, ()):
                    if entry["handle"] == about:
                        entry["verdict"] = verdict
        forecast = self.consequences.seal_verdict(
            self.book,
            self.queue,
            evaluator_handle=handle,
            evaluator_id=sample.chosen,
            about=about,
            payoff=payoff,
            event=self.n,
            now_ns=self.clock.now_ns,
            tick_ns=self.tick_clock.interval_ns,
        )
        self.stats.forecasts_sealed += 1
        self._open_forecasts(handle, sample.chosen, about, ret.outputs.get("forecasts"))
        self.pending[handle] = PendingJudgement(handle, CH_CONFORMITY, self.n)
        self._emit(
            EventKind.VERDICT,
            {
                "about_handle": about,
                "evaluator_handle": handle,
                "verdict": verdict,
                "payoff": payoff,
                "payoff_handle": forecast.handle,
                "rationale": str(ret.outputs.get("rationale", ""))[:2000],
                "producer_outputs": payload.get("outputs", payload),
                "propensity": self._public_propensity(handle),
            },
        )

    def _meta_step(self, ev: Event, handle: str, sample: Sample, deadline: int,
                   *, returned: Return | None = None) -> None:
        self._start_return(handle)
        payload = _to_plain(ev.payload)
        recursive = ev.kind is EventKind.META_VERDICT
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
                **({} if recursive else {"payoff": payload.get("payoff")}),
                "rationale": payload.get("rationale", ""),
                # The judge's own account of the verdicts it was choosing among.
                "propensity": payload.get("propensity"),
            },
            "producer_outputs": payload.get("producer_outputs", {}),
            "charter": self._charter_text(),
            "world": self._world_block(),
        }
        inputs["your_action_policy"] = self._action_policy(sample.chosen)  # private
        if "window" in payload:
            inputs["window"] = payload["window"]
        if recursive:
            inputs["meta_verdict"] = payload
        generic = ev.kind not in (EventKind.VERDICT, EventKind.META_VERDICT)
        if generic:
            inputs["event"] = {"kind": str(ev.kind), "payload": payload}
            inputs["subject_handle"] = about
        # The root judge whose payoff forecast eventually grades this tier's top meta.
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
        req = self._request(
            handle,
            ("Assess the public return addressed by about_handle for conformity with the charter. "
             "The input's subject_handle is the default when present." if generic else
             "Assess the released representative verdict for conformity with the charter, "
             "using its window as context."),
            inputs,
            schema,
            deadline,
            channel,
            propensity=payload.get("propensity"),
        )
        ret = (returned if returned is not None else self._invoke(sample.chosen, req, "meta"))
        self.consequences.finish(handle, ret.cost)
        self.handle_to_assembly[handle] = sample.chosen
        self.memory.setdefault(sample.chosen, deque(maxlen=3)).append(
            {"handle": handle, "outputs": ret.outputs, "verdict": None}
        )
        self._apply_registrations(handle, ret)
        conformity = _as_unit(ret.outputs.get("conformity")) if ret.status == "ok" else None
        target = self._judged_event(ev, handle, ret) if conformity is not None else None
        if target is not None:
            about = self._event_subject(target)
            if target is not ev:
                payload = _to_plain(target.payload)
                tier = payload.get("tier", 1) + 1
                judge_handle = payload.get("evaluator_handle", about)
        else:
            conformity = None
        if channel == CH_FAST and conformity is None:
            # a malformed conformity is objectively non-conforming
            self._settle_priced(
                handle,
                channel=CH_FAST,
                score=0.0,
                definition_version=DEF_FAST,
                sampling_ref=None,
                cards="meta",
            )
            self.stats.fast_settlements += 1
        elif channel == CH_FAST:
            # The top meta earns nothing for being well formed: its conformity is graded by
            # Brier against the judged verdict's eventual consequence.
            known = self.verdict_outcomes.get(judge_handle)
            if known is not None:
                y, _at, forecast_handle = known
                self._settle_meta_consequence(handle, conformity, y, forecast_handle)
            else:
                self.ledger.append({"kind": "meta.awaiting_consequence", "handle": handle,
                                    "judge_handle": judge_handle, "ts": self.clock.now_ns})
                self.pending_meta.setdefault(judge_handle, []).append((handle, conformity))
        else:
            self.ledger.append(
                {
                    "kind": "meta.pending",
                    "handle": handle,
                    "tier": tier,
                    "opened_at_event": self.n,
                    "ts": self.clock.now_ns,
                }
            )
            self.pending[handle] = PendingJudgement(handle, channel, self.n, tier)
        if conformity is not None:
            self._emit(
                EventKind.META_VERDICT,
                {
                    "about": about,
                    "tier": tier,
                    "score": conformity,
                    "by": handle,
                    "evaluator_handle": judge_handle,
                    "propensity": self._public_propensity(handle),
                    "rationale": str(ret.outputs.get("rationale", ""))[:2000],
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
    drip: bool = True,
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
        drip=drip,
        router_gamma=router_gamma,
        provider=provider,
        market=market,
        exchange=exchange,
        clock_source=clock_source,
        kill_at_end=kill_at_end,
    ).run()
