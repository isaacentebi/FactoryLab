"""Exchange adapters: the protocol, a deterministic fake, and Hyperliquid.

Amounts on this boundary are ``Decimal`` in the venue's own units. Conversion
to wallet micro-USD happens in the runtime using the venue's reported mark, so
the exchange adapter never touches the wallet.
"""

from __future__ import annotations

import hashlib
import os
import random
from collections.abc import Iterator
from dataclasses import dataclass, field, replace
from decimal import ROUND_DOWN, Decimal
from enum import StrEnum
from typing import Any, Protocol
from uuid import uuid4

from factorylab.world.events import WorldEvent, WorldEventKind


class VenueUnavailable(RuntimeError):
    """The venue's API failed transiently after retries. Raised only when no last-good
    value exists to fall back on; otherwise reads return the last good value."""


NS_PER_MS = 1_000_000
NS_PER_HOUR = 3_600 * 1_000_000_000


def _interval_ns(interval: str) -> int:
    return {"1m": 60, "5m": 300, "15m": 900, "1h": 3600}[interval] * 1_000_000_000


def _check_count(n: int, maximum: int) -> None:
    if type(n) is not int or not 1 <= n <= maximum:
        raise ValueError(f"count must be an integer between 1 and {maximum}")


class OrderKind(StrEnum):
    MARKET = "market"
    LIMIT = "limit"


@dataclass(frozen=True)
class Order:
    """An order request. ``limit_px`` is required for limit orders."""

    coin: str
    is_buy: bool
    size: Decimal
    kind: OrderKind = OrderKind.MARKET
    limit_px: Decimal | None = None
    client_id: str | None = None
    reduce_only: bool = False

    def __post_init__(self) -> None:
        if not self.size.is_finite() or self.size <= 0:
            raise ValueError("order size must be finite and positive")
        if self.limit_px is not None and (not self.limit_px.is_finite() or self.limit_px <= 0):
            raise ValueError("limit price must be finite and positive")
        if self.kind is OrderKind.LIMIT and self.limit_px is None:
            raise ValueError("limit orders need limit_px")


@dataclass(frozen=True)
class OrderResult:
    order_id: str | None
    status: str  # "filled" | "resting" | "rejected" | "cancelled" | "uncertain"
    filled_size: Decimal
    avg_px: Decimal | None
    error: str | None = None


@dataclass(frozen=True)
class Fill:
    order_id: str
    coin: str
    is_buy: bool
    size: Decimal
    px: Decimal
    fee: Decimal  # in USD, positive means paid
    ts_ns: int
    realized: Decimal = Decimal(0)  # closed P&L in USD, signed
    liquidation: bool = False


@dataclass(frozen=True)
class FundingEvent:
    coin: str
    rate: Decimal  # per funding interval, signed
    premium: Decimal | None
    ts_ns: int


@dataclass(frozen=True)
class FundingPayment:
    """A venue-identified payment uses positive USD for money paid, negative for received."""

    id: str
    coin: str
    paid_usd: Decimal
    rate: Decimal
    ts_ns: int


@dataclass(frozen=True)
class Position:
    coin: str
    size: Decimal  # signed; negative is short
    entry_px: Decimal


@dataclass(frozen=True)
class AccountState:
    equity_usd: Decimal
    cash_usd: Decimal
    positions: tuple[Position, ...]
    margin_used_usd: Decimal


class Exchange(Protocol):
    """What the runtime needs from any venue. Implementations must be side-effect free
    in ``mids``/``funding``/``account``/``fills`` and must never raise on an
    order the venue rejects; they return ``OrderResult(status="rejected")``."""

    name: str

    def mids(self) -> dict[str, Decimal]: ...
    def funding(self) -> list[FundingEvent]: ...
    def funding_payments(self, since_ns: int) -> list[FundingPayment]: ...
    def account(self) -> AccountState: ...
    def place(self, order: Order) -> OrderResult: ...
    def cancel(self, order_id: str, *, coin: str | None = None,
               client_id: str | None = None) -> dict | None: ...
    def lookup(self, client_id: str, *, order_id: str | None = None) -> OrderResult: ...
    def fills(self, since_ns: int) -> list[Fill]: ...
    def candles(self, coin: str, interval: str, n: int) -> list[dict]: ...
    def order_book(self, coin: str, depth: int) -> dict: ...
    def funding_history(self, coin: str, n: int) -> list[FundingEvent]: ...
    def open_orders(self) -> list[dict]: ...
    def close(self, coin: str, size: Decimal | None = None, *,
              client_id: str | None = None) -> OrderResult: ...
    def set_leverage(self, coin: str, leverage: int) -> dict: ...


# --------------------------------------------------------------------------- fake


