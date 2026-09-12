"""The live path: wall-clock ticks, a real venue, real models.

Spec v0.5 section 8. Nothing here changes physics. ``LiveClock`` paces the
loop against wall-clock time; ``LiveVenue`` turns venue reads into the same
world events the fake venue produces; ``Reconciler`` periodically compares
the wallet with the two real pots (OpenRouter credits, venue equity) and
records the discrepancy. The wallet stays authoritative.
"""

from __future__ import annotations

import os
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from factorylab.world.clock import ClockIterator
from factorylab.world.events import WorldEvent, WorldEventKind

NS_PER_SECOND = 1_000_000_000


@dataclass
class LiveClock:
    """Yields one ``Tick`` per ``interval_ns`` of wall-clock time, ``count`` times.

    The first tick is immediate. ``sleep`` and ``now_ns`` are injectable so
    tests run without waiting. Guarantees strictly increasing timestamps.
    """

    interval_ns: int
    count: int
    now_ns: Callable[[], int] = time.time_ns
    sleep: Callable[[float], None] = time.sleep
    source: str = "wallclock"
    index: int = 0
    last_ns: int = -1

    def set_interval(self, interval_ns: int) -> None:
        """Adopt positive integer nanoseconds for the next tick after the current yield."""
        if type(interval_ns) is not int or interval_ns <= 0:
            raise ValueError("interval_ns must be positive integer nanoseconds")
        self.interval_ns = interval_ns

    def events(self) -> ClockIterator:
        """Return a wall-clock stream whose interval remains amendable when injected."""
        return ClockIterator(self, self._events())

    def _events(self) -> Iterator[WorldEvent]:
        while self.index < self.count:
            if self.last_ns >= 0:
                target = self.last_ns + self.interval_ns
                wait = target - self.now_ns()
                if wait > 0:
                    self.sleep(wait / NS_PER_SECOND)
            ts = max(self.now_ns(), self.last_ns + 1)
            self.last_ns = ts
            i = self.index
            self.index += 1
            yield WorldEvent(WorldEventKind.TICK, ts, self.source, {"index": i})

    def state(self) -> dict:
        """Retain the original budget, next tick index and last delivered timestamp."""
        return {"interval_ns": self.interval_ns, "count": self.count, "source": self.source,
                "index": self.index, "last_ns": self.last_ns}

    @classmethod
    def restore(cls, state: dict, *, now_ns=time.time_ns, sleep=time.sleep) -> LiveClock:
        """Continue the saved clock with fresh process-local time and sleep functions."""
        return cls(**state, now_ns=now_ns, sleep=sleep)


