"""Runtime bootstrap method group."""

from __future__ import annotations

import json
import random
from collections import deque
from decimal import Decimal
from typing import Any

from factorylab.charter.charter import Charter
from factorylab.charter.controller import CardRegion
from factorylab.charter.measurement import CardSamples
from factorylab.cortex.assembly import Assembly, AssemblySpec
from factorylab.kernel.events import Bus, Event
from factorylab.kernel.ledger import Ledger, LedgerLock
from factorylab.kernel.money import money_to_usd
from factorylab.kernel.queue import DecisionQueue
from factorylab.kernel.registry import Contract, PriceSpec, Registry, ResourceBounds
from factorylab.kernel.reserve import NoveltyReserve
from factorylab.kernel.termination import Termination
from factorylab.kernel.timing import TimingRegistry, UpwardBuffer
from factorylab.kernel.wallet import DripSchedule, Wallet
from factorylab.runtime.cadence import GovernanceCadence
from factorylab.runtime.cascade import CascadeGate
from factorylab.runtime.immune import ImmunePriceController
from factorylab.runtime.live import LiveClock, LiveVenue, Reconciler, build_provider
from factorylab.runtime.observations import seed_book
from factorylab.runtime.resume import JournalProxy, RecoveryJournal
from factorylab.runtime.worlds import WorldManifest
from factorylab.settlement import (
    ConsequenceStanding,
    ForecastBook,
    Observer,
    PrevalenceBaseline,
    Settler,
)
from factorylab.settlement.consequence import FillCursor, ReturnConsequences
from factorylab.world.clock import ClockIterator, ClockSource
from factorylab.world.exchange import FakeExchange, HyperliquidExchange
from factorylab.world.market import MultiProvider, X402Provider
from factorylab.world.metering import Meter
from factorylab.world.models import FakeModel, TokenPrice

try:  # phase 3 packages; hard imports once every workstream is merged
    from factorylab.world.venue_tools import VenueTools
except ImportError:  # pragma: no cover
    VenueTools = None  # type: ignore[assignment]


from factorylab.cortex.tools import ObservationRunner, ToolRunner

try:
    from factorylab.charter.amendment import Amendment
    from factorylab.charter.book import CharterBook
except ImportError:  # pragma: no cover
    Amendment = CharterBook = None  # type: ignore[assignment]


from factorylab.runtime.feedback import PendingJudgement
from factorylab.runtime.pricing import MeasureWindow
from factorylab.runtime.routing import RouterState
from factorylab.runtime.shared import SimClock, _to_plain
from factorylab.runtime.summary import RunStats, _assembly_contract, _model_contract
from factorylab.world.scripted import ScriptedProvider


