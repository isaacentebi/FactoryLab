"""Polymarket event markets: a public read client, a simulated venue, and the live seam.

Polymarket lists binary event markets. Each market is a condition on the Gnosis
Conditional Token Framework (CTF) with two outcome tokens, and each token trades
on Polymarket's central limit order book (the CLOB) at a price between 0 and 1
dollar. When the market resolves, a winning token redeems for 1 and a losing
one for 0 (collateral is pUSD, Polymarket's USDC-backed token, since CLOB V2).
That is the whole reason this surface exists in the factory: the
population already makes Brier-scored forecasts inside the loop, and an event
market is a place where the world outside the loop prices a belief and then
settles it (essay II.III, the realized-consequence signal "sits outside the
factory's input entirely"; II.IV.a, "vote on values, bet on beliefs").

Three parts, in the order the runtime needs them:

``PolymarketReader``
    Plain HTTPS GETs against the two public, unauthenticated APIs: the Gamma
    market-metadata API and the CLOB's public book and price endpoints. Bounded
    timeout, bounded body, no redirects, and no response body in any error.
``FakePolymarket``
    A deterministic venue with seeded markets, a moving book, resting orders
    that fill, a collateral pot, and scripted resolution. It answers the same
    reads as the reader and is the only venue writes reach in this phase.
``LiveOrderAdapter``
    The marked seam for live order signing on Polygon. It is not built. Its
    docstring lists what it will need.

Sources, read 2026-09-22 (Polymarket moved to CLOB V2 on 2026-04-28; the
changelog is https://docs.polymarket.com/changelog/predictions):

* Gamma discovery, market fields and ``/public-search``:
  https://docs.polymarket.com/market-data/discover-markets and the spec at
  https://docs.polymarket.com/api-spec/gamma-openapi.yaml
* Market details, tick sizes and the fee schedule:
  https://docs.polymarket.com/market-data/market-details
* CLOB public book, midpoint and price endpoints:
  https://docs.polymarket.com/market-data/prices-order-books and
  https://docs.polymarket.com/api-spec/clob-openapi.yaml
* Fees (``fee = shares * rate * p * (1 - p)``, takers only):
  https://docs.polymarket.com/trading/fees
* Order types and signing: https://docs.polymarket.com/trading/place-orders
* Resolution (UMA Optimistic Oracle, 50-50 on "unknown"):
  https://docs.polymarket.com/concepts/resolution
* Redemption: https://docs.polymarket.com/trading/positions/manage
* Collateral (pUSD on Polygon): https://docs.polymarket.com/concepts/pusd
* Contracts: https://docs.polymarket.com/resources/contracts
* Wallets, allowances and API credentials:
  https://docs.polymarket.com/trading/wallets-auth and
  https://docs.polymarket.com/getting-started/api#authentication
"""

from __future__ import annotations

import json
import random
import time
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any
from urllib import error, parse, request

GAMMA_URL = "https://gamma-api.polymarket.com"
CLOB_URL = "https://clob.polymarket.com"

#: A read either answers inside this or is unavailable this call. The CLOB and Gamma
#: answer in well under a second; ten seconds is the connector's own bound.
HTTP_TIMEOUT_S = 10
#: The most bytes one read may bring into the process. A Gamma page of twenty
#: markets is about 150 KB; a book is a few KB.
MAX_BODY_BYTES = 2 * 1024 * 1024

#: What one market's free text may weigh once parsed. The description is the
#: market's resolution rules, written by Polymarket's market creators: outside text.
MAX_QUESTION_CHARS = 300
MAX_DESCRIPTION_CHARS = 2000
MAX_SOURCE_CHARS = 300

#: The tick sizes the CLOB publishes (market-details). A market states its own in
#: ``orderPriceMinTickSize`` and the book's ``tick_size``; that is what is enforced.
TICK_SIZES = tuple(Decimal(t) for t in ("0.1", "0.01", "0.005", "0.0025", "0.001", "0.0001"))


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


#: What a resolved binary market's outcome prices can be: one winner, or a 50-50 answer.
_PAYOUT_VECTORS = ({Decimal(0), Decimal(1)}, {Decimal("0.5")})


