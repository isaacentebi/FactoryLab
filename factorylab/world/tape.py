"""A recorded market: a past paid run's diary, replayed as this world's venue.

A tape is the world, not architecture (AGENTS.md: "a venue, a market or a data
source is the world"). It is what a paid run's seats were actually shown: the mids,
the funding rates and the instants its ticks were delivered at, plus whatever order
books and instrument listing that run happened to read. Nothing else is invented.

Chapter II §II.b (physics is enforced, not announced): the venue built on a tape is
a ``FakeExchange`` and stays one, so every simulated-world guarantee holds -- the
deterministic journal, the fake treasury, no live adapter, no live rail and no
real-money branch. What changes is where its prices come from: a function of time,
read strictly at or before the venue's own instant, never a step counter.
"""

from __future__ import annotations

import bisect
import hashlib
import json
import re
from collections.abc import Iterator
from dataclasses import dataclass, field, replace
from decimal import Decimal
from pathlib import Path
from typing import Any

from factorylab.world.events import WorldEvent, WorldEventKind
from factorylab.world.exchange import (
    MIN_ORDER_VALUE_USD,
    NS_PER_HOUR,
    FakeExchange,
    FundingEvent,
    FundingPayment,
    Order,
    OrderKind,
    OrderResult,
    _check_count,
)

#: The compact tape's format tag. A tape file carries it; a diary does not.
TAPE_FORMAT = "factorylab-tape/1"

#: Hyperliquid's published base-tier fee schedule, as fractions of notional (taker,
#: maker), for a diary whose recorded instrument listing did not state the account's
#: own rates. Recorded rates always win. The perp taker rate is the one longrun1's
#: recorded fills were charged (0.03814 USD on 84.757 USD of notional).
PUBLISHED_FEES = {"perp": ("0.00045", "0.00015"), "spot": ("0.0007", "0.0004")}

#: Why an order is refused once the recording has ended (published, never advice).
MARKET_ENDED = "the recorded market has ended"
#: The termination reason of a world whose paced clock reached its tape's end.
TAPE_ENDED = "tape_ended"

#: Why an order on a market the tape recorded no liquidity for is refused.
NO_LIQUIDITY = "the tape recorded no liquidity for this market"

#: The fake venue's own spread, used only for a coin no recorded book ever priced
#: (and no other coin's book either). The tape says so: ``spread_source`` "assumed".
ASSUMED_SPREAD_BPS = Decimal(2)


# --------------------------------------------------------------------------- reading


def _decode(value: Any) -> Any:
    """The recorded-I/O encoding (``runtime.resume.encode``) for the shapes a venue answers.

    Only data comes back: a mapping, a list, a decimal as its exact text, a record
    as the mapping of its fields. The world package does not import the runtime.
    """
    if isinstance(value, list):
        return [_decode(v) for v in value]
    if not isinstance(value, dict):
        return value
    if "$decimal" in value:
        return value["$decimal"]
    if "$map" in value:
        return {_decode(k): _decode(v) for k, v in value["$map"]}
    if "$tuple" in value:
        return [_decode(v) for v in value["$tuple"]]
    if "$record" in value:
        return {k: _decode(v) for k, v in value["fields"].items()}
    if "$float" in value:
        return value["$float"]
    return {k: _decode(v) for k, v in value.items()}


def _array_items(path: Path) -> Iterator[dict]:
    """The items of a diary written as one JSON array (``events.json``), one at a time.

    A paid run's diary is hundreds of megabytes; only a few thousand of its items
    are market data, so the array is read incrementally rather than held whole.
    """
    decoder = json.JSONDecoder()
    with open(path, encoding="utf-8") as handle:
        buffer, pos, done = handle.read(1 << 20), 0, False

        def skip() -> None:
            nonlocal pos
            while pos < len(buffer) and buffer[pos] in " \t\r\n,":
                pos += 1

        skip()
        if buffer[pos:pos + 1] != "[":
            raise ValueError(f"{path} is not a JSON array of diary items")
        pos += 1
        while True:
            skip()
            if pos >= len(buffer) and not done:
                more = handle.read(1 << 20)
                done = not more
                buffer, pos = buffer[pos:] + more, 0
                continue
            if buffer[pos:pos + 1] == "]":
                return
            try:
                item, end = decoder.raw_decode(buffer, pos)
            except json.JSONDecodeError:
                if done:
                    raise
                more = handle.read(1 << 22)
                done = not more
                buffer, pos = buffer[pos:] + more, 0
                continue
            pos = end
            yield item


_CALL = re.compile(r'^\{"call": (\d+)')


def _split_items(directory: Path) -> Iterator[dict]:
    """The market items of a diary split into one ``<kind>.jsonl`` file per kind.

    ``io.result.jsonl`` runs to hundreds of megabytes of recorded answers; only the
    answers to the venue reads a tape keeps are parsed.
    """
    for name in ("Launch", "Tick", "MarketMid", "Funding"):
        path = directory / f"event_{name}.jsonl"
        if path.exists():
            with open(path, encoding="utf-8") as handle:
                yield from (json.loads(line) for line in handle if line.strip())
    calls: dict[int, dict] = {}
    path = directory / "io.call.jsonl"
    if path.exists():
        with open(path, encoding="utf-8") as handle:
            for line in handle:
                item = json.loads(line)
                if item.get("name") in _KEPT_READS:
                    calls[item["seq"]] = item
                    yield item
    path = directory / "io.result.jsonl"
    if calls and path.exists():
        with open(path, encoding="utf-8") as handle:
            for line in handle:
                match = _CALL.match(line)
                if match and int(match.group(1)) in calls:
                    yield json.loads(line)


#: The recorded venue reads a tape keeps besides its events.
_KEPT_READS = ("exchange.instruments", "exchange.order_book")


def _nonzero(text: Any) -> bool:
    try:
        return Decimal(str(text)) != 0
    except ArithmeticError:
        return True


