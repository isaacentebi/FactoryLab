"""A read-only recorder of Hyperliquid's broad market, written as a replayable tape.

A tape is the world, not architecture (AGENTS.md): what it holds is what the venue's
public reads answered, stamped as delivered, and nothing else (factorylab/world/tape.py).
The diary cutter (``tape.cut``) keeps only the markets a paid run happened to trade;
this recorder records a whole universe -- every perp, every USDC spot pair, named
HIP-3 dexes -- with batched reads, so the recording's cost follows the dexes it reads,
never the number of markets (Chapter II §IV.c):

- one ``allMids`` read per poll for the first perp dex and all spot, and one per named
  HIP-3 dex;
- one ``metaAndAssetCtxs`` read per perp dex every ``funding_every`` polls, whose
  funding rate and premium are kept only where they changed;
- ``books_per_poll`` ``l2Book`` reads per poll, rotating through the universe, so every
  market's liquidity is sampled at a bounded rate whatever the universe's size.

It signs nothing and reads no key: its only account read is ``userFees`` of a public
address it is given (by default the zero address, whose answer is the venue's base
schedule), which states the fee rates the tape replays. Every read is a POST to the
public info endpoint through the ``post`` callable it is built with, so a test drives
it without a network.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable, Iterable, Iterator
from decimal import Decimal
from pathlib import Path
from typing import Any

from factorylab.world.exchange import (
    MIN_ORDER_VALUE_USD,
    NS_PER_MS,
    scaled_fee_rates,
)
from factorylab.world.tape import TAPE_FORMAT
from factorylab.world.universe import named_dexes, resolve

#: The address whose ``userFees`` answer a recording states by default: an account with
#: no volume, staking or referral, so the venue answers its base schedule.
BASE_SCHEDULE_USER = "0x" + "0" * 40
#: The recording journal's format tag (one JSON line per poll, after a header line).
JOURNAL_FORMAT = "factorylab-tape-journal/1"
#: Hyperliquid's documented REST weights ("Rate limits and user limits"): allMids and
#: l2Book weigh 2; every other info request 20.
READ_WEIGHT = {"allMids": 2, "l2Book": 2}


def _weight(kind: str) -> int:
    return READ_WEIGHT.get(kind, 20)


class Recorder:
    """Polls a universe of Hyperliquid markets with batched public reads.

    Guarantees: the reads one poll sends number ``1 + len(dexes)`` mids reads, at most
    ``books_per_poll`` book reads and, on a funding poll, ``1 + len(dexes)`` context
    reads, whatever the number of markets; nothing is signed; a failed read is recorded
    as absent for that poll, never filled from an earlier one.
    """

    def __init__(self, post: Callable[[dict], Any], *, coins: Iterable[str],
                 spot_pairs: Iterable[str], books_per_poll: int = 8, book_depth: int = 5,
                 funding_every: int = 1, fee_user: str = BASE_SCHEDULE_USER,
                 source: str = "hyperliquid", clock: Callable[[], int] = time.time_ns) -> None:
        if type(books_per_poll) is not int or books_per_poll < 0:
            raise ValueError("books_per_poll must be a nonnegative integer")
        if type(book_depth) is not int or not 1 <= book_depth <= 20:
            raise ValueError("book_depth must be an integer between 1 and 20")
        if type(funding_every) is not int or funding_every < 1:
            raise ValueError("funding_every must be a positive integer")
        self.post = post
        self.coins, self.spot_pairs = tuple(coins), tuple(spot_pairs)
        self.dexes = named_dexes(self.coins)
        self.books_per_poll, self.book_depth = books_per_poll, book_depth
        self.funding_every, self.fee_user, self.clock = funding_every, fee_user, clock
        self.source = source
        self.polls = 0
        self.weight = 0
        self._book_cursor = 0
        self.header: dict[str, Any] | None = None

    def _read(self, body: dict) -> Any:
        self.weight += _weight(body["type"])
        return self.post(body)

    # ---- the listing, read once

    def start(self) -> dict:
        """Read the venue's listing, resolve the universe and state each market's terms.

        Returns the journal header: the resolved markets, each market's listing row (lot
        and tick size, price precision, order floor, a perp's leverage limit, margin
        mode and dex) and its fee rates as ``userFees`` of ``fee_user`` states them,
        scaled by the venue's HIP-3 rule (``scaled_fee_rates``). Raises ``ValueError``
        when a named dex is not margined in USDC or a selector selects nothing.
        """
        now = self.clock()
        spot_meta = self._read({"type": "spotMeta"})
        tokens = {t["index"]: t for t in spot_meta["tokens"]}
        usdc = next(t["index"] for t in spot_meta["tokens"] if t["name"] == "USDC")
        perps: dict[str, dict] = {}
        for dex in ("", *self.dexes):
            meta = self._read({"type": "meta", **({"dex": dex} if dex else {})})
            if dex and meta.get("collateralToken", usdc) != usdc:
                raise ValueError(f"perp dex {dex!r} is not margined in USDC")
            for asset in meta["universe"]:
                perps[asset["name"]] = {"dex": dex, **asset}
        wire: dict[str, str] = {}
        spot_rows: dict[str, dict] = {}
        for row in spot_meta["universe"]:
            base, quote = (tokens[i] for i in row["tokens"])
            if quote["name"] != "USDC":
                continue
            pair = f'{base["name"]}/USDC'
            wire[pair] = row["name"]
            spot_rows[pair] = {"szDecimals": int(base["szDecimals"])}
        fees = self._read({"type": "userFees", "user": self.fee_user})
        base = {market: {"taker_fee_rate": str(Decimal(str(fees[taker]))),
                         "maker_fee_rate": str(Decimal(str(fees[maker]))),
                         "fee_basis": "fraction of notional, userFees of the recording's "
                                      "fee user"}
                for market, (taker, maker) in (
                    ("perp", ("userCrossRate", "userAddRate")),
                    ("spot", ("userSpotCrossRate", "userSpotAddRate")))}
        listing = {
            "perp": [{"coin": name, **({"delisted": True} if asset.get("isDelisted") else {})}
                     for name, asset in perps.items()],
            "spot": [{"coin": pair} for pair in spot_rows]}
        coins, pairs = resolve(self.coins, self.spot_pairs, listing)
        rows: dict[str, list[dict]] = {"perp": [], "spot": []}
        rates: dict[str, dict] = {}
        for name in coins:
            asset = perps.get(name)
            if asset is None:
                raise ValueError(f"the venue lists no perp {name!r}")
            sz = int(asset["szDecimals"])
            isolated = (bool(asset.get("onlyIsolated"))
                        or asset.get("marginMode") in ("strictIsolated", "noCross"))
            rates[name] = scaled_fee_rates(
                base["perp"], deployer_fee_scale=(asset.get("deployerFeeScale")
                                                  if asset["dex"] else None),
                growth_mode=asset.get("growthMode") == "enabled",
                referral=fees.get("activeReferralDiscount"))
            rows["perp"].append({
                "coin": name, "lot_size": str(Decimal(1).scaleb(-sz)),
                "tick_size": str(Decimal(1).scaleb(-6 + sz)),
                "price_significant_figures": 5, "integer_prices_allowed": True,
                "min_order_value_usd": MIN_ORDER_VALUE_USD,
                "max_leverage": int(asset["maxLeverage"]),
                "margin": "isolated" if isolated else "cross",
                **({"dex": asset["dex"]} if asset["dex"] else {}), **rates[name]})
            wire.setdefault(name, name)
        for pair in pairs:
            if pair not in spot_rows:
                raise ValueError(f"the venue lists no USDC spot pair {pair!r}")
            sz = spot_rows[pair]["szDecimals"]
            rates[pair] = scaled_fee_rates(base["spot"],
                                           referral=fees.get("activeReferralDiscount"))
            rows["spot"].append({
                "coin": pair, "lot_size": str(Decimal(1).scaleb(-sz)),
                "tick_size": str(Decimal(1).scaleb(-8 + sz)),
                "price_significant_figures": 5, "integer_prices_allowed": True,
                "min_order_value_usd": MIN_ORDER_VALUE_USD, **rates[pair]})
        self.header = {
            "format": JOURNAL_FORMAT, "ts": now, "fee_user": self.fee_user,
            "source": self.source,
            "selectors": {"coins": list(self.coins), "spot_pairs": list(self.spot_pairs)},
            "dexes": list(self.dexes), "markets": [*coins, *pairs],
            "wire": {m: wire[m] for m in (*coins, *pairs)}, "instruments": rows,
            "fees": {m: {"taker": r.get("taker_fee_rate"), "maker": r.get("maker_fee_rate")}
                     for m, r in rates.items()},
            "start_weight": self.weight}
        return self.header

    # ---- one poll

    def poll(self) -> dict:
        """One poll: every market's mid, the funding contexts on a funding poll, and a
        rotating, bounded sample of books, stamped at the instant its last answer
        arrived. A read that failed is named in ``failed``."""
        if self.header is None:
            raise RuntimeError("start() reads the listing before the first poll")
        weight_before = self.weight
        failed: list[str] = []
        public = {w: m for m, w in self.header["wire"].items()}
        mids: dict[str, str] = {}
        for dex in ("", *self.dexes):
            try:
                answer = self._read({"type": "allMids", **({"dex": dex} if dex else {})})
            except Exception as exc:  # noqa: BLE001 - an unanswered read is absent
                failed.append(f"allMids:{dex}:{type(exc).__name__}")
                continue
            for name, value in (answer or {}).items():
                market = public.get(name)
                if market is not None:
                    mids[market] = str(value)
        funding: dict[str, list] = {}
        if self.polls % self.funding_every == 0:
            markets = set(self.header["markets"])
            for dex in ("", *self.dexes):
                try:
                    meta, ctxs = self._read({"type": "metaAndAssetCtxs",
                                             **({"dex": dex} if dex else {})})
                    pairs = zip(meta["universe"], ctxs, strict=False)
                except Exception as exc:  # noqa: BLE001 - an unanswered read is absent
                    failed.append(f"metaAndAssetCtxs:{dex}:{type(exc).__name__}")
                    continue
                for asset, ctx in pairs:
                    name = asset.get("name")
                    if name in markets and ctx.get("funding") is not None:
                        funding[name] = [str(ctx["funding"]),
                                         None if ctx.get("premium") is None
                                         else str(ctx["premium"])]
        books: dict[str, list] = {}
        universe = self.header["markets"]
        for _ in range(min(self.books_per_poll, len(universe))):
            market = universe[self._book_cursor % len(universe)]
            self._book_cursor += 1
            try:
                raw = self._read({"type": "l2Book", "coin": self.header["wire"][market]})
                sides = [[[str(level["px"]), str(level["sz"])]
                          for level in side[:self.book_depth]] for side in raw["levels"]]
                books[market] = [int(raw["time"]) * NS_PER_MS, *sides]
            except Exception as exc:  # noqa: BLE001 - an unanswered book is absent
                failed.append(f"l2Book:{market}:{type(exc).__name__}")
        self.polls += 1
        # Stamped when the last answer arrived, never before a read: a fact is replayed
        # only from the instant it was known (Chapter II §III.b; no look-ahead).
        ts = self.clock()
        return {"ts": ts, "mids": mids, "funding": funding, "books": books,
                "failed": failed, "weight": self.weight - weight_before}


def record(recorder: Recorder, journal: Path, *, polls: int, interval_s: float,
           sleep: Callable[[float], None] = time.sleep) -> Path:
    """Write ``recorder``'s header and ``polls`` polls to ``journal``, one JSON line each,
    flushed as written, so an interrupted recording keeps every poll it finished."""
    header = {**recorder.start(), "interval_s": interval_s}
    with open(journal, "w", encoding="utf-8") as handle:
        handle.write(json.dumps(header, sort_keys=True) + "\n")
        handle.flush()
        for n in range(polls):
            started = time.monotonic()
            handle.write(json.dumps(recorder.poll(), sort_keys=True) + "\n")
            handle.flush()
            if n + 1 < polls:
                sleep(max(0.0, interval_s - (time.monotonic() - started)))
    return journal


def read_journal(path: Path) -> tuple[dict, Iterator[dict]]:
    """A recording journal's header and its polls, read one line at a time."""
    handle = open(path, encoding="utf-8")  # noqa: SIM115 - closed by the generator
    header = json.loads(handle.readline())
    if header.get("format") != JOURNAL_FORMAT:
        handle.close()
        raise ValueError(f"{path} is not a factorylab tape journal")

    def polls() -> Iterator[dict]:
        with handle:
            for line in handle:
                if line.strip():
                    yield json.loads(line)

    return header, polls()