def payout(market: dict[str, Any], token_id: str) -> Decimal | None:
    """What one token of a parsed market redeems for, or None while that is not settled.

    Guarantees a value only for a closed market whose outcome prices are a
    redemption (1 and 0, or 0.5 each) and whose UMA status, when stated, is
    ``resolved``: a closed market still in its challenge window or in dispute has
    no payout yet (concepts/resolution). Gamma publishes a resolved market's
    ``outcomePrices`` as its redemption values, and the simulated venue does too.
    """
    if not market.get("closed"):
        return None
    status = market.get("uma_resolution_status")
    if status is not None and status != "resolved":
        return None
    prices = [_decimal(o.get("price")) for o in market.get("outcomes") or ()]
    if None in prices or len(prices) != 2 or set(prices) not in _PAYOUT_VECTORS:
        return None
    return next((p for o, p in zip(market["outcomes"], prices, strict=True)
                 if o.get("token_id") == token_id), None)


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


# --- the public read client -------------------------------------------------------------

class _NoRedirect(request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        """A public read goes to the host it named or nowhere."""
        return None


def http_get_json(url: str, *, timeout_s: int = HTTP_TIMEOUT_S) -> Any:
    """One bounded GET that returns parsed JSON with exact decimals, or raises locally.

    Guarantees: at most ``timeout_s`` per socket operation, at most
    ``MAX_BODY_BYTES`` read, no redirect followed, numbers parsed as ``Decimal``,
    and every failure a ``PolymarketUnavailable`` whose message is a status or a
    local reason and never the response body.
    """
    req = request.Request(url, headers={"Accept": "application/json",
                                        "User-Agent": "FactoryLab/0.4"}, method="GET")
    try:
        response = request.build_opener(_NoRedirect()).open(req, timeout=timeout_s)
    except error.HTTPError as exc:
        exc.close()
        raise PolymarketUnavailable(f"HTTP {exc.code}") from None
    except (error.URLError, TimeoutError, OSError) as exc:
        raise PolymarketUnavailable(f"transport: {type(exc).__name__}") from None
    with response:
        body = response.read(MAX_BODY_BYTES + 1)
    if len(body) > MAX_BODY_BYTES:
        raise PolymarketUnavailable("response larger than the read bound")
    try:
        return json.loads(body, parse_float=Decimal)
    except (ValueError, UnicodeError):
        raise PolymarketUnavailable("response is not JSON") from None


#: Polymarket's published API rate limits ("Rate Limits", docs.polymarket.com,
#: read 2026-09-24): Gamma general 4,000 requests / 10 s, /events 500, /markets 300,
#: /public-search 350; CLOB general 9,000, /book 1,500, /books 500, /price 1,500,
#: /midpoint 1,500, each over a sliding 10 s window, throttled when exceeded. The
#: reads here reach /public-search, /markets and /book, so the tightest endpoint a
#: request can land on is Gamma /markets: 300 per 10 s, 1,800 a minute.
PUBLISHED_REQUESTS_PER_MINUTE = 1_800
#: What the world's Polymarket reads may use by default, all together: 10% of the
#: tightest published limit, a conservative fraction, since the IP may be shared.
DEFAULT_READ_REQUESTS_PER_MINUTE = 180
#: Of that, held back for the kernel's own settlement and marking reads, which no
#: seat can spend.
DEFAULT_KERNEL_RESERVE_PER_MINUTE = 60
#: Requests one seat read sends: every Polymarket read tool is one GET, sent once.
SEAT_READ_REQUESTS = 1
#: The most requests one kernel read can send, by reader method: ``market_of_token``
#: asks the closed listing, the open one, then the closed one again (up to 3); every
#: other read is one GET.
READ_REQUESTS = {"market_of_token": 3}


def read_requests(method: str) -> int:
    """The most requests one read by ``method`` can send."""
    return READ_REQUESTS.get(method, 1)


@dataclass
class PolymarketReader:
    """The public, credential-free read surface. Guarantees no call signs or moves funds.

    Every method is a GET on an unauthenticated endpoint; the transport is a
    parameter so tests read recorded responses and never the network.
    """

    gamma_url: str = GAMMA_URL
    clob_url: str = CLOB_URL
    get: Any = http_get_json
    name: str = "polymarket"
    deterministic: bool = False
    #: A value no earlier Gamma read carried, for ``CACHE_KEY``.
    nonce: Any = time.time_ns

    #: Gamma answers through a shared cache (``cache-control: public, max-age=300``),
    #: keyed by the whole URL. Read 2026-09-23: ``/markets/4827887`` was served from it
    #: (``cf-cache-status: HIT``) still ``closed: false``, UMA ``proposed``, after the
    #: market had resolved, while the same path with a query parameter no one had
    #: asked for was a MISS and current. Every Gamma read carries a fresh value
    #: under this key, which Gamma ignores, so what it returns is the origin's state
    #: at the read and never a copy up to five minutes old. The CLOB is not cached
    #: (``cf-cache-status: DYNAMIC``).
    CACHE_KEY = "_"

    def _gamma(self, path: str, **params: Any) -> Any:
        fresh = {k: v for k, v in params.items() if v is not None}
        fresh[self.CACHE_KEY] = self.nonce()
        self.sent = getattr(self, "sent", 0) + 1  # counted before it is sent
        return self.get(f"{self.gamma_url}{path}?{parse.urlencode(fresh)}")

    def _clob(self, path: str, **params: Any) -> Any:
        self.sent = getattr(self, "sent", 0) + 1
        return self.get(f"{self.clob_url}{path}?{parse.urlencode(params)}")

    def requests_sent(self) -> int:
        """Guarantees the count of every request this reader has sent, monotone. A read
        of its own counter, journaled read-only, so a replay charges what was sent."""
        return getattr(self, "sent", 0)

    def search_markets(self, query: str, limit: int) -> list[dict[str, Any]]:
        """Markets matching ``query`` through Gamma's public search, best ranked first."""
        raw = self._gamma("/public-search", q=query, limit_per_type=limit,
                          search_profiles="false", search_tags="false")
        return parse_search(raw, limit)

    def market(self, market_id: str) -> dict[str, Any]:
        """One market's contract and rules text, by Gamma market id, as the origin holds
        it at the read (never the shared cache's copy: ``CACHE_KEY``)."""
        detail = market_detail(self._gamma(f"/markets/{parse.quote(market_id, safe='')}"))
        if detail is None:
            raise PolymarketUnavailable("market response has no tradable shape")
        return detail

    def market_of_token(self, token_id: str) -> dict[str, Any] | None:
        """The market listing ``token_id`` among its outcomes, closed or open, or None.

        Gamma's ``/markets`` lists open markets unless asked for closed ones (read
        2026-09-23: a resolved market's token answered ``[]`` without ``closed=true``).
        Closing is final, so the closed listing is asked first and the open one only
        for a token it does not hold. A market that closes between those two reads is
        in neither, so an empty open answer asks the closed listing once more: None
        means the token was absent from the closed listing on both sides of the open
        read, never that the lookup straddled a close. Every read goes past the shared
        cache (``CACHE_KEY``), which served a just-resolved market as still open.
        """
        for closed in ("true", None, "true"):
            raw = self._gamma("/markets", clob_token_ids=token_id, closed=closed)
            for row in raw if isinstance(raw, list) else []:
                detail = market_detail(row)
                if detail and any(o["token_id"] == token_id for o in detail["outcomes"]):
                    return detail
        return None

    def order_book(self, token_id: str, depth: int) -> dict[str, Any]:
        """The CLOB's book summary for one outcome token, best first on both sides."""
        return parse_book(self._clob("/book", token_id=token_id), depth)

    def midpoint(self, token_id: str) -> str | None:
        """The CLOB's midpoint for one outcome token, as a decimal string."""
        raw = self._clob("/midpoint", token_id=token_id)
        mid = _decimal(raw.get("mid")) if isinstance(raw, dict) else None
        return None if mid is None else str(mid)


# --- the simulated venue ------------------------------------------------------------------

#: The seeded listing. Questions are neutral placeholders on purpose: the fake is a
#: venue for plumbing and accounting, not a source of claims about the world.
#: ``resolves_after_s`` schedules a resolution that long after the venue's first
#: step, its answer drawn at that moment with the market's own price as the
#: probability of "Yes", so the simulated market is calibrated by construction.
DEFAULT_FAKE_MARKETS = (
    {"market_id": "fake-1", "question":
         "Will simulated event A occur by its scripted resolution time?",
     "outcomes": ("Yes", "No"), "mid": "0.40", "fee_rate": "0", "resolves_after_s": 900},
    {"market_id": "fake-2", "question":
         "Will simulated event B occur by its scripted resolution time?",
     "outcomes": ("Yes", "No"), "mid": "0.70", "fee_rate": "0.05",
     "resolves_after_s": 3600},
    {"market_id": "fake-3", "question":
         "Will simulated event C occur by its scripted resolution time?",
     "outcomes": ("Yes", "No"), "mid": "0.15", "fee_rate": "0.04",
     "resolves_after_s": None},
)


@dataclass
class FakePolymarket:
    """A deterministic event-market venue for simulated worlds.

    Guarantees: identical ``seed``, ``markets`` and ``resolutions`` produce
    identical books, fills and resolutions in the same call order. Prices live on
    a ``tick`` grid strictly inside (0, 1); an outcome's two tokens price to 1.
    The book is one tick either side of the mid, ``depth_shares`` deep a level.
    A limit buy at or above the best ask fills at once at the ask (a sell at or
    below the best bid, at the bid); otherwise it rests and fills at its own
    price once the moving mid crosses it. A resting buy holds ``price * size``
    USDC and a resting sell holds its tokens, so nothing is sold twice. Fees are
    Polymarket's: a taker pays ``shares * rate * p * (1 - p)``, rounded to five
    decimals, at the market's own rate; a resting order that is filled pays none.
    At a scripted resolution every resting order on the market is cancelled and
    every token redeems into the pot: 1 USDC a winning token, 0 a losing one,
    0.5 each on a 50-50 answer. Client ids are idempotent: repeating one returns
    the first answer and never trades twice.
    """

    name: str = "fake-polymarket"
    seed: int = 0
    start_usdc: Decimal = Decimal(0)
    markets: tuple[dict, ...] = DEFAULT_FAKE_MARKETS
    #: market id -> (resolve at or after this ns, winning outcome index, or None for
    #: 50-50). A market named here ignores its seeded ``resolves_after_s``.
    resolutions: dict[str, tuple[int, int | None]] = field(default_factory=dict)
    tick: Decimal = Decimal("0.01")
    min_order_size: Decimal = Decimal(5)
    depth_shares: Decimal = Decimal(500)
    step_ticks: int = 1

    deterministic = True

    def __post_init__(self) -> None:
        self._rng = random.Random(self.seed)
        self._now_ns = 0
        self._started_ns: int | None = None
        self._drawn: dict[str, int] = {}  # market id -> ns its seeded resolution is due
        self._cash = Decimal(self.start_usdc)
        self._markets: dict[str, dict] = {}
        self._tokens: dict[str, tuple[str, int]] = {}
        for index, seed_market in enumerate(self.markets):
            market_id = str(seed_market["market_id"])
            mid = Decimal(str(seed_market["mid"]))
            tokens = [str(10**20 + (self.seed * 1000 + index) * 2 + side) for side in (0, 1)]
            self._markets[market_id] = {
                "market_id": market_id,
                "condition_id": f"0x{self.seed:04x}{index:060x}",
                "question": seed_market["question"],
                "outcomes": list(seed_market["outcomes"]),
                "tokens": tokens, "mid": mid,
                "fee_rate": Decimal(str(seed_market.get("fee_rate", "0"))),
                "closed": False, "winner": None, "resolved_at_ns": None,
            }
            for side, token in enumerate(tokens):
                self._tokens[token] = (market_id, side)
        self._positions: dict[str, dict[str, Decimal]] = {}
        self._orders: dict[str, dict] = {}  # resting only
        self._all_orders: dict[str, dict] = {}  # every accepted order, by order id
        self._client_results: dict[str, dict] = {}
        self._next_oid = 1
        self._events: list[dict] = []

    # ---- reads

    def _token_mid(self, token_id: str) -> Decimal:
        market_id, side = self._tokens[token_id]
        mid = self._markets[market_id]["mid"]
        return mid if side == 0 else 1 - mid

    def _public(self, market: dict, *, detail: bool = False) -> dict[str, Any]:
        yes = market["mid"]
        prices = (yes, 1 - yes)
        if market["closed"]:
            prices = tuple(self._payout(market, side) for side in (0, 1))
        row = {
            "market_id": market["market_id"], "condition_id": market["condition_id"],
            "slug": market["market_id"], "question": market["question"],
            "end_date": None, "resolution_source": "scripted",
            "outcomes": [{"outcome": name, "outcome_index": index, "token_id": token,
                          "price": str(price)}
                         for index, (name, token, price) in enumerate(zip(
                             market["outcomes"], market["tokens"], prices, strict=True))],
            "active": not market["closed"], "closed": market["closed"],
            "accepting_orders": not market["closed"], "order_book": True,
            "tick_size": str(self.tick), "min_order_size": str(self.min_order_size),
            "neg_risk": False,
            "fees": {"enabled": bool(market["fee_rate"]), "rate": str(market["fee_rate"]),
                     "exponent": "1", "taker_only": True},
            "uma_resolution_status": "resolved" if market["closed"] else None,
            "best_bid": None if market["closed"] else str(yes - self.tick),
            "best_ask": None if market["closed"] else str(yes + self.tick),
            "last_trade_price": str(yes), "volume_usd": "0", "liquidity_usd": "0",
        }
        if detail:
            row["description"] = "A simulated market. It resolves on the world's script."
        return row

    def search_markets(self, query: str, limit: int) -> list[dict[str, Any]]:
        """Seeded markets whose question contains ``query``, case-insensitively."""
        self._count(1)
        needle = query.lower()
        rows = [self._public(m) for m in self._markets.values()
                if needle in m["question"].lower() or needle in m["market_id"]]
        return rows[:limit]

    def market(self, market_id: str) -> dict[str, Any]:
        self._count(1)
        market = self._markets.get(market_id)
        if market is None:
            raise PolymarketUnavailable("HTTP 404")
        return self._public(market, detail=True)

    def _count(self, requests: int) -> None:
        # The requests the live reader would send for the same read: the simulated
        # venue is metered as the live one is (``requests_sent``).
        self.sent = getattr(self, "sent", 0) + requests

    def requests_sent(self) -> int:
        """Guarantees the count of requests the live reader would have sent for every
        read this venue answered, monotone."""
        return getattr(self, "sent", 0)

    def market_of_token(self, token_id: str) -> dict[str, Any] | None:
        listed = self._tokens.get(token_id)
        if listed is None:
            self._count(3)  # closed, open, closed: absent from all three
            return None
        market = self._markets[listed[0]]
        self._count(1 if market["closed"] else 2)  # found closed, or closed then open
        return self._public(market, detail=True)

    def _best(self, token_id: str) -> tuple[Decimal, Decimal]:
        mid = self._token_mid(token_id)
        return mid - self.tick, mid + self.tick

    def order_book(self, token_id: str, depth: int) -> dict[str, Any]:
        self._count(1)
        listed = self._tokens.get(token_id)
        if listed is None:
            raise PolymarketUnavailable("HTTP 404")
        market = self._markets[listed[0]]
        if market["closed"]:
            bids = asks = []
        else:
            bid, ask = self._best(token_id)
            bids = [{"price": str(bid - self.tick * i), "size": str(self.depth_shares)}
                    for i in range(depth) if bid - self.tick * i > 0]
            asks = [{"price": str(ask + self.tick * i), "size": str(self.depth_shares)}
                    for i in range(depth) if ask + self.tick * i < 1]
        return {"token_id": token_id, "condition_id": market["condition_id"],
                "bids": bids, "asks": asks,
                "midpoint": None if market["closed"] else str(self._token_mid(token_id)),
                "tick_size": str(self.tick), "min_order_size": str(self.min_order_size),
                "neg_risk": False, "last_trade_price": str(self._token_mid(token_id)),
                "timestamp_ms": str(self._now_ns // 1_000_000)}

    def midpoint(self, token_id: str) -> str | None:
        self._count(1)
        if token_id not in self._tokens:
            raise PolymarketUnavailable("HTTP 404")
        return str(self._token_mid(token_id))

    def taker_fee(self, token_id: str, size: Decimal, price: Decimal) -> Decimal:
        """What a taker pays to trade ``size`` tokens at ``price``, in USDC."""
        rate = self._markets[self._tokens[token_id][0]]["fee_rate"]
        return (size * rate * price * (1 - price)).quantize(Decimal("0.00001"))

    # ---- account

    def _held(self, token_id: str) -> Decimal:
        return self._positions.get(token_id, {}).get("size", Decimal(0))

    def _holds(self) -> tuple[Decimal, dict[str, Decimal]]:
        """USDC held by resting buys and tokens held by resting sells."""
        usdc, tokens = Decimal(0), {}
        for order in self._orders.values():
            if order["is_buy"]:
                usdc += order["price"] * order["remaining"]
            else:
                tokens[order["token_id"]] = tokens.get(order["token_id"], Decimal(0)) + order[
                    "remaining"]
        return usdc, tokens

    def account(self) -> dict[str, Any]:
        """The pot as a custodian would state it: USDC, what orders hold, tokens held."""
        held_usdc, held_tokens = self._holds()
        return {
            "usdc": str(self._cash), "usdc_available": str(self._cash - held_usdc),
            "positions": [
                {"token_id": token, "market_id": self._tokens[token][0],
                 "outcome_index": self._tokens[token][1],
                 # The market creator's label, for the runtime to normalise; it is
                 # third-party text and never published as it is.
                 "outcome_name": self._markets[self._tokens[token][0]]["outcomes"][
                     self._tokens[token][1]],
                 "size": str(p["size"]), "avg_px": str(p["avg_px"]),
                 "available": str(p["size"] - held_tokens.get(token, Decimal(0)))}
                for token, p in sorted(self._positions.items()) if p["size"] > 0],
            "open_orders": [self._order_view(o) for o in self._orders.values()],
            "observed_at_ns": self._now_ns,
        }

    @staticmethod
    def _order_view(order: dict) -> dict[str, Any]:
        return {"order_id": order["order_id"], "token_id": order["token_id"],
                "side": "buy" if order["is_buy"] else "sell", "price": str(order["price"]),
                "size": str(order["size"]), "remaining": str(order["remaining"])}

    # ---- writes

    def _fill(self, order: dict, size: Decimal, px: Decimal, *, taker: bool) -> None:
        token = order["token_id"]
        fee = self.taker_fee(token, size, px) if taker else Decimal(0)
        position = self._positions.setdefault(token, {"size": Decimal(0),
                                                      "avg_px": Decimal(0)})
        realized = Decimal(0)
        if order["is_buy"]:
            total = position["size"] + size
            position["avg_px"] = (position["size"] * position["avg_px"] + size * px) / total
            position["size"] = total
            self._cash -= px * size + fee
        else:
            realized = (px - position["avg_px"]) * size
            position["size"] -= size
            self._cash += px * size - fee
        order["remaining"] -= size
        order["filled"] += size
        order["notional"] += px * size
        self._events.append({
            "kind": "fill", "order_id": order["order_id"], "token_id": token,
            "market_id": self._tokens[token][0], "is_buy": order["is_buy"],
            "size": str(size), "px": str(px), "fee_usd": str(fee),
            "realized_usd": str(realized), "ts_ns": self._now_ns})

    def _result(self, order: dict) -> dict[str, Any]:
        status = ("filled" if order["remaining"] == 0 else
                  "cancelled" if order.get("cancelled") else "resting")
        avg = order["notional"] / order["filled"] if order["filled"] else None
        return {"order_id": order["order_id"], "status": status,
                "filled_size": str(order["filled"]),
                "avg_px": None if avg is None else str(avg), "error": None}

    def place(self, *, client_id: str, token_id: str, is_buy: bool, size: Decimal,
              price: Decimal) -> dict[str, Any]:
        """Accept or reject one good-until-cancelled limit order, once per client id."""
        if client_id in self._client_results:
            return self.lookup(client_id)

        def reject(reason: str) -> dict[str, Any]:
            result = {"order_id": None, "status": "rejected", "filled_size": "0",
                      "avg_px": None, "error": reason}
            self._client_results[client_id] = result
            return dict(result)

        listed = self._tokens.get(token_id)
        if listed is None:
            return reject("unknown token")
        market = self._markets[listed[0]]
        if market["closed"]:
            return reject("market is closed")
        if not self.tick <= price <= 1 - self.tick or price % self.tick:
            return reject(f"price must be on the {self.tick} tick inside (0, 1)")
        if size < self.min_order_size:
            return reject(f"size below the minimum order of {self.min_order_size}")
        held_usdc, held_tokens = self._holds()
        if is_buy and price * size + self.taker_fee(token_id, size, price) > (
                self._cash - held_usdc):
            return reject("not enough balance / allowance")
        if not is_buy and size > self._held(token_id) - held_tokens.get(token_id, Decimal(0)):
            return reject("not enough balance / allowance")
        order = {"order_id": f"pm-{self._next_oid}", "client_id": client_id,
                 "token_id": token_id, "is_buy": is_buy, "price": price, "size": size,
                 "remaining": size, "filled": Decimal(0), "notional": Decimal(0)}
        self._next_oid += 1
        bid, ask = self._best(token_id)
        if is_buy and price >= ask:
            self._fill(order, size, ask, taker=True)
        elif not is_buy and price <= bid:
            self._fill(order, size, bid, taker=True)
        if order["remaining"]:
            self._orders[order["order_id"]] = order
        self._all_orders[order["order_id"]] = order
        self._client_results[client_id] = {"order_id": order["order_id"]}
        return self._result(order)

    def cancel(self, *, client_id: str, order_id: str) -> dict[str, Any]:
        """Cancel one resting order; a repeat returns the first answer."""
        if client_id in self._client_results:
            return dict(self._client_results[client_id])
        order = self._orders.pop(order_id, None)
        if order is None:
            result = {"order_id": order_id, "status": "rejected",
                      "error": "order is not resting"}
        else:
            order["cancelled"] = True
            result = {"order_id": order_id, "status": "cancelled", "error": None,
                      "filled_size": str(order["filled"])}
        self._client_results[client_id] = result
        return dict(result)

    def lookup(self, client_id: str) -> dict[str, Any]:
        """What the venue holds under a client id; unknown means it never arrived."""
        known = self._client_results.get(client_id)
        if known is None:
            return {"order_id": None, "status": "rejected", "filled_size": "0",
                    "avg_px": None, "error": "no order under this client id"}
        if "status" in known:
            return dict(known)  # a rejection or a cancellation answers as it did
        return self._result(self._all_orders[known["order_id"]])

    # ---- time

    @staticmethod
    def _payout(market: dict, side: int) -> Decimal:
        if market["winner"] is None:
            return Decimal("0.5")
        return Decimal(1) if market["winner"] == side else Decimal(0)

    def advance(self, now_ns: int) -> list[dict[str, Any]]:
        """Move every open market one step, fill what crossed, resolve what is due.

        Returns the events since the last call, in the order they happened:
        ``fill``, ``cancelled`` (an order a resolution removed) and ``resolution``
        (one per token a position was held in, with the payout per token).
        """
        if self._started_ns is None:
            self._started_ns = now_ns
            for seed_market in self.markets:
                after = seed_market.get("resolves_after_s")
                if after is not None and seed_market["market_id"] not in self.resolutions:
                    self._drawn[str(seed_market["market_id"])] = (
                        now_ns + int(after) * 1_000_000_000)
        if now_ns > self._now_ns:
            self._now_ns = now_ns
            for market in self._markets.values():
                if market["closed"]:
                    continue
                move = self.tick * self.step_ticks * self._rng.choice((-1, 0, 1))
                market["mid"] = min(1 - 2 * self.tick, max(2 * self.tick, market["mid"] + move))
            for order in list(self._orders.values()):
                bid, ask = self._best(order["token_id"])
                if (order["is_buy"] and ask <= order["price"]) or (
                        not order["is_buy"] and bid >= order["price"]):
                    self._fill(order, order["remaining"], order["price"], taker=False)
                    self._orders.pop(order["order_id"], None)
            for market_id, (at_ns, winner) in sorted(self.resolutions.items()):
                market = self._markets.get(market_id)
                if market is not None and not market["closed"] and now_ns >= at_ns:
                    self._resolve(market, winner)
            for market_id, at_ns in sorted(self._drawn.items()):
                market = self._markets[market_id]
                if not market["closed"] and now_ns >= at_ns:
                    self._resolve(market, 0 if Decimal(str(self._rng.random())) < market[
                        "mid"] else 1)
        return self.drain_events()

    def drain_events(self) -> list[dict[str, Any]]:
        """The events not yet handed over, oldest first; each is handed over once."""
        events, self._events = self._events, []
        return events

    def _resolve(self, market: dict, winner: int | None) -> None:
        market["closed"], market["winner"] = True, winner
        market["resolved_at_ns"] = self._now_ns
        for order in [o for o in self._orders.values() if o["token_id"] in market["tokens"]]:
            order["cancelled"] = True
            self._orders.pop(order["order_id"])
            self._events.append({"kind": "cancelled", "order_id": order["order_id"],
                                 "token_id": order["token_id"],
                                 "market_id": market["market_id"], "ts_ns": self._now_ns})
        for side, token in enumerate(market["tokens"]):
            position = self._positions.get(token)
            payout = self._payout(market, side)
            if position is None or position["size"] <= 0:
                continue
            size = position["size"]
            realized = (payout - position["avg_px"]) * size
            self._cash += payout * size
            position["size"] = Decimal(0)
            self._events.append({
                "kind": "resolution", "market_id": market["market_id"],
                "condition_id": market["condition_id"], "token_id": token,
                "outcome_index": side, "outcome_name": market["outcomes"][side],
                "payout": str(payout),
                "size": str(size), "realized_usd": str(realized), "ts_ns": self._now_ns})


# --- the live seam (phase 2) ------------------------------------------------------------

class LiveOrderAdapter:
    """The marked seam for live Polymarket orders. Not built: every method refuses.

    Phase 2 replaces this with an adapter that has the same ``place``, ``cancel``,
    ``lookup``, ``account`` and ``advance`` contract as ``FakePolymarket``. What it
    needs, none of which exists in this repository (facts as of CLOB V2,
    2026-04-28):

    * **Custody.** A Polygon PoS (chain id 137) wallet as the ``polymarket`` pot,
      holding pUSD, Polymarket's USDC-backed collateral token
      (``0xC011a7E12a19f7B1f670d46F03B03f3342E82DFB``, 6 decimals), plus a little
      POL for approvals and redemptions. USDC.e is wrapped into pUSD through the
      CollateralOnramp (https://docs.polymarket.com/concepts/pusd). The key lives
      in the operator's secret store beside the others; nothing here reads it. No
      treasury route funds this pot yet: until one exists the operator funds it,
      and the pot never borrows Hyperliquid or Base collateral.
    * **Signature type.** 0 (a plain EOA) needs Polymarket's allowlisting; 3 (a
      Deposit Wallet, ERC-1271) is the default for accounts made since 2026-05-04
      (https://docs.polymarket.com/trading/wallets-auth).
    * **Allowances.** Once per wallet: pUSD ``approve`` and the CTF's
      ``setApprovalForAll`` for the CTF Exchange
      (``0xE111180000d2663C0091e4f400237545B87B996B``) and the Neg Risk CTF
      Exchange (``0xe2222d279d744050d28e00520010520000310F59``), and the collateral
      adapters for redemption (https://docs.polymarket.com/resources/contracts).
    * **Credentials.** CLOB API credentials (key, secret, passphrase) from an L1
      EIP-712 ``ClobAuth`` signature (``POST /auth/api-key`` or
      ``GET /auth/derive-api-key``); every trading request then carries L2 HMAC
      headers.
    * **Signing.** Each order is an EIP-712 V2 ``Order`` signed under the
      "Polymarket CTF Exchange" version 2 domain, with the market's tick size and
      neg-risk flag. ``py-clob-client`` is archived and V1 clients no longer work
      against production; the maintained options are ``polymarket-client`` (the
      official SDK) and ``py-clob-client-v2``. Adding either is a dependency
      decision AGENTS.md requires a written reason for, and both pull in more than
      ``eth_account``, which this repository already has.
    * **Identity.** The CLOB takes no client order id. The durable identity is the
      order's own hash, fixed by its fields and salt before submission: the adapter
      derives the salt from the runtime's client id, ledgers the hash with the
      intent, and recovers an uncertain submission by looking the hash up instead
      of resubmitting.
    * **Settlement.** Resolution is proposed and, if disputed, decided through
      UMA's Optimistic Oracle (a two-hour challenge window; days if it reaches a
      DVM vote). The adapter reads it from Gamma (``closed``, ``outcomePrices``,
      ``umaResolutionStatus``), then redeems with ``redeemPositions`` through the
      collateral adapter, an on-chain transaction the pot pays gas for. Only the
      redemption receipt is the pot's pUSD.
    * **Access.** Polymarket's geographic restrictions
      (https://docs.polymarket.com/api-reference/geoblock) apply to the operator's
      jurisdiction; the operator confirms eligibility.
    """

    name = "polymarket-live"
    deterministic = False
    REFUSAL = "live Polymarket orders are not built in this phase"

    def place(self, **_: Any) -> dict[str, Any]:
        raise PolymarketRefused(self.REFUSAL)

    def cancel(self, **_: Any) -> dict[str, Any]:
        raise PolymarketRefused(self.REFUSAL)

    def lookup(self, client_id: str) -> dict[str, Any]:
        raise PolymarketRefused(self.REFUSAL)
