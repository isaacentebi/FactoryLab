"""Record a broad Hyperliquid market, read-only, as a tape a fake venue can replay.

A tape is the world, not architecture (AGENTS.md). The diary cutter (``fastloop.py
tape``) keeps only the markets a paid run traded; this records a whole universe with
batched public reads (factorylab/world/recorder.py): one ``allMids`` per perp dex a
poll, funding contexts per dex, and a bounded, rotating sample of books. It signs
nothing, holds no key and reads no account but ``userFees`` of a public address.

    uv run python scripts/record_tape.py --network mainnet --minutes 10 --interval 10 \\
        --coins '*' --coins 'xyz:*' --spot '*/USDC' --out work/tapes/breadth

writes ``<out>.journal.jsonl`` (one line a poll, flushed as it goes) and
``<out>.tape.json`` (the compact tape), and prints the tape's identity, its size and
its size per recorded hour. ``--compact <journal>`` rebuilds the tape from a journal.
"""

from __future__ import annotations

import argparse
import gzip
import json
import sys
import time
from pathlib import Path
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from factorylab.world.recorder import (  # noqa: E402
    BASE_SCHEDULE_USER,
    Recorder,
    compact,
    read_journal,
    record,
)
from factorylab.world.tape import Tape, canonical_bytes  # noqa: E402

HOSTS = {"mainnet": "https://api.hyperliquid.xyz/info",
         "testnet": "https://api.hyperliquid-testnet.xyz/info"}


def http_post(url: str, *, timeout: float = 20.0, attempts: int = 3):
    """A POST to the public info endpoint, retried on a failed or throttled answer."""
    def post(body: dict):
        delay = 0.5
        for attempt in range(attempts):
            try:
                request = Request(url, data=json.dumps(body).encode(),
                                  headers={"Content-Type": "application/json"})
                with urlopen(request, timeout=timeout) as response:
                    return json.load(response)
            except OSError:
                if attempt == attempts - 1:
                    raise
                time.sleep(delay)
                delay *= 2
        raise AssertionError("unreachable")
    return post


def summarize(tape: Tape, path: Path, journal: Path | None, weight: int | None) -> dict:
    """The tape's identity and what it costs to keep, per recorded hour.

    A tape's listing and fee rows are written once; its ticks, mids, funding rows and
    books grow with every poll. Per hour is the once part plus the per-poll part times
    the polls an hour holds at the recording's own pace.
    """
    raw = path.stat().st_size
    packed = len(gzip.compress(path.read_bytes()))
    series = ("ticks", "mids", "funding", "books")
    once = len(canonical_bytes({k: ([] if k == "ticks" else {}) if k in series else v
                                for k, v in tape.data.items()}))
    polls = len(tape.ticks)
    span = tape.end_ns - tape.start_ns
    polls_per_hour = (polls - 1) * 3_600e9 / span if span else 0.0
    per_poll = (raw - once) / polls
    ratio = packed / raw
    card = {**tape.summary(), "path": str(path), "bytes": raw, "gzip_bytes": packed,
            "markets_count": len(tape.markets), "once_bytes": once,
            "bytes_per_poll": round(per_poll), "polls_per_hour": round(polls_per_hour, 1),
            "bytes_per_hour": round(per_poll * polls_per_hour),
            "bytes_per_day": round(once + per_poll * polls_per_hour * 24),
            "gzip_bytes_per_day_estimate": round((once + per_poll * polls_per_hour * 24)
                                                 * ratio)}
    card.pop("markets", None)
    if journal is not None:
        card["journal_bytes"] = journal.stat().st_size
    if weight is not None and polls:
        card["request_weight_per_poll"] = round(weight / polls, 1)
        card["request_weight_per_minute"] = round(weight / polls * polls_per_hour / 60, 1)
    return card


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--network", choices=sorted(HOSTS), default="testnet")
    parser.add_argument("--coins", action="append", default=None,
                        help="a perp or selector (*, <dex>:*); repeat for more")
    parser.add_argument("--spot", action="append", default=None,
                        help="a BASE/USDC pair or */USDC; repeat for more")
    parser.add_argument("--minutes", type=float, default=10.0)
    parser.add_argument("--interval", type=float, default=10.0, help="seconds between polls")
    parser.add_argument("--books-per-poll", type=int, default=8)
    parser.add_argument("--book-depth", type=int, default=5)
    parser.add_argument("--funding-every", type=int, default=1,
                        help="read funding contexts every this many polls")
    parser.add_argument("--fee-user", default=BASE_SCHEDULE_USER,
                        help="the public address whose userFees the tape states")
    parser.add_argument("--out", type=Path, default=None, help="output path stem")
    parser.add_argument("--compact", type=Path, default=None,
                        help="rebuild the tape from this journal instead of recording")
    args = parser.parse_args(argv)
    if args.compact is not None:
        journal, weight = args.compact, None
        stem = Path(str(journal).removesuffix(".journal.jsonl"))
        header, polls = read_journal(journal)
    else:
        stem = args.out or ROOT / "work/tapes" / (
            f"breadth-{args.network}-{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}")
        stem.parent.mkdir(parents=True, exist_ok=True)
        journal = Path(f"{stem}.journal.jsonl")
        recorder = Recorder(http_post(HOSTS[args.network]),
                            coins=args.coins or ["*"], spot_pairs=args.spot or ["*/USDC"],
                            books_per_poll=args.books_per_poll, book_depth=args.book_depth,
                            funding_every=args.funding_every, fee_user=args.fee_user,
                            source=f"hyperliquid-{args.network}")
        polls_wanted = max(1, int(args.minutes * 60 / args.interval) + 1)
        record(recorder, journal, polls=polls_wanted, interval_s=args.interval)
        weight = recorder.weight - recorder.header["start_weight"]
        header, polls = read_journal(journal)
    data = compact(header, polls)
    tape = Tape.from_data(data)
    path = Path(f"{stem}.tape.json")
    tape.write(path)
    print(json.dumps(summarize(tape, path, journal, weight), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
