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

Prices. At each reserve-window boundary the runtime measures the window
that closed using the observation catalogue
and hands each priced metric card one observation. The price controller
(spec v0.6 section 8.1) revises a bounded λ per card; verdict and conformity
scores settle net of Σ λ·violation, clipped to [0, 1]. The consequence and
exposure channels, the novelty reserve and router exploration are outside
its authority.

Registration. Any return may carry proposals. Well-formed ones are paid from
the novelty reserve, registered with the proposing decision as provenance,
and announced. Adding an assembly opens a new comparator epoch for every
router whose menu grew.
"""

from __future__ import annotations

import hashlib
import inspect
import json
import random
from collections import deque
from dataclasses import dataclass, field, replace
from decimal import ROUND_HALF_EVEN, Decimal
from itertools import islice
from statistics import median
from typing import Any

from factorylab.charter.charter import Charter, seed_charter
from factorylab.charter.controller import CardRegion, PriceController
from factorylab.cortex.assembly import Assembly, AssemblySpec
from factorylab.cortex.registration import (
    AssemblyProposal,
    ModelProposal,
    ToolProposal,
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
from factorylab.runtime.cadence import GovernanceCadence
from factorylab.runtime.cards import parses, region_for
from factorylab.runtime.cascade import CascadeGate, event_tier, release_threshold
from factorylab.runtime.live import LiveClock, LiveVenue, Reconciler, build_provider
from factorylab.runtime.observations import CATALOGUE, catalogue, observation_for
from factorylab.runtime.resume import (
    JournalProxy,
    RecoveryJournal,
    decode,
    encode,
    runtime_state,
)
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
from factorylab.settlement.consequence import FillCursor, ReturnConsequences
from factorylab.world.clock import ClockIterator, ClockSource, DripSource, merge_sources
from factorylab.world.events import WorldEvent, WorldEventKind
from factorylab.world.exchange import FakeExchange, HyperliquidExchange, Order
from factorylab.world.market import MultiProvider, X402MeteredModel, X402Provider
from factorylab.world.metering import Meter, Metered, MeteredModel
from factorylab.world.models import FakeModel, ModelRequest, ModelResponse, TokenPrice
from factorylab.world.x402 import X402Error

try:  # phase 3 packages; hard imports once every workstream is merged
    from factorylab.world.venue_tools import VenueTools
except ImportError:  # pragma: no cover
    VenueTools = None  # type: ignore[assignment]
from factorylab.cortex.tools import PopulationTool, ToolRunner, as_spec

try:
    from factorylab.charter.amendment import Amendment
    from factorylab.charter.book import CharterBook
except ImportError:  # pragma: no cover
    Amendment = CharterBook = None  # type: ignore[assignment]

NOOP = "NOOP"
CH_FAST, CH_VERDICT, CH_CONFORMITY, CH_CONSEQUENCE = "fast", "verdict", "conformity", "consequence"
CH_EXPOSURE, DEF_EXPOSURE = "exposure", "exposure-v1"
DEF_FAST, DEF_VERDICT, DEF_CONFORMITY = "fast-v1", "verdict-v1", "conformity-v1"
PRODUCER_KINDS = frozenset({"Tick", "MarketMid", "Funding", "Fill", "OrderRejected"})
# metric cards a judged return answers for, by the judged role (spec v0.6 section 8.1)
PRODUCER_CARDS = frozenset({"cost_per_return", "well_formed_rate", "turnover"})
EVALUATOR_CARDS = frozenset({"forecast_skill", "well_formed_rate"})
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
    tool_at_calls: tuple[int, ...] = (30, 50, 70, 90, 110, 130, 150)
    treasury_at_call: int = 120
    router_add_at_call: int = 100
    _producer_calls: int = 0

    def complete(self, req: ModelRequest) -> ModelResponse:
        text = "\n".join(str(m.get("content", "")) for m in req.messages)
        inputs = _inputs_from_prompt(text)
        desc = _description_from_prompt(text)
        if desc.startswith("Evaluate"):
            reply = self._evaluate(req, inputs)
        elif desc.startswith("Assess"):
            reply = self._meta(inputs)
        elif desc.startswith("Vote"):
            reply = {"vote": True, "reason": "scripted yes"}
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
        if "tool_results" in inputs:
            reply["seen_tool_results"] = len(inputs["tool_results"])
            return reply
        if n in self.tool_at_calls:
            # first the venue, then the population tool once it exists, then both
            calls = [{"tool": "venue.candles", "args": {"coin": "BTC", "interval": "1m", "n": 5}}]
            if n >= 50:
                calls.append({"tool": "spread-check", "args": {"mid": 100.0, "bps": 3}})
            reply["tool_calls"] = calls
        if n == self.treasury_at_call:
            reply["tool_calls"] = [
                {
                    "tool": "treasury.transfer",
                    "args": {"direction": "to_venue", "usd": 5, "reason": "scripted"},
                }
            ]
        if n == self.router_add_at_call:
            reply["register"] = [
                {
                    "kind": "router",
                    "event_kind": "Tick",
                    "learner": "exp3",
                    "gamma": 0.3,
                    "add": True,
                }
            ]
        if n == 45:
            reply["register"] = [
                {
                    "kind": "tool",
                    "id": "spread-check",
                    "description": "Return the half-spread in price units for a mid and bps.",
                    "args_schema": {
                        "type": "object",
                        "properties": {"mid": {"type": "number"}, "bps": {"type": "integer"}},
                        "required": ["mid", "bps"],
                    },
                    "code": (
                        "import json,sys\na=json.load(sys.stdin)\n"
                        "print(json.dumps({'half_spread': a['mid']*a['bps']/20000}))"
                    ),
                    "timeout_s": 2,
                }
            ]
        if n == 55:
            reply["register"] = [
                {
                    "kind": "amendment",
                    "id": "turnover-card",
                    "add": [
                        {
                            "id": "turnover",
                            "norm": "care with scarce resources",
                            "description": "Notional traded per window relative to equity.",
                            "units": "ratio",
                            "window": "rolling 100 events",
                            "acceptable_region": "below 5",
                            "observation": "turnover",
                            "lambda": 0.6,
                        }
                    ],
                    "replace": [],
                    "remove": [],
                    "predicted_effect": "Evaluators will mark down churn; fewer round trips.",
                }
            ]
        if n == 65:
            reply["register"] = [
                {
                    "kind": "assembly",
                    "id": "web-observer",
                    "role": "producer",
                    "model_id": "fake-haiku:online",
                    "system_prompt": "Search for context on funding moves; reply with JSON.",
                    "accepts": ["MarketMid"],
                    "max_tokens": 128,
                }
            ]
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
        if action in ("noop", "hold"):
            verdict = 0.9 if req.model_id == "fake-haiku" else 0.1
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

    def state(self) -> dict:
        """Retain the exact learner, public universe order and comparator epoch."""
        return {
            "kind": self.kind,
            "universe": list(self.universe),
            "router": self.router.state(),
            "epoch": self.epoch,
        }

    @classmethod
    def restore(cls, state: dict) -> RouterState:
        """Restore the router and its learner as the same object against the saved menu."""
        from factorylab.learners.base import restore_learner

        saved = state["router"]["learner"]
        learner = (
            _KeyedLearner.restore(saved)
            if saved["algorithm"] == "KeyedLearner"
            else restore_learner(saved)
        )
        universe = list(state["universe"])
        router = Router(learner, lambda _k: [a for a in universe if a != NOOP])
        return cls(state["kind"], universe, learner, router, state["epoch"])


def _duration_str(ns: int) -> str:
    """Whole seconds/minutes/hours where exact, else seconds with a decimal."""
    if ns % 3_600_000_000_000 == 0:
        return f"{ns // 3_600_000_000_000}h"
    if ns % 60_000_000_000 == 0:
        return f"{ns // 60_000_000_000}m"
    if ns % 1_000_000_000 == 0:
        return f"{ns // 1_000_000_000}s"
    return f"{ns / 1_000_000_000:g}s"


class _ObservedX402Model(X402MeteredModel):
    """Paid completions enter observations after metering, including during journal replay."""

    def complete(self, req: ModelRequest, *, handle: str) -> Metered[ModelResponse]:
        """A positive committed request yields exactly one ledgered purchase observation."""
        result = super().complete(req, handle=handle)
        if result.cost > 0:
            self.record({"kind": "observation.market_purchase", "handle": handle})
        return result


@dataclass
class MeasureWindow:
    """Raw material for one reserve window's metric-card observations."""

    index: int
    equity_start_micro: int
    costs: list[int] = field(
        default_factory=list
    )  # wallet cost of each well-formed producer return
    invocations: int = 0
    ok: int = 0
    notional_micro: int = 0  # filled size × price, summed
    forecast_skills: list[float] = field(default_factory=list)
    producer_returns: int = 0
    noop_returns: int = 0
    revision_returns: int = 0
    revision_handles: set[str] = field(default_factory=set)
    registrations: int = 0
    registration_rejections: int = 0
    amendments_proposed: int = 0
    amendments_activated: int = 0
    verdicts: dict[str, dict[str, list[float]]] = field(default_factory=dict)
    consequences_settled: int = 0
    consequences_paid_off: int = 0
    fills: int = 0
    realized_pnl_micro: int = 0
    max_position_notional_micro: int | None = None
    exposures_settled: int = 0
    exposures_won: int = 0
    meta_verdicts: list[float] = field(default_factory=list)
    outcomes: int = 0
    censored: int = 0
    tool_calls: int = 0
    market_purchases: int = 0


@dataclass
class PendingJudgement:
    handle: str  # decision awaiting a verdict (producer) or conformity (evaluator/meta)
    channel: str
    opened_at_event: int
    tier: int = 1


