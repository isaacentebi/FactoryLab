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
from factorylab.charter.controller import CardRegion, PriceController
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
from factorylab.kernel.wallet import ReleaseSchedule, Wallet
from factorylab.runtime import release, witness
from factorylab.runtime.cadence import GovernanceCadence
from factorylab.runtime.cascade import CascadeGate
from factorylab.runtime.compute import ContractConsequences
from factorylab.runtime.feedback import PendingJudgement
from factorylab.runtime.immune import thrash_controller
from factorylab.runtime.live import (
    LiveClock,
    LiveVenue,
    Reconciler,
    WallClock,
    build_provider,
    wall_paced,
)
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

#: Why a live hybrid Venice world will not start without the capital-loop opt-in.
CAPITAL_LOOP_REFUSED = (
    "this world spends real Base mainnet USDC on Venice (treasury.venice_network); start it "
    "only with scripts/edition4_rehearsal.py --capital-loop "
    "(docs/architecture/capital-loop-rehearsal.md)")


#: Why a world whose treasury rail signs with the mainnet reserve key will not start
#: without a diary on disk.
MAINNET_RAIL_REFUSED = (
    "mainnet_rail_requires_a_ledger: this world's treasury rail signs with the mainnet "
    "reserve key; run it with --ledger, so the reserve's record can name the diary its "
    "transfers are booked in and a cancel can tell whether it has ended")


def mainnet_rail(manifest: WorldManifest) -> bool:
    """Whether a live world's treasury rail signs with the mainnet reserve key: a
    reserve on a mainnet venue, or a hybrid rail's Venice leg on Base mainnet."""
    treasury = manifest.treasury
    return treasury.reserve_address is not None and (
        manifest.exchange.mainnet
        or getattr(treasury, "venice_network", None) == "base-mainnet")


