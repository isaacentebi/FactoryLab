"""The one door: every answer the Polymarket venue sends the pot, parsed strictly.

Chapter II §II.b (physics is enforced, the hard cast): the pot's books are only as
true as what they read, and the venue may be buggy, malformed, lagging or
contradictory (the threat model, ``docs/manifest.md``). So every answer the order path
reads (a POST acknowledgement or refusal, an order read-back, a cancel answer, the open
orders, the trades with their maker legs, the positions, the balance, a market and a
book) is parsed here, against its documented shape, into a record of exact types:

* the fields the pot reads are present and of their documented type; a field the pot
  does not read may be absent or extra, and is ignored;
* this world's own order id is ``0x`` and 64 hex digits, a hash it signed; another
  party's id is any non-empty string (it never touches the books); each is lower-cased
  here, once, and everything downstream compares lower-case values;
* a price is strictly inside (0, 1), a size positive, an amount a finite decimal (never
  a float: the transport parses numbers as ``Decimal``, and a bool is never a number);
* a status is one of its documented values; a row that is not an object is malformed;
* a leg or read-back of one of this world's orders is fully determined by the order it
  signed, but for its size: its token, BUY, and its signed limit price exactly (a maker
  executes at its own price); its size positive and no more than the signed size.

An answer that does not conform is ``Malformed`` as a whole: a read is then unread (the
caller's cursor does not move) and a placement's acknowledgement uncertain (its
commitment kept, its hash a cancellation target). Nothing downstream reads raw venue
JSON. The one reader that walks raw JSON is ``scan_contradictions``, which runs first,
never raises, and keeps what it finds whatever the strict parse then decides.

Sources: https://docs.polymarket.com/resources/error-codes,
https://docs.polymarket.com/trading/place-orders,
https://docs.polymarket.com/trading/manage-orders,
https://docs.polymarket.com/api-spec/clob-openapi.yaml,
https://docs.polymarket.com/market-data/market-details.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any

#: Six-decimal units of pUSD and of outcome tokens.
UNIT = 10 ** 6
#: The ticks Polymarket publishes (market-details).
TICKS = frozenset({"0.1", "0.01", "0.005", "0.0025", "0.001", "0.0001"})
#: An order's CLOB status, read as the runtime's (``GET /data/order/{hash}``).
ORDER_STATUS = {"LIVE": "resting", "MATCHED": "filled", "CANCELED": "cancelled",
                "CANCELED_MARKET_RESOLVED": "cancelled", "INVALID": "rejected"}
#: A trade's settlement status (``GET /data/trades``): only CONFIRMED is final, FAILED
#: never settles.
TRADE_STATUS = frozenset({"MATCHED", "MINED", "CONFIRMED", "RETRYING", "FAILED"})
#: What a ``POST /order`` acknowledgement's ``status`` may be (place-orders).
ACK_STATUS = frozenset({"live", "matched", "delayed", "unmatched"})

_HASH = re.compile(r"0x[0-9a-fA-F]{64}")
_ADDRESS = re.compile(r"0x[0-9a-fA-F]{40}")
_TOKEN = re.compile(r"[0-9]{1,100}")
_INTEGER = re.compile(r"[0-9]{1,30}")
_ID = r"(?P<id>0x[0-9a-fA-F]{64})"


class Malformed(ValueError):
    """A venue answer that does not conform to its documented shape. Its text names the
    field, never the venue's own words."""


# --- fields -----------------------------------------------------------------------------


def obj(value: Any, what: str) -> dict:
    if not isinstance(value, dict):
        raise Malformed(f"{what} is not an object")
    return value


def field(row: dict, key: str, what: str) -> Any:
    if key not in row:
        raise Malformed(f"{what} has no {key}")
    return row[key]


def order_hash(value: Any, what: str = "order hash") -> str:
    """``0x`` and 64 hex digits, lower-cased."""
    if not isinstance(value, str) or not _HASH.fullmatch(value):
        raise Malformed(f"{what} is not an order hash")
    return value.lower()


def other_id(value: Any, what: str) -> str:
    """Another party's order id: any non-empty string, lower-cased. It never touches
    this world's books; only this world's own ids must be the hashes it signed
    (architect's decision on #177: the documented example's taker id is 40 digits)."""
    if not isinstance(value, str) or not value:
        raise Malformed(f"{what} is not an id")
    return value.lower()


