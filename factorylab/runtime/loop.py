"""The event loop: world → nervous system → cortex → evaluation → settlement.

Spec v0.4 section 4.11 and v0.5 sections 3, 4. The loop owns no money, no
scores and no rules; it is glue over kernel physics.

Roles. Producers respond to world events. Every producer decision, including
NOOP, is published as a ``ProducerReturn`` and judged by an evaluator chosen
by the evaluator router (whose executed distribution blends a protected
share weighted by consequence standing). An evaluator returns a verdict,
which settles the producer decision on its ``verdict`` channel, and sealed
forecasts, each opened as its own decision on the ``consequence`` channel
and settled later by the world. A meta assembly judges the verdict and
settles the evaluator decision on ``conformity``. Anything nobody judged in
time is censored: no score, no learning, no manufactured outcome.

Registration. Any return may carry proposals. Well-formed ones are paid from
the novelty reserve, registered with the proposing decision as provenance,
and announced. Adding an assembly opens a new comparator epoch for every
router whose menu grew.
"""

from __future__ import annotations

import hashlib
import json
import random
from collections import deque
from dataclasses import dataclass, field
from decimal import ROUND_HALF_EVEN, Decimal
from typing import Any

from factorylab.charter.charter import Charter, seed_charter
from factorylab.cortex.assembly import Assembly, AssemblySpec
from factorylab.cortex.registration import (
    AssemblyProposal,
    ModelProposal,
    RouterProposal,
    parse_proposals,
)
from factorylab.cortex.request import Request, Return
from factorylab.kernel.events import Bus, Event, EventKind
from factorylab.kernel.ledger import Ledger
from factorylab.kernel.money import money_to_usd, usd_to_money
from factorylab.kernel.queue import DecisionQueue, LearningReturn, PropensityRecord, SettleStatus
from factorylab.kernel.registry import Contract, PriceSpec, Registry, ResourceBounds
from factorylab.kernel.reserve import NoveltyReserve
from factorylab.kernel.termination import Termination
from factorylab.kernel.timing import TimingRegistry, UpwardBuffer
from factorylab.kernel.wallet import DripSchedule, Infeasible, Wallet
from factorylab.learners.base import BanditFeedback
from factorylab.learners.exp3 import EXP3
from factorylab.learners.router import Router, Sample
from factorylab.runtime.live import LiveClock, LiveVenue, Reconciler, build_provider
from factorylab.runtime.worlds import WorldManifest
from factorylab.settlement import (
    SEED_VOCABULARY,
    ConsequenceStanding,
    Forecast,
    ForecastBook,
    Observer,
    PrevalenceBaseline,
    Settler,
    WindowFacts,
    open_forecast_decision,
)
from factorylab.world.clock import ClockSource, DripSource, merge_sources
from factorylab.world.events import WorldEvent, WorldEventKind
from factorylab.world.exchange import FakeExchange, HyperliquidExchange, Order
from factorylab.world.metering import Meter, MeteredModel
from factorylab.world.models import ModelRequest, ModelResponse, TokenPrice

NOOP = "NOOP"
CH_FAST, CH_VERDICT, CH_CONFORMITY, CH_CONSEQUENCE = "fast", "verdict", "conformity", "consequence"
DEF_FAST, DEF_VERDICT, DEF_CONFORMITY = "fast-v1", "verdict-v1", "conformity-v1"
PRODUCER_KINDS = frozenset({"Tick", "MarketMid", "Funding", "Fill", "OrderRejected"})
EVALUATION_BOUNDARY = "producer → evaluator → meta"


class SimClock:
    """Simulated time. Every kernel component reads the current event's timestamp from here."""

    def __init__(self, now_ns: int = 0) -> None:
        self.now_ns = now_ns

    def __call__(self) -> int:
        return self.now_ns


# --------------------------------------------------------------------- scripted provider


