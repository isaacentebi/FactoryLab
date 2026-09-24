"""Exchange adapters: the protocol, a deterministic fake, and Hyperliquid.

Amounts on this boundary are ``Decimal`` in the venue's own units. Conversion
to wallet micro-USD happens in the runtime using the venue's reported mark, so
the exchange adapter never touches the wallet.
"""

from __future__ import annotations

import hashlib
import os
import random
from dataclasses import dataclass, field, replace
from decimal import ROUND_DOWN, Decimal
from enum import StrEnum
from math import isfinite
from typing import Any, Protocol
from uuid import uuid4

from factorylab.world.events import WorldEvent, WorldEventKind
from factorylab.world.vaults import FAKE_ACCOUNT


class VenueUnavailable(RuntimeError):
    """The venue's API failed after retries. Prices are never served from an older
    read; an account read may fall back to the last complete snapshot, and then says
    so (``AccountState.stale``) and keeps its original ``observed_at_ns``."""


NS_PER_MS = 1_000_000
NS_PER_HOUR = 3_600 * 1_000_000_000

# Hyperliquid refuses any perp or spot order worth less than this, on both networks.
# Published with lot and tick size so a size is known to be legal before it is paid for.
MIN_ORDER_VALUE_USD = "10"


def _position_leverage(raw: Any) -> Decimal | None:
    """Hyperliquid's per-position ``leverage`` object (``{"type", "value"}``) as a number."""
    value = raw.get("value") if isinstance(raw, dict) else raw
    try:
        leverage = Decimal(str(value))
    except (ArithmeticError, ValueError, TypeError):
        return None
    return leverage if leverage.is_finite() and leverage > 0 else None


def _funding_identity(row: dict, coin: str, stamp_ms: int) -> str:
    """One funding payment's identity, from the row itself.

    Hyperliquid's ``userFunding`` rows carry an all-zero ``hash`` (funding is not a
    transaction), so ``hash:coin`` named every payment of a coin the same thing: a
    page kept one of them and the live cursor refused every later one as seen. A
    real hash keeps its historical ``hash:coin`` identity; a zero or missing one is
    replaced by the row's full identity -- time, coin, amount and position size --
    which is what distinguishes two payments at all.
    """
    raw = str(row.get("hash") or "")
    if raw and raw.lower().removeprefix("0x").strip("0"):
        return f"{raw}:{coin}"
    delta = row["delta"]
    return f"funding:{stamp_ms}:{coin}:{delta.get('usdc')}:{delta.get('szi')}"


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
    market: str = "perp"

    def __post_init__(self) -> None:
        if self.market not in ("perp", "spot"):
            raise ValueError("market must be perp or spot")
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
    market: str = "perp"
    inventory_size: Decimal | None = None


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
class SpotBalance:
    coin: str
    total: Decimal
    available: Decimal