def address(value: Any, what: str = "address") -> str:
    """``0x`` and 40 hex digits, lower-cased."""
    if not isinstance(value, str) or not _ADDRESS.fullmatch(value):
        raise Malformed(f"{what} is not an address")
    return value.lower()


def token_id(value: Any, what: str = "token id") -> str:
    """An outcome token id: a decimal integer, as a string."""
    if not isinstance(value, str) or not _TOKEN.fullmatch(value):
        raise Malformed(f"{what} is not a token id")
    return value


def number(value: Any, what: str) -> Decimal:
    """A finite decimal from a string, an integer or a ``Decimal``; never a bool or a
    float."""
    if isinstance(value, bool) or not isinstance(value, (str, int, Decimal)):
        raise Malformed(f"{what} is not a number")
    try:
        parsed = Decimal(value)
    except (InvalidOperation, ValueError):
        raise Malformed(f"{what} is not a number") from None
    if not parsed.is_finite():
        raise Malformed(f"{what} is not finite")
    return parsed


def price(value: Any, what: str = "price") -> Decimal:
    """A price strictly inside (0, 1)."""
    parsed = number(value, what)
    if not 0 < parsed < 1:
        raise Malformed(f"{what} is outside (0, 1)")
    return parsed


def positive(value: Any, what: str) -> Decimal:
    parsed = number(value, what)
    if parsed <= 0:
        raise Malformed(f"{what} is not positive")
    return parsed


def non_negative(value: Any, what: str) -> Decimal:
    parsed = number(value, what)
    if parsed < 0:
        raise Malformed(f"{what} is negative")
    return parsed


def boolean(value: Any, what: str) -> bool:
    if not isinstance(value, bool):
        raise Malformed(f"{what} is not a bool")
    return value


def text(value: Any, what: str) -> str:
    if not isinstance(value, str):
        raise Malformed(f"{what} is not text")
    return value


def rows(value: Any, what: str) -> list:
    if not isinstance(value, list):
        raise Malformed(f"{what} is not a list")
    return value


def cursor(value: Any) -> str:
    if not isinstance(value, str) or not value:
        raise Malformed("next_cursor is not a cursor")
    return value


# --- the submission ---------------------------------------------------------------------


#: The documented refusals of a submission, the only answers that prove an order never
#: existed (resources/error-codes, read 2026-09-29): (HTTP status, the error text, the
#: local reason). An id in the text must be this order's.
REFUSALS = (
    (400, re.compile(r"invalid post-only order: order crosses book"),
     "post-only order crosses the book"),
    (400, re.compile(rf"order {_ID} crosses the book"), "post-only order crosses the book"),
    (400, re.compile(rf"order {_ID} is invalid\. Price \([^()]*\) breaks minimum tick size "
                     r"rule: [0-9.]+"), "price breaks the market's tick"),
    (400, re.compile(rf"order {_ID} is invalid\. Size \([^()]*\) lower than the minimum: "
                     r"[0-9.]+"), "size below the market's minimum"),
    (400, re.compile(r"not enough balance / allowance"), "not enough balance / allowance"),
    (400, re.compile(r"invalid expiration"), "invalid expiration"),
    (400, re.compile(r"the market is not yet ready to process new orders"),
     "the market is not ready for orders"),
    (400, re.compile(r"Invalid order payload"), "the venue refused the order payload"),
    (400, re.compile(r"the order owner has to be the owner of the API KEY"),
     "the order's owner is not the API key's"),
    (400, re.compile(r"the order signer address has to be the address of the API KEY"),
     "the order's signer is not the API key's"),
    (400, re.compile(r"'0x[0-9a-fA-F]{40}' address banned"), "the address is banned"),
    (400, re.compile(r"'0x[0-9a-fA-F]{40}' address in closed only mode"),
     "the address is in closed-only mode"),
    (401, re.compile(r"Unauthorized/Invalid api key"), "the API key was refused"),
    (401, re.compile(r"Invalid L1 Request headers"), "the L1 headers were refused"),
    (429, re.compile(r"Too Many Requests"), "the venue's rate limit"),
)