def cut(path: str | Path) -> dict:
    """The compact tape of a diary: ``events.json`` or a directory split by kind.

    Guarantees the tape holds only what the diary recorded, for the markets its
    Launch manifest named: the delivered tick stamps; each market's mids and each
    perp's funding-rate observations, stamped as delivered; the recorded order books,
    stamped with the venue's own book time; and the first recorded instrument listing.
    A funding row that moved money (``paid_usd`` non-zero) is an account payment of the
    run that recorded it, not market data, and is left out: a replay's payments are
    computed from its own positions (Chapter II §II.b).
    """
    path = Path(path)
    items = _split_items(path) if path.is_dir() else _array_items(path)
    manifest: dict = {}
    venue = None
    ticks: list[int] = []
    mids: dict[str, dict[int, str]] = {}
    funding: dict[str, dict[int, list]] = {}
    books: dict[str, dict[int, list]] = {}
    instruments = None
    names: dict[int, str] = {}
    for item in items:
        kind = item.get("kind")
        if kind == "io.call":
            names[item["seq"]] = item.get("name")
            continue
        if kind == "io.result":
            name = names.get(item.get("call"))
            if "result" not in item:
                continue  # an answer kept beside its diary: not part of this cut
            if name == "exchange.instruments" and instruments is None:
                instruments = _decode(item["result"])
            elif name == "exchange.order_book":
                book = _decode(item["result"])
                if isinstance(book, dict) and book.get("ts_ns"):
                    books.setdefault(book["coin"], {})[int(book["ts_ns"])] = [
                        [[str(level["price"]), str(level["size"])] for level in book[side]]
                        for side in ("bids", "asks")]
            continue
        if kind != "event":
            continue
        event = item.get("event") or {}
        payload = event.get("payload") or {}
        ts = int(event.get("ts_ns") or 0)
        if event.get("kind") == "Launch":
            manifest = payload.get("manifest") or {}
        elif event.get("kind") == "Tick":
            ticks.append(ts)
        elif event.get("kind") == "MarketMid":
            venue = venue or event.get("source")
            mids.setdefault(str(payload["coin"]), {})[ts] = str(payload["mid"])
        elif event.get("kind") == "Funding" and not _nonzero(payload.get("paid_usd", "0")):
            funding.setdefault(str(payload["coin"]), {})[ts] = [
                str(payload["rate"]),
                None if payload.get("premium") is None else str(payload["premium"])]
    exchange = manifest.get("exchange") or {}
    markets = tuple(exchange.get("coins") or ()) + tuple(exchange.get("spot_pairs") or ())
    kept = [m for m in (markets or sorted(mids)) if m in mids]
    if not ticks or not kept:
        raise ValueError(f"{path} recorded no ticks or no mids for its markets")
    listing = None
    if isinstance(instruments, dict):
        listing = {market: [row for row in rows if isinstance(row, dict)
                            and row.get("coin") in kept]
                   for market, rows in instruments.items() if isinstance(rows, list)}
    return {
        "format": TAPE_FORMAT,
        "venue": venue,
        "declared_tick_ns": manifest.get("tick_interval_ns"),
        "ticks": sorted(set(ticks)),
        "mids": {c: [[ts, px] for ts, px in sorted(mids[c].items())] for c in kept},
        "funding": {c: [[ts, *row] for ts, row in sorted(funding[c].items())]
                    for c in kept if c in funding},
        "books": {c: [[ts, *sides] for ts, sides in sorted(books[c].items())]
                  for c in kept if c in books},
        "instruments": listing,
    }


def canonical_bytes(data: dict) -> bytes:
    """The tape's identity bytes: sorted keys, no whitespace, exact decimal text."""
    return json.dumps(data, sort_keys=True, separators=(",", ":")).encode()


def _median(values: list[Decimal]) -> Decimal:
    ordered = sorted(values)
    mid = len(ordered) // 2
    return ordered[mid] if len(ordered) % 2 else (ordered[mid - 1] + ordered[mid]) / 2