@dataclass(frozen=True)
class AccountState:
    equity_usd: Decimal
    cash_usd: Decimal
    positions: tuple[Position, ...]
    margin_used_usd: Decimal
    spot_balances: tuple[SpotBalance, ...] = ()
    # The perpetuals account on its own: ``equity_usd`` also carries the spot book,
    # marked, and spot marks are not collateral for a perp. Custody keeps the two
    # apart, so the venue states the split rather than leaving it to be derived.
    perps_equity_usd: Decimal | None = None
    # When the venue gave this account, and whether it is a fallback to an older
    # snapshot rather than this read's answer. A stale account is never live: the
    # wind-down's reconciliation, the watchers and the prompts refuse it.
    observed_at_ns: int | None = None
    stale: bool = False
    # Spot tokens held that the venue gives no usable USD mark for. They are listed
    # in ``spot_balances`` and excluded from ``equity_usd``: an equity that counts
    # them at a guessed price is invented, and one unpriceable token must not make
    # the whole account unreadable.
    unpriced: tuple[str, ...] = ()


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
              client_id: str | None = None, market: str = "perp") -> OrderResult: ...
    def instruments(self) -> dict: ...
    def set_leverage(self, coin: str, leverage: int, *, market: str = "perp") -> dict: ...
    def collateral_view(self, coin: str, market: str = "perp") -> dict: ...


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
    spot_pairs: tuple[str, ...] = ()
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
    min_order_value_usd: Decimal = Decimal(0)  # published and enforced; the fake has no floor
    listed_coins: tuple[str, ...] = ()
    listed_spot_pairs: tuple[str, ...] = ()
    # Vaults (factorylab/world/vaults.py). The lockup is mainnet's documented day. The
    # fake has no trading on a vault's own account, so a vault's equity moves only by
    # ``vault_return_bps`` a step; and an outside depositor exists only when a caller
    # scripts one: ``vault_depositor_usd`` enters every vault this account leads and
    # leaves ``vault_depositor_steps`` steps later. All zero: no vault ever moves.
    vault_lockup_ns: int = 86_400 * 10**9
    vault_return_bps: Decimal = Decimal(0)
    vault_depositor_usd: Decimal = Decimal(0)
    vault_depositor_steps: int = 0
    # The account this venue's books are, as a leader or depositor names it. A class
    # attribute, not a field: the fake has one account and it is not configurable.
    _address = FAKE_ACCOUNT

    def __post_init__(self) -> None:
        self._rng = random.Random(self.seed)
        self._now_ns = 0
        self._step = 0
        self._mids: dict[str, Decimal] = dict(self.start_prices)
        for c in (*self.coins, *self.listed_coins):
            self._mids.setdefault(c, Decimal("100"))
        self._spot_cash = Decimal(0)
        self._spot_positions: dict[str, Position] = {}
        for pair in (*self.spot_pairs, *self.listed_spot_pairs):
            self._mids.setdefault(pair.split("/")[0], Decimal("100"))
            self._mids[pair] = self._mids[pair.split("/")[0]]
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
        for coin in dict.fromkeys((*self.coins, *self.listed_coins,
                                   *(p.split("/")[0] for p in
                                     (*self.spot_pairs, *self.listed_spot_pairs)))):
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
        for pair in dict.fromkeys((*self.spot_pairs, *self.listed_spot_pairs)):
            self._mids[pair] = self._mids[pair.split("/")[0]]
            self._mid_history[pair].append((ts_ns, self._mids[pair]))
            events.append(WorldEvent(WorldEventKind.MARKET_MID, ts_ns, self.name,
                                     {"coin": pair, "mid": str(self._mids[pair])}))
        events.extend(self._cross_resting())
        events.extend(self._liquidate_if_needed())
        if ts_ns - self._last_funding_ns >= self.funding_interval_ns:
            self._last_funding_ns = ts_ns - (ts_ns % self.funding_interval_ns)
            events.extend(self._apply_funding())
        if self.__dict__.get("_vaults"):
            self._advance_vaults()
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
        self._cash = Decimal(cash_usd) - self._spot_cash - sum(
            (p.size * p.entry_px for p in self._spot_positions.values()), Decimal(0))

    # ---- protocol

    def mids(self) -> dict[str, Decimal]:
        return dict(self._mids)

    def funding(self) -> list[FundingEvent]:
        return [FundingEvent(c, self.funding_rate, None, self._now_ns)
                for c in dict.fromkeys((*self.coins, *self.listed_coins))]

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
            equity_usd=self._cash + unrealized + self._spot_cash + sum(
                (p.size * self._mids[p.coin] for p in self._spot_positions.values()), Decimal(0)),
            perps_equity_usd=self._cash + unrealized,
            cash_usd=self._cash,
            positions=tuple(self._positions.values()),
            margin_used_usd=margin,
            spot_balances=(SpotBalance("USDC", self._spot_cash, self._spot_available("USDC")), *(
                SpotBalance(p.coin.split("/")[0], p.size, self._spot_available(p.coin))
                for p in self._spot_positions.values())),
        )

    def collateral_view(self, coin: str, market: str = "perp") -> dict:
        """The pool this venue would actually charge an order's margin against.

        GPT-6 Pro's third reading §2: a collateral check against ``equity_usd``
        is too broad, because equity includes spot marks that are not eligible
        collateral for a perp. So the venue says what it holds, by class, and
        the runtime does the arithmetic on the right pool:

        ``eligible_equity_usd`` for a perp is the perps account alone -- cash
        plus unrealised P&L -- and never the spot book. ``open_order_holds_usd``
        is the margin resting orders are holding, and
        ``holds_included_in_margin_used`` says whether that is already inside
        ``margin_used_usd``; here it is not, because the fake charges margin for
        open positions only, so the caller must add it.

        The fake answers honestly about being a fake: these are its own books,
        read at its own clock, and its ``account_mode`` is the cross margin it
        actually implements.
        """
        spot = "/" in coin or market == "spot"
        resting = sum((o.size * o.limit_px / self._leverage.get(o.coin, self.max_leverage)
                       for o in self._resting.values()
                       if o.market == "perp" and not o.reduce_only), Decimal(0))
        return {
            "account_mode": "cross",
            "collateral_asset": "USDC",
            "eligible_equity_usd": self._perp_equity(),  # never the spot book
            "margin_used_usd": self.account().margin_used_usd,
            "open_order_holds_usd": resting,
            "holds_included_in_margin_used": False,
            "leverage_for_instrument": (Decimal(1) if spot
                                        else self._leverage.get(coin, self.max_leverage)),
            "position_size": (Decimal(0) if spot else
                              (self._positions[coin].size if coin in self._positions
                               else Decimal(0))),
            "spot_available": {
                "USDC": self._spot_available("USDC"),
                **({coin.split("/")[0]: self._spot_available(coin)} if spot else {}),
            },
            "observed_at_ns": self._now_ns,
        }

    def place(self, order: Order) -> OrderResult:
        """A stable client id admits at most one order, including after a lost acknowledgement."""
        if order.client_id is None:
            return self._place(order)
        client_id = order.client_id
        if client_id in self._client_results:
            return self._client_results[client_id]
        result = self._place(order)
        self._client_results[client_id] = result
        return result

    def _place(self, order: Order) -> OrderResult:
        if order.coin not in (self.spot_pairs if order.market == "spot" else self.coins):
            return OrderResult(None, "rejected", Decimal(0), None, "unknown coin")
        if order.market == "spot" and (order.size % Decimal("0.000001")
                or order.limit_px is not None and order.limit_px % Decimal("0.01")):
            return OrderResult(None, "rejected", Decimal(0), None, "invalid spot tick or lot size")
        if self.min_order_value_usd:
            px = order.limit_px if order.limit_px is not None else self._mids[order.coin]
            if order.size * px < self.min_order_value_usd:
                return OrderResult(None, "rejected", Decimal(0), None,
                                   "order below the venue minimum value")
        oid = str(self._next_oid)
        self._next_oid += 1
        if order.kind is OrderKind.LIMIT:
            assert order.limit_px is not None
            if self._crosses(order, self._mids[order.coin]):
                return self._fill(oid, order, order.limit_px)
            if order.market == "spot" and not self._spot_affordable(order, order.limit_px):
                return OrderResult(oid, "rejected", Decimal(0), None, "insufficient spot balance")
            self._resting[oid] = order
            return OrderResult(oid, "resting", Decimal(0), None)
        mid = self._mids[order.coin]
        half = mid * self.spread_bps / Decimal(20_000)
        px = mid + half if order.is_buy else mid - half
        if order.market == "spot":
            px = px.quantize(Decimal("0.01"))
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
        market: str = "perp",
    ) -> OrderResult:
        """Close at most the current position; flat or invalid requests are rejected."""
        if client_id is not None and client_id in self._client_results:
            return self._client_results[client_id]
        if market not in ("perp", "spot"):
            return OrderResult(None, "rejected", Decimal(0), None, "unknown market")
        pos = (self._spot_positions if market == "spot" else self._positions).get(coin)
        if pos is None:
            return OrderResult(None, "rejected", Decimal(0), None, "no open position")
        if size is not None and (not size.is_finite() or size <= 0):
            return OrderResult(None, "rejected", Decimal(0), None, "size must be positive")
        amount = abs(pos.size) if size is None else min(size, abs(pos.size))
        return self.place(Order(coin, pos.size < 0, amount, client_id=client_id,
                                reduce_only=True, market=market))

    def set_leverage(self, coin: str, leverage: int, *, market: str = "perp") -> dict:
        """Accept integer leverage within the venue cap without changing other coins."""
        if market == "spot" or coin in self.spot_pairs:
            return {"status": "rejected", "error": "spot does not support leverage"}
        if coin not in self.coins:
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
        if order.market == "spot":
            return self._fill_spot(oid, order, px)
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
        fill = Fill(oid, order.coin, order.is_buy, order.size, px, fee, self._now_ns,
                    realized, liquidation)
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
        return margin <= self._perp_equity()

    def _liquidate_if_needed(self) -> list[WorldEvent]:
        """Force-close every position at mid when equity falls below maintenance margin.

        Maintenance margin is ``maintenance_fraction`` of initial margin
        (notional / each coin's leverage). The realised loss lands in cash like any
        other fill, so a leveraged position can take the account negative.

        Guarantees liquidation at the first observed price below maintenance margin,
        never that the loss stops there: one step of the price path is atomic, so a
        gap larger than the maintenance buffer is realised in full and the account,
        and the world wallet that settles it, end below zero by the overshoot. A
        world's ``termination.balance_floor_usd`` is therefore a condition tested
        after each settlement, not a level the venue can be held to.
        """
        if not self._positions:
            return []
        acct = self.account()
        maintenance = acct.margin_used_usd * self.maintenance_fraction
        if self._perp_equity() >= maintenance:
            return []
        events: list[WorldEvent] = []
        for pos in list(self._positions.values()):
            oid = str(self._next_oid)
            self._next_oid += 1
            close = Order(pos.coin, pos.size < 0, abs(pos.size))
            self._fill(oid, close, self._mids[pos.coin], liquidation=True)
            events.extend(self.drain_events())
        return events

    def _perp_equity(self) -> Decimal:
        return self._cash + sum(((self._mids[p.coin] - p.entry_px) * p.size
                                 for p in self._positions.values()), Decimal(0))

    def _perp_withdrawable(self) -> Decimal:
        resting = sum((o.size * o.limit_px / self._leverage.get(o.coin, self.max_leverage)
                       for o in self._resting.values() if o.market == "perp"
                       and not o.reduce_only), Decimal(0))
        return max(Decimal(0), min(self._cash,
                   self._perp_equity() - self.account().margin_used_usd - resting))

    def _spot_available(self, coin: str) -> Decimal:
        if coin == "USDC":
            held = self._spot_cash
            committed = sum((o.size * o.limit_px * (1 + self.fee_bps / 10_000)
                             for o in self._resting.values() if o.market == "spot" and o.is_buy),
                            Decimal(0))
        else:
            held = self._spot_positions[coin].size if coin in self._spot_positions else Decimal(0)
            committed = sum((o.size for o in self._resting.values()
                             if o.market == "spot" and o.coin == coin and not o.is_buy), Decimal(0))
        return max(Decimal(0), held - committed)

    def _spot_affordable(self, order: Order, px: Decimal) -> bool:
        fee = (order.size * px * self.fee_bps / 10_000).quantize(Decimal("0.000001"))
        return ((not order.reduce_only and order.size * px + fee <= self._spot_available("USDC"))
                if order.is_buy else order.size <= self._spot_available(order.coin))

    def class_transfer(self, amount: Decimal, to_perp: bool) -> None:
        """Move quote cash between classes without changing total capital."""
        available = self._spot_available("USDC") if to_perp else self._perp_withdrawable()
        if not amount.is_finite() or amount <= 0 or amount > available:
            raise ValueError("insufficient available class cash")
        self._spot_cash += -amount if to_perp else amount
        self._cash += amount if to_perp else -amount

    def instruments(self) -> dict:
        """Publish deterministic lot and price increments and the venue's order floor."""
        return {market: [{"coin": c, "lot_size": "0.000001", "tick_size": "0.01",
                          "min_order_value_usd": str(self.min_order_value_usd)}
                         for c in coins]
                for market, coins in (
                    ("perp", tuple(dict.fromkeys((*self.coins, *self.listed_coins)))),
                    ("spot", tuple(dict.fromkeys((*self.spot_pairs, *self.listed_spot_pairs)))))}

    def _fill_spot(self, oid: str, order: Order, px: Decimal) -> OrderResult:
        pos = self._spot_positions.get(order.coin)
        held = pos.size if pos else Decimal(0)
        fee = (order.size * px * self.fee_bps / 10_000).quantize(Decimal("0.000001"))
        if not self._spot_affordable(order, px):
            return OrderResult(oid, "rejected", Decimal(0), None, "insufficient spot balance")
        realized = Decimal(0) if order.is_buy else (px - pos.entry_px) * order.size
        self._spot_cash += (-order.size * px if order.is_buy else order.size * px) - fee
        size = held + (order.size if order.is_buy else -order.size)
        if size:
            entry = ((held * (pos.entry_px if pos else 0) + order.size * px) / size
                     if order.is_buy else pos.entry_px)
            self._spot_positions[order.coin] = Position(order.coin, size, entry)
        else:
            self._spot_positions.pop(order.coin, None)
        self._fills.append(Fill(oid, order.coin, order.is_buy, order.size, px, fee,
                                self._now_ns, realized, market="spot"))
        self._pending_events.append(WorldEvent(WorldEventKind.FILL, self._now_ns, self.name, {
            "order_id": oid, "coin": order.coin, "is_buy": order.is_buy,
            "size": str(order.size), "px": str(px), "fee_usd": str(fee),
            "realized_usd": str(realized), "liquidation": False, "market": "spot"}))
        return OrderResult(oid, "filled", order.size, px)

    def _apply_funding(self) -> list[WorldEvent]:
        events: list[WorldEvent] = []
        for coin in dict.fromkeys((*self.coins, *self.listed_coins)):
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

    # ---- vaults: the venue's terms and their sources are in factorylab/world/vaults.py

    def _vault_state(self) -> tuple[dict, list, dict]:
        """The vault book, its ledger rows and its client results, created on first use.

        Created lazily so a venue that never touches a vault checkpoints exactly as
        it did before vaults existed.
        """
        d = self.__dict__
        return (d.setdefault("_vaults", {}), d.setdefault("_vault_rows", []),
                d.setdefault("_vault_results", {}))

    def _vault_equity(self) -> Decimal:
        """This account's equity across every vault it holds: a pot, never collateral."""
        from factorylab.world.vaults import FAKE_ACCOUNT

        return sum((v["followers"][FAKE_ACCOUNT]["equity"]
                    for v in self.__dict__.get("_vaults", {}).values()
                    if FAKE_ACCOUNT in v["followers"]), Decimal(0))

    def _vault_row(self, kind: str, tx: str, **fields: Any) -> None:
        """One row of this account's own venue ledger, in the normalised row shape."""
        _, rows, _ = self._vault_state()
        rows.append({"ts_ns": self._now_ns, "hash": tx, "type": kind, **fields})

    def vault_create(self, name: str, description: str, usd: Decimal, *,
                     client_id: str | None = None) -> dict:
        """Guarantees one vault per client id, created only with the deposit and fee affordable.

        The initial deposit and the creation fee both leave the perps account; the
        deposit becomes this account's equity in the vault, the fee is gone.
        """
        from factorylab.world.vaults import (
            CREATE_FEE_USD,
            FAKE_ACCOUNT,
            check_create,
            exact_micro,
            row_hash,
        )

        vaults, _, results = self._vault_state()
        if client_id is not None and client_id in results:
            return dict(results[client_id])
        if exact_micro(usd) is None:  # the venue takes whole micro-USDC, like the live one
            return {"status": "rejected", "error": "usd is finer than one micro-USD"}
        reason = check_create(name, description, usd)
        usd = Decimal(str(usd))
        if reason is None and usd + CREATE_FEE_USD > self._perp_withdrawable():
            reason = "insufficient perps collateral for the deposit and the creation fee"
        if reason is not None:
            result = {"status": "rejected", "error": reason}
        else:
            address = row_hash("vault", self.seed, len(vaults), name)[:42]
            self._cash -= usd + CREATE_FEE_USD
            vaults[address] = {
                "name": name, "description": description, "leader": FAKE_ACCOUNT,
                "created_ns": self._now_ns, "closed": False, "allow_deposits": True,
                "followers": {FAKE_ACCOUNT: {"equity": usd, "basis": usd,
                                             "entry_ns": self._now_ns,
                                             "lockup_until_ns": self._now_ns
                                             + self.vault_lockup_ns}}}
            tx = row_hash("create", address, self._now_ns)
            self._vault_row("vaultCreate", tx, vault=address, user=None, usd=usd,
                            fee=CREATE_FEE_USD)
            result = {"status": "ok", "vault": address, "usd": str(usd),
                      "fee_usd": str(CREATE_FEE_USD), "hash": tx}
        if client_id is not None:
            results[client_id] = result
        return dict(result)

    def vault_transfer(self, vault: str, is_deposit: bool, usd: Decimal, *,
                       client_id: str | None = None) -> dict:
        """Guarantees one transfer per client id, between perps collateral and a vault."""
        from factorylab.world.vaults import FAKE_ACCOUNT, exact_micro

        _, _, results = self._vault_state()
        if client_id is not None and client_id in results:
            return dict(results[client_id])
        if exact_micro(usd) is None:  # the venue takes whole micro-USDC, like the live one
            return {"status": "rejected", "error": "usd is finer than one micro-USD"}
        result = self._vault_move(str(vault).lower(), FAKE_ACCOUNT, is_deposit,
                                  Decimal(str(usd)))
        if client_id is not None:
            results[client_id] = result
        return dict(result)

    def _vault_move(self, vault: str, user: str, is_deposit: bool, usd: Decimal) -> dict:
        """One deposit or withdrawal by ``user``, under the venue's documented terms.

        This account's own moves change its perps cash and write its ledger rows; an
        outside depositor's move changes only the vault, except that the commission on
        its profit is paid to the leader, which writes a commission row when the
        leader is this account.
        """
        from factorylab.world.vaults import (
            FAKE_ACCOUNT,
            LEADER_MIN_FRACTION,
            commission_on,
            leader_share_after,
            row_hash,
        )

        vaults, rows, _ = self._vault_state()
        v = vaults.get(vault)
        if v is None:
            return {"status": "rejected", "error": "unknown vault"}
        if v["closed"]:
            return {"status": "rejected", "error": "vault is closed"}
        if not usd.is_finite() or usd <= 0:
            return {"status": "rejected", "error": "usd must be positive"}
        own, followers = user == FAKE_ACCOUNT, v["followers"]
        total = sum((f["equity"] for f in followers.values()), Decimal(0))
        leader_equity = followers.get(v["leader"], {}).get("equity", Decimal(0))
        tx = row_hash("transfer", vault, user, is_deposit, usd, self._now_ns, len(rows))
        if is_deposit:
            if not v["allow_deposits"] and user != v["leader"]:
                return {"status": "rejected", "error": "vault does not accept deposits"}
            if own and usd > self._perp_withdrawable():
                return {"status": "rejected", "error": "insufficient perps collateral"}
            if user != v["leader"] and leader_equity / (total + usd) < LEADER_MIN_FRACTION:
                return {"status": "rejected",
                        "error": "deposit would take the leader below 5% of the vault"}
            f = followers.setdefault(user, {"equity": Decimal(0), "basis": Decimal(0)})
            f.update(equity=f["equity"] + usd, basis=f["basis"] + usd, entry_ns=self._now_ns,
                     lockup_until_ns=self._now_ns + self.vault_lockup_ns)
            if own:
                self._cash -= usd
                self._vault_row("vaultDeposit", tx, vault=vault, user=None, usd=usd)
            return {"status": "ok", "vault": vault, "usd": str(usd), "hash": tx}
        f = followers.get(user)
        if f is None or usd > f["equity"]:
            return {"status": "rejected",
                    "error": "withdrawal exceeds the equity held in the vault"}
        if self._now_ns < f["lockup_until_ns"]:
            return {"status": "rejected",
                    "error": f"deposit locked until {f['lockup_until_ns']} ns"}
        if user == v["leader"]:
            after = leader_share_after(f["equity"], total, usd)
            if after is not None and after < LEADER_MIN_FRACTION:
                return {"status": "rejected", "error": "leader share would fall below 5%"}
        commission, basis_out = commission_on(f["equity"], f["basis"], usd)
        f.update(equity=f["equity"] - usd, basis=f["basis"] - basis_out)
        if f["equity"] == 0:
            del followers[user]
        net = usd - commission
        if own:
            self._cash += net
            self._vault_row("vaultWithdraw", tx, vault=vault, user=user, requested=usd,
                            commission=commission, closing_cost=Decimal(0), basis=basis_out,
                            net=net)
        rebate = Decimal(0)
        if v["leader"] == FAKE_ACCOUNT and commission > 0:
            # The leader is paid in the withdrawal's own transaction; when the leader
            # withdrew, that is its own commission coming back.
            self._cash += commission
            self._vault_row("vaultLeaderCommission", tx, vault=None, user=FAKE_ACCOUNT,
                            usd=commission)
            rebate = commission if own else Decimal(0)
        return {"status": "ok", "vault": vault, "usd": str(usd), "net": str(net),
                "basis": str(basis_out), "commission": str(commission),
                "commission_rebate": str(rebate), "hash": tx}

    def vault_details(self, vault: str) -> dict:
        """A vault's record in the surface's shape; an unknown vault is an error."""
        from factorylab.world.vaults import FAKE_ACCOUNT, LEADER_MIN_FRACTION, LEADER_PROFIT_SHARE

        v = self.__dict__.get("_vaults", {}).get(str(vault).lower())
        if v is None:
            return {"error": "vault not found"}
        followers = v["followers"]
        total = sum((f["equity"] for f in followers.values()), Decimal(0))
        leader_equity = followers.get(v["leader"], {}).get("equity", Decimal(0))
        mine = followers.get(FAKE_ACCOUNT)
        own = mine["equity"] if mine else Decimal(0)
        leading = v["leader"] == FAKE_ACCOUNT
        withdrawable = (max(Decimal(0), (own - LEADER_MIN_FRACTION * total)
                            / (1 - LEADER_MIN_FRACTION)) if leading and len(followers) > 1
                        else own)
        return {"vault": str(vault).lower(), "name": v["name"], "description": v["description"],
                "leader": v["leader"], "is_leader": leading, "equity_usd": total,
                "depositors": len([u for u in followers if u != v["leader"]]),
                "depositors_capped": False,
                "leader_fraction": leader_equity / total if total else Decimal(0),
                "leader_commission": LEADER_PROFIT_SHARE, "own_equity_usd": own,
                "own_lockup_until_ns": mine["lockup_until_ns"] if mine else None,
                "max_withdrawable_usd": withdrawable.quantize(Decimal("0.000001"),
                                                              rounding=ROUND_DOWN),
                "allow_deposits": v["allow_deposits"], "is_closed": v["closed"],
                "observed_at_ns": self._now_ns}

    def vault_equities(self) -> dict:
        """This account's vault positions and the vaults it leads."""
        from factorylab.world.vaults import FAKE_ACCOUNT

        vaults = self.__dict__.get("_vaults", {})
        return {"positions": [{"vault": a, "equity_usd": v["followers"][FAKE_ACCOUNT]["equity"],
                               "locked_until_ns": v["followers"][FAKE_ACCOUNT]["lockup_until_ns"]}
                              for a, v in vaults.items() if FAKE_ACCOUNT in v["followers"]],
                "leading": [{"vault": a, "name": v["name"]} for a, v in vaults.items()
                            if v["leader"] == FAKE_ACCOUNT],
                "observed_at_ns": self._now_ns}

    def vault_ledger(self, since_ns: int) -> list[dict]:
        """This account's vault ledger rows at or after an inclusive cursor, oldest first."""
        return [dict(r) for r in self.__dict__.get("_vault_rows", []) if r["ts_ns"] >= since_ns]

    def vault_lookup(self, client_id: str, *, operation: str, args: dict, since_ns: int = 0,
                     claimed: frozenset = frozenset(), position: int = 0,
                     peers: int = 1) -> dict:
        """Resolve a vault write from this account's own ledger rows, as the live venue
        must: the fake answers by the row, never by the client id it remembers."""
        from factorylab.world.vaults import match_intent

        return match_intent(self.vault_ledger(since_ns), operation, args, FAKE_ACCOUNT,
                            claimed=claimed, position=position, peers=peers)

    def simulate_vault(self, name: str, leader: str, usd: Decimal) -> str:
        """An outside party's vault, for this account to deposit into; returns its address."""
        from factorylab.world.vaults import row_hash

        vaults, _, _ = self._vault_state()
        address = row_hash("outside", leader, len(vaults), name)[:42]
        vaults[address] = {"name": name, "description": name, "leader": leader.lower(),
                           "created_ns": self._now_ns, "closed": False, "allow_deposits": True,
                           "followers": {leader.lower(): {
                               "equity": Decimal(usd), "basis": Decimal(usd),
                               "entry_ns": self._now_ns, "lockup_until_ns": self._now_ns}}}
        return address

    def simulate_deposit(self, vault: str, user: str, usd: Decimal) -> dict:
        """An outside depositor's deposit: money from outside the factory enters the vault."""
        return self._vault_move(str(vault).lower(), user.lower(), True, Decimal(usd))

    def simulate_withdraw(self, vault: str, user: str, usd: Decimal) -> dict:
        """An outside depositor's withdrawal, paying the leader its commission."""
        return self._vault_move(str(vault).lower(), user.lower(), False, Decimal(usd))

    def mark_vaults(self, bps: Decimal) -> None:
        """Every open vault's followers gain or lose ``bps`` of their equity, pro rata."""
        factor = 1 + Decimal(bps) / Decimal(10_000)
        for v in self.__dict__.get("_vaults", {}).values():
            if not v["closed"]:
                for f in v["followers"].values():
                    f["equity"] = (f["equity"] * factor).quantize(Decimal("0.000001"),
                                                                  rounding=ROUND_DOWN)

    def _advance_vaults(self) -> None:
        """One step of the scripted vault world: the return, then the outside depositor."""
        from factorylab.world.vaults import FAKE_ACCOUNT

        if self.vault_return_bps:
            self.mark_vaults(self.vault_return_bps)
        if not self.vault_depositor_usd:
            return
        depositor = "0x" + "de9051".rjust(40, "0")
        entered = self.__dict__.setdefault("_depositor_entered", {})
        for address, v in self.__dict__["_vaults"].items():
            if v["leader"] != FAKE_ACCOUNT or v["closed"]:
                continue
            if address not in entered:
                if self.simulate_deposit(address, depositor,
                                         self.vault_depositor_usd)["status"] == "ok":
                    entered[address] = self._step
                continue
            held = v["followers"].get(depositor)
            if (entered[address] is not None and held is not None
                    and self._step - entered[address] >= self.vault_depositor_steps
                    and self._now_ns >= held["lockup_until_ns"]):
                if self.simulate_withdraw(address, depositor, held["equity"])["status"] == "ok":
                    entered[address] = None