def refusal(status: int, body: Any, order_id: str) -> str | None:
    """The local reason a submission was refused outright, or None: the answer is an
    HTTP 4xx whose body is exactly ``{"error": text}`` with ``text`` one of the
    documented refusals (``REFUSALS``), naming no other order and never a duplicate.
    Anything else, an extra field, a duplicate, an ``orderID``, a ``success`` or a
    ``status`` included, proves nothing: the order is uncertain (architect's decision
    on Sol's round-7 review of #177)."""
    if not 400 <= status < 500 or not isinstance(body, dict) or set(body) != {"error"}:
        return None
    stated = body["error"]
    if not isinstance(stated, str) or "duplicat" in stated.lower():
        return None
    for code, pattern, reason in REFUSALS:
        found = pattern.fullmatch(stated)
        if code == status and found:
            named = found.groupdict().get("id")
            if named is not None and named.lower() != order_id.lower():
                return None
            return reason
    return None


def ack(answer: Any, order_id: str) -> dict[str, Any]:
    """A 2xx ``POST /order`` acknowledgement as the runtime reads it: ``resting`` only
    for ``success: true``, no error, this order's hash and ``status: live``; any other
    answer, a ``success: false`` included, is ``uncertain`` and read back by the hash,
    never taken as a rejection (Sol P1, round 7)."""
    def uncertain(why: str) -> dict[str, Any]:
        return {"order_id": order_id, "status": "uncertain", "error": why}

    try:
        row = obj(answer, "order answer")
        if boolean(field(row, "success", "order answer"), "success") is not True:
            return uncertain("order answer does not state success")
        if order_hash(field(row, "orderID", "order answer"), "orderID") != order_id.lower():
            return uncertain("venue answered another order id")
        if text(row.get("errorMsg", ""), "errorMsg"):
            return uncertain("order answer states success and an error")
        status = text(field(row, "status", "order answer"), "status")
        if status not in ACK_STATUS:
            raise Malformed("order answer status is not documented")
    except Malformed as exc:
        return uncertain(str(exc))
    if status == "live":
        return {"order_id": order_id, "status": "resting", "filled_size": "0",
                "avg_px": None, "error": None}
    # "matched" and "delayed" are read back by the hash: an ACK is never a fill.
    return uncertain(f"order status {status} is read back by its hash")


def cancel_answer(answer: Any, order_id: str) -> tuple[str, str | None]:
    """A ``DELETE /order`` answer ``{canceled, not_canceled}`` for one order hash:
    ``("cancelled", None)``, ``("not_canceled", why)`` or ``("unknown", None)`` when it
    names both or neither; raises ``Malformed`` otherwise."""
    row = obj(answer, "cancel answer")
    cancelled = {other_id(h, "canceled") for h in rows(
        field(row, "canceled", "cancel answer"), "canceled")}
    refused = obj(field(row, "not_canceled", "cancel answer"), "not_canceled")
    refused = {other_id(h, "not_canceled"): v for h, v in refused.items()}
    ours = order_id.lower()
    if (ours in cancelled) == (ours in refused):
        return "unknown", None
    if ours in cancelled:
        return "cancelled", None
    why = refused[ours]
    return "not_canceled", ("order is not resting" if not isinstance(why, str) else
                            "order already matched" if "match" in why.lower() else
                            "order is not resting")


# --- orders -----------------------------------------------------------------------------


@dataclass(frozen=True)
class Signed:
    """What this world signed for one order hash: its token, size and limit price."""

    token_id: str
    size: Decimal
    limit: Decimal


@dataclass(frozen=True)
class Order:
    """One CLOB order (``OpenOrder``): lower-case hash, the runtime's status, BUY or
    SELL, its price, size and what it matched (``0 <= matched <= size``)."""

    order_id: str
    status: str
    token_id: str
    side: str
    price: Decimal
    size: Decimal
    matched: Decimal


