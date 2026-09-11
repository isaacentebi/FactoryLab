"""The event loop: world → nervous system → cortex → settlement.

Spec section 4.11. Everything here is glue over kernel physics; the loop owns
no money, no scores and no rules of its own. It converts world events into
kernel events, asks each router for a sampled action with a logged
propensity, opens a decision, invokes the chosen assembly through metering,
executes any order the assembly asked for, settles exchange outcomes into
the wallet, and delivers delayed consequence scores back to the handle that
earned them. When the wallet dies, Termination ends the world and the seal
key is released.

Phase 1 runs the ``scripted`` world only. The ``testnet`` world is reached
through ``factorylab probe`` until the runtime grows a live-venue path.
"""

from __future__ import annotations

import json
import random
from collections import deque
from dataclasses import dataclass, field
from decimal import ROUND_HALF_EVEN, Decimal
from typing import Any

from factorylab.cortex.assembly import Assembly, AssemblySpec
from factorylab.cortex.request import Request, Return
from factorylab.kernel.events import Bus, Event, EventKind
from factorylab.kernel.ledger import Ledger
from factorylab.kernel.money import money_to_usd, usd_to_money
from factorylab.kernel.queue import DecisionQueue, LearningReturn, PropensityRecord, SettleStatus
from factorylab.kernel.registry import Contract, PriceSpec, Registry, ResourceBounds
from factorylab.kernel.reserve import NoveltyReserve
from factorylab.kernel.termination import Termination
from factorylab.kernel.timing import TimingRegistry, UpwardBuffer
from factorylab.kernel.wallet import DripSchedule, Wallet
from factorylab.learners.base import BanditFeedback
from factorylab.learners.exp3 import EXP3
from factorylab.learners.router import Router
from factorylab.runtime.worlds import WorldManifest
from factorylab.world.clock import ClockSource, DripSource, merge_sources
from factorylab.world.events import WorldEvent, WorldEventKind
from factorylab.world.exchange import FakeExchange, Order
from factorylab.world.metering import Meter, MeteredModel
from factorylab.world.models import ModelRequest, ModelResponse

NOOP = "NOOP"
FAST = "fast"
CONSEQUENCE = "consequence"
FAST_DEF = "fast-v1"
CONSEQUENCE_DEF = "consequence-v1"


class SimClock:
    """Simulated time. Every kernel component reads the current event's timestamp from here."""

    def __init__(self, now_ns: int = 0) -> None:
        self.now_ns = now_ns

    def __call__(self) -> int:
        return self.now_ns


@dataclass
class ScriptedProvider:
    """Deterministic stand-in for a model in the scripted world.

    On ``Tick`` requests it cycles buy / hold / sell / hold so the exchange
    produces fills, funding and P&L; on anything else it returns a hold.
    Token usage is declared so costs are exact. It exists to close the loop,
    not to be clever.
    """

    name: str = "scripted"
    notional_fraction: str = "0.8"  # of equity times leverage
    leverage: str = "3"
    input_tokens: int = 300
    output_tokens: int = 40
    _calls: int = 0

    def complete(self, req: ModelRequest) -> ModelResponse:
        self._calls += 1
        text = "\n".join(str(m.get("content", "")) for m in req.messages)
        reply: dict[str, Any] = {"action": "hold"}
        if "event Tick" in text:
            try:
                start = text.index("INPUTS")
                end = text.index("OUTCOME SCHEMA")
                inputs = json.loads(text[start + len("INPUTS") : end])
                equity = Decimal(str(inputs["payload"]["account"]["equity_usd"]))
                mid = Decimal(str(inputs["payload"]["mids"]["BTC"]))
                phase = int(inputs["payload"]["index"]) % 4  # buy, hold, sell, hold by tick
            except (ValueError, KeyError, ArithmeticError):
                equity, mid, phase = Decimal(0), Decimal(0), 0
            if mid > 0 and equity > 0 and phase in (1, 3):
                notional = equity * Decimal(self.leverage) * Decimal(self.notional_fraction)
                size = (notional / mid).quantize(Decimal("0.000001"))
                if size > 0:
                    side = "buy" if phase == 1 else "sell"
                    reply = {"action": "order", "coin": "BTC", "side": side, "size": str(size)}
        return ModelResponse(
            req.model_id, json.dumps(reply), self.input_tokens, self.output_tokens, "end_turn"
        )