@dataclass
class RunStats:
    resumes: int = 0
    events: int = 0
    decisions: int = 0
    invocations: int = 0
    noops: int = 0
    orders_placed: int = 0
    orders_rejected: int = 0
    fills: int = 0
    producer_returns: int = 0
    verdicts: int = 0
    meta_verdicts: dict[int, int] = field(default_factory=dict)
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
    tool_calls: int = 0
    tool_call_failures: int = 0
    population_tools_registered: int = 0
    amendments_proposed: int = 0
    amendments_passed: int = 0
    amendments_activated: int = 0
    clock_changes: int = 0
    votes_cast: int = 0
    transfer_intents: int = 0
    exposures_settled: int = 0
    exposures_won: int = 0
    price_updates: int = 0
    price_skipped: int = 0
    penalized_settlements: int = 0
    max_settlement_latency_events: int = 0
    last_window_values: dict[str, float] = field(default_factory=dict)
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
        market: X402Provider | None = None,
        exchange: Any | None = None,
        clock_source: Any | None = None,
        reconcile_every: int = 10,
        kill_at_end: bool = False,
        _journal: RecoveryJournal | None = None,
    ) -> None:
        self.m = manifest
        self.kill_at_end = kill_at_end
        self.live = manifest.exchange.kind != "fake"
        self.clock_source = clock_source
        self.tick_clock = (
            LiveClock(manifest.tick_interval_ns, events)
            if self.live
            else ClockSource(manifest.tick_interval_ns, manifest.tick_interval_ns, events)
        )
        if clock_source is not None and hasattr(clock_source, "set_interval"):
            self.tick_clock = (
                clock_source._clock if isinstance(clock_source, ClockIterator) else clock_source
            )
        self.reconciler = Reconciler(every=reconcile_every)
        self.events_budget = events
        self.seed = manifest.seed if seed is None else seed
        self.rng = random.Random(self.seed)
        self.cascade: dict[int, CascadeGate] = {}
        self.cascade_windows: dict[str, list[str]] = {}  # representative -> other handles
        self.clock = SimClock(0) if _journal is None else _journal.clock
        self.stats = RunStats()
        self.ev = manifest.evaluation
        self.charter: Charter = seed_charter()

        # kernel
        self.ledger = _journal or RecoveryJournal(
            Ledger(
                ledger_path,
                manifest=json.loads(manifest.canonical_json()),
                clock_ns=self.clock,
                full_verify_every=1024,
                key_path=(ledger_path + ".key") if ledger_path else None,
            ),
            self.clock,
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
        self.cadence = GovernanceCadence(
            self.ledger,
            sample=manifest.timing.cadence_sample,
            min_ratio=manifest.timing.min_ratio,
            backstop=self.ev.consequence_backstop_events,
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
        self.consequences = ReturnConsequences(self.ledger, self.ev.consequence_backstop_events)
        self.consequence_fills = FillCursor(self.ledger)

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
        self.exchange = JournalProxy(
            self.exchange,
            self.ledger,
            "exchange",
            deterministic=isinstance(self.exchange, FakeExchange) and not self.live,
        )
        # Fills before launch belong to nobody; funding uses the same launch boundary.
        self.venue = (
            LiveVenue(self.exchange, last_fill_ns=self.clock.now_ns, ledger=self.ledger)
            if self.live
            else None
        )
        self.prices = manifest.price_table()
        self.meter = Meter(self.wallet)
        if provider is None:
            provider = build_provider(manifest)
        self.provider = provider if provider is not None else ScriptedProvider()
        from factorylab.world.treasury import FakeTreasury, Treasury, UnconfiguredRail

        if not self.live:
            self.treasury = FakeTreasury(
                self.ledger, self.wallet, fee_micro=manifest.treasury.fake_fee_micro
            )
        else:
            if manifest.treasury.reserve_address is not None:
                from factorylab.world.treasury_rails import LiveRail

                rail = LiveRail(self.exchange, manifest.treasury)
            else:
                rail = UnconfiguredRail(self.exchange.target)
            self.treasury = Treasury(
                self.ledger,
                self.wallet,
                rail,
                provider=self.provider,
                fee_ceiling_micro=manifest.treasury.max_transfer_fee_micro,
            )
        self.wallet.bind_pots(self.treasury.pots)
        self.treasury.rail = JournalProxy(
            self.treasury.rail, self.ledger, "treasury.rail", deterministic=not self.live
        )
        self.market = (
            market
            if market is not None
            else (
                self.provider.x402
                if isinstance(self.provider, MultiProvider)
                else self.provider
                if isinstance(self.provider, X402Provider)
                else X402Provider(discovery_url=manifest.treasury.discovery_url)
            )
        )
        self.provider = JournalProxy(
            self.provider,
            self.ledger,
            "provider",
            deterministic=isinstance(self.provider, (ScriptedProvider, FakeModel)),
        )
        self.market = JournalProxy(self.market, self.ledger, "market")
        self.sellers: dict[str, dict] = {}
        self.catalogue: dict[str, TokenPrice] | None = None
        if not self.ledger.bootstrap and hasattr(self.provider, "catalogue"):
            try:
                self.catalogue = {e.id: e.price() for e in self.provider.catalogue()}
            except Exception:  # catalogue unavailable: model proposals will be rejected
                self.catalogue = None

        if not self.ledger.bootstrap:
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
        self.routers: dict[str, list[RouterState]] = {}
        for kind in self._routable_kinds():
            self._build_router(kind, "exp3", router_gamma)
        self.pending_exposure: dict[str, int] = {}  # antagonist decision handle -> opened event
        self.delivered_seen: dict[str, int] = {}
        self.snapshot_keys: dict[str, str] = {}  # decision handle -> snapshot key

        # world memory (public facts) and assembly memory (private to each assembly)
        self.recent_mids: dict[str, deque[dict[str, Any]]] = {}
        self.realized_to_date = 0
        self.fees_to_date = 0
        self.funding_to_date = 0
        self.memory: dict[str, deque[dict[str, Any]]] = {}
        self.handle_to_assembly: dict[str, str] = {}
        self.tool_specs: dict[str, dict[str, Any]] = {}  # tool id -> spec dict (world block)
        self.population_tools: dict[str, Any] = {}
        self.tool_owner: dict[str, str] = {}  # population tool id -> proposing assembly id
        self.venue_tools = None
        if VenueTools is not None:
            self.venue_tools = VenueTools(
                self.exchange,
                coins=manifest.exchange.coins,
                max_leverage=manifest.tools.max_leverage,
            )
            for spec in self.venue_tools.contracts():
                self.tool_specs[spec.id] = {
                    "id": spec.id,
                    "description": spec.description,
                    "args_schema": _to_plain(spec.args_schema),
                    "price_micro_per_call": spec.price_micro_per_call,
                    "kind": spec.kind,
                }
        self.tool_specs["treasury.transfer"] = {
            "id": "treasury.transfer",
            "description": "Submit a transfer between venue and reserve. Principal stays held "
            "until receipt-confirmed arrival. The result carries references or a refusal reason.",
            "args_schema": {
                "type": "object",
                "properties": {
                    "direction": {"enum": ["to_reserve", "to_venue"]},
                    "usd": {"type": ["string", "integer"], "description": "Exact positive USD"},
                    "reason": {"type": "string"},
                },
                "required": ["direction", "usd"],
            },
            "price_micro_per_call": 0,
            "kind": "treasury",
        }
        self.tool_specs["catalogue.search"] = {
            "id": "catalogue.search",
            "description": "Search the model catalogues (OpenRouter, Venice, registered sellers) "
            "by substring; returns ids with prices per million tokens and context length.",
            "args_schema": {
                "type": "object",
                "properties": {
                    "substring": {"type": "string"},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 50, "default": 20},
                },
                "required": ["substring"],
                "additionalProperties": False,
            },
            "price_micro_per_call": manifest.tools.population_tool_micro_per_call,
            "kind": "catalogue",
        }
        self.tool_specs["market.discover"] = {
            "id": "market.discover",
            "description": "Discover compute sellers with their resource URLs and listed prices.",
            "args_schema": {
                "type": "object",
                "properties": {
                    "url_substring": {"type": "string"},
                    "query": {"type": "string"},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 100, "default": 20},
                },
                "additionalProperties": False,
            },
            "price_micro_per_call": manifest.tools.population_tool_micro_per_call,
            "kind": "market",
        }
        self.tool_runner = JournalProxy(ToolRunner(), self.ledger, "sandbox")
        self.charter_book = CharterBook(self.ledger, self.charter)
        self.pending_votes: list[Any] = []  # committees awaiting tally

        # prices (spec v0.6 section 8.1): regions are parsed here, the controller only prices
        pr = manifest.prices
        self.controller = PriceController(
            self.ledger,
            eta=pr.eta,
            kappa=pr.kappa,
            decay=pr.decay,
            lambda_max=pr.lambda_max,
            min_window_events=pr.min_window_events,
            timing=self.timing,
        )
        self.regions: dict[str, CardRegion] = {}  # cards of the current edition with a region
        self.priced: set[str] = set()  # card ids currently registered with the controller
        self.rolling: dict[str, float] = {}
        self.unparsed_logged: set[tuple[str, int]] = set()
        self.window = MeasureWindow(0, self.wallet.balance)

        # loop state
        self.pending: dict[str, PendingJudgement] = {}
        self.balance_at: list[int] = [self.wallet.balance]  # index = event number
        self.events_log: list[dict[str, Any]] = [{"kind": "Launch", "payload": {}}]
        self.last_closure_ns = -1
        self.reserve_window_start: int | None = None
        self.internal: deque[Event] = deque()
        self.n = 0
        self.emitted = 0
        self.insolvency_count = 0
        self.registration_feedback: deque[dict[str, Any]] = deque(maxlen=8)
        self._compute_routed = False
        self._compute_unaffordable = False
        self.world_consumed = 0
        self.ticks_consumed = 0
        self.drips_consumed = 0
        self.started = False

    # ---- setup helpers

    def _register_seed_contracts(self) -> None:
        for tier in self.m.models:
            price = self.prices.price(tier.id)
            if tier.id.startswith("x402:"):
                price, seller = self.market.registration_price(tier.id)
                self.registry.register(_model_contract(tier.id, price, "x402"))
                self._record_seller(tier.id, price, seller)
                continue
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
        model = MeteredModel(self.provider, self.prices, self.meter)
        if spec.model_id.startswith("x402:"):
            model = _ObservedX402Model(
                self.market,
                self.prices,
                self.meter,
                record=self._record_market,
                on_unaffordable=self._compute_failure,
            )
        asm = Assembly(spec, model)
        self.assemblies[spec.id] = asm
        return asm

    def _routable_kinds(self) -> list[str]:
        kinds = {k for a in self.assemblies.values() for k in a.spec.accepts}
        return sorted(kinds)

    def _universe_for(self, kind: str, ev: Event | None = None) -> list[str]:
        judged = None
        if ev is not None and kind in ("Verdict", "MetaVerdict"):
            handle = ev.payload["by"] if kind == "MetaVerdict" else ev.payload["evaluator_handle"]
            judged = self.handle_to_assembly.get(handle)
        ids = sorted(
            a.spec.id
            for a in self.assemblies.values()
            if kind in a.spec.accepts and a.spec.id != judged
        )
        return ids + [NOOP]

    def _all_router_states(self) -> list[RouterState]:
        return [st for states in self.routers.values() for st in states]

    def _make_learner(
        self, kind: str, learner_kind: str, gamma: float, universe: list[str], lid: str
    ):
        if learner_kind == "blum_mansour":
            from factorylab.learners.blum_mansour import BlumMansour
            from factorylab.learners.delayed import SnapshotLearner

            inner = BlumMansour(lambda acts: EXP3(acts, gamma), universe, id=lid)
            return _KeyedLearner(SnapshotLearner(inner, id=lid))
        return EXP3(universe, gamma, id=lid)

    def _build_router(
        self, kind: str, learner_kind: str, gamma: float, *, replace: bool = True
    ) -> RouterState:
        """Create a router for ``kind``. ``replace`` swaps the whole set; else one is added."""
        universe = self._universe_for(kind)
        existing = self.routers.get(kind, [])
        index = 0 if replace else len(existing)
        if not replace and len(existing) >= self.m.tools.max_routers_per_kind:
            raise ValueError("router cap reached for this event kind")
        lid = f"router:{kind}" if index == 0 else f"router:{kind}#{index}"
        learner = self._make_learner(kind, learner_kind, gamma, universe, lid)
        router = Router(learner, lambda _k, u=universe: [x for x in u if x != NOOP])
        state = RouterState(kind, universe, learner, router)
        if replace:
            self.routers[kind] = [state]
        else:
            self.routers.setdefault(kind, []).append(state)
        if not hasattr(self, "delivered_seen"):
            self.delivered_seen = {}
        self.delivered_seen.setdefault(learner.id, 0)
        return state

    # ---- public schematics (spec v0.4 §1.6: schematics, contracts, prices and charter are public)

    PROPOSAL_SHAPES: dict[str, Any] = {
        "model": {
            "kind": "model",
            "openrouter_id": "vendor/model-id from the catalogue, optionally @none|@low|@high|@max "
            "for reasoning; venice:<id> for Venice; x402:<seller_url>#<model> for a seller "
            "priced per request",
        },
        "assembly": {
            "kind": "assembly",
            "id": "slug-2-to-48-chars",
            "role": "producer | evaluator | meta | antagonist",
            "model_id": "a registered model id",
            "system_prompt": "text, at most 4000 chars",
            "accepts": ["event kinds this assembly is woken for"],
            "max_tokens": 512,
            "effort": "low | medium | high",
        },
        "router": {
            "kind": "router",
            "event_kind": "an event kind",
            "learner": "exp3 | blum_mansour",
            "gamma": 0.1,
            "add": "false replaces the kind's routers; true adds another (several wake at once)",
        },
        "tool": {
            "kind": "tool",
            "id": "slug",
            "description": "what it computes",
            "args_schema": {"type": "object", "properties": {"x": {"type": "number"}}},
            "code": "python: read a JSON object from stdin, print a JSON object",
            "timeout_s": 2,
        },
        "amendment": {
            "kind": "amendment",
            "id": "slug",
            "add": [
                {
                    "id": "card-id",
                    "norm": "one of the charter norms",
                    "description": "what is measured",
                    "units": "…",
                    "window": "…",
                    "acceptable_region": "…",
                    "observation": "one of world.observations ids",
                    "lambda": "optional number in [0, prices.lambda_max], for add or replace",
                }
            ],
            "replace": [],
            "remove": ["card-id"],
            "predicted_effect": "what you expect to change and why",
            "tick_interval": "optional duration string, e.g. 30s",
        },
    }
    A_RETURN_MAY_INCLUDE: dict[str, str] = {
        "action": (
            '"noop" | "hold" | "order"; an "order" return also carries "coin" (one of the '
            'world\'s coins), "side" ("buy" | "sell") and "size" (base units as a decimal '
            'string, e.g. "0.005"), and is placed at market on return; limit, reduce-only, '
            "close, leverage and cancel are tool_calls on the venue.* tools"
        ),
        "order_example": '{"action": "order", "coin": "ETH", "side": "buy", "size": "0.004"}',
        "register": "a list of proposals, each shaped like one of proposal_shapes",
        "tool_calls": (
            'a list of {"tool": id, "args": {...}} (max 4); results come back in a second call'
        ),
    }

    def _world_block(self) -> dict[str, Any]:
        """Facts about the world any assembly may see. No rules, no goals, no private state."""
        try:
            acct = self.exchange.account()
            account = {
                "equity_usd": str(acct.equity_usd),
                "cash_usd": str(acct.cash_usd),
                "positions": [
                    {"coin": p.coin, "size": str(p.size), "entry_px": str(p.entry_px)}
                    for p in acct.positions
                ],
                "margin_used_usd": str(acct.margin_used_usd),
            }
        except RuntimeError:
            account = {"equity_usd": str(money_to_usd(self.wallet.balance)), "positions": []}
        account["realized_pnl_usd_to_date"] = str(money_to_usd(self.realized_to_date))
        account["fees_usd_to_date"] = str(money_to_usd(self.fees_to_date))
        account["funding_usd_to_date"] = str(money_to_usd(self.funding_to_date))
        return {
            "wallet_balance_usd": str(money_to_usd(self.wallet.balance)),
            "pots": self.wallet.pots(),
            "charter_edition": self.charter.edition,
            "recent_mids": {c: list(v) for c, v in self.recent_mids.items()},
            "account": account,
            "tools": list(self.tool_specs.values()),
            "observations": catalogue(),
            "novelty_reserve_remaining_usd": str(money_to_usd(self.reserve.remaining())),
            "models": [
                {
                    "id": mid,
                    "usd_per_million_input_tokens": _price_str(p.input_micro),
                    "usd_per_million_output_tokens": _price_str(p.output_micro),
                    "per_request_micro": p.per_request_micro,
                }
                for mid, p in self.prices.prices.items()
            ],
            "sellers": [{"model_id": mid, **seller} for mid, seller in self.sellers.items()],
            "assemblies": [
                {
                    "id": a.spec.id,
                    "role": a.spec.role,
                    "model_id": a.spec.model_id,
                    "accepts": sorted(a.spec.accepts),
                }
                for a in self.assemblies.values()
            ],
            "routers": [
                {"event_kind": st.kind, "learner": type(st.learner).__name__, "menu": st.universe}
                for st in self._all_router_states()
            ],
            "clock": {
                "tick_interval": _duration_str(self.tick_clock.interval_ns),
                "min_tick": _duration_str(self.m.clock.min_tick_ns),
                "max_tick": _duration_str(self.m.max_tick_ns),
            },
            "governance": self.cadence.world_block(self.tick_clock.interval_ns),
            "registration_feedback": list(self.registration_feedback),
            "scoring": self._scoring_block(),
            "prices": {"lambda_max": self.m.prices.lambda_max},
            "amendment_feedback": getattr(self, "amendment_feedback", None),
            "card_prices": [
                {
                    "card_id": cid,
                    "lambda": self.controller.price(cid),
                    "region": (
                        {"kind": r.kind, "lo": r.lo, "hi": r.hi, "scale": r.scale}
                        if (r := self.regions.get(cid)) is not None
                        else None
                    ),
                }
                for cid in sorted(self.priced)
            ],
            "event_kinds": sorted(PRODUCER_KINDS | {"ProducerReturn", "Verdict", "MetaVerdict"}),
            "meta_input": (
                "A meta judges the released representative verdict. Its window describes "
                "the arrivals it represents: count, mean score, min, max, and decision handles."
            ),
            "a_return_may_include": self.A_RETURN_MAY_INCLUDE,
            "proposal_shapes": self.PROPOSAL_SHAPES,
        }

    @staticmethod
    def _register_schema() -> dict[str, Any]:
        return {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"kind": {"enum": ["model", "assembly", "router"]}},
                "required": ["kind"],
            },
        }

    def _forecast_schema(self) -> dict[str, Any]:
        return {
            "type": "array",
            "maxItems": self.ev.max_forecasts_per_verdict,
            "items": {
                "type": "object",
                "properties": {
                    "predicate": {"enum": [p.id for p in SEED_VOCABULARY]},
                    "params": {
                        "type": "object",
                        "properties": {"horizon_events": {"type": "integer", "minimum": 1}},
                    },
                    "q": {"type": "number", "minimum": 0, "maximum": 1},
                },
                "required": ["predicate", "params", "q"],
            },
        }

    # ---- feasibility and mixing

    def _is_feasible(self, action_id: str) -> tuple[bool, str]:
        asm = self.assemblies[action_id]
        probe = ModelRequest(
            asm.spec.model_id,
            asm.spec.system_prompt,
            ({"role": "user", "content": ""},),
            asm.spec.max_tokens,
        )
        is_market = asm.spec.model_id.startswith("x402:")
        ceiling = asm.model.ceiling(probe) * (1 if is_market else 2)
        if ceiling > self.wallet.available:
            return False, f"compute: ceiling {ceiling} exceeds wallet {self.wallet.available}"
        try:
            if is_market:
                return self.market.affordable(asm.spec.model_id, ceiling)
            if hasattr(self.provider, "affordable"):
                return self.provider.affordable(asm.spec.model_id, ceiling)
            if hasattr(self.provider, "balance_micro"):
                balance = self.provider.balance_micro()
                if balance is not None and balance < ceiling:
                    return False, f"compute: provider balance {balance} below ceiling {ceiling}"
        except Exception:
            return False, "provider: balance unavailable"
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
            self.bus.publish(
                Event(
                    "launch",
                    EventKind.LAUNCH,
                    0,
                    {"manifest_hash": self.m.manifest_hash()},
                    "kernel",
                )
            )
            self.started = True
            self._snapshot("launch")
        while True:
            ev = self._next_event(stream)
            if ev is None or not self._process_event(ev):
                break
        if self.kill_at_end and not self.termination.final:
            # a budgeted rehearsal world ends by explicit kill so its diary becomes readable
            self.termination.kill("explicit_kill:budget")
        return self._summary()

    def _process_event(self, ev: Event) -> bool:
        """Normal execution and recovery use identical transitions after a durable input item."""
        previous_window = self.reserve_window_start
        self.n += 1
        self.clock.now_ns = max(self.clock.now_ns, ev.ts_ns)
        self.bus.publish(ev)
        self.stats.events += 1
        self.events_log.append({"kind": str(ev.kind), "payload": _to_plain(ev.payload)})
        if ev.kind is EventKind.MARKET_MID:
            coin = str(ev.payload.get("coin"))
            dq = self.recent_mids.setdefault(coin, deque(maxlen=20))
            dq.append({"t_s": ev.ts_ns // 1_000_000_000, "mid": str(ev.payload.get("mid"))})

        self.wallet.drip(self.clock.now_ns)
        self._manage_reserve_window()
        self._observe_delivered_event(ev)
        if ev.kind is EventKind.TICK:
            self.treasury.tick(self.clock.now_ns)
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

    def _snapshot(self, boundary: str) -> None:
        """Persist a complete continuation at launch and after each boundary event finishes."""
        self.ledger.append(
            {"kind": "snapshot", "boundary": boundary, "n": self.n, "state": runtime_state(self)}
        )

    def _resume_at(self, now_ns: int) -> None:
        """Reconcile and ledger outage timeouts before admitting another world event."""
        now_ns = max(now_ns, self.clock.now_ns)
        self.ledger.append({"kind": "resume.begin", "now_ns": now_ns, "n": self.n})
        self.clock.now_ns = now_ns
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

    def _emit(self, kind: EventKind, payload: dict[str, Any], source: str = "runtime") -> None:
        self.emitted += 1
        self.internal.append(
            Event(f"{kind.value.lower()}-{self.emitted}", kind, self.clock.now_ns, payload, source)
        )

    def _check_termination(self) -> bool:
        reason = self.termination.check(self.wallet, self.clock.now_ns)
        if reason is None and self.insolvency_count >= self.m.treasury.insolvency_events:
            reason = "insolvency:compute"
        if reason is None:
            return False
        self._settle_due_forecasts()
        self.termination.kill(reason)
        return True

    def _catalogue_search(self, substring: str, limit: int) -> list[dict[str, Any]]:
        """Case-insensitive substring over every catalogue the provider exposes plus registered
        prices; a world fact, never a recommendation. Unavailable catalogues yield nothing."""
        needle = substring.lower()
        out: list[dict[str, Any]] = []
        seen: set[str] = set()
        entries: list[Any] = []
        if hasattr(self.provider, "catalogue"):
            try:
                entries = list(self.provider.catalogue())
            except Exception:  # catalogue unavailable: the search is simply empty
                entries = []
        for e in entries:
            if needle in e.id.lower() or needle in (e.name or "").lower():
                seen.add(e.id)
                p = e.price()
                out.append(
                    {
                        "id": e.id,
                        "name": e.name,
                        "usd_per_million_input_tokens": _price_str(p.input_micro),
                        "usd_per_million_output_tokens": _price_str(p.output_micro),
                        "context_length": e.context_length,
                    }
                )
        for mid, p in self.prices.prices.items():
            if mid not in seen and needle in mid.lower():
                out.append(
                    {
                        "id": mid,
                        "name": mid,
                        "usd_per_million_input_tokens": _price_str(p.input_micro),
                        "usd_per_million_output_tokens": _price_str(p.output_micro),
                        "per_request_micro": p.per_request_micro,
                    }
                )
        out.sort(key=lambda m: m["id"])
        return out[: max(1, min(limit, 50))]

    def _record_market(self, item: dict) -> None:
        """Payment and pricing evidence is ledgered before dependent runtime state changes."""
        self.ledger.append({**item, "ts": self.clock.now_ns})
        if item["kind"] == "observation.market_purchase":
            self.window.market_purchases += 1

    def _record_seller(self, model_id: str, price: TokenPrice, seller: dict) -> None:
        """Registered seller metadata and the provider ceiling follow durable pricing evidence."""
        self._record_market({"kind": "market.registered", "model_id": model_id, **seller})
        self.market.register(model_id, price.per_request_micro)
        self.prices.register(model_id, price)
        self.sellers[model_id] = seller

    def _compute_failure(self, handle: str) -> None:
        """A reserve shortfall counts once in the enclosing routed event, after its ledger item."""
        self._record_market({"kind": "compute.unaffordable", "handle": handle})
        self._compute_unaffordable = True

    def _record_insolvency_event(self, ev: Event) -> None:
        """Routed affordable events reset the streak; events without decisions leave it alone."""
        if not self._compute_routed:
            return
        count = self.insolvency_count + 1 if self._compute_unaffordable else 0
        self._record_market(
            {
                "kind": "treasury.insolvency",
                "event_id": ev.id,
                "consecutive_events": count,
                "unaffordable": self._compute_unaffordable,
            }
        )
        self.insolvency_count = count

    def _manage_reserve_window(self) -> None:
        if self.reserve_window_start is None:
            self.cadence.launch(self.clock.now_ns if self.live else 0)
        if (
            self.reserve_window_start is None
            or self.clock.now_ns >= self.reserve_window_start + self.m.novelty.window_ns
        ):
            if self.reserve_window_start is not None:
                self._close_price_window()
            self.reserve.open_window(self.clock.now_ns, self.wallet.balance)
            self.reserve_window_start = self.clock.now_ns
            self.stats.reserve_windows += 1
            self.window = MeasureWindow(self.stats.reserve_windows, self._equity_micro())
            self._observe_positions()
            self._activate_charter_if_due()
            self._derive_regions()

    # ---- prices

    def _equity_micro(self) -> int:
        try:
            return _usd_to_micro(self.exchange.account().equity_usd)
        except RuntimeError:  # read-only live venue: the wallet is the only equity there is
            return self.wallet.balance

    def _observe_delivered_event(self, ev: Event) -> None:
        """Only ledgered event deliveries contribute raw verdict samples to this window."""
        if ev.kind is EventKind.VERDICT:
            judges = self.window.verdicts.setdefault(ev.payload["about_handle"], {})
            judge = self.handle_to_assembly.get(
                ev.payload["evaluator_handle"], ev.payload["evaluator_handle"]
            )
            judges.setdefault(judge, []).append(float(ev.payload["verdict"]))
        elif ev.kind is EventKind.META_VERDICT:
            self.window.meta_verdicts.append(float(ev.payload["score"]))
        elif ev.kind is EventKind.MARKET_MID:
            self._observe_positions()

    def _observe_positions(self) -> None:
        """A new peak position notional is recorded before it enters the window."""
        try:
            positions = self.exchange.account().positions
            mids = self.exchange.mids() if positions else {}
        except RuntimeError:
            return
        notionals: dict[str, Decimal] = {}
        if any(position.size and position.coin not in mids for position in positions):
            return
        for position in positions:
            if position.size:
                notionals[position.coin] = notionals.get(position.coin, Decimal(0)) + (
                    position.size * Decimal(str(mids[position.coin]))
                )
        if not notionals:
            return
        peak = max(_usd_to_micro(abs(value)) for value in notionals.values())
        previous = self.window.max_position_notional_micro
        if previous is None or peak > previous:
            self.ledger.append(
                {
                    "kind": "observation.position_peak",
                    "window": self.window.index,
                    "notional_micro": peak,
                    "ts": self.clock.now_ns,
                }
            )
            self.window.max_position_notional_micro = peak

    def _derive_regions(self) -> None:
        """Every readable card of the current edition holds a region; unreadable ones hold none.

        New cards register; cards whose bound moved (a rolling median, a restated
        card) keep their price and get the new bounds. Each change is a
        ``price.region`` entry; prose the runtime cannot read is logged once per
        card per edition as ``price.unparsed``.
        """
        regions: dict[str, CardRegion] = {}
        for card in self.charter.cards:
            region = region_for(card, rolling=self.rolling)
            if region is None:
                if card.id in self.regions:
                    self.controller.clear_region(card.id)
                key = (card.id, self.charter.edition)
                unknown = []
                if not parses(card):
                    unknown.append("region")
                if observation_for(card.observation) is None:
                    unknown.append("observation")
                if unknown and key not in self.unparsed_logged:
                    self.ledger.append(
                        {
                            "kind": "price.unparsed",
                            "card_id": card.id,
                            "text": card.acceptable_region,
                            "observation": card.observation,
                            "unparsed": unknown,
                            "reason": "unknown " + " and ".join(unknown),
                            "edition": self.charter.edition,
                            "ts": self.clock.now_ns,
                        }
                    )
                    self.unparsed_logged.add(key)
                continue
            regions[card.id] = region
            if region == self.regions.get(card.id):
                continue
            self.ledger.append(
                {
                    "kind": "price.region",
                    "card_id": card.id,
                    "edition": self.charter.edition,
                    "region": {
                        "kind": region.kind,
                        "lo": region.lo,
                        "hi": region.hi,
                        "scale": region.scale,
                    },
                    "ts": self.clock.now_ns,
                }
            )
            if card.id not in self.priced:
                self.controller.register_pending(card.id)
                self.priced.add(card.id)
            self.controller.update_region(region)
        self.regions = regions

    def _close_price_window(self) -> None:
        """The window that just closed yields at most one observation per priced card.

        cost_per_return: mean wallet cost (micro-USD) of well-formed producer
        returns; well_formed_rate: ok returns over all invocations;
        forecast_skill: mean consequence-standing skill over evaluators with
        settled forecasts; turnover: filled notional over equity at the window
        start (0 with no fills). A quantity without support is not observed.
        """
        evaluators = {a.spec.id for a in self.assemblies.values() if a.spec.role == "evaluator"}
        skills = [
            v["skill"]
            for eid, v in self.standing.snapshot().items()
            if eid in evaluators and v.get("n")
        ]
        w = replace(self.window, forecast_skills=skills)
        values = {o.id: value for o in CATALOGUE if (value := o.measure(w)) is not None}
        card_values = {
            c.id: values[o.id]
            for c in self.charter.cards
            if c.id in self.regions
            and (o := observation_for(c.observation)) is not None
            and o.id in values
        }
        self.ledger.append(
            {
                "kind": "price.window",
                "window": w.index,
                "window_end_event": self.n,
                "values": card_values,  # Diary dimensions are card ids, not catalogue ids.
                "observations": values,
                "ts": self.clock.now_ns,
            }
        )
        before = self.controller.snapshot()["cards"]
        observed = sorted(card_values)
        for card_id in observed:
            self.controller.observe(card_id, card_values[card_id], window_end_event=self.n)
        after = self.controller.snapshot()["cards"]
        for card_id in observed:
            if after[card_id]["updates"] > before[card_id]["updates"]:
                self.stats.price_updates += 1
            else:
                self.stats.price_skipped += 1
        for card in self.charter.cards:
            observation = observation_for(card.observation)
            if observation is not None and observation.id in values:
                samples = (
                    w.costs if observation.id == "cost_per_return" else [values[observation.id]]
                )
                self.rolling[f"{card.id}_prev_median"] = float(median(samples))
        self.stats.last_window_values = values

    def _penalty_for(self, cards: frozenset[str]) -> float:
        """Σ λ_j · violation_j over the latest window's values for cards the role answers for."""
        values = {
            card.id: self.stats.last_window_values[observation.id]
            for card in self.charter.cards
            if card.id in cards
            and card.id in self.regions
            and (observation := observation_for(card.observation)) is not None
            and observation.id in self.stats.last_window_values
        }
        return self.controller.penalty(values) if values else 0.0

    def _settle_priced(
        self,
        handle: str,
        *,
        channel: str,
        score: float,
        definition_version: str,
        sampling_ref: str | None,
        cards: frozenset[str],
    ) -> None:
        """Settle a judged score less the card penalty, clipped to [0, 1]; both are ledgered."""
        penalty = self._penalty_for(cards)
        effective = min(1.0, max(0.0, score - penalty))
        self.queue.settle(
            handle,
            channel=channel,
            score=effective,
            status=SettleStatus.SETTLED,
            definition_version=definition_version,
            sampling_ref=sampling_ref,
        )
        self.window.outcomes += 1
        self.ledger.append(
            {
                "kind": "price.penalty",
                "handle": handle,
                "channel": channel,
                "raw": score,
                "penalty": penalty,
                "effective": effective,
                "ts": self.clock.now_ns,
            }
        )
        if penalty > 0:
            self.stats.penalized_settlements += 1

    # ---- exchange effects

    def _settle_exchange_effects(self, evs: list[WorldEvent]) -> None:
        for we in evs:
            self.consequences.observe(str(we.kind), dict(we.payload), self.n)
        for we in evs:
            if self.wallet.dead:
                return
            if we.kind is WorldEventKind.FILL:
                self.stats.fills += 1
                self.window.fills += 1
                self.window.notional_micro += _usd_to_micro(
                    Decimal(str(we.payload["size"])) * Decimal(str(we.payload["px"]))
                )
                realized = _usd_to_micro(we.payload["realized_usd"])
                self.window.realized_pnl_micro += realized
                fee = _usd_to_micro(we.payload["fee_usd"])
                self.realized_to_date += realized
                self.fees_to_date += fee
                delta = realized - fee
                if delta:
                    self.wallet.settle(delta, f"fill:{we.payload['order_id']}", "exchange_pnl")
            elif we.kind is WorldEventKind.FUNDING:
                paid = _usd_to_micro(we.payload["paid_usd"])
                self.funding_to_date -= paid
                if paid:
                    self.wallet.settle(-paid, f"funding:{we.payload['coin']}:{we.ts_ns}", "funding")
            self.internal.append(self._kernel_event(we))
        if hasattr(self.exchange, "sync_cash"):
            self.exchange.sync_cash(
                getattr(self.treasury, "venue_balance_usd", money_to_usd(self.wallet.balance))
            )
        self._observe_positions()

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
        self.consequences.order_result(
            ret.handle,
            {
                "status": result.status,
                "order_id": result.order_id,
                "filled_size": str(result.filled_size),
            },
            {"size": str(order.size)},
            self.n,
        )
        self.stats.orders_placed += 1
        if result.status == "rejected":
            self.stats.orders_rejected += 1
        if hasattr(self.exchange, "drain_events"):  # fake venue fills synchronously
            self._settle_exchange_effects(self.exchange.drain_events())

    # ---- routing

    def _route(self, ev: Event) -> None:
        kind = str(ev.kind)
        if ev.kind is EventKind.META_VERDICT:
            self._deliver_meta_verdict(ev)
        if ev.kind in (EventKind.VERDICT, EventKind.META_VERDICT):
            ev = self._cascade_arrival(ev)
            if ev is None:
                return
        for state in list(self.routers.get(kind, [])):
            self._route_with(state, ev)

    def _cascade_arrival(self, ev: Event) -> Event | None:
        """Ledger every arrival and release before changing buffers or routing upward."""
        tier = event_tier(ev)
        gate = self.cascade.get(tier)
        rng = random.Random()
        rng.setstate(self.rng.getstate())
        if gate is None:
            gate = CascadeGate(
                release_threshold(
                    self.m.timing.min_ratio,
                    self.m.timing.jitter_fraction,
                    rng.random(),
                )
            )
        next_gate, released = gate.add(ev)
        self.ledger.append(
            {
                "kind": "cascade.arrival",
                "tier": tier,
                "event_id": ev.id,
                "threshold": gate.threshold,
                "ts": self.clock.now_ns,
            }
        )
        if released is not None:
            self.ledger.append(
                {
                    "kind": "cascade.release",
                    "tier": tier,
                    "event_id": ev.id,
                    "window": _to_plain(released.payload["window"]),
                    "ts": self.clock.now_ns,
                }
            )
        self.rng.setstate(rng.getstate())
        if next_gate is None:
            self.cascade.pop(tier, None)
        else:
            self.cascade[tier] = next_gate
        if released is not None:
            # The meta judges the window as a distribution (essay II.IV.c); its score
            # settles every handle in the window, so nobody gains by not being sampled.
            handles = list(released.payload["window"]["handles"])
            self.cascade_windows[handles[-1]] = handles[:-1]
        return released

    def _route_with(self, state: RouterState, ev: Event) -> None:
        kind = str(ev.kind)
        mix = self._mix_with_standing if kind == "ProducerReturn" else None
        key = f"{state.learner.id}:{self.n}"
        if isinstance(state.learner, _KeyedLearner):
            state.learner.current_key = key
        universe = self._universe_for(kind, ev)

        def feasible(action_id: str) -> tuple[bool, str]:
            if action_id not in universe:
                return False, "self-judgement"
            return self._is_feasible(action_id)

        sample = state.router.route(kind, feasible, self.rng, mix=mix)
        candidates = [a for a in universe if a != NOOP]
        excluded = dict(sample.excluded)
        unaffordable = bool(candidates) and all(
            excluded.get(a, "").startswith("compute:") for a in candidates
        )
        self._record_market(
            {
                "kind": "compute.route",
                "event_id": ev.id,
                "router": state.learner.id,
                "unaffordable": unaffordable,
            }
        )
        self._compute_routed = True
        self._compute_unaffordable |= unaffordable
        self.stats.exclusions += len(sample.excluded)
        role = self._role_for_kind(kind)
        channel = {"producer": CH_VERDICT, "evaluator": CH_CONFORMITY, "meta": CH_FAST}[role]
        if role == "meta" and any(
            "MetaVerdict" in a.spec.accepts for a in self.assemblies.values()
        ):
            channel = CH_CONFORMITY
        chosen_role = self.assemblies[sample.chosen].spec.role if sample.chosen != NOOP else None
        if chosen_role == "antagonist":
            channel = CH_EXPOSURE
        deadline = (
            self.clock.now_ns + (self.ev.verdict_timeout_events + 2) * self.tick_clock.interval_ns
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
        if kind in ("Verdict", "MetaVerdict"):
            return "meta"
        return "producer"

    def _allowed_tools(self, action_id: str) -> set[str]:
        """Every registered tool is a public primitive; schematics are public (v0.4 §1.6)."""
        return set(self.tool_specs)

    def _run_tool(self, action_id: str, handle: str, call: dict[str, Any]) -> tuple[dict, int]:
        """Execute one tool call through metering. Returns (result, cost)."""
        tool_id = str(call.get("tool"))
        args = call.get("args") if isinstance(call.get("args"), dict) else {}
        if tool_id not in self.tool_specs or tool_id not in self._allowed_tools(action_id):
            return {"error": "unknown or disallowed tool"}, 0
        spec = self.tool_specs[tool_id]
        price = int(spec["price_micro_per_call"])

        def execute() -> dict:
            if spec["kind"] == "venue":
                return self.venue_tools.call(tool_id, args)
            if spec["kind"] == "catalogue":
                return {
                    "models": self._catalogue_search(
                        str(args["substring"]), int(args.get("limit", 20))
                    )
                }
            if spec["kind"] == "market":
                return {
                    "sellers": self.market.discover(
                        url_substring=args.get("url_substring"),
                        query=args.get("query"),
                        limit=args.get("limit", 20),
                    )
                }
            if spec["kind"] == "treasury":
                direction = args.get("direction")
                usd = args.get("usd")
                intent = {
                    "direction": direction,
                    "usd": str(usd),
                    "reason": str(args.get("reason", ""))[:500],
                    "by": action_id,
                    "handle": handle,
                }
                self.ledger.append({"kind": "treasury.intent", **intent, "ts": self.clock.now_ns})
                self._emit(EventKind.TRANSFER_INTENT, intent, source="kernel")
                self.stats.transfer_intents += 1
                return self.treasury.transfer(
                    direction, usd, handle=handle, now_ns=self.clock.now_ns
                )
            tool = self.population_tools.get(tool_id)
            if tool is None:
                return {"error": "tool unavailable"}
            return self.tool_runner.run(tool, args)

        try:
            metered = self.meter.run(
                handle=handle,
                reason=f"tool:{tool_id}",
                ceiling=price,
                execute=execute,
                cost_of=lambda _r: price,
            )
        except Exception as exc:  # infeasible reservation or tool crash: no result, no charge
            return {"error": f"{type(exc).__name__}: {exc}"[:200]}, 0
        if spec["kind"] == "venue":
            self.consequences.order_result(handle, metered.result, args, self.n)
            if tool_id == "venue.cancel" and metered.result.get("status") == "cancelled":
                self.consequences.cancel(str(args["order_id"]), self.n)
            if hasattr(self.exchange, "drain_events"):
                self._settle_exchange_effects(self.exchange.drain_events())
        return metered.result, metered.cost

    @staticmethod
    def _carries_revision(ret: Return) -> bool:
        """A return counts once for a proposal object or tool call, even if later rejected."""
        proposals = ret.outputs.get("register")
        return bool(ret.tool_calls) or (
            isinstance(proposals, list) and any(isinstance(p, dict) for p in proposals)
        )

    def _invoke(self, action_id: str, req: Request, role: str) -> Return:
        asm = self.assemblies[action_id]
        ret = asm.invoke(req)
        self._check_compute_return(req.handle, ret)
        revision = self._carries_revision(ret)
        if ret.status == "ok" and ret.tool_calls:
            results = []
            tool_cost = 0
            for call in ret.tool_calls:
                result, cost = self._run_tool(action_id, req.handle, call)
                tool_cost += cost
                ok = not (isinstance(result, dict) and "error" in result)
                self.stats.tool_calls += 1
                if not ok:
                    self.stats.tool_call_failures += 1
                self.ledger.append(
                    {
                        "kind": "tool.call",
                        "handle": req.handle,
                        "assembly_id": action_id,
                        "tool": call.get("tool"),
                        "args": json.dumps(call.get("args"), default=str)[:1000],
                        "ok": ok,
                        "cost": cost,
                        "ts": self.clock.now_ns,
                    }
                )
                self.window.tool_calls += 1
                results.append(
                    {"tool": call.get("tool"), "args": call.get("args"), "result": result}
                )
            follow = Request(
                handle=req.handle,
                description=req.description,
                inputs={**req.inputs, "tool_results": results},
                capability_versions=req.capability_versions,
                outcome_schema=req.outcome_schema,
                deadline_ns=req.deadline_ns,
                cost_ceiling=req.cost_ceiling,
                parent_handle=req.parent_handle,
                completion_criterion=req.completion_criterion,
                scoring_channel=req.scoring_channel,
                resource_liability=req.resource_liability,
            )
            second = asm.invoke(follow)
            self._check_compute_return(req.handle, second)
            revision = revision or self._carries_revision(second)
            if second.tool_calls:
                self.ledger.append(
                    {"kind": "tool.calls_ignored", "handle": req.handle, "ts": self.clock.now_ns}
                )
            ret = Return(
                req.handle,
                second.outputs,
                ret.cost + tool_cost + second.cost,
                second.status,
                children=second.children,
                served_by=second.served_by,
                stop_reason=second.stop_reason,
            )
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
                "outputs": json.dumps(ret.outputs, default=str)[:4000],
                "ts": self.clock.now_ns,
            }
        )
        self.window.invocations += 1
        if ret.status == "ok":
            self.window.ok += 1
            if role == "producer":
                self.window.costs.append(ret.cost)
        if role == "producer" and revision:
            self.window.revision_handles.add(req.handle)
        return ret

    def _check_compute_return(self, handle: str, ret: Return) -> None:
        """Assembly-wrapped affordability failures join the enclosing event's insolvency count."""
        reason = str(ret.outputs.get("reason", ""))
        if ret.status == "failed" and (
            reason == "ceiling exceeds request cost_ceiling"
            or reason.startswith(("InsufficientReserve:", "infeasible:"))
        ):
            self._compute_failure(handle)

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
        self.consequences.start(handle, self.n)
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
        inputs = {
            "kind": str(ev.kind),
            "payload": payload,
            "world": self._world_block(),
            "your_recent_returns": list(self.memory.get(sample.chosen, ())),
        }
        if sample.chosen == NOOP:
            self.stats.noops += 1
            ret = Return(handle, {"action": "noop"}, 0, "ok")
        else:
            schema = {
                "type": "object",
                "properties": {
                    "action": {"type": "string"},
                    "register": self._register_schema(),
                },
                "required": ["action"],
            }
            req = self._request(handle, description, inputs, schema, deadline, CH_VERDICT)
            ret = self._invoke(sample.chosen, req, "producer")
            self._execute_outputs(ret)
            self._apply_registrations(handle, ret)
            self.handle_to_assembly[handle] = sample.chosen
            self.memory.setdefault(sample.chosen, deque(maxlen=3)).append(
                {"handle": handle, "outputs": ret.outputs, "verdict": None}
            )
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
        self._emit(
            EventKind.PRODUCER_RETURN,
            {
                "about_handle": handle,
                "description": description,
                # Judges see the event the producer answered, never the producer's private
                # memory or its copy of the world block, and never its name (v0.4 §1.6).
                "inputs": {"kind": inputs["kind"], "payload": inputs["payload"]},
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
        }
        schema = {
            "type": "object",
            "properties": {
                "verdict": {"type": "number", "minimum": 0, "maximum": 1},
                "rationale": {"type": "string"},
                "forecasts": self._forecast_schema(),
                "register": self._register_schema(),
            },
            "required": ["verdict", "rationale", "forecasts"],
        }
        req = self._request(
            handle,
            "Evaluate a producer return against the charter, then give "
            f"{self.ev.max_forecasts_per_verdict} forecasts: for each, a predicate from the "
            "list and q = your probability it happens within its horizon.",
            inputs,
            schema,
            deadline,
            CH_CONFORMITY,
        )
        ret = self._invoke(sample.chosen, req, "evaluator")
        self._apply_registrations(handle, ret)
        self.handle_to_assembly[handle] = sample.chosen
        self.memory.setdefault(sample.chosen, deque(maxlen=3)).append(
            {"handle": handle, "outputs": ret.outputs, "verdict": None}
        )
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
            and about_decision.channel == CH_VERDICT
            and about_decision.status is SettleStatus.PENDING
        ):
            self._settle_priced(
                about,
                channel=CH_VERDICT,
                score=verdict,
                definition_version=DEF_VERDICT,
                sampling_ref=handle,
                cards=PRODUCER_CARDS,
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
        self.consequences.seal_verdict(
            self.book,
            self.queue,
            evaluator_handle=handle,
            evaluator_id=sample.chosen,
            about=about,
            verdict=verdict,
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
                    deadline_ns=self.clock.now_ns + (horizon + 2) * self.tick_clock.interval_ns * 4,
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
        recursive = ev.kind is EventKind.META_VERDICT
        about = payload["by"] if recursive else payload["evaluator_handle"]
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
                "verdict": payload["score"] if recursive else payload["verdict"],
                "rationale": payload.get("rationale", ""),
            },
            "producer_outputs": payload.get("producer_outputs", {}),
            "charter": self.charter.render(),
            "world": self._world_block(),
        }
        if "window" in payload:
            inputs["window"] = payload["window"]
        if recursive:
            inputs["meta_verdict"] = payload
        schema = {
            "type": "object",
            "properties": {
                "conformity": {"type": "number"},
                "rationale": {"type": "string"},
                "register": self._register_schema(),
            },
            "required": ["conformity"],
        }
        req = self._request(
            handle,
            "Assess the released representative verdict for conformity with the charter, "
            "using its window as context.",
            inputs,
            schema,
            deadline,
            channel,
        )
        ret = self._invoke(sample.chosen, req, "meta")
        self.handle_to_assembly[handle] = sample.chosen
        self.memory.setdefault(sample.chosen, deque(maxlen=3)).append(
            {"handle": handle, "outputs": ret.outputs, "verdict": None}
        )
        self._apply_registrations(handle, ret)
        conformity = _as_unit(ret.outputs.get("conformity")) if ret.status == "ok" else None
        if channel == CH_FAST:
            self.queue.settle(
                handle,
                channel=CH_FAST,
                score=1.0 if conformity is not None else 0.0,
                status=SettleStatus.SETTLED,
                definition_version=DEF_FAST,
                sampling_ref=None,
            )
            self.stats.fast_settlements += 1
            self.window.outcomes += 1
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
                    "rationale": str(ret.outputs.get("rationale", ""))[:2000],
                },
            )

    def _deliver_meta_verdict(self, ev: Event) -> None:
        """Only the first timely higher-tier judgement settles its original handle."""
        payload = ev.payload
        tier, about = payload["tier"], payload["about"]
        self.stats.meta_verdicts[tier] = self.stats.meta_verdicts.get(tier, 0) + 1
        pend = self.pending.get(about)
        if (
            pend is None
            or pend.channel != CH_CONFORMITY
            or tier <= pend.tier
            or self.n - pend.opened_at_event > self.ev.verdict_timeout_events
            or self.queue.get(about).status is not SettleStatus.PENDING
        ):
            return
        self._settle_priced(
            about,
            channel=CH_CONFORMITY,
            score=payload["score"],
            definition_version=DEF_CONFORMITY,
            sampling_ref=payload["by"],
            cards=EVALUATOR_CARDS,
        )
        del self.pending[about]
        self.stats.conformities += 1
        for sibling in self.cascade_windows.pop(about, []):
            sib = self.pending.get(sibling)
            if (
                sib is None
                or self.n - sib.opened_at_event > self.ev.verdict_timeout_events
                or self.queue.get(sibling).status is not SettleStatus.PENDING
            ):
                continue
            self._settle_priced(
                sibling,
                channel=CH_CONFORMITY,
                score=payload["score"],
                definition_version=DEF_CONFORMITY,
                sampling_ref=payload["by"],
                cards=EVALUATOR_CARDS,
            )
            del self.pending[sibling]
            self.stats.conformities += 1
        self.stats.max_settlement_latency_events = max(
            self.stats.max_settlement_latency_events, self.n - pend.opened_at_event
        )
        owner = self.handle_to_assembly.get(about)
        if owner is not None:
            for entry in self.memory.get(owner, ()):
                if entry["handle"] == about:
                    entry["verdict"] = payload["score"]

    # ---- registration

    def _apply_registrations(self, handle: str, ret: Return) -> None:
        if ret.status != "ok":
            return
        raw = ret.outputs.get("register")
        amendments = []
        if isinstance(raw, list):
            amendments = [x for x in raw if isinstance(x, dict) and x.get("kind") == "amendment"]
            raw = [x for x in raw if not (isinstance(x, dict) and x.get("kind") == "amendment")]
        # Keep the shared parser's proposal cap and ordering while adapting its legacy
        # vendor/model field validation to opaque seller URLs and Venice model ids.
        namespaced = {}
        if isinstance(raw, list):
            original_ids = {
                item.get("openrouter_id")
                for item in raw
                if isinstance(item, dict) and isinstance(item.get("openrouter_id"), str)
            }
            adapted = []
            for index, item in enumerate(raw):
                mid = item.get("openrouter_id") if isinstance(item, dict) else None
                if (
                    isinstance(mid, str)
                    and item.get("kind") == "model"
                    and mid.startswith(("x402:", "venice:"))
                ):
                    alias = f"namespace/{index}"
                    while alias in original_ids:
                        alias += "-"
                    namespaced[alias] = mid
                    item = {**item, "openrouter_id": alias}
                adapted.append(item)
            raw = adapted
        extra: dict[str, Any] = {}
        if "known_tools" in inspect.signature(parse_proposals).parameters:
            extra["known_tools"] = frozenset(self.tool_specs)
        accepted, rejected = parse_proposals(
            {**ret.outputs, "register": raw} if raw is not None else ret.outputs,
            event_kinds=PRODUCER_KINDS | {"ProducerReturn", "Verdict", "MetaVerdict"},
            known_models=frozenset(self.prices.prices),
            known_assemblies=frozenset(self.assemblies),
            **extra,
        )
        accepted = [
            ModelProposal(namespaced[prop.openrouter_id])
            if isinstance(prop, ModelProposal) and prop.openrouter_id in namespaced
            else prop
            for prop in accepted
        ]
        for item in amendments:
            try:
                self._propose_amendment(handle, item)
                self.stats.registrations_accepted += 1
                self.window.registrations += 1
            except (Infeasible, PermissionError, ValueError, KeyError, TypeError) as exc:
                self._reject_registration(
                    handle, f"amendment: {type(exc).__name__}: {exc}"[:300], None
                )
        for r in rejected:
            self._reject_registration(handle, r.reason, r.index)
        for prop in accepted:
            try:
                self._register(handle, prop)
                self.stats.registrations_accepted += 1
                self.window.registrations += 1
            except (Infeasible, PermissionError, ValueError, KeyError, X402Error) as exc:
                self._reject_registration(handle, f"{type(exc).__name__}: {exc}", None)

    def _reject_registration(self, handle: str, reason: str, index: int | None) -> None:
        """Ledger a refused proposal and keep the reason public: a proposer that cannot see
        why it was refused re-proposes the same thing (run 6, eleven times)."""
        self.stats.registrations_rejected += 1
        item = {"kind": "registration.rejected", "handle": handle, "reason": reason}
        if index is not None:
            item["index"] = index
        self.ledger.append({**item, "ts": self.clock.now_ns})
        self.window.registration_rejections += 1
        self.registration_feedback.append({k: v for k, v in item.items() if k != "kind"})

    def _register(self, handle: str, prop: Any) -> None:
        amount = self.ev.trial_amount_micro
        if isinstance(prop, ToolProposal):
            contract = Contract(
                id=f"tool:{prop.id}",
                version=1,
                kind="tool",
                description=prop.description,
                input_schema=_to_plain(prop.args_schema),
                output_schema={"type": "object"},
                price=PriceSpec({"call": self.m.tools.population_tool_micro_per_call}),
                permissions=frozenset({"sandbox.run"}),
                resource_bounds=ResourceBounds(max_duration_ns=prop.timeout_s * 1_000_000_000),
            )
            res = self.reserve.reserve_for(contract, amount)
            self.registry.register(contract, by_handle=handle, reservation=res)
            tool = PopulationTool(
                prop.id, prop.description, prop.args_schema, prop.code, prop.timeout_s, handle
            )
            self.population_tools[prop.id] = tool
            owner = self.handle_to_assembly.get(handle)
            if owner is not None:
                self.tool_owner[prop.id] = owner
            self.tool_specs[prop.id] = as_spec(tool, self.m.tools.population_tool_micro_per_call)
            self.stats.population_tools_registered += 1
            self._emit(EventKind.REGISTERED, {"kind": "tool", "id": prop.id, "by": handle})
            return
        if isinstance(prop, ModelProposal):
            if prop.openrouter_id.startswith("x402:"):
                if prop.openrouter_id in self.prices.prices:
                    raise ValueError("model already registered")
                price, seller = self.market.registration_price(prop.openrouter_id)
                contract = _model_contract(prop.openrouter_id, price, "x402")
                res = self.reserve.reserve_for(contract, amount)
                self.registry.register(contract, by_handle=handle, reservation=res)
                self._record_seller(prop.openrouter_id, price, seller)
                self._emit(
                    EventKind.REGISTERED,
                    {
                        "kind": "model",
                        "id": prop.openrouter_id,
                        "by": handle,
                        "network": seller["network"],
                        "per_request_micro": price.per_request_micro,
                    },
                )
                return
            base, _, effort = prop.openrouter_id.partition("@")
            if not base or len(base) > 4096 or any(c.isspace() for c in base):
                raise ValueError("invalid model id")
            if effort and effort not in (
                "none",
                "minimal",
                "low",
                "medium",
                "high",
                "xhigh",
                "max",
            ):
                raise ValueError("reasoning level must be none|minimal|low|medium|high|xhigh|max")
            if base in self.prices.prices:
                price = self.prices.price(base)
            elif self.catalogue is not None and base in self.catalogue:
                price = self.catalogue[base]
            else:
                raise ValueError("no catalogue entry for that model in this world")
            provider = "venice" if base.startswith("venice:") else "openrouter"
            contract = _model_contract(prop.openrouter_id, price, provider)
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
            self._build_router(prop.event_kind, prop.learner, prop.gamma, replace=not prop.add)
            self.stats.routers_replaced += 1
            self._emit(
                EventKind.ROUTER_REPLACED,
                {
                    "event_kind": prop.event_kind,
                    "learner": prop.learner,
                    "gamma": prop.gamma,
                    "added": prop.add,
                    "by": handle,
                },
            )

    def _propose_amendment(self, handle: str, item: dict[str, Any]) -> None:
        from factorylab.charter.amendment import proposed_price, proposed_tick_interval
        from factorylab.charter.charter import MetricCard

        tick_interval = None
        if "tick_interval" in item:
            try:
                proposed_tick_interval(
                    item["tick_interval"], self.m.clock.min_tick_ns, self.m.max_tick_ns
                )
            except ValueError as exc:
                feedback = {"id": str(item.get("id", "")), "reason": str(exc)}
                self.ledger.append({"kind": "amendment.rejected", **feedback})
                self.amendment_feedback = feedback
                raise
            tick_interval = item["tick_interval"]
        prices = []

        def cards(key: str) -> tuple[MetricCard, ...]:
            raw = item.get(key) or []
            if not isinstance(raw, list):
                raise ValueError(f"{key} must be a list")
            out = []
            for c in raw:
                if not isinstance(c, dict):
                    raise ValueError(f"{key} entries must be objects")
                if "lambda" in c:
                    try:
                        value = proposed_price(c["lambda"], self.m.prices.lambda_max)
                    except ValueError as exc:
                        feedback = {"id": str(item.get("id", "")), "reason": str(exc)}
                        self.ledger.append({"kind": "amendment.rejected", **feedback})
                        self.amendment_feedback = feedback
                        raise
                    prices.append((str(c.get("id", "")), value))
                out.append(
                    MetricCard(
                        str(c.get("id", "")),
                        str(c.get("norm", "")),
                        str(c.get("description", "")),
                        str(c.get("units", "")),
                        str(c.get("window", "")),
                        str(c.get("acceptable_region", "")),
                        str(c.get("observation", "")),
                    )
                )
            return tuple(out)

        remove = item.get("remove") or []
        if not isinstance(remove, list) or any(not isinstance(r, str) for r in remove):
            raise ValueError("remove must be a list of card ids")
        am = Amendment(
            id=str(item.get("id", "")),
            proposer_handle=handle,
            edition_base=self.charter.edition,
            add=cards("add"),
            replace=cards("replace"),
            remove=tuple(remove),
            predicted_effect=str(item.get("predicted_effect", "")),
            proposed_prices=tuple(prices),
            tick_interval=tick_interval,
        )
        contract = Contract(
            id=f"amendment:{am.id}",
            version=1,
            kind="tool",
            description="charter amendment proposal",
            input_schema={"type": "object"},
            output_schema={"type": "object"},
            price=PriceSpec({}),
            permissions=frozenset(),
            resource_bounds=ResourceBounds(),
        )
        res = self.reserve.reserve_for(contract, self.ev.trial_amount_micro)
        self.registry.register(contract, by_handle=handle, reservation=res)
        self.charter_book.propose(am)
        self.stats.amendments_proposed += 1
        self.window.amendments_proposed += 1
        eligible = {a.spec.id: a.spec.role for a in self.assemblies.values()}
        committee = self.charter_book.seat(am.id, eligible, self.rng)
        self._hold_vote(am, committee)

    def _hold_vote(self, am: Any, committee: Any) -> None:
        prices = dict(am.proposed_prices)
        for seat in committee.seats:
            alias, assembly_id = seat[0], seat[1]
            handle = f"vote-{am.id}-{alias}"
            inputs = {
                "amendment": {
                    "id": am.id,
                    "add": [
                        {**vars(c), **({"lambda": prices[c.id]} if c.id in prices else {})}
                        for c in am.add
                    ],
                    "replace": [
                        {**vars(c), **({"lambda": prices[c.id]} if c.id in prices else {})}
                        for c in am.replace
                    ],
                    "remove": list(am.remove),
                    "predicted_effect": am.predicted_effect,
                    **({"tick_interval": am.tick_interval} if am.tick_interval is not None else {}),
                },
                "charter": self.charter.render(),
                "world": self._world_block(),
            }
            schema = {
                "type": "object",
                "properties": {"vote": {"type": "boolean"}, "reason": {"type": "string"}},
                "required": ["vote", "reason"],
            }
            req = self._request(
                handle,
                "Vote on an amendment to the charter's metric cards.",
                inputs,
                schema,
                self.clock.now_ns + self.tick_clock.interval_ns * 10,
                CH_FAST,
            )
            asm = self.assemblies.get(assembly_id)
            if asm is None:
                self.charter_book.abstain(committee, alias)
                continue
            ret = asm.invoke(req)
            self.stats.invocations += 1
            self.ledger.append(
                {
                    "kind": "invocation",
                    "assembly_id": assembly_id,
                    "role": "voter",
                    "handle": handle,
                    "cost": ret.cost,
                    "status": ret.status,
                    "stop_reason": ret.stop_reason or "none",
                    "served_by": ret.served_by,
                    "outputs": json.dumps(ret.outputs, default=str)[:2000],
                    "ts": self.clock.now_ns,
                }
            )
            vote = ret.outputs.get("vote") if ret.status == "ok" else None
            if isinstance(vote, bool):
                self.charter_book.vote(
                    committee, alias, vote, str(ret.outputs.get("reason", ""))[:1000]
                )
                self.stats.votes_cast += 1
            else:
                self.charter_book.abstain(committee, alias)
        outcome = self.charter_book.tally(committee)
        if outcome == "passed":
            self.cadence.approve(am.id)
            self.cadence.ready(
                now_ns=self.clock.now_ns,
                tick_interval_ns=self.tick_clock.interval_ns,
                window=self.stats.reserve_windows,
            )
            self.stats.amendments_passed += 1

    def _next_charter_activation(self) -> Charter | None:
        """Activate only at a boundary that meets the measured governance separation."""
        if not self.cadence.ready(
            now_ns=self.clock.now_ns,
            tick_interval_ns=self.tick_clock.interval_ns,
            window=self.stats.reserve_windows,
        ):
            return None
        new = self.charter_book.activate_due(self.clock.now_ns)
        if new is not None:
            am = self.charter_book.activated_amendment(new.edition)
            self.cadence.activated(am.id, self.clock.now_ns, self.tick_clock.interval_ns)
        return new

    def _activate_charter_if_due(self) -> None:
        new = self._next_charter_activation()
        while new is not None:
            self.charter = new
            am = self.charter_book.activated_amendment(new.edition)
            for card_id in sorted(self.priced - {c.id for c in new.cards}):
                self.controller.remove(card_id, amendment_id=am.id)
                self.priced.remove(card_id)
                self.regions.pop(card_id, None)
            self._derive_regions()
            for card_id, value in am.proposed_prices:
                if card_id not in self.priced:
                    self.controller.register_pending(card_id)
                    self.priced.add(card_id)
                self.controller.set_price(card_id, value, amendment_id=am.id)
            if am.tick_interval is not None:
                from factorylab.charter.amendment import proposed_tick_interval

                interval = proposed_tick_interval(
                    am.tick_interval, self.m.clock.min_tick_ns, self.m.max_tick_ns
                )
                if interval != self.tick_clock.interval_ns:
                    self.ledger.append(
                        {
                            "kind": "clock.changed",
                            "edition": new.edition,
                            "old_ns": self.tick_clock.interval_ns,
                            "new_ns": interval,
                        }
                    )
                    self.tick_clock.set_interval(interval)
                    self.stats.clock_changes += 1
            self.stats.amendments_activated += 1
            self.window.amendments_activated += 1
            new = self._next_charter_activation()

    def _open_epoch(self, kind: str) -> None:
        universe = self._universe_for(kind)
        entry = {"kind": "epoch", "event_kind": kind, "universe": universe, "ts": self.clock.now_ns}
        states = self.routers.get(kind)
        if not states:
            self._build_router(kind, "exp3", self.router_gamma)
            self.ledger.append({**entry, "carried": False})
            self.stats.epochs += 1
            return
        for i, state in enumerate(list(states)):
            if universe == state.universe:
                continue
            if isinstance(state.learner, EXP3):
                new_learner = state.learner.expand(universe)
                state.learner = new_learner
                state.universe = universe
                state.router = Router(
                    new_learner, lambda _k, u=universe: [x for x in u if x != NOOP]
                )
                state.epoch += 1
                self.ledger.append({**entry, "carried": True, "router": state.learner.id})
            else:  # snapshot learners cannot expand; rebuild fresh over the new universe
                lid = state.learner.id
                fresh = self._make_learner(kind, "blum_mansour", self.router_gamma, universe, lid)
                states[i] = RouterState(
                    kind,
                    universe,
                    fresh,
                    Router(fresh, lambda _k, u=universe: [x for x in u if x != NOOP]),
                    state.epoch + 1,
                )
                self.ledger.append({**entry, "carried": False, "router": lid})
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

    def _scoring_block(self) -> dict[str, Any]:
        """How decisions settle, stated as facts about the world (v0.4 §1.6: schematics are
        public; no goals). Run 7 showed judges grading conformity alone because nothing told
        them a verdict is also a forecast, and producers reinforced by verdicts that never
        answered to money."""
        ev = self.ev
        return {
            "producer_or_antagonist_return": (
                "settles on the verdict channel: the score is the verdict (0 to 1) an evaluator "
                f"gives it within {ev.verdict_timeout_events} events, less the card penalty; "
                "unjudged returns are censored (no score, no learning)"
            ),
            "antagonist_exposure": (
                "an antagonist return also settles 1 on the exposure channel when a judge's "
                "forecast about it scores worse than the prevalence baseline, else 0"
            ),
            "verdict": (
                "a verdict is also sealed as a forecast, with q = verdict, that the judged "
                "return pays off; it settles on the evaluator's consequence standing"
            ),
            "return_paid_off": (
                "1 when the lots the return's own fills opened are closed with realized gain "
                "net of fees, funding and the return's own compute cost; a return that placed "
                "no fills settles 0; lots still open after "
                f"{ev.consequence_backstop_events} events are marked to mid"
            ),
            "consequence_standing": (
                "Brier score of the evaluator's forecasts against the prevalence baseline; "
                f"it enters evaluator selection with weight {ev.consequence_share} beside the "
                "learned selection"
            ),
            "evaluator_return": (
                "settles on the conformity channel: the score a meta gives the verdict within "
                f"{ev.verdict_timeout_events} events, less the card penalty; metas judge one "
                f"verdict in every {self.m.timing.min_ratio} (with jitter) as the window's "
                "representative and its score settles the whole window"
            ),
            "meta_return": (
                "settles on the fast channel (well-formed = 1) unless a higher tier of metas "
                "exists, in which case on conformity like an evaluator"
            ),
            "card_penalty": (
                "each priced metric card subtracts its price times the window's violation "
                "from verdict and conformity scores; prices are in card_prices"
            ),
            "novelty_reserve": (
                "registrations draw on the novelty reserve at the trial amount; refused "
                "proposals carry a reason in registration_feedback"
            ),
        }

    def _standing_for(self, evaluator_id: str) -> dict[str, Any] | None:
        """A judge's own consequence standing: skill against the prevalence baseline, sample
        size, selection weight. Its own running score, private to it (v0.4 §1.6)."""
        st = self.standing.snapshot().get(evaluator_id)
        if not st or not st.get("n"):
            return None
        return {
            "skill_vs_baseline": round(float(st["skill"]), 4),
            "settled_forecasts": st["n"],
            "selection_weight": round(float(st["weight"]), 4),
        }

    def _deliver_consequence_to_memory(self, s: Any) -> None:
        """The reward line must reach the primitive that acted, not only its router (essay
        II.I.b: memory across rounds, reward attributable to the decision). A producer learns
        whether its return paid off; a judge learns whether the return it blessed paid off and
        how its verdict scored. Private local state, never public. Run 7 showed judges blessing
        inaction at 1.0 while their standing fell, because nothing ever told them."""
        if s.predicate_id != "return_paid_off":
            return
        producer = self.handle_to_assembly.get(s.about_handle)
        if producer is not None:
            for entry in self.memory.get(producer, ()):
                if entry["handle"] == s.about_handle:
                    entry["paid_off"] = s.y
        prefix = "verdict-"
        if not s.handle.startswith(prefix):
            return
        judge_handle = s.handle[len(prefix) :]
        judge = self.handle_to_assembly.get(judge_handle)
        if judge is None:
            return
        for entry in self.memory.get(judge, ()):
            if entry["handle"] == judge_handle:
                entry["judged_return_paid_off"] = s.y
                entry["your_consequence_brier"] = round(float(s.brier), 4)
                entry["baseline_brier"] = (
                    round(float(s.baseline_brier), 4) if s.baseline_brier is not None else None
                )

    def _settle_exposures(self, settled: list[Any]) -> None:
        """An antagonist wins when a judge's forecast about its return scored below baseline."""
        for s in settled:
            opened = self.pending_exposure.get(s.about_handle)
            if opened is None or s.brier is None or s.baseline_brier is None:
                continue
            if s.brier < s.baseline_brier:
                self._settle_exposure(s.about_handle, 1.0)
        waiting = {f.about_handle for f in self.book.pending()}
        stale = [
            h
            for h, o in self.pending_exposure.items()
            if self.n - o > self.ev.verdict_timeout_events and h not in waiting
        ]
        for h in stale:
            self._settle_exposure(h, 0.0)

    def _settle_exposure(self, handle: str, score: float) -> None:
        if self.queue.get(handle).status not in (SettleStatus.PENDING, SettleStatus.TIMED_OUT):
            self.pending_exposure.pop(handle, None)
            return
        self.queue.settle(
            handle,
            channel=CH_EXPOSURE,
            score=score,
            status=SettleStatus.SETTLED,
            definition_version=DEF_EXPOSURE,
            sampling_ref=None,
        )
        self.pending_exposure.pop(handle, None)
        self.stats.exposures_settled += 1
        self.window.outcomes += 1
        self.window.exposures_settled += 1
        if score > 0:
            self.stats.exposures_won += 1
            self.window.exposures_won += 1

    def _settle_due_forecasts(self) -> None:
        self.consequences.resolve(self.n)
        pending = {f.handle: f for f in self.book.pending()}
        settled = self.settler.settle_due(self.n, self._facts_for)
        settled.extend(self.settler.settle_consequences(self.consequences.payoff))
        self._settle_exposures(settled)
        for s in settled:
            forecast = pending[s.handle]
            self.cadence.record(
                handle=s.handle,
                predicate_id=s.predicate_id,
                opened_event=forecast.made_at_event,
                settled_event=self.n,
                opened_ns=self.queue.get(s.handle).opened_ns,
                settled_ns=self.clock.now_ns,
                status=str(s.status),
            )
            self.stats.forecasts_settled += 1
            self.window.outcomes += 1
            self.window.censored += int(s.status is SettleStatus.CENSORED)
            if s.predicate_id == "return_paid_off" and s.status is SettleStatus.SETTLED:
                self.window.consequences_settled += 1
                self.window.consequences_paid_off += int(s.y == 1)
            self._emit(
                EventKind.FORECAST_SETTLED,
                {
                    "handle": s.handle,
                    "evaluator_id": s.evaluator_id,
                    "predicate": s.predicate_id,
                    "y": s.y,
                    "brier": s.brier,
                    "status": str(s.status),
                    "marked": s.marked,
                },
            )
            if s.brier is None:
                continue
            self._deliver_consequence_to_memory(s)
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
                self.window.outcomes += 1
                self.window.censored += 1
            del self.pending[p.handle]

    def _deliver_returns(self) -> None:
        for state in self._all_router_states():
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
            "exchange_equity_usd": _equity_or_none(self.exchange.target),
            "live": self.live,
            "evaluation_boundary": EVALUATION_BOUNDARY,
            "charter_edition": self.charter.edition,
            "tools": sorted(self.tool_specs),
            "standing": self.standing.snapshot(),
            "prices": self.controller.snapshot(),
            "routers": {
                st.learner.id: {
                    "event_kind": st.kind,
                    "universe": st.universe,
                    "epoch": st.epoch,
                    "learner": type(st.learner).__name__,
                }
                for st in self._all_router_states()
            },
            "stats": {**vars(self.stats), **self.consequences.counts()},
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

    def state(self) -> dict:
        """Preserve the adapter's current decision key as well as all frozen learning rounds."""
        return {
            "algorithm": "KeyedLearner",
            "inner": self.inner.state(),
            "current_key": self.current_key,
        }

    @classmethod
    def restore(cls, state: dict) -> _KeyedLearner:
        """Rebind a complete snapshot learner without losing its in-flight routing key."""
        from factorylab.learners.delayed import SnapshotLearner

        learner = cls(SnapshotLearner.restore(state["inner"]))
        learner.current_key = state["current_key"]
        return learner


def _price_str(value: Any) -> str:
    """Micro-USD per token equals USD per million tokens; render exactly, Fraction or int."""
    num = getattr(value, "numerator", value)
    den = getattr(value, "denominator", 1)
    return str((Decimal(num) / Decimal(den)).normalize())


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
        description=f"{provider} model {model_id}"
        + (" on eip155:8453 (USDC)" if provider == "x402" else ""),
        input_schema={"type": "object"},
        output_schema={"type": "object"},
        price=PriceSpec(
            {
                "input_token": int(price.input_micro),
                "output_token": int(price.output_micro),
                **(
                    {"request": price.per_request_micro}
                    if provider == "x402" or price.per_request_micro
                    else {}
                ),
            }
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
        market=market,
        exchange=exchange,
        clock_source=clock_source,
        kill_at_end=kill_at_end,
    ).run()