@dataclass
class LiveVenue:
    """Adapts a real ``Exchange`` to per-tick world events.

    Each tick reads mids and funding; when the exchange has an account, new
    fills since the last poll are emitted with their realised P&L. Rates remain
    observations; separate venue-identified funding payments carry actual cash.
    """

    exchange: Any
    last_fill_ns: int = 0
    seen_fills: set[str] = field(default_factory=set)
    ledger: Any = None
    last_funding_ns: int | None = None
    seen_funding: set[str] = field(default_factory=set)

    def funding_payments(self, now_ns: int) -> list[WorldEvent]:
        """Emit post-launch funding once, with an inclusive cursor that keeps timestamp peers."""
        if self.last_funding_ns is None:
            if self.ledger is not None:
                self.ledger.append({"kind": "funding.cursor", "since_ns": now_ns, "seen": []})
            self.last_funding_ns = now_ns
        method = getattr(self.exchange, "funding_payments", None)
        if method is None:
            return []  # read-only legacy/test adapter
        try:
            payments = method(self.last_funding_ns)
        except RuntimeError:
            return []  # no key or transient venue outage: preserve cursor
        payments = sorted((p for p in payments if p.ts_ns >= self.last_funding_ns
                           and p.id not in self.seen_funding), key=lambda p: (p.ts_ns, p.id))
        if not payments:
            return []
        latest = max(p.ts_ns for p in payments)
        seen = {p.id for p in payments if p.ts_ns == latest}
        if latest == self.last_funding_ns:
            seen |= self.seen_funding
        if self.ledger is not None:
            self.ledger.append({"kind": "funding.cursor", "since_ns": latest,
                                "seen": sorted(seen)})
        self.last_funding_ns, self.seen_funding = latest, seen
        return [WorldEvent(WorldEventKind.FUNDING, p.ts_ns, self.exchange.name,
                           {"coin": p.coin, "rate": str(p.rate), "paid_usd": str(p.paid_usd),
                            "payment_id": p.id, "observed_at_ns": now_ns}) for p in payments]

    def on_tick(self, now_ns: int) -> list[WorldEvent]:
        out: list[WorldEvent] = []
        for coin, mid in self.exchange.mids().items():
            out.append(
                WorldEvent(
                    WorldEventKind.MARKET_MID,
                    now_ns,
                    self.exchange.name,
                    {"coin": coin, "mid": str(mid)},
                )
            )
        for f in self.exchange.funding():
            out.append(
                WorldEvent(
                    WorldEventKind.FUNDING,
                    now_ns,
                    self.exchange.name,
                    {
                        "coin": f.coin,
                        "rate": str(f.rate),
                        "premium": str(f.premium),
                        "paid_usd": "0",
                    },
                )
            )
        try:
            fills = self.exchange.fills(self.last_fill_ns)
        except RuntimeError:  # no account: read-only venue
            fills = []
        for fl in fills:
            if fl.order_id in self.seen_fills:
                continue
            self.seen_fills.add(fl.order_id)
            self.last_fill_ns = max(self.last_fill_ns, fl.ts_ns)
            out.append(
                WorldEvent(
                    WorldEventKind.FILL,
                    max(now_ns, fl.ts_ns),
                    self.exchange.name,
                    {
                        "order_id": fl.order_id,
                        "coin": fl.coin,
                        "is_buy": fl.is_buy,
                        "size": str(fl.size),
                        "px": str(fl.px),
                        "fee_usd": str(fl.fee),
                        "realized_usd": str(fl.realized),
                        "liquidation": fl.liquidation,
                    },
                )
            )
        out.extend(self.funding_payments(now_ns))
        return out


@dataclass
class Reconciler:
    """Compares the wallet with the real pots every ``every`` ticks."""

    every: int = 10
    _ticks: int = 0

    def due(self) -> bool:
        self._ticks += 1
        return self._ticks % self.every == 0

    @staticmethod
    def snapshot(wallet_balance_micro: int, provider: Any, exchange: Any, *,
                 pots_view: dict | None = None, ledger: Any = None) -> dict[str, Any]:
        from factorylab.world.treasury import provider_pots

        account, positions = None, None
        try:
            account = exchange.account()
            positions = [{"coin": p.coin, "size": str(p.size), "entry_px": str(p.entry_px)}
                         for p in account.positions]
        except Exception:
            pass
        if pots_view is None:
            seed, sellers = provider_pots(provider)
            try:
                equity = str(account.equity_usd)
                venue = int(Decimal(equity) * 1_000_000)
            except Exception:
                venue = None
            pots_view = {"venue": venue, "reserve": 0, "seed": seed, "sellers": sellers,
                         "pending": False}
        else:
            pots_view = dict(pots_view)
        values = [pots_view.get(k) for k in ("venue", "reserve", "seed")]
        values.extend(pots_view["sellers"].values())
        complete = not pots_view.get("pending", False) and all(type(v) is int for v in values)
        pots = sum(values) if complete else None
        discrepancy = wallet_balance_micro - pots if pots is not None else None
        within = abs(discrepancy) <= 500_000 if discrepancy is not None else None
        if ledger is not None and within is False:
            ledger.append({"kind": "reconcile.drift", "wallet_micro": wallet_balance_micro,
                           "pots_micro": pots, "discrepancy_micro": discrepancy,
                           "tolerance_micro": 500_000, "pots": pots_view})
        return {
            "wallet_micro": wallet_balance_micro,
            "positions": positions,
            "openrouter_remaining_micro": pots_view["seed"],
            "venue_equity_usd": str(Decimal(pots_view["venue"]) / 1_000_000)
            if pots_view["venue"] is not None else None,
            "pots": pots_view, "pots_micro": pots, "discrepancy_micro": discrepancy,
            "within_tolerance": within, "tolerance_micro": 500_000,
        }