@dataclass(frozen=True)
class Tape:
    """A compact tape and its SHA-256, indexed for reads at or before an instant.

    Guarantees every read answers from the latest recorded row at or before the
    instant asked, and ``None`` before a series' first row: the tape never answers
    from the future and never wraps around to its start.
    """

    data: dict
    sha256: str
    _index: dict = field(default_factory=dict, repr=False, compare=False)

    @classmethod
    def from_data(cls, data: dict) -> Tape:
        if data.get("format") != TAPE_FORMAT:
            raise ValueError("not a factorylab tape")
        tape = cls(data, hashlib.sha256(canonical_bytes(data)).hexdigest())
        for series in ("mids", "funding", "books"):
            for coin, rows in data.get(series, {}).items():
                tape._index[(series, coin)] = [int(row[0]) for row in rows]
        return tape

    @classmethod
    def load(cls, path: str | Path) -> Tape:
        """A compact tape file, or the tape cut from a diary (``cut``)."""
        path = Path(path)
        if path.is_file():
            with open(path, encoding="utf-8") as handle:
                head = handle.read(64).lstrip()
            if head.startswith("{"):  # a tape is one object; a diary is an array
                return cls.from_data(json.loads(path.read_text()))
        return cls.from_data(cut(path))

    def write(self, path: str | Path) -> None:
        Path(path).write_bytes(canonical_bytes(self.data) + b"\n")

    # ---- identity

    @property
    def ticks(self) -> list[int]:
        return self.data["ticks"]

    @property
    def start_ns(self) -> int:
        return int(self.ticks[0])

    @property
    def end_ns(self) -> int:
        return int(self.ticks[-1])

    @property
    def markets(self) -> tuple[str, ...]:
        return tuple(self.data["mids"])

    @property
    def perps(self) -> tuple[str, ...]:
        return tuple(m for m in self.markets if "/" not in m)

    @property
    def pairs(self) -> tuple[str, ...]:
        return tuple(m for m in self.markets if "/" in m)

    # ---- reads at or before an instant

    def _row(self, series: str, coin: str, ts_ns: int) -> list | None:
        stamps = self._index.get((series, coin))
        if not stamps:
            return None
        i = bisect.bisect_right(stamps, ts_ns) - 1
        return None if i < 0 else self.data[series][coin][i]

    def mid_at(self, coin: str, ts_ns: int) -> tuple[int, Decimal] | None:
        row = self._row("mids", coin, ts_ns)
        return None if row is None else (int(row[0]), Decimal(row[1]))

    def funding_at(self, coin: str, ts_ns: int) -> tuple[int, Decimal, Decimal | None] | None:
        row = self._row("funding", coin, ts_ns)
        if row is None:
            return None
        return int(row[0]), Decimal(row[1]), None if row[2] is None else Decimal(row[2])

    def book_at(self, coin: str, ts_ns: int) -> tuple[int, list, list] | None:
        row = self._row("books", coin, ts_ns)
        if row is None:
            return None
        bids, asks = ([(Decimal(px), Decimal(sz)) for px, sz in side] for side in row[1:])
        return int(row[0]), bids, asks

    # ---- the venue's published terms

    def instrument_rows(self, market: str) -> list[dict]:
        listing = self.data.get("instruments") or {}
        return [dict(row) for row in listing.get(market, [])]

    def fees(self, market: str) -> tuple[Decimal, Decimal, str]:
        """(taker, maker, source): the recorded account rates, else the published ones."""
        for row in self.instrument_rows(market):
            if "taker_fee_rate" in row and "maker_fee_rate" in row:
                return (Decimal(str(row["taker_fee_rate"])),
                        Decimal(str(row["maker_fee_rate"])), "recorded")
        taker, maker = PUBLISHED_FEES[market]
        return Decimal(taker), Decimal(maker), "published"

    def _book_spreads_bps(self, coin: str) -> list[Decimal]:
        out = []
        for row in self.data.get("books", {}).get(coin, []):
            bids, asks = row[1], row[2]
            if bids and asks:
                bid, ask = Decimal(bids[0][0]), Decimal(asks[0][0])
                if ask > bid > 0:
                    out.append((ask - bid) / ((ask + bid) / 2) * 10_000)
        return out

    def spread_bps(self, coin: str) -> tuple[Decimal, str]:
        """(bps, source): the median recorded top-of-book spread for ``coin``; else the
        median over every recorded book on the tape; else the fake's own, ``assumed``."""
        own = self._book_spreads_bps(coin)
        if own:
            return _median(own).quantize(Decimal("0.0001")), "recorded"
        every = [s for c in self.data.get("books", {}) for s in self._book_spreads_bps(c)]
        if every:
            return _median(every).quantize(Decimal("0.0001")), "recorded_other_markets"
        return ASSUMED_SPREAD_BPS, "assumed"

    def level_size(self, market: str, ts_ns: int | None = None) -> Decimal | None:
        """The size of one synthetic level of ``market`` (``TapeVenue._book``); never unbounded.

        The median top-of-book size the recorded books showed for ``market`` itself;
        else the smallest-notional top-of-book level recorded for any market on the
        tape, converted to ``market``'s units at its mid at or before ``ts_ns`` (the
        tape's first instant when None); else None: the tape recorded no liquidity for
        this market, and an order on it is refused.
        """
        sizes = [Decimal(side[0][1]) for row in self.data.get("books", {}).get(market, [])
                 for side in (row[1], row[2]) if side]
        if sizes:
            return _median(sizes)
        notionals = [Decimal(side[0][0]) * Decimal(side[0][1])
                     for rows in self.data.get("books", {}).values() for row in rows
                     for side in (row[1], row[2]) if side]
        mid = self.mid_at(market, self.start_ns if ts_ns is None else ts_ns)
        if not notionals or mid is None or mid[1] <= 0:
            return None
        return min(notionals) / mid[1]

    def depth_source(self, market: str) -> str:
        """Where one synthetic level's size comes from (``level_size``)."""
        if self.data.get("books", {}).get(market):
            return "recorded"
        if any(self.data.get("books", {}).values()):
            return "smallest_recorded_level_on_the_tape"
        return "none"

    def min_order_value(self, market: str) -> Decimal:
        """The venue's order floor for ``market``: the recorded listing's, else Hyperliquid's."""
        kind = "spot" if "/" in market else "perp"
        for row in self.instrument_rows(kind):
            if row.get("coin") == market and row.get("min_order_value_usd") is not None:
                return Decimal(str(row["min_order_value_usd"]))
        return Decimal(MIN_ORDER_VALUE_USD)

    def identity(self) -> dict:
        """What a manifest's ``[exchange.tape]`` must say about this tape, all of it
        derived from the tape itself: its SHA-256, span, markets and stated spreads."""
        return {"sha256": self.sha256, "start_ns": self.start_ns, "end_ns": self.end_ns,
                "markets": tuple(self.markets),
                "spread_bps": {m: str(self.spread_bps(m)[0]) for m in self.markets}}

    def summary(self) -> dict:
        """What a scorecard and a Launch record say about this tape."""
        return {"sha256": self.sha256, "venue": self.data.get("venue"),
                "start_ns": self.start_ns, "end_ns": self.end_ns,
                "hours": round((self.end_ns - self.start_ns) / NS_PER_HOUR, 3),
                "ticks": len(self.ticks), "markets": list(self.markets),
                "books": sum(len(v) for v in self.data.get("books", {}).values())}


# --------------------------------------------------------------------------- the venue