class MainnetRailRequiresALedger(ValueError):
    """A live world with a mainnet treasury rail was given no ledger: nothing started."""


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
        router_gamma: float,
        provider: Any | None = None,
        market: X402Provider | None = None,
        exchange: Any | None = None,
        clock_source: Any | None = None,
        reconcile_every: int = 10,
        kill_at_end: bool = False,
        capital_loop: bool = False,
        _journal: RecoveryJournal | None = None,
        _lock: LedgerLock | None = None,
    ) -> None:
        self.live = manifest.exchange.kind != "fake"
        if (self.live and getattr(manifest.treasury, "venice_network", None) is not None
                and capital_loop is not True):
            # A manifest alone never switches on real-money mode: `factorylab run` or
            # `resume` of a hybrid world would sign mainnet top-ups with no rehearsal
            # guard around them. Only the capital-loop runner passes the opt-in, and it
            # is not checkpointed, so a resume must be asked for it again.
            from factorylab.world.evm import RailError

            raise RailError(CAPITAL_LOOP_REFUSED)
        # The venue read share is a load-time invariant, checked before anything is
        # written, for a manifest built in code as for one read from a file.
        problem = manifest.read_share_problem()
        if problem is None and _journal is None:
            # Genesis admits the retained private state cap against the host's free
            # disk once; the cap is fixed for the world's life, so a resume (which
            # arrives with its journal) is never refused for the host's disk since.
            problem = manifest.host_disk_problem(ledger_path)
        if problem is not None:
            raise ValueError(problem)
        if self.live and not ledger_path and _journal is None and mainnet_rail(manifest):
            # Every reserve-key entry of a world names its diary: without one, a used
            # authorization could never be shown booked (a false recovery), and a
            # cancel could never tell whether the world has ended. Refused before any
            # venue, key or file is touched.
            raise MainnetRailRequiresALedger(MAINNET_RAIL_REFUSED)
        self._ledger_lock = _lock or LedgerLock(ledger_path)
        # Where this process keeps the world's diary, and the host's one live Polymarket
        # reader when this world holds it: taken before the first event or replay, and
        # released when the world stops (``runtime/polymarket.py``, ``arm``).
        self.ledger_path = ledger_path
        self._polymarket_ip_lock = None
        self.m = manifest
        self.kill_at_end = kill_at_end
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
        # The factory's clock (essay II.IV.b-c; time audit T1-T3): measured loops and
        # the derived schedules of the loops they command, all in world ticks.
        from factorylab.runtime.clockwork import Clockwork

        self.clockwork = Clockwork(min_ratio=manifest.timing.min_ratio,
                                   jitter_fraction=manifest.timing.jitter_fraction,
                                   seed=self.seed, sample=manifest.timing.cadence_sample)
        # handle -> [opened tick, cutoff tick] for every decision not yet final.
        self.decision_ticks: dict[str, list[int]] = {}
        # World ticks consumed: the one clock domain every loop counts in (T3).
        self.ticks_consumed = 0
        # card id -> the tick its price last moved (time audit T2).
        self.card_clock: dict[str, int] = {}
        # card id -> its consecutive closed windows with no reading (wave 16, R10-f):
        # a dark card is published to governance, never coerced.
        self.card_unmeasured: dict[str, int] = {}
        # Whether a governance tier fits between the slowest loop and the world (T7).
        self.governance_viable = True
        # event kind -> the tick a grown menu started waiting for its epoch (T6).
        self.pending_epochs: dict[str, int] = {}
        # Where the treasury caps' own wall-clock windows are counted from (T1, T13).
        self.cap_anchor_ns: int | None = None
        self.clock = SimClock(0) if _journal is None else _journal.clock
        if self.live and _journal is None:
            self.clock.now_ns = (
                self.tick_clock.now_ns() if wall_paced(self.tick_clock)
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
        from pathlib import Path as _Path

        from factorylab.runtime.capital_loop import ReserveGuard
        from factorylab.world.treasury import UnconfiguredRail

        # Every EIP-3009 authorization this world signs with the reserve key is written
        # ahead to the reserve's record, under its lock, or never signed
        # (x402.sign_transfer_authorization): a capital-loop run on the same reserve can
        # then neither overlap it nor miss what it authorized. The entry names this
        # world's exact diary (``--ledger runs/foo.jsonl`` included), where a used
        # authorization of its must be booked.
        ledger = _Path(ledger_path).resolve() if ledger_path else None
        run_dir = ledger.parent if ledger is not None else None
        if isinstance(self.market, X402Provider) and self.market.guard is None:
            self.market.guard = ReserveGuard("x402_purchase", run_dir=run_dir, ledger=ledger)

        if self.live:
            if manifest.treasury.reserve_address is not None:
                from factorylab.world.treasury_rails import HybridRail, LiveRail

                # A hybrid capital-loop rehearsal buys real Venice credit on Base mainnet
                # and pays for it from the testnet pots through a shadow leg (II.IV).
                hybrid = getattr(manifest.treasury, "venice_network", None) == "base-mainnet"
                rail = (HybridRail if hybrid else LiveRail)(self.exchange, manifest.treasury)
                # A Venice purchase is confirmed on the chain's debit; the diary's own
                # metered spend since the purchase started is recorded beside the
                # advisory balance so a lost acknowledgment stays explainable (C5).
                rail.metered_usage_since = self._venice_usage_since
                # The capital-loop runner replaces this with its held lock's record.
                rail.bind_guard(ReserveGuard("treasury", run_dir=run_dir, ledger=ledger))
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
        # Locked backing and its release schedule come from the manifest; the offsets
        # are anchored to the ledgered Launch timestamp when the world launches.
        endowment = manifest.endowment
        releases = (ReleaseSchedule(tuple(endowment.releases))
                    if endowment.locked_micro else None)
        self.wallet = Wallet(self.initial, self.ledger, None, clock_ns=self.clock,
                             balance_floor_micro=manifest.termination.balance_floor_micro,
                             reported_cost_multiple=manifest.treasury.reported_cost_multiple,
                             locked_micro=endowment.locked_micro, release_schedule=releases)
        self.bus = Bus(self.ledger)
        self.termination = Termination(ledger=self.ledger, bus=self.bus, clock_ns=self.clock)
        self.registry = Registry(self.ledger)
        self.queue = DecisionQueue(self.ledger, clock_ns=self.clock)
        self.reserve = NoveltyReserve(
            manifest.novelty.share,
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

        # settlement
        self.book = ForecastBook(self.ledger)
        self.baseline = PrevalenceBaseline()
        self.standing = ConsequenceStanding(self.ev.min_coverage)
        self.observer = Observer()
        # Every settled claim counts once: the outside signal "sits outside the
        # factory's input entirely" (essay II.III.b), so no charter card weights it
        # (ruling R1 deleted ``settlement.weights``).
        self.settler = Settler(self.book, self.queue, self.standing, self.baseline,
                               self.observer)
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
        # Every answered venue read is kept for the rest of its tick, so an identical
        # seat read is answered without a request (``ComputeMixin._tick_answer``).
        self._tick_reads = None
        self.exchange.observer = self._observe_venue_answer
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
                venice_shadow_sink=getattr(manifest.treasury, "venice_shadow_sink", None),
                max_venice_total_micro=getattr(manifest.treasury, "max_venice_total_micro",
                                               None),
                venice_reserve_floor_micro=getattr(manifest.treasury,
                                                   "venice_reserve_floor_micro", None),
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
                forward_wait_ticks=manifest.treasury.forward_wait_ticks,
                clock_ns=self.clock,
                max_venice_total_micro=getattr(manifest.treasury, "max_venice_total_micro",
                                               None),
            )
        if manifest.polymarket.enabled:
            from factorylab.runtime.polymarket import pots_view

            # The polymarket pot sits beside the treasury's pots, never inside them.
            self.wallet.bind_pots(lambda: pots_view(self))
        else:
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
        # Time audit T8: the safety path reads wall time between model calls, journaled.
        self.wall = JournalProxy(WallClock(lambda: self.tick_clock, self.clock), self.ledger,
                                 "wall", deterministic=not self.live)
        self._safety_ns = self.clock.now_ns
        self._safety_stop: str | None = None
        # Uncertain bills settle from the provider's own balance, read through the
        # journal like every other provider read so replay reproduces it.
        self.bill_settlement = BillSettlement(self._provider_balance, record=self._record_market)
        self.sellers: dict[str, dict] = {}
        self.market_index: list[dict] | None = None
        self.unresolved_x402: dict[str, dict] = {}
        self.catalogue: dict[str, TokenPrice] | None = None
        # Provider-native output limits are launch evidence, separate from both
        # pricing and the combined context window.  Checkpoints retain this map so
        # a resumed world cannot be steered by a provider changing its catalogue.
        self.catalogue_completion_limits: dict[str, int | None] = {}
        if not self.ledger.bootstrap and hasattr(self.provider, "catalogue"):
            try:
                entries = list(self.provider.catalogue())
                self.catalogue = {e.id: e.price() for e in entries}
                self.catalogue_completion_limits = {
                    e.id: self._advertised_completion_limit(e) for e in entries
                }
            except Exception:  # catalogue unavailable: model proposals will be rejected
                self.catalogue = None
                self.catalogue_completion_limits = {}

        if not self.ledger.bootstrap:
            self._register_seed_contracts()
        self.assemblies: dict[str, Assembly] = {}
        self.retired_assemblies: set[str] = set()
        # The retired ids, oldest retirement first: whose kept state is released first
        # when a private-state write needs room under the cap (``[storage]``).
        self.retirement_order: list[str] = []
        # id -> its lineage key: the registration serial a new id draws (the seeds
        # take the first ones), kept across its versions, since only its owner may
        # re-version it. id -> the lineage key of the seat that registered its current
        # version (None: no known seat); a seed is in none, so only it owns itself.
        self.lineage_keys: dict[str, int] = {
            a.id: index + 1 for index, a in enumerate(manifest.assemblies)}
        self.registration_serial = len(manifest.assemblies)
        self.registrants: dict[str, int | None] = {}
        self.retirement_proposals: dict[str, dict] = {}
        self.return_kinds: dict[str, str] = {}
        self.return_bindings: dict[str, dict] = {}
        self.return_events: dict[str, Event] = {}
        self.decision_subjects: dict[str, str] = {}
        self.event_schemas: dict[str, dict] = {}
        for a in manifest.assemblies:
            # During recovery these temporary assemblies are cleared and replaced
            # by their resolved checkpoint specs.  Do not consult today's catalogue.
            max_tokens = (
                512 if self.ledger.bootstrap and a.max_tokens is None
                else self._resolve_max_tokens(a.model_id, a.max_tokens)
            )
            self._instantiate(
                AssemblySpec(
                    id=a.id,
                    version=1,
                    model_id=a.model_id,
                    max_tokens=max_tokens,
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
            self._build_router(kind, self._seed_learner_kind(kind), router_gamma)
        self.pending_exposure: dict[str, int] = {}  # antagonist decision handle -> opened tick
        # Exposure decisions whose seat answered status: cannot -> the reason it gave:
        # left ungraded, they settle declined, priced as an abstention (ruling R9).
        self.declined_exposures: dict[str, str] = {}
        # The reward chain (ruling R1). Antagonist handle -> the consequence scores of
        # the judges scored on its return, until its exposure settles.
        self.exposure_scores: dict[str, list] = {}
        # Each judge's consequence scores on ordinary (non-antagonist) returns, as
        # [sum, count]: the centre its exposures are measured from (``exposure_score``).
        self.judge_ordinary: dict[str, list] = {}
        # Adversarial judges' counter-verdicts awaiting the world's measurement of the
        # return they re-judged (``FeedbackMixin._settle_counters``).
        self.pending_counters: dict[str, dict[str, Any]] = {}
        # First-tier judge handle -> the world it was shown, frozen at its verdict, for
        # an adversarial judge drawn when that verdict is given (``_counter_step``).
        self.verdict_views: dict[str, dict[str, Any]] = {}
        # Whether the live roster keeps its evaluator seats a strict majority, and the
        # tick that was last measured (``_watch_evaluator_majority``).
        self.evaluator_majority: bool | None = None
        # The chaos faults drawn for the tick now running (``runtime.chaos``).
        self.chaos_tick: dict[str, Any] = {}
        # Judged return -> [[judge handle, verdict], ...] that arrived while this event
        # was routed; settled on their mean once routing is done.
        self.arrived_verdicts: dict[str, list[list]] = {}
        # Decision handle -> (its consequence score or None, the tick it closed): what a
        # meta that graded it predicted, kept for one that grades it later.
        self.consequence_scores: dict[str, tuple[float | None, int]] = {}
        # Judged return -> its measurement once final, so every verdict about it is
        # scored against one fact; and the mids a declined trade is priced from.
        self.world_outcomes: dict[str, dict[str, Any]] = {}
        # Anticipatory settlement: each judged return's mark once taken, and the judge
        # decisions rewarded on it whose final measurement is still owed to standing.
        # The venue's clock (wave 16, D2): the latest mid and funding-rate print of each
        # coin, [ts_ns, value], that a named trade opens from and is measured by.
        self.venue_marks: dict[str, list] = {}
        self.funding_prints: dict[str, list] = {}
        self.reference_mids: dict[str, dict[str, Any]] = {}
        self.consequence_mix: float = self.ev.consequence_share  # live sampling actuator
        # The consequence scores the last closed window issued, and whether the actuator
        # is blind for want of them (wave 16, ruling R-B).
        self.last_window_consequences: int = 0
        self.sampling_blind: dict[str, int] | None = None
        self.sampling_history: list[dict[str, Any]] = []
        # The niche for unhistoried actions (ruling R5): per open invocation, the
        # unhistoried tool action whose result its next model round reads. Emptied
        # when the invocation returns, so a checkpoint never holds an entry.
        self.niche_rounds: dict[str, str] = {}
        # Each seat's use of the period's niche, against ``novelty.seat_share`` of it:
        # {"start_tick", "cap" (the period's share), "used": {seat: micro-USD}}.
        self.niche_use: dict[str, Any] = {}
        # The thrash charge each open core-router round carries (versioning C2): the
        # price in force at its draw times the router's own movement. Only nonzero.
        self.thrash_charges: dict[str, float] = {}
        # Time audit T14: when each loop's configuration last changed, and the
        # lifespans recorded since the immune organ last closed a window.
        self.config_ticks: dict[str, int] = {}
        self.lifespan_log: list[dict[str, Any]] = []
        self.delivered_seen: dict[str, int] = {
            st.learner.id: 0 for st in self._all_router_states()
        }
        # Wave 17b: each seat's cursor over its own deliveries (``assembly:<id>``), read
        # by its next ballot; the committee-eligibility tally kept at settlement and
        # the evidence pairs it counted for retained decisions; and the order intents
        # of released decisions, as counts (``released_intents``).
        self.policy_seen: dict[str, int] = {}
        # Each seat's delivery count at recent boundaries, [[tick, count]], oldest
        # first: what its deliveries were at a tick, so those older than the
        # published retention can be released unread (wave 17b).
        self.policy_marks: dict[str, list[list[int]]] = {}
        # The venue transactions of released decisions' vault writes, [hash, the clock
        # when it was released], while a vault lookup's window can still return one
        # (``_prune_vault_released``): still bound, so no later write takes it.
        self.vault_released_hashes: list[list] = []
        self.eligibility_tally: dict[str, int] = {}
        self.eligibility_evidence: set[tuple[str, str]] = set()
        self.released_intents: dict[str, int] = {}
        self.vote_handles: dict[str, str] = {}
        self.order_intents: dict[str, dict] = {}
        # The vault surface ([venue] vault_tools): vault writes by client id, the
        # vaults this world's seats created or hold, and the venue ledger cursor.
        self.vault_intents: dict[str, dict] = {}
        self.vault_book: dict[str, dict] = {}
        self.vault_ledger_cursor_ns = self.clock.now_ns
        self.vault_ledger_seen: list[str] = []  # row hashes read at the cursor's millisecond
        # Decision handle -> why a venue write it attempted was refused, read once
        # by that decision's own answer: a refused write is not an answer's licence.
        self.venue_attempts: dict[str, str] = {}
        self.voted_amendments: set[str] = set()
        self.snapshot_keys: dict[str, str] = {}  # decision handle -> snapshot key
        # NOOP handle -> the abstention credit its router is owed, and when it is due.
        self.noop_credits: dict[str, dict] = {}
        # Decision handle -> its settled score before the card penalty, until the router
        # that drew it learns it (wave 16, D4: the router's observed mean raw score).
        self.raw_scores: dict[str, float] = {}
        # Decision handle -> the card penalty its priced settlement bore, read with its
        # raw score by every learner (wave 16, R10-g).
        self.round_penalties: dict[str, float] = {}
        # Decision handle -> the settlement its penalty waits for its origin window's
        # close to price (wave 16, D5; ruling R-I, Q9).
        self.deferred_settlements: dict[str, dict] = {}

        # world memory (public facts) and assembly memory (private to each assembly)
        self.recent_mids: dict[str, deque[dict[str, Any]]] = {}
        # The venue's taker rate per market and when it was read (wave 16, D1): what a
        # named road not taken pays for its round trip. None until the first broadcast.
        self.fee_schedule: dict[str, Any] | None = None
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
        # The artifact archive (C9): records in the ledger, bytes beside it by hash.
        from factorylab.kernel.artifacts import ArtifactStore, artifact_root

        self.artifacts = ArtifactStore(
            self.ledger, root=artifact_root(ledger_path) if ledger_path else None,
            clock_ns=self.clock,
        )
        # The disk is finite: retained private state has a hard cap for the world's
        # life, and a write that needs room releases retired ids' kept state first.
        self.artifacts.private_cap = manifest.storage.retained_private_bytes
        self.artifacts.reclaimable = self._reclaimable_state
        self.artifacts.on_reclaimed = self._state_reclaimed
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
        # Wave 17b: an item's retention horizon counts world ticks.
        self.outcomes.tick = lambda: self.ticks_consumed
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
        # W4: calling decision -> {population tool id: successful calls} by a seat of
        # another lineage than the tool's builder, until that decision settles and its
        # builder is credited (``CompositionMixin._credit_requested``).
        self.tool_uses: dict[str, dict[str, int]] = {}
        # W4: registering decision -> {"until": tick, "tools": [...], "scores": [...]},
        # held for its tool-use window (``CompositionMixin._hold_for_tool_use``).
        self.tool_holds: dict[str, dict[str, Any]] = {}
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
                # The venue charges nothing for a call: its public reads are free
                # and its fees land on the venue account where they happen.
                "price_micro_per_call": spec.price_micro_per_call,
                "kind": spec.kind,
            }
        vault_examples: dict[str, list[dict]] = {}
        if getattr(manifest.exchange, "vault_tools", False):
            # A surface, published only where the manifest opts in: what each call
            # does and costs, and no word about what a vault might be for.
            from factorylab.world.venue_tools import vault_specs

            specs, vault_examples = vault_specs()
            self.tool_specs.update(specs)
            self.treasury.vault_custody = True
        from factorylab.world.venue_tools import (
            _BASE_WEIGHT,
            TICK_ANSWER_FACT,
            VENUE_WEIGHT_PER_MINUTE,
        )

        budget = manifest.exchange.public_read_weight_per_minute
        seats = manifest.exchange.max_readers
        for tool_id, weight in _BASE_WEIGHT.items():
            if tool_id not in self.tool_specs:
                continue
            # A limit is a published fact (essay II.I.b), never advice.
            if weight == 0:
                self.tool_specs[tool_id]["description"] += (
                    " Held by seats with a venue read slot. Answered from the listing the "
                    "venue adapter loaded: it sends no request and spends none of your "
                    "venue read share.")
                continue
            self.tool_specs[tool_id]["description"] += (
                f" Held by seats with a venue read slot (at most {seats}). Each slot has "
                f"a fixed share of {budget // seats} venue request weight ({budget} over "
                f"{seats} slots) in any sliding 60 s of world time; the rest of the "
                f"venue's {VENUE_WEIGHT_PER_MINUTE} a minute per IP is the kernel's. This "
                f"read is sent at most once and weighs {weight}"
                + (" plus 1 per 60 candles asked" if tool_id == "venue.candles" else
                   " plus 1 per 20 rates asked" if tool_id == "venue.funding_history"
                   else "")
                + ", charged to your share for every read. A read your remaining share "
                "cannot cover is refused and not sent. " + TICK_ANSWER_FACT)
        # seat -> [[world ns, venue weight]] of its reads in the sliding minute.
        self.venue_read_use: dict[str, list[list[int]]] = {}
        # The same for Polymarket reads (``runtime/polymarket.py``); empty, and never
        # spent, in a world without the block.
        self.polymarket_read_use: dict[str, list[list[int]]] = {}
        self._polymarket_tick_reads = None
        # slot index -> the world ns from which a freed slot may be given again (its
        # last holder's last read has left the sliding minute by then), and the seats
        # registered while no slot was free, in registration order.
        self.slot_free_at: dict[str, int] = {}
        # slot index -> the registration that last held it, whose Polymarket open reads
        # must all have stopped counting before the slot is given again.
        self.slot_last_reader: dict[str, str] = {}
        # The venue read slots, by position: the seeds, in manifest order, up to
        # ``[venue] max_readers``; then registrations into the lowest free slot. A
        # retired seat's slot is None until it is given again. Seeds past the slots
        # wait for one like any seat registered without one.
        seeds = [a.id for a in manifest.assemblies]
        self.venue_readers: list[str | None] = seeds[:manifest.exchange.max_readers]
        self.slot_waiting: list[str] = seeds[manifest.exchange.max_readers:]

        # The directions this world's rail admits, each described once and truly for
        # this world (``transfer_tool_spec``); ``_ensure_treasury_tool`` re-derives it
        # when a rehearsal wraps the rail after launch.
        self._ensure_treasury_tool()
        self.tool_specs["catalogue.search"] = {
            "id": "catalogue.search",
            "description": "Find tools, assemblies, complete proposal shapes and model offers "
            "by substring. Returns exact tool argument and return schemas and prices, live "
            "assembly contracts with their descriptions, proposal contracts, and matching "
            "model ids with token prices and context length.",
            "args_schema": {
                "type": "object",
                "properties": {
                    "substring": {"type": "string"},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 50, "default": 20},
                },
                "required": ["substring"],
                "additionalProperties": False,
            },
            "price_micro_per_call": 0,
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
            "price_micro_per_call": 0,
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
            # One example per admitted direction, none favoured (smuggling audit D5).
            **({"treasury.transfer": self.tool_specs["treasury.transfer"]["args_schema"][
                "examples"]} if "treasury.transfer" in self.tool_specs else {}),
            "catalogue.search": [{"substring": "flash", "limit": 20}],
            "market.discover": [{"query": "inference", "limit": 20}],
            "artifact.get": [{"sha": "0" * 64}],
            "outcome.get": [{"outcome_id": "outcome:1"}, {"handle": "decision-1"}],
            **vault_examples,
        }
        self.tool_specs["artifact.get"] = {
            "id": "artifact.get",
            "description": "Read an archived artifact by its sha256: your own working "
            "state, an artifact you hold, or the current private state of a program in "
            "your own lineage. A hash you released recently is refused with "
            "artifact_released; any other hash you hold no reference to is refused with "
            "artifact_private, whether or not the archive holds it. The read is free and "
            "ledgered. Returns your kind, bytes and text (base64 for binary), up to 64 KiB.",
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
        self.tool_specs["outcome.list"] = {
            "id": "outcome.list",
            "description": "Page your own outcome index without acknowledging "
            "items. Read any indexed body with outcome.get using its exact outcome_id.",
            "args_schema": {
                "type": "object",
                "properties": {
                    "after": {"type": "integer", "minimum": 0, "default": 0},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 32, "default": 8},
                },
                "additionalProperties": False,
            },
            "price_micro_per_call": 0,
            "kind": "outcome",
        }
        examples["outcome.list"] = [{"after": 0, "limit": 8}]
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
        # Every published tool carries examples its own schema accepts (B1). Stamping
        # after the whole seed set is assembled keeps that total: a seed tool added
        # without an example fails at launch rather than reaching the population.
        for tool_id, spec in self.tool_specs.items():
            spec["args_schema"]["examples"] = examples[tool_id]
        from factorylab.runtime.polymarket import install as install_polymarket

        # [polymarket] enabled: event-market tools with their own examples, and the
        # simulated venue or the public read client behind them. Absent otherwise.
        install_polymarket(self)
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
        # The thrash price (versioning C2): the charter's PID over the gap's volatility.
        self.thrash_controller = thrash_controller(self.ledger, manifest)
        self.controller = PriceController(
            self.ledger,
            eta=pr.eta,
            decay=pr.decay,
            penalty_cap=pr.penalty_cap,
            min_window_events=pr.min_window_events,
            kp=pr.kp,
            kd=pr.kd,
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
        # index + event_log_base = event number. Both lists hold only the events an
        # open forecast's window can still read (``_prune_event_log``, wave 17).
        self.balance_at: list[int] = [self.wallet.balance]
        self.events_log: list[dict[str, Any]] = [{"kind": "Launch", "payload": {}}]
        self.event_log_base = 0
        self.reserve_window_start: int | None = None
        self.internal: deque[Event] = deque()
        self.n = 0
        self.emitted = 0
        self.insolvency_count = 0
        self.dormancy: dict[str, Any] | None = None  # C2: set while paid cognition is paused
        self._compute_routed = False
        self._compute_unaffordable = False
        self.world_consumed = 0
        self.ticks_consumed = 0
        # Venue effects by custody, per decision, until its outcome is settled: the
        # consequence line reports provider cost and venue delta separately rather
        # than one net (edition 3, C5).
        self.venue_deltas: dict[str, dict[str, int]] = {}
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

    @staticmethod
    def _advertised_completion_limit(entry: Any) -> int | None:
        """Return a positive advertised completion limit, or None when absent/invalid."""
        value = getattr(entry, "max_completion_tokens", None)
        return value if type(value) is int and value > 0 else None

    @staticmethod
    def _model_limit_aliases(model_id: str) -> tuple[str, ...]:
        """Return provider catalogue identities used by runtime model aliases."""
        aliases = [model_id]
        base, separator, _effort = model_id.rpartition("@")
        if separator and base:
            aliases.append(base)
        for candidate in tuple(aliases):
            if candidate.endswith(":online"):
                aliases.append(candidate.removesuffix(":online"))
        return tuple(dict.fromkeys(aliases))

    def _provider_completion_limit(self, model_id: str) -> int | None:
        """Return the launch-pinned provider completion limit for a model or alias."""
        for alias in self._model_limit_aliases(model_id):
            limit = self.catalogue_completion_limits.get(alias)
            if type(limit) is int and limit > 0:
                return limit
        return None

    def _resolve_max_tokens(self, model_id: str, requested: int | None, *,
                            program: bool = False) -> int:
        """Resolve provider-native output once; every executable spec gets a positive int."""
        if requested is not None:
            return requested
        if program:
            # Programs do not buy a completion.  Keep the historical harmless
            # contract bound when their proposal omits max_tokens.
            return 512
        limit = self._provider_completion_limit(model_id)
        if limit is None:
            raise ValueError(
                f"provider-native max_tokens unavailable for model {model_id!r}"
            )
        self.catalogue_completion_limits[model_id] = limit
        return limit

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
            max_tokens = self._resolve_max_tokens(seed.model_id, seed.max_tokens)
            self.registry.register(
                _assembly_contract(seed.id, seed.role, seed.accepts, max_tokens,
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
