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
#: A canonical decimal token id: ASCII digits, no sign, no space, no leading zero (Sol P1,
#: round 10: "0" + YES named YES a second time), so one on-chain token has one spelling.
_TOKEN = re.compile(r"0|[1-9][0-9]{0,99}")
_INTEGER = re.compile(r"[0-9]{1,30}")
_ID = r"(?P<id>0x[0-9a-fA-F]{64})"


#: What one market's free text may weigh once parsed. The description is the
#: market's resolution rules, written by Polymarket's market creators: outside text.
MAX_QUESTION_CHARS = 300
MAX_DESCRIPTION_CHARS = 2000
MAX_SOURCE_CHARS = 300


class PolymarketUnavailable(RuntimeError):
    """A read that did not answer. The message is a local reason, never a remote body."""


class PolymarketRefused(ValueError):
    """A request the venue will not accept, stated locally."""



# --- parsing: the public responses, reduced to what a contract publishes -------------

def _text(value: Any, limit: int) -> str | None:
    """A bounded string or nothing; outside text never arrives unbounded."""
    if not isinstance(value, str):
        return None
    text = value.strip()[:limit]
    return text or None


def _decimal(value: Any) -> Decimal | None:
    """A finite decimal from a string or number field, or None. Never a float."""
    if isinstance(value, bool) or value is None:
        return None
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    return number if number.is_finite() else None


def _json_list(value: Any) -> list:
    """Gamma encodes ``outcomes``, ``outcomePrices`` and ``clobTokenIds`` as JSON text."""
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except ValueError:
            return []
        return parsed if isinstance(parsed, list) else []
    return []


def parse_market(raw: Any) -> dict[str, Any] | None:
    """One Gamma market as the tools publish it, or None when it is not a tradable shape.

    Guarantees every number is a decimal string (never a float), every free-text
    field is bounded, and ``outcomes`` pairs each outcome name with its CLOB token
    id and its last Gamma price in the order Gamma lists them. A market whose
    outcome names and token ids do not pair up is dropped rather than published
    half-formed.
    """
    if not isinstance(raw, dict):
        return None
    market_id = raw.get("id")
    names = _json_list(raw.get("outcomes"))
    tokens = _json_list(raw.get("clobTokenIds"))
    prices = _json_list(raw.get("outcomePrices"))
    if market_id is None or not names or len(names) != len(tokens):
        return None
    outcomes = []
    for index, (name, token) in enumerate(zip(names, tokens, strict=True)):
        price = _decimal(prices[index]) if index < len(prices) else None
        outcomes.append({"outcome": _text(name, 80) or f"outcome {index}",
                         "outcome_index": index, "token_id": str(token),
                         "price": None if price is None else str(price)})

    def number(key: str) -> str | None:
        value = _decimal(raw.get(key))
        return None if value is None else str(value)

    # Since 2026-03-31 a market's fee is its ``feeSchedule`` where ``feesEnabled``
    # (market-details, "trading fees"); ``makerBaseFee``/``takerBaseFee`` are legacy.
    schedule = raw.get("feeSchedule") if isinstance(raw.get("feeSchedule"), dict) else {}
    rate = _decimal(schedule.get("rate")) if raw.get("feesEnabled") is True else Decimal(0)
    exponent = _decimal(schedule.get("exponent"))

    return {
        "market_id": str(market_id),
        "condition_id": _text(raw.get("conditionId"), 80),
        "slug": _text(raw.get("slug"), 200),
        "question": _text(raw.get("question"), MAX_QUESTION_CHARS),
        "end_date": _text(raw.get("endDate") or raw.get("endDateIso"), 40),
        "resolution_source": _text(raw.get("resolutionSource"), MAX_SOURCE_CHARS),
        "outcomes": outcomes,
        "active": raw.get("active") is True,
        "closed": raw.get("closed") is True,
        "accepting_orders": raw.get("acceptingOrders") is True,
        "order_book": raw.get("enableOrderBook") is True,
        "tick_size": number("orderPriceMinTickSize"),
        "min_order_size": number("orderMinSize"),
        "neg_risk": raw.get("negRisk") is True,
        "fees": {"enabled": raw.get("feesEnabled") is True,
                 "rate": None if rate is None else str(rate),
                 "exponent": None if exponent is None else str(exponent),
                 "taker_only": schedule.get("takerOnly") is not False},
        "uma_resolution_status": _text(raw.get("umaResolutionStatus"), 40),
        "best_bid": number("bestBid"),
        "best_ask": number("bestAsk"),
        "last_trade_price": number("lastTradePrice"),
        "volume_usd": number("volumeNum") or number("volume"),
        "liquidity_usd": number("liquidityNum") or number("liquidity"),
    }