class TapeVenue(FakeExchange):
    """The deterministic fake venue, its prices and funding rates read from a tape.

    Guarantees: every mid, funding rate and candle it answers is the latest recorded
    one at or before its own instant; its instant only moves forward, to the world's
    ticks, which the world's own clock sets (the tape is sampled at the world's tick,
    never the reverse); funding is charged once per hour boundary of tape time on the
    position held at that boundary, at the last recorded rate and mid at or before it,
    and never once per recorded row; past the tape's last row the last row holds and
    the tape never loops. Its name, ``tape:<sha8>``, says what it is.

    Fills are the recording's, never kinder (money path; Chapter II §II.b, the hard
    cast): an order is acknowledged as resting and executes only when the recording
    first shows its market after the instant it was sent, never against the quote
    current when it was sent; the book it meets is the recorded one when that is at
    least as recent as the recorded mid, otherwise one level each side at the mid plus
    or minus half the tape's spread, as deep as the recorded books' median top level
    (unbounded when the tape recorded none); a market order is immediate-or-cancel
    within Hyperliquid's 5% of the mid it was sent at, and what it cannot fill is
    cancelled; a limit that crosses on arrival takes at the book's prices, at the taker
    rate, and rests the remainder; a resting limit fills only when a recorded mid after
    it rested is strictly through its price (a trade-through, never a book level that
    merely sits past it) and the opposite top of book has reached it, at its price,
    at the maker rate, up to what the top level of the book on that side still
    holds; within one tick arriving takers are matched
    before resting makers, as on the venue; liquidity taken from one recorded snapshot
    is not offered again; a synthetic level is never unbounded, and a market the tape
    recorded no liquidity for refuses every order; every order is refused below the
    venue's order floor; funding accrued since the last hour boundary is charged pro
    rata when the world ends (``settle_accrued_funding``). The rules are published with
    the instrument listing (``instruments``) as facts.

    The tape itself is held outside the instance dictionary, so a checkpoint carries
    the venue's state and not a copy of the recording: a resume rebuilds the venue
    from the same tape, whose SHA-256 the world's manifest fixes.
    """

    #: Hyperliquid's market order is an immediate-or-cancel limit this far through the
    #: mid its sender read (the SDK's DEFAULT_SLIPPAGE; ``HyperliquidExchange``).
    MARKET_SLIPPAGE = Decimal("0.05")

    __slots__ = ("_tape",)

    def __init__(self, tape: Tape, *, coins: tuple[str, ...] = ("BTC", "ETH"),
                 spot_pairs: tuple[str, ...] = (), start_cash_usd: Decimal = Decimal("100"),
                 seed: int = 0) -> None:
        missing = [m for m in (*coins, *spot_pairs) if m not in tape.markets]
        if missing:
            raise ValueError(f"the tape recorded no mids for {missing}")
        self._tape = tape
        taker, _maker, _source = tape.fees("perp")
        spread = tape.spread_bps(coins[0] if coins else tape.markets[0])[0]
        start = {m: tape.mid_at(m, tape.start_ns) for m in tape.markets}
        super().__init__(
            name=f"tape:{tape.sha256[:8]}", seed=seed, start_cash_usd=Decimal(start_cash_usd),
            coins=tuple(coins), spot_pairs=tuple(spot_pairs),
            start_prices={m: row[1] for m, row in start.items() if row is not None},
            spread_bps=spread, fee_bps=taker * 10_000,
            listed_coins=tape.perps, listed_spot_pairs=tape.pairs)
        for market, row in start.items():
            if row is not None:
                self._mids[market] = row[1]
                if "/" in market:  # the fake keeps a pair's base at the pair's mid
                    self._mids[market.split("/")[0]] = row[1]
        self._now_ns = tape.start_ns
        self._last_funding_ns = tape.start_ns - tape.start_ns % NS_PER_HOUR
        # Orders sent and not yet arrived: id -> the order, the recorded row its sender
        # read (its instant and mid) and the instant it was sent.
        self._inflight: dict[str, dict] = {}
        # Liquidity taken, by (market, snapshot instant, side, price): what one recorded
        # snapshot offered is offered once.
        self._taken: dict[tuple[str, int, str, str], Decimal] = {}
        # Orders the venue refused on arrival, with the reason it gave.
        self._rejected: dict[str, str] = {}
        # When each resting order began to rest: only a recorded mid after it trades
        # through it.
        self._rested_ns: dict[str, int] = {}
        # Each perp's signed position-hours since the last funding settlement, and the
        # instant they were accrued to: what a partial hour owes when the world ends.
        self._accrued: dict[str, Decimal] = {}
        self._accrued_at = self._last_funding_ns

    @property
    def tape(self) -> Tape:
        return self._tape

    @property
    def tape_sha256(self) -> str:
        return self._tape.sha256

    @property
    def opens_ns(self) -> int:
        """The instant the recording starts: a world on this venue launches there."""
        return self._tape.start_ns

    @property
    def closes_ns(self) -> int:
        """The instant the recording ends: a world on this venue ends there."""
        return self._tape.end_ns

    def _quoted(self) -> tuple[str, ...]:
        return tuple(m for m in dict.fromkeys(
            (*self.coins, *self.listed_coins, *self.spot_pairs, *self.listed_spot_pairs))
            if m in self._tape.markets)

    def advance(self, ts_ns: int) -> list[WorldEvent]:
        """Move the venue to ``ts_ns`` and answer what the tape recorded up to it.

        Returns a ``MarketMid`` per recorded market (its latest row at or before
        ``ts_ns``), a ``Funding`` per perp for each hour boundary crossed (on the
        positions held at it, before anything fills at ``ts_ns``), then the fills and
        refusals of orders that arrived (takers first, as on the venue), then those of
        resting orders a recorded mid traded through, then any liquidation. Guarantees
        time never moves backwards, and never past the recording's end
        (``closes_ns``): an instant after it is the end itself, so nothing is invented
        in time the tape never recorded (no fill, no funding boundary, no mark).
        """
        if ts_ns < self._now_ns:
            raise ValueError("TapeVenue time cannot move backwards")
        ts_ns = min(ts_ns, self._tape.end_ns)
        self._now_ns = ts_ns
        self._step += 1
        events: list[WorldEvent] = []
        for market in self._quoted():
            row = self._tape.mid_at(market, ts_ns)
            if row is None:
                continue
            mid = row[1]
            self._mids[market] = mid
            if "/" in market:
                self._mids[market.split("/")[0]] = mid
            self._mid_history.setdefault(market, []).append((ts_ns, mid))
            events.append(WorldEvent(WorldEventKind.MARKET_MID, ts_ns, self.name,
                                     {"coin": market, "mid": str(mid)}))
        events.extend(self._settle_funding(ts_ns))
        events.extend(self._arrive())
        events.extend(self._cross_resting())
        events.extend(self._liquidate_if_needed())
        if self.__dict__.get("_vaults"):
            self._advance_vaults()
        return events

    def _settle_funding(self, ts_ns: int) -> list[WorldEvent]:
        """One funding settlement per hour boundary of tape time crossed up to ``ts_ns``."""
        events: list[WorldEvent] = []
        boundary = self._last_funding_ns + NS_PER_HOUR
        while boundary <= ts_ns:
            events.extend(self._fund(boundary))
            self._last_funding_ns = boundary
            boundary += NS_PER_HOUR
        return events

    def _accrue(self, ts_ns: int) -> None:
        """Add each open perp position's size times the time it was held, up to ``ts_ns``."""
        ts_ns = min(ts_ns, self._tape.end_ns)  # nothing accrues after the recording
        if ts_ns <= self._accrued_at:
            return
        span = Decimal(ts_ns - self._accrued_at) / NS_PER_HOUR
        for coin, pos in self._positions.items():
            self._accrued[coin] = self._accrued.get(coin, Decimal(0)) + pos.size * span
        self._accrued_at = ts_ns

    def _fill(self, oid, order, px, *, liquidation=False, fee_rate=None):
        # A position changes here: what the old one accrued is counted first.
        self._accrue(self._now_ns)
        return super()._fill(oid, order, px, liquidation=liquidation, fee_rate=fee_rate)

    def _fund(self, boundary: int) -> list[WorldEvent]:
        """The hour's funding on the position held at ``boundary`` (the venue's rule)."""
        # The boundary settles the hour whatever was held inside it: nothing accrued
        # before it is owed again.
        self._accrued, self._accrued_at = {}, boundary
        return self._charge(boundary, {c: p.size for c, p in self._positions.items()},
                            f"{boundary}")

    def _charge(self, instant: int, sizes: dict[str, Decimal], ident: str) -> list[WorldEvent]:
        """Charge ``sizes`` (signed, in position-hours) at the recorded rate and mid."""
        events: list[WorldEvent] = []
        for coin in dict.fromkeys((*self.coins, *self.listed_coins)):
            rate_row = self._tape.funding_at(coin, instant)
            mark_row = self._tape.mid_at(coin, instant)
            if rate_row is None or mark_row is None:
                continue  # no rate recorded yet: nothing is known to charge
            _ts, rate, premium = rate_row
            self._funding_history.append(FundingEvent(coin, rate, premium, instant))
            # Longs pay a positive rate: size times the mark times the rate.
            paid = sizes.get(coin, Decimal(0)) * mark_row[1] * rate
            self._cash -= paid
            self._funding_payments.append(FundingPayment(f"{ident}:{coin}", coin, paid, rate,
                                                         instant))
            events.append(WorldEvent(WorldEventKind.FUNDING, self._now_ns, self.name,
                                     {"coin": coin, "rate": str(rate), "paid_usd": str(paid)}))
        return events

    def settle_accrued_funding(self, ts_ns: int) -> list[WorldEvent]:
        """Charge, at ``ts_ns``, the funding accrued since the last hour boundary.

        Called when the world ends (the tape ran out, the budget did, or a kill). Each
        perp is charged the hour's last recorded rate at or before ``ts_ns`` on the
        position-hours it actually held since the boundary: every position it held,
        for as long as it held it, and nothing for a time it was flat. So a replay never
        leaves a cost accrued and uncharged, and never books a receipt for time it did
        not hold a position. What is charged is cleared: nothing is charged twice.
        """
        ts_ns = min(max(ts_ns, self._now_ns), self._tape.end_ns)
        events = self._settle_funding(ts_ns)
        self._accrue(ts_ns)
        if any(self._accrued.values()):
            events.extend(self._charge(ts_ns, self._accrued, f"{ts_ns}:partial"))
        self._accrued = {}
        return events

    def close_recording(self, ts_ns: int) -> list[WorldEvent]:
        """End the recorded market at ``ts_ns``, when its world ends; its effects, in order.

        The first half of the terminal sequence (the runtime's ``kill``): the funding
        accrued since the last hour boundary is charged (``settle_accrued_funding``);
        every order still in flight can no longer arrive, since no later row will ever
        be recorded, and is cancelled (an immediate-or-cancel order that no counterparty
        ever met), each with an ``OrderRejected`` the runtime settles like any venue
        cancel. From here an order sent (the wind-down's closes) executes at once
        against the last recorded book, by the same depth rules, at the taker rate:
        the recording has nothing later to meet it with. ``seal_recording`` then refuses
        every order. Resting limits stay listed, for the wind-down to cancel.
        """
        events = self.settle_accrued_funding(ts_ns)
        for oid, flight in list(self._inflight.items()):
            del self._inflight[oid]
            self._cancelled.add(oid)
            events.append(WorldEvent(WorldEventKind.ORDER_REJECTED, self._now_ns, self.name, {
                "order_id": oid, "coin": flight["order"].coin,
                "reason": "the recorded market ended before the order arrived",
                "cancelled_size": str(flight["order"].size)}))
        self._terminal = True
        return events

    def seal_recording(self) -> None:
        """The recorded market is over: every order sent from now on is refused."""
        self._closed = True

    def funding(self) -> list[FundingEvent]:
        """The latest recorded funding rate of each perp at or before the venue's instant."""
        out = []
        for coin in dict.fromkeys((*self.coins, *self.listed_coins)):
            row = self._tape.funding_at(coin, self._now_ns)
            if row is not None:
                out.append(FundingEvent(coin, row[1], row[2], row[0]))
        return out

    # ---- the venue's terms, published as facts (Chapter II §I.b)

    def _rates(self, market: str) -> tuple[Decimal, Decimal]:
        """(taker, maker) for an order on ``market`` (a pair trades on spot rates)."""
        taker, maker, _source = self._tape.fees("spot" if "/" in market else "perp")
        return taker, maker

    def instruments(self) -> dict:
        """Each listed market's record as the recording's listing stated it (lot and tick
        sizes, the order floor), with the fee rates every fill here is charged, the
        spread and book the tape states, and the rules its fills follow."""
        out: dict[str, list[dict]] = {}
        for kind, markets in (("perp", dict.fromkeys((*self.coins, *self.listed_coins))),
                              ("spot", dict.fromkeys((*self.spot_pairs,
                                                      *self.listed_spot_pairs)))):
            recorded = {row.get("coin"): row for row in self._tape.instrument_rows(kind)}
            taker, maker, source = self._tape.fees(kind)
            rows = []
            for market in markets:
                row = dict(recorded.get(market) or {"coin": market, "lot_size": "0.000001",
                                                    "tick_size": "0.01"})
                # A recording that could not read its account's rates said so; the rates
                # this venue charges are stated below, so that note no longer applies.
                row.pop("fee_rates", None)
                row.pop("reason", None)
                spread, spread_source = self._tape.spread_bps(market)
                depth = self._tape.level_size(market, self._now_ns)
                if depth is None:
                    row["liquidity"] = NO_LIQUIDITY
                row.update({
                    "min_order_value_usd": str(self._tape.min_order_value(market)),
                    "taker_fee_rate": str(taker), "maker_fee_rate": str(maker),
                    "fee_basis": ("fraction of notional, the recorded account's userFees"
                                  if source == "recorded" else
                                  "fraction of notional, Hyperliquid's published base tier"),
                    "spread_bps": str(spread), "spread_source": spread_source,
                    "synthetic_level_size": None if depth is None else str(depth),
                    "synthetic_level_source": self._tape.depth_source(market),
                    "execution": self.EXECUTION})
                rows.append(row)
            out[kind] = rows
        return out

    #: The fill rules, as facts about this venue (never advice).
    EXECUTION = (
        "A recorded market. An order is acknowledged as resting and executes when the "
        "recording first shows its market after the instant it was sent. The book is "
        "the recorded order book when it is at least as recent as the recorded mid, "
        "otherwise one level each side at the mid plus or minus half of spread_bps, "
        "holding synthetic_level_size; a market whose synthetic_level_size is null "
        "refuses every order. A market order is immediate-or-cancel within 5% of the mid "
        "when it was sent; any part not filled is cancelled. A limit order that crosses "
        "on arrival fills at the book's prices at taker_fee_rate and rests the rest. "
        "Within one tick arriving orders are matched before resting ones. A resting "
        "limit fills only when a recorded mid after it rested is strictly beyond its "
        "price and the opposite top of book is at or through its price, at its price, "
        "at maker_fee_rate, up to what the top level on that side still holds. "
        "Size taken from one recorded snapshot is not offered again. "
        "Funding settles at each UTC hour on the position then held, at the last "
        "recorded rate and mid, and pro rata for the part of an hour when the world "
        "ends.")

    # ---- the book an arriving or resting order meets

    def _book(self, market: str) -> tuple[int, list, list, str]:
        """(snapshot instant, bids, asks, source) at the venue's instant, best first.

        Each level is ``[price, available]``: what the snapshot offered less what was
        already taken from it. No level is unbounded; a market the tape recorded no
        liquidity for has none.
        """
        mid_row = self._tape.mid_at(market, self._now_ns)
        if mid_row is None:
            raise ValueError(f"the tape has recorded no price for {market} yet")
        recorded = self._tape.book_at(market, self._now_ns)
        if recorded is not None and recorded[0] >= mid_row[0]:
            snapshot, bids, asks = recorded
            source = "recorded"
        else:
            snapshot, mid = mid_row
            half = mid * self._tape.spread_bps(market)[0] / 20_000
            size = self._tape.level_size(market, self._now_ns)
            if size is None:  # never unbounded: no recorded liquidity, no level
                bids, asks, source = [], [], "none"
            else:
                bids, asks, source = [(mid - half, size)], [(mid + half, size)], "synthetic"
        # Keys of older snapshots can never be met again: the tape only moves forward.
        for key in [k for k in self._taken if k[0] == market and k[1] != snapshot]:
            del self._taken[key]

        def left(side: str, levels: list) -> list:
            out = []
            for px, size in levels:
                taken = self._taken.get((market, snapshot, side, str(px)), Decimal(0))
                out.append([px, max(Decimal(0), size - taken)])
            return out

        return snapshot, left("bid", bids), left("ask", asks), source

    def order_book(self, coin: str, depth: int) -> dict:
        """The book an order sent now would meet, at most ``depth`` levels a side."""
        _check_count(depth, 20)
        snapshot, bids, asks, source = self._book(coin)
        return {"coin": coin, "ts_ns": snapshot, "source": source,
                "bids": [{"price": px, "size": size} for px, size in bids[:depth]],
                "asks": [{"price": px, "size": size} for px, size in asks[:depth]]}

    @staticmethod
    def _walk(levels: list, size: Decimal, limit: Decimal, *, buy: bool,
              strict: bool) -> list[tuple[Decimal, Decimal]]:
        """What ``size`` would take from ``levels`` at prices within ``limit``: at or inside
        it, or strictly inside it when ``strict`` (a trade-through, not a touch)."""
        takes: list[tuple[Decimal, Decimal]] = []
        remaining = size
        for px, available in levels:
            inside = (px < limit if strict else px <= limit) if buy else (
                px > limit if strict else px >= limit)
            if remaining <= 0 or not inside:
                break
            take = min(remaining, available)
            if take > 0:
                takes.append((px, take))
                remaining -= take
        return takes

    def _commit(self, market: str, snapshot: int, side: str, takes: list) -> None:
        for px, take in takes:
            key = (market, snapshot, side, str(px))
            self._taken[key] = self._taken.get(key, Decimal(0)) + take

    # ---- orders

    def _place(self, order: Order) -> OrderResult:
        """Refuse what the venue refuses now; send the rest, acknowledged as resting."""
        if order.coin not in (self.spot_pairs if order.market == "spot" else self.coins):
            return OrderResult(None, "rejected", Decimal(0), None, "unknown coin")
        if order.market == "spot" and (order.size % Decimal("0.000001")
                or order.limit_px is not None and order.limit_px % Decimal("0.01")):
            return OrderResult(None, "rejected", Decimal(0), None, "invalid spot tick or lot size")
        if self.__dict__.get("_closed"):
            return OrderResult(None, "rejected", Decimal(0), None, MARKET_ENDED)
        read = self._tape.mid_at(order.coin, self._now_ns)
        if read is None:
            return OrderResult(None, "rejected", Decimal(0), None, "no recorded price yet")
        if self._tape.level_size(order.coin, self._now_ns) is None:
            return OrderResult(None, "rejected", Decimal(0), None, NO_LIQUIDITY)
        px = order.limit_px if order.limit_px is not None else read[1]
        if order.size * px < self._tape.min_order_value(order.coin):
            return OrderResult(None, "rejected", Decimal(0), None,
                               "order below the venue minimum value")
        oid = str(self._next_oid)
        self._next_oid += 1
        flight = {"order": order, "read_ns": read[0], "read_mid": read[1],
                  "sent_ns": self._now_ns}
        if self.__dict__.get("_terminal"):
            # The world is winding down after its recording ended: nothing later will
            # ever arrive, so the order meets the last recorded book now, by the same
            # rules as any arrival (depth, the 5% bound, the taker rate), and says what
            # it did as the venue's own answer.
            # Executed first: ``_execute`` drains the pending list and replaces it.
            events = self._execute(oid, flight)
            self._pending_events.extend(events)
            return self.lookup("", order_id=oid)
        self._inflight[oid] = flight
        return OrderResult(oid, "resting", Decimal(0), None)

    def _arrive(self) -> list[WorldEvent]:
        """Execute every order whose market the recording has shown anew since it was sent."""
        events: list[WorldEvent] = []
        for oid, flight in list(self._inflight.items()):
            row = self._tape.mid_at(flight["order"].coin, self._now_ns)
            if row is None or row[0] <= flight["read_ns"]:
                continue  # nothing recorded after what its sender read: still in flight
            del self._inflight[oid]
            events.extend(self._execute(oid, flight))
        return events

    def _refuse(self, oid: str, order: Order, reason: str) -> list[WorldEvent]:
        self._rejected[oid] = reason
        return [WorldEvent(WorldEventKind.ORDER_REJECTED, self._now_ns, self.name,
                           {"order_id": oid, "coin": order.coin, "reason": reason})]

    def _execute(self, oid: str, flight: dict) -> list[WorldEvent]:
        order: Order = flight["order"]
        market, buy = order.coin, order.is_buy
        size = order.size
        if order.market == "perp" and order.reduce_only:
            pos = self._positions.get(market)
            if pos is None or (pos.size > 0) == buy:
                return self._refuse(oid, order, "not reducing position")
            size = min(size, abs(pos.size))
        snapshot, bids, asks, _source = self._book(market)
        side, levels = ("ask", asks) if buy else ("bid", bids)
        if order.kind is OrderKind.MARKET:
            factor = 1 + self.MARKET_SLIPPAGE if buy else 1 - self.MARKET_SLIPPAGE
            bound = flight["read_mid"] * factor
        else:
            bound = order.limit_px
        takes = self._walk(levels, size, bound, buy=buy, strict=False)
        filled = sum((take for _px, take in takes), Decimal(0))
        events: list[WorldEvent] = []
        if filled > 0:
            notional = sum((px * take for px, take in takes), Decimal(0))
            vwap = (notional / filled).quantize(Decimal("1e-10"))
            taker, _maker = self._rates(market)
            result = self._fill(oid, replace_size(order, filled), vwap, fee_rate=taker)
            events.extend(self.drain_events())
            if result.status != "filled":
                if order.market == "spot":  # the spot book refuses without an event
                    events.extend(self._refuse(oid, order, result.error or "rejected"))
                else:
                    self._rejected[oid] = result.error or "rejected"
                return events
            self._commit(market, snapshot, side, takes)
        remainder = size - filled
        if remainder <= 0:
            return events
        if order.kind is OrderKind.MARKET:
            # Immediate-or-cancel: what the book could not fill is cancelled, never rested.
            if filled > 0:
                self._cancelled.add(oid)
                events.append(WorldEvent(WorldEventKind.ORDER_REJECTED, self._now_ns, self.name,
                                         {"order_id": oid, "coin": market,
                                          "reason": "immediate-or-cancel remainder cancelled",
                                          "cancelled_size": str(remainder)}))
                return events
            return events + self._refuse(
                oid, order, "immediate-or-cancel: no liquidity within 5% of the mid sent at")
        # What would rest is the remainder, at its price, with the fee a resting (maker)
        # fill of it would pay: the part already filled has already been paid for.
        resting = replace_size(order, remainder)
        if order.market == "spot" and not self._spot_affordable(
                resting, order.limit_px, fee_rate=self._rates(market)[1]):
            if filled <= 0:
                return events + self._refuse(oid, order, "insufficient spot balance")
            # Part of it filled: that part stands, and only the unaffordable rest is
            # cancelled, so the order reports what it filled.
            self._cancelled.add(oid)
            return events + [WorldEvent(
                WorldEventKind.ORDER_REJECTED, self._now_ns, self.name,
                {"order_id": oid, "coin": market,
                 "reason": "remainder cancelled: insufficient spot balance",
                 "cancelled_size": str(remainder)})]
        self._resting[oid] = resting
        self._rested_ns[oid] = self._now_ns
        return events

    def _cross_resting(self) -> list[WorldEvent]:
        """Fill resting limits a recorded mid has traded through, at their price, as makers.

        A resting buy fills only when both hold: a mid recorded after it began resting is
        strictly below its price (a sell: strictly above), so a trade happened through
        it; and the opposite top of book, recorded or synthetic, is at or below its
        price (a sell: the bid at or above it), so a counterparty offered at its price.
        A mid through the price with the ask still above it is no fill: nobody sold at
        the limit, and filling there would book the gap between the ask and the limit
        as profit. A book level that merely sits past its price, the mid not through
        it, is a quote and not a trade, and fills nothing either. The fill is at the
        order's price, at the maker rate, capped by what the top level of the book on
        that side still holds after this tick's arriving takers, and what it takes is
        not offered again.
        """
        events: list[WorldEvent] = []
        for oid, order in list(self._resting.items()):
            assert order.limit_px is not None
            row = self._tape.mid_at(order.coin, self._now_ns)
            if row is None or row[0] <= self._rested_ns.get(oid, self._now_ns):
                continue  # no mid recorded since it rested
            through = row[1] < order.limit_px if order.is_buy else row[1] > order.limit_px
            if not through:
                continue
            snapshot, bids, asks, _source = self._book(order.coin)
            side, levels = ("ask", asks) if order.is_buy else ("bid", bids)
            if not levels:
                continue
            top_px, available = levels[0]
            offered = top_px <= order.limit_px if order.is_buy else top_px >= order.limit_px
            if not offered:
                continue  # the mid went through, but no counterparty quoted the limit
            filled = min(order.size, available)
            if filled <= 0:
                continue
            _taker, maker = self._rates(order.coin)
            # The order's own reservation is released before it pays for itself: a resting
            # spot order reserves its cash (or inventory), and weighing its fill against
            # a balance that still holds that reservation would need it twice over.
            del self._resting[oid]
            result = self._fill(oid, replace_size(order, filled), order.limit_px,
                                fee_rate=maker)
            events.extend(self.drain_events())
            if result.status != "filled":
                if order.market == "spot":
                    # The spot book refused this fill: the order keeps resting, reserved.
                    self._resting[oid] = order
                    continue
                self._rested_ns.pop(oid, None)
                self._rejected[oid] = result.error or "rejected"
                continue
            self._commit(order.coin, snapshot, side, [(top_px, filled)])
            if filled < order.size:
                self._resting[oid] = replace_size(order, order.size - filled)
            else:
                self._rested_ns.pop(oid, None)
        return events

    def _spot_affordable(self, order: Order, px: Decimal, *,
                         fee_rate: Decimal | None = None) -> bool:
        """A spot buy is affordable with its cost and the fee on it: ``fee_rate``, else
        the spot taker rate."""
        rate = self._rates(order.coin)[0] if fee_rate is None else fee_rate
        fee = (order.size * px * rate).quantize(Decimal("0.000001"))
        return ((not order.reduce_only and order.size * px + fee <= self._spot_available("USDC"))
                if order.is_buy else order.size <= self._spot_available(order.coin))

    def open_orders(self) -> list[dict]:
        """Resting orders, then orders sent and not yet arrived (``in_flight``)."""
        return [*super().open_orders(), *(
            {"order_id": oid, "coin": f["order"].coin,
             "side": "buy" if f["order"].is_buy else "sell", "size": f["order"].size,
             "price": f["order"].limit_px, "in_flight": True}
            for oid, f in self._inflight.items())]

    def cancel(self, order_id: str, *, coin: str | None = None,
               client_id: str | None = None) -> dict:
        """A resting or in-flight limit is cancellable; an immediate-or-cancel is not."""
        if client_id is not None and client_id in self._cancel_results:
            return dict(self._cancel_results[client_id])
        flight = self._inflight.get(order_id)
        if flight is None or coin is not None and flight["order"].coin != coin:
            result = super().cancel(order_id, coin=coin, client_id=client_id)
            if order_id not in self._resting:
                self._rested_ns.pop(order_id, None)  # no longer resting: nothing to cross
            return result
        if flight["order"].kind is OrderKind.MARKET:
            result = {"status": "rejected",
                      "error": "an immediate-or-cancel order cannot be cancelled"}
        else:
            del self._inflight[order_id]
            self._cancelled.add(order_id)
            result = {"status": "cancelled", "order_id": order_id}
        if client_id is not None:
            self._cancel_results[client_id] = result
        return dict(result)

    def lookup(self, client_id: str, *, order_id: str | None = None) -> OrderResult:
        """What became of an order: in flight or resting, filled, cancelled (with any
        part filled first), or refused on arrival."""
        result = self._client_results.get(client_id)
        oid = order_id or (result.order_id if result else None)
        fills = [f for f in self._fills if f.order_id == oid]
        size = sum((f.size for f in fills), Decimal(0))
        avg = sum((f.size * f.px for f in fills), Decimal(0)) / size if size else None
        if oid in self._inflight:
            return OrderResult(oid, "resting", Decimal(0), None)
        if oid in self._resting:
            return OrderResult(oid, "resting", size, avg)
        if oid in self._cancelled:
            return OrderResult(oid, "cancelled", size, avg)
        if oid in self._rejected:
            return OrderResult(oid, "rejected", Decimal(0), None, self._rejected[oid])
        if fills:
            return OrderResult(oid, "filled", size, avg)
        return result or OrderResult(oid, "uncertain", Decimal(0), None, "order not observed")

    def collateral_view(self, coin: str, market: str = "perp") -> dict:
        """As the fake's, with orders in flight holding margin as resting ones do."""
        view = super().collateral_view(coin, market)
        view["open_order_holds_usd"] += sum((
            f["order"].size * (f["order"].limit_px or f["read_mid"])
            / self._leverage.get(f["order"].coin, self.max_leverage)
            for f in self._inflight.values()
            if f["order"].market == "perp" and not f["order"].reduce_only), Decimal(0))
        return view


def replace_size(order: Order, size: Decimal) -> Order:
    """The same order for ``size``: a partial fill, or what rests after one."""
    return replace(order, size=size)
