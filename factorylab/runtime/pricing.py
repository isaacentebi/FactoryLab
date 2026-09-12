"""Runtime pricing method group."""

from __future__ import annotations

from dataclasses import dataclass, field, replace

from factorylab.charter.controller import CardRegion
from factorylab.charter.measurement import measure_cards
from factorylab.kernel.events import Event, EventKind
from factorylab.kernel.queue import SettleStatus
from factorylab.runtime.cards import parses, region_for
from factorylab.runtime.immune import close_window
from factorylab.runtime.observations import CATALOGUE, observation_for


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


class PricingMixin:
    """Preserve runtime state and behavior for pricing operations."""

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
            self.market_index = None
            self.stats.reserve_windows += 1
            self.window = MeasureWindow(self.stats.reserve_windows, self._equity_micro())
            self._observe_positions()
            self._activate_charter_if_due()
            self._derive_regions()

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
        card_values = measure_cards(self.charter.cards, self.card_samples, w)  # A6: typed windows
        card_values = {cid: value for cid, value in card_values.items() if cid in self.regions}
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
        self.rolling.update({f"{cid}_prev_median": value
                             for cid, value in self.card_samples.medians.items()})
        self._close_policy_window(w.index)  # A15: delayed committee liability
        self.stats.last_window_values = values
        self.controller.set_decay(self.m.prices.decay, ledger=self.ledger, window=w.index)
        close_window(self, values)

    def _penalty_for(self, cards: str) -> float:
        """Σ λ_j · violation_j over the latest window's values for cards the role answers for."""
        values = {
            card.id: self.card_samples.values[card.id]
            for card in self.charter.cards
            if card.answers_for in (cards, "all")
            and card.id in self.regions
            and observation_for(card.observation) is not None
            and card.id in self.card_samples.values
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
        cards: str,
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
