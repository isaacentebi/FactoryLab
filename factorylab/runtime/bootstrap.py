"""Runtime bootstrap method group."""

from __future__ import annotations

import hashlib
import json
import random
import secrets
from collections import deque
from decimal import Decimal
from typing import Any

from factorylab.charter.book import CharterBook
from factorylab.charter.charter import Charter
from factorylab.charter.controller import CardRegion
from factorylab.charter.measurement import CardSamples
from factorylab.cortex.assembly import Assembly, AssemblySpec
from factorylab.cortex.tools import ObservationRunner, ToolRunner
from factorylab.kernel.budget import BudgetBook
from factorylab.kernel.events import Bus, Event
from factorylab.kernel.ledger import Ledger, LedgerLock
from factorylab.kernel.money import money_to_usd
from factorylab.kernel.queue import DecisionQueue
from factorylab.kernel.registry import Contract, PriceSpec, Registry, ResourceBounds
from factorylab.kernel.reserve import NoveltyReserve
from factorylab.kernel.termination import Termination
from factorylab.kernel.timing import TimingRegistry, UpwardBuffer
from factorylab.kernel.wallet import DripSchedule, ReleaseSchedule, Wallet
from factorylab.runtime import release, witness
from factorylab.runtime.cadence import GovernanceCadence
from factorylab.runtime.cards import forecast_weight
from factorylab.runtime.cascade import CascadeGate
from factorylab.runtime.compute import ContractConsequences
from factorylab.runtime.feedback import PendingJudgement
from factorylab.runtime.immune import ImmunePriceController
from factorylab.runtime.live import LiveClock, LiveVenue, Reconciler, build_provider
from factorylab.runtime.observations import seed_book
from factorylab.runtime.pricing import MeasureWindow
from factorylab.runtime.resume import JournalProxy, RecoveryJournal
from factorylab.runtime.routing import RouterState
from factorylab.runtime.seller import configured_facilitator
from factorylab.runtime.shared import SimClock, _to_plain
from factorylab.runtime.summary import RunStats, _assembly_contract, _model_contract
from factorylab.runtime.worlds import WorldManifest
from factorylab.settlement import (
    ConsequenceStanding,
    ForecastBook,
    Observer,
    PrevalenceBaseline,
    Settler,
)
from factorylab.settlement.consequence import FillCursor
from factorylab.world.clock import ClockIterator, ClockSource
from factorylab.world.exchange import (
    FakeExchange,
    HyperliquidExchange,
    bind_launch_nonce,
    live_exchange,
)
from factorylab.world.market import MultiProvider, X402Provider
from factorylab.world.metering import BillSettlement, Meter
from factorylab.world.models import FakeModel, TokenPrice
from factorylab.world.scripted import ScriptedProvider
from factorylab.world.venue_tools import VenueTools


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
            self.exchange = live_exchange(manifest.exchange, HyperliquidExchange)
        else:
            shocks: dict[int, dict[str, Decimal]] = {}
            for sh in manifest.exchange.shocks:
                shocks.setdefault(sh.step, {})[sh.coin] = Decimal(sh.multiplier)
            self.exchange = FakeExchange(
                seed=manifest.exchange.seed,
                coins=manifest.exchange.coins,
                spot_pairs=manifest.exchange.spot_pairs,
                start_cash_usd=money_to_usd(self.initial),
                shocks=shocks,
            )
        # One launch, one venue identity space. The nonce is drawn here so the
        # pre-launch snapshot already carries it and replay reproduces the exact
        # Launch event; resume overwrites it from the checkpoint. A live venue is
        # shared across launches, so the nonce is random; the deterministic venue
        # is private to its run, so the nonce follows the manifest and the seed and
        # identical inputs still produce a byte-identical diary.
        if self.live:
            self.launch_nonce = secrets.token_hex(16)
        else:
            self.launch_nonce = hashlib.sha256(
                f"{manifest.manifest_hash}:{self.seed}".encode()).hexdigest()[:32]
        bind_launch_nonce(self.exchange, self.launch_nonce)
        # One launch, one release. The digest of the executing code is drawn beside
        # the nonce so the pre-launch snapshot carries it and the Launch event ledgers
        # it; restore compares the saved digest with the running one and refuses a
        # different release under the old identity (C4).
        self.release_digest = release.release_digest()
        # One launch, one x402 facilitator. The seller settles every paid call
        # through it, so it is read from the environment here, once, ledgered in
        # the Launch event and every checkpoint, compared on restore
        # (``facilitator_mismatch``) and read back from the ledger by the seller.
        self.facilitator_url = configured_facilitator()
        # One launch, one witness requirement (edition 3, R3-C). Whether a receiver
        # was configured when this world launched, and which receiver it was (its
        # URL's hash: the address itself never enters the diary), are part of the
        # launch identity. Ledgered in ``Launch`` and carried in every checkpoint,
        # so unsetting the environment variable afterwards cannot remove the
        # receiver's veto: a resume with no receiver refuses (``witness_required``)
        # and one naming a different receiver refuses (``witness_mismatch``).
        self.witness_required = witness.receiver_identity() is not None
        self.witness_receiver = witness.receiver_identity()
        # The diary this state descends from (the hash of its first sealed record).
        # None until the ledger has one; restore sets it from the checkpoint so a
        # twin restored in memory still names the diary it came from.
        self.diary_id: str | None = None
        if provider is None:
            provider = build_provider(manifest)
        self.provider = provider if provider is not None else ScriptedProvider()
        if isinstance(self.provider, ScriptedProvider) and manifest.exchange.spot_pairs:
            self.provider.spot_pair = manifest.exchange.spot_pairs[0]
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
                # A Venice purchase is confirmed on the chain's debit; the diary's own
                # metered spend since the purchase started is recorded beside the
                # advisory balance so a lost acknowledgment stays explainable (C5).
                rail.metered_usage_since = self._venice_usage_since
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
                if ledger.event_times()["launch"]:
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
        # Locked backing and its release schedule come from the manifest; the offsets
        # are anchored to the ledgered Launch timestamp when the world launches.
        endowment = manifest.endowment
        releases = (ReleaseSchedule(tuple(endowment.releases))
                    if endowment.locked_micro else None)
        self.wallet = Wallet(self.initial, self.ledger, schedule, clock_ns=self.clock,
                             balance_floor_micro=manifest.termination.balance_floor_micro,
                             reported_cost_multiple=manifest.treasury.reported_cost_multiple,
                             locked_micro=endowment.locked_micro, release_schedule=releases)
        self.bus = Bus(self.ledger)
        self.termination = Termination(ledger=self.ledger, bus=self.bus, clock_ns=self.clock)
        self.registry = Registry(self.ledger)
        self.queue = DecisionQueue(self.ledger, clock_ns=self.clock)
        self.reserve = NoveltyReserve(
            manifest.novelty.share,
            manifest.novelty.window_ns,
            has_history=self._registration_has_history,
            ledger=self.ledger,
            clock_ns=self.clock,
        )
        self.wallet.bind_novelty(self.reserve, self._novelty_compute)
        self.cadence = GovernanceCadence(
            self.ledger,
            sample=manifest.timing.cadence_sample,
            min_ratio=manifest.timing.min_ratio,
            backstop=self.ev.consequence_backstop_ticks,
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
        # Edition 3 (C3): the charter's cards, not the settler, say what a judge's
        # forecasts are worth. A claim about a return in a scope no card answers
        # for carries no weight; with no scoped card every claim counts equally.
        self.settler = Settler(
            self.book, self.queue, self.standing, self.baseline, self.observer,
            weight_for=lambda forecast: forecast_weight(
                self.charter, self.return_kinds.get(forecast.about_handle)),
        )
        self.consequences = ContractConsequences(
            self.ledger, self.ev.consequence_backstop_ticks, self)
        self.consequence_fills = FillCursor(self.ledger, start_ns=self.clock.now_ns)

        # world
        self.exchange = JournalProxy(
            self.exchange,
            self.ledger,
            "exchange",
            deterministic=isinstance(self.exchange, FakeExchange) and not self.live,
        )
        from factorylab.world.venue_tools import seed_markets

        seed_markets(self.exchange, manifest.exchange)
        # Fills before launch belong to nobody; funding uses the same launch boundary.
        self.venue = (
            LiveVenue(self.exchange, last_fill_ns=self.clock.now_ns, ledger=self.ledger,
                      last_funding_ns=self.clock.now_ns, markets=self._trading_markets)
            if self.live
            else None
        )
        self.prices = manifest.price_table()
        self.meter = Meter(self.wallet)
        # Each seat spends through its own entitlement (C10); the pool is the remainder.
        self.budget = BudgetBook(self.wallet, self.ledger, clock_ns=self.clock,
                                 base_share=str(manifest.endowment.base_share))
        from factorylab.world.treasury import FakeTreasury, Treasury

        if not self.live:
            self.treasury = FakeTreasury(
                self.ledger, self.wallet, exchange=self.exchange,
                fee_micro=manifest.treasury.fake_fee_micro,
                max_venice_per_window=manifest.treasury.max_venice_per_window,
                clock_ns=self.clock,
            )
        else:
            self.treasury = Treasury(
                self.ledger,
                self.wallet,
                rail,
                provider=self.provider,
                fee_ceiling_micro=manifest.treasury.max_transfer_fee_micro,
                max_venice_per_window=manifest.treasury.max_venice_per_window,
                max_forward_fees_per_window=manifest.treasury.max_forward_fees_per_window,
                forward_wait_windows=manifest.treasury.forward_wait_windows,
                clock_ns=self.clock,
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
        # Uncertain bills settle from the provider's own balance, read through the
        # journal like every other provider read so replay reproduces it.
        self.bill_settlement = BillSettlement(self._provider_balance, record=self._record_market)
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
        self.retired_assemblies: set[str] = set()
        self.retirement_proposals: dict[str, dict] = {}
        self.return_kinds: dict[str, str] = {}
        self.return_bindings: dict[str, dict] = {}
        self.return_events: dict[str, Event] = {}
        self.decision_subjects: dict[str, str] = {}
        self.event_schemas: dict[str, dict] = {}
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
                    emits=a.emits,
                    schemas=a.schemas,
                    # Edition 3, C5: a seat may carry its own lens. Until C1's working
                    # state lands the lens is the seat's prompt; a manifest that names
                    # none keeps the population-wide seed prompt.
                    **({"system_prompt": a.system_prompt}
                       if a.system_prompt is not None else {}),
                )
            )
        if not self.ledger.bootstrap:
            # Genesis: base_share of the unlocked launch balance, equally across the
            # seeded seats; the remainder is the unallocated pool. A resume restores
            # the book from its checkpoint instead.
            self.budget.genesis([a.id for a in manifest.assemblies])

        # nervous system
        self.router_gamma = router_gamma
        self.routers: dict[str, list[RouterState]] = {}
        self.retired_routers: dict[str, RouterState] = {}
        for kind in self._routable_kinds():
            self._build_router(kind, "exp3", router_gamma)
        self.pending_exposure: dict[str, int] = {}  # antagonist decision handle -> opened event
        # antagonist handle -> which of the two exposure facts have arrived
        self.exposure_evidence: dict[str, dict[str, bool]] = {}
        # judge handle -> top-meta (handle, conformity) pairs awaiting the judge's payoff
        self.pending_meta: dict[str, list[tuple[str, float]]] = {}
        # judge handle -> (verdict beat baseline, event, forecast handle), pruned by backstop
        self.verdict_outcomes: dict[str, tuple[int, int, str]] = {}
        self.consequence_mix: float = self.ev.consequence_share  # live sampling actuator
        self.sampling_history: list[dict[str, Any]] = []
        # learning-death grant: the window it is live for and the assemblies that spent it
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
        self.spot_inventory = {}
        if not self.ledger.bootstrap:
            # Launch holdings have no author. Their basis is the observed launch mark,
            # so a later closer is credited only for the world's subsequent price move.
            balances = self.exchange.account().spot_balances
            held = [balance for balance in balances if balance.coin != "USDC" and balance.total]
            mids = self.exchange.mids() if held else {}
            for balance in held:
                coin = f"{balance.coin}/USDC"
                if coin not in mids:
                    self._ledger_lock.close()
                    raise ValueError(f"launch spot inventory has no price: {coin}")
                size, px = str(balance.total), str(mids[coin])
                table = self.consequences.table.seed_spot(coin, size, px)
                self.consequences._apply("spot_seed", {"coin": coin, "size": size,
                                                       "entry_px": px}, table)
                self.ledger.append({"kind": "spot.inventory", "coin": coin,
                                    "size": size, "entry_px": px, "source": "launch"})
                self.spot_inventory[coin] = (Decimal(size), Decimal(px))
        self.notes: dict[str, dict] = {}
        # The artifact archive (C9): records in the ledger, bytes beside it by hash.
        from factorylab.kernel.artifacts import ArtifactStore, artifact_root

        self.artifacts = ArtifactStore(
            self.ledger, root=artifact_root(ledger_path) if ledger_path else None,
            clock_ns=self.clock,
        )
        # Continuity (C1): a head pointer per seat over the archive, and an inbox of
        # settled consequences addressed to the seat that decided them. These replace
        # the three-entry memory deque, which lost a decision before its outcome landed.
        from factorylab.runtime.continuity import OutcomeInbox, WorkingState

        self.working_state = WorkingState(self.artifacts, self.ledger, self.clock)
        self.outcomes = OutcomeInbox(self.artifacts, self.ledger, self.clock)
        # R3-F: what a seat said is retained until that decision's last consequence
        # settles or the seat retires. This is the inbox's only way to ask.
        self.outcomes.consequences_open = lambda handle: (
            self.consequences.account_open(handle)
            or self.outcomes.seat_of(handle) not in self.retired_assemblies)
        if not self.ledger.bootstrap:
            # The manifest may hand a seat its first head — a lens, a method, a
            # starting hypothesis. It is the initial value of a pointer the seat
            # owns from then on, not a field the world keeps rewriting. A resume
            # restores the head the seat actually holds instead.
            for a in manifest.assemblies:
                if getattr(a, "initial_state", None):
                    self.working_state.put(a.id, dict(a.initial_state))
        self.handle_to_assembly: dict[str, str] = {}
        self.tool_specs: dict[str, dict[str, Any]] = {}  # tool id -> spec dict (world block)
        self.population_tools: dict[str, Any] = {}
        self.tool_owner: dict[str, str] = {}  # population tool id -> proposing assembly id
        # C10 routing evidence: each seat's last rendered ceiling and the world size then.
        self.seat_ceilings: dict[str, dict[str, int]] = {}
        self.entitlement_bridges: dict[str, int] = {}  # handle -> pool-backed cover, one call
        self.venue_tools = VenueTools(
            self.exchange,
            coins=manifest.exchange.coins,
            spot_pairs=manifest.exchange.spot_pairs,
        )
        for spec in self.venue_tools.contracts():
            self.tool_specs[spec.id] = {
                "id": spec.id,
                "description": spec.description,
                "args_schema": _to_plain(spec.args_schema),
                "price_micro_per_call": spec.price_micro_per_call,
                "kind": spec.kind,
            }
            if spec.id in self.venue_tools.PUBLIC_READS:
                self.tool_specs[spec.id]["price_micro_per_call"] = (
                    manifest.connectors.call_price_micro)
        self.tool_specs["treasury.transfer"] = {
            "id": "treasury.transfer",
            "description": "Move USDC spot_to_perps or perps_to_spot, between venue and reserve, "
            "or to_venice from "
            "reserve in a fixed $5 tranche, within treasury.max_venice_per_window. "
            "Principal stays held "
            "until receipt-confirmed arrival. The result carries references or a refusal reason. "
            "to_reserve needs spot HYPE in the venue account for the Core gas charge (buy it on "
            "HYPE/USDC); its Base mint is self-paid when the reserve holds ETH, otherwise Circle "
            "forwards it for the fee quoted in pots.gas.",
            "args_schema": {
                "type": "object",
                "properties": {
                    "direction": {"enum": ["to_reserve", "to_venue", "to_venice",
                                           "spot_to_perps", "perps_to_spot"]},
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
            "venue.instruments": [{}], "venue.mids": [{}], "venue.funding": [{}],
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
            "note.put": [{"key": "shared-plan", "text": "What the last window showed."}],
            "note.get": [{"key": "shared-plan"}],
            "artifact.get": [{"sha": "0" * 64}],
            "outcome.get": [{"outcome_id": "outcome:1"}, {"handle": "decision-1"}],
        }
        from factorylab.runtime.notes import specs as note_specs

        self.tool_specs.update(note_specs(manifest.notes))
        self.tool_specs["artifact.get"] = {
            "id": "artifact.get",
            "description": "Read an archived artifact by its sha256: your own working "
            "state, an artifact you wrote, an artifact published with public: true, or "
            "the private state of a program in your own lineage. Anything else is "
            "refused with artifact_private. The read is free and ledgered. Returns "
            "owner, kind, bytes and text (base64 for binary), up to 64 KiB.",
            "args_schema": {
                "type": "object",
                "properties": {"sha": {"type": "string", "minLength": 64, "maxLength": 64}},
                "required": ["sha"],
                "additionalProperties": False,
            },
            "price_micro_per_call": 0,
            "kind": "artifact",
        }
        self.tool_specs["outcome.get"] = {
            "id": "outcome.get",
            "description": "Read one item of your own outcome inbox by its exact "
            "outcome_id (the address carried on every item, including items older than "
            "the few your request carries inline). One decision can settle into several "
            "outcomes, so handle is a fallback only: it returns the oldest item of that "
            "decision you have not read, and says so. Returns what you said then, the "
            "outcome, when it was observed, the financial delta, an evidence pointer and "
            "the other outcome_ids on that decision. Free and ledgered; a kernel read, "
            "never a model call.",
            "args_schema": {
                "type": "object",
                "properties": {
                    "outcome_id": {"type": "string", "minLength": 1, "maxLength": 128},
                    "handle": {"type": "string", "minLength": 1, "maxLength": 128},
                },
                "additionalProperties": False,
            },
            "price_micro_per_call": 0,
            "kind": "outcome",
        }
        if getattr(getattr(manifest, "prompt", None), "mode", "reference") == "compact":
            from factorylab.cortex.schematics import INSTITUTION_SECTIONS

            self.tool_specs["world.read"] = {
                "id": "world.read",
                "description": "Read one current public institutional section by its "
                "directory handle. Returns its authoritative contract, not private "
                "participant state. Free; the next model call still costs inference.",
                "args_schema": {
                    "type": "object",
                    "properties": {"section": {"type": "string",
                                                "enum": sorted(INSTITUTION_SECTIONS)}},
                    "required": ["section"], "additionalProperties": False,
                },
                "price_micro_per_call": 0, "kind": "institution",
            }
            examples["world.read"] = [{"section": "composition"}]
        if getattr(manifest.tools, "address_enabled", False):
            from factorylab.runtime.address import specs as address_specs

            # The transport is priced like every other population tool this world
            # publishes, so addressing is a call a seat pays for out of its own
            # entitlement rather than a free channel that rewards volume.
            self.tool_specs.update(
                address_specs(manifest.tools.population_tool_micro_per_call))
            examples["address.send"] = [{"recipient": "another-live-participant",
                                        "text": "Your funding series is the one I lack."}]
        # Every published tool carries examples its own schema accepts (B1). Stamping
        # after the whole seed set is assembled keeps that total: a seed tool added
        # without an example fails at launch rather than reaching the population.
        for tool_id, spec in self.tool_specs.items():
            spec["args_schema"]["examples"] = examples[tool_id]
        self.tool_runner = JournalProxy(ToolRunner(), self.ledger, "sandbox")
        available = self.tool_runner.available
        self.ledger.append({"kind": "sandbox.availability", "available": available})
        self.tool_jail_available = available
        # Program seats (C8) run in the same jail; the journal name ``sandbox.run`` is
        # already a recorded read, so a resume replays a program's reply, never its code.
        from factorylab.cortex.sandbox import ProgramRunner

        self.program_runner = JournalProxy(ProgramRunner(available=available), self.ledger,
                                           "sandbox")
        # Population measurements run in the same jail, under the tool limits.
        self.observation_runner = JournalProxy(ObservationRunner(), self.ledger, "observation")
        self.registered_observations: dict[str, dict[str, Any]] = {}
        # A learner over an assembly's own declared action set, and its open rounds.
        self.assembly_learners: dict[str, Any] = {}
        self.assembly_rounds: dict[str, str] = {}
        self.charter_book = CharterBook(self.ledger, self.charter)
        self.pending_votes: list[Any] = []  # committees awaiting tally

        # prices: regions are parsed here, the controller only prices
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
        # A verdict commitment is closed out once and never re-opened. The judge's
        # payoff forecast stays pending in the book after an unread close, so
        # without these the per-event commitment pass would re-create the same
        # commitment and close it unread again on every event. They are snapshot
        # state (``resume._RUNTIME_FIELDS``): a restored runtime re-emits nothing.
        self.verdicts_closed_out: set[str] = set()
        self.verdicts_graded: set[str] = set()
        self.balance_at: list[int] = [self.wallet.balance]  # index = event number
        self.events_log: list[dict[str, Any]] = [{"kind": "Launch", "payload": {}}]
        self.last_closure_ns = -1
        self.reserve_window_start: int | None = None
        self.internal: deque[Event] = deque()
        self.n = 0
        self.emitted = 0
        self.insolvency_count = 0
        self.dormancy: dict[str, Any] | None = None  # C2: set while paid cognition is paused
        self.registration_feedback: deque[dict[str, Any]] = deque(maxlen=8)
        self._compute_routed = False
        self._compute_unaffordable = False
        self.world_consumed = 0
        self.ticks_consumed = 0
        # Venue effects by custody, per decision, until its outcome is settled: the
        # consequence line reports provider cost and venue delta separately rather
        # than one net (edition 3, C5).
        self.venue_deltas: dict[str, dict[str, int]] = {}
        self.drips_consumed = 0
        # The venue reads prompts are built from, each held for the tick that read it:
        # the listing (#89), the mid prices and the account state. Not resumable
        # state: a resumed runtime reads afresh and records that read.
        self._instruments_memo: tuple[int, dict] | None = None
        self._mids_memo: tuple[int, dict] | None = None
        self._account_memo: tuple[int, Any] | None = None
        self.started = False

    def _venice_usage_since(self, since_ns: int) -> int:
        """Metered Venice spend since ``since_ns``, summed from the diary's invocation records.

        Read only at confirmation time, once per purchase, so the walk over the
        diary is paid rarely. The value is evidence in the confirmation event, not a
        condition of it.
        """
        total = 0
        for item in self.ledger._iter_items():
            if (item.get("kind") == "invocation" and item.get("ts", 0) >= since_ns
                    and str(item.get("served_by") or "").startswith("venice:")):
                total += int(item.get("cost") or 0)
        return total

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
                _assembly_contract(seed.id, seed.role, seed.accepts, seed.max_tokens,
                                   emits=seed.emits, schemas=seed.schemas)
            )
        # The seed catalogue is registered the same way the population's own
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