def order(answer: Any, *, expect: str | None = None, signed: Signed | None = None) -> Order:
    """One order read back. ``expect`` is the hash asked for; ``signed`` what this world
    signed for it, which the answer must be exactly: a BUY of that token and size at
    that limit (Sol P1, rounds 6 and 8: an answer is bound to the signed order, never
    checked against itself)."""
    row = obj(answer, "order")
    stated = field(row, "id", "order")
    # An order asked for by its hash is this world's: it must be that hash; a listed
    # order may be anyone's.
    found = order_hash(stated, "order id") if expect is not None else other_id(
        stated, "order id")
    if expect is not None and found != expect.lower():
        raise Malformed("order read back is another order")
    status = ORDER_STATUS.get(text(field(row, "status", "order"), "order status"))
    if status is None:
        raise Malformed("order status is not documented")
    side = text(field(row, "side", "order"), "side")
    if side not in ("BUY", "SELL"):
        raise Malformed("order side is not documented")
    record = Order(order_id=found, status=status,
                   token_id=token_id(field(row, "asset_id", "order"), "asset_id"),
                   side=side.lower(), price=price(field(row, "price", "order")),
                   size=positive(field(row, "original_size", "order"), "original_size"),
                   matched=non_negative(field(row, "size_matched", "order"), "size_matched"))
    if record.matched > record.size or (status == "filled" and record.matched != record.size):
        raise Malformed("order matched size contradicts its size")
    if signed is not None and (
            record.side != "buy" or record.token_id != signed.token_id
            or record.size != signed.size or record.price != signed.limit):
        raise Malformed("order contradicts the order this world signed")
    return record


def orders_page(answer: Any) -> tuple[list[Order], str]:
    """A ``GET /data/orders`` page: its orders and its ``next_cursor``."""
    page = obj(answer, "orders page")
    listed = [order(row) for row in rows(field(page, "data", "orders page"), "data")]
    return listed, cursor(field(page, "next_cursor", "orders page"))


# --- trades -----------------------------------------------------------------------------


@dataclass(frozen=True)
class Leg:
    """One leg of one of this world's orders in a trade."""

    order_id: str
    size: Decimal
    price: Decimal
    taker: bool


@dataclass(frozen=True)
class Trade:
    """One trade that has a leg of this world's: its id, settlement status, match time
    (seconds, and nanoseconds where stated) and this world's legs."""

    trade_id: str
    status: str
    at: int
    instant: int
    legs: tuple[Leg, ...]


def trades_page(answer: Any, ours: dict[str, Signed]) -> tuple[list[Trade], str]:
    """A ``GET /data/trades`` page: the trades with a leg of one of ``ours`` (hash ->
    what this world signed), and its ``next_cursor``. Every row and every maker leg is
    parsed, whoever's it is; another party's order id is any non-empty string, and a
    leg is this world's only when its id is, in any case, a hash this world signed.

    A leg of this world's is fully determined by its signed order but for its size
    (architect's rule on Sol's round-8 review of #177): its token is the signed token,
    it is a BUY at exactly the signed limit (a maker executes at its own price), and its
    size is positive and no more than the signed size. Anything else is malformed,
    never booked (Sol: a leg reported under another token's hash, or below its limit,
    invented money; a size in base units, 10^6 times the shares, is malformed too).
    The cumulative bound, what is booked plus a new leg, is kept where legs are booked
    (``LivePolymarket._fills``)."""
    page = obj(answer, "trades page")
    own = {h.lower(): signed for h, signed in ours.items()}
    trades = []
    for raw in rows(field(page, "data", "trades page"), "data"):
        row = obj(raw, "trade")
        trade_id = text(field(row, "id", "trade"), "trade id")
        if not trade_id:
            raise Malformed("trade id is empty")
        status = text(field(row, "status", "trade"), "trade status").removeprefix(
            "TRADE_STATUS_")
        if status not in TRADE_STATUS:
            raise Malformed("trade status is not documented")
        at = _seconds(field(row, "match_time", "trade"), "match_time")
        nano = row.get("match_time_nano")
        instant = at * 1_000_000_000 if nano in (None, "") else _seconds(nano, "match_time_nano")
        legs = []
        taker = other_id(field(row, "taker_order_id", "trade"), "taker_order_id")
        side = text(field(row, "side", "trade"), "trade side")
        size = positive(field(row, "size", "trade"), "trade size")
        paid = price(field(row, "price", "trade"), "trade price")
        if side not in ("BUY", "SELL"):
            raise Malformed("trade side is not documented")
        if taker in own:
            asset = token_id(field(row, "asset_id", "trade"), "trade asset_id")
            legs.append(_leg(taker, asset, side, size, paid, own[taker], taker=True))
        for maker_raw in rows(field(row, "maker_orders", "trade"), "maker_orders"):
            maker = obj(maker_raw, "maker leg")
            maker_id = other_id(field(maker, "order_id", "maker leg"), "maker order_id")
            maker_side = text(field(maker, "side", "maker leg"), "maker side")
            if maker_side not in ("BUY", "SELL"):
                raise Malformed("maker side is not documented")
            matched = positive(field(maker, "matched_amount", "maker leg"), "matched_amount")
            maker_price = price(field(maker, "price", "maker leg"), "maker price")
            if maker_id in own:
                asset = token_id(field(maker, "asset_id", "maker leg"), "maker asset_id")
                legs.append(_leg(maker_id, asset, maker_side, matched, maker_price,
                                 own[maker_id], taker=False))
        if legs:
            trades.append(Trade(trade_id, status, at, instant, tuple(legs)))
    return trades, cursor(field(page, "next_cursor", "trades page"))