# --------------------------------------------------------------------- hyperliquid


class HyperliquidExchange:
    """Hyperliquid perpetuals and configured USDC spot pairs via the official SDK.

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
        spot_pairs: tuple[str, ...] = (),
    ) -> None:
        from hyperliquid.info import Info
        from hyperliquid.utils import constants

        self.name = "hyperliquid-mainnet" if mainnet else "hyperliquid-testnet"
        self.base_url = constants.MAINNET_API_URL if mainnet else constants.TESTNET_API_URL
        self.coins = coins
        self.spot_pairs = spot_pairs
        self._info = Info(self.base_url, skip_ws=True, timeout=timeout)
        self._address = address
        self._exchange: Any | None = None
        key = os.environ.get(key_env)
        if key:
            from eth_account import Account
            from hyperliquid.exchange import Exchange as HLExchange

            wallet = Account.from_key(key)
            self._address = self._address or wallet.address
            self._exchange = HLExchange(wallet, self.base_url, account_address=self._address,
                                        timeout=timeout)
        meta = self._info.meta()
        self._sz_decimals = {a["name"]: int(a["szDecimals"]) for a in meta["universe"]}
        self._listed_coins = tuple(self._sz_decimals)
        self._spot_names = {}
        self._spot_tokens = {}
        # Spot metadata is read whatever the manifest configures: a fill is classified
        # by the venue's own universe, never by the subset this world may trade.
        self._configure_spot(self._info.spot_meta())
        self.transient_failures = 0
        self.account_fallbacks = 0
        self._last_mids: dict[str, Decimal] | None = None
        self._last_account: AccountState | None = None
        self._last_account_ns: int | None = None
        # Leverage this account has acknowledged, by coin; set_leverage records it.
        self._leverage: dict[str, Decimal] = {}
        # Hyperliquid accounts are cross-margin unless an instrument was switched
        # to isolated; nothing here switches one, and the mode is reported as read.
        self._account_mode = "cross"

    def _configure_spot(self, meta: dict) -> None:
        """Record the venue's whole spot universe, and the wire names of traded pairs."""
        tokens = {t["index"]: t for t in meta["tokens"]}
        self._spot_marks = {}
        self._spot_universe = {}
        for row in meta["universe"]:
            try:
                base, quote = (tokens[i] for i in row["tokens"])
            except (KeyError, ValueError):
                continue  # a pair naming a token this metadata does not carry is not ours
            pair = f'{base["name"]}/{quote["name"]}'
            self._spot_universe[row["name"]] = pair
            if quote["name"] == "USDC":
                self._spot_marks[base["name"]] = row["name"]
            if quote["name"] == "USDC":
                self._spot_names[pair] = row["name"]
                self._spot_tokens[pair] = base["name"]
                self._sz_decimals[pair] = int(base["szDecimals"])
        missing = set(self.spot_pairs) - self._spot_names.keys()
        if missing:
            raise ValueError(f"spot pairs unavailable in venue metadata: {sorted(missing)}")

    def _wire_coin(self, coin: str) -> str:
        return getattr(self, "_spot_names", {}).get(coin, coin)

    def _public_coin(self, coin: str) -> str:
        """A wire name in the venue's spot universe names its pair, configured or not."""
        return getattr(self, "_spot_universe", {}).get(coin, coin)

    def _is_spot(self, coin: str) -> bool:
        """Classify by the venue's spot universe, never by the manifest's traded subset."""
        return coin in getattr(self, "_spot_universe", {})

    def instruments(self) -> dict:
        """Expose lot precision, the venue's price precision rule and its order floor."""
        return {market: [{"coin": c, "lot_size": str(Decimal(1).scaleb(-self._sz_decimals[c])),
                          "tick_size": str(Decimal(1).scaleb(
                              -(8 if market == "spot" else 6) + self._sz_decimals[c])),
                          "price_significant_figures": 5, "integer_prices_allowed": True,
                          "min_order_value_usd": MIN_ORDER_VALUE_USD}
                         for c in coins]
                for market, coins in (("perp", getattr(self, "_listed_coins", self.coins)),
                                      ("spot", tuple(getattr(self, "_spot_names", {}))))}

    def _guarded(self, what: str, call: Any, attempts: int = 3) -> Any:
        """Call the API with retries on transient failures; raise VenueUnavailable after.

        Timeouts, connection errors and 5xx answers are the venue's weather, not
        the world's death. Three attempts with 0.5 s, 1 s, 2 s pauses; every
        exhausted call counts in ``transient_failures`` so the runtime can report it.
        """
        import time

        import requests
        from hyperliquid.utils.error import ClientError, ServerError

        from factorylab.world.venue_tools import request_weight

        delay = 0.5
        for attempt in range(attempts):
            # Every attempt is a request the venue weighs against the IP limit, a
            # retry after a 429 included: counted before it is sent, whatever answers.
            self.request_weight = getattr(self, "request_weight", 0) + request_weight(what)
            try:
                result = call()
            except ClientError as exc:
                # A 4xx is the SDK's ClientError, which is not a RuntimeError and used
                # to escape every catch site and kill the tick. A 429 is the venue
                # asking us to slow down: back off harder and ask again. Any other 4xx
                # is an answer that asking again will not change.
                status = getattr(exc, "status_code", None)
                if status != 429 or attempt == attempts - 1:
                    self.transient_failures = getattr(self, "transient_failures", 0) + 1
                    raise VenueUnavailable(
                        f"{what}: ClientError {status}") from exc
                time.sleep(delay * 4)
                delay *= 2
            except (requests.RequestException, OSError, TimeoutError, ServerError) as exc:
                if attempt == attempts - 1:
                    self.transient_failures = getattr(self, "transient_failures", 0) + 1
                    raise VenueUnavailable(f"{what}: {type(exc).__name__}: {exc}") from exc
                time.sleep(delay)
                delay *= 2
            else:
                # The weight that grows with what was returned is known only now.
                self.request_weight += request_weight(what, result) - request_weight(what)
                return result
        raise AssertionError("unreachable")

    def request_weight_sent(self) -> int:
        """Guarantees the documented venue weight of every request this adapter has sent,
        every attempt counted, monotone. Journaled like any venue read, so a replay
        charges exactly what the recording measured."""
        return getattr(self, "request_weight", 0)

    # ---- reads

    def mids(self) -> dict[str, Decimal]:
        import time

        # A failed read is unavailable, never the last prices served as live ones:
        # every caller already reads an unavailable price as unavailable, and a
        # stale mid in a dict is indistinguishable from a fresh one.
        raw = self._guarded("all_mids", self._info.all_mids)
        self.__dict__["_last_mids_ns"] = time.time_ns()
        self._last_mids = {c: Decimal(str(raw[self._wire_coin(c)]))
                           for c in (*getattr(self, "_listed_coins", self.coins),
                                     *getattr(self, "_spot_names", {}))
                           if self._wire_coin(c) in raw}
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
            if not isinstance(asset, dict) or not isinstance(asset.get("name"), str):
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
        import time

        if not self._address:
            raise RuntimeError("account() needs an address or a private key")
        spot = mids = None
        try:
            st = self._guarded("user_state", lambda: self._info.user_state(self._address))
            if getattr(self, "spot_pairs", ()):
                spot = self._guarded("spot_user_state", lambda:
                                     self._info.spot_user_state(self._address))
                mids = self._guarded("spot_mids", self._info.all_mids)
        except VenueUnavailable:
            # Half an account is not an account: perps and spot fall back together,
            # so a spot endpoint outage returns the last complete snapshot. Its
            # observation time is not refreshed: a stale account is stale, and the
            # collateral check refuses to open new risk on it.
            if self._last_account is None:
                raise
            self.account_fallbacks = getattr(self, "account_fallbacks", 0) + 1
            return replace(self._last_account, stale=True)
        summary = st["marginSummary"]
        positions: list[Position] = []
        in_effect: dict[str, Decimal] = {}
        for ap in st.get("assetPositions", []):
            p = ap["position"]
            size = Decimal(str(p["szi"]))
            if size == 0:
                continue
            entry = Decimal(str(p["entryPx"])) if p.get("entryPx") else Decimal(0)
            positions.append(Position(p["coin"], size, entry))
            leverage = _position_leverage(p.get("leverage"))
            if leverage is not None:
                in_effect[p["coin"]] = leverage
        # The leverage the venue reports in effect for each open position, as read.
        self.__dict__["_position_leverage"] = in_effect
        balances = []
        unpriced: list[str] = []
        spot_value = Decimal(0)
        if spot is not None:
            for row in spot.get("balances", []):
                total = Decimal(str(row["total"]))
                balances.append(SpotBalance(row["coin"], total,
                                            total - Decimal(str(row.get("hold", "0")))))
                if row["coin"] == "USDC":
                    spot_value += total
                elif total:
                    symbol = self._spot_marks.get(row["coin"])
                    try:
                        mark = Decimal(str(mids[symbol]))
                    except (KeyError, TypeError, ArithmeticError, ValueError):
                        mark = None
                    if mark is None or not mark.is_finite() or mark <= 0:
                        unpriced.append(str(row["coin"]))
                        continue
                    spot_value += total * mark
        observed_at = time.time_ns()
        self.__dict__["_last_account_ns"] = observed_at
        self._last_account = AccountState(
            equity_usd=Decimal(str(summary["accountValue"])) + spot_value,
            perps_equity_usd=Decimal(str(summary["accountValue"])),
            cash_usd=Decimal(str(st.get("withdrawable", summary["accountValue"]))),
            positions=tuple(positions),
            margin_used_usd=Decimal(str(summary["totalMarginUsed"])),
            spot_balances=tuple(balances),
            observed_at_ns=observed_at,
            unpriced=tuple(unpriced),
        )
        return self._last_account

    def collateral_view(self, coin: str, market: str = "perp") -> dict:
        """What Hyperliquid would charge this instrument's margin against, and when it was read.

        ``eligible_equity_usd`` is the perps account value alone. The spot book
        is reported separately in ``spot_available`` and is not collateral for a
        perp: counting it was the reviewer's "too broad if equity_usd includes
        spot marks that are not eligible collateral".

        ``margin_used_usd`` is ``totalMarginUsed``, which covers open positions
        and not resting orders, so ``holds_included_in_margin_used`` is False and
        ``open_order_holds_usd`` is the margin those resting orders hold, at the
        leverage the venue has in effect for each coin, or ``None`` when that is
        not known for some coin. ``leverage_for_instrument`` is likewise the
        venue's own figure or ``None``; nothing here assumes 1x (decision D1).

        ``observed_at_ns`` is the moment of the account read this view is built
        from -- including a fallback to the last complete snapshot when the spot
        endpoint was out -- so a caller can refuse to open new risk on a stale
        answer rather than treating an old number as current. ``stale`` says that
        this view came from such a fallback: a read that succeeded and then an
        endpoint failure in the same tick leaves ``observed_at_ns`` recent, so the
        timestamp alone does not carry the fact.
        """
        account = self.account()
        spot = "/" in coin or market == "spot"
        leverage = self._acknowledged_leverage(coin)
        holds: Decimal | None = Decimal(0)
        for order in self.open_orders():
            if "/" in order["coin"]:
                continue
            order_leverage = self._acknowledged_leverage(order["coin"])
            if order_leverage is None:
                holds = None  # the venue has not said what this order holds
                break
            holds += (Decimal(str(order["size"])) * Decimal(str(order["price"]))
                      / order_leverage)
        usdc = next((b.available for b in account.spot_balances if b.coin == "USDC"), Decimal(0))
        base = coin.split("/")[0] if spot else None
        available = {"USDC": usdc}
        if base is not None:
            available[base] = next(
                (b.available for b in account.spot_balances if b.coin == base), Decimal(0))
        return {
            "account_mode": getattr(self, "_account_mode", "cross"),
            "collateral_asset": "USDC",
            # Perps equity: the venue's account value without the spot book.
            "eligible_equity_usd": account.perps_equity_usd,
            "margin_used_usd": account.margin_used_usd,
            "open_order_holds_usd": holds,
            "holds_included_in_margin_used": False,
            "leverage_for_instrument": Decimal(1) if spot else leverage,
            "position_size": (Decimal(0) if spot else next(
                (p.size for p in account.positions if p.coin == coin), Decimal(0))),
            "spot_available": available,
            "observed_at_ns": getattr(self, "_last_account_ns", None),
            # The account read this view is built from was a fallback to the last
            # complete snapshot, not this read's answer. Its observation time is
            # the earlier read's and can be this very tick, so the marker travels
            # with the view: an age check alone would not see it.
            "stale": bool(getattr(account, "stale", False)),
        }

    def _acknowledged_leverage(self, coin: str) -> Decimal | None:
        """The leverage the venue has in effect for a coin, or ``None`` when it has not said.

        The venue's own account read wins: ``clearinghouseState`` reports the
        leverage of every open position. Failing that, the venue's acknowledgement
        of this account's ``set_leverage``. Neither is a guess, and an unknown
        leverage is reported as unknown rather than as 1x (architect decision D1):
        the venue then decides whether it can carry the order.
        """
        read = self.__dict__.get("_position_leverage", {}).get(coin)
        if read is not None:
            return read
        return self.__dict__.get("_leverage", {}).get(coin)

    def funding_payments(self, since_ns: int) -> list[FundingPayment]:
        """Read inclusive, paginated user cash flows; never infer payments from funding rates.

        Hyperliquid's delta.usdc is a credit to the user, so paid_usd negates it.
        The boundary millisecond is reread and deduplicated by ``_funding_identity``.
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
                    if not isinstance(coin, str) or not coin:
                        continue
                    ident = _funding_identity(row, coin, stamp)
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
        """Return observed fills; an exhausted read raises instead of proving an empty set.

        Runtime polling may defer a failed read, while terminal reconciliation
        reports that failure explicitly. Neither advances the inclusive cursor.
        """
        if not self._address:
            raise RuntimeError("fills() needs an address or a private key")
        raw = self._guarded(
            "user_fills_by_time",
            lambda: self._info.user_fills_by_time(self._address, since_ns // NS_PER_MS),
        )
        if not isinstance(raw, list):
            raise VenueUnavailable("invalid fill response")
        out: list[Fill] = []
        for f in raw:
            try:
                values = [Decimal(str(f.get(key, "0")))
                          for key in ("sz", "px", "fee", "closedPnl")]
                size, px, fee, realized = values
                pair = self._public_coin(f["coin"])
                market = "spot" if self._is_spot(f["coin"]) else "perp"
                if market == "spot":
                    token = f.get("feeToken", "USDC")
                    if token == pair.split("/")[0]:
                        size -= fee if f["side"] == "B" else -fee
                        fee *= px
                    elif token != "USDC":
                        raise ValueError("spot fee token has no USD mark")
                stamp = int(f["time"]) * NS_PER_MS
                if (any(not value.is_finite() for value in values)
                        or size <= 0 or px <= 0 or stamp < 0
                        or f["side"] not in ("B", "A")
                        or not isinstance(f["coin"], str) or not f["coin"]):
                    continue
                out.append(Fill(
                    order_id=str(f["oid"]), coin=pair,
                    is_buy=f["side"] == "B",
                    size=Decimal(str(f["sz"])), px=px, fee=fee, ts_ns=stamp, realized=realized,
                    liquidation=bool(f.get("liquidation")),
                    market=market,
                    inventory_size=size,
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
        raw = self._guarded("candles", lambda: self._info.candles_snapshot(
            self._wire_coin(coin), interval, start_ms, end_ms))
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
        raw = self._guarded("l2_snapshot", lambda: self._info.l2_snapshot(self._wire_coin(coin)))
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
        raw = self._guarded("funding_history", lambda: self._info.funding_history(
            coin, end_ms - n * NS_PER_HOUR // NS_PER_MS, end_ms))
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
                "coin": self._public_coin(o["coin"]),
                "side": "buy" if o["side"] == "B" else "sell",
                "size": Decimal(str(o["sz"])),
                "price": Decimal(str(o["limitPx"])),
            }
            for o in self._guarded("open_orders", lambda: self._info.open_orders(self._address))
        ]

    # ---- writes

    def client_id(self, handle: str):
        """Namespaced worlds cannot reuse another world's venue decision identity.

        Decision handles restart at ``decision-1`` on a fresh ledger and the manifest
        namespace is fixed, so a launch nonce is folded in as well: a rerun of one
        manifest can never reproduce a previous launch's identities, while a resumed
        world restores its nonce and so keeps the identities it already submitted.
        Absent both, the historical bare-handle derivation is preserved exactly.
        """
        from hyperliquid.utils.types import Cloid

        parts = [str(part) for part in (getattr(self, "_client_namespace", None),
                                        getattr(self, "_launch_nonce", None))
                 if part is not None]
        identity = ":".join([*parts, handle])
        return Cloid.from_str("0x" + hashlib.sha256(identity.encode()).hexdigest()[:32])

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
            # A venue answer carrying another launch's identity is not ours to book.
            # Nothing is claimed about it: this launch's identity stays uncertain.
            expected = None if order_id is not None else self.client_id(client_id).to_raw()
            observed = order.get("cloid")
            if (expected is not None and isinstance(observed, str)
                    and observed.lower() != expected.lower()):
                return OrderResult(None, "uncertain", Decimal(0), None,
                                   "order identity belongs to another launch")
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
        except Exception as exc:
            return OrderResult(order_id, "uncertain", Decimal(0), None,
                               f"lookup exception: {type(exc).__name__}")
        return OrderResult(order_id, "uncertain", Decimal(0), None, "unrecognized order status")

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
            except Exception as exc:
                # Exception messages may carry credentials or signed request bodies.
                result = OrderResult(None, "uncertain", Decimal(0), None,
                                     f"submit exception: {type(exc).__name__}")
            if result.status == "uncertain":
                submitted = result
                result = self.lookup(client_id)
                if result.status == "uncertain":
                    result = OrderResult(result.order_id, "uncertain", result.filled_size,
                                         result.avg_px,
                                         f"submit: {submitted.error}; lookup: {result.error}")
        results[client_id] = result
        return result

    def place(self, order: Order) -> OrderResult:
        if self._exchange is None:
            return OrderResult(None, "rejected", Decimal(0), None, "no signing key")
        client_id = order.client_id or str(uuid4())
        if client_id in self.__dict__.get("_client_results", {}):
            return self._submit(client_id, lambda: None)  # existing identity only reconciles
        if order.market == "spot":
            if order.coin not in getattr(self, "spot_pairs", ()):
                return OrderResult(None, "rejected", Decimal(0), None, "unknown spot pair")
            if order.reduce_only and order.is_buy:
                return OrderResult(None, "rejected", Decimal(0), None, "spot buy cannot reduce")
        elif order.coin in getattr(self, "spot_pairs", ()):
            return OrderResult(None, "rejected", Decimal(0), None, "pair requires market spot")
        if order.market == "perp" and order.coin not in self.coins:
            return OrderResult(None, "rejected", Decimal(0), None, "unregistered perp coin")
        if order.market == "perp" and order.reduce_only and order.kind is OrderKind.MARKET:
            return self._close_perp(order.coin, order.size, client_id, is_buy=order.is_buy)
        try:
            rounded = self._round_size(order.coin, order.size)
            sz = self._wire_number(rounded)
            price = (self._wire_number(self._round_price(
                order.coin, order.limit_px, order.is_buy, spot=order.market == "spot"))
                     if order.kind is OrderKind.LIMIT else None)
        except Exception as exc:
            return OrderResult(None, "rejected", Decimal(0), None,
                               f"order preparation failed: {type(exc).__name__}")
        cloid = self.client_id(client_id)
        if order.kind is OrderKind.MARKET:
            return self._market_order(order.coin, order.is_buy, rounded, client_id)
        return self._submit(client_id, lambda: self._exchange.order(
            self._wire_coin(order.coin), order.is_buy, sz, price,
            {"limit": {"tif": "Gtc"}},
            reduce_only=order.reduce_only and order.market == "perp", cloid=cloid))

    def _market_order(self, coin: str, is_buy: bool, size: Decimal, client_id: str,
                      *, reduce_only: bool = False) -> OrderResult:
        """Fresh quote validation precedes dispatch; write-phase failures require reconciliation."""
        from hyperliquid.exchange import Exchange as SDKExchange

        wire = self._wire_coin(coin)
        try:
            # Match the SDK's market-order preparation without using cached marks.
            # Keep its existing default price calculation; introduce no new price limit.
            dex = wire.split(":", 1)[0] if ":" in wire else ""
            mids = self._info.all_mids(dex) if dex else self._info.all_mids()
            mid = float(Decimal(str(mids[wire])))
            if not isfinite(mid) or mid <= 0:
                raise ValueError("invalid market quote")
            price = self._exchange._slippage_price(wire, is_buy, SDKExchange.DEFAULT_SLIPPAGE, mid)
            price = self._wire_number(price)
            sz = self._wire_number(size)
        except Exception as exc:
            return OrderResult(None, "rejected", Decimal(0), None,
                               f"market preparation failed: {type(exc).__name__}")
        cloid = self.client_id(client_id)
        return self._submit(client_id, lambda: self._exchange.order(
            wire, is_buy, sz, price, {"limit": {"tif": "Ioc"}},
            reduce_only=reduce_only, cloid=cloid))

    def cancel(self, order_id: str, *, coin: str | None = None,
               client_id: str | None = None) -> dict:
        """A cancel's stable decision identity retains its target oid through reconciliation.

        Hyperliquid cancellations have no independent client-id wire field: the
        target order's immutable oid is the venue's idempotency and lookup identity.
        """
        if self._exchange is None:
            return {"status": "rejected", "error": "no signing key"}
        try:
            order_id = self._order_id(order_id)
        except ValueError:
            return {"status": "rejected", "error": "invalid venue order identity"}
        client_id = client_id or str(uuid4())
        results = self.__dict__.setdefault("_cancel_results", {})
        if client_id in results and results[client_id]["status"] != "uncertain":
            return dict(results[client_id])
        if client_id not in results:
            results[client_id] = {"status": "uncertain", "order_id": order_id}
            for name in (coin,) if coin is not None else self.coins:
                try:
                    response = self._exchange.cancel(self._wire_coin(name), int(order_id))
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
        # A repeated caller cannot redirect reconciliation to a different order.
        order_id = results[client_id]["order_id"]
        result = self.lookup(client_id, order_id=order_id)
        if result.status == "cancelled":
            results[client_id] = {"status": "cancelled", "order_id": order_id}
        elif result.status in ("filled", "rejected"):
            results[client_id] = {"status": "rejected", "order_id": order_id,
                                  "error": "order already terminal"}
        return dict(results[client_id])

    def close(self, coin: str, size: Decimal | None = None, *,
              client_id: str | None = None, market: str = "perp") -> OrderResult:
        """A reduce-only close is submitted once with its originating decision's cloid."""
        if self._exchange is None:
            return OrderResult(None, "rejected", Decimal(0), None, "no signing key")
        if client_id is not None and client_id in getattr(self, "_client_results", {}):
            result = self._client_results[client_id]
            return self.lookup(client_id) if result.status == "uncertain" else result
        if market == "spot":
            if coin not in getattr(self, "spot_pairs", ()):
                return OrderResult(None, "rejected", Decimal(0), None, "unknown spot pair")
            try:
                state = self._info.spot_user_state(self._address)
                row = next((b for b in state["balances"]
                            if b["coin"] == self._spot_tokens[coin]), None)
                total = Decimal(str(row["total"])) if row else Decimal(0)
                hold = Decimal(str(row["hold"])) if row else Decimal(0)
                if not total.is_finite() or not hold.is_finite() or not 0 <= hold <= total:
                    raise ValueError("invalid spot balance")
                balance = total - hold
                amount = balance if size is None else min(size, balance)
                if amount <= 0:
                    return OrderResult(None, "rejected", Decimal(0), None, "no spot balance")
                return self.place(Order(coin, False, amount, client_id=client_id, market="spot"))
            except Exception as exc:
                return OrderResult(None, "rejected", Decimal(0), None,
                                   f"spot close preparation failed: {type(exc).__name__}")
        return self._close_perp(coin, size, client_id or str(uuid4()))

    def _close_perp(self, coin: str, size: Decimal | None, client_id: str,
                    *, is_buy: bool | None = None) -> OrderResult:
        """One fresh position determines size without reversing an explicitly requested side."""
        try:
            rounded = None if size is None else self._round_size(coin, size)
            if rounded is not None:
                self._wire_number(rounded)
            dex = coin.split(":", 1)[0] if ":" in coin else ""
            state = (self._info.user_state(self._address, dex) if dex
                     else self._info.user_state(self._address))
            position = next((Decimal(str(row["position"]["szi"]))
                             for row in state["assetPositions"]
                             if row["position"]["coin"] == coin), Decimal(0))
            if not position.is_finite():
                raise ValueError("invalid position size")
            if not position:
                return OrderResult(None, "rejected", Decimal(0), None, "no open position")
            if is_buy is not None and (position > 0) == is_buy:
                return OrderResult(None, "rejected", Decimal(0), None, "not reducing position")
            amount = abs(position) if rounded is None else min(rounded, abs(position))
            amount = self._round_size(coin, amount)
            if amount <= 0:
                return OrderResult(None, "rejected", Decimal(0), None, "size below venue precision")
        except Exception as exc:
            return OrderResult(None, "rejected", Decimal(0), None,
                               f"close preparation failed: {type(exc).__name__}")
        client_id = client_id or str(uuid4())
        return self._market_order(coin, position < 0, amount, client_id, reduce_only=True)

    def set_leverage(self, coin: str, leverage: int, *, market: str = "perp") -> dict:
        """Return venue acknowledgement or a rejected result without propagating failures."""
        if market == "spot" or coin in getattr(self, "spot_pairs", ()):
            return {"status": "rejected", "error": "spot does not support leverage"}
        if self._exchange is None:
            return {"status": "rejected", "error": "no signing key"}
        if type(leverage) is not int or leverage < 1:
            return {"status": "rejected", "error": "leverage must be a positive integer"}
        try:
            resp = self._exchange.update_leverage(leverage, coin, is_cross=True)
            if resp.get("status") == "ok":
                # The collateral view discounts margin only at leverage the venue
                # has acknowledged; this is where it becomes acknowledged.
                self.__dict__.setdefault("_leverage", {})[coin] = Decimal(leverage)
                return {"status": "ok", "coin": coin, "leverage": leverage}
            return {"status": "rejected", "error": str(resp)}
        except Exception as exc:
            return {"status": "rejected", "error": f"{type(exc).__name__}: {exc}"}

    # ---- vaults: the venue's terms and their sources are in factorylab/world/vaults.py

    def vault_details(self, vault: str) -> dict:
        """``vaultDetails`` for one vault, with this account's own follower state."""
        import time

        from factorylab.world.vaults import details_from_wire

        body = {"type": "vaultDetails", "vaultAddress": vault,
                **({"user": self._address} if self._address else {})}
        raw = self._guarded("vault_details", lambda: self._info.post("/info", body))
        return details_from_wire(raw, self._address, time.time_ns())

    def vault_equities(self) -> dict:
        """``userVaultEquities`` and ``leadingVaults`` for this account, read together."""
        import time

        if not self._address:
            raise RuntimeError("vault_equities() needs an address or a private key")
        held = self._guarded("user_vault_equities",
                             lambda: self._info.user_vault_equities(self._address))
        leading = self._guarded("leading_vaults", lambda: self._info.post(
            "/info", {"type": "leadingVaults", "user": self._address}))
        positions = []
        for row in held if isinstance(held, list) else []:
            lock = row.get("lockedUntilTimestamp")
            positions.append({"vault": str(row["vaultAddress"]).lower(),
                              "equity_usd": Decimal(str(row["equity"])),
                              "locked_until_ns": int(lock) * NS_PER_MS
                              if isinstance(lock, int) else None})
        return {"positions": positions,
                "leading": [{"vault": str(r["address"]).lower(), "name": r.get("name")}
                            for r in (leading if isinstance(leading, list) else [])],
                "observed_at_ns": time.time_ns()}

    def vault_ledger(self, since_ns: int) -> list[dict]:
        """Vault rows of this account's non-funding ledger at or after an inclusive cursor.

        Paginated and failing closed exactly like ``funding_payments``: a stalled
        full page raises rather than silently skipping its tail.
        """
        from factorylab.world.vaults import ledger_rows

        if type(since_ns) is not int or since_ns < 0:
            raise ValueError("since_ns must be nonnegative integer nanoseconds")
        if not self._address:
            raise RuntimeError("vault_ledger() needs an address or a private key")
        start = since_ns // NS_PER_MS
        rows: dict[tuple, dict] = {}
        while True:
            page = self._guarded("non_funding_ledger", lambda start=start:
                                 self._info.user_non_funding_ledger_updates(self._address, start))
            if not isinstance(page, list):
                raise ValueError("invalid non-funding ledger response")
            for row in ledger_rows(page):
                if row["ts_ns"] >= since_ns:
                    rows[(row["hash"], row["type"], row["vault"], str(row.get("usd")),
                          str(row.get("requested")))] = row
            if len(page) < 500:
                break
            latest = max((int(r.get("time", 0)) for r in page), default=start)
            if latest <= start:
                raise ValueError("non-funding ledger pagination stalled at a full timestamp")
            start = latest
        return sorted(rows.values(), key=lambda r: (r["ts_ns"], r["hash"], r["type"]))

    def vault_lookup(self, client_id: str, *, operation: str, args: dict, since_ns: int,
                     claimed: frozenset = frozenset(), position: int = 0,
                     peers: int = 1) -> dict:
        """Resolve a vault write by its own ledger row; never submits anything."""
        from factorylab.world.vaults import match_intent

        try:
            return match_intent(self.vault_ledger(since_ns), operation, args, self._address,
                                claimed=claimed, position=position, peers=peers)
        except Exception as exc:
            return {"status": "uncertain", "error": f"lookup exception: {type(exc).__name__}"}

    def _vault_submit(self, client_id: str | None, submit) -> dict:
        """Submit once per identity. Neither vault action carries a client order id, so
        a seen identity is answered from what it received, never sent again."""
        results = self.__dict__.setdefault("_vault_results", {})
        if client_id is not None and client_id in results:
            return dict(results[client_id])
        if self._exchange is None:
            return {"status": "rejected", "error": "no signing key"}
        if client_id is not None:
            results[client_id] = {"status": "uncertain", "error": "submission unacknowledged"}
        try:
            resp = submit()
            if isinstance(resp, dict) and resp.get("status") == "ok":
                response = resp.get("response") or {}
                result = {"status": "ok"}
                if isinstance(response, dict) and isinstance(response.get("data"), str):
                    result["vault"] = response["data"].lower()
            elif isinstance(resp, dict) and resp.get("status") == "err":
                result = {"status": "rejected", "error": str(resp.get("response"))[:300]}
            else:
                result = {"status": "uncertain", "error": "unknown response shape"}
        except Exception as exc:
            # Exception messages may carry credentials or signed request bodies.
            result = {"status": "uncertain", "error": f"submit exception: {type(exc).__name__}"}
        if client_id is not None:
            results[client_id] = result
        return dict(result)

    def vault_create(self, name: str, description: str, usd: Decimal, *,
                     client_id: str | None = None) -> dict:
        """``createVault``, signed as an L1 action. The SDK has no helper for it; the
        action's fields are the TS SDK's schema, in its order (unverified on the wire)."""
        from factorylab.world.vaults import check_create, exact_micro

        reason = check_create(name, description, usd)
        if reason is not None:
            return {"status": "rejected", "error": reason}
        micro = exact_micro(usd)
        if micro is None:
            return {"status": "rejected", "error": "usd is finer than one micro-USD"}

        def submit():
            from hyperliquid.utils.constants import MAINNET_API_URL
            from hyperliquid.utils.signing import get_timestamp_ms, sign_l1_action

            ex = self._exchange
            nonce = get_timestamp_ms()
            action = {"type": "createVault", "name": name, "description": description,
                      "initialUsd": micro, "nonce": nonce}
            signature = sign_l1_action(ex.wallet, action, None, nonce, ex.expires_after,
                                       ex.base_url == MAINNET_API_URL)
            return ex._post_action(action, signature, nonce)

        result = self._vault_submit(client_id, submit)
        return {**result, "usd": str(usd)} if result["status"] == "ok" else result

    def vault_transfer(self, vault: str, is_deposit: bool, usd: Decimal, *,
                       client_id: str | None = None) -> dict:
        """``vaultTransfer`` through the SDK; ``usd`` goes on the wire as micro-USDC."""
        from factorylab.world.vaults import exact_micro

        micro = exact_micro(usd)
        if micro is None:
            return {"status": "rejected", "error": "usd is finer than one micro-USD"}
        if micro <= 0:
            return {"status": "rejected", "error": "usd must be positive"}
        result = self._vault_submit(client_id, lambda: self._exchange.vault_usd_transfer(
            vault, is_deposit, micro))
        return ({**result, "vault": str(vault).lower(), "usd": str(usd)}
                if result["status"] == "ok" else result)

    # ---- helpers

    @staticmethod
    def _order_id(value: Any) -> str:
        """Only a nonzero integer venue identity can acknowledge an order."""
        if (type(value) not in (int, str) or not str(value).isascii()
                or not str(value).isdecimal() or int(value) <= 0):
            raise ValueError("invalid venue order identity")
        return str(value)

    @staticmethod
    def _wire_number(value: Decimal | float) -> float:
        """Only positive finite quantities representable by the SDK reach dispatch."""
        from hyperliquid.utils.signing import float_to_wire

        number = float(value)
        if not isfinite(number) or number <= 0 or Decimal(float_to_wire(number)) <= 0:
            raise ValueError("invalid SDK wire number")
        return number

    def _round_price(self, coin: str, price: Decimal, is_buy: bool, *, spot: bool) -> Decimal:
        """A limit price Hyperliquid will accept, never more aggressive than the one asked.

        The venue's rule: at most five significant figures -- an integer price is
        always allowed whatever its figures -- and at most ``6 - szDecimals``
        decimals for a perp, ``8 - szDecimals`` for spot. A price that breaks it is
        rejected outright, so it is rounded here, toward the passive side: a buy
        down, a sell up. Guarantees a positive result or raises ``ValueError``.
        """
        from decimal import ROUND_UP

        if not price.is_finite() or price <= 0:
            raise ValueError("limit price must be finite and positive")
        decimals = (8 if spot else 6) - self._sz_decimals.get(coin, 4)
        figures = Decimal(1).scaleb(price.adjusted() - 4)  # the fifth significant figure
        quantum = max(min(figures, Decimal(1)), Decimal(1).scaleb(-max(decimals, 0)))
        rounded = price.quantize(quantum, rounding=ROUND_DOWN if is_buy else ROUND_UP)
        if rounded <= 0:
            raise ValueError("limit price below the venue's price precision")
        return rounded

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