@dataclass
class ScriptedProvider:
    """Deterministic stand-in for models in the scripted world, aware of the three roles.

    Producers cycle buy / hold / sell / hold on ticks (sized to equity) and
    occasionally propose registrations. Evaluators return a verdict and two
    forecasts whose probabilities depend on the evaluator's own prompt, so
    evaluators differ. Metas return a conformity score. Token usage is
    declared so costs are exact. It exists to close the loop, not to be
    clever.
    """

    name: str = "scripted"
    notional_fraction: str = "0.8"
    leverage: str = "3"
    input_tokens: int = 300
    output_tokens: int = 40
    register_at_calls: tuple[int, ...] = (40, 60, 80)
    _producer_calls: int = 0

    def complete(self, req: ModelRequest) -> ModelResponse:
        text = "\n".join(str(m.get("content", "")) for m in req.messages)
        inputs = _inputs_from_prompt(text)
        desc = _description_from_prompt(text)
        if desc.startswith("Evaluate"):
            reply = self._evaluate(req, inputs)
        elif desc.startswith("Assess"):
            reply = self._meta(inputs)
        else:
            reply = self._produce(desc, inputs)
        return ModelResponse(
            req.model_id, json.dumps(reply), self.input_tokens, self.output_tokens, "end_turn"
        )

    def _produce(self, desc: str, inputs: dict[str, Any]) -> dict[str, Any]:
        self._producer_calls += 1
        reply: dict[str, Any] = {"action": "hold"}
        if "event Tick" in desc:
            try:
                payload = inputs["payload"]
                equity = Decimal(str(payload["account"]["equity_usd"]))
                mid = Decimal(str(payload["mids"]["BTC"]))
                phase = int(payload["index"]) % 4
            except (KeyError, ValueError, ArithmeticError, TypeError):
                equity, mid, phase = Decimal(0), Decimal(0), 0
            if mid > 0 and equity > 0 and phase in (1, 3):
                notional = equity * Decimal(self.leverage) * Decimal(self.notional_fraction)
                size = (notional / mid).quantize(Decimal("0.000001"))
                if size > 0:
                    side = "buy" if phase == 1 else "sell"
                    reply = {"action": "order", "coin": "BTC", "side": side, "size": str(size)}
        n = self._producer_calls
        if n == self.register_at_calls[0]:
            reply["register"] = [
                {
                    "kind": "assembly",
                    "id": "funding-watcher",
                    "role": "producer",
                    "model_id": "fake-haiku",
                    "system_prompt": "Watch funding and mids; reply with a JSON action.",
                    "accepts": ["Funding", "MarketMid"],
                    "max_tokens": 128,
                }
            ]
        elif n == self.register_at_calls[1]:
            reply["register"] = [{"kind": "model", "openrouter_id": "meta/muse-spark-1.3"}]
        elif n == self.register_at_calls[2]:
            reply["register"] = [
                {"kind": "router", "event_kind": "MarketMid", "learner": "exp3", "gamma": 0.2}
            ]
        return reply

    @staticmethod
    def _evaluate(req: ModelRequest, inputs: dict[str, Any]) -> dict[str, Any]:
        producer = inputs.get("producer", {})
        status = producer.get("status")
        action = (producer.get("outputs") or {}).get("action")
        verdict = 1.0 if status == "ok" and action in ("order", "hold") else 0.3
        if action == "noop":
            verdict = 0.6
        style = int(hashlib.sha256(req.system.encode()).hexdigest(), 16) % 4
        q = (0.3, 0.45, 0.6, 0.75)[style]
        return {
            "verdict": verdict,
            "rationale": "scripted judgement",
            "forecasts": [
                {"predicate": "wallet_up", "params": {"horizon_events": 10}, "q": q},
                {"predicate": "fill_within", "params": {"horizon_events": 10}, "q": 1 - q},
            ],
        }

    @staticmethod
    def _meta(inputs: dict[str, Any]) -> dict[str, Any]:
        v = inputs.get("verdict", {})
        ok = isinstance(v.get("verdict"), int | float) and bool(v.get("rationale"))
        return {"conformity": 0.8 if ok else 0.1, "rationale": "scripted meta"}


def _description_from_prompt(text: str) -> str:
    try:
        start = text.index("REQUEST\n") + len("REQUEST\n")
        end = text.index("\n\nINPUTS", start)
        return text[start:end]
    except ValueError:
        return ""


def _inputs_from_prompt(text: str) -> dict[str, Any]:
    try:
        start = text.index("INPUTS\n") + len("INPUTS\n")
        end = text.index("\n\nOUTCOME SCHEMA", start)
        return json.loads(text[start:end])
    except (ValueError, json.JSONDecodeError):
        return {}


# --------------------------------------------------------------------------- state


@dataclass
class RouterState:
    kind: str
    universe: list[str]
    learner: Any
    router: Router
    epoch: int = 1


@dataclass
class PendingJudgement:
    handle: str  # decision awaiting a verdict (producer) or conformity (evaluator)
    channel: str
    opened_at_event: int


@dataclass
class RunStats:
    events: int = 0
    decisions: int = 0
    invocations: int = 0
    noops: int = 0
    orders_placed: int = 0
    orders_rejected: int = 0
    fills: int = 0
    producer_returns: int = 0
    verdicts: int = 0
    conformities: int = 0
    censored: int = 0
    fast_settlements: int = 0
    forecasts_sealed: int = 0
    forecasts_settled: int = 0
    timeouts: int = 0
    upward_releases: int = 0
    reserve_windows: int = 0
    exclusions: int = 0
    registrations_accepted: int = 0
    registrations_rejected: int = 0
    epochs: int = 0
    routers_replaced: int = 0
    reconciliations: int = 0
    max_settlement_latency_events: int = 0
    sample_propensity: dict[str, Any] | None = None
    invocation_status: dict[str, int] = field(default_factory=dict)
    invocations_by_role: dict[str, int] = field(default_factory=dict)
    stop_reasons: dict[str, int] = field(default_factory=dict)


def _usd_to_micro(value: str | Decimal) -> int:
    q = Decimal(str(value)).quantize(Decimal("0.000001"), rounding=ROUND_HALF_EVEN)
    return usd_to_money(str(q))


def _to_plain(payload: Any) -> Any:
    if hasattr(payload, "items"):
        return {k: _to_plain(v) for k, v in payload.items()}
    if isinstance(payload, list | tuple):
        return [_to_plain(v) for v in payload]
    return payload


# ------------------------------------------------------------------------- runtime


