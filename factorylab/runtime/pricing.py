"""Runtime pricing method group."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field, replace
from decimal import Decimal
from fractions import Fraction

from factorylab.charter.charter import MetricCard
from factorylab.charter.controller import CardRegion, relative_region, violation
from factorylab.charter.measurement import _groups, measure_cards
from factorylab.cortex.registration import measured_role
from factorylab.kernel.events import Event, EventKind
from factorylab.kernel.money import usd_to_micro
from factorylab.kernel.queue import SettleStatus
from factorylab.runtime.cards import parses, region_for
from factorylab.runtime.immune import close_window
from factorylab.runtime.observations import ObservationBook, normalise, trim_series


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
    decisions: dict[str, dict] = field(default_factory=dict)
    closed_values: dict[str, float] | None = None
    closed_regions: dict[str, CardRegion] = field(default_factory=dict)
    closed_shares: list[dict] = field(default_factory=list)
    # The edition that priced the window, frozen with its prices at the close. A later
    # amendment cannot remove or restate a card out of what this window already attributed.
    closed_cards: tuple[MetricCard, ...] = ()
    closed_prices: dict[str, float] = field(default_factory=dict)
    mids: list[dict] = field(default_factory=list)
    funding: list[dict] = field(default_factory=list)
    wallet_balance_micro: list[list[int]] = field(default_factory=list)
    tick_timestamps_ns: list[int] = field(default_factory=list)
    books: list[dict] = field(default_factory=list)
    # How many samples each bounded public series has already discarded, by the
    # path it is published at. Private attribution, never a window observation:
    # it is what turns a retained index back into a position in the whole window.
    series_discarded: dict[str, int] = field(default_factory=dict)


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

    def _charge_storage(self, handle: str, cost_micro: int) -> None:
        """Bind a metered retained-storage charge to a decision that can be scored for it.

        Retained public storage is an explicit liability of the decision that
        holds it. Every charge enters that decision's cost contribution for the
        window it landed in, so the charter's cost cards see it where it was
        spent. While the decision's own consequence outcome is still open the
        charge is also carried into that outcome's cost, so a return cannot pay
        off on a margin its storage has already consumed. An outcome is fixed
        once and never reopened, so afterwards the cost contribution is the whole
        of the liability and it stays with the note's current owner decision.
        """
        if cost_micro <= 0:
            return
        sample = self._contribution(handle, self._decision_role(handle))
        sample["cost"] += cost_micro
        carried = self.consequences.carry(handle, cost_micro)
        self.ledger.append({"kind": "price.contribution", "handle": handle,
                            "window": self.window.index, "role": sample["role"],
                            "cost": cost_micro, "storage": True, "carried": carried})

    def _invoke(self, action_id, req, role, *, child=False):
        """Prices retain the completed decision's own cost, schema result and tool attempts."""
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
                            "window": self.window.index, "role": observed_role, **evidence})
        sample["role"] = observed_role
        for name, value in evidence.items():
            sample[name] += value
        return ret

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
        self.cadence.advance(self.n)
        if self.reserve_window_start is None:
            self.cadence.launch(self.clock.now_ns if self.live else 0)
        if (
            self.reserve_window_start is None
            or self.clock.now_ns >= self.reserve_window_start + self.m.novelty.window_ns
        ):
            closed = self.window.index if self.reserve_window_start is not None else None
            if closed is not None:
                self._close_price_window()
            self.reserve.open_window(self.clock.now_ns, self.wallet.balance)
            self.reserve_window_start = self.clock.now_ns
            self.market_index = None
            self.stats.reserve_windows += 1
            self._issue_novelty_grant()
            self.window = MeasureWindow(self.stats.reserve_windows, self._equity_micro())
            from factorylab.runtime.notes import charge_window

            charge_window(self)
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

    def _issue_novelty_grant(self) -> None:
        """Learning death in the window that closed grants one extra novelty trial per
        assembly for the window that opens; whatever the previous grant left
        unspent expires here, and the flag must be raised again to re-issue it."""
        flagged = bool(self.stats.pathologies.get("learning_death"))
        window = self.stats.reserve_windows if flagged else None
        self.novelty_grant = {"window": window, "consumed": []}
        if flagged:
            self.ledger.append({"kind": "novelty.grant", "window": window,
                                "ts": self.clock.now_ns})
    def _prune_price_evidence(self) -> None:
        """Completed decisions release old attribution windows after their totals are frozen."""
        for handle in tuple(self.price_origins):
            if (self.queue.get(handle).status not in (SettleStatus.PENDING, SettleStatus.TIMED_OUT)
                    and handle not in self.pending and handle not in self.pending_exposure):
                del self.price_origins[handle]
        retained = {index for origins in self.price_origins.values() for index in origins.values()}
        self.price_windows = {index: window for index, window in self.price_windows.items()
                              if index == self.window.index or index in retained}

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
            series.append({"coin": str(ev.payload["coin"]), "ts_ns": ev.ts_ns, "value": value})
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
                                    "ts": self.clock.now_ns})
                entry["trial_window"] = self.window.index
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

    def _derive_regions(self) -> None:
        """Every readable card of the current edition holds a region; unreadable ones hold none.

        New cards register; cards whose bound moved (a rolling median, a restated
        card) keep their price and get the new bounds. Each change is a
        ``price.region`` entry; prose the runtime cannot read is logged once per
        card per edition as ``price.unparsed``.
        """
        regions: dict[str, CardRegion] = {}
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
        forecast_skill: mean consequence-standing skill over evaluators with
        settled forecasts; turnover: filled notional over equity at the window
        start (0 with no fills). A quantity without support is not observed.
        """
        evaluators = {a.spec.id for a in self.assemblies.values()
                      if measured_role(a.spec.emits) == "evaluator"}
        skills = [
            v["skill"]
            for eid, v in self.standing.snapshot().items()
            if eid in evaluators and v.get("n")
        ]
        w = replace(self.window, forecast_skills=skills)
        book = self.observations
        named = {normalise(c.observation) for c in self.charter.cards}
        for oid, entry in self.registered_observations.items():
            born = entry.get("trial_window")
            if born is None and "inactive_window" not in entry:
                self.ledger.append({"kind": "observation.inactive", "observation": oid,
                                    "version": entry["version"], "window": w.index,
                                    "ts": self.clock.now_ns})
                entry["inactive_window"] = w.index
            lifetime_start = born if born is not None else entry["inactive_window"]
            expired = w.index - lifetime_start >= self.m.novelty.max_lifetime_windows
            if oid not in named and expired and not entry.get("retired"):
                self.ledger.append({"kind": "observation.retired", "observation": oid,
                                    "version": entry["version"], "window": w.index,
                                    "reason": "unused_trial_expired", "ts": self.clock.now_ns})
                entry["retired"] = True
        values = {}
        for observation in book.all():
            entry = self.registered_observations.get(observation.id, {})
            born = entry.get("trial_window")
            trial = born is not None and w.index - born < self.m.novelty.max_lifetime_windows
            if observation.registered and observation.id not in named and not trial:
                continue
            value = book.value(observation, w)
            if value is not None:
                values[observation.id] = value
        # Typed windows. A card may name a registered observation.
        card_values = measure_cards(self.charter.cards, self.card_samples, w, observations=book)
        card_values = {cid: value for cid, value in card_values.items() if cid in self.regions}
        # A decision settling late is priced on the window it worked in.
        self.window.closed_values = dict(card_values)
        self.window.closed_regions = dict(self.regions)
        self.window.closed_shares = [
            {"card_id": card.id, "shares": self._cost_shares(card)}
            for card in self.charter.cards
            if card.id in card_values and normalise(card.observation) == "cost_per_return"
        ]
        self.ledger.append(
            {
                "kind": "price.window",
                "window": w.index,
                "window_end_event": self.n,
                "values": card_values,  # Diary dimensions are card ids, not catalogue ids.
                "observations": values,
                "regions": {cid: asdict(region) for cid, region in self.regions.items()},
                "charter_edition": self.charter.edition,
                "ts": self.clock.now_ns,
            }
        )
        self.controller.expire_relief(window=w.index)
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
        self.rolling.update({f"{cid}_prev_median": value
                             for cid, value in self.card_samples.medians.items()})
        self._close_policy_window(w.index)  # delayed committee liability
        self.stats.last_window_values = values
        self.controller.set_decay(self.m.prices.decay, ledger=self.ledger, window=w.index)
        close_window(self, values)
        # The window's own blame is settled here, before any amendment can activate at this
        # boundary: the cards it measured and the prices its close left them holding. A verdict
        # or a late settlement from this window is attributed by this edition, never by the one
        # that replaced it (docs/manifest.md, observation units and attribution).
        self.window.closed_cards = tuple(c for c in self.charter.cards if c.id in card_values)
        self.window.closed_prices = {c.id: self.controller.price(c.id)
                                     for c in self.window.closed_cards}
        self._prune_price_evidence()

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

    def _penalty_terms(self, cards: str, handle: str | None) -> list[dict]:
        """Late decisions keep their own windows; current windows use observed causal prefixes."""
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
            amount = violation(region, values[card.id])
            weight = price * amount
            share = 1.0 if handle is None else self._decision_share(
                window, handle, observation.id, card.answers_for, region, values[card.id]
            )
            if handle is not None and observation.id == "cost_per_return":
                share = self._cost_share(card, window, handle, share)
            terms.append({"card_id": card.id, "observation": observation.id,
                          "window": window.index, "violation": amount,
                          "lambda": price, "weight": weight,
                          "share": share})
        return terms

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
                group = group[-card.window.n:]
            successful = [r for r in group if r["ok"]]
            for row in successful:
                handle = row["handle"]
                shares[handle] = shares.get(handle, Fraction()) + Fraction(
                    row["cost"], len(successful))
        total = sum(shares.values())
        return {h: float(amount / total) for h, amount in shares.items()} if total else {}

    @staticmethod
    def _decision_share(window, handle, observation, role, region, value) -> float:
        """Attributable violations use own contributions; other observations divide by support."""
        samples = window.decisions
        own = samples.get(handle, {})
        numerator = denominator = 0
        if observation == "cost_per_return":
            eligible = {h: d["cost"] for h, d in samples.items()
                        if (role == "all" or d["role"] == role) and d["ok"]}
            numerator, denominator = eligible.get(handle, 0), sum(eligible.values())
        elif observation == "well_formed_rate":
            deficit = region.kind in ("min", "band") and value < region.lo
            contributions = {h: d["invocations"] - d["ok"] if deficit else d["ok"]
                             for h, d in samples.items()}
            numerator, denominator = contributions.get(handle, 0), sum(contributions.values())
        elif observation in ("tool_calls", "turnover"):
            key = "tool_calls" if observation == "tool_calls" else "notional_micro"
            numerator, denominator = own.get(key, 0), getattr(window, key)
        else:
            n = sum(role == "all" or d["role"] == role for d in samples.values())
            return 1 / max(1, n)
        return min(1.0, numerator / denominator) if denominator > 0 else 0.0

    def _penalty_for(self, cards: str, handle: str | None = None) -> float:
        """Cap the total pressure, then allocate its penalty-weighted contribution share."""
        terms = self._penalty_terms(cards, handle)
        total = sum(t["weight"] for t in terms)
        if total <= 0:
            return 0.0
        share = sum(t["weight"] * t["share"] for t in terms) / total
        return min(total, self.m.prices.penalty_cap) * share

    def _settle_priced(
        self,
        handle: str,
        *,
        channel: str,
        score: float,
        definition_version: str,
        sampling_ref: str | None,
        cards: str,
    ) -> None:
        """Settle a judged score less the card penalty, clipped to [0, 1]; both are ledgered."""
        if handle not in self.price_origins:
            self._contribution(handle, cards)
        penalty = self._penalty_for(cards, handle)
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
                "penalty_cap": self.m.prices.penalty_cap,
                "terms": self._penalty_terms(cards, handle),
                "ts": self.clock.now_ns,
            }
        )
        if penalty > 0:
            self.stats.penalized_settlements += 1
