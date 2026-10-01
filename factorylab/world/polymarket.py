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

Two parts, in the order the runtime needs them (live orders are
``world/polymarket_clob.py``, ``LivePolymarket``):

``PolymarketReader``
    Plain HTTPS GETs against the two public, unauthenticated APIs: the Gamma
    market-metadata API and the CLOB's public book and price endpoints. Bounded
    timeout, bounded body, no redirects, and no response body in any error.
``FakePolymarket``
    A deterministic venue with seeded markets, a moving book, resting orders
    that fill, a collateral pot, and scripted resolution. It answers the same
    reads as the reader, and its writes are the contract the live order venue keeps.

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
from decimal import ROUND_DOWN, Decimal
from typing import Any
from urllib import error, parse, request

from factorylab.world import polymarket_wire as wire
from factorylab.world.polymarket_wire import (  # noqa: F401 - the readers' public names
    MAX_DESCRIPTION_CHARS,
    MAX_QUESTION_CHARS,
    MAX_SOURCE_CHARS,
    PolymarketRefused,
    PolymarketUnavailable,
    _decimal,
    market_detail,
    parse_book,
    parse_market,
    parse_search,
)

GAMMA_URL = "https://gamma-api.polymarket.com"
CLOB_URL = "https://clob.polymarket.com"

#: A read either answers inside this or is unavailable this call. The CLOB and Gamma
#: answer in well under a second; ten seconds is the connector's own bound.
HTTP_TIMEOUT_S = 10
#: The most bytes one read may bring into the process. A Gamma page of twenty
#: markets is about 150 KB; a book is a few KB.
MAX_BODY_BYTES = 2 * 1024 * 1024


#: The tick sizes the CLOB publishes (market-details). A market states its own in
#: ``orderPriceMinTickSize`` and the book's ``tick_size``; that is what is enforced.
TICK_SIZES = tuple(Decimal(t) for t in ("0.1", "0.01", "0.005", "0.0025", "0.001", "0.0001"))



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
#: reads here reach /public-search, /markets and /book, and every claim's token lookup
#: lands on /markets, so the tightest endpoint a request can land on is Gamma
#: /markets: 300 per sliding 10 s. Every Polymarket budget here is per sliding 10 s,
#: the window Polymarket itself counts (a per-minute budget let 16 seats burst far
#: past 300 within one 10 s).
PUBLISHED_REQUESTS_PER_10S = 300
#: What the world's Polymarket reads may use by default, all together: two thirds of
#: the tightest published limit, 200 per 10 s. The IP of the host a world runs on is
#: dedicated to that factory (one live Polymarket world a host, ``ip_lock``), so no
#: share of the limit is left for other tenants. Half (150) would leave each of 16
#: seats 3 requests per 10 s, one claim's token lookup, but a judge's one return may
#: carry ``max_forecasts_per_verdict`` (2) claims, 6 requests, so the default is the
#: least budget that fits a whole return: (200 - 100) // 16 = 6. The world counts
#: every request at a wall-clock stamp taken just before it is sent (``PolymarketReader.
#: drain_sends``), in the window Polymarket counts, so its bound (196 of 300,
#: ``runtime/polymarket.py``, ``open_limit``) holds in wall time; what remains of the
#: 300 covers only the difference between this host's clock and Polymarket's, and a
#: request's time in flight between its stamp and its arrival.
DEFAULT_READ_REQUESTS_PER_10S = 200
#: Of that, held back for the kernel's own settlement reads, which no seat can spend:
#: N = 100 // 2 = 50 open reads, 3 a seat at 16 slots.
DEFAULT_KERNEL_RESERVE_PER_10S = 100
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
    #: The wall clock every request is stamped with just before it is sent
    #: (``drain_sends``).
    wall: Any = time.time_ns
    #: The stamps of the requests sent since the last ``drain_sends``.
    sends: list = field(default_factory=list)

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
        self._stamp()
        return self.get(f"{self.gamma_url}{path}?{parse.urlencode(fresh)}")

    def _clob(self, path: str, **params: Any) -> Any:
        self._stamp()
        return self.get(f"{self.clob_url}{path}?{parse.urlencode(params)}")

    def _stamp(self) -> None:
        """Guarantees the request about to be sent is counted and stamped with the wall
        clock now, before it is sent: it counts from this stamp on, while it is in
        flight, and whether it then fails, times out or is answered."""
        # Before, not after: a request that fails in flight may still have reached
        # Polymarket, so it counts.
        self.sent = getattr(self, "sent", 0) + 1
        self.sends.append(int(self.wall()))

    def requests_sent(self) -> int:
        """Guarantees the count of every request this reader has sent, monotone. A read
        of its own counter, journaled read-only, so a replay charges what was sent."""
        return getattr(self, "sent", 0)

    def drain_sends(self) -> list[int]:
        """Guarantees the wall-clock stamp (``wall``, read just before the request was
        sent) of every request this reader began to send since the last drain, one per
        request, in send order, each returned once. A request in flight is included,
        as is one that failed or timed out: each counts from its stamp on.

        Polymarket counts its limits in wall time, so the world charges each request at
        its stamp, which is no later than the instant it left this host. Read through
        the journal, so a replay charges the stamps the run read.
        """
        sends, self.sends = self.sends, []
        return sends

    def wall_ns(self) -> int:
        """The wall clock this reader stamps its requests with, now. Read through the
        journal, so a replay reads the instant the run read."""
        return int(self.wall())

    def search_markets(self, query: str, limit: int) -> list[dict[str, Any]]:
        """Markets matching ``query`` through Gamma's public search, best ranked first."""
        raw = self._gamma("/public-search", q=query, limit_per_type=limit,
                          search_profiles="false", search_tags="false")
        return wire.read_search(raw, limit)

    def market(self, market_id: str) -> dict[str, Any]:
        """One market's contract and rules text, by Gamma market id, as the origin holds
        it at the read (never the shared cache's copy: ``CACHE_KEY``)."""
        return wire.read_market(
            self._gamma(f"/markets/{parse.quote(market_id, safe='')}"), market_id)

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
            found = wire.read_market_of_token(
                self._gamma("/markets", clob_token_ids=token_id, closed=closed), token_id)
            if found is not None:
                return found
        return None

    def order_book(self, token_id: str, depth: int) -> dict[str, Any]:
        """The CLOB's book summary for one outcome token, best first on both sides."""
        return wire.read_book(self._clob("/book", token_id=token_id), depth, token_id)

    def midpoint(self, token_id: str) -> str | None:
        """The CLOB's midpoint for one outcome token, as a decimal string."""
        return wire.read_midpoint(self._clob("/midpoint", token_id=token_id))