class Runtime:
    """One world, from launch to the end of its event budget or its death."""

    def __init__(
        self,
        manifest: WorldManifest,
        *,
        events: int,
        seed: int | None,
        initial_balance_micro: int | None,
        ledger_path: str | None,
        drip: bool,
        router_gamma: float,
        provider: Any | None = None,
        exchange: Any | None = None,
        clock_source: Any | None = None,
        reconcile_every: int = 10,
        kill_at_end: bool = False,
    ) -> None:
        self.m = manifest
        self.kill_at_end = kill_at_end
        self.live = manifest.exchange.kind != "fake"
        self.clock_source = clock_source
        self.reconciler = Reconciler(every=reconcile_every)
        self.events_budget = events
        self.seed = manifest.seed if seed is None else seed
        self.rng = random.Random(self.seed)
        self.clock = SimClock(0)
        self.stats = RunStats()
        self.ev = manifest.evaluation
        self.charter: Charter = seed_charter()

        # kernel
        self.ledger = Ledger(
            ledger_path,
            manifest=json.loads(manifest.canonical_json()),
            clock_ns=self.clock,
            full_verify_every=1024,
            key_path=(ledger_path + ".key") if ledger_path else None,
        )
        self.use_drip = drip and manifest.drip is not None
        schedule = None
        if self.use_drip and manifest.drip is not None:
            d = manifest.drip
            schedule = DripSchedule(d.amount_micro, d.period_ns, d.start_ns, d.end_ns)
        self.initial = (
            manifest.initial_balance_micro
            if initial_balance_micro is None
            else initial_balance_micro
        )
        self.wallet = Wallet(self.initial, self.ledger, schedule, clock_ns=self.clock)
        self.bus = Bus(self.ledger)
        self.termination = Termination(ledger=self.ledger, bus=self.bus, clock_ns=self.clock)
        self.registry = Registry(self.ledger)
        self.queue = DecisionQueue(self.ledger, clock_ns=self.clock)
        self.reserve = NoveltyReserve(
            manifest.novelty.share,
            manifest.novelty.window_ns,
            has_history=self.queue.has_history,
            ledger=self.ledger,
            clock_ns=self.clock,
        )
        self.timing = TimingRegistry()
        self.timing.register_loop("leaf", [])
        self.timing.register_loop("governance", ["leaf"])
        self.buffer = UpwardBuffer(
            self.timing, "governance", min_ratio=manifest.timing.min_ratio, seed=self.seed
        )

        # settlement
        self.book = ForecastBook(self.ledger)
        self.baseline = PrevalenceBaseline()
        self.standing = ConsequenceStanding(self.ev.min_coverage)
        self.observer = Observer()
        self.settler = Settler(self.book, self.queue, self.standing, self.baseline, self.observer)

        # world
        if exchange is not None:
            self.exchange = exchange
        elif self.live:
            self.exchange = HyperliquidExchange(
                mainnet=manifest.exchange.mainnet, coins=manifest.exchange.coins
            )
        else:
            shocks: dict[int, dict[str, Decimal]] = {}
            for sh in manifest.exchange.shocks:
                shocks.setdefault(sh.step, {})[sh.coin] = Decimal(sh.multiplier)
            self.exchange = FakeExchange(
                seed=manifest.exchange.seed,
                coins=manifest.exchange.coins,
                start_cash_usd=money_to_usd(self.initial),
                shocks=shocks,
            )
        self.venue = LiveVenue(self.exchange) if self.live else None
        self.prices = manifest.price_table()
        self.meter = Meter(self.wallet)
        if provider is None:
            provider = build_provider(manifest)
        self.provider = provider if provider is not None else ScriptedProvider()
        self.catalogue: dict[str, TokenPrice] | None = None
        if hasattr(self.provider, "catalogue"):
            try:
                self.catalogue = {e.id: e.price() for e in self.provider.catalogue()}
            except Exception:  # catalogue unavailable: model proposals will be rejected
                self.catalogue = None

        self._register_seed_contracts()
        self.assemblies: dict[str, Assembly] = {}
        for a in manifest.assemblies:
            self._instantiate(
                AssemblySpec(
                    id=a.id,
                    version=1,
                    model_id=a.model_id,
                    max_tokens=a.max_tokens,
                    effort=a.effort,
                    memory_policy=a.memory_policy,
                    accepts=frozenset(a.accepts),
                    role=a.role,
                )
            )

        # nervous system
        self.router_gamma = router_gamma
        self.routers: dict[str, RouterState] = {}
        for kind in self._routable_kinds():
            self._build_router(kind, "exp3", router_gamma)
        self.delivered_seen: dict[str, int] = {}
        self.snapshot_keys: dict[str, str] = {}  # decision handle -> snapshot key

        # loop state
        self.pending: dict[str, PendingJudgement] = {}
        self.balance_at: list[int] = [self.wallet.balance]  # index = event number
        self.events_log: list[dict[str, Any]] = [{"kind": "Launch", "payload": {}}]
        self.last_closure_ns = -1
        self.reserve_window_start: int | None = None
        self.internal: deque[Event] = deque()
        self.n = 0
        self.emitted = 0

    # ---- setup helpers

    def _register_seed_contracts(self) -> None:
        for tier in self.m.models:
            price = self.prices.price(tier.id)
            self.registry.register(_model_contract(tier.id, price, tier.provider))
        for seed in self.m.assemblies:
            self.registry.register(
                _assembly_contract(seed.id, seed.role, seed.accepts, seed.max_tokens)
            )
        self.registry.register(
            Contract(
                id="exchange:" + self.m.exchange.kind,
                version=1,
                kind="exchange",
                description="venue for perpetual orders",
                input_schema={"type": "object"},
                output_schema={"type": "object"},
                price=PriceSpec({}),
                permissions=frozenset({"exchange.order"}),
                resource_bounds=ResourceBounds(),
            )
        )

    def _instantiate(self, spec: AssemblySpec) -> Assembly:
        asm = Assembly(spec, MeteredModel(self.provider, self.prices, self.meter))
        self.assemblies[spec.id] = asm
        return asm

    def _routable_kinds(self) -> list[str]:
        kinds = {k for a in self.assemblies.values() for k in a.spec.accepts}
        return sorted(kinds)

    def _universe_for(self, kind: str) -> list[str]:
        ids = sorted(a.spec.id for a in self.assemblies.values() if kind in a.spec.accepts)
        return ids + [NOOP]

    def _build_router(self, kind: str, learner_kind: str, gamma: float) -> RouterState:
        universe = self._universe_for(kind)
        learner: Any
        if learner_kind == "blum_mansour":
            from factorylab.learners.blum_mansour import BlumMansour
            from factorylab.learners.delayed import SnapshotLearner

            inner = BlumMansour(lambda acts: EXP3(acts, gamma), universe, id=f"router:{kind}")
            learner = _KeyedLearner(SnapshotLearner(inner, id=f"router:{kind}"))
        else:
            learner = EXP3(universe, gamma, id=f"router:{kind}")
        router = Router(learner, lambda _k, u=universe: [x for x in u if x != NOOP])
        state = RouterState(kind, universe, learner, router)
        self.routers[kind] = state
        if not hasattr(self, "delivered_seen"):
            self.delivered_seen = {}
        self.delivered_seen.setdefault(learner.id, 0)
        return state

    # ---- feasibility and mixing

    def _is_feasible(self, action_id: str) -> tuple[bool, str]:
        asm = self.assemblies[action_id]
        probe = ModelRequest(
            asm.spec.model_id,
            asm.spec.system_prompt,
            ({"role": "user", "content": ""},),
            asm.spec.max_tokens,
        )
        ceiling = asm.model.ceiling(probe) * 2
        if ceiling > self.wallet.available:
            return False, f"wallet: ceiling {ceiling} exceeds available {self.wallet.available}"
        return True, ""

    def _mix_with_standing(self, dist: dict[str, float]) -> dict[str, float]:
        s = self.ev.consequence_share
        evaluators = [a for a in dist if a != NOOP]
        if s <= 0 or not evaluators:
            return dist
        weights = {a: self.standing.weight(a) for a in evaluators}
        total = sum(weights.values())
        if total <= 0:
            return dist
        mixed = {a: (1 - s) * p + s * (weights.get(a, 0.0) / total) for a, p in dist.items()}
        norm = sum(mixed.values())
        return {a: p / norm for a, p in mixed.items()}

    # ---- the loop

    def run(self) -> dict[str, Any]:
        tick_ns = self.m.tick_interval_ns
        if self.clock_source is not None:
            sources = [self.clock_source]
        elif self.live:
            sources = [LiveClock(tick_ns, self.events_budget).events()]
        else:
            sources = [
                ClockSource(
                    start_ns=tick_ns, interval_ns=tick_ns, count=self.events_budget
                ).events()
            ]
        if self.use_drip and self.m.drip is not None:
            d = self.m.drip
            sources.append(
                DripSource(
                    d.amount_micro,
                    d.period_ns,
                    max(d.start_ns, tick_ns),
                    min(d.end_ns, (self.events_budget + 1) * tick_ns),
                ).events()
            )
        stream = merge_sources(*sources)
        self.bus.publish(
            Event(
                "launch", EventKind.LAUNCH, 0, {"manifest_hash": self.m.manifest_hash()}, "kernel"
            )
        )
        while True:
            ev = self._next_event(stream)
            if ev is None:
                break
            self.n += 1
            self.clock.now_ns = max(self.clock.now_ns, ev.ts_ns)
            self.bus.publish(ev)
            self.stats.events += 1
            self.events_log.append({"kind": str(ev.kind), "payload": _to_plain(ev.payload)})

            self.wallet.drip(self.clock.now_ns)
            self._manage_reserve_window()
            if ev.kind is EventKind.TICK:
                if self.venue is not None:
                    self._settle_exchange_effects(self.venue.on_tick(self.clock.now_ns))
                    if self.reconciler.due():
                        snap = Reconciler.snapshot(
                            self.wallet.balance, self.provider, self.exchange
                        )
                        self.stats.reconciliations += 1
                        self._emit(EventKind.RECONCILED, snap, source="kernel")
                else:
                    self._settle_exchange_effects(self.exchange.advance(self.clock.now_ns))
            if self._check_termination():
                break

            self._route(ev)
            if self._check_termination():
                break

            self._settle_due_forecasts()
            self._censor_stale_judgements()
            self.stats.timeouts += len(self.queue.expire(self.clock.now_ns))
            self._deliver_returns()
            self.balance_at.append(self.wallet.balance)
        if self.kill_at_end and not self.termination.final:
            # a budgeted rehearsal world ends by explicit kill so its diary becomes readable
            self.termination.kill("explicit_kill:budget")
        return self._summary()

    def _next_event(self, stream) -> Event | None:
        if self.internal:
            return self.internal.popleft()
        we = next(stream, None)
        if we is None:
            return None
        return self._kernel_event(we)

    def _kernel_event(self, we: WorldEvent) -> Event:
        self.emitted += 1
        return Event(
            f"ev-{self.emitted}", EventKind(str(we.kind)), we.ts_ns, dict(we.payload), we.source
        )

    def _emit(self, kind: EventKind, payload: dict[str, Any], source: str = "runtime") -> None:
        self.emitted += 1
        self.internal.append(
            Event(f"{kind.value.lower()}-{self.emitted}", kind, self.clock.now_ns, payload, source)
        )

    def _check_termination(self) -> bool:
        reason = self.termination.check(self.wallet, self.clock.now_ns)
        if reason is None:
            return False
        self.termination.kill(reason)
        return True

    def _manage_reserve_window(self) -> None:
        if (
            self.reserve_window_start is None
            or self.clock.now_ns >= self.reserve_window_start + self.m.novelty.window_ns
        ):
            self.reserve.open_window(self.clock.now_ns, self.wallet.balance)
            self.reserve_window_start = self.clock.now_ns
            self.stats.reserve_windows += 1

    # ---- exchange effects

    def _settle_exchange_effects(self, evs: list[WorldEvent]) -> None:
        for we in evs:
            if self.wallet.dead:
                return
            if we.kind is WorldEventKind.FILL:
                self.stats.fills += 1
                delta = _usd_to_micro(we.payload["realized_usd"]) - _usd_to_micro(
                    we.payload["fee_usd"]
                )
                if delta:
                    self.wallet.settle(delta, f"fill:{we.payload['order_id']}", "exchange_pnl")
            elif we.kind is WorldEventKind.FUNDING:
                paid = _usd_to_micro(we.payload["paid_usd"])
                if paid:
                    self.wallet.settle(-paid, f"funding:{we.payload['coin']}:{we.ts_ns}", "funding")
            self.internal.append(self._kernel_event(we))
        if hasattr(self.exchange, "sync_cash"):
            self.exchange.sync_cash(money_to_usd(self.wallet.balance))

    def _execute_outputs(self, ret: Return) -> None:
        out = ret.outputs
        if ret.status != "ok" or out.get("action") != "order":
            return
        try:
            order = Order(
                str(out["coin"]),
                str(out.get("side", "buy")).lower() == "buy",
                Decimal(str(out["size"])),
            )
        except (KeyError, ValueError, ArithmeticError):
            return
        result = self.exchange.place(order)
        self.stats.orders_placed += 1
        if result.status == "rejected":
            self.stats.orders_rejected += 1
        if hasattr(self.exchange, "drain_events"):  # fake venue fills synchronously
            self._settle_exchange_effects(self.exchange.drain_events())

    # ---- routing

    def _route(self, ev: Event) -> None:
        kind = str(ev.kind)
        state = self.routers.get(kind)
        if state is None:
            return
        mix = self._mix_with_standing if kind == "ProducerReturn" else None
        key = f"{state.learner.id}:{self.n}"
        if isinstance(state.learner, _KeyedLearner):
            state.learner.current_key = key
        sample = state.router.route(kind, self._is_feasible, self.rng, mix=mix)
        self.stats.exclusions += len(sample.excluded)
        role = self._role_for_kind(kind)
        channel = {"producer": CH_VERDICT, "evaluator": CH_CONFORMITY, "meta": CH_FAST}[role]
        deadline = (
            self.clock.now_ns + (self.ev.verdict_timeout_events + 2) * self.m.tick_interval_ns
        )
        handle = self.queue.open(
            actor=sample.learner_id,
            event_id=ev.id,
            propensity=self._propensity(sample),
            channel=channel,
            deadline_ns=deadline,
            parent_handle=None,
            cost_ceiling=self.wallet.available,
        )
        if isinstance(state.learner, _KeyedLearner):
            self.snapshot_keys[handle] = key
        self.stats.decisions += 1
        if self.stats.sample_propensity is None and sample.chosen != NOOP:
            self.stats.sample_propensity = {
                "handle": handle,
                "action_ids": list(sample.action_ids),
                "probs": list(sample.probs),
                "chosen": sample.chosen,
                "rng_seed": sample.rng_seed,
            }
        if role == "producer":
            self._producer_step(ev, handle, sample, deadline)
        elif role == "evaluator":
            self._evaluator_step(ev, handle, sample, deadline)
        else:
            self._meta_step(ev, handle, sample, deadline)

    @staticmethod
    def _propensity(sample: Sample) -> PropensityRecord:
        return PropensityRecord(
            sample.action_ids,
            sample.probs,
            sample.chosen,
            sample.rng_seed,
            sample.learner_id,
            sample.learner_state_hash,
        )

    @staticmethod
    def _role_for_kind(kind: str) -> str:
        if kind == "ProducerReturn":
            return "evaluator"
        if kind == "Verdict":
            return "meta"
        return "producer"

    def _invoke(self, action_id: str, req: Request, role: str) -> Return:
        asm = self.assemblies[action_id]
        ret = asm.invoke(req)
        self.stats.invocations += 1
        self.stats.invocation_status[ret.status] = (
            self.stats.invocation_status.get(ret.status, 0) + 1
        )
        self.stats.invocations_by_role[role] = self.stats.invocations_by_role.get(role, 0) + 1
        sr = ret.stop_reason or "none"
        self.stats.stop_reasons[sr] = self.stats.stop_reasons.get(sr, 0) + 1
        self.ledger.append(
            {
                "kind": "invocation",
                "assembly_id": action_id,
                "role": role,
                "handle": req.handle,
                "cost": ret.cost,
                "status": ret.status,
                "stop_reason": sr,
                "served_by": ret.served_by,
                "ts": self.clock.now_ns,
            }
        )
        return ret

    def _request(
        self,
        handle: str,
        description: str,
        inputs: dict[str, Any],
        schema: dict[str, Any],
        deadline: int,
        channel: str,
    ) -> Request:
        return Request(
            handle=handle,
            description=description,
            inputs=inputs,
            capability_versions={},
            outcome_schema=schema,
            deadline_ns=deadline,
            cost_ceiling=self.wallet.available,
            parent_handle=None,
            completion_criterion="a JSON object satisfying the outcome schema",
            scoring_channel=channel,
            resource_liability=handle,
        )

    # ---- producer

    def _producer_step(self, ev: Event, handle: str, sample: Sample, deadline: int) -> None:
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
        inputs = {"kind": str(ev.kind), "payload": payload}
        if sample.chosen == NOOP:
            self.stats.noops += 1
            ret = Return(handle, {"action": "noop"}, 0, "ok")
        else:
            schema = {"type": "object", "properties": {"action": {"type": "string"}}}
            req = self._request(handle, description, inputs, schema, deadline, CH_VERDICT)
            ret = self._invoke(sample.chosen, req, "producer")
            self._execute_outputs(ret)
            self._apply_registrations(handle, ret)
        self.pending[handle] = PendingJudgement(handle, CH_VERDICT, self.n)
        self.stats.producer_returns += 1
        self._emit(
            EventKind.PRODUCER_RETURN,
            {
                "about_handle": handle,
                "description": description,
                "inputs": inputs,
                "outputs": ret.outputs,
                "cost": ret.cost,
                "status": ret.status,
            },
        )

    # ---- evaluator

    def _evaluator_step(self, ev: Event, handle: str, sample: Sample, deadline: int) -> None:
        payload = _to_plain(ev.payload)
        about = payload["about_handle"]
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
                "description": payload["description"],
                "inputs": payload["inputs"],
                "outputs": payload["outputs"],
                "cost_micro_usd": payload["cost"],
                "status": payload["status"],
            },
            "charter": self.charter.render(),
            "vocabulary": [
                {"predicate": p.id, "description": p.description, "params": list(p.param_schema)}
                for p in SEED_VOCABULARY
            ],
        }
        schema = {
            "type": "object",
            "properties": {
                "verdict": {"type": "number", "minimum": 0, "maximum": 1},
                "rationale": {"type": "string"},
                "forecasts": {"type": "array"},
            },
            "required": ["verdict", "rationale"],
        }
        req = self._request(
            handle,
            "Evaluate a producer return against the charter and forecast its consequences.",
            inputs,
            schema,
            deadline,
            CH_CONFORMITY,
        )
        ret = self._invoke(sample.chosen, req, "evaluator")
        self._apply_registrations(handle, ret)
        verdict = _as_unit(ret.outputs.get("verdict")) if ret.status == "ok" else None
        if verdict is None:
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
            return
        pend = self.pending.pop(about, None)
        if pend is not None and self.queue.get(about).status is SettleStatus.PENDING:
            self.queue.settle(
                about,
                channel=CH_VERDICT,
                score=verdict,
                status=SettleStatus.SETTLED,
                definition_version=DEF_VERDICT,
                sampling_ref=handle,
            )
            self.stats.verdicts += 1
            self.stats.max_settlement_latency_events = max(
                self.stats.max_settlement_latency_events, self.n - pend.opened_at_event
            )
        self._open_forecasts(handle, sample.chosen, about, ret.outputs.get("forecasts"))
        self.pending[handle] = PendingJudgement(handle, CH_CONFORMITY, self.n)
        self._emit(
            EventKind.VERDICT,
            {
                "about_handle": about,
                "evaluator_handle": handle,
                "verdict": verdict,
                "rationale": str(ret.outputs.get("rationale", ""))[:2000],
                "producer_outputs": payload["outputs"],
            },
        )

    def _open_forecasts(
        self, evaluator_handle: str, evaluator_id: str, about: str, raw: Any
    ) -> None:
        if not isinstance(raw, list):
            return
        known = {p.id: p for p in SEED_VOCABULARY}
        for item in raw[: self.ev.max_forecasts_per_verdict]:
            if not isinstance(item, dict):
                continue
            pid = item.get("predicate")
            q = _as_unit(item.get("q"))
            params = item.get("params") if isinstance(item.get("params"), dict) else {}
            if pid not in known or q is None:
                continue
            horizon = params.get(known[pid].horizon_param, self.ev.forecast_horizon_events)
            if type(horizon) is not int or not 1 <= horizon <= 200:
                continue
            params = dict(params, **{known[pid].horizon_param: horizon})
            try:
                fh = open_forecast_decision(
                    self.queue,
                    evaluator_id=evaluator_id,
                    event_id=f"forecast-{evaluator_handle}",
                    q=q,
                    deadline_ns=self.clock.now_ns + (horizon + 2) * self.m.tick_interval_ns * 4,
                    parent_handle=evaluator_handle,
                    now_event=self.n,
                    horizon=horizon,
                )
                self.book.seal(
                    Forecast(fh, evaluator_id, about, pid, params, q, self.n, self.n + horizon, "")
                )
            except (ValueError, KeyError):
                continue
            self.stats.forecasts_sealed += 1

    # ---- meta

    def _meta_step(self, ev: Event, handle: str, sample: Sample, deadline: int) -> None:
        payload = _to_plain(ev.payload)
        evaluator_handle = payload["evaluator_handle"]
        if sample.chosen == NOOP:
            self.stats.noops += 1
            self.queue.settle(
                handle,
                channel=CH_FAST,
                score=0.0,
                status=SettleStatus.INAPPLICABLE,
                definition_version=DEF_FAST,
                sampling_ref=None,
            )
            return
        inputs = {
            "verdict": {"verdict": payload["verdict"], "rationale": payload["rationale"]},
            "producer_outputs": payload["producer_outputs"],
            "charter": self.charter.render(),
        }
        schema = {
            "type": "object",
            "properties": {"conformity": {"type": "number"}, "rationale": {"type": "string"}},
            "required": ["conformity"],
        }
        req = self._request(
            handle,
            "Assess an evaluator verdict for conformity with the charter.",
            inputs,
            schema,
            deadline,
            CH_FAST,
        )
        ret = self._invoke(sample.chosen, req, "meta")
        self._apply_registrations(handle, ret)
        conformity = _as_unit(ret.outputs.get("conformity")) if ret.status == "ok" else None
        self.queue.settle(
            handle,
            channel=CH_FAST,
            score=1.0 if conformity is not None else 0.0,
            status=SettleStatus.SETTLED,
            definition_version=DEF_FAST,
            sampling_ref=None,
        )
        self.stats.fast_settlements += 1
        if conformity is None:
            return
        pend = self.pending.pop(evaluator_handle, None)
        if pend is not None and self.queue.get(evaluator_handle).status is SettleStatus.PENDING:
            self.queue.settle(
                evaluator_handle,
                channel=CH_CONFORMITY,
                score=conformity,
                status=SettleStatus.SETTLED,
                definition_version=DEF_CONFORMITY,
                sampling_ref=handle,
            )
            self.stats.conformities += 1

    # ---- registration

    def _apply_registrations(self, handle: str, ret: Return) -> None:
        if ret.status != "ok":
            return
        accepted, rejected = parse_proposals(
            ret.outputs,
            event_kinds=PRODUCER_KINDS | {"ProducerReturn", "Verdict"},
            known_models=frozenset(self.prices.prices),
            known_assemblies=frozenset(self.assemblies),
        )
        for r in rejected:
            self.stats.registrations_rejected += 1
            self.ledger.append(
                {
                    "kind": "registration.rejected",
                    "handle": handle,
                    "index": r.index,
                    "reason": r.reason,
                    "ts": self.clock.now_ns,
                }
            )
        for prop in accepted:
            try:
                self._register(handle, prop)
                self.stats.registrations_accepted += 1
            except (Infeasible, PermissionError, ValueError, KeyError) as exc:
                self.stats.registrations_rejected += 1
                self.ledger.append(
                    {
                        "kind": "registration.rejected",
                        "handle": handle,
                        "reason": f"{type(exc).__name__}: {exc}",
                        "ts": self.clock.now_ns,
                    }
                )

    def _register(
        self, handle: str, prop: ModelProposal | AssemblyProposal | RouterProposal
    ) -> None:
        amount = self.ev.trial_amount_micro
        if isinstance(prop, ModelProposal):
            if self.catalogue is None or prop.openrouter_id not in self.catalogue:
                raise ValueError("no catalogue entry for that model in this world")
            price = self.catalogue[prop.openrouter_id]
            contract = _model_contract(prop.openrouter_id, price, "openrouter")
            res = self.reserve.reserve_for(contract, amount)
            self.registry.register(contract, by_handle=handle, reservation=res)
            self.prices.register(prop.openrouter_id, price)
            self._emit(
                EventKind.REGISTERED, {"kind": "model", "id": prop.openrouter_id, "by": handle}
            )
        elif isinstance(prop, AssemblyProposal):
            contract = _assembly_contract(prop.id, prop.role, prop.accepts, prop.max_tokens)
            res = self.reserve.reserve_for(contract, amount)
            self.registry.register(contract, by_handle=handle, reservation=res)
            self._instantiate(
                AssemblySpec(
                    id=prop.id,
                    version=1,
                    model_id=prop.model_id,
                    system_prompt=prop.system_prompt,
                    max_tokens=prop.max_tokens,
                    effort=prop.effort,
                    accepts=frozenset(prop.accepts),
                    role=prop.role,
                )
            )
            for kind in prop.accepts:
                self._open_epoch(kind)
            self._emit(
                EventKind.REGISTERED,
                {
                    "kind": "assembly",
                    "id": prop.id,
                    "role": prop.role,
                    "accepts": list(prop.accepts),
                    "by": handle,
                },
            )
        else:
            contract = Contract(
                id=f"router:{prop.event_kind}:{prop.learner}:{self.n}",
                version=1,
                kind="router",
                description=f"{prop.learner} router for {prop.event_kind}",
                input_schema={"type": "object", "properties": {"kind": {"const": prop.event_kind}}},
                output_schema={"type": "object"},
                price=PriceSpec({}),
                permissions=frozenset(),
                resource_bounds=ResourceBounds(),
            )
            res = self.reserve.reserve_for(contract, amount)
            self.registry.register(contract, by_handle=handle, reservation=res)
            if prop.learner == "blum_mansour":
                try:
                    import factorylab.learners.delayed  # noqa: F401
                except ImportError as exc:
                    raise ValueError("blum_mansour router unavailable in this build") from exc
            self._build_router(prop.event_kind, prop.learner, prop.gamma)
            self.stats.routers_replaced += 1
            self._emit(
                EventKind.ROUTER_REPLACED,
                {
                    "event_kind": prop.event_kind,
                    "learner": prop.learner,
                    "gamma": prop.gamma,
                    "by": handle,
                },
            )

    def _open_epoch(self, kind: str) -> None:
        state = self.routers.get(kind)
        universe = self._universe_for(kind)
        entry = {"kind": "epoch", "event_kind": kind, "universe": universe, "ts": self.clock.now_ns}
        if state is None:
            self._build_router(kind, "exp3", self.router_gamma)
            self.ledger.append({**entry, "carried": False})
            self.stats.epochs += 1
            return
        if universe == state.universe:
            return
        if isinstance(state.learner, EXP3):
            new_learner = state.learner.expand(universe)
            state.learner = new_learner
            state.universe = universe
            state.router = Router(new_learner, lambda _k, u=universe: [x for x in u if x != NOOP])
            state.epoch += 1
            self.ledger.append({**entry, "carried": True})
        else:  # snapshot learners cannot expand; rebuild fresh over the new universe
            prev = state.epoch
            self._build_router(kind, "blum_mansour", self.router_gamma)
            self.routers[kind].epoch = prev + 1
            self.ledger.append({**entry, "carried": False})
        self.stats.epochs += 1

    # ---- settlement and learning

    def _facts_for(self, f: Forecast) -> WindowFacts | None:
        if f.made_at_event >= len(self.balance_at):
            return None
        start = f.made_at_event
        window_balances = self.balance_at[start : self.n + 1]
        return WindowFacts(
            balance_at_forecast=self.balance_at[start],
            balance_at_settlement=self.wallet.balance,
            min_balance_in_window=min(window_balances) if window_balances else self.wallet.balance,
            events=tuple(self.events_log[start + 1 : self.n + 1]),
        )

    def _settle_due_forecasts(self) -> None:
        for s in self.settler.settle_due(self.n, self._facts_for):
            self.stats.forecasts_settled += 1
            self._emit(
                EventKind.FORECAST_SETTLED,
                {
                    "handle": s.handle,
                    "evaluator_id": s.evaluator_id,
                    "predicate": s.predicate_id,
                    "y": s.y,
                    "brier": s.brier,
                    "status": str(s.status),
                },
            )
            self.last_closure_ns = max(self.clock.now_ns, self.last_closure_ns + 1)
            self.timing.record_closure("leaf", self.last_closure_ns)
            self.buffer.add(
                LearningReturn(
                    s.handle, CH_CONSEQUENCE, float(s.brier), "brier-v1", SettleStatus.SETTLED, None
                ),
                self.clock.now_ns,
            )
            released = self.buffer.release()
            if released is not None:
                self.stats.upward_releases += 1
                self.ledger.append(
                    {"kind": "upward.release", "summary": released, "ts": self.clock.now_ns}
                )

    def _censor_stale_judgements(self) -> None:
        stale = [
            p
            for p in self.pending.values()
            if self.n - p.opened_at_event > self.ev.verdict_timeout_events
        ]
        for p in stale:
            del self.pending[p.handle]
            if self.queue.get(p.handle).status is SettleStatus.PENDING:
                self.queue.settle(
                    p.handle,
                    channel=p.channel,
                    score=0.0,
                    status=SettleStatus.CENSORED,
                    definition_version="censored-v1",
                    sampling_ref=None,
                )
                self.stats.censored += 1

    def _deliver_returns(self) -> None:
        for state in list(self.routers.values()):
            lid = state.learner.id
            returns = self.queue.returns_for(lid)
            for lr in returns[self.delivered_seen.get(lid, 0) :]:
                if lr.status not in (SettleStatus.SETTLED, SettleStatus.TIMED_OUT):
                    continue  # censored or inapplicable: no evidence, no update
                decision = self.queue.get(lr.handle)
                prop = decision.propensity
                idx = prop.action_ids.index(prop.chosen)
                reward = (
                    min(1.0, max(0.0, float(lr.score)))
                    if lr.status is SettleStatus.SETTLED
                    else 0.0
                )
                fb = BanditFeedback(prop.chosen, reward, prop.probs[idx])
                learner = state.learner
                if isinstance(learner, _KeyedLearner):
                    key = self.snapshot_keys.pop(lr.handle, None)
                    if key is not None:
                        learner.inner.update_for(key, fb)
                elif set(prop.action_ids) <= set(state.universe):
                    learner.update(fb)
            self.delivered_seen[lid] = len(returns)

    # ---- summary

    def _summary(self) -> dict[str, Any]:
        summary: dict[str, Any] = {
            "world": self.m.name,
            "manifest_hash": self.m.manifest_hash(),
            "seed": self.seed,
            "drip": self.use_drip,
            "terminated": self.termination.final,
            "termination_reason": self.termination.reason,
            "seal_key_released": self.ledger.seal_key_released(),
            "wallet_balance_micro": self.wallet.balance,
            "wallet_conservation": self.wallet.check_conservation(),
            "ledger_verify": self.ledger.verify(),
            "outstanding_decisions": len(self.queue.outstanding()),
            "exchange_equity_usd": _equity_or_none(self.exchange),
            "live": self.live,
            "evaluation_boundary": EVALUATION_BOUNDARY,
            "standing": self.standing.snapshot(),
            "routers": {
                k: {"universe": s.universe, "epoch": s.epoch, "learner": type(s.learner).__name__}
                for k, s in self.routers.items()
            },
            "stats": dict(vars(self.stats)),
        }
        if not self.termination.final or self.kill_at_end:
            summary["aggregates"] = {
                "action_frequencies": self.ledger.aggregate("action_frequencies"),
                "invocations_by_assembly": self.ledger.aggregate("invocations_by_assembly"),
                "spend_by_capability": self.ledger.aggregate("spend_by_capability"),
                "settlement_latency": self.ledger.aggregate("settlement_latency"),
            }
        return summary


