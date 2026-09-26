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
from decimal import ROUND_CEILING, Decimal
from pathlib import Path
from typing import Any

from factorylab.world.events import WorldEvent, WorldEventKind, funding_instant
from factorylab.world.exchange import (
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
TAPE_FORMAT = "factorylab-tape/2"

#: Why an order is refused once the recording has ended (published, never advice).
MARKET_ENDED = "the recorded market has ended"
#: The termination reason of a world whose paced clock reached its tape's end.
TAPE_ENDED = "tape_ended"

#: Why an order on a market the tape recorded no liquidity for is refused.
NO_LIQUIDITY = "the tape recorded no liquidity for this market"

# A tape world never exposes a value the recording does not contain (Codex review of
# #151, 7b8de4f; Chapter II §II.b, the hard cast): where the recording is silent the
# venue refuses, and says why, as a fact. Never a constant in the recording's place.

#: Why a market is absent, and an order on it refused, before its first recorded mid.
NO_RECORDED_MARKET = "the recording has no market for this coin yet"
#: Why an order is refused on a market whose recorded listing lacks its lot size, tick
#: size or order floor.
NO_RECORDED_LISTING = ("the recording states no lot size, tick size or order floor for "
                       "this market")
#: Why an order that takes liquidity (a market order, a limit crossing on arrival) is
#: refused: no taker fee rate of this account was recorded by now.
NO_TAKER_RATE = ("the recording states no taker fee rate for this market by now: orders "
                 "that take liquidity are refused")
#: Why an order that can rest (a limit) is refused: no maker fee rate recorded by now.
NO_MAKER_RATE = ("the recording states no maker fee rate for this market by now: orders "
                 "that can rest are refused")
#: Where a fee rate on the tape came from (``Tape.fee_at``): the venue's own statement
#: of this account's rates (``userFees``, read with the instrument listing), the fills
#: the venue booked on this market, or the fills it booked on the venue's other markets
#: of the same class (perp or spot), pooled.
FEE_SOURCES = ("venue_read", "fills", "fills_pooled")
#: Why leverage above 1x is refused: the recording states none, so no credit is extended.
NO_RECORDED_LEVERAGE = "the recording states no leverage terms: positions are margined at 1x"
#: Why a vault write is refused: the recording holds no vault.
NO_RECORDED_VAULTS = "the recording has no vaults"


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
    for name in ("event_Launch", "event_Tick", "event_MarketMid", "event_Funding",
                 "event_Fill", "order.intent", "order.acknowledged"):
        path = directory / f"{name}.jsonl"
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


def _simplest(lo: Decimal, hi: Decimal) -> Decimal:
    """The decimal with the fewest significant digits in ``[lo, hi]`` (``lo <= hi``)."""
    if lo <= 0 <= hi:
        return Decimal(0)
    if hi < 0:
        return -_simplest(-hi, -lo)
    k = hi.adjusted()
    while True:
        quantum = Decimal(1).scaleb(k)
        n = max((lo / quantum).to_integral_value(rounding=ROUND_CEILING), Decimal(1))
        if n * quantum <= hi:
            return (n * quantum).normalize()
        k -= 1


def fill_rate(fee_usd: Any, px: Any, size: Any) -> Decimal | None:
    """The fee rate one recorded fill states, as a fraction of its notional.

    The venue rounds a fee to the places it records, so a fill states its rate only to
    within one unit of the fee's last recorded place either side, whatever the rounding
    rule; the rate is the simplest decimal in that interval (the one with the fewest
    significant digits), a function of this fill alone. None for a fill with no notional.
    """
    fee, notional = Decimal(str(fee_usd)), Decimal(str(px)) * Decimal(str(size))
    if not fee.is_finite() or not notional.is_finite() or notional <= 0:
        return None
    unit = Decimal(1).scaleb(fee.as_tuple().exponent)
    return _simplest((fee - unit) / notional, (fee + unit) / notional)


#: The operations whose orders the venue executes immediately or cancels, never resting
#: (``HyperliquidExchange``: a market order and a close are immediate-or-cancel limits).
_IMMEDIATE = frozenset({"venue.place_market", "venue.close"})


def _fill_side(fill_ts: int, payload: dict, orders: dict) -> str | None:
    """Whether a recorded fill took liquidity ("taker") or provided it ("maker"), from
    what the diary recorded of its order, or None where that does not settle it.

    The diary does not record the venue's ``crossed`` flag, so the side is read off the
    order: every fill of an immediate-or-cancel order took liquidity; so did every fill
    of a limit the venue acknowledged as filled; a fill of a limit the venue
    acknowledged as resting with nothing filled, observed after that acknowledgement,
    met it on the book, so it provided liquidity. A limit acknowledged resting with a
    part already filled, a liquidation, and a fill whose order the diary does not name
    are left out: their side is not recorded.
    """
    order = orders.get(str(payload.get("order_id")))
    if order is None or payload.get("liquidation"):
        return None
    operation, status, filled, ack_ts = order
    if operation in _IMMEDIATE:
        return "taker"
    if operation == "venue.place_limit":
        if status == "filled":
            return "taker"
        if status == "resting" and filled == 0 and fill_ts > ack_ts:
            return "maker"
    return None


def _steps(observations: list[tuple[int, str, str]], *, every: bool) -> list[list]:
    """A rate as a step function of tape time: ``[instant, rate, provenance]`` rows, a
    row wherever the rate changes. With ``every`` a step names every observation it
    holds (fills); otherwise only the first (a venue read repeated each tick)."""
    steps: list[list] = []
    for ts, rate, source in sorted(observations, key=lambda o: o[0]):
        if steps and steps[-1][1] == rate:
            if every:
                steps[-1][2].append(source)
            continue
        steps.append([ts, rate, [source]])
    return steps


def _fee_record(kept: list[str], reads: list, fills: list, orders: dict) -> dict:
    """Each kept market's fee rates as the diary recorded them, by source and side."""
    observed: dict[tuple[str, str, str], list] = {}
    for ts, call, listing in reads:
        if not isinstance(listing, dict):
            continue
        for kind in ("perp", "spot"):
            for row in listing.get(kind) or ():
                if (not isinstance(row, dict) or row.get("coin") not in kept
                        or "userFees" not in str(row.get("fee_basis", ""))):
                    continue
                for side in ("taker", "maker"):
                    rate = row.get(f"{side}_fee_rate")
                    if rate is not None:
                        observed.setdefault((row["coin"], "venue_read", side), []).append(
                            (ts, str(Decimal(str(rate)).normalize()),
                             f"exchange.instruments call {call}"))
    seen: dict[tuple[str, int], int] = {}
    for ts, payload in fills:
        market = str(payload.get("coin"))
        side = _fill_side(ts, payload, orders)
        rate = fill_rate(payload.get("fee_usd"), payload.get("px"), payload.get("size"))
        if market not in kept or side is None or rate is None:
            continue
        key = (str(payload["order_id"]), ts)
        seen[key] = seen.get(key, 0) + 1
        observed.setdefault((market, "fills", side), []).append(
            (ts, str(rate), f"fill {key[0]}@{ts}#{seen[key]}"))
    out: dict[str, dict] = {}
    for (market, source, side), rows in sorted(observed.items()):
        out.setdefault(market, {}).setdefault(source, {})[side] = _steps(
            rows, every=source == "fills")
    return out


def cut(path: str | Path) -> dict:
    """The compact tape of a diary: ``events.json`` or a directory split by kind.

    Guarantees the tape holds only what the diary recorded, for the markets its
    Launch manifest named: the delivered tick stamps; each market's mids and each
    perp's funding-rate observations, stamped as delivered; the recorded order books,
    stamped with the venue's own book time; the first recorded instrument listing; and
    the account's fee rates, each stamped with the instant the diary recorded it and
    naming what it was read from (``_fee_record``): the venue's own statement of them
    with each instrument read, and the fills the venue booked. A funding row that
    moved money (``paid_usd`` non-zero) is an account payment of the run that recorded
    it, not market data, and is left out: a replay's payments are computed from its
    own positions (Chapter II §II.b). Only a diary of a live venue states fee rates: a
    simulated venue's are its own constants, never the world's.
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
    reads: list[tuple[int, int, Any]] = []
    fills: list[tuple[int, dict]] = []
    intents: dict[str, str] = {}
    acks: dict[str, tuple[dict, int]] = {}
    for item in items:
        kind = item.get("kind")
        if kind == "io.call":
            names[item["seq"]] = item.get("name")
            continue
        if kind == "order.intent":
            intents[str(item.get("client_id"))] = str(item.get("operation"))
            continue
        if kind == "order.acknowledged":
            acks[str(item.get("client_id"))] = (item.get("result") or {}, int(item["ts"]))
            continue
        if kind == "io.result":
            name = names.get(item.get("call"))
            if "result" not in item:
                continue  # an answer kept beside its diary: not part of this cut
            if name == "exchange.instruments":
                listing = _decode(item["result"])
                instruments = listing if instruments is None else instruments
                reads.append((int(item.get("ts") or 0), item.get("call"), listing))
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
        elif event.get("kind") == "Fill":
            fills.append((ts, payload))
        elif event.get("kind") == "Funding" and not _nonzero(payload.get("paid_usd", "0")):
            # The rate's own instant, when the diary's venue stated one.
            funding.setdefault(str(payload["coin"]), {})[funding_instant(payload, ts)] = [
                str(payload["rate"]),
                None if payload.get("premium") is None else str(payload["premium"])]
    exchange = manifest.get("exchange") or {}
    markets = tuple(exchange.get("coins") or ()) + tuple(exchange.get("spot_pairs") or ())
    kept = [m for m in (markets or sorted(mids)) if m in mids]
    if not ticks or not kept:
        raise ValueError(f"{path} recorded no ticks or no mids for its markets")
    orders: dict[str, tuple] = {}
    for client_id, (result, ack_ts) in acks.items():
        if result.get("order_id") is not None and client_id in intents:
            orders[str(result["order_id"])] = (
                intents[client_id], result.get("status"),
                Decimal(str(result.get("filled_size") or 0)), ack_ts)
    live = exchange.get("kind") == "hyperliquid"
    fees = _fee_record(kept, reads, fills, orders) if live else {}
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
        "fees": fees,
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
        for market, sources in data.get("fees", {}).items():
            for source, sides in sources.items():
                for side, steps in sides.items():
                    tape._index[("fees", market, source, side)] = [int(r[0]) for r in steps]
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

    def listing(self, market: str) -> dict | None:
        """``market``'s own row of the recorded instrument listing, or None."""
        kind = "spot" if "/" in market else "perp"
        for row in self.instrument_rows(kind):
            if row.get("coin") == market:
                return row
        return None

    def _step(self, market: str, source: str, side: str, ts_ns: int) -> list | None:
        stamps = self._index.get(("fees", market, source, side))
        if not stamps:
            return None
        i = bisect.bisect_right(stamps, ts_ns) - 1
        return None if i < 0 else self.data["fees"][market][source][side][i]

    def fee_at(self, market: str, side: str, ts_ns: int) -> tuple | None:
        """(rate, source, since_ns, provenance): the account's ``side`` ("taker" or
        "maker") fee rate on ``market`` as the recording stood at ``ts_ns``, or None.

        A step function of tape time, read strictly at or before ``ts_ns``: a rate is
        usable only from the instant it was recorded, never averaged across the future.
        The venue's own statement of the rate (``venue_read``) is the primary source;
        else the latest rate this market's fills stated (``fills``); else the latest
        stated by fills on the venue's other markets of the same class (perp or spot),
        which is said (``fills_pooled``). Never a published schedule or a constant.
        """
        for source in ("venue_read", "fills"):
            step = self._step(market, source, side, ts_ns)
            if step is not None:
                return Decimal(step[1]), source, int(step[0]), list(step[2])
        spot = "/" in market
        pooled = [step for other in self.data.get("fees", {})
                  if other != market and ("/" in other) == spot
                  for step in [self._step(other, "fills", side, ts_ns)] if step is not None]
        if not pooled:
            return None
        step = max(pooled, key=lambda row: int(row[0]))
        return Decimal(step[1]), "fills_pooled", int(step[0]), list(step[2])

    def fees(self, market: str, ts_ns: int) -> tuple[Decimal | None, Decimal | None]:
        """(taker, maker) rates on ``market`` at ``ts_ns`` (``fee_at``); None for a side
        the recording had not stated by then."""
        taker, maker = (self.fee_at(market, side, ts_ns) for side in ("taker", "maker"))
        return (None if taker is None else taker[0]), (None if maker is None else maker[0])

    def _book_spreads_bps(self, coin: str) -> list[Decimal]:
        out = []
        for row in self.data.get("books", {}).get(coin, []):
            bids, asks = row[1], row[2]
            if bids and asks:
                bid, ask = Decimal(bids[0][0]), Decimal(asks[0][0])
                if ask > bid > 0:
                    out.append((ask - bid) / ((ask + bid) / 2) * 10_000)
        return out

    def spread_bps(self, coin: str) -> tuple[Decimal | None, str]:
        """(bps, source): the median recorded top-of-book spread for ``coin``; else the
        median over every recorded book on the tape; else (None, "none"): the tape
        recorded no book, states no spread, and no synthetic level exists."""
        own = self._book_spreads_bps(coin)
        if own:
            return _median(own).quantize(Decimal("0.0001")), "recorded"
        every = [s for c in self.data.get("books", {}) for s in self._book_spreads_bps(c)]
        if every:
            return _median(every).quantize(Decimal("0.0001")), "recorded_other_markets"
        return None, "none"

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

    def terms(self, market: str) -> tuple[Decimal, Decimal, Decimal] | None:
        """(lot size, tick size, order floor) as ``market``'s recorded listing row states
        them, or None when the recording does not state all three."""
        row = self.listing(market) or {}
        fields = [row.get(k) for k in ("lot_size", "tick_size", "min_order_value_usd")]
        if any(v is None for v in fields):
            return None
        lot, tick, floor = (Decimal(str(v)) for v in fields)
        return (lot, tick, floor) if lot > 0 and tick > 0 else None

    def identity(self) -> dict:
        """What a manifest's ``[exchange.tape]`` must say about this tape, all of it
        derived from the tape itself: its SHA-256, span, markets and the spreads it
        states (none for a market when the tape recorded no book at all)."""
        spreads = {m: self.spread_bps(m)[0] for m in self.markets}
        return {"sha256": self.sha256, "start_ns": self.start_ns, "end_ns": self.end_ns,
                "markets": tuple(self.markets),
                "spread_bps": {m: str(bps) for m, bps in spreads.items() if bps is not None}}

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
    (no level, and every order refused, when the tape recorded none); a market order is
    immediate-or-cancel within Hyperliquid's 5% of the mid it was sent at, and what it
    cannot fill is cancelled; a limit that crosses on arrival takes at the book's prices,
    at the taker rate, and rests the remainder; a resting limit fills only when a recorded mid after
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

    It never exposes a value its recording does not contain (Codex review of #151,
    7b8de4f): a market is absent from mids, books and the listing until its first
    recorded row; an order is refused on a market whose recorded listing does not state
    its lot, tick, order floor and fee rates, and is held to that recorded precision; no
    leverage terms are recorded, so none is extended (1x); no vault is recorded, so none
    can be made. None of the fake's own terms (its 100 mid, spread, fee, funding rate,
    lot and tick, 3x) is ever read.

    The tape itself is held outside the instance dictionary, so a checkpoint carries
    the venue's state and not a copy of the recording: a resume rebuilds the venue
    from the same tape, whose SHA-256 the world's manifest fixes.
    """

    #: Hyperliquid's market order is an immediate-or-cancel limit this far through the
    #: mid its sender read (the SDK's DEFAULT_SLIPPAGE; ``HyperliquidExchange``).
    MARKET_SLIPPAGE = Decimal("0.05")

    __slots__ = ("_tape",)

    def __init__(self, tape: Tape, *, coins: tuple[str, ...] = ("BTC", "ETH"),
                 spot_pairs: tuple[str, ...] = (), start_cash_usd: Decimal,
                 seed: int = 0) -> None:
        missing = [m for m in (*coins, *spot_pairs) if m not in tape.markets]
        if missing:
            raise ValueError(f"the tape recorded no mids for {missing}")
        self._tape = tape
        # The fake's own terms are never read here, and are set so that none could leak
        # if one were (Codex review of #151, 7b8de4f): every fee is the recorded rate
        # (``_rates``), every book and fill the tape's (``_book``, ``_place``), every
        # funding rate recorded (``_charge``). What the recording states nothing about
        # is refused: no leverage terms, so no credit (1x, and a position is closed
        # only when the perps account's equity is below zero), and no vaults.
        super().__init__(
            name=f"tape:{tape.sha256[:8]}", seed=seed, start_cash_usd=Decimal(start_cash_usd),
            coins=tuple(coins), spot_pairs=tuple(spot_pairs), start_prices={},
            spread_bps=Decimal(0), fee_bps=Decimal(0), funding_rate=Decimal(0),
            step_bps=Decimal(0), max_leverage=Decimal(1), maintenance_fraction=Decimal(0),
            listed_coins=tape.perps, listed_spot_pairs=tape.pairs)
        # Only what the recording shows at its first instant: a market with no row yet
        # has no mid, no book and no listing until its first row (never the fake's 100).
        self._mids = {}
        for market in tape.markets:
            row = tape.mid_at(market, tape.start_ns)
            if row is not None:
                self._set_mid(market, row[1])
        self._mid_history = {market: [] for market in self._mids}
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

    def _set_mid(self, market: str, mid: Decimal) -> None:
        self._mids[market] = mid
        base = market.split("/")[0]
        if "/" in market and base not in self._tape.markets:
            # The fake keeps a pair's base at the pair's mid; never over a recorded perp.
            self._mids[base] = mid

    def _recorded(self, market: str) -> tuple[int, Decimal] | None:
        """``market``'s latest recorded mid at or before the venue's instant, or None."""
        return self._tape.mid_at(market, self._now_ns)

    def mids(self) -> dict[str, Decimal]:
        """Each market's latest recorded mid as the venue last read it (and a pair's base
        at the pair's); a market with no recorded row yet is absent, whatever else put
        a price in the fake's table."""
        out: dict[str, Decimal] = {}
        for market in self._quoted():
            if market in self._mids and self._recorded(market) is not None:
                out[market] = self._mids[market]
                base = market.split("/")[0]
                if "/" in market and base not in self._tape.markets:
                    out[base] = self._mids[market]
        return out

    def candles(self, coin: str, interval: str, n: int) -> list[dict]:
        if self._recorded(coin) is None:
            raise ValueError(NO_RECORDED_MARKET)
        return super().candles(coin, interval, n)

    def funding_history(self, coin: str, n: int) -> list[FundingEvent]:
        if self._recorded(coin) is None:
            raise ValueError(NO_RECORDED_MARKET)
        return super().funding_history(coin, n)

    def set_leverage(self, coin: str, leverage: int, *, market: str = "perp") -> dict:
        """1x only: the recording states no leverage terms, so no credit is extended."""
        if (market != "spot" and coin in self.coins and type(leverage) is int
                and leverage > 1):
            return {"status": "rejected", "error": NO_RECORDED_LEVERAGE}
        return super().set_leverage(coin, leverage, market=market)

    def vault_create(self, name: str, description: str, usd: Decimal, *,
                     client_id: str | None = None) -> dict:
        return {"status": "rejected", "error": NO_RECORDED_VAULTS}

    def vault_transfer(self, vault: str, is_deposit: bool, usd: Decimal, *,
                       client_id: str | None = None) -> dict:
        return {"status": "rejected", "error": NO_RECORDED_VAULTS}

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
            self._set_mid(market, mid)
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
        if fee_rate is None:
            # A liquidation takes liquidity: the recorded taker rate, never the fake's.
            # A position the recording let open only as a maker, before any taker rate
            # was recorded, is closed at its own recorded maker rate: the only rate of
            # this account the recording states by then.
            taker, maker = self._rates(order.coin)
            fee_rate = taker if taker is not None else maker
        return super()._fill(oid, order, px, liquidation=liquidation, fee_rate=fee_rate)

    def _resting_maker(self, market: str) -> Decimal:
        """The maker rate an order resting on ``market`` pays: recorded before it could
        rest (``_side_refusal``), and a recorded rate never ceases to be one."""
        maker = self._rates(market)[1]
        if maker is None:
            raise ValueError(NO_MAKER_RATE)
        return maker

    def _spot_available(self, coin: str) -> Decimal:
        """As the fake's, a resting spot buy holding its cost and the recorded maker fee
        it would pay (the fake holds its own ``fee_bps``, which a tape never charges)."""
        if coin != "USDC":
            return super()._spot_available(coin)
        committed = sum((o.size * o.limit_px * (1 + self._resting_maker(o.coin))
                         for o in self._resting.values() if o.market == "spot" and o.is_buy),
                        Decimal(0))
        return max(Decimal(0), self._spot_cash - committed)

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
            # ``funding_ns``: the funding time this payment is for, which the
            # advance that emits it may have passed.
            events.append(WorldEvent(WorldEventKind.FUNDING, self._now_ns, self.name,
                                     {"coin": coin, "rate": str(rate), "paid_usd": str(paid),
                                      "funding_ns": instant,
                                      # The price the payment is on (size * it * rate).
                                      "mark": str(mark_row[1])}))
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

    def _rates(self, market: str) -> tuple[Decimal | None, Decimal | None]:
        """(taker, maker) on ``market`` as the recording stood at the venue's instant
        (``Tape.fee_at``); None for a side not recorded by then, which no order pays:
        ``_place`` and ``_execute`` refuse what would need it."""
        return self._tape.fees(market, self._now_ns)

    def _side_refusal(self, market: str, kind: OrderKind) -> str | None:
        """Why an order of ``kind`` is refused on ``market`` now for want of a fee rate:
        an immediate-or-cancel order takes liquidity and needs the taker rate; a limit
        can rest and needs the maker rate (and, if it crosses on arrival, the taker rate
        then, ``_execute``)."""
        taker, maker = self._rates(market)
        if kind is OrderKind.MARKET:
            return NO_TAKER_RATE if taker is None else None
        return NO_MAKER_RATE if maker is None else None

    def _level_size(self, market: str) -> Decimal | None:
        """One synthetic level of ``market``, in whole recorded lots; None when the tape
        recorded no liquidity for it (or less than one lot of it)."""
        size = self._tape.level_size(market, self._now_ns)
        terms = self._tape.terms(market)
        if size is not None and terms is not None:
            size = size // terms[0] * terms[0]
        return size if size else None

    def _refusal(self, market: str) -> str | None:
        """Why every order on ``market`` is refused now, or None: the recording is silent
        on something an order here needs (Codex review of #151, 7b8de4f)."""
        if self.__dict__.get("_closed"):
            return MARKET_ENDED
        if self._recorded(market) is None:
            return NO_RECORDED_MARKET
        if self._tape.terms(market) is None:
            return NO_RECORDED_LISTING
        if self._level_size(market) is None:
            return NO_LIQUIDITY
        return None

    def instruments(self) -> dict:
        """Each market the recording shows by now, as its recorded listing stated it (lot
        and tick sizes, price precision, the order floor, the account's fee rates),
        with the spread and book the tape states and the rules its fills follow; a
        term the recording does not state is null and named in ``refused``. A market
        with no recorded row yet is absent (``NO_RECORDED_MARKET``)."""
        out: dict[str, list[dict]] = {}
        for kind, markets in (("perp", dict.fromkeys((*self.coins, *self.listed_coins))),
                              ("spot", dict.fromkeys((*self.spot_pairs,
                                                      *self.listed_spot_pairs)))):
            rows = []
            for market in markets:
                if self._recorded(market) is None:
                    continue
                row = dict(self._tape.listing(market) or {"coin": market})
                # A recording that could not read its account's rates said so; the fields
                # below state what this venue has, so that note no longer applies.
                row.pop("fee_rates", None)
                row.pop("reason", None)
                terms = self._tape.terms(market)
                taker, maker = (self._tape.fee_at(market, side, self._now_ns)
                                for side in ("taker", "maker"))
                spread, spread_source = self._tape.spread_bps(market)
                depth = self._level_size(market)
                if depth is None:
                    row["liquidity"] = NO_LIQUIDITY
                row.update({
                    "lot_size": None if terms is None else str(terms[0]),
                    "tick_size": None if terms is None else str(terms[1]),
                    "min_order_value_usd": None if terms is None else str(terms[2]),
                    "taker_fee_rate": None if taker is None else str(taker[0]),
                    "maker_fee_rate": None if maker is None else str(maker[0]),
                    "fee_basis": "fraction of notional, this account's recorded rate",
                    "taker_fee_source": None if taker is None else taker[1],
                    "taker_fee_since_ns": None if taker is None else taker[2],
                    "maker_fee_source": None if maker is None else maker[1],
                    "maker_fee_since_ns": None if maker is None else maker[2],
                    "market_orders_refused": self._side_refusal(market, OrderKind.MARKET),
                    "limit_orders_refused": self._side_refusal(market, OrderKind.LIMIT),
                    "spread_bps": None if spread is None else str(spread),
                    "spread_source": spread_source,
                    "synthetic_level_size": None if depth is None else str(depth),
                    "synthetic_level_source": self._tape.depth_source(market),
                    "refused": self._refusal(market),
                    "execution": self.EXECUTION})
                if kind == "perp":
                    row["max_leverage"] = 1
                rows.append(row)
            out[kind] = rows
        return out

    #: The fill rules, as facts about this venue (never advice).
    EXECUTION = (
        "A recorded market. A market appears in mids, books and this listing from its "
        "first recorded mid; an order on a market not listed here is refused. Every "
        "order on a market whose refused is not null is refused, for that reason: the "
        "recording states no lot size, tick size or order floor for it, or no "
        "liquidity. Fee rates are this account's as the recording stood at the venue's "
        "instant: the venue's own statement of them (taker_fee_source venue_read), else "
        "the rate the latest recorded fill on this market stated (fills), else on the "
        "venue's other markets of its class (fills_pooled), each usable only from the "
        "instant it was recorded (taker_fee_since_ns, maker_fee_since_ns). A market "
        "order needs taker_fee_rate and a limit order maker_fee_rate: while either is "
        "null those orders are refused (market_orders_refused, limit_orders_refused), "
        "and a limit that would cross on arrival with no taker_fee_rate is refused "
        "then. An order whose size is not a multiple of lot_size, whose limit "
        "price is not a multiple of tick_size or has more than price_significant_figures "
        "significant figures (an integer price excepted when integer_prices_allowed), or "
        "whose value is below min_order_value_usd, is refused. "
        "An order is acknowledged as resting and executes when the "
        "recording first shows its market after the instant it was sent. The book is "
        "the recorded order book when it is at least as recent as the recorded mid, "
        "otherwise one level each side at the mid plus or minus half of spread_bps, "
        "holding synthetic_level_size. A market order is immediate-or-cancel within 5% "
        "of the mid when it was sent; any part not filled is cancelled. A limit order "
        "that crosses on arrival fills at the book's prices at taker_fee_rate and rests "
        "the rest. Within one tick arriving orders are matched before resting ones. A "
        "resting limit fills only when a recorded mid after it rested is strictly beyond "
        "its price and the opposite top of book is at or through its price, at its "
        "price, at maker_fee_rate, up to what the top level on that side still holds. "
        "Size taken from one recorded snapshot is not offered again. "
        "Funding settles at each UTC hour on the position then held, at the last "
        "recorded rate and mid, and pro rata for the part of an hour when the world "
        "ends. The recording states no leverage terms: perp positions are margined at "
        "1x, and are closed at the mid, at taker_fee_rate (maker_fee_rate while no "
        "taker rate is recorded), when the perps account's "
        "equity is below zero. The recording has no vaults.")

    # ---- the book an arriving or resting order meets

    def _book(self, market: str) -> tuple[int, list, list, str]:
        """(snapshot instant, bids, asks, source) at the venue's instant, best first.

        Each level is ``[price, available]``: what the snapshot offered less what was
        already taken from it. No level is unbounded; a market the tape recorded no
        liquidity for has none.
        """
        mid_row = self._recorded(market)
        if mid_row is None:
            raise ValueError(NO_RECORDED_MARKET)
        recorded = self._tape.book_at(market, self._now_ns)
        if recorded is not None and recorded[0] >= mid_row[0]:
            snapshot, bids, asks = recorded
            source = "recorded"
        else:
            snapshot, mid = mid_row
            size = self._level_size(market)
            if size is None:  # never unbounded: no recorded liquidity, no level
                bids, asks, source = [], [], "none"
            else:
                # A level exists only where some book was recorded, so a spread is stated.
                half = mid * self._tape.spread_bps(market)[0] / 20_000
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
        refusal = self._refusal(order.coin) or self._side_refusal(order.coin, order.kind)
        if refusal is not None:
            return OrderResult(None, "rejected", Decimal(0), None, refusal)
        read = self._recorded(order.coin)
        lot, tick, floor = self._tape.terms(order.coin)
        # The recorded listing's precision, as the venue enforces it: never the fake's.
        if order.size <= 0 or order.size % lot:
            return OrderResult(None, "rejected", Decimal(0), None,
                               "size is not a positive multiple of the recorded lot_size")
        if order.limit_px is not None and not self._price_ok(order.coin, order.limit_px, tick):
            return OrderResult(None, "rejected", Decimal(0), None,
                               "price is off the recorded tick_size or significant figures")
        px = order.limit_px if order.limit_px is not None else read[1]
        if order.size * px < floor:
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

    def _price_ok(self, market: str, px: Decimal, tick: Decimal) -> bool:
        """``px`` is a price the recorded listing admits: positive, a multiple of its
        tick size, and within its significant figures (an integer price excepted where
        the listing allows integers). A term the listing does not state is not applied."""
        if not px.is_finite() or px <= 0 or px % tick:
            return False
        row = self._tape.listing(market) or {}
        figures = row.get("price_significant_figures")
        if figures is None or row.get("integer_prices_allowed") and px == px.to_integral():
            return True
        return len(px.normalize().as_tuple().digits) <= int(figures)

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

    def _executed(self, oid: str) -> Decimal:
        """What order ``oid`` has filled so far."""
        return sum((f.size for f in self._fills if f.order_id == oid), Decimal(0))

    def _refuse(self, oid: str, order: Order, reason: str, *,
                drained: list[WorldEvent] | None = None) -> list[WorldEvent]:
        """End order ``oid`` for ``reason``, keeping whatever it already executed.

        Guarantees an order that has filled anything is never reported rejected (Codex
        review of #151, d5b92f7): what it executed stands, only its unfilled rest
        (``order``, as it now stands) is cancelled, and it reads back ``cancelled``
        with its executed size, as a venue reports a partly filled order whose rest was
        cancelled; an order that filled nothing is ``rejected``. Either way exactly one
        ``OrderRejected`` says so: one the fake's own fill already put in ``drained``
        for this order is replaced by it.
        """
        events = [e for e in drained or () if not (
            e.kind == WorldEventKind.ORDER_REJECTED and e.payload.get("order_id") == oid)]
        if self._executed(oid) > 0:
            self._cancelled.add(oid)
            payload = {"order_id": oid, "coin": order.coin,
                       "reason": f"remainder cancelled: {reason}",
                       "cancelled_size": str(order.size)}
        else:
            self._rejected[oid] = reason
            payload = {"order_id": oid, "coin": order.coin, "reason": reason}
        return [*events, WorldEvent(WorldEventKind.ORDER_REJECTED, self._now_ns, self.name,
                                    payload)]

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
        taker, maker = self._rates(market)
        if filled > 0 and taker is None:
            # It would take liquidity, and no taker rate of this account was recorded by
            # this instant: a limit that crosses on arrival is refused, never charged
            # a rate the recording does not state.
            return self._refuse(oid, order, NO_TAKER_RATE)
        if filled > 0:
            notional = sum((px * take for px, take in takes), Decimal(0))
            vwap = (notional / filled).quantize(Decimal("1e-10"))
            result = self._fill(oid, replace_size(order, filled), vwap, fee_rate=taker)
            if result.status != "filled":
                return self._refuse(oid, order, result.error or "rejected",
                                    drained=events + self.drain_events())
            events.extend(self.drain_events())
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
                resting, order.limit_px, fee_rate=maker):
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
            if maker is None:  # rested under a maker rate; the tape only moves forward
                continue
            # The order's own reservation is released before it pays for itself: a resting
            # spot order reserves its cash (or inventory), and weighing its fill against
            # a balance that still holds that reservation would need it twice over.
            del self._resting[oid]
            result = self._fill(oid, replace_size(order, filled), order.limit_px,
                                fee_rate=maker)
            drained = self.drain_events()
            if result.status != "filled":
                if order.market == "spot":
                    # The spot book refused this fill: the order keeps resting, reserved.
                    self._resting[oid] = order
                    events.extend(drained)
                    continue
                # The perp account cannot carry it: what the order filled before stands,
                # and its unfilled rest (all of ``order`` now) ends here (``_refuse``).
                self._rested_ns.pop(oid, None)
                events = self._refuse(oid, order, result.error or "rejected",
                                      drained=events + drained)
                continue
            events.extend(drained)
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
        if rate is None:  # never charged a rate the recording does not state
            raise ValueError(NO_TAKER_RATE)
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
        """What became of an order: in flight or resting, filled, cancelled, or refused.

        Guarantees its executed size is reported first, whatever ended it: an order that
        filled anything and then ended short of its size reads ``cancelled`` with that
        size, never ``rejected`` with none (Codex review of #151, d5b92f7)."""
        result = self._client_results.get(client_id)
        oid = order_id or (result.order_id if result else None)
        fills = [f for f in self._fills if f.order_id == oid]
        size = sum((f.size for f in fills), Decimal(0))
        avg = sum((f.size * f.px for f in fills), Decimal(0)) / size if size else None
        if oid in self._inflight:
            return OrderResult(oid, "resting", Decimal(0), None)
        if oid in self._resting:
            return OrderResult(oid, "resting", size, avg)
        if oid in self._cancelled or oid in self._rejected and size:
            return OrderResult(oid, "cancelled", size, avg, self._rejected.get(oid))
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
