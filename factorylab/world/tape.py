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
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any

from factorylab.world.events import WorldEvent, WorldEventKind
from factorylab.world.exchange import (
    NS_PER_HOUR,
    FakeExchange,
    FundingEvent,
    FundingPayment,
)

#: The compact tape's format tag. A tape file carries it; a diary does not.
TAPE_FORMAT = "factorylab-tape/1"

#: Hyperliquid's published base-tier fee schedule, as fractions of notional (taker,
#: maker), for a diary whose recorded instrument listing did not state the account's
#: own rates. Recorded rates always win. The perp taker rate is the one longrun1's
#: recorded fills were charged (0.03814 USD on 84.757 USD of notional).
PUBLISHED_FEES = {"perp": ("0.00045", "0.00015"), "spot": ("0.0007", "0.0004")}

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

    The tape itself is held outside the instance dictionary, so a checkpoint carries
    the venue's state and not a copy of the recording: a resume rebuilds the venue
    from the same tape, whose SHA-256 the world's manifest fixes.
    """

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
        ``ts_ns``), a ``Funding`` per perp for each hour boundary crossed, then any
        fills, in that order. Guarantees time never moves backwards.
        """
        if ts_ns < self._now_ns:
            raise ValueError("TapeVenue time cannot move backwards")
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

    def _fund(self, boundary: int) -> list[WorldEvent]:
        events: list[WorldEvent] = []
        for coin in dict.fromkeys((*self.coins, *self.listed_coins)):
            rate_row = self._tape.funding_at(coin, boundary)
            mark_row = self._tape.mid_at(coin, boundary)
            if rate_row is None or mark_row is None:
                continue  # no rate recorded yet: nothing is known to charge
            _ts, rate, premium = rate_row
            self._funding_history.append(FundingEvent(coin, rate, premium, boundary))
            pos = self._positions.get(coin)
            paid = Decimal(0)
            if pos is not None:
                # Longs pay a positive rate: size times the mark at the boundary.
                paid = pos.size * mark_row[1] * rate
                self._cash -= paid
            self._funding_payments.append(
                FundingPayment(f"{boundary}:{coin}", coin, paid, rate, boundary))
            events.append(WorldEvent(WorldEventKind.FUNDING, self._now_ns, self.name,
                                     {"coin": coin, "rate": str(rate), "paid_usd": str(paid)}))
        return events

    def funding(self) -> list[FundingEvent]:
        """The latest recorded funding rate of each perp at or before the venue's instant."""
        out = []
        for coin in dict.fromkeys((*self.coins, *self.listed_coins)):
            row = self._tape.funding_at(coin, self._now_ns)
            if row is not None:
                out.append(FundingEvent(coin, row[1], row[2], row[0]))
        return out