class _KeyedLearner:
    """Adapter so a SnapshotLearner can be driven through Router.route.

    The runtime sets ``current_key`` before routing; ``distribution`` records
    the snapshot under that key. Updates go through ``inner.update_for``.
    """

    def __init__(self, inner: Any) -> None:
        self.inner = inner
        self.id = inner.id
        self.current_key: str | None = None

    def distribution(self, feasible):
        if self.current_key is None:
            return self.inner.distribution(feasible)
        return self.inner.distribution_for(self.current_key, feasible)

    def update(self, feedback) -> None:
        raise TypeError("use inner.update_for(key, feedback)")

    def state(self) -> bytes:
        return self.inner.state()


def _equity_or_none(exchange: Any) -> str | None:
    try:
        return str(exchange.account().equity_usd)
    except RuntimeError:
        return None


def _as_unit(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    v = float(value)
    if v != v or v < 0 or v > 1:
        return None
    return v


def _model_contract(model_id: str, price: TokenPrice, provider: str) -> Contract:
    return Contract(
        id=f"model:{model_id}",
        version=1,
        kind="model",
        description=f"{provider} model {model_id}",
        input_schema={"type": "object"},
        output_schema={"type": "object"},
        price=PriceSpec(
            {"input_token": int(price.input_micro), "output_token": int(price.output_micro)}
        ),
        permissions=frozenset({"model.complete"}),
        resource_bounds=ResourceBounds(),
    )


def _assembly_contract(aid: str, role: str, accepts: tuple[str, ...], max_tokens: int) -> Contract:
    return Contract(
        id=aid,
        version=1,
        kind="assembly",
        description=f"{role} assembly",
        input_schema={"type": "object", "properties": {"kind": {"enum": list(accepts)}}},
        output_schema={"type": "object"},
        price=PriceSpec({}),
        permissions=frozenset({"model.complete", "exchange.order"}),
        resource_bounds=ResourceBounds(max_output_tokens=max_tokens),
    )


def run_world(
    manifest: WorldManifest,
    *,
    events: int = 200,
    seed: int | None = None,
    initial_balance_micro: int | None = None,
    ledger_path: str | None = None,
    horizon_events: int = 10,
    router_gamma: float = 0.1,
    drip: bool = True,
    provider: Any | None = None,
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
    """
    return Runtime(
        manifest,
        events=events,
        seed=seed,
        initial_balance_micro=initial_balance_micro,
        ledger_path=ledger_path,
        drip=drip,
        router_gamma=router_gamma,
        provider=provider,
        exchange=exchange,
        clock_source=clock_source,
        kill_at_end=kill_at_end,
    ).run()