@dataclass
class _PendingConsequence:
    handle: str
    actor: str
    balance_at_open: int
    opened_at_event: int
    due_at_event: int


@dataclass
class RunStats:
    events: int = 0
    decisions: int = 0
    invocations: int = 0
    noops: int = 0
    orders_placed: int = 0
    orders_rejected: int = 0
    fills: int = 0
    fast_settlements: int = 0
    consequence_settlements: int = 0
    max_settlement_latency_events: int = 0
    timeouts: int = 0
    upward_releases: int = 0
    reserve_windows: int = 0
    exclusions: int = 0
    sample_propensity: dict[str, Any] | None = None
    invocation_status: dict[str, int] = field(default_factory=dict)


def _usd_to_micro(value: str | Decimal) -> int:
    q = Decimal(str(value)).quantize(Decimal("0.000001"), rounding=ROUND_HALF_EVEN)
    return usd_to_money(str(q))


def _to_plain(payload: Any) -> Any:
    if hasattr(payload, "items"):
        return {k: _to_plain(v) for k, v in payload.items()}
    if isinstance(payload, (list, tuple)):
        return [_to_plain(v) for v in payload]
    return payload


def build_contracts(manifest: WorldManifest, registry: Registry) -> None:
    """Register the seed: model tiers, assemblies, exchange and routers. Provenance 'seed'."""
    for tier in manifest.models:
        price = manifest.price_table().price(tier.id)
        registry.register(
            Contract(
                id=f"model:{tier.id}",
                version=1,
                kind="model",
                description=f"{tier.provider} model {tier.id}",
                input_schema={"type": "object"},
                output_schema={"type": "object"},
                price=PriceSpec(
                    {"input_token": price.input_micro, "output_token": price.output_micro}
                ),
                permissions=frozenset({"model.complete"}),
                resource_bounds=ResourceBounds(),
            )
        )
    for seed in manifest.assemblies:
        registry.register(
            Contract(
                id=seed.id,
                version=1,
                kind="assembly",
                description=f"seed assembly on {seed.model_id}",
                input_schema={
                    "type": "object",
                    "properties": {"kind": {"enum": list(seed.accepts)}},
                },
                output_schema={"type": "object", "properties": {"action": {"type": "string"}}},
                price=PriceSpec({}),
                permissions=frozenset({"model.complete", "exchange.order"}),
                resource_bounds=ResourceBounds(max_output_tokens=seed.max_tokens),
            )
        )
    registry.register(
        Contract(
            id="exchange:" + manifest.exchange.kind,
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
) -> dict[str, Any]:
    """Run the scripted world for ``events`` world events and return a summary.

    Guarantees: every invocation is metered before its result is used; every
    decision has a logged propensity that reproduces its sample; consequence
    scores settle to the handle that opened them; the world terminates by
    death if the wallet reaches zero and the summary reports the seal state.
    """
    if manifest.exchange.kind != "fake":
        raise NotImplementedError("phase 1 runs the scripted world; use `probe` for live venues")
    seed = manifest.seed if seed is None else seed
    rng = random.Random(seed)
    clock = SimClock(0)
    stats = RunStats()

    # ---- kernel
    ledger = Ledger(
        ledger_path,
        manifest=json.loads(manifest.canonical_json()),
        clock_ns=clock,
        full_verify_every=1024,
    )
    use_drip = drip and manifest.drip is not None
    drip_schedule = None
    if use_drip and manifest.drip is not None:
        d = manifest.drip
        drip_schedule = DripSchedule(d.amount_micro, d.period_ns, d.start_ns, d.end_ns)
    initial = (
        manifest.initial_balance_micro if initial_balance_micro is None else initial_balance_micro
    )
    wallet = Wallet(initial, ledger, drip_schedule, clock_ns=clock)
    bus = Bus(ledger)
    termination = Termination(ledger=ledger, bus=bus, clock_ns=clock)
    registry = Registry(ledger)
    build_contracts(manifest, registry)
    queue = DecisionQueue(ledger, clock_ns=clock)
    reserve = NoveltyReserve(
        manifest.novelty.share,
        manifest.novelty.window_ns,
        has_history=queue.has_history,
        ledger=ledger,
        clock_ns=clock,
    )
    timing = TimingRegistry()
    timing.register_loop("leaf", [])
    timing.register_loop("governance", ["leaf"])
    buffer = UpwardBuffer(timing, "governance", min_ratio=manifest.timing.min_ratio, seed=seed)

    # ---- world
    shocks: dict[int, dict[str, Decimal]] = {}
    for sh in manifest.exchange.shocks:
        shocks.setdefault(sh.step, {})[sh.coin] = Decimal(sh.multiplier)
    exchange = FakeExchange(
        seed=manifest.exchange.seed,
        coins=manifest.exchange.coins,
        start_cash_usd=money_to_usd(initial),
        shocks=shocks,
    )
    prices = manifest.price_table()
    meter = Meter(wallet)
    provider = ScriptedProvider()
    assemblies: dict[str, Assembly] = {}
    for a in manifest.assemblies:
        spec = AssemblySpec(
            id=a.id,
            version=1,
            model_id=a.model_id,
            max_tokens=a.max_tokens,
            effort=a.effort,
            memory_policy=a.memory_policy,
            accepts=frozenset(a.accepts),
        )
        assemblies[a.id] = Assembly(spec, MeteredModel(provider, prices, meter))

    # ---- nervous system: one EXP3 router per event kind that any assembly accepts
    kinds = sorted({k for a in manifest.assemblies for k in a.accepts})
    routers: dict[str, Router] = {}
    learners: dict[str, EXP3] = {}
    for kind in kinds:
        universe = [a.id for a in manifest.assemblies if kind in a.accepts] + [NOOP]
        learner = EXP3(universe, router_gamma, id=f"router:{kind}")
        learners[learner.id] = learner
        routers[kind] = Router(learner, lambda _k, u=universe: [x for x in u if x != NOOP])
    delivered_seen: dict[str, int] = {lid: 0 for lid in learners}

    def is_feasible(action_id: str) -> tuple[bool, str]:
        asm = assemblies[action_id]
        probe = ModelRequest(
            asm.spec.model_id,
            asm.spec.system_prompt,
            ({"role": "user", "content": ""},),
            asm.spec.max_tokens,
        )
        ceiling = asm.model.ceiling(probe) * 2  # request prompts are longer than the empty probe
        if ceiling > wallet.available:
            return False, f"wallet: ceiling {ceiling} exceeds available {wallet.available}"
        return True, ""

    pending: list[_PendingConsequence] = []
    last_closure_ns = -1
    consequence_scale = max(1, initial // 100)  # 1% of the initial balance = full-scale swing
    tick_ns = manifest.tick_interval_ns
    reserve_window_start: int | None = None
    event_index = 0

    # ---- helpers

    def kernel_event(we: WorldEvent, n: int) -> Event:
        return Event(f"ev-{n}", EventKind(str(we.kind)), we.ts_ns, dict(we.payload), we.source)

    def settle_exchange_effects(evs: list[WorldEvent]) -> None:
        for we in evs:
            if wallet.dead:
                return
            if we.kind is WorldEventKind.FILL:
                stats.fills += 1
                delta = _usd_to_micro(we.payload["realized_usd"]) - _usd_to_micro(
                    we.payload["fee_usd"]
                )
                if delta:
                    wallet.settle(delta, f"fill:{we.payload['order_id']}", "exchange_pnl")
            elif we.kind is WorldEventKind.FUNDING:
                paid = _usd_to_micro(we.payload["paid_usd"])
                if paid:
                    wallet.settle(-paid, f"funding:{we.payload['coin']}:{we.ts_ns}", "funding")
        exchange.sync_cash(money_to_usd(wallet.balance))

    def execute_outputs(ret: Return) -> list[WorldEvent]:
        out = ret.outputs
        if ret.status != "ok" or out.get("action") != "order":
            return []
        try:
            order = Order(
                str(out["coin"]),
                str(out.get("side", "buy")).lower() == "buy",
                Decimal(str(out["size"])),
            )
        except (KeyError, ValueError, ArithmeticError):
            return []
        result = exchange.place(order)
        stats.orders_placed += 1
        if result.status == "rejected":
            stats.orders_rejected += 1
        produced = exchange.drain_events()
        settle_exchange_effects(produced)
        return produced

    def deliver_returns() -> None:
        for lid, learner in learners.items():
            returns = queue.returns_for(lid)
            for lr in returns[delivered_seen[lid] :]:
                decision = queue.get(lr.handle)
                prop = decision.propensity
                idx = prop.action_ids.index(prop.chosen)
                reward = min(1.0, max(0.0, float(lr.score)))
                learner.update(BanditFeedback(prop.chosen, reward, prop.probs[idx]))
            delivered_seen[lid] = len(returns)

    def settle_due_consequences(n: int) -> None:
        nonlocal pending, last_closure_ns
        still: list[_PendingConsequence] = []
        for pc in pending:
            if pc.due_at_event > n:
                still.append(pc)
                continue
            decision = queue.get(pc.handle)
            if decision.status != SettleStatus.PENDING:
                continue
            delta = wallet.balance - pc.balance_at_open
            score = min(1.0, max(0.0, 0.5 + delta / (2.0 * consequence_scale)))
            queue.settle(
                pc.handle,
                channel=CONSEQUENCE,
                score=score,
                status=SettleStatus.SETTLED,
                definition_version=CONSEQUENCE_DEF,
                sampling_ref=None,
            )
            stats.consequence_settlements += 1
            stats.max_settlement_latency_events = max(
                stats.max_settlement_latency_events, n - pc.opened_at_event
            )
            last_closure_ns = max(clock.now_ns, last_closure_ns + 1)
            timing.record_closure("leaf", last_closure_ns)
            buffer.add(
                LearningReturn(
                    pc.handle, CONSEQUENCE, score, CONSEQUENCE_DEF, SettleStatus.SETTLED, None
                ),
                clock.now_ns,
            )
            released = buffer.release()
            if released is not None:
                stats.upward_releases += 1
                ledger.append({"kind": "upward.release", "summary": released, "ts": clock.now_ns})
        pending = still

    def route_event(ev: Event, n: int) -> list[WorldEvent]:
        router = routers.get(str(ev.kind))
        if router is None:
            return []
        sample = router.route(str(ev.kind), is_feasible, rng)
        stats.exclusions += len(sample.excluded)
        prop = PropensityRecord(
            sample.action_ids,
            sample.probs,
            sample.chosen,
            sample.rng_seed,
            sample.learner_id,
            sample.learner_state_hash,
        )
        channel = CONSEQUENCE if ev.kind is EventKind.TICK else FAST
        deadline = (
            clock.now_ns + (horizon_events + 2) * tick_ns
            if channel == CONSEQUENCE
            else clock.now_ns + tick_ns
        )
        balance_before = wallet.balance
        handle = queue.open(
            actor=sample.learner_id,
            event_id=ev.id,
            propensity=prop,
            channel=channel,
            deadline_ns=deadline,
            parent_handle=None,
            cost_ceiling=wallet.available,
        )
        stats.decisions += 1
        if stats.sample_propensity is None and sample.chosen != NOOP:
            stats.sample_propensity = {
                "handle": handle,
                "action_ids": list(sample.action_ids),
                "probs": list(sample.probs),
                "chosen": sample.chosen,
                "rng_seed": sample.rng_seed,
            }
        produced: list[WorldEvent] = []
        if sample.chosen == NOOP:
            stats.noops += 1
            fast_score = 0.0
        else:
            asm = assemblies[sample.chosen]
            payload = _to_plain(ev.payload)
            if ev.kind is EventKind.TICK:
                acct = exchange.account()
                payload["account"] = {
                    "equity_usd": str(acct.equity_usd),
                    "positions": [
                        {"coin": p.coin, "size": str(p.size), "entry_px": str(p.entry_px)}
                        for p in acct.positions
                    ],
                }
                payload["mids"] = {c: str(m) for c, m in exchange.mids().items()}
            req = Request(
                handle=handle,
                description=f"Respond to event {ev.kind} on {ev.source}.",
                inputs={"kind": str(ev.kind), "payload": payload},
                capability_versions={f"model:{asm.spec.model_id}": 1, sample.chosen: 1},
                outcome_schema={"type": "object", "properties": {"action": {"type": "string"}}},
                deadline_ns=deadline,
                cost_ceiling=wallet.available,
                parent_handle=None,
                completion_criterion="a JSON object with an action field",
                scoring_channel=channel,
                resource_liability=handle,
            )
            ret = asm.invoke(req)
            stats.invocations += 1
            stats.invocation_status[ret.status] = stats.invocation_status.get(ret.status, 0) + 1
            ledger.append(
                {
                    "kind": "invocation",
                    "assembly_id": sample.chosen,
                    "handle": handle,
                    "cost": ret.cost,
                    "status": ret.status,
                    "ts": clock.now_ns,
                }
            )
            produced = execute_outputs(ret)
            fast_score = 1.0 if ret.status == "ok" else 0.0
        if channel == FAST:
            queue.settle(
                handle,
                channel=FAST,
                score=fast_score,
                status=SettleStatus.SETTLED,
                definition_version=FAST_DEF,
                sampling_ref=None,
            )
            stats.fast_settlements += 1
        else:
            pending.append(
                _PendingConsequence(
                    handle, sample.learner_id, balance_before, n, n + horizon_events
                )
            )
        return produced

    # ---- the loop
    sources = [ClockSource(start_ns=tick_ns, interval_ns=tick_ns, count=events).events()]
    if use_drip and manifest.drip is not None:
        d = manifest.drip
        sources.append(
            DripSource(
                d.amount_micro,
                d.period_ns,
                max(d.start_ns, tick_ns),
                min(d.end_ns, (events + 1) * tick_ns),
            ).events()
        )
    queue_events: deque[WorldEvent] = deque()
    stream = merge_sources(*sources)

    def next_event() -> WorldEvent | None:
        if queue_events:
            return queue_events.popleft()
        return next(stream, None)

    bus.publish(
        Event("launch", EventKind.LAUNCH, 0, {"manifest_hash": manifest.manifest_hash()}, "kernel")
    )
    while True:
        we = next_event()
        if we is None:
            break
        event_index += 1
        n = event_index
        clock.now_ns = max(clock.now_ns, we.ts_ns)
        ev = kernel_event(we, n)
        bus.publish(ev)
        stats.events += 1

        wallet.drip(clock.now_ns)
        if (
            reserve_window_start is None
            or clock.now_ns >= reserve_window_start + manifest.novelty.window_ns
        ):
            reserve.open_window(clock.now_ns, wallet.balance)
            reserve_window_start = clock.now_ns
            stats.reserve_windows += 1

        if we.kind is WorldEventKind.TICK:
            produced = exchange.advance(clock.now_ns)
            settle_exchange_effects(produced)
            queue_events.extend(produced)

        reason = termination.check(wallet, clock.now_ns)
        if reason is not None:
            termination.kill(reason)
            break

        produced = route_event(ev, n)
        queue_events.extend(produced)

        reason = termination.check(wallet, clock.now_ns)
        if reason is not None:
            termination.kill(reason)
            break

        settle_due_consequences(n)
        stats.timeouts += len(queue.expire(clock.now_ns))
        deliver_returns()

    # ---- summary
    summary: dict[str, Any] = {
        "world": manifest.name,
        "manifest_hash": manifest.manifest_hash(),
        "seed": seed,
        "drip": use_drip,
        "terminated": termination.final,
        "termination_reason": termination.reason,
        "seal_key_released": ledger.seal_key_released(),
        "wallet_balance_micro": wallet.balance,
        "wallet_conservation": wallet.check_conservation(),
        "ledger_verify": ledger.verify(),
        "outstanding_decisions": len(queue.outstanding()),
        "exchange_equity_usd": str(exchange.account().equity_usd),
        "stats": {k: v for k, v in vars(stats).items()},
    }
    if not termination.final:
        summary["aggregates"] = {
            "action_frequencies": ledger.aggregate("action_frequencies"),
            "invocations_by_assembly": ledger.aggregate("invocations_by_assembly"),
            "spend_by_capability": ledger.aggregate("spend_by_capability"),
            "settlement_latency": ledger.aggregate("settlement_latency"),
        }
    return summary
