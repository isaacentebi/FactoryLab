"""Runtime pricing method group."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field, replace
from decimal import Decimal
from fractions import Fraction
from math import ceil

from factorylab.charter.charter import MetricCard
from factorylab.charter.controller import CardRegion, relative_region, violation
from factorylab.charter.controller import pressure as card_pressure
from factorylab.charter.measurement import _groups, _horizon, measure_cards
from factorylab.cortex.registration import measured_role
from factorylab.kernel.events import Event, EventKind
from factorylab.kernel.money import usd_to_micro
from factorylab.kernel.queue import SettleStatus
from factorylab.runtime.cards import parses, region_for
from factorylab.runtime.immune import close_window
from factorylab.runtime.observations import ObservationBook, normalise, trim_series
from factorylab.world.events import funding_instant


@dataclass
class MeasureWindow:
    """Raw material for one reserve window's metric-card observations."""

    index: int
    # None when the venue would not say what its equity was at the window's start.
    equity_start_micro: int | None
    costs: list[int] = field(
        default_factory=list
    )  # wallet cost of each well-formed producer return
    invocations: int = 0
    ok: int = 0
    notional_micro: int = 0  # filled size × price, summed
    forecast_skills: list[float] = field(default_factory=list)
    # How many consequence scores the world issued in the window (a verdict's, a meta's,
    # a settled forecast's): zero means the window had no consequence reading at all
    # (wave 16, ruling R-B; the sampling actuator reads it).
    consequence_readings: int = 0
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
    # The acting returns whose payoff this window settled, and those that paid off: the
    # denominator and numerator of ``consequence_paid_off_rate`` by decision, for relief
    # attribution (wave 16, D5). Per-decision attribution, never a window observation.
    paid_off_settled: list = field(default_factory=list)
    paid_off_handles: list = field(default_factory=list)
    # Returns that executed nothing and named a trade, whose world outcome this window
    # fixed (measured, or known absent); those measured with an informative base-rate
    # key; and of those, the ones with y = 1 (wave 16, R-H: the non-acting outcomes
    # consequence_paid_off_rate does not count, published on their own).
    non_acting_outcomes: int = 0
    non_acting_informative: int = 0
    non_acting_paid_off: int = 0
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
    decisions: dict[str, dict] = field(default_factory=dict)
    closed_values: dict[str, float] | None = None
    # Per rate observation, frozen at the close: the decisions in its denominator and
    # in its numerator (wave 16, D5: who relieved a violation of the rate).
    closed_relief: dict[str, dict[str, list]] = field(default_factory=dict)
    closed_regions: dict[str, CardRegion] = field(default_factory=dict)
    closed_shares: list[dict] = field(default_factory=list)
    # The edition that priced the window, frozen with its prices at the close. A later
    # amendment cannot remove or restate a card out of what this window already attributed.
    closed_cards: tuple[MetricCard, ...] = ()
    closed_prices: dict[str, float] = field(default_factory=dict)
    # Each measured card's per-scope values at the close (scope -> value), for a card
    # whose window is per assembly or per role: who a violation is attributable to.
    closed_scopes: dict[str, dict[str, float]] = field(default_factory=dict)
    # Each measured card's holdout violation at the close (charter audit M3).
    closed_holdouts: dict[str, float] = field(default_factory=dict)
    mids: list[dict] = field(default_factory=list)
    funding: list[dict] = field(default_factory=list)
    wallet_balance_micro: list[list[int]] = field(default_factory=list)
    tick_timestamps_ns: list[int] = field(default_factory=list)
    books: list[dict] = field(default_factory=list)
    # How many samples each bounded public series has already discarded, by the
    # path it is published at. Private attribution, never a window observation:
    # it is what turns a retained index back into a position in the whole window.
    series_discarded: dict[str, int] = field(default_factory=dict)
    # Every invocation's metered cost, in micro-USD: the window's compute burn
    # (``burn_per_window``; charter audit M6). Only a debit with a real counterparty
    # is metered, so this is money that left the factory in the window.
    compute_spend_micro: int = 0
    # The world tick the window opened at and the tick its drawn period ends (time
    # audit T1): the price loop's own schedule, published with the window.
    opened_tick: int = 0
    due_tick: int | None = None
    # The part of it the evaluator roles spent (judges, adversarial judges and every
    # tier of meta): ``evaluator_compute_share``'s numerator (the #132 review, item 2).
    evaluator_spend_micro: int = 0
    # The early-warning summaries of the score series at this window's close
    # (``runtime.ews``): evaluator-facing, never a public window fact.
    ews_variance: float | None = None
    ews_autocorrelation: float | None = None
    # The window's model calls by the provider that served them and by the foundation
    # family of the model (essay II.IV.c, entrainment; time audit T15). The dependency
    # observations read these; the names are not a public window fact.
    calls_by_provider: dict[str, int] = field(default_factory=dict)
    calls_by_family: dict[str, int] = field(default_factory=dict)
    # UTF-8 bytes of the opening prompts the window's invocations were rendered, summed
    # whole and for the YOU and INPUTS sections: the ledgered ``sections`` counts (essay
    # II.IV.a, the ceded metrics layer; the context-size observations read these).
    # How many of the window's invocations had their opening prompt rendered and
    # measured: the denominator of the three prompt means. A request that could not
    # be rendered is an invocation (it failed) but no prompt.
    prompts: int = 0
    prompt_bytes: int = 0
    you_bytes: int = 0
    inputs_bytes: int = 0
    # The INPUTS bytes of the window's invocations commissioned on a published return,
    # summed: what reading returns cost in context, filed under no author here.
    downstream_read_bytes: int = 0
    # The invocations whose downstream reading is measured: the reading mean's
    # denominator. Every invocation from wave 7 on; a record closed before it has none,
    # since its returns' readings were never metered (not metered as zero).
    read_measured: int = 0


#: The definition of a censored settlement that carries a price: its decision left
#: an accepted commitment avoidably unresolved, and its ``score`` is the penalty the
#: charter charged it, never an observed score.
UNRESOLVED_PRICED = "forecast-unresolved-priced-v1"

# Observations whose shares already come from each decision's own contribution.
_EXACT_SHARES = frozenset({"cost_per_return", "cost_per_attempt", "well_formed_rate",
                           "tool_calls", "turnover"})
#: The per-decision rate observations a violation of which is attributed by relief
#: (wave 16, D5): each decision either moved the rate toward its region (it relieved
#: the violation) or did not. The attributed share is 0 for a reliever and an equal
#: share of the non-relievers' otherwise, NOOP and declines among them (ruling R9).
RELIEF_RATES = frozenset({"revision_rate", "consequence_paid_off_rate", "noop_share"})


