"""The live path: wall-clock ticks, a real venue, real models.

Nothing here changes physics. ``LiveClock`` paces the
loop against wall-clock time; ``LiveVenue`` turns venue reads into the same
world events the fake venue produces; ``Reconciler`` periodically compares
the wallet with the two real pots (OpenRouter credits, venue equity) and
records the discrepancy. The wallet stays authoritative.
"""

from __future__ import annotations

import os
import time
from collections import deque
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from factorylab.runtime.reasons import CredentialMissing
from factorylab.world.clock import ClockIterator
from factorylab.world.events import WorldEvent, WorldEventKind

NS_PER_SECOND = 1_000_000_000


#: Tick gaps retained for the measured interval. Long enough to cover a reserve
#: window at the manifest's own cadence, short enough that a slow hour is still
#: visible after the loop recovers.
MEASURED_SAMPLE = 64


@dataclass
class LiveClock:
    """Yields one ``Tick`` per ``interval_ns`` of wall-clock time, ``count`` times.

    The first tick is immediate. ``sleep`` and ``now_ns`` are injectable so
    tests run without waiting. Guarantees strictly increasing timestamps.

    ``interval_ns`` is what the manifest declared; it is not what the loop
    achieves. A tick whose work outlasts the interval simply fires late, so the
    clock also retains the gaps it actually delivered and reports their mean as
    ``measured_interval_ns``. Anything converting events into real time — the
    governance cadence above all — must use the measured interval, or it prices
    the world's slowest loop at a speed the world never ran at. The sample
    belongs to one declared interval: an amendment that changes the tick
    discards it, and the declared interval remains the floor of any conversion
    until the new cadence has been measured.

    ``deadline_ns``, when set, ends the stream at the first tick at or after it,
    so a wall-clock length stays a wall-clock length however long a tick takes.
    """

    interval_ns: int
    count: int
    now_ns: Callable[[], int] = time.time_ns
    sleep: Callable[[float], None] = time.sleep
    source: str = "wallclock"
    index: int = 0
    last_ns: int = -1
    deadline_ns: int | None = None
    gaps: deque[int] = field(default_factory=lambda: deque(maxlen=MEASURED_SAMPLE))

    def set_interval(self, interval_ns: int) -> None:
        """Adopt positive integer nanoseconds and discard gaps delivered at the old interval.

        The retained sample describes one declared cadence. Gaps measured at a
        60 s tick say nothing about a world the charter has just moved to a
        10 minute tick, and keeping them would price the new world at the old
        world's speed.
        """
        if type(interval_ns) is not int or interval_ns <= 0:
            raise ValueError("interval_ns must be positive integer nanoseconds")
        if interval_ns != self.interval_ns:
            self.gaps.clear()
        self.interval_ns = interval_ns

    def measured_interval_ns(self) -> int:
        """Return the mean delivered tick gap of the retained window, never below one ns.

        Falls back to the declared interval until two ticks have been delivered:
        an unmeasured loop is not evidence that the loop is fast.
        """
        if not self.gaps:
            return self.interval_ns
        return max(1, sum(self.gaps) // len(self.gaps))

    def intervals(self) -> dict:
        """Publish both intervals and the sample behind the measured one."""
        return {"declared_ns": self.interval_ns, "measured_ns": self.measured_interval_ns(),
                "samples": len(self.gaps)}

    def events(self) -> ClockIterator:
        """Return a wall-clock stream whose interval remains amendable when injected."""
        return ClockIterator(self, self._events())

    def _events(self) -> Iterator[WorldEvent]:
        while self.index < self.count:
            if self.last_ns >= 0:
                target = self.last_ns + self.interval_ns
                wait = target - self.now_ns()
                if wait > 0:
                    self.sleep(min(wait, self.interval_ns) / NS_PER_SECOND)
            ts = max(self.now_ns(), self.last_ns + 1)
            if self.deadline_ns is not None and ts >= self.deadline_ns:
                return
            if self.last_ns >= 0:
                self.gaps.append(ts - self.last_ns)
            self.last_ns = ts
            i = self.index
            self.index += 1
            yield WorldEvent(WorldEventKind.TICK, ts, self.source, {"index": i})

    def state(self) -> dict:
        """Retain the original budget, next tick index and last delivered timestamp.

        The measured sample remains cadence evidence after resume. The deadline
        is not saved: an absolute deadline from a dead process would end the
        resumed world before its first tick.
        """
        return {"interval_ns": self.interval_ns, "count": self.count, "source": self.source,
                "index": self.index, "last_ns": self.last_ns, "gaps": list(self.gaps)}

    @classmethod
    def restore(cls, state: dict, *, now_ns=time.time_ns, sleep=time.sleep) -> LiveClock:
        """Continue the saved clock with fresh process-local time and sleep functions."""
        state = dict(state)
        gaps = state.pop("gaps", ())
        return cls(**state, gaps=deque(gaps, maxlen=MEASURED_SAMPLE), now_ns=now_ns, sleep=sleep)


@dataclass
class WallClock:
    """The wall clock the safety path reads between model calls (time audit T8).

    Read through the journal, so a replay reads the instant the run read and makes
    the same safety decisions. It is the wall clock the world's ticks are paced
    against (``LiveClock.now_ns``, injectable); a world whose ticks are not paced
    against wall time does not move inside an event, so it reads the event's
    simulated instant.
    """

    tick_clock: Callable[[], Any]
    sim: Any = None
    name: str = "wall"

    def now_ns(self) -> int:
        """Nanoseconds now on the clock the world's ticks are paced against."""
        clock = self.tick_clock()
        if isinstance(clock, LiveClock):
            return int(clock.now_ns())
        return int(self.sim.now_ns)

    def tick_ns(self) -> int:
        """The delivered tick interval now: the slower of the measured and declared gap."""
        from factorylab.runtime.clockwork import tick_ns

        return int(tick_ns(self.tick_clock()))


@dataclass
class LiveVenue:
    """Adapts a real ``Exchange`` to per-tick world events.

    Each tick reads mids and funding; when the exchange has an account, new
    fills since the last poll are emitted with their realised P&L. Rates remain
    observations; separate venue-identified funding payments carry actual cash.

    ``markets`` bounds the per-tick broadcast to the world's own trading
    markets: the manifest seed plus every market the population has registered,
    read fresh on each tick so a registration joins from the next one and a
    resume restores the set with the runtime that replays it. A venue lists far
    more than a world trades — 212 perpetuals and 1,263 USDC spot pairs on
    Hyperliquid testnet — and one delivered event is one routed decision, so an
    unbounded broadcast prices a tick at the size of the venue rather than the
    size of the world. It bounds the broadcast only: public reads of any listed
    coin are unaffected, and money — fills and settled funding payments — is
    never filtered, because a payment on a market the world stopped trading is
    still cash that moved. ``None`` broadcasts everything the venue returns.
    """

    exchange: Any
    last_fill_ns: int = field(default_factory=time.time_ns)
    seen_fills: set[str] = field(default_factory=set)
    ledger: Any = None
    last_funding_ns: int | None = None
    seen_funding: set[str] = field(default_factory=set)
    markets: Callable[[], tuple[str, ...]] | None = None

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
        except (RuntimeError, OSError, ValueError, ArithmeticError):
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

    def _broadcast(self) -> frozenset[str] | None:
        """Return this tick's trading markets, or None when nothing bounds the broadcast."""
        if self.markets is None:
            return None
        return frozenset(self.markets())

    def on_tick(self, now_ns: int, *, include_fills: bool = True) -> list[WorldEvent]:
        traded = self._broadcast()
        out: list[WorldEvent] = []
        try:
            mids = self.exchange.mids()
        except (RuntimeError, OSError, ValueError, ArithmeticError):
            mids = {}
        for coin, mid in mids.items():
            if traded is not None and coin not in traded:
                continue
            out.append(
                WorldEvent(
                    WorldEventKind.MARKET_MID,
                    now_ns,
                    self.exchange.name,
                    {"coin": coin, "mid": str(mid)},
                )
            )
        try:
            funding = self.exchange.funding()
        except (RuntimeError, OSError, ValueError, ArithmeticError):
            funding = []
        for f in funding:
            if traded is not None and f.coin not in traded:
                continue
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
            fills = self.exchange.fills(self.last_fill_ns) if include_fills else []
        except (RuntimeError, OSError, ValueError, ArithmeticError):  # no account: read-only venue
            fills = []
        for fl in fills:
            if fl.ts_ns < self.last_fill_ns or fl.order_id in self.seen_fills:
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
                        "market": getattr(fl, "market", "perp"),
                        "inventory_size": str(getattr(fl, "inventory_size", None) or fl.size),
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


def _extra_body(manifest: Any) -> Any:
    """Per-model extra request bodies, when the manifest type carries them (test stubs may not)."""
    return manifest.extra_body_config() if hasattr(manifest, "extra_body_config") else None


def _schema_models(manifest: Any) -> frozenset[str]:
    """The model ids whose route carries the contract as a schema (test stubs may carry none)."""
    return (manifest.schema_contract_models()
            if hasattr(manifest, "schema_contract_models") else frozenset())


def build_provider(manifest: Any) -> Any:
    """Return the model provider a manifest asks for.

    ``openrouter`` needs ``OPENROUTER_API_KEY`` in the environment; a missing
    credential raises ``CredentialMissing``, which the CLI reports as one code
    and never echoes. ``fake`` returns None so the runtime uses its scripted
    provider.
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
            raise CredentialMissing("x402 needs RESERVE_PRIVATE_KEY")
        from factorylab.world.openrouter import OpenRouterProvider
        from factorylab.world.venice import VeniceProvider

        if "openrouter" in providers and not os.environ.get("OPENROUTER_API_KEY"):
            raise CredentialMissing("OPENROUTER_API_KEY is not set")
        if any(not t.id.startswith("x402:") for t in manifest.models if t.provider == "x402"):
            raise RuntimeError("x402 model ids must start with x402:")
        config = {t.id: dict(t.reasoning) for t in manifest.models if t.reasoning}
        return MultiProvider(
            OpenRouterProvider(reasoning_config=config, web_config=manifest.web_config(),
                               extra_body=_extra_body(manifest),
                               schema_models=_schema_models(manifest)),
            VeniceProvider(reasoning_config=config, web_config=manifest.web_config(),
                           schema_models=_schema_models(manifest)), market,
        )
    if "venice" in providers:
        from factorylab.world.venice import VeniceProvider

        if not (os.environ.get("VENICE_API_KEY") or os.environ.get("RESERVE_PRIVATE_KEY")):
            raise CredentialMissing("Venice needs VENICE_API_KEY or RESERVE_PRIVATE_KEY")
        if not providers <= {"venice", "openrouter"}:
            raise RuntimeError(f"unsupported provider set {sorted(providers)}")
        if any(not t.id.startswith("venice:") for t in manifest.models if t.provider == "venice"):
            raise RuntimeError("Venice model ids must start with venice:")
        config = {t.id: dict(t.reasoning) for t in manifest.models if t.reasoning}
        venice = VeniceProvider(reasoning_config=config, web_config=manifest.web_config(),
                                schema_models=_schema_models(manifest))
        if providers == {"venice"}:
            return venice
        if not os.environ.get("OPENROUTER_API_KEY"):
            raise CredentialMissing("OPENROUTER_API_KEY is not set")
        from factorylab.world.openrouter import OpenRouterProvider

        return MultiProvider(OpenRouterProvider(
            reasoning_config=config, web_config=manifest.web_config(),
            extra_body=_extra_body(manifest), schema_models=_schema_models(manifest),
        ), venice, market)
    if "openrouter" in providers:
        if not os.environ.get("OPENROUTER_API_KEY"):
            raise CredentialMissing("OPENROUTER_API_KEY is not set; the testnet world needs it")
        from factorylab.world.openrouter import OpenRouterProvider
        from factorylab.world.venice import VeniceProvider

        config = {t.id: dict(t.reasoning) for t in manifest.models if t.reasoning}
        return MultiProvider(
            OpenRouterProvider(reasoning_config=config, web_config=manifest.web_config(),
                               extra_body=_extra_body(manifest),
                               schema_models=_schema_models(manifest)),
            VeniceProvider(reasoning_config=config, web_config=manifest.web_config(),
                           schema_models=_schema_models(manifest)), market,
        )
    raise RuntimeError(f"unsupported provider set {sorted(providers)}")