@dataclass
class FakeExchange:
    """A deterministic venue for the ``scripted`` world.

    Guarantees: identical ``seed`` and ``price_path`` produce identical mids,
    fills, funding and account states in the same call order; market orders
    fill immediately at mid plus or minus half the spread; limit orders fill
    only if the current mid crosses the limit; fees are ``fee_bps`` of notional;
    funding accrues once per ``funding_interval_ns`` on open positions at the
    scripted rate. Equity is cash plus unrealized P&L marked at mid.
    """

    name: str = "fake"
    seed: int = 0
    start_cash_usd: Decimal = Decimal("100")
    coins: tuple[str, ...] = ("BTC", "ETH")
    price_path: dict[str, list[Decimal]] | None = None
    start_prices: dict[str, Decimal] = field(
        default_factory=lambda: {"BTC": Decimal("60000"), "ETH": Decimal("2500")}
    )
    spread_bps: Decimal = Decimal("2")
    fee_bps: Decimal = Decimal("3.5")
    funding_rate: Decimal = Decimal("0.0001")
    funding_interval_ns: int = NS_PER_HOUR
    step_bps: Decimal = Decimal("10")  # random-walk step when no price_path
    max_leverage: Decimal = Decimal("3")
    shocks: dict[int, dict[str, Decimal]] = field(default_factory=dict)  # step -> coin -> mult
    maintenance_fraction: Decimal = Decimal("0.5")  # of initial margin; below it, liquidate

    def __post_init__(self) -> None:
        self._rng = random.Random(self.seed)
        self._now_ns = 0
        self._step = 0
        self._mids: dict[str, Decimal] = dict(self.start_prices)
        for c in self.coins:
            self._mids.setdefault(c, Decimal("100"))
        self._cash = Decimal(self.start_cash_usd)
        self._positions: dict[str, Position] = {}
        self._fills: list[Fill] = []
        self._resting: dict[str, Order] = {}
        self._client_results: dict[str, OrderResult] = {}
        self._cancel_results: dict[str, dict] = {}
        self._cancelled: set[str] = set()
        self._next_oid = 1
        self._last_funding_ns = 0
        self._pending_events: list[WorldEvent] = []
        self._mid_history: dict[str, list[tuple[int, Decimal]]] = {coin: [] for coin in self._mids}
        self._funding_history: list[FundingEvent] = []
        self._funding_payments: list[FundingPayment] = []
        self._leverage: dict[str, Decimal] = {}

    # ---- time and prices

    def advance(self, ts_ns: int) -> list[WorldEvent]:
        """Move venue time forward to ``ts_ns``.

        Returns the events produced in order: a ``MarketMid`` per coin, any
        ``Fill`` from resting limit orders crossed by the new mids, and a
        ``Funding`` per coin if a funding boundary was crossed. Guarantees
        time never moves backwards.
        """
        if ts_ns < self._now_ns:
            raise ValueError("FakeExchange time cannot move backwards")
        self._now_ns = ts_ns
        self._step += 1
        events: list[WorldEvent] = []
        for coin in self.coins:
            self._mids[coin] = self._next_price(coin)
            self._mid_history[coin].append((ts_ns, self._mids[coin]))
            events.append(
                WorldEvent(
                    WorldEventKind.MARKET_MID,
                    ts_ns,
                    self.name,
                    {"coin": coin, "mid": str(self._mids[coin])},
                )
            )
        events.extend(self._cross_resting())
        events.extend(self._liquidate_if_needed())
        if ts_ns - self._last_funding_ns >= self.funding_interval_ns:
            self._last_funding_ns = ts_ns - (ts_ns % self.funding_interval_ns)
            events.extend(self._apply_funding())
        return events

    def _next_price(self, coin: str) -> Decimal:
        if self.price_path and coin in self.price_path:
            path = self.price_path[coin]
            return path[min(self._step - 1, len(path) - 1)]
        drift = Decimal(self._rng.uniform(-1, 1)) * self.step_bps / Decimal(10_000)
        px = self._mids[coin] * (Decimal(1) + drift)
        shock = self.shocks.get(self._step, {}).get(coin)
        if shock is not None:
            px = px * shock
        return px.quantize(Decimal("0.01"), rounding=ROUND_DOWN)

    def sync_cash(self, cash_usd: Decimal) -> None:
        """Set venue cash to the authoritative wallet figure.

        The runtime calls this after settling fills and funding into the wallet
        so margin checks see the wallet's truth, including compute spend the
        venue never observes.
        """
        self._cash = Decimal(cash_usd)

    # ---- protocol

    def mids(self) -> dict[str, Decimal]:
        return dict(self._mids)

    def funding(self) -> list[FundingEvent]:
        return [FundingEvent(c, self.funding_rate, None, self._now_ns) for c in self.coins]

    def funding_payments(self, since_ns: int) -> list[FundingPayment]:
        """Return actual simulated cash flows at or after an inclusive nanosecond cursor."""
        return [p for p in self._funding_payments if p.ts_ns >= since_ns]

    def account(self) -> AccountState:
        unrealized = Decimal(0)
        margin = Decimal(0)
        for p in self._positions.values():
            mid = self._mids[p.coin]
            unrealized += (mid - p.entry_px) * p.size
            margin += abs(p.size) * mid / self._leverage.get(p.coin, self.max_leverage)
        return AccountState(
            equity_usd=self._cash + unrealized,
            cash_usd=self._cash,
            positions=tuple(self._positions.values()),
            margin_used_usd=margin,
        )

    def place(self, order: Order) -> OrderResult:
        """A stable client id admits at most one order, including after a lost acknowledgement."""
        client_id = order.client_id or f"fake-client-{self._next_oid}"
        if client_id in self._client_results:
            return self._client_results[client_id]
        result = self._place(order)
        self._client_results[client_id] = result
        return result

    def _place(self, order: Order) -> OrderResult:
        if order.coin not in self._mids:
            return OrderResult(None, "rejected", Decimal(0), None, "unknown coin")
        oid = str(self._next_oid)
        self._next_oid += 1
        if order.kind is OrderKind.LIMIT:
            assert order.limit_px is not None
            if self._crosses(order, self._mids[order.coin]):
                return self._fill(oid, order, order.limit_px)
            self._resting[oid] = order
            return OrderResult(oid, "resting", Decimal(0), None)
        mid = self._mids[order.coin]
        half = mid * self.spread_bps / Decimal(20_000)
        px = mid + half if order.is_buy else mid - half
        return self._fill(oid, order, px)

    def cancel(self, order_id: str, *, coin: str | None = None,
               client_id: str | None = None) -> dict:
        """Coin-scoped, client-addressed cancellations are idempotent."""
        if client_id is not None and client_id in self._cancel_results:
            return dict(self._cancel_results[client_id])
        order = self._resting.get(order_id)
        if order is None or coin is not None and order.coin != coin:
            result = {"status": "rejected", "error": "unknown order for coin"}
        else:
            del self._resting[order_id]
            self._cancelled.add(order_id)
            result = {"status": "cancelled", "order_id": order_id}
        if client_id is not None:
            self._cancel_results[client_id] = result
        return dict(result)

    def lookup(self, client_id: str, *, order_id: str | None = None) -> OrderResult:
        """Resolve original order identity without placing or cancelling anything."""
        result = self._client_results.get(client_id)
        oid = order_id or (result.order_id if result else None)
        if oid in self._cancelled:
            return OrderResult(oid, "cancelled", Decimal(0), None)
        fills = [f for f in self._fills if f.order_id == oid]
        if fills and oid not in self._resting:
            size = sum((f.size for f in fills), Decimal(0))
            return OrderResult(oid, "filled", size,
                               sum((f.size * f.px for f in fills), Decimal(0)) / size)
        return result or OrderResult(oid, "uncertain", Decimal(0), None, "order not observed")

    def fills(self, since_ns: int) -> list[Fill]:
        return [f for f in self._fills if f.ts_ns >= since_ns]

    def drain_events(self) -> list[WorldEvent]:
        """Return and clear events produced by ``place`` (fills, rejections)."""
        out, self._pending_events = self._pending_events, []
        return out

    def candles(self, coin: str, interval: str, n: int) -> list[dict]:
        """Return up to n observed OHLC buckets, oldest first, with zero synthetic volume.

        Timestamps are bucket starts in nanoseconds. Empty buckets are omitted;
        the latest bucket may be incomplete. Only advance() supplies observations.
        """
        _check_count(n, 200)
        width = _interval_ns(interval)
        buckets: dict[int, dict] = {}
        for ts_ns, mid in self._mid_history[coin]:
            start = ts_ns - ts_ns % width
            if start not in buckets:
                buckets[start] = {
                    "ts_ns": start,
                    "open": mid,
                    "high": mid,
                    "low": mid,
                    "close": mid,
                    "volume": Decimal(0),
                }
            else:
                candle = buckets[start]
                candle["high"] = max(candle["high"], mid)
                candle["low"] = min(candle["low"], mid)
                candle["close"] = mid
        return list(buckets.values())[-n:]

    def order_book(self, coin: str, depth: int) -> dict:
        """Return deterministic best-first levels, with unit size halved at each level.

        Level k is mid plus/minus k times the configured full spread. A zero
        spread produces coincident prices, consistent with a frictionless fake.
        """
        _check_count(depth, 20)
        mid = self._mids[coin]
        spread = mid * self.spread_bps / Decimal(10_000)
        return {
            "coin": coin,
            "ts_ns": self._now_ns,
            "bids": [
                {"price": mid - k * spread, "size": Decimal(1) / (2 ** (k - 1))}
                for k in range(1, depth + 1)
            ],
            "asks": [
                {"price": mid + k * spread, "size": Decimal(1) / (2 ** (k - 1))}
                for k in range(1, depth + 1)
            ],
        }

    def funding_history(self, coin: str, n: int) -> list[FundingEvent]:
        """Return only applied funding observations, oldest first, capped at n."""
        _check_count(n, 100)
        if coin not in self._mids:
            raise ValueError("unknown coin")
        return [event for event in self._funding_history if event.coin == coin][-n:]

    def open_orders(self) -> list[dict]:
        """Return detached snapshots of currently resting orders."""
        return [
            {
                "order_id": oid,
                "coin": order.coin,
                "side": "buy" if order.is_buy else "sell",
                "size": order.size,
                "price": order.limit_px,
            }
            for oid, order in self._resting.items()
        ]

    def close(
        self, coin: str, size: Decimal | None = None, *, client_id: str | None = None,
    ) -> OrderResult:
        """Close at most the current position; flat or invalid requests are rejected."""
        if client_id is not None and client_id in self._client_results:
            return self._client_results[client_id]
        pos = self._positions.get(coin)
        if pos is None:
            return OrderResult(None, "rejected", Decimal(0), None, "no open position")
        if size is not None and (not size.is_finite() or size <= 0):
            return OrderResult(None, "rejected", Decimal(0), None, "size must be positive")
        amount = abs(pos.size) if size is None else min(size, abs(pos.size))
        return self.place(Order(coin, pos.size < 0, amount, client_id=client_id, reduce_only=True))

    def set_leverage(self, coin: str, leverage: int) -> dict:
        """Accept integer leverage within the venue cap without changing other coins."""
        if coin not in self._mids:
            return {"status": "rejected", "error": "unknown coin"}
        if type(leverage) is not int or not 1 <= leverage <= self.max_leverage:
            return {"status": "rejected", "error": f"leverage must be 1..{self.max_leverage}"}
        self._leverage[coin] = Decimal(leverage)
        return {"status": "ok", "coin": coin, "leverage": leverage}

    # ---- internals

    def _crosses(self, order: Order, mid: Decimal) -> bool:
        assert order.limit_px is not None
        return mid <= order.limit_px if order.is_buy else mid >= order.limit_px

    def _cross_resting(self) -> list[WorldEvent]:
        events: list[WorldEvent] = []
        for oid, order in list(self._resting.items()):
            if self._crosses(order, self._mids[order.coin]):
                del self._resting[oid]
                assert order.limit_px is not None
                self._fill(oid, order, order.limit_px)
                events.extend(self.drain_events())
        return events

    def _fill(
        self, oid: str, order: Order, px: Decimal, *, liquidation: bool = False
    ) -> OrderResult:
        if order.reduce_only:
            pos = self._positions.get(order.coin)
            if pos is None or (pos.size > 0) == order.is_buy:
                self._pending_events.append(
                    WorldEvent(
                        WorldEventKind.ORDER_REJECTED,
                        self._now_ns,
                        self.name,
                        {"order_id": oid, "coin": order.coin, "reason": "not reducing position"},
                    )
                )
                return OrderResult(oid, "rejected", Decimal(0), None, "not reducing position")
            order = replace(order, size=min(order.size, abs(pos.size)))
        notional = order.size * px
        fee = (notional * self.fee_bps / Decimal(10_000)).quantize(Decimal("0.000001"))
        signed = order.size if order.is_buy else -order.size
        pos = self._positions.get(order.coin)
        new_size = (pos.size if pos else Decimal(0)) + signed
        # margin check on the resulting position (never blocks a liquidation close)
        if not liquidation and (pos is None or abs(new_size) > abs(pos.size)):
            if not self._margin_ok(order.coin, new_size, px):
                self._pending_events.append(
                    WorldEvent(
                        WorldEventKind.ORDER_REJECTED,
                        self._now_ns,
                        self.name,
                        {"order_id": oid, "coin": order.coin, "reason": "insufficient margin"},
                    )
                )
                return OrderResult(oid, "rejected", Decimal(0), None, "insufficient margin")
        realized = Decimal(0)
        if pos is not None and (pos.size > 0) != (signed > 0):
            closing = min(abs(pos.size), abs(signed))
            realized = (px - pos.entry_px) * closing * (1 if pos.size > 0 else -1)
        self._cash += realized - fee
        if new_size == 0:
            self._positions.pop(order.coin, None)
        elif pos is None:
            self._positions[order.coin] = Position(order.coin, new_size, px)
        elif (pos.size > 0) != (new_size > 0):
            # flipped through zero: the residual is a fresh position at the fill price
            self._positions[order.coin] = Position(order.coin, new_size, px)
        elif abs(new_size) > abs(pos.size):
            # adding to the same side: size-weighted entry
            prev_abs = abs(pos.size)
            entry = (pos.entry_px * prev_abs + px * abs(signed)) / (prev_abs + abs(signed))
            self._positions[order.coin] = Position(order.coin, new_size, entry)
        else:
            # partial reduce keeps the entry price
            self._positions[order.coin] = Position(order.coin, new_size, pos.entry_px)
        fill = Fill(oid, order.coin, order.is_buy, order.size, px, fee, self._now_ns)
        self._fills.append(fill)
        self._pending_events.append(
            WorldEvent(
                WorldEventKind.FILL,
                self._now_ns,
                self.name,
                {
                    "order_id": oid,
                    "coin": order.coin,
                    "is_buy": order.is_buy,
                    "size": str(order.size),
                    "px": str(px),
                    "fee_usd": str(fee),
                    "realized_usd": str(realized),
                    "liquidation": liquidation,
                },
            )
        )
        return OrderResult(oid, "filled", order.size, px)

    def _margin_ok(self, coin: str, new_size: Decimal, px: Decimal) -> bool:
        margin = abs(new_size) * px / self._leverage.get(coin, self.max_leverage)
        for p in self._positions.values():
            if p.coin != coin:
                margin += (
                    abs(p.size) * self._mids[p.coin] / self._leverage.get(p.coin, self.max_leverage)
                )
        return margin <= self.account().equity_usd

    def _liquidate_if_needed(self) -> list[WorldEvent]:
        """Force-close every position at mid when equity falls below maintenance margin.

        Maintenance margin is ``maintenance_fraction`` of initial margin
        (notional / each coin's leverage). The realised loss lands in cash like any
        other fill, so a leveraged position can take the account negative.
        """
        if not self._positions:
            return []
        acct = self.account()
        maintenance = acct.margin_used_usd * self.maintenance_fraction
        if acct.equity_usd >= maintenance:
            return []
        events: list[WorldEvent] = []
        for pos in list(self._positions.values()):
            oid = str(self._next_oid)
            self._next_oid += 1
            close = Order(pos.coin, pos.size < 0, abs(pos.size))
            self._fill(oid, close, self._mids[pos.coin], liquidation=True)
            events.extend(self.drain_events())
        return events

    def _apply_funding(self) -> list[WorldEvent]:
        events: list[WorldEvent] = []
        for coin in self.coins:
            self._funding_history.append(FundingEvent(coin, self.funding_rate, None, self._now_ns))
            pos = self._positions.get(coin)
            paid = Decimal(0)
            if pos is not None:
                # longs pay when rate is positive
                paid = pos.size * self._mids[coin] * self.funding_rate
                self._cash -= paid
            self._funding_payments.append(FundingPayment(
                f"{self._now_ns}:{coin}", coin, paid, self.funding_rate, self._now_ns,
            ))
            events.append(
                WorldEvent(
                    WorldEventKind.FUNDING,
                    self._now_ns,
                    self.name,
                    {"coin": coin, "rate": str(self.funding_rate), "paid_usd": str(paid)},
                )
            )
        return events