def market_detail(raw: Any) -> dict[str, Any] | None:
    """A parsed market plus its bounded description (the market's resolution rules)."""
    market = parse_market(raw)
    if market is None:
        return None
    market["description"] = _text(raw.get("description"), MAX_DESCRIPTION_CHARS)
    return market


def parse_search(raw: Any, limit: int) -> list[dict[str, Any]]:
    """The markets a public-search answer names, flattened out of their events.

    Guarantees at most ``limit`` markets, each parsed by ``parse_market``, in the
    order the API ranked their events; closed markets are kept (a resolved market
    is a fact worth finding) and marked ``closed``.
    """
    events = raw.get("events") if isinstance(raw, dict) else None
    found: list[dict[str, Any]] = []
    for event in events if isinstance(events, list) else []:
        for item in (event.get("markets") or []) if isinstance(event, dict) else []:
            market = parse_market(item)
            if market is not None and len(found) < limit:
                found.append(market)
    return found



def _levels(rows: Any, *, best_first_descending: bool, depth: int) -> list[dict[str, str]]:
    levels = []
    for row in rows if isinstance(rows, list) else []:
        price = _decimal(row.get("price")) if isinstance(row, dict) else None
        size = _decimal(row.get("size")) if isinstance(row, dict) else None
        if price is None or size is None or size <= 0:
            continue
        levels.append((price, size))
    levels.sort(key=lambda level: level[0], reverse=best_first_descending)
    return [{"price": str(p), "size": str(s)} for p, s in levels[:depth]]


def parse_book(raw: Any, depth: int) -> dict[str, Any]:
    """A CLOB book summary with both sides best first, whatever order the API sent.

    The CLOB lists bids ascending and asks descending, so the best of each is
    last; sorting here means a reader never depends on that. Guarantees at most
    ``depth`` levels a side, every price and size a decimal string, and the
    midpoint of the best bid and ask when both exist.
    """
    if not isinstance(raw, dict):
        raise PolymarketUnavailable("book response is not an object")
    bids = _levels(raw.get("bids"), best_first_descending=True, depth=depth)
    asks = _levels(raw.get("asks"), best_first_descending=False, depth=depth)
    mid = None
    if bids and asks:
        mid = str((Decimal(bids[0]["price"]) + Decimal(asks[0]["price"])) / 2)
    tick = _decimal(raw.get("tick_size"))
    minimum = _decimal(raw.get("min_order_size"))
    return {
        "token_id": str(raw.get("asset_id") or ""),
        "condition_id": _text(raw.get("market"), 80),
        "bids": bids, "asks": asks, "midpoint": mid,
        "tick_size": None if tick is None else str(tick),
        "min_order_size": None if minimum is None else str(minimum),
        "neg_risk": raw.get("neg_risk") is True,
        "last_trade_price": (None if _decimal(raw.get("last_trade_price")) is None
                             else str(_decimal(raw.get("last_trade_price")))),
        "timestamp_ms": _text(str(raw.get("timestamp") or ""), 20),
    }




# --- the door ---------------------------------------------------------------------------


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
    """An outcome token id: a canonical decimal integer, as a string (``_TOKEN``), so
    comparing ids as strings is comparing the tokens."""
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


def unique(keys: Any, what: str) -> None:
    """Each key once across a complete reply, pagination included (UNIQUENESS, the sweep
    after Sol's round-11 review of #177: a position listed twice was counted twice)."""
    seen: set = set()
    for key in keys:
        if key in seen:
            raise Malformed(f"{what} is listed twice")
        seen.add(key)