class PricingMixin:
    """Preserve runtime state and behavior for pricing operations."""

    @property
    def observations(self) -> ObservationBook:
        """The factory's live measurement vocabulary: the seeds plus what it registered.

        Every consumer of a card's observation reads through this book, so a
        registered measurement is priced, published and diagnosed exactly like a
        seed one; only the way it is computed differs. The book holds this
        runtime's own registrations and no other's.
        """
        return ObservationBook(self.registered_observations, run=self.observation_runner.run,
                               reject=self._observation_out_of_range)

    def _observation_out_of_range(self, observation, value: float) -> None:
        """Ledger a measurement that left its declared range; the window observes nothing.

        The range a registration declared bounds its supported outputs,
        so a value outside it cannot be scored as if it were inside
        and is not quietly moved to the edge either. The diary names it so the
        population can see which measurement stopped supporting its card.
        """
        lo, hi = observation.unit_range
        self.ledger.append({
            "kind": "observation.out_of_range", "observation": observation.id,
            "value": value, "range": [lo, hi], "version": observation.version,
            "window": getattr(getattr(self, "window", None), "index", None),
            "ts": self.clock.now_ns,
        })

    def _init_fidelity(self) -> None:
        """Manifest settings and attributed observations are initialized before any decision."""
        self.cadence.configure(min_support=self.m.timing.min_support)
        self.price_windows: dict[int, MeasureWindow] = {}
        self.price_origins: dict[str, dict[str, int]] = {}

    def _contribution(self, handle: str, role: str) -> dict:
        """Every original decision has one contribution record per measurement window."""
        self.price_windows[self.window.index] = self.window
        self.price_origins.setdefault(handle, {"origin": self.window.index})
        return self.window.decisions.setdefault(handle, {
            "role": role, "cost": 0, "ok": 0, "invocations": 0, "tool_calls": 0,
            "notional_micro": 0,
        })

    def _decision_role(self, handle: str) -> str:
        """The measurement scope a decision's return was, or would be, priced in."""
        emitted = self.return_kinds.get(handle)
        if emitted is None:
            action_id = self.handle_to_assembly.get(handle)
            if action_id in self.assemblies:
                emitted = self.assemblies[action_id].spec.emits
        return measured_role(emitted) if emitted else "producer"

    def _invoke(self, action_id, req, role, *, child=False):
        """Prices retain the completed decision's own cost, schema result and tool attempts.

        A decision of a seat with no reward trail yet (its protected trial) is marked
        as taken in the unhistoried niche (essay II.II.b): it bears no card penalty and
        is counted in no one's share (wave 16, R-E).
        """
        niche = (not child and action_id in self.assemblies
                 and self._unhistoried(action_id))
        before_calls = self.window.tool_calls
        before_children = sum(d["tool_calls"] for d in self.window.decisions.values())
        ret = super()._invoke(action_id, req, role, child=child)
        child_calls = sum(d["tool_calls"] for d in self.window.decisions.values()) - before_children
        emitted = self.return_kinds.get(req.handle)
        if emitted is None and action_id in self.assemblies:
            emitted = self.assemblies[action_id].spec.emits
        observed_role = measured_role(emitted) if emitted else role
        sample = self._contribution(req.handle, observed_role)
        evidence = {"cost": ret.cost, "ok": int(ret.status == "ok"), "invocations": 1,
                    "tool_calls": self.window.tool_calls - before_calls - child_calls}
        self.ledger.append({"kind": "price.contribution", "handle": req.handle,
                            "window": self.window.index, "role": observed_role, **evidence,
                            **({"niche": True} if niche else {})})
        sample["role"] = observed_role
        if niche:
            sample["niche"] = True
        for name, value in evidence.items():
            sample[name] += value
        self.window.compute_spend_micro += ret.cost
        if observed_role in ("evaluator", "meta", "adversary"):
            self.window.evaluator_spend_micro += ret.cost
        self._count_dependency(action_id)
        return ret

    def _count_dependency(self, action_id: str) -> None:
        """One model call counted by the provider that served it and its model's family.

        Essay II.IV.c: loops that "share a common medium (e.g., a common foundation
        model ...) or a common infrastructure ... risk entrainment", and governance must
        "force or incentivize the randomization of a factory's dependency class"
        (time audit T15). The counts feed the dependency observations a card can price.
        """
        from factorylab.runtime.families import model_family

        assembly = self.assemblies.get(action_id)
        if assembly is None:
            return
        model = assembly.spec.model_id
        provider = next((tier.provider for tier in self.m.models if tier.id == model),
                        "x402" if model.startswith("x402:") else
                        "program" if model == "program" else "openrouter")
        for counts, name in ((self.window.calls_by_provider, provider),
                             (self.window.calls_by_family, model_family(model))):
            counts[name] = counts.get(name, 0) + 1

    def _record_pricing_fills(self, events) -> None:
        """Filled notional belongs to the order's original decision in the fill's window."""
        if self.consequences.pending_orders:
            # An unresolved order write defers these fills; acknowledgement replays them.
            return
        for ev in events:
            if str(ev.kind) == "Fill":
                self._record_fill_notional(dict(ev.payload))

    def _record_fill_notional(self, payload: dict) -> None:
        """One observed fill, priced on the same path whether it was deferred or not."""
        owners = {order.order_id: order.handle for order in self.consequences.table.orders}
        handle = owners.get(str(payload["order_id"]))
        if handle is None:
            return
        notional = usd_to_micro(
            Decimal(str(payload["size"])) * Decimal(str(payload["px"])), rounding="nearest")
        sample = self._contribution(handle, "producer")
        self.ledger.append({"kind": "price.contribution", "handle": handle,
                            "window": self.window.index, "notional_micro": notional})
        sample["notional_micro"] += notional
        self.price_origins[handle]["turnover"] = self.window.index

    def _manage_reserve_window(self) -> None:
        """Close the measurement window when the price loop is due, and open the next.

        Essay II.IV.c; time audit T1, T2. The window is the price loop's period,
        counted in world ticks: drawn, with the price loop's own jitter, as
        ``min_ratio`` times the fastest priced card's sample loop
        (``_price_inner``), and due only when that period has elapsed and the
        ratio still holds against the loop measured now. No other loop shares its
        boundary: the immune organ, the sampling actuator, governance and the money
        caps each keep their own schedule.
        """
        if self.reserve_window_start is None:
            self.cadence.launch(self.clock.now_ns if self.live else 0)
        now = self.ticks_consumed
        inner = self._price_inner()
        if self.reserve_window_start is not None and not self.clockwork.due("price", now, inner):
            return
        closed = self.window.index if self.reserve_window_start is not None else None
        if closed is not None:
            self._close_price_window()
        schedule = self.clockwork.fire("price", now, inner)
        self._ledger_loop("price", schedule, inner_loop="card samples")
        if closed is None:
            # Launch opens every outer loop's first period (time audit T1): the immune
            # organ's over the price loop (versioning P5) and the sampling actuator's
            # over the consequence loop, each on its own jittered schedule, and checks
            # that a governance tier is viable at all (T7).
            for name, over, label in (("immune", ceil(schedule["period"]), "price"),
                                      ("sampling", self._consequence_period(), "consequence")):
                self._ledger_loop(name, self.clockwork.fire(name, now, over), inner_loop=label)
            self._check_viability()
        # Time audit T6: the novelty share is a flow, one share of the spendable
        # budget per measured consequence period, of which this window accrues the
        # part its drawn period covers. Locked backing is not spendable, and a venue
        # loss can carry the unlocked part below zero.
        period = self._consequence_period()
        self.reserve.open_window(
            self.clock.now_ns, max(0, self.wallet.unlocked),
            accrued=Fraction(min(period, ceil(schedule["period"])), period))
        self.reserve_window_start = self.clock.now_ns
        self._open_niche_period()
        self.market_index = None
        self.stats.reserve_windows += 1
        self.window = MeasureWindow(self.stats.reserve_windows, self._equity_micro(),
                                    opened_tick=now, due_tick=schedule["due"])
        from factorylab.runtime.continuity import collect_window

        collect_window(self)  # the archive collects; retained state is never charged
        self.price_windows[self.window.index] = self.window
        self._observe_positions()
        self._activate_charter_if_due()
        self._derive_regions()
        if closed is not None:
            # The closed window's public world block, ledgered once, after any
            # charter activation at this boundary, so the wake never shows an
            # activated amendment against the edition it replaced.
            from factorylab.runtime.wake import public_window_item

            self.ledger.append({**public_window_item(self, window=closed, event=self.n),
                                "ts": self.clock.now_ns})

    def _ledger_loop(self, name: str, schedule: dict, *, inner_loop: str) -> None:
        """One derived loop fired: its tick, the period drawn and the inner loop's period."""
        self.ledger.append({"kind": "clock.loop", "loop": name, "tick": schedule["opened"],
                            "period_ticks": schedule["period"], "due_tick": schedule["due"],
                            "inner": inner_loop, "inner_ticks": schedule["inner"],
                            "ts": self.clock.now_ns})

    def _consequence_period(self) -> int:
        """The measured consequence loop in ticks: the backstop, or the p90 settlement above it."""
        return self.cadence.consequence_period_events()

    def _patience(self) -> int:
        """How long an exploration is protected, in ticks: ``min_ratio`` consequence periods.

        Essay II.IV.b: "the compensation period of any exploratory learner must be
        shorter than the lifetime of the things it is being compensated for
        discovering", and "some share of the exploratory population is allowed to
        live longer than justified by its own current scoring" (time audit T5).
        """
        return self.m.timing.min_ratio * self._consequence_period()

    def _card_inner(self, card: MetricCard) -> int:
        """The measured period, in ticks, of the loop a card's samples come from.

        A card measured on settled forecasts waits on the forecast loop; any other
        on the settle loop of the role it answers for (every role's slowest for a
        card that answers for all): a price changes the rewards of that role's
        decisions, and its effect returns only when they settle (time audit T2).

        The forecast loop's period is what forecasters chose: a seat may seal a
        horizon of up to 200 ticks, and a p90 it stretches would slow the very
        price that bills its unresolved commitments. So that meter counts only
        with the consequence loop's support (``timing.min_support``) and never
        beyond the consequence backstop, the physics' bound on how long any
        consequence is allowed to wait; a longer horizon is the seat's choice, not
        a period of the world the price must wait out.
        """
        from factorylab.charter.measurement import FORECAST_ROWS

        observation = normalise(card.observation)
        if card.window.kind == "forecasts" or (
                card.window.kind == "windows" and observation in FORECAST_ROWS):
            return min(self.clockwork.measured("forecast", support=self.m.timing.min_support),
                       self.ev.consequence_backstop_ticks)
        if card.answers_for != "all":
            return self.clockwork.measured(f"settle:{card.answers_for}")
        return max((self.clockwork.measured(name) for name in self.clockwork.latencies
                    if name.startswith("settle:")), default=1)

    def _price_inner(self) -> int:
        """The fastest priced card's sample loop in ticks: what the window must separate from."""
        cards = [c for c in self.charter.cards if c.id in self.regions]
        return min((self._card_inner(c) for c in cards), default=1)

    def _learned(self, raw: float, charge: float, bound: float) -> float:
        """The one map every learner learns a round on: ``(r + B - P) / (1 + B)``.

        Wave 16, ruling R10-l (superseding R10-g's per-charge maps): ``r`` is the round's
        raw score (or the raw neutral a round that delivered nothing is credited, D4),
        ``P`` the total charge the round bears (its D5 card share plus, for a router,
        its I-10 thrash charge, each bounded by ``prices.penalty_cap``) and ``B`` the
        largest ``P`` can be for that learner (``_charge_bound``). Applied exactly once
        per round (``_learning_value`` is its one caller): composing one map per charge
        compresses the scale and weighs one charge against another. No clip: with
        ``0 <= r <= 1`` and ``0 <= P <= B`` the value lies in ``[0, 1]``.
        """
        return (float(raw) + float(bound) - float(charge)) / (1.0 + float(bound))

    def _charge_bound(self, router: bool) -> float:
        """``B``, the largest total charge a learner's round can bear, for the world's life.

        A seat's own learner bears only its card share, at most ``prices.penalty_cap``;
        a router can bear a card share and a thrash charge, each at most the cap (any
        router can be charged: I-10), so ``2 * penalty_cap`` (ruling R10-l).
        """
        cap = self.m.prices.penalty_cap
        return 2 * cap if router else cap

    def _round_priced(self, handle: str, score: float) -> tuple[float, float]:
        """A settled round's raw score and card penalty, unmapped: ``(r, p)``.

        Its raw score and penalty when it was priced (``raw_scores``,
        ``round_penalties``), else its score with no penalty.
        """
        raw = self.raw_scores.get(handle)
        if raw is None:
            return float(score), 0.0
        return float(raw), float(self.round_penalties.get(handle, 0.0))

    def _prune_price_evidence(self) -> None:
        """Completed decisions release old attribution windows after their totals are frozen."""
        for handle in tuple({**self.raw_scores, **self.round_penalties}):
            # A raw score and its penalty wait for whoever learns the decision: its
            # consumer's read of every return delivered for it (the kernel owes it
            # nothing, ``DecisionQueue.owed``; a router's learning is such a read) and
            # the seat's own learner (``assembly_rounds``). A decision whose actor is no
            # learner is owed nothing once final, so it keeps nothing either.
            decision = self.queue.get(handle)
            if (decision.status not in (SettleStatus.PENDING, SettleStatus.TIMED_OUT)
                    and handle not in self.assembly_rounds
                    and self.queue.owed(handle) is None):
                self.raw_scores.pop(handle, None)
                self.round_penalties.pop(handle, None)
        for handle in tuple(self.thrash_charges):
            # A charge is spent when its round trains; a round that closed and whose
            # router has read every return it was owed will never train.
            decision = self.queue.get(handle)
            if (decision.status not in (SettleStatus.PENDING, SettleStatus.TIMED_OUT)
                    and handle not in self.noop_credits
                    and self.queue.delivered_count(decision.actor)
                    <= self.delivered_seen.get(decision.actor, 0)):
                del self.thrash_charges[handle]
        for handle in tuple(self.price_origins):
            if (self.queue.get(handle).status not in (SettleStatus.PENDING, SettleStatus.TIMED_OUT)
                    and handle not in self.pending and handle not in self.pending_exposure
                    # An abstention is priced when its credit falls due (ruling R9).
                    and handle not in self.noop_credits):
                del self.price_origins[handle]
        retained = {index for origins in self.price_origins.values() for index in origins.values()}
        self.price_windows = {index: window for index, window in self.price_windows.items()
                              if index == self.window.index or index in retained}
        # A final decision is never cut off again: its tick record goes (time audit T3).
        self.queue.forget_ticks()

    def _observe_delivered_event(self, ev: Event) -> None:
        """Only ledgered deliveries contribute window samples or start observation trials."""
        if ev.kind is EventKind.FUNDING and ("paid_usd" in ev.payload or "rate" not in ev.payload):
            # A funding *payment* is a wallet fact, not a market series sample, even when the
            # live venue attaches the rate it was paid at; the rate series comes from the tick.
            return
        if ev.kind in (EventKind.MARKET_MID, EventKind.FUNDING):
            key = "mids" if ev.kind is EventKind.MARKET_MID else "funding"
            value = (usd_to_micro(Decimal(str(ev.payload["mid"])), rounding="nearest")
                     if key == "mids" else float(ev.payload["rate"]))
            series = getattr(self.window, key)
            at = ev.ts_ns if key == "mids" else funding_instant(ev.payload, ev.ts_ns)
            series.append({"coin": str(ev.payload["coin"]), "ts_ns": at, "value": value})
            trim_series(self.window, key, per_coin=True)
        elif ev.kind is EventKind.TICK:
            self.window.tick_timestamps_ns.append(ev.ts_ns)
            trim_series(self.window, "tick_timestamps_ns")
            self.window.wallet_balance_micro.append([ev.ts_ns, self.wallet.balance])
            trim_series(self.window, "wallet_balance_micro")
        elif ev.kind is EventKind.REGISTERED and ev.payload.get("kind") == "observation":
            entry = self.registered_observations.get(ev.payload["id"])
            if (entry is not None and entry["version"] == ev.payload["version"]
                    and "trial_window" not in entry):
                self.ledger.append({"kind": "observation.trial", "observation": ev.payload["id"],
                                    "version": entry["version"], "window": self.window.index,
                                    "tick": self.ticks_consumed, "ts": self.clock.now_ns})
                entry["trial_window"] = self.window.index
                entry["trial_tick"] = self.ticks_consumed
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

    def _reset_redefined_cards(self) -> None:
        """A card whose observation changed under the same id is a new metric (Codex on
        #152): the evidence of its old meaning stops counting for it. Guarantees its
        controller duration and episode reset (``PriceController.redefine``), its
        consecutive unmeasured windows (R10-f) restart; the organ's own step drops it
        from the failing set it holds (M-6; ``versions.organ_step``, which replay runs
        too), so no stable-failure duration spans the redefinition.
        A card whose observation is unchanged keeps all of it; old windows are read
        under the meaning they recorded (``immune.thrash_roles``,
        ``live.current_metrics``), and a closed price window prices on the cards it froze.
        """
        known = getattr(self, "card_meanings", None)
        if known is None:
            known = self.card_meanings = {}
        for card in self.charter.cards:
            meaning = card.observation.strip().lower()
            if card.id in known and known[card.id] != meaning:
                if card.id in self.controller.card_ids():
                    self.controller.redefine(card.id, edition=self.charter.edition)
                self.card_unmeasured.pop(card.id, None)
            known[card.id] = meaning

    def _derive_regions(self) -> None:
        """Every readable card of the current edition holds a region; unreadable ones hold none.

        New cards register; cards whose bound moved (a rolling median, a restated
        card) keep their price and get the new bounds. Each change is a
        ``price.region`` entry; prose the runtime cannot read is logged once per
        card per edition as ``price.unparsed``.
        """
        regions: dict[str, CardRegion] = {}
        self._reset_redefined_cards()
        for card in self.charter.cards:
            region = region_for(card, rolling=self.rolling, observations=self.observations)
            if region is None:
                if card.id in self.regions:
                    self.controller.clear_region(card.id)
                key = (card.id, self.charter.edition)
                unknown = []
                if not parses(card):
                    unknown.append("region")
                if self.observations.get(card.observation) is None:
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
            region = relative_region(region)
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
        forecast_skill: mean skill of the settled forecasts of evaluators with any
        (the forecasts they sealed and the world resolved); turnover: filled notional
        over equity at the window start (0 with no fills). A quantity without support
        is not observed.

        The consequence score that grades an evaluator's verdict never enters a card
        (wave 16, section 9: realized consequence "must never be priced or traded
        through λ or the charter", the nonfungible core of essay II.IV.a's
        Kantorovich marketplace): ``forecast_skill`` reads settled forecasts alone.
        """
        evaluators = {a.spec.id for a in self.assemblies.values()
                      if measured_role(a.spec.emits) == "evaluator"}
        skills = [v["payoff_skill"] for eid, v in self.standing.snapshot().items()
                  if eid in evaluators and v.get("n")]
        w = replace(self.window, forecast_skills=skills)
        history, series = self._early_warning_open(w)
        book = self.observations
        # An observation is in use while a card names it: the charter's, a card a
        # pending motion would add or put in place, or a challenge's replacement under
        # trial. A motion's promise is framed on its card's observation at every ballot
        # (``_promise_frame``), so one on the agenda keeps it alive.
        agenda = [card for amendment in self.charter_book.pending()
                  for card in (*getattr(amendment, "add", ()),
                               *getattr(amendment, "replace", ()))]
        named = {normalise(c.observation)
                 for c in (*self.charter.cards, *agenda, *self._challenge_cards().values())}
        now, patience = self.ticks_consumed, self._patience()
        for oid, entry in self.registered_observations.items():
            if "trial_window" in entry and "trial_tick" not in entry:
                # Registered before the tick clock: its patience counts from now.
                entry["trial_tick"] = now
            born = entry.get("trial_tick")
            if born is None and "inactive_window" not in entry:
                self.ledger.append({"kind": "observation.inactive", "observation": oid,
                                    "version": entry["version"], "window": w.index,
                                    "tick": now, "ts": self.clock.now_ns})
                entry["inactive_window"] = w.index
            if born is None:
                entry.setdefault("inactive_tick", now)
            lifetime_start = born if born is not None else entry["inactive_tick"]
            # Time audit T5: a trial lives ``min_ratio`` consequence periods, in ticks.
            expired = now - lifetime_start >= patience
            if oid not in named and expired and not entry.get("retired"):
                self.ledger.append({"kind": "observation.retired", "observation": oid,
                                    "version": entry["version"], "window": w.index,
                                    "reason": "unused_trial_expired", "tick": now,
                                    "ts": self.clock.now_ns})
                entry["retired"] = True
        values = {}
        for observation in book.all():
            entry = self.registered_observations.get(observation.id, {})
            born = entry.get("trial_tick")
            trial = born is not None and now - born < patience
            if observation.registered and observation.id not in named and not trial:
                continue
            value = book.value(observation, w)
            if value is not None:
                values[observation.id] = value
        # Typed windows. A card may name a registered observation.
        card_values = measure_cards(self.charter.cards, self.card_samples, w, observations=book)
        card_values = {cid: value for cid, value in card_values.items() if cid in self.regions}
        self._early_warning_close(w, history, series, card_values)
        # The viability of a governance tier against the slowest loop, whose settling
        # time the live versioning measures at this close (time audit T7).
        self._check_viability()
        # Wave 16, R10-f (§III early warning): a card no window measures stays not
        # failing (M-6); how long it has been dark is published to governance, and
        # nothing is added or redefined to make it measurable (that would be Class 2).
        self.card_unmeasured = {card.id: (0 if card.id in card_values
                                          else self.card_unmeasured.get(card.id, 0) + 1)
                                for card in self.charter.cards}
        # A decision settling late is priced on the window it worked in.
        self.window.closed_relief = self._relief_sets(w.index)
        self.window.closed_values = dict(card_values)
        self.window.closed_regions = dict(self.regions)
        self.window.closed_scopes = {cid: dict(self.card_samples.scopes.get(cid) or {})
                                     for cid in card_values}
        self.window.closed_shares = [
            {"card_id": card.id, "shares": self._cost_shares(card)}
            for card in self.charter.cards
            if card.id in card_values and normalise(card.observation) == "cost_per_return"
        ]
        # Charter audit M3: each measured card's holdouts, resolved on this window.
        held = self._holdout_results(card_values)
        holdouts = {cid: row["violation"] for cid, row in held.items()}
        self.window.closed_holdouts = dict(holdouts)
        self.card_samples.holdouts = dict(holdouts)
        self.ledger.append(
            {
                "kind": "price.window",
                "window": w.index,
                "window_end_event": self.n,
                "values": card_values,  # Diary dimensions are card ids, not catalogue ids.
                "observations": values,
                "regions": {cid: asdict(region) for cid, region in self.regions.items()},
                "charter_edition": self.charter.edition,
                **({"holdouts": held} if held else {}),
                "ts": self.clock.now_ns,
            }
        )
        cards = {c.id: c for c in self.charter.cards}
        # The total pressure on the reward of each card's roles at the prices in force
        # now: at ``penalty_cap`` the window is published at the bound (R-E); only the
        # card's own bound freezes its integrator (R10-e).
        pressure = self._card_pressure(card_values, holdouts)
        for card_id in sorted(card_values):
            held = self._price_held(cards[card_id], w)
            if held is not None:
                # Time audit T2: the price loop moves only on a new settled sample, and
                # no faster than ``min_ratio`` times the loop its samples come from.
                self.ledger.append({"kind": "price.skipped", "card_id": card_id,
                                    "value": card_values[card_id], "window": w.index,
                                    "tick": now, **held})
                self.stats.price_skipped += 1
                continue
            before = self.controller.snapshot()["cards"][card_id]["updates"]
            self.controller.observe(card_id, card_values[card_id], window_end_event=self.n,
                                    holdout=holdouts.get(card_id, 0.0),
                                    anticipated=self._anticipated_violation(
                                        card_id, card_values[card_id]),
                                    pressure=pressure.get(card_id))
            if self.controller.snapshot()["cards"][card_id]["updates"] > before:
                self.card_clock[card_id] = now
                self.stats.price_updates += 1
            else:
                self.stats.price_skipped += 1
        self.rolling.update({f"{cid}_prev_median": value
                             for cid, value in self.card_samples.medians.items()})
        self._close_policy_window(w.index)  # delayed committee liability
        self.stats.last_window_values = values
        self.last_window_consequences = w.consequence_readings
        # The window's own blame is settled here, before any amendment can activate at this
        # boundary: the cards it measured and the prices its close left them holding. A verdict
        # or a late settlement from this window is attributed by this edition, never by the one
        # that replaced it (docs/manifest.md, observation units and attribution). It is frozen
        # before the immune organ runs: a ratchet it issues prices the next window, never
        # the window whose failure it diagnosed.
        self.window.closed_cards = tuple(c for c in self.charter.cards if c.id in card_values)
        self.window.closed_prices = {c.id: self.controller.price(c.id)
                                     for c in self.window.closed_cards}
        self._ledger_unattributed()
        close_window(self, values)
        # Wave 16, D5 (ruling R-I, Q9): the decisions this window priced by a count of
        # its decisions settle now, on the count frozen at its close.
        self._settle_deferred(w.index)
        self._prune_price_evidence()

    def _card_observed(self, card_id: str) -> dict[str, int]:
        """A card's consecutive closed windows with no reading (``unmeasured_windows``;
        wave 16, R10-f): 0 for a card measured at the last close or not yet closed."""
        return {"unmeasured_windows": self.card_unmeasured.get(card_id, 0)}

    def _card_pressure(self, card_values: dict[str, float],
                       holdouts: dict[str, float]) -> dict[str, float]:
        """Each measured card's total pressure on the reward: ``sum(lambda * v)`` over the
        cards of the roles it answers for, at the prices in force.

        A decision's penalty is ``min(S, penalty_cap) * share``, ``S`` that sum over its
        role's cards and the cards that answer for all (``_penalty_for``). A card that
        answers for one role presses that role's ``S``; one that answers for all
        presses every role's, so its pressure is the least of them: at the cap, no
        decision's reward moves with its price (wave 16, ruling R-E).
        """
        cards = {c.id: c for c in self.charter.cards if c.id in card_values}
        weight = {}
        for cid in cards:
            region = self.regions.get(cid)
            if region is None:
                continue
            # Saturated at the cap: an unbounded adopted price never overflows (R-E).
            weight[cid] = card_pressure(
                self.controller.price(cid),
                violation(region, card_values[cid]) + holdouts.get(cid, 0.0),
                self.m.prices.penalty_cap)
        roles = sorted({c.answers_for for c in cards.values() if c.answers_for != "all"})

        def role_sum(role: str | None) -> float:
            return sum(w for cid, w in weight.items()
                       if cards[cid].answers_for in ("all", role))

        pressure = {}
        for cid, card in cards.items():
            if card.answers_for != "all":
                pressure[cid] = role_sum(card.answers_for)
            else:
                pressure[cid] = min([role_sum(None), *(role_sum(r) for r in roles)])
        return pressure

    def _relief_sets(self, index: int) -> dict[str, dict[str, list]]:
        """Per rate observation, the window's decisions in its denominator and numerator.

        ``revision_rate`` and ``noop_share`` read the window's producer returns (their
        ``revision`` and ``noop`` flags); ``consequence_paid_off_rate`` the acting
        returns whose payoff the window settled and those that paid off. Frozen at the
        close (wave 16, D5).
        """
        rows = [row for row in self.card_samples.returns
                if row.get("window") == index and row.get("role") == "producer"]
        return {
            "revision_rate": {"members": sorted({r["handle"] for r in rows}),
                              "hits": sorted({r["handle"] for r in rows if r["revision"]})},
            "noop_share": {"members": sorted({r["handle"] for r in rows}),
                           "hits": sorted({r["handle"] for r in rows if r["noop"]})},
            "consequence_paid_off_rate": {"members": sorted(set(self.window.paid_off_settled)),
                                          "hits": sorted(set(self.window.paid_off_handles))},
        }

    def _is_niche(self, handle: str | None, window=None) -> bool:
        """Whether a decision was taken in the unhistoried niche (essay II.II.b).

        A decision of a seat with no reward trail (its protected trial), or one that
        took an unhistoried action the novelty reserve paid for (``niche.action``):
        the niche is "delivered as a fact about the world", so failed exploration
        there bears no penalty attribution (wave 16, R-E as amended).
        """
        if handle is None:
            return False
        if window is None:
            origin = self.price_origins.get(handle, {}).get("origin")
            window = self.price_windows.get(origin)
        sample = window.decisions.get(handle) if window is not None else None
        return bool(sample and sample.get("niche"))

    def _check_viability(self) -> None:
        """Ledger when a governance tier stops, or starts again, to fit (time audit T7).

        Essay II.IV.c: governance lives "between an upper bound of sampling noise
        and a lower bound of 'lagging the world'". Nonviable means that band is
        empty for this world: ``min_ratio`` times the slowest loop does not fit
        within the world's whole run or its repricing period
        (``timing.world_repricing``, converted at the delivered tick). A run nearing
        its end is not a world without a governance tier, so the bound is the total
        run length, never the ticks left. Each change of state is one ledger entry;
        the state is published in the world block. Nothing here accelerates a loop:
        that is left to the charter.
        """
        from factorylab.runtime.clockwork import ticks_for

        run = getattr(self.tick_clock, "count", None) or self.events_budget
        repricing = self.m.timing.world_repricing_ns
        state = self.cadence.viability(
            run_ticks=run,
            world_ticks=ticks_for(repricing, self.tick_clock) if repricing else None)
        if state["viable"] != self.governance_viable:
            self.ledger.append({"kind": "governance.viable" if state["viable"]
                                else "governance.nonviable", "tick": self.ticks_consumed,
                                **state, "ts": self.clock.now_ns})
            self.governance_viable = state["viable"]

    def _price_held(self, card: MetricCard, window: MeasureWindow) -> dict | None:
        """Why a card's price may not move at this close, or None when it may.

        Time audit T2. ``no_new_sample``: nothing settled in the card's scope in
        the window that closed, so its measurement is the one already priced and
        integrating it again would count the same evidence twice. ``ratio``: fewer
        than ``min_ratio`` times the card's sample loop (``_card_inner``) have
        passed since its price last moved, so the effect of that move has not
        returned yet (essay II.IV.c: correcting "against the unfinished
        transients of the controlled loop").
        """
        from factorylab.charter.measurement import fresh_sample

        if not fresh_sample(card, self.card_samples, window):
            return {"reason": "no_new_sample"}
        last = self.card_clock.get(card.id)
        inner = self._card_inner(card)
        if last is not None and self.ticks_consumed - last < self.m.timing.min_ratio * inner:
            return {"reason": "ratio", "last_tick": last, "inner_ticks": inner}
        return None

    def _early_warning_open(self, w: MeasureWindow) -> tuple[list[dict], dict]:
        """This window's score profile joins the history, and its summaries are measured.

        Essay II.III.a, ruling R3, evaluations M2: variance and lag-one
        autocorrelation at several timescales, and ensemble disagreement, computed
        online at every window close. The score series are read with the seed
        measures themselves, before any observation is valued, so the two summary
        observations (``ews_variance``, ``ews_autocorrelation``) are values of this
        window like any other and a card may price them.
        """
        from factorylab.runtime import ews
        from factorylab.runtime.observations import SEEDS

        k = self.m.immune.k
        values = {oid: SEEDS[oid].measure(w) for oid in (
            "verdict_mean", "meta_verdict_mean", "forecast_skill", "evaluator_disagreement")}
        profile = {**ews.score_profile(values), "balance": self.wallet.balance / 1_000_000}
        history = ews.history_with(self.stats.ews_history, w.index, profile, k)
        series = ews.table(history, [], k)
        w.ews_variance, w.ews_autocorrelation = ews.summary(series)
        self.window.ews_variance, self.window.ews_autocorrelation = (
            w.ews_variance, w.ews_autocorrelation)
        return history, series

    def _early_warning_close(self, w: MeasureWindow, history: list[dict], series: dict,
                             card_values: dict[str, float]) -> None:
        """Add the cards' series and publish the table to the evaluators; ledger it.

        Guarantees the published table carries every series ``early_warnings`` reads
        (the score series, each card's value series and the wallet balance) at k, 2k
        and 4k windows, and that the record is the evaluators' (``ews.view``), never
        a producer's.
        """
        from factorylab.runtime import ews

        k = self.m.immune.k
        history[-1]["profile"].update({f"card:{cid}": value for cid, value in card_values.items()})
        self.stats.ews_history = history
        cards = sorted({f"card:{c.id}" for c in self.charter.cards})
        table = ews.table(history, cards, k)
        summary = {"ews_variance": w.ews_variance, "ews_autocorrelation": w.ews_autocorrelation}
        self.stats.early_warning = {"window": w.index, "spans_windows": [k, 2 * k, 4 * k],
                                    "summary": summary, "series": table}
        supported = sorted(name for name, scales in table.items()
                           if any(s.get("variance") is not None for s in scales))
        self.ledger.append({"kind": "ews.window", "window": w.index, "summary": summary,
                            "supported_series": supported, "ts": self.clock.now_ns})

    def _anticipated_violation(self, card_id: str, value: float) -> float | None:
        """The market's expected change in a card's violation, or None without a market.

        The charter's markets (``runtime.markets``) answer this from the
        conditional forecasts on open motions; a runtime without them prices
        backward only.
        """
        return None

    def _holdout_results(self, card_values: dict[str, float]) -> dict[str, dict]:
        """Each measured card's holdouts resolved on the window that just closed.

        Essay II.IV.a: the evaluatory layer adds "holdout test criteria to a given
        charter". A holdout is a registered predicate frozen at a version; it is
        resolved on the closed window's public facts. Returns, per card with any,
        ``{"results": {id@version: bool | None}, "violation": v}``, ``v`` from
        ``charter.holdout_violation``: each failed holdout adds one resolution step
        of the card's region to its violation, and an unresolved one adds nothing.
        """
        from factorylab.charter.charter import holdout_violation
        from factorylab.runtime.observations import window_facts

        cards = [c for c in self.charter.cards if c.holdout and c.id in card_values]
        if not cards or not self.card_samples.windows:
            return {}
        facts = window_facts(self.card_samples.windows[-1])
        out = {}
        for card in cards:
            results = {entry: self._resolve_holdout(entry, facts) for entry in card.holdout}
            out[card.id] = {"results": results, "violation": holdout_violation(
                list(results.values()), self._resolution_step(card.id))}
        return out

    def _resolution_step(self, card_id: str) -> float:
        """One promise resolution of a card's region, in the region's own relative units.

        ``committee.promise_resolution * observation.scale / region.scale``: the move a
        promise must clear, as a distance the controller prices. Zero for a card with
        no region or no observation.
        """
        region = self.regions.get(card_id)
        card = next((c for c in self.charter.cards if c.id == card_id), None)
        observation = self.observations.get(card.observation) if card is not None else None
        if region is None or observation is None or region.scale <= 0:
            return 0.0
        return self.m.committee.promise_resolution * observation.scale / region.scale

    def _resolve_holdout(self, entry: str, facts: dict) -> bool | None:
        """One ``predicate@version`` on public facts; unknown or unresolved is None."""
        name, _, version = entry.partition("@")
        try:
            value, _error = self.predicates.resolve(name, {"horizon_events": 1}, facts,
                                                     version=int(version))
        except (ValueError, TypeError):
            return None
        return value

    def _ledger_unattributed(self) -> None:
        """Guarantees a priced violation that no decision will carry is ledgered, never silent.

        A scoped card routes each violating scope's part of its violation onto that
        scope's decisions priced on this window (``_attributed_share``). A scope that
        violates but has no such decision leaves its part uncharged, because nobody
        will settle against it. That part is recorded at the close as
        ``price.unattributed`` (card, scope, window, the frozen lambda, the scope's
        own violation and its part of the card's), once per card and scope. It is
        evidence that the charter's price did not bite there. It is not a charge:
        no score, wallet or learner moves (essay II.II.b prices only through the
        reward of a decision, and there is no decision here to carry it).
        """
        window = self.window
        for card in window.closed_cards:
            per = card.window.per
            price = window.closed_prices.get(card.id, 0.0)
            region = window.closed_regions.get(card.id)
            scopes = window.closed_scopes.get(card.id) or {}
            observation = self.observations.get(card.observation)
            if (per not in ("assembly", "role") or price <= 0 or region is None
                    or observation is None or not scopes):
                continue
            excess = {scope: violation(region, value) for scope, value in scopes.items()}
            total = sum(excess.values())
            if total <= 0:
                continue
            # The decisions that will settle against this window's frozen price for
            # this card: the same origin rule ``_priced_cards`` applies.
            carried = set()
            for handle, origins in self.price_origins.items():
                if origins.get(observation.id, origins.get("origin")) != window.index:
                    continue
                if card.answers_for != "all" and (
                        self._scope_of(window, handle, "role") != card.answers_for):
                    continue
                carried.add(self._scope_of(window, handle, per))
            for scope in sorted(excess):
                if excess[scope] <= 0 or scope in carried:
                    continue
                self.ledger.append({
                    "kind": "price.unattributed", "card_id": card.id, "scope": scope,
                    "per": per, "window": window.index, "lambda": price,
                    "violation": excess[scope], "part": excess[scope] / total,
                    "ts": self.clock.now_ns,
                })

    def _priced_cards(self, origins: dict[str, int]) -> list[tuple]:
        """The (card, observation, window, price) a decision is priced on, one per card id.

        A card is measured in the window its observation is attributed to. An open
        window is priced by the edition in force now; a closed one by the edition and
        the prices frozen at its close, so an amendment activated at a boundary cannot
        remove or restate a card out of what the window already attributed.
        """
        book = self.observations
        windows = [self.price_windows.get(origins.get("origin"), self.window)]
        windows += [self.price_windows[index] for key, index in origins.items()
                    if key != "origin" and index in self.price_windows]
        priced: list[tuple] = []
        seen: set[str] = set()
        for window in windows:
            closed = window.closed_values is not None and bool(window.closed_cards)
            for card in (window.closed_cards if closed else self.charter.cards):
                observation = book.get(card.observation)
                if card.id in seen or observation is None:
                    continue
                if self.price_windows.get(origins.get(observation.id, origins.get("origin")),
                                          self.window) is not window:
                    continue
                seen.add(card.id)
                priced.append((card, observation, window, window.closed_prices[card.id] if closed
                               else self.controller.price(card.id)))
        return priced

    def _penalty_terms(self, cards: str, handle: str | None, *,
                       as_role: str | None = None) -> list[dict]:
        """Late decisions keep their own windows; current windows use observed causal prefixes.

        ``as_role`` scopes ``handle`` in that role wherever a share is measured by role,
        whatever role its window recorded: an abstention priced on each role its draw
        could have woken is measured as a decision of that role (``_priced_abstention``).
        """
        terms = []
        origins = self.price_origins.get(handle, {})
        for card, observation, window, price in self._priced_cards(origins):
            if card.answers_for not in (cards, "all"):
                continue
            # The card's own typed measurement, from its decision's window when that closed.
            values = (self.card_samples.values if window.closed_values is None
                      else window.closed_values)
            regions = self.regions if window.closed_values is None else window.closed_regions
            region = regions.get(card.id)
            if region is None or card.id not in values:
                continue
            holdouts = (self.card_samples.holdouts if window.closed_values is None
                        else window.closed_holdouts)
            amount = violation(region, values[card.id]) + holdouts.get(card.id, 0.0)
            weight = card_pressure(price, amount, self.m.prices.penalty_cap)
            owner = None
            share = 1.0 if handle is None else self._decision_share(
                window, handle, observation.id, card.answers_for, region, values[card.id],
                as_role=as_role,
            )
            if handle is not None and observation.id == "cost_per_return":
                share = self._cost_share(card, window, handle, share)
            elif (handle is not None and amount > 0 and observation.id not in _EXACT_SHARES
                  and not (observation.id in RELIEF_RATES
                           and handle in self._relievers(window, observation.id, region,
                                                         values[card.id]))):
                # Relief takes precedence in every scope (wave 16, D5; Codex on #152):
                # a decision that moved the rate toward its region bears nothing,
                # whatever its scope's aggregate, so it never takes a scope's share.
                scopes = (self.card_samples.scopes if window.closed_values is None
                          else window.closed_scopes).get(card.id) or {}
                attributed = self._attributed_share(window, handle, card, region, scopes,
                                                    as_role=as_role)
                if attributed is not None:
                    share, owner = attributed
            term = {"card_id": card.id, "observation": observation.id,
                    "window": window.index, "violation": amount,
                    "lambda": price, "weight": weight,
                    "share": share}
            if owner is not None:
                term["owner"] = owner
            terms.append(term)
        return terms

    def _scope_of(self, window, handle: str, per: str) -> str | None:
        """The scope a decision is measured in for a per-assembly or per-role card."""
        if per == "assembly":
            return self.handle_to_assembly.get(handle)
        sample = window.decisions.get(handle)
        return sample["role"] if sample is not None else self._decision_role(handle)

    def _attributed_share(self, window, handle: str, card, region,
                          scopes: dict[str, float], *,
                          as_role: str | None = None) -> tuple[float, str | None] | None:
        """Route a scoped card's violation onto the scopes whose own samples violate it.

        A card measured per assembly (or per role) is the mean of its scopes, so
        its violation is attributable: each violating scope owns the part of it
        its own distance outside the region contributes, and a scope inside the
        region owns none. A decision carries its scope's part divided among that
        scope's decisions that responded in the window, never below
        ``prices.min_blame_share`` of the part. Returns ``(share, owner scope)``,
        with share ``0.0`` for a decision whose scope is compliant, or None when
        the card is not scoped or no scope violates, so the caller keeps the
        generic floor-split for a violation with no attributable owner.
        """
        per = card.window.per
        if per not in ("assembly", "role") or not scopes:
            return None
        excess = {scope: violation(region, value) for scope, value in scopes.items()}
        total = sum(excess.values())
        if total <= 0:
            return None
        own = (as_role if as_role is not None and per == "role"
               else self._scope_of(window, handle, per))
        if not excess.get(own):
            return 0.0, own
        # Decisions taken in the unhistoried niche are not counted (wave 16, R-E).
        peers = {h for h, d in self._split_decisions(window).items()
                 if h != handle and (d["invocations"] or d["ok"] or not d["cost"])
                 and self._scope_of(window, h, per) == own}
        peers.add(handle)
        part = excess[own] / total
        return part * max(self.m.prices.min_blame_share, 1 / len(peers)), own

    def _cost_share(self, card, window, handle: str, contributed: float) -> float:
        """A closed window owns the shares it froze; a live one re-reads its sample now.

        A decision made and settled inside one window is priced on the card's
        selected returns as they stand, so its own cost carries its own part of
        the pressure. Only a settlement delayed past its window's closure reads
        that window's frozen ownership, and a live window whose card selects no
        returns falls back to its observed contribution totals.
        """
        if window.closed_values is not None:
            for frozen in window.closed_shares:
                if frozen["card_id"] == card.id:
                    return frozen["shares"].get(handle, 0.0)
            return contributed
        shares = self._cost_shares(card, live=True)
        return shares.get(handle, 0.0) if shares else contributed

    def _cost_shares(self, card, *, live: bool = False) -> dict[str, float]:
        """Cost ownership uses exactly the supported scopes and successful selected returns.

        A closed window owns the scopes its own measurement supported. A live
        window has no closed record to select yet, so a window-selector card
        reads the returns that window has made so far, in every scope it shows.
        """
        samples = self.card_samples
        supported = None if live else samples.scopes.get(card.id, {})
        rows = samples.returns
        if card.window.kind == "windows":
            if live:
                first = last = self.window.index
            elif samples.windows:
                selected = samples.windows[-card.window.n:]
                first, last = selected[0]["index"], selected[-1]["index"]
            else:
                return {}
            rows = [r for r in rows if first <= r["window"] <= last]
            if card.window.per is None:
                rows = [r for r in rows if r["role"] == "producer"]
                if not rows:
                    # Global window sufficient statistics also support native callers
                    # that supplied contribution records without invocation samples.
                    rows = [dict(d, handle=h) for index, w in self.price_windows.items()
                            if first <= index <= last for h, d in w.decisions.items()
                            if d["role"] == "producer"]
        shares: dict[str, Fraction] = {}
        for scope, group in _groups(card, rows).items():
            if supported is not None and scope not in supported:
                continue
            if card.window.kind == "returns":
                # The same horizon the card measured.
                group = _horizon(card.observation, group, card.window.n, partial=True)
            # A niche decision's return owns no cost share and dilutes none (R-E).
            successful = [r for r in group if r["ok"] and self._in_split(r["handle"])]
            # The scope's mean cost, measured the way the card measured it: a
            # scope with no response has no measured cost to own.
            responses = len(successful)
            if not responses:
                continue
            for row in successful:
                handle = row["handle"]
                shares[handle] = shares.get(handle, Fraction()) + Fraction(
                    row["cost"], responses)
        total = sum(shares.values())
        return {h: float(amount / total) for h, amount in shares.items()} if total else {}

    def _in_split(self, handle: str, window=None) -> bool:
        """Whether a decision is counted when a card's penalty is split across decisions.

        Wave 16, R-E as amended: a decision taken in the unhistoried niche bears
        nothing and changes nobody's split. Every share, its numerator and its
        denominator (relief rates, cost, well-formedness, tool calls, turnover, the
        frozen cost shares, a scope's peers), reads its decisions through this one
        predicate, so no path counts a niche decision another path does not.
        """
        return not self._is_niche(handle, window)

    def _split_decisions(self, window) -> dict[str, dict]:
        """The window's decisions a card's penalty is split across (``_in_split``)."""
        return {h: d for h, d in window.decisions.items() if self._in_split(h, window)}

    def _decision_share(self, window, handle, observation, role, region, value, *,
                        as_role: str | None = None) -> float:
        """Attributable violations use own contributions; rates by relief; others by count.

        A rate observation (``RELIEF_RATES``; wave 16, D5) is attributed by relief:
        a decision that moved the rate toward its region (for a floor, one in its
        numerator; for a ceiling, one in its denominator and not its numerator) bears
        nothing, and every other decision of the scope bears an equal share, ``1 /
        n``, NOOP and declines included (ruling R9). Any other generic observation
        divides by the scope's decisions, never below ``prices.min_blame_share``.
        The count is the window's decisions when it closed, so a share never depends
        on the order decisions settled in, and decisions taken in the unhistoried
        niche are not counted: they bear nothing and change nobody's share.
        ``as_role`` counts ``handle`` among that role's decisions, whatever role its
        window recorded.
        """
        if not self._in_split(handle, window):
            return 0.0
        samples = self._split_decisions(window)
        own = samples.get(handle, {})
        numerator = denominator = 0
        if observation in ("cost_per_return", "cost_per_attempt"):
            # Per return, only successful cost is measured; per attempt, every
            # invocation's cost is spent and owned, failed ones included.
            eligible = {h: d["cost"] for h, d in samples.items()
                        if (role == "all" or d["role"] == role)
                        and (d["ok"] if observation == "cost_per_return" else d["cost"])}
            numerator, denominator = eligible.get(handle, 0), sum(eligible.values())
        elif observation == "well_formed_rate":
            deficit = region.kind in ("min", "band") and value < region.lo
            contributions = {h: d["invocations"] - d["ok"] if deficit else d["ok"]
                             for h, d in samples.items()}
            numerator, denominator = contributions.get(handle, 0), sum(contributions.values())
        elif observation in ("tool_calls", "turnover"):
            # Divided by what the decisions in the split contributed, never by the
            # window's total, which a niche decision's calls or notional would dilute.
            key = "tool_calls" if observation == "tool_calls" else "notional_micro"
            numerator = own.get(key, 0)
            denominator = sum(d.get(key, 0) for d in samples.values())
        else:
            # A decision whose only entry in this window is money spent made no
            # response this observation reads, so it does not take a share of the
            # violation and does not dilute the shares that do.
            scope = [h for h, d in samples.items()
                     if (d["invocations"] or d["ok"] or not d["cost"])
                     and (role == "all" or (as_role if h == handle and as_role is not None
                                            else d["role"]) == role)]
            if handle not in scope:
                scope.append(handle)
            if observation in RELIEF_RATES:
                relievers = self._relievers(window, observation, region, value)
                if handle in relievers:
                    return 0.0
                return 1.0 / max(1, sum(h not in relievers for h in scope))
            return max(self.m.prices.min_blame_share, 1 / max(1, len(scope)))
        return min(1.0, numerator / denominator) if denominator > 0 else 0.0

    def _relievers(self, window, observation: str, region, value: float) -> set[str]:
        """The decisions that moved a rate toward its region in ``window``.

        A floor (or a band read below its low bound) is relieved by the decisions in
        the rate's numerator; a ceiling (or a band read above its high bound) by the
        decisions in its denominator and not its numerator. Read from the relief sets
        the window froze at its close, or live from an open window.
        """
        sets = (window.closed_relief or {}).get(observation)
        if sets is None:
            sets = self._relief_sets(window.index).get(observation, {})
        members, hits = set(sets.get("members", ())), set(sets.get("hits", ()))
        floor = region.kind == "min" or (region.kind == "band" and value < region.lo)
        return hits if floor else members - hits

    def _penalty_for(self, cards: str, handle: str | None = None, *,
                     as_role: str | None = None) -> float:
        """Cap the total pressure, then allocate its penalty-weighted contribution share.

        A decision taken in the unhistoried niche bears no penalty (wave 16, R-E as
        amended: "the prevention of learning death should be delivered as a fact about
        the world"). It is a penalty rule, never a reward floor: the decision keeps
        whatever its judges gave it.
        """
        if not self._in_split(handle):
            return 0.0
        terms = self._penalty_terms(cards, handle, as_role=as_role)
        total = sum(t["weight"] for t in terms)
        if total <= 0:
            return 0.0
        share = sum(t["weight"] * t["share"] for t in terms) / total
        return min(total, self.m.prices.penalty_cap) * share

    def _awaits_close(self, cards: str, handle: str | None) -> bool:
        """Whether a decision's penalty waits for its origin window to close.

        True while that window is open and one of the decision's cards (one with a
        region, answering for its role) bears on it with a share counted over the
        window's decisions (a rate by relief, or any other generic observation):
        that count, the window's violation and the price it closes at are final only
        at the close, so whether it bites is not known before it. A niche decision
        bears nothing and waits for nothing.
        """
        if handle is None:
            return False
        origins = self.price_origins.get(handle, {})
        window = self.price_windows.get(origins.get("origin"))
        if window is None or window.closed_values is not None:
            return False
        if not self._in_split(handle, window):  # the origin window's record (Sol F3)
            return False
        return any(card.answers_for in (cards, "all") and card.id in self.regions
                   and observation.id not in _EXACT_SHARES and w is window
                   for card, observation, w, _price in self._priced_cards(origins))

    def _settle_deferred(self, index: int) -> None:
        """Settle every decision whose penalty waited for window ``index`` to close."""
        for handle in sorted(h for h in self.deferred_settlements
                             if self.price_origins.get(h, {}).get("origin") == index):
            row = self.deferred_settlements.pop(handle)
            if self.queue.get(handle).status not in (SettleStatus.PENDING,
                                                     SettleStatus.TIMED_OUT):
                continue
            self._settle_priced(handle, channel=row["channel"], score=row["score"],
                                definition_version=row["definition_version"],
                                sampling_ref=row["sampling_ref"], cards=row["cards"],
                                unresolved=tuple(row["unresolved"]))

    def _settle_priced(
        self,
        handle: str,
        *,
        channel: str,
        score: float,
        definition_version: str,
        sampling_ref: str | None,
        cards: str,
        unresolved: tuple[str, ...] = (),
    ) -> None:
        """Settle a judged score less the card penalty, clipped to [0, 1]; both are ledgered.

        The penalty is subtracted, as the essay's Lagrangian prescribes (reward less
        lambda times cost), and the result is clipped at zero because a settled
        score is a unit-interval reward: a penalty larger than the raw score takes
        the whole reward and no more. ``prices.penalty_cap`` < 1 already bounds it.

        A decision whose own commitments were left avoidably unresolved
        (``unresolved`` names them) has no observed score, so it settles censored,
        never as a zero; its penalty is carried on that censored settlement under
        ``UNRESOLVED_PRICED`` for the record, and its learners are credited as for
        an abstention, the router's observed mean raw score less the same penalty
        (``_learn_router_return``; wave 16, D4).
        """
        if handle not in self.price_origins:
            self._contribution(handle, cards)
        if self._awaits_close(cards, handle):
            # Wave 16, D5 (ruling R-I, Q9): its share is a count of the window's
            # decisions, frozen when the window closes, so it settles then.
            self.deferred_settlements[handle] = {
                "channel": channel, "score": score, "definition_version": definition_version,
                "sampling_ref": sampling_ref, "cards": cards, "unresolved": list(unresolved)}
            self.ledger.append({"kind": "price.deferred", "handle": handle,
                                "window": self.price_origins[handle]["origin"],
                                "ts": self.clock.now_ns})
            return
        penalty = self._penalty_for(cards, handle)
        if not unresolved:
            # The score before its card penalty: what the router's observed mean is made
            # of (``RouterState.neutral``; wave 16, D4), taken when the router learns it,
            # and with its penalty what every learner learns (``_learned``; R10-g).
            self.raw_scores[handle] = float(score)
            self.round_penalties[handle] = float(penalty)
        if unresolved:
            status, effective = SettleStatus.CENSORED, None
            definition_version, settled_score = UNRESOLVED_PRICED, penalty
        else:
            effective = min(1.0, max(0.0, score - penalty))
            status, settled_score = SettleStatus.SETTLED, effective
        self.queue.settle(
            handle,
            channel=channel,
            score=settled_score,
            status=status,
            definition_version=definition_version,
            sampling_ref=sampling_ref,
        )
        self.window.outcomes += 1
        entry = {
            "kind": "price.penalty",
            "handle": handle,
            "channel": channel,
            "raw": None if unresolved else score,
            "penalty": penalty,
            "effective": effective,
            "penalty_cap": self.m.prices.penalty_cap,
            "terms": self._penalty_terms(cards, handle),
            "ts": self.clock.now_ns,
        }
        if unresolved:
            entry["unresolved"] = list(unresolved)
        self.ledger.append(entry)
        if penalty > 0:
            self.stats.penalized_settlements += 1