# --------------------------------------------------------------------- hyperliquid


class HyperliquidExchange:
    """Hyperliquid perpetuals via the official SDK.

    Testnet by default. Mainnet is selected only when ``mainnet=True`` is passed
    explicitly by a world manifest. Read calls need only an address; writes
    need a private key, taken from ``key_env`` (default ``HL_PRIVATE_KEY``) and
    never from code. Guarantees that ``place`` returns a rejected result rather
    than raising on venue-side rejection.
    """

    def __init__(
        self,
        *,
        mainnet: bool = False,
        address: str | None = None,
        key_env: str = "HL_PRIVATE_KEY",
        coins: tuple[str, ...] = ("BTC", "ETH"),
        timeout: float = 20.0,
    ) -> None:
        from hyperliquid.info import Info
        from hyperliquid.utils import constants

        self.name = "hyperliquid-mainnet" if mainnet else "hyperliquid-testnet"
        self.base_url = constants.MAINNET_API_URL if mainnet else constants.TESTNET_API_URL
        self.coins = coins
        self._info = Info(self.base_url, skip_ws=True, timeout=timeout)
        self._address = address
        self._exchange: Any | None = None
        key = os.environ.get(key_env)
        if key:
            from eth_account import Account
            from hyperliquid.exchange import Exchange as HLExchange

            wallet = Account.from_key(key)
            self._address = self._address or wallet.address
            self._exchange = HLExchange(wallet, self.base_url, account_address=self._address)
        meta = self._info.meta()
        self._sz_decimals = {a["name"]: int(a["szDecimals"]) for a in meta["universe"]}
        self.transient_failures = 0
        self._last_mids: dict[str, Decimal] | None = None
        self._last_account: AccountState | None = None

    def _guarded(self, what: str, call: Any, attempts: int = 3) -> Any:
        """Call the API with retries on transient failures; raise VenueUnavailable after.

        Timeouts, connection errors and 5xx answers are the venue's weather, not
        the world's death. Three attempts with 0.5 s, 1 s, 2 s pauses; every
        exhausted call counts in ``transient_failures`` so the runtime can report it.
        """
        import time

        import requests
        from hyperliquid.utils.error import ServerError

        delay = 0.5
        for attempt in range(attempts):
            try:
                return call()
            except (requests.RequestException, OSError, TimeoutError, ServerError) as exc:
                if attempt == attempts - 1:
                    self.transient_failures += 1
                    raise VenueUnavailable(f"{what}: {type(exc).__name__}: {exc}") from exc
                time.sleep(delay)
                delay *= 2
        raise AssertionError("unreachable")

    # ---- reads

    def mids(self) -> dict[str, Decimal]:
        try:
            raw = self._guarded("all_mids", self._info.all_mids)
        except VenueUnavailable:
            if self._last_mids is None:
                raise
            return dict(self._last_mids)
        self._last_mids = {c: Decimal(str(raw[c])) for c in self.coins if c in raw}
        return dict(self._last_mids)

    def funding(self) -> list[FundingEvent]:
        import time

        try:
            raw = self._guarded("meta_and_asset_ctxs", self._info.meta_and_asset_ctxs)
        except VenueUnavailable:
            return []
        if not isinstance(raw, (list, tuple)) or len(raw) != 2:
            raise VenueUnavailable("invalid funding response")
        meta, ctxs = raw
        if (not isinstance(meta, dict) or not isinstance(meta.get("universe"), list)
                or not isinstance(ctxs, list)):
            raise VenueUnavailable("invalid funding response")
        now_ns = time.time_ns()
        out: list[FundingEvent] = []
        for asset, ctx in zip(meta["universe"], ctxs, strict=False):
            if not isinstance(asset, dict) or asset.get("name") not in self.coins:
                continue
            name = asset["name"]
            try:
                rate = Decimal(str(ctx["funding"]))
                premium = Decimal(str(ctx["premium"])) if ctx.get("premium") is not None else None
                if not rate.is_finite() or premium is not None and not premium.is_finite():
                    continue
                out.append(FundingEvent(name, rate, premium, now_ns))
            except (KeyError, TypeError, ValueError, ArithmeticError, AttributeError):
                continue
        return out

    def account(self) -> AccountState:
        if not self._address:
            raise RuntimeError("account() needs an address or a private key")
        try:
            st = self._guarded("user_state", lambda: self._info.user_state(self._address))
        except VenueUnavailable:
            if self._last_account is None:
                raise
            return self._last_account
        summary = st["marginSummary"]
        positions: list[Position] = []
        for ap in st.get("assetPositions", []):
            p = ap["position"]
            size = Decimal(str(p["szi"]))
            if size == 0:
                continue
            entry = Decimal(str(p["entryPx"])) if p.get("entryPx") else Decimal(0)
            positions.append(Position(p["coin"], size, entry))
        self._last_account = AccountState(
            equity_usd=Decimal(str(summary["accountValue"])),
            cash_usd=Decimal(str(st.get("withdrawable", summary["accountValue"]))),
            positions=tuple(positions),
            margin_used_usd=Decimal(str(summary["totalMarginUsed"])),
        )
        return self._last_account

    def funding_payments(self, since_ns: int) -> list[FundingPayment]:
        """Read inclusive, paginated user cash flows; never infer payments from funding rates.

        Hyperliquid's delta.usdc is a credit to the user, so paid_usd negates it.
        The boundary millisecond is reread and deduplicated by hash plus coin.
        A stalled full page fails closed instead of silently skipping its tail.
        """
        if type(since_ns) is not int or since_ns < 0:
            raise ValueError("since_ns must be nonnegative integer nanoseconds")
        if not self._address:
            raise RuntimeError("funding_payments() needs an address or a private key")
        start = since_ns // NS_PER_MS
        payments: dict[str, FundingPayment] = {}
        while True:
            page = self._guarded("user_funding", lambda start=start:
                                 self._info.user_funding_history(self._address, start))
            if not isinstance(page, list):
                raise ValueError("invalid user funding response")
            timestamps = []
            for row in page:
                try:
                    stamp = int(row["time"])
                    if stamp < 0:
                        continue
                    timestamps.append(stamp)
                    delta = row["delta"]
                    if delta.get("type") != "funding":
                        continue
                    ts_ns = stamp * NS_PER_MS
                    if ts_ns < since_ns:
                        continue
                    coin = delta["coin"]
                    if not isinstance(coin, str) or not coin or not row["hash"]:
                        continue
                    ident = f"{row['hash']}:{coin}"
                    paid = -Decimal(str(delta["usdc"]))
                    rate = Decimal(str(delta["fundingRate"]))
                    if not paid.is_finite() or not rate.is_finite():
                        continue
                    payments[ident] = FundingPayment(ident, coin, paid, rate, ts_ns)
                except (KeyError, TypeError, ValueError, ArithmeticError, AttributeError):
                    continue
            if len(page) < 500:
                break
            latest = max(timestamps, default=start)
            if latest <= start:
                raise ValueError("funding pagination stalled at a full timestamp")
            start = latest
        return sorted(payments.values(), key=lambda p: (p.ts_ns, p.id))

    def fills(self, since_ns: int) -> list[Fill]:
        """Fills since ``since_ns``; an unavailable venue yields none, and the caller's
        last-seen timestamp makes the next poll pick them up."""
        if not self._address:
            raise RuntimeError("fills() needs an address or a private key")
        try:
            raw = self._guarded(
                "user_fills_by_time",
                lambda: self._info.user_fills_by_time(self._address, since_ns // NS_PER_MS),
            )
        except VenueUnavailable:
            return []
        if not isinstance(raw, list):
            raise VenueUnavailable("invalid fill response")
        out: list[Fill] = []
        for f in raw:
            try:
                values = [Decimal(str(f.get(key, "0")))
                          for key in ("sz", "px", "fee", "closedPnl")]
                size, px, fee, realized = values
                stamp = int(f["time"]) * NS_PER_MS
                if (any(not value.is_finite() for value in values)
                        or size <= 0 or px <= 0 or stamp < 0
                        or f["side"] not in ("B", "A")
                        or not isinstance(f["coin"], str) or not f["coin"]):
                    continue
                out.append(Fill(
                    order_id=str(f["oid"]), coin=f["coin"], is_buy=f["side"] == "B",
                    size=size, px=px, fee=fee, ts_ns=stamp, realized=realized,
                    liquidation=bool(f.get("liquidation")),
                ))
            except (KeyError, TypeError, ValueError, ArithmeticError, AttributeError):
                continue
        return out

    def candles(self, coin: str, interval: str, n: int) -> list[dict]:
        """Return up to n recent OHLCV buckets in increasing nanosecond timestamp order."""
        import time

        _check_count(n, 200)
        width_ms = _interval_ns(interval) // NS_PER_MS
        end_ms = time.time_ns() // NS_PER_MS
        start_ms = end_ms - end_ms % width_ms - (n - 1) * width_ms
        raw = self._info.candles_snapshot(coin, interval, start_ms, end_ms)
        return [
            {
                "ts_ns": int(c["t"]) * NS_PER_MS,
                "open": Decimal(str(c["o"])),
                "high": Decimal(str(c["h"])),
                "low": Decimal(str(c["l"])),
                "close": Decimal(str(c["c"])),
                "volume": Decimal(str(c["v"])),
            }
            for c in sorted(raw, key=lambda c: int(c["t"]))[-n:]
        ]

    def order_book(self, coin: str, depth: int) -> dict:
        """Return at most depth levels per side, bids descending and asks ascending."""
        _check_count(depth, 20)
        raw = self._info.l2_snapshot(coin)
        sides = [
            sorted(
                [
                    {"price": Decimal(str(level["px"])), "size": Decimal(str(level["sz"]))}
                    for level in levels
                ],
                key=lambda level: level["price"],
                reverse=index == 0,
            )[:depth]
            for index, levels in enumerate(raw["levels"])
        ]
        return {
            "coin": coin,
            "ts_ns": int(raw["time"]) * NS_PER_MS,
            "bids": sides[0],
            "asks": sides[1],
        }

    def funding_history(self, coin: str, n: int) -> list[FundingEvent]:
        """Return up to n recent hourly funding observations, oldest first."""
        import time

        _check_count(n, 100)
        end_ms = time.time_ns() // NS_PER_MS
        raw = self._info.funding_history(coin, end_ms - n * NS_PER_HOUR // NS_PER_MS, end_ms)
        return [
            FundingEvent(
                coin,
                Decimal(str(f["fundingRate"])),
                Decimal(str(f["premium"])) if f.get("premium") is not None else None,
                int(f["time"]) * NS_PER_MS,
            )
            for f in sorted(raw, key=lambda f: int(f["time"]))[-n:]
        ]

    def open_orders(self) -> list[dict]:
        """Return normalized resting orders for the configured address."""
        if not self._address:
            raise RuntimeError("open_orders() needs an address or a private key")
        return [
            {
                "order_id": str(o["oid"]),
                "coin": o["coin"],
                "side": "buy" if o["side"] == "B" else "sell",
                "size": Decimal(str(o["sz"])),
                "price": Decimal(str(o["limitPx"])),
            }
            for o in self._info.open_orders(self._address)
        ]

    # ---- writes

    @staticmethod
    def client_id(handle: str):
        """Map a persistent decision identity to Hyperliquid's required 128-bit cloid."""
        from hyperliquid.utils.types import Cloid

        return Cloid.from_str("0x" + hashlib.sha256(handle.encode()).hexdigest()[:32])

    def lookup(self, client_id: str, *, order_id: str | None = None) -> OrderResult:
        """Unknown or unavailable order status is uncertainty, never a negative acknowledgement."""
        try:
            response = (self._info.query_order_by_oid(self._address, int(order_id))
                        if order_id is not None else
                        self._info.query_order_by_cloid(self._address, self.client_id(client_id)))
            if response.get("status") != "order":
                return OrderResult(order_id, "uncertain", Decimal(0), None, "order not observed")
            detail = response["order"]
            order = detail["order"]
            status, oid = detail["status"], self._order_id(order["oid"])
            size = Decimal(str(order["origSz"]))
            remaining = Decimal(str(order["sz"]))
            if not size.is_finite() or not remaining.is_finite() or not 0 <= remaining <= size:
                raise ValueError("invalid order quantity")
            if status == "open":
                return OrderResult(oid, "resting", size - remaining, None)
            if status == "filled":
                # Order status does not provide an execution price; fills supply accounting.
                return OrderResult(oid, "filled", size, None)
            if status == "canceled" or status.endswith("Canceled"):
                return OrderResult(oid, "cancelled", size - remaining, None)
            if status == "rejected" or status.endswith("Rejected"):
                return OrderResult(oid, "rejected", Decimal(0), None, "venue rejected order")
        except Exception:
            pass
        return OrderResult(order_id, "uncertain", Decimal(0), None, "order status unavailable")

    def _submit(self, client_id: str, submit) -> OrderResult:
        """Submit once per identity; a lost or malformed acknowledgement requires lookup."""
        results = self.__dict__.setdefault("_client_results", {})
        if client_id in results:
            previous = results[client_id]
            if previous.status != "uncertain":
                return previous
            result = self.lookup(client_id)
        else:
            results[client_id] = OrderResult(None, "uncertain", Decimal(0), None)
            try:
                result = self._parse_order_response(submit())
            except Exception:
                result = results[client_id]
            if result.status == "uncertain":
                result = self.lookup(client_id)
        results[client_id] = result
        return result

    def place(self, order: Order) -> OrderResult:
        if self._exchange is None:
            return OrderResult(None, "rejected", Decimal(0), None, "no signing key")
        client_id = order.client_id or str(uuid4())
        if order.reduce_only and order.kind is OrderKind.MARKET:
            try:
                pos = next((p for p in self.account().positions if p.coin == order.coin), None)
            except Exception:
                return OrderResult(None, "rejected", Decimal(0), None, "position unavailable")
            if pos is None or (pos.size > 0) == order.is_buy:
                return OrderResult(None, "rejected", Decimal(0), None, "not reducing position")
            return self.close(order.coin, min(order.size, abs(pos.size)), client_id=client_id)
        rounded = self._round_size(order.coin, order.size)
        if not rounded.is_finite() or rounded <= 0:
            return OrderResult(None, "rejected", Decimal(0), None, "size below venue precision")
        cloid = self.client_id(client_id)
        sz = float(rounded)  # SDK wire format only; accounting remains exact Decimal.
        if order.kind is OrderKind.MARKET:
            return self._submit(client_id, lambda: self._exchange.market_open(
                order.coin, order.is_buy, sz, cloid=cloid))
        return self._submit(client_id, lambda: self._exchange.order(
            order.coin, order.is_buy, sz, float(order.limit_px), {"limit": {"tif": "Gtc"}},
            reduce_only=order.reduce_only, cloid=cloid))

    def cancel(self, order_id: str, *, coin: str | None = None,
               client_id: str | None = None) -> dict:
        """A cancel's stable decision identity retains its target oid through reconciliation.

        Hyperliquid cancellations have no independent client-id wire field: the
        target order's immutable oid is the venue's idempotency and lookup identity.
        """
        if self._exchange is None:
            return {"status": "rejected", "error": "no signing key"}
        client_id = client_id or str(uuid4())
        results = self.__dict__.setdefault("_cancel_results", {})
        if client_id in results and results[client_id]["status"] != "uncertain":
            return dict(results[client_id])
        if client_id not in results:
            results[client_id] = {"status": "uncertain", "order_id": order_id}
            for name in (coin,) if coin is not None else self.coins:
                try:
                    response = self._exchange.cancel(name, int(order_id))
                    if response.get("status") == "err":
                        results[client_id] = {"status": "rejected", "error": "venue rejection"}
                        return dict(results[client_id])
                    statuses = response["response"]["data"]["statuses"]
                    if response.get("status") == "ok" and statuses == ["success"]:
                        results[client_id] = {"status": "cancelled", "order_id": order_id}
                        return dict(results[client_id])
                    if response.get("status") == "err" or any(
                        isinstance(s, dict) and "error" in s for s in statuses
                    ):
                        results[client_id] = {"status": "rejected", "error": "venue rejection"}
                        return dict(results[client_id])
                except Exception:
                    break  # Never try another market after an ambiguous submission.
        result = self.lookup(client_id, order_id=order_id)
        if result.status == "cancelled":
            results[client_id] = {"status": "cancelled", "order_id": order_id}
        elif result.status in ("filled", "rejected"):
            results[client_id] = {"status": "rejected", "order_id": order_id,
                                  "error": "order already terminal"}
        return dict(results[client_id])

    def close(self, coin: str, size: Decimal | None = None, *,
              client_id: str | None = None) -> OrderResult:
        """A reduce-only close is submitted once with its originating decision's cloid."""
        if self._exchange is None:
            return OrderResult(None, "rejected", Decimal(0), None, "no signing key")
        rounded = None if size is None else self._round_size(coin, size)
        if rounded is not None and (not rounded.is_finite() or rounded <= 0):
            return OrderResult(None, "rejected", Decimal(0), None, "size below venue precision")
        client_id = client_id or str(uuid4())
        return self._submit(client_id, lambda: self._exchange.market_close(
            coin, sz=None if rounded is None else float(rounded), cloid=self.client_id(client_id)))

    def set_leverage(self, coin: str, leverage: int) -> dict:
        """Return venue acknowledgement or a rejected result without propagating failures."""
        if self._exchange is None:
            return {"status": "rejected", "error": "no signing key"}
        if type(leverage) is not int or leverage < 1:
            return {"status": "rejected", "error": "leverage must be a positive integer"}
        try:
            resp = self._exchange.update_leverage(leverage, coin, is_cross=True)
            if resp.get("status") == "ok":
                return {"status": "ok", "coin": coin, "leverage": leverage}
            return {"status": "rejected", "error": str(resp)}
        except Exception as exc:
            return {"status": "rejected", "error": f"{type(exc).__name__}: {exc}"}

    # ---- helpers

    @staticmethod
    def _order_id(value: Any) -> str:
        """Only a nonzero integer venue identity can acknowledge an order."""
        if (type(value) not in (int, str) or not str(value).isascii()
                or not str(value).isdecimal() or int(value) <= 0):
            raise ValueError("invalid venue order identity")
        return str(value)

    def _round_size(self, coin: str, size: Decimal) -> Decimal:
        d = self._sz_decimals.get(coin, 4)
        return size.quantize(Decimal(1).scaleb(-d), rounding=ROUND_DOWN)

    @staticmethod
    def _parse_order_response(resp: Any) -> OrderResult:
        try:
            if resp.get("status") == "err":
                return OrderResult(None, "rejected", Decimal(0), None, "venue rejection")
            if resp.get("status") != "ok":
                return OrderResult(None, "uncertain", Decimal(0), None, "unknown response")
            statuses = resp["response"]["data"]["statuses"]
            st = statuses[0]
            if "filled" in st:
                f = st["filled"]
                size, px = Decimal(str(f["totalSz"])), Decimal(str(f["avgPx"]))
                if not size.is_finite() or not px.is_finite() or size <= 0 or px <= 0:
                    raise ValueError("invalid fill acknowledgement")
                return OrderResult(
                    HyperliquidExchange._order_id(f["oid"]), "filled", size, px
                )
            if "resting" in st:
                return OrderResult(HyperliquidExchange._order_id(st["resting"]["oid"]),
                                   "resting", Decimal(0), None)
            if "error" in st:
                return OrderResult(None, "rejected", Decimal(0), None, str(st["error"]))
        except (
            KeyError,
            IndexError,
            TypeError,
            AttributeError,
            ArithmeticError,
            ValueError,
        ):
            return OrderResult(None, "uncertain", Decimal(0), None, "unparseable acknowledgement")
        return OrderResult(None, "uncertain", Decimal(0), None, "unknown response shape")


def stream_market(exchange: Exchange, clock: Iterator[WorldEvent]) -> Iterator[WorldEvent]:
    """Interleave live venue reads with a clock stream.

    For each tick, emits the tick, then one ``MarketMid`` per coin, then any
    funding observations. Suitable for the ``testnet`` world's read-only probe.
    """
    for tick in clock:
        yield tick
        for coin, mid in exchange.mids().items():
            yield WorldEvent(
                WorldEventKind.MARKET_MID,
                tick.ts_ns,
                exchange.name,
                {"coin": coin, "mid": str(mid)},
            )
        for f in exchange.funding():
            yield WorldEvent(
                WorldEventKind.FUNDING,
                tick.ts_ns,
                exchange.name,
                {"coin": f.coin, "rate": str(f.rate), "premium": str(f.premium)},
            )