def _seconds(value: Any, what: str) -> int:
    if isinstance(value, bool):
        raise Malformed(f"{what} is not a time")
    if isinstance(value, int) and value >= 0:
        return value
    if isinstance(value, str) and _INTEGER.fullmatch(value):
        return int(value)
    raise Malformed(f"{what} is not a time")


def _leg(order_id: str, asset: str, side: str, size: Decimal, paid: Decimal,
         signed: Signed, *, taker: bool) -> Leg:
    if (asset != signed.token_id or side != "BUY" or paid != signed.limit
            or size > signed.size):
        raise Malformed("a leg contradicts the order this world signed")
    return Leg(order_id, size, paid, taker)


# --- the pot ----------------------------------------------------------------------------


def balance(answer: Any) -> Decimal:
    """``GET /balance-allowance``: the collateral balance in pUSD (six-decimal units)."""
    stated = field(obj(answer, "balance answer"), "balance", "balance answer")
    if isinstance(stated, bool) or not (
            (isinstance(stated, int) and stated >= 0)
            or (isinstance(stated, str) and _INTEGER.fullmatch(stated))):
        raise Malformed("balance is not a whole number of units")
    return Decimal(int(stated)) / UNIT


@dataclass(frozen=True)
class Position:
    """One Data API position: token, size, average price, outcome index and label."""

    token_id: str
    size: Decimal
    avg_px: Decimal
    outcome_index: int
    outcome_name: str | None


def positions_page(answer: Any) -> list[Position]:
    """A Data API ``/positions`` page (a list)."""
    found = []
    for raw in rows(answer, "positions page"):
        row = obj(raw, "position")
        index = field(row, "outcomeIndex", "position")
        if isinstance(index, bool) or not isinstance(index, int) or index < 0:
            raise Malformed("outcomeIndex is not an index")
        avg = non_negative(field(row, "avgPrice", "position"), "avgPrice")
        if avg > 1:
            raise Malformed("avgPrice is above 1")
        name = row.get("outcome")
        if name is not None and not isinstance(name, str):
            raise Malformed("outcome is not text")
        found.append(Position(token_id(field(row, "asset", "position"), "asset"),
                              non_negative(field(row, "size", "position"), "size"),
                              avg, index, name))
    return found


# --- the market and its book --------------------------------------------------------------


def market(answer: Any) -> dict[str, Any]:
    """A Gamma market the pot writes on or settles against: the fields the order path
    reads checked, then parsed as the tools publish it (``market_detail``)."""
    from factorylab.world.polymarket import market_detail

    row = obj(answer, "market")
    market_id = field(row, "id", "market")
    if isinstance(market_id, bool) or not isinstance(market_id, (str, int)) or market_id == "":
        raise Malformed("market id is not an id")
    names = _listed(field(row, "outcomes", "market"), "outcomes")
    tokens = _listed(field(row, "clobTokenIds", "market"), "clobTokenIds")
    prices = _listed(field(row, "outcomePrices", "market"), "outcomePrices")
    if not len(names) == len(tokens) == len(prices) == 2:
        raise Malformed("market is not a two-outcome market")
    for name in names:
        text(name, "outcome")
    for token in tokens:
        token_id(token, "clobTokenId")
    for stated in prices:
        paid = number(stated, "outcomePrice")
        if not 0 <= paid <= 1:
            raise Malformed("outcomePrice is outside [0, 1]")
    for key in ("active", "closed", "acceptingOrders", "enableOrderBook", "negRisk"):
        boolean(field(row, key, "market"), key)
    tick = number(field(row, "orderPriceMinTickSize", "market"), "orderPriceMinTickSize")
    if format(tick.normalize(), "f") not in TICKS:
        raise Malformed("tick is not one Polymarket publishes")
    positive(field(row, "orderMinSize", "market"), "orderMinSize")
    status = row.get("umaResolutionStatus")
    if status is not None and not isinstance(status, str):
        raise Malformed("umaResolutionStatus is not text")
    detail = market_detail(row)
    if detail is None:
        raise Malformed("market has no tradable shape")
    return detail


