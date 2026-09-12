"""Runtime pricing method group."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field, replace
from decimal import Decimal

from factorylab.charter.controller import CardRegion, violation
from factorylab.charter.measurement import measure_cards
from factorylab.kernel.events import Event, EventKind
from factorylab.kernel.queue import SettleStatus
from factorylab.runtime.cards import parses, region_for
from factorylab.runtime.immune import close_window
from factorylab.runtime.observations import ObservationBook
from factorylab.runtime.shared import _usd_to_micro


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


class PricingMixin:
    """Preserve runtime state and behavior for pricing operations."""

    @property
    def observations(self) -> ObservationBook:
        """The factory's live measurement vocabulary: the seeds plus what it registered.

        A11. Every consumer of a card's observation reads through this book, so a
        registered measurement is priced, published and diagnosed exactly like a
        seed one; only the way it is computed differs.
        """
        return ObservationBook(self.registered_observations, run=self.observation_runner.run)

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

    def _invoke(self, action_id, req, role, *, child=False):
        """Prices retain the completed decision's own cost, schema result and tool attempts."""
        before_calls = self.window.tool_calls
        before_children = sum(d["tool_calls"] for d in self.window.decisions.values())
        ret = super()._invoke(action_id, req, role, child=child)
        child_calls = sum(d["tool_calls"] for d in self.window.decisions.values()) - before_children
        sample = self._contribution(req.handle, role)
        evidence = {"cost": ret.cost, "ok": int(ret.status == "ok"), "invocations": 1,
                    "tool_calls": self.window.tool_calls - before_calls - child_calls}
        self.ledger.append({"kind": "price.contribution", "handle": req.handle,
                            "window": self.window.index, "role": role, **evidence})
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
        notional = _usd_to_micro(Decimal(str(payload["size"])) * Decimal(str(payload["px"])))
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
            if self.reserve_window_start is not None:
                self._close_price_window()
            self.reserve.open_window(self.clock.now_ns, self.wallet.balance)
            self.reserve_window_start = self.clock.now_ns
            self.market_index = None
            self.stats.reserve_windows += 1
            self._issue_novelty_grant()
            self.window = MeasureWindow(self.stats.reserve_windows, self._equity_micro())
            self.price_windows[self.window.index] = self.window
            self._observe_positions()
            self._activate_charter_if_due()
            self._derive_regions()

    def _issue_novelty_grant(self) -> None:
        """Learning death in the window that closed grants one extra novelty trial per
        assembly for the window that opens (spec A13); whatever the previous grant left
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
        book = self.observations
        values = {o.id: value for o in book.all() if (value := book.value(o, w)) is not None}
        # A6: typed windows. A11: a card may name a registered observation.
        card_values = measure_cards(self.charter.cards, self.card_samples, w, observations=book)
        card_values = {cid: value for cid, value in card_values.items() if cid in self.regions}
        # A4: a decision settling late is priced on the window it worked in.
        self.window.closed_values = dict(card_values)
        self.window.closed_regions = dict(self.regions)
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
        self._close_policy_window(w.index)  # A15: delayed committee liability
        self.stats.last_window_values = values
        self.controller.set_decay(self.m.prices.decay, ledger=self.ledger, window=w.index)
        close_window(self, values)
        self._prune_price_evidence()

    def _penalty_terms(self, cards: str, handle: str | None) -> list[dict]:
        """Late decisions keep their own windows; current windows use observed causal prefixes."""
        terms = []
        origins = self.price_origins.get(handle, {})
        for card in self.charter.cards:
            observation = self.observations.get(card.observation)
            if card.answers_for not in (cards, "all") or observation is None:
                continue
            window = self.price_windows.get(origins.get(observation.id, origins.get("origin")),
                                            self.window)
            # A6: the card's own typed measurement, from its decision's window when that closed.
            values = (self.card_samples.values if window.closed_values is None
                      else window.closed_values)
            regions = self.regions if window.closed_values is None else window.closed_regions
            region = regions.get(card.id)
            if region is None or card.id not in values:
                continue
            amount = violation(region, values[card.id])
            weight = self.controller.price(card.id) * amount
            share = 1.0 if handle is None else self._decision_share(
                window, handle, observation.id, card.answers_for, region, values[card.id]
            )
            terms.append({"card_id": card.id, "observation": observation.id,
                          "window": window.index, "violation": amount,
                          "lambda": self.controller.price(card.id), "weight": weight,
                          "share": share})
        return terms

    @staticmethod
    def _decision_share(window, handle, observation, role, region, value) -> float:
        """Attributable violations use own contributions; other observations divide by support."""
        samples = window.decisions
        own = samples.get(handle, {})
        numerator = denominator = 0
        if observation == "cost_per_return":
            eligible = {h: d["cost"] for h, d in samples.items()
                        if d["role"] == "producer" and d["ok"]}
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