def live_exchange(spec: Any, venue_class: Any = None, *,
                  launch_nonce: str | None = None) -> HyperliquidExchange:
    """The single place a manifest becomes a live venue, so no caller reads a partial world.

    The runtime and the wake both construct through here; a field added to
    ``ExchangeSpec`` reaches every call site at once. ``venue_class`` lets a
    caller bind the class from its own module namespace. ``launch_nonce`` is not
    a manifest field: it is drawn once per launch and restored by resume, so the
    adapter itself stays a deterministic function of the identity it is given.
    """
    exchange = (venue_class or HyperliquidExchange)(
        mainnet=spec.mainnet, coins=spec.coins, spot_pairs=spec.spot_pairs,
    )
    if getattr(spec, "client_namespace", None) is not None:
        exchange._client_namespace = spec.client_namespace
    if launch_nonce is not None:
        exchange._launch_nonce = launch_nonce
    return exchange


def bind_launch_nonce(exchange: Any, launch_nonce: str | None) -> None:
    """Carry one launch's venue identity onto the adapter that derives client order IDs.

    A checkpoint written before launch nonces existed restores ``None``, which
    removes the attribute again so the resumed world reproduces exactly the
    client order IDs it originally submitted. Adapters without venue identities
    (the deterministic fake) are left untouched.
    """
    target = getattr(exchange, "target", exchange)
    if not hasattr(target, "client_id"):
        return
    if launch_nonce is None:
        target.__dict__.pop("_launch_nonce", None)
    else:
        target.__dict__["_launch_nonce"] = launch_nonce