def markets(answer: Any) -> list[dict[str, Any]]:
    """A Gamma ``/markets`` listing: every market in it, each parsed by ``market``."""
    return [market(row) for row in rows(answer, "markets")]


def _listed(value: Any, what: str) -> list:
    """Gamma encodes a market's lists as JSON text."""
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            raise Malformed(f"{what} is not a list") from None
    return rows(value, what)


def book(answer: Any, depth: int) -> dict[str, Any]:
    """A CLOB ``/book`` summary for a held token's mark: every level a price inside
    (0, 1) and a positive size, then best first on both sides (``parse_book``)."""
    from factorylab.world.polymarket import parse_book

    row = obj(answer, "book")
    for side in ("bids", "asks"):
        for level_raw in rows(field(row, side, "book"), side):
            level = obj(level_raw, "book level")
            price(field(level, "price", "book level"), "book price")
            positive(field(level, "size", "book level"), "book size")
    return parse_book(row, depth)


# --- the contradiction scan: the one raw reader -------------------------------------------


def scan_contradictions(answer: Any, ours: Any) -> dict[str, str]:
    """Every leg of this world's in a raw ``/data/trades`` page (or its ``data``) that
    contradicts the post-only venue: leg -> why. Walks the raw JSON defensively and
    never raises: a non-object is skipped, a hash is matched whatever its case, and a
    leg is contradicting when this world's order is the trade's taker, or when any key
    of its maker leg whose name contains "fee" (any case: ``fee_rate_bps``,
    ``feeRateBps``, ``fees``...) states a charge (``_charged``; architect's decisions on
    Sol's round-7 review of #177). It runs before the strict parse, and
    what it finds is kept whatever that parse decides."""
    found: dict[str, str] = {}
    try:
        own = {str(h).lower() for h in ours}
        data = answer.get("data") if isinstance(answer, dict) else answer
        for row in data if isinstance(data, list) else []:
            if not isinstance(row, dict):
                continue
            trade = str(row.get("id"))
            taker = row.get("taker_order_id")
            if isinstance(taker, str) and taker.lower() in own:
                found[f"{trade}:{taker.lower()}:1"] = (
                    "the venue reports this post-only order as a taker")
            makers = row.get("maker_orders")
            for maker in makers if isinstance(makers, list) else []:
                if not isinstance(maker, dict):
                    continue
                order_id = maker.get("order_id")
                if isinstance(order_id, str) and order_id.lower() in own and _charged(maker):
                    found[f"{trade}:{order_id.lower()}:0"] = (
                        "the venue reports a fee on this maker fill")
        return found
    except Exception:  # noqa: BLE001 - the scan never raises; what it found is kept
        return found


def _charged(leg: dict) -> bool:
    """Whether any fee-named key of a leg states a charge.

    A key is uncharged when its value is null or a finite decimal equal to zero (``0``,
    ``0.0``, ``"0"``, ``"0.00"``, ``"-0"``): the documented ``fee_rate_bps`` is a string,
    ``"0"`` on a maker's leg, and a post-only maker is never charged, so a null states
    nothing a fee actually taken would not show as drift. It is charged when it is a
    nonzero number or any other value (``"garbage"``, ``{}``, ``[]``, a bool): a
    statement the pot cannot read as zero (architect's decision on #177)."""
    for key, value in leg.items():
        if isinstance(key, str) and "fee" in key.lower() and value is not None:
            try:
                if isinstance(value, bool) or not isinstance(value, (str, int, Decimal,
                                                                     float)):
                    return True
                amount = Decimal(str(value).strip()) if isinstance(value, str) else Decimal(
                    value)
            except (InvalidOperation, ValueError):
                return True
            if not amount.is_finite() or amount != 0:
                return True
    return False
