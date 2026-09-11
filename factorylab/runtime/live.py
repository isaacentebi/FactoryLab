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

    def events(self) -> Iterator[WorldEvent]:
        last = -1
        for i in range(self.count):
            if i > 0:
                target = last + self.interval_ns
                wait = target - self.now_ns()
                if wait > 0:
                    self.sleep(wait / NS_PER_SECOND)
            ts = max(self.now_ns(), last + 1)
            last = ts
            yield WorldEvent(WorldEventKind.TICK, ts, self.source, {"index": i})


@dataclass
class LiveVenue:
    """Adapts a real ``Exchange`` to per-tick world events.

    Each tick reads mids and funding; when the exchange has an account, new
    fills since the last poll are emitted with their realised P&L so the
    runtime can settle them. Funding payments are reported as rates only:
    the venue account's own funding cash flows are reconciled, not settled,
    in this phase.
    """

    exchange: Any
    last_fill_ns: int = 0
    seen_fills: set[str] = field(default_factory=set)

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
    def snapshot(wallet_balance_micro: int, provider: Any, exchange: Any) -> dict[str, Any]:
        remaining = None
        if hasattr(provider, "balance_micro"):
            try:
                remaining = provider.balance_micro()
            except Exception:
                remaining = None
        equity = None
        try:
            equity = str(exchange.account().equity_usd)
        except Exception:
            equity = None
        pots = 0
        if remaining is not None:
            pots += remaining
        if equity is not None:
            pots += int(Decimal(equity) * 1_000_000)
        return {
            "wallet_micro": wallet_balance_micro,
            "openrouter_remaining_micro": remaining,
            "venue_equity_usd": equity,
            "pots_micro": pots if (remaining is not None or equity is not None) else None,
            "discrepancy_micro": (wallet_balance_micro - pots)
            if (remaining is not None or equity is not None)
            else None,
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
    if "openrouter" in providers:
        if not os.environ.get("OPENROUTER_API_KEY"):
            raise RuntimeError("OPENROUTER_API_KEY is not set; the testnet world needs it")
        from factorylab.world.openrouter import OpenRouterProvider

        reasoning = frozenset(t.id for t in manifest.models if getattr(t, "reasoning", False))
        return OpenRouterProvider(reasoning_models=reasoning)
    raise RuntimeError(f"unsupported provider set {sorted(providers)}")