#: ROUNDING_CONFIG of the official clients: decimals of price, size and USD amount per tick.
ROUNDING = {
    "0.1": (1, 2, 3), "0.01": (2, 2, 4), "0.005": (3, 2, 5),
    "0.0025": (4, 2, 6), "0.001": (3, 2, 5), "0.0001": (4, 2, 6),
}


def amount_refusal(size: Decimal, price: Decimal, tick: Decimal) -> str | None:
    """Why a limit order of ``size`` tokens at ``price`` is not one the exchange would
    read exactly, or None. The one amount rule of every venue kind (Sol P2, round 5, on
    #177: the simulated venue took a size the live one refuses): a price off the tick or
    outside (0, 1), a size not positive or past its decimals, a notional past its."""
    rounding = ROUNDING.get(format(tick.normalize(), "f"))
    if rounding is None:
        return f"tick size {tick} is not one Polymarket publishes"
    _price_places, size_places, amount_places = rounding
    if not tick <= price <= 1 - tick or price % tick:
        return f"price is not on the market's {tick} tick inside (0, 1)"
    if size <= 0 or size != size.quantize(Decimal(1).scaleb(-size_places), rounding=ROUND_DOWN):
        return f"size must be positive with at most {size_places} decimals"
    usd = price * size
    if usd != usd.quantize(Decimal(1).scaleb(-amount_places), rounding=ROUND_DOWN):
        return f"notional has more than {amount_places} decimals"
    return None


# --- the simulated venue ------------------------------------------------------------------

#: The seeded listing. Questions are neutral placeholders on purpose: the fake is a
#: venue for plumbing and accounting, not a source of claims about the world.
#: ``resolves_after_s`` schedules a resolution that long after the venue's first
#: step, its answer drawn at that moment with the market's own price as the
#: probability of "Yes", so the simulated market is calibrated by construction.
DEFAULT_FAKE_MARKETS = (
    {"market_id": "fake-1", "question":
         "Will simulated event A occur by its scripted resolution time?",
     "outcomes": ("Yes", "No"), "mid": "0.40", "resolves_after_s": 900},
    {"market_id": "fake-2", "question":
         "Will simulated event B occur by its scripted resolution time?",
     "outcomes": ("Yes", "No"), "mid": "0.70", "resolves_after_s": 3600},
    {"market_id": "fake-3", "question":
         "Will simulated event C occur by its scripted resolution time?",
     "outcomes": ("Yes", "No"), "mid": "0.15", "resolves_after_s": None},
)