class BootstrapMixin:
    """Preserve runtime state and behavior for bootstrap operations."""

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
        _lock: LedgerLock | None = None,
    ) -> None:
        self._ledger_lock = _lock or LedgerLock(ledger_path)
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
        if self.live and _journal is None:
            self.clock.now_ns = (
                self.tick_clock.now_ns() if isinstance(self.tick_clock, LiveClock)
                else self.tick_clock.start_ns
            )
        self.stats = RunStats()
        self.ev = manifest.evaluation
        self.charter: Charter = manifest.charter

        self.initial = (
            manifest.initial_balance_micro
            if initial_balance_micro is None
            else initial_balance_micro
        )
        # Adapter construction must succeed before a persistent world exists.
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
        if provider is None:
            provider = build_provider(manifest)
        self.provider = provider if provider is not None else ScriptedProvider()
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
        self.market.max_request_micro = manifest.treasury.max_request_micro
        from factorylab.world.treasury import UnconfiguredRail

        if self.live:
            if manifest.treasury.reserve_address is not None:
                from factorylab.world.treasury_rails import LiveRail

                rail = LiveRail(self.exchange, manifest.treasury)
            else:
                rail = UnconfiguredRail(self.exchange)

        # kernel
        if _journal is not None:
            self.ledger = _journal
        else:
            manifest_data = json.loads(manifest.canonical_json())
            from pathlib import Path

            if ledger_path and Path(ledger_path).exists():
                ledger = Ledger.reopen(ledger_path, manifest=manifest_data, clock_ns=self.clock)
                if ledger._event_times()["launch"]:
                    raise FileExistsError("world already launched; use resume")
                ledger.append({"kind": "launch.retry"})
            else:
                ledger = Ledger(
                    ledger_path, manifest=manifest_data, clock_ns=self.clock,
                    full_verify_every=1024,
                    key_path=(ledger_path + ".key") if ledger_path else None,
                )
            self.ledger = RecoveryJournal(ledger, self.clock)
        self.use_drip = drip and manifest.drip is not None
        schedule = None
        if self.use_drip and manifest.drip is not None:
            d = manifest.drip
            schedule = DripSchedule(d.amount_micro, d.period_ns, d.start_ns, d.end_ns)
        self.wallet = Wallet(self.initial, self.ledger, schedule, clock_ns=self.clock,
                             reported_cost_multiple=manifest.treasury.reported_cost_multiple)
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
        self.wallet.bind_novelty(self.reserve, self._novelty_compute)
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
        self.consequence_fills = FillCursor(self.ledger, start_ns=self.clock.now_ns)

        # world
        self.exchange = JournalProxy(
            self.exchange,
            self.ledger,
            "exchange",
            deterministic=isinstance(self.exchange, FakeExchange) and not self.live,
        )
        # Fills before launch belong to nobody; funding uses the same launch boundary.
        self.venue = (
            LiveVenue(self.exchange, last_fill_ns=self.clock.now_ns, ledger=self.ledger,
                      last_funding_ns=self.clock.now_ns)
            if self.live
            else None
        )
        self.prices = manifest.price_table()
        self.meter = Meter(self.wallet)
        from factorylab.world.treasury import FakeTreasury, Treasury

        if not self.live:
            self.treasury = FakeTreasury(
                self.ledger, self.wallet, fee_micro=manifest.treasury.fake_fee_micro,
                max_venice_per_window=manifest.treasury.max_venice_per_window,
            )
        else:
            self.treasury = Treasury(
                self.ledger,
                self.wallet,
                rail,
                provider=self.provider,
                fee_ceiling_micro=manifest.treasury.max_transfer_fee_micro,
                max_venice_per_window=manifest.treasury.max_venice_per_window,
            )
        self.wallet.bind_pots(self.treasury.pots)
        self.treasury.rail = JournalProxy(
            self.treasury.rail, self.ledger, "treasury.rail", deterministic=not self.live
        )
        self.provider = JournalProxy(
            self.provider,
            self.ledger,
            "provider",
            deterministic=isinstance(self.provider, (ScriptedProvider, FakeModel)),
        )
        self.market = JournalProxy(self.market, self.ledger, "market")
        self.sellers: dict[str, dict] = {}
        self.market_index: list[dict] | None = None
        self.unresolved_x402: dict[str, dict] = {}
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
        self.retired_routers: dict[str, RouterState] = {}
        for kind in self._routable_kinds():
            self._build_router(kind, "exp3", router_gamma)
        self.pending_exposure: dict[str, int] = {}  # antagonist decision handle -> opened event
        # antagonist handle -> which of the two exposure facts have arrived (A5)
        self.exposure_evidence: dict[str, dict[str, bool]] = {}
        # judge handle -> top-meta (handle, conformity) pairs awaiting the judge's payoff (A14)
        self.pending_meta: dict[str, list[tuple[str, float]]] = {}
        # judge handle -> (verdict beat baseline, event, forecast handle), pruned by backstop
        self.verdict_outcomes: dict[str, tuple[int, int, str]] = {}
        self.consequence_mix: float = self.ev.consequence_share  # live sampling actuator (A14)
        self.sampling_history: list[dict[str, Any]] = []
        # learning-death grant: the window it is live for and the assemblies that spent it (A13)
        self.novelty_grant: dict[str, Any] = {"window": None, "consumed": []}
        self.delivered_seen: dict[str, int] = {
            st.learner.id: 0 for st in self._all_router_states()
        }
        self.vote_handles: dict[str, str] = {}
        self.order_intents: dict[str, dict] = {}
        self.voted_amendments: set[str] = set()
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
            "description": "Submit a transfer between venue and reserve, or to_venice from "
            "reserve in a fixed $5 tranche, within treasury.max_venice_per_window. "
            "Principal stays held "
            "until receipt-confirmed arrival. The result carries references or a refusal reason.",
            "args_schema": {
                "type": "object",
                "properties": {
                    "direction": {"enum": ["to_reserve", "to_venue", "to_venice"]},
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
        coin = manifest.exchange.coins[0]
        examples = {
            "venue.candles": [{"coin": coin, "interval": "1m", "n": 20}],
            "venue.order_book": [{"coin": coin, "depth": 5}],
            "venue.funding_history": [{"coin": coin, "n": 10}],
            "venue.open_orders": [{}], "venue.positions": [{}],
            "venue.place_market": [{"coin": coin, "side": "buy", "size": "0.001"}],
            "venue.place_limit": [{"coin": coin, "side": "buy", "size": "0.001", "price": "100"}],
            "venue.cancel": [{"coin": coin, "order_id": "1"}],
            "venue.close": [{"coin": coin}, {"coin": coin, "size": None}],
            "venue.set_leverage": [{"coin": coin, "leverage": 1}],
            "treasury.transfer": [{"direction": direction, "usd": amount}
                                  for direction in ("to_reserve", "to_venice")
                                  for amount in ("5", 5)],
            "catalogue.search": [{"substring": "flash", "limit": 20}],
            "market.discover": [{"query": "inference", "limit": 20}],
        }
        for tool_id, spec in self.tool_specs.items():
            spec["args_schema"]["examples"] = examples[tool_id]
        self.tool_runner = JournalProxy(ToolRunner(), self.ledger, "sandbox")
        available = self.tool_runner.available
        self.ledger.append({"kind": "sandbox.availability", "available": available})
        self.tool_jail_available = available
        # A11: population measurements run in the same jail, under the tool limits.
        self.observation_runner = JournalProxy(ObservationRunner(), self.ledger, "observation")
        self.registered_observations: dict[str, dict[str, Any]] = {}
        # A10: a learner over an assembly's own declared action set, and its open rounds.
        self.assembly_learners: dict[str, Any] = {}
        self.assembly_rounds: dict[str, str] = {}
        self.charter_book = CharterBook(self.ledger, self.charter)
        self.pending_votes: list[Any] = []  # committees awaiting tally

        # prices (spec v0.6 section 8.1): regions are parsed here, the controller only prices
        pr = manifest.prices
        self.controller = ImmunePriceController(
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
        for card_id, value in manifest.charter_prices:
            self.controller.register_pending(card_id)
            self.priced.add(card_id)
            self.controller.set_price(card_id, value, amendment_id="manifest:edition1")
        self.rolling: dict[str, float] = {}
        self.unparsed_logged: set[tuple[str, int]] = set()
        self.window = MeasureWindow(0, self.wallet.balance)
        self.card_samples = CardSamples()

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

    def _register_seed_contracts(self) -> None:
        for tier in self.m.models:
            price = self.prices.price(tier.id)
            if tier.id.startswith("x402:"):
                price, seller = self._seller_price(tier.id)
                self.registry.register(_model_contract(tier.id, price, "x402"))
                self._record_seller(tier.id, price, seller)
                continue
            self.registry.register(_model_contract(tier.id, price, tier.provider))
        for seed in self.m.assemblies:
            self.registry.register(
                _assembly_contract(seed.id, seed.role, seed.accepts, seed.max_tokens)
            )
        # A11: the seed catalogue is registered the same way the population's own
        # measurements are, so the vocabulary has one registry and one versioning
        # rule. What a seed does not carry is code: the kernel measures it natively.
        for observation in seed_book().all():
            self.registry.register(
                Contract(
                    id=f"observation:{observation.id}",
                    version=1,
                    kind="observation",
                    description=observation.description,
                    input_schema={"type": "object", "description": "public per-window facts"},
                    output_schema={"type": "number", "minimum": observation.unit_range[0],
                                   "maximum": observation.unit_range[1]},
                    price=PriceSpec({}),
                    permissions=frozenset(),
                    resource_bounds=ResourceBounds(),
                )
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