def same(found: Any, asked: Any, what: str) -> None:
    """The reply is about what was asked for, compared canonically (IDENTITY: a foreign
    market row once supplied the payout of the market asked for)."""
    if found != asked:
        raise Malformed(f"{what} is not the one asked for")


def stated_owner(row: dict, key: str, funder: str | None, what: str) -> None:
    """An owner field the reply states names this pot's funder (IDENTITY); one it does
    not state is not required. For a leg bound to a hash this world signed."""
    if funder is not None and row.get(key) is not None:
        same(address(row[key], what), funder.lower(), what)


def owner(row: dict, key: str, funder: str | None, what: str) -> None:
    """The owner field names this pot's funder, required (IDENTITY, Sol P1, round 12): a
    reply bound to no hash this world signed (a position, an open order) is this pot's
    only by its owner field."""
    if funder is not None:
        same(address(field(row, key, what), what), funder.lower(), what)


# --- first sight binds, forever -----------------------------------------------------------


def bind(store: dict, kind: str, key: str, facts: Any, *, floor: bool = False) -> str | None:
    """First sight binds, forever (the owner's rule after Sol's round-13 review of #177):
    the facts first observed for ``(kind, key)`` are recorded in ``store`` (durable: the
    poll's checkpointed cursor), and every later observation is checked against them.
    Returns None when it agrees, else why it disagrees, which the caller treats as a
    contradiction that halts buying; the binding stands. ``facts`` are JSON values. With
    ``floor`` they are a quantity that may only rise: the most ever observed is kept,
    and a report below it disagrees."""
    bound = store.setdefault(kind, {})
    if key not in bound:
        bound[key] = facts
        return None
    if floor:
        if Decimal(str(facts)) < Decimal(str(bound[key])):
            return f"a {kind} is reported below what was first observed"
        bound[key] = str(max(Decimal(str(facts)), Decimal(str(bound[key]))))
        return None
    if bound[key] != facts:
        return f"a {kind} disagrees with what was first observed"
    return None


def bind_market(store: dict, market: dict[str, Any]) -> str | None:
    """Bind a parsed market (``market_detail``'s shape) at first sight: the market to its
    outcome tokens in order, and each token to its market, outcome index and outcome
    label (as a digest: the label is third-party text, never stored). Returns why a
    later reply disagrees, or None."""
    import hashlib

    tokens = [str(o["token_id"]) for o in market["outcomes"]]
    reason = bind(store, "market", str(market["market_id"]), tokens)
    for index, outcome in enumerate(market["outcomes"]):
        label = hashlib.sha256(str(outcome.get("outcome")).encode()).hexdigest()[:16]
        reason = reason or bind(store, "token", str(outcome["token_id"]), [
            str(market["market_id"]), index, label])
    return reason


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
    listed = [other_id(h, "canceled") for h in rows(
        field(row, "canceled", "cancel answer"), "canceled")]
    refused_raw = obj(field(row, "not_canceled", "cancel answer"), "not_canceled")
    named = [other_id(h, "not_canceled") for h in refused_raw]
    unique(listed, "a cancelled order")
    unique(named, "a refused cancel")
    cancelled = set(listed)
    refused = dict(zip(named, refused_raw.values(), strict=True))
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


def orders_page(answer: Any, funder: str | None = None) -> tuple[list[Order], str]:
    """A ``GET /data/orders`` page: its orders, each this pot's (its ``maker_address``,
    required, the funder: an open order is bound to no hash), and its ``next_cursor``.
    Uniqueness across the complete listing is the caller's, over every page."""
    page = obj(answer, "orders page")
    listed = []
    for raw in rows(field(page, "data", "orders page"), "data"):
        owner(obj(raw, "order"), "maker_address", funder, "order maker")
        listed.append(order(raw))
    unique((o.order_id for o in listed), "an order")
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