@dataclass
class FakePolymarket:
    """A deterministic event-market venue for simulated worlds.

    Guarantees: identical ``seed``, ``markets`` and ``resolutions`` produce
    identical books, fills and resolutions in the same call order. Prices live on
    a ``tick`` grid strictly inside (0, 1); an outcome's two tokens price to 1.
    The book is one tick either side of the mid, ``depth_shares`` deep a level.
    It takes BUY orders only, as the factory's Polymarket venue does: a position is
    held to its resolution. Every order is post-only, as the live venue's are: a buy
    at or above the best ask would cross, and it is rejected before it executes; any
    other rests and fills at its own price once the moving mid crosses it. A resting
    buy holds ``price * size`` USDC. A maker pays no fee, so no fill here is charged
    one.
    At a scripted resolution every resting order on the market is cancelled and every
    token held stays in the pot, resolved, worth its payout: 1 USDC a winning token, 0
    a losing one, 0.5 each on a 50-50 answer. As on the live venue, nothing redeems it
    into spendable USDC. Client ids are idempotent: repeating one returns
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
            # The listing's own statement, in the live listing's shape: the simulated
            # venue has no takers, so it states no fee schedule.
            "fees": {"enabled": False, "rate": "0", "exponent": "1", "taker_only": True},
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

    # ---- account

    def _holds(self) -> Decimal:
        """USDC held by resting buys."""
        return sum((o["price"] * o["remaining"] for o in self._orders.values()), Decimal(0))

    def account(self) -> dict[str, Any]:
        """The pot as a custodian would state it: USDC, what orders hold, tokens held."""
        held_usdc = self._holds()
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
                 "available": str(p["size"]),
                 **({"payout": str(p["payout"])} if "payout" in p else {})}
                for token, p in sorted(self._positions.items()) if p["size"] > 0],
            "open_orders": [self._order_view(o) for o in self._orders.values()],
            "observed_at_ns": self._now_ns,
        }

    @staticmethod
    def _order_view(order: dict) -> dict[str, Any]:
        return {"order_id": order["order_id"], "token_id": order["token_id"],
                "side": "buy", "price": str(order["price"]),
                "size": str(order["size"]), "remaining": str(order["remaining"])}

    # ---- writes

    def _fill(self, order: dict, size: Decimal, px: Decimal) -> None:
        token = order["token_id"]
        position = self._positions.setdefault(token, {"size": Decimal(0),
                                                      "avg_px": Decimal(0)})
        total = position["size"] + size
        position["avg_px"] = (position["size"] * position["avg_px"] + size * px) / total
        position["size"] = total
        self._cash -= px * size
        order["remaining"] -= size
        order["filled"] += size
        order["notional"] += px * size
        self._events.append({
            "kind": "fill", "order_id": order["order_id"], "token_id": token,
            "market_id": self._tokens[token][0], "is_buy": True,
            "size": str(size), "px": str(px), "fee_usd": "0",
            "realized_usd": "0", "ts_ns": self._now_ns})

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
            # The venue's refusal of the submission, as the live venue's documented 4xx:
            # the order never existed.
            result = {"order_id": None, "status": "rejected", "filled_size": "0",
                      "avg_px": None, "error": reason, "venue_refused": True}
            self._client_results[client_id] = result
            return dict(result)

        if not is_buy:
            return reject("the venue takes BUY orders only")
        listed = self._tokens.get(token_id)
        if listed is None:
            return reject("unknown token")
        market = self._markets[listed[0]]
        if market["closed"]:
            return reject("market is closed")
        refused = amount_refusal(size, price, self.tick)
        if refused is not None:
            return reject(refused)
        if size < self.min_order_size:
            return reject(f"size below the minimum order of {self.min_order_size}")
        if price >= self._best(token_id)[1]:
            # Post-only, as the live venue's orders are (docs, error-codes): an order
            # that would cross is rejected before it executes, never filled as a taker.
            return reject("invalid post-only order: order crosses book")
        if price * size > self._cash - self._holds():
            return reject("not enough balance / allowance")
        order = {"order_id": f"pm-{self._next_oid}", "client_id": client_id,
                 "token_id": token_id, "is_buy": is_buy, "price": price, "size": size,
                 "remaining": size, "filled": Decimal(0), "notional": Decimal(0)}
        self._next_oid += 1
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
                _bid, ask = self._best(order["token_id"])
                if ask <= order["price"]:
                    self._fill(order, order["remaining"], order["price"])
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
            # The payout stays in custody as resolved tokens, worth their payout, as on
            # the live venue: nothing redeems them into spendable cash (Sol P2, round 6,
            # on #177).
            position["payout"] = payout
            self._events.append({
                "kind": "resolution", "market_id": market["market_id"],
                "condition_id": market["condition_id"], "token_id": token,
                "outcome_index": side, "outcome_name": market["outcomes"][side],
                "payout": str(payout),
                "size": str(size), "realized_usd": str(realized), "ts_ns": self._now_ns})
