"""Exchange adapters: the protocol, a deterministic fake, and Hyperliquid.

Amounts on this boundary are ``Decimal`` in the venue's own units. Conversion
to wallet micro-USD happens in the runtime using the venue's reported mark, so
the exchange adapter never touches the wallet.
"""

from __future__ import annotations

import os
import random
from collections.abc import Iterator
from dataclasses import dataclass, field
from decimal import ROUND_DOWN, Decimal
from enum import StrEnum
from typing import Any, Protocol

from factorylab.world.events import WorldEvent, WorldEventKind

NS_PER_MS = 1_000_000
NS_PER_HOUR = 3_600 * 1_000_000_000


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
        if self.size <= 0:
            raise ValueError("order size must be positive")
        if self.kind is OrderKind.LIMIT and self.limit_px is None:
            raise ValueError("limit orders need limit_px")


@dataclass(frozen=True)
class OrderResult:
    order_id: str | None
    status: str  # "filled" | "resting" | "rejected"
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


@dataclass(frozen=True)
class FundingEvent:
    coin: str
    rate: Decimal  # per funding interval, signed
    premium: Decimal | None
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
    def account(self) -> AccountState: ...
    def place(self, order: Order) -> OrderResult: ...
    def cancel(self, order_id: str) -> None: ...
    def fills(self, since_ns: int) -> list[Fill]: ...


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
        self._next_oid = 1
        self._last_funding_ns = 0
        self._pending_events: list[WorldEvent] = []

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

    def account(self) -> AccountState:
        unrealized = Decimal(0)
        notional = Decimal(0)
        for p in self._positions.values():
            mid = self._mids[p.coin]
            unrealized += (mid - p.entry_px) * p.size
            notional += abs(p.size) * mid
        return AccountState(
            equity_usd=self._cash + unrealized,
            cash_usd=self._cash,
            positions=tuple(self._positions.values()),
            margin_used_usd=notional / self.max_leverage if notional else Decimal(0),
        )

    def place(self, order: Order) -> OrderResult:
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

    def cancel(self, order_id: str) -> None:
        self._resting.pop(order_id, None)

    def fills(self, since_ns: int) -> list[Fill]:
        return [f for f in self._fills if f.ts_ns >= since_ns]

    def drain_events(self) -> list[WorldEvent]:
        """Return and clear events produced by ``place`` (fills, rejections)."""
        out, self._pending_events = self._pending_events, []
        return out

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
        notional = abs(new_size) * px
        for p in self._positions.values():
            if p.coin != coin:
                notional += abs(p.size) * self._mids[p.coin]
        return notional <= self.account().equity_usd * self.max_leverage

    def _liquidate_if_needed(self) -> list[WorldEvent]:
        """Force-close every position at mid when equity falls below maintenance margin.

        Maintenance margin is ``maintenance_fraction`` of initial margin
        (notional / max_leverage). The realised loss lands in cash like any
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
            pos = self._positions.get(coin)
            paid = Decimal(0)
            if pos is not None:
                # longs pay when rate is positive
                paid = pos.size * self._mids[coin] * self.funding_rate
                self._cash -= paid
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

    # ---- reads

    def mids(self) -> dict[str, Decimal]:
        raw = self._info.all_mids()
        return {c: Decimal(str(raw[c])) for c in self.coins if c in raw}

    def funding(self) -> list[FundingEvent]:
        import time

        meta, ctxs = self._info.meta_and_asset_ctxs()
        names = [a["name"] for a in meta["universe"]]
        now_ns = time.time_ns()
        out: list[FundingEvent] = []
        for name, ctx in zip(names, ctxs, strict=False):
            if name in self.coins:
                out.append(
                    FundingEvent(
                        name,
                        Decimal(str(ctx["funding"])),
                        Decimal(str(ctx["premium"])) if ctx.get("premium") is not None else None,
                        now_ns,
                    )
                )
        return out

    def account(self) -> AccountState:
        if not self._address:
            raise RuntimeError("account() needs an address or a private key")
        st = self._info.user_state(self._address)
        summary = st["marginSummary"]
        positions: list[Position] = []
        for ap in st.get("assetPositions", []):
            p = ap["position"]
            size = Decimal(str(p["szi"]))
            if size == 0:
                continue
            entry = Decimal(str(p["entryPx"])) if p.get("entryPx") else Decimal(0)
            positions.append(Position(p["coin"], size, entry))
        return AccountState(
            equity_usd=Decimal(str(summary["accountValue"])),
            cash_usd=Decimal(str(st.get("withdrawable", summary["accountValue"]))),
            positions=tuple(positions),
            margin_used_usd=Decimal(str(summary["totalMarginUsed"])),
        )

    def fills(self, since_ns: int) -> list[Fill]:
        if not self._address:
            raise RuntimeError("fills() needs an address or a private key")
        raw = self._info.user_fills_by_time(self._address, since_ns // NS_PER_MS)
        out: list[Fill] = []
        for f in raw:
            out.append(
                Fill(
                    order_id=str(f["oid"]),
                    coin=f["coin"],
                    is_buy=f["side"] == "B",
                    size=Decimal(str(f["sz"])),
                    px=Decimal(str(f["px"])),
                    fee=Decimal(str(f.get("fee", "0"))),
                    ts_ns=int(f["time"]) * NS_PER_MS,
                )
            )
        return out

    # ---- writes

    def place(self, order: Order) -> OrderResult:
        if self._exchange is None:
            return OrderResult(None, "rejected", Decimal(0), None, "no signing key")
        sz = float(self._round_size(order.coin, order.size))
        try:
            if order.kind is OrderKind.MARKET:
                resp = self._exchange.market_open(order.coin, order.is_buy, sz)
            else:
                assert order.limit_px is not None
                resp = self._exchange.order(
                    order.coin,
                    order.is_buy,
                    sz,
                    float(order.limit_px),
                    {"limit": {"tif": "Gtc"}},
                    reduce_only=order.reduce_only,
                )
        except Exception as exc:  # network or signing failure is a rejection, not a crash
            return OrderResult(None, "rejected", Decimal(0), None, f"{type(exc).__name__}: {exc}")
        return self._parse_order_response(resp)

    def cancel(self, order_id: str) -> None:
        if self._exchange is None:
            return
        # Hyperliquid cancels need the coin; callers track it. We try each coin.
        for coin in self.coins:
            try:
                self._exchange.cancel(coin, int(order_id))
                return
            except Exception:
                continue

    # ---- helpers

    def _round_size(self, coin: str, size: Decimal) -> Decimal:
        d = self._sz_decimals.get(coin, 4)
        return size.quantize(Decimal(1).scaleb(-d), rounding=ROUND_DOWN)

    @staticmethod
    def _parse_order_response(resp: Any) -> OrderResult:
        try:
            if resp.get("status") != "ok":
                return OrderResult(None, "rejected", Decimal(0), None, str(resp))
            statuses = resp["response"]["data"]["statuses"]
            st = statuses[0]
            if "filled" in st:
                f = st["filled"]
                return OrderResult(
                    str(f["oid"]), "filled", Decimal(str(f["totalSz"])), Decimal(str(f["avgPx"]))
                )
            if "resting" in st:
                return OrderResult(str(st["resting"]["oid"]), "resting", Decimal(0), None)
            if "error" in st:
                return OrderResult(None, "rejected", Decimal(0), None, str(st["error"]))
        except (KeyError, IndexError, TypeError, AttributeError) as exc:
            return OrderResult(None, "rejected", Decimal(0), None, f"unparseable: {exc}")
        return OrderResult(None, "rejected", Decimal(0), None, "unknown response shape")


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