def trades_page(answer: Any, ours: dict[str, Signed], *, seen: set | None = None,
                funder: str | None = None) -> tuple[list[Trade], str]:
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
    (``LivePolymarket._fills``). Each trade id appears once across a read's complete
    listing (``seen`` carries the ids of its earlier pages), each order once among a
    trade's maker legs, and a leg of this world's states this pot as its maker when it
    states one."""
    seen = set() if seen is None else seen
    page = obj(answer, "trades page")
    own = {h.lower(): signed for h, signed in ours.items()}
    trades = []
    for raw in rows(field(page, "data", "trades page"), "data"):
        row = obj(raw, "trade")
        trade_id = text(field(row, "id", "trade"), "trade id")
        if not trade_id:
            raise Malformed("trade id is empty")
        if trade_id in seen:
            raise Malformed("a trade is listed twice")
        seen.add(trade_id)
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
        makers = [obj(m, "maker leg") for m in rows(field(row, "maker_orders", "trade"),
                                                      "maker_orders")]
        unique((other_id(field(m, "order_id", "maker leg"), "maker order_id")
                for m in makers), "a maker order in a trade")
        for maker in makers:
            maker_id = other_id(field(maker, "order_id", "maker leg"), "maker order_id")
            maker_side = text(field(maker, "side", "maker leg"), "maker side")
            if maker_side not in ("BUY", "SELL"):
                raise Malformed("maker side is not documented")
            matched = positive(field(maker, "matched_amount", "maker leg"), "matched_amount")
            maker_price = price(field(maker, "price", "maker leg"), "maker price")
            if maker_id in own:
                stated_owner(maker, "maker_address", funder, "maker address")
                asset = token_id(field(maker, "asset_id", "maker leg"), "maker asset_id")
                legs.append(_leg(maker_id, asset, maker_side, matched, maker_price,
                                 own[maker_id], taker=False))
        # Every trade is returned, with this world's legs or none: a trade once bound to
        # this world's legs is compared even when it comes back without them.
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


def positions_page(answer: Any, funder: str | None = None) -> list[Position]:
    """A Data API ``/positions`` page (a list), each row this pot's (its ``proxyWallet``,
    required, the funder: a position is bound to no hash). Uniqueness of tokens across
    the complete listing is the caller's, over every page."""
    found = []
    for raw in rows(answer, "positions page"):
        row = obj(raw, "position")
        owner(row, "proxyWallet", funder, "position wallet")
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
    unique((p.token_id for p in found), "a position")
    return found


# --- the market and its book --------------------------------------------------------------


def market(answer: Any, expect: str | None = None) -> dict[str, Any]:
    """A Gamma market the pot writes on or settles against: the fields the order path
    reads checked, then parsed as the tools publish it (``market_detail``). ``expect``
    is the market id asked for: the row must be that market (Sol P1, round 11: a
    foreign resolved row naming our token supplied its payout)."""
    row = obj(answer, "market")
    market_id = field(row, "id", "market")
    if isinstance(market_id, bool) or not isinstance(market_id, (str, int)) or market_id == "":
        raise Malformed("market id is not an id")
    if expect is not None:
        same(str(market_id), str(expect), "market")
    names = _listed(field(row, "outcomes", "market"), "outcomes")
    tokens = _listed(field(row, "clobTokenIds", "market"), "clobTokenIds")
    prices = _listed(field(row, "outcomePrices", "market"), "outcomePrices")
    if not len(names) == len(tokens) == len(prices) == 2:
        raise Malformed("market is not a two-outcome market")
    for name in names:
        text(name, "outcome")
    for token in tokens:
        token_id(token, "clobTokenId")
    if len(set(tokens)) != len(tokens):
        # Sol P1 (round 9) on #177: [YES, YES] with payouts [1, 0] paid a losing YES. Each
        # outcome token is named once, so each held token has exactly one payout; the
        # payout itself is a resolved binary market's vector or none (``payout``).
        raise Malformed("market names an outcome token twice")
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
    _read_tokens(detail)
    return detail


def markets(answer: Any) -> list[dict[str, Any]]:
    """A Gamma ``/markets`` listing: every market in it, each parsed by ``market``, each
    market once."""
    found = [market(row) for row in rows(answer, "markets")]
    unique((m["market_id"] for m in found), "a market")
    return found