def compact(header: dict, polls: Iterable[dict]) -> dict:
    """The tape (``TAPE_FORMAT``) a recording journal states, and nothing it does not.

    Guarantees: a tick per poll; each market's mid at every poll that answered it; a
    perp's funding rate and premium where the recording read them and they changed
    (``legacy`` regime: the venue's current rate, never a settled boundary); every
    sampled book at the venue's own book time; the listing rows the header states; and
    each market's fee rates as ``venue_read`` steps from the recording's first instant.
    """
    ticks: list[int] = []
    mids: dict[str, list] = {}
    funding: dict[str, list] = {}
    books: dict[str, dict[int, list]] = {}
    for poll in polls:
        ts = int(poll["ts"])
        ticks.append(ts)
        for market, px in poll["mids"].items():
            mids.setdefault(market, []).append([ts, px])
        for coin, (rate, premium) in poll.get("funding", {}).items():
            rows = funding.setdefault(coin, [])
            if not rows or rows[-1][1:] != [rate, premium]:
                rows.append([ts, rate, premium])
        for market, (stamp, bids, asks) in poll.get("books", {}).items():
            books.setdefault(market, {})[int(stamp)] = [bids, asks]
    if not ticks or not mids:
        raise ValueError("the recording holds no poll or no mid")
    start = ticks[0]
    provenance = [f"userFees of {header['fee_user']} at {header['ts']}"]
    fees = {market: {"venue_read": {side: [[start, rate, list(provenance)]]
                                    for side, rate in sides.items() if rate is not None}}
            for market, sides in header["fees"].items() if market in mids}
    kept = set(mids)
    return {
        "format": TAPE_FORMAT,
        "venue": header.get("source"),
        "declared_tick_ns": (round(header["interval_s"] * 1e9)
                             if header.get("interval_s") else None),
        "ticks": ticks,
        "mids": {m: mids[m] for m in header["markets"] if m in mids},
        "funding": {c: rows for c, rows in funding.items() if c in kept},
        "funding_regime": {c: "legacy" for c in header["markets"]
                           if c in kept and "/" not in c},
        "settled_funding": {},
        "books": {m: [[ts, *sides] for ts, sides in sorted(rows.items())]
                  for m, rows in books.items() if m in kept},
        "instruments": {market: [row for row in rows if row["coin"] in kept]
                        for market, rows in header["instruments"].items()},
        "fees": fees,
    }