def build_provider(manifest: Any) -> Any:
    """Return the model provider a manifest asks for.

    ``openrouter`` needs ``OPENROUTER_API_KEY`` in the environment; the error
    says so without echoing anything. ``fake`` returns None so the runtime
    uses its scripted provider.
    """
    providers = {t.provider for t in manifest.models}
    if providers == {"fake"}:
        return None
    from factorylab.world.market import DISCOVERY_URL, MultiProvider, X402Provider

    market = X402Provider(discovery_url=getattr(
        getattr(manifest, "treasury", None), "discovery_url", DISCOVERY_URL,
    ))
    if "x402" in providers:
        if not providers <= {"venice", "openrouter", "x402"}:
            raise RuntimeError(f"unsupported provider set {sorted(providers)}")
        if not os.environ.get("RESERVE_PRIVATE_KEY"):
            raise RuntimeError("x402 needs RESERVE_PRIVATE_KEY")
        from factorylab.world.openrouter import OpenRouterProvider
        from factorylab.world.venice import VeniceProvider

        if "openrouter" in providers and not os.environ.get("OPENROUTER_API_KEY"):
            raise RuntimeError("OPENROUTER_API_KEY is not set")
        if any(not t.id.startswith("x402:") for t in manifest.models if t.provider == "x402"):
            raise RuntimeError("x402 model ids must start with x402:")
        config = {t.id: dict(t.reasoning) for t in manifest.models if t.reasoning}
        return MultiProvider(
            OpenRouterProvider(reasoning_config=config, web_config=manifest.web_config()),
            VeniceProvider(reasoning_config=config, web_config=manifest.web_config()), market,
        )
    if "venice" in providers:
        from factorylab.world.venice import VeniceProvider

        if not (os.environ.get("VENICE_API_KEY") or os.environ.get("RESERVE_PRIVATE_KEY")):
            raise RuntimeError("Venice needs VENICE_API_KEY or RESERVE_PRIVATE_KEY")
        if not providers <= {"venice", "openrouter"}:
            raise RuntimeError(f"unsupported provider set {sorted(providers)}")
        if any(not t.id.startswith("venice:") for t in manifest.models if t.provider == "venice"):
            raise RuntimeError("Venice model ids must start with venice:")
        config = {t.id: dict(t.reasoning) for t in manifest.models if t.reasoning}
        venice = VeniceProvider(reasoning_config=config, web_config=manifest.web_config())
        if providers == {"venice"}:
            return venice
        if not os.environ.get("OPENROUTER_API_KEY"):
            raise RuntimeError("OPENROUTER_API_KEY is not set")
        from factorylab.world.openrouter import OpenRouterProvider

        return MultiProvider(OpenRouterProvider(
            reasoning_config=config, web_config=manifest.web_config(),
        ), venice, market)
    if "openrouter" in providers:
        if not os.environ.get("OPENROUTER_API_KEY"):
            raise RuntimeError("OPENROUTER_API_KEY is not set; the testnet world needs it")
        from factorylab.world.openrouter import OpenRouterProvider
        from factorylab.world.venice import VeniceProvider

        config = {t.id: dict(t.reasoning) for t in manifest.models if t.reasoning}
        return MultiProvider(
            OpenRouterProvider(reasoning_config=config, web_config=manifest.web_config()),
            VeniceProvider(reasoning_config=config, web_config=manifest.web_config()), market,
        )
    raise RuntimeError(f"unsupported provider set {sorted(providers)}")