def _listed(value: Any, what: str) -> list:
    """Gamma encodes a market's lists as JSON text."""
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            raise Malformed(f"{what} is not a list") from None
    return rows(value, what)


def book(answer: Any, depth: int, token: str) -> dict[str, Any]:
    """A CLOB ``/book`` summary for ``token``'s mark: its ``asset_id`` that token (Sol P1,
    round 10: another token's valid book became this one's mark), every level a price
    inside (0, 1) and a positive size, then best first on both sides (``parse_book``)."""
    row = obj(answer, "book")
    if token_id(field(row, "asset_id", "book"), "book asset_id") != token:
        raise Malformed("book is another token's")
    for side in ("bids", "asks"):
        levels = [obj(level, "book level") for level in rows(field(row, side, "book"), side)]
        unique((price(field(level, "price", "book level"), "book price")
                for level in levels), f"a {side[:-1]} price level")
        for level in levels:
            positive(field(level, "size", "book level"), "book size")
    return parse_book(row, depth)


# --- the public reads, for seats and the kernel's settlement ------------------------------
#
# A seat's read and an event claim's settlement read are bound to what was asked as
# the order path's are (IDENTITY, UNIQUENESS), and otherwise parsed leniently as the
# tools publish them: a field a market does not state is absent from it, not fatal.


def read_search(answer: Any, limit: int) -> list[dict[str, Any]]:
    """Gamma's ``/public-search``: the markets it names, as ``parse_search``, each market
    once and each checked as every market is (``_read_tokens``; Sol P2, round 13)."""
    # Every market the reply names is checked, before any is cut by ``limit`` (Sol P2,
    # round 14).
    found = parse_search(answer, 10**9)
    unique((d["market_id"] for d in found), "a market")
    for detail in found:
        _read_tokens(detail)
    return found[:limit]


def _read_tokens(detail: dict[str, Any]) -> list[str]:
    """The checks every parsed market passes, public or money path: each outcome token
    canonical and once, each stated outcome price inside [0, 1]."""
    tokens = [token_id(o["token_id"], "clobTokenId") for o in detail["outcomes"]]
    unique(tokens, "an outcome token")
    for outcome in detail["outcomes"]:
        if outcome.get("price") is not None and not 0 <= Decimal(outcome["price"]) <= 1:
            raise Malformed("outcomePrice is outside [0, 1]")
    return tokens


def read_market(answer: Any, market_id: str) -> dict[str, Any]:
    """Gamma's ``/markets/{id}``: the market asked for (its ``id``), each outcome token
    once and canonical."""
    row = obj(answer, "market")
    same(str(field(row, "id", "market")), str(market_id), "market")
    detail = market_detail(row)
    if detail is None:
        raise Malformed("market has no tradable shape")
    _read_tokens(detail)
    return detail


def read_market_of_token(answer: Any, token: str) -> dict[str, Any] | None:
    """A Gamma ``/markets?clob_token_ids`` listing: the one market naming ``token``, or
    None. Each market once; two markets naming the token is malformed (Sol P1, round
    12: the first of two was taken, and a foreign resolved market settled our bet)."""
    details = [d for d in (market_detail(row) for row in rows(answer, "markets")) if d]
    unique((d["market_id"] for d in details), "a market")
    naming = [d for d in details if token in _read_tokens(d)]
    if len(naming) > 1:
        raise Malformed("a token is named by two markets")
    return naming[0] if naming else None


def read_midpoint(answer: Any) -> str | None:
    """The CLOB's ``/midpoint``: its ``mid`` as a decimal string, or None."""
    mid = _decimal(answer.get("mid")) if isinstance(answer, dict) else None
    if mid is not None and not 0 <= mid <= 1:
        raise Malformed("midpoint is outside [0, 1]")
    return None if mid is None else str(mid)


def credentials(answer: Any) -> tuple[str, str, str]:
    """``/auth/derive-api-key`` or ``/auth/api-key``: (apiKey, secret, passphrase)."""
    row = obj(answer, "credentials answer")
    return tuple(text(field(row, k, "credentials answer"), k)  # type: ignore[return-value]
                 for k in ("apiKey", "secret", "passphrase"))


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
