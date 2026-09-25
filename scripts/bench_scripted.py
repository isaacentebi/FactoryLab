"""Time the offline scripted world and fingerprint what it wrote.

Each size runs the ``scripted`` manifest in memory with a fixed seed, exactly as
``factorylab run --world scripted --events N --seed S`` does, and prints the wall
time with two sha256 digests: one over the canonical bytes of every ledger item in
append order (with its sequence number), one over the canonical run summary. Two
builds that print the same digests for the same size and seed wrote the same diary.

The one field that must differ between two builds is ``release_digest``: it is a
hash of the executing source tree (``runtime/release.py``), ledgered in ``Launch``
and in every checkpoint. The ledger digest replaces that value with ``RELEASE``
before hashing, and leaves out a ``snapshot`` item's hash and size of the checkpoint
state (which holds the digest), so it compares behaviour, not source bytes.

    uv run python scripts/bench_scripted.py                 # 50 100 200 500
    uv run python scripts/bench_scripted.py 50 100 --seed 1 --json out.json
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time

from factorylab.kernel.ledger import canonical
from factorylab.runtime.loop import Runtime
from factorylab.runtime.worlds import load_manifest


def run_once(events: int, seed: int, world: str = "scripted",
             summary_dir: str | None = None) -> dict:
    """Run one world and return its wall time, item count and digests."""
    manifest = load_manifest(world)
    rt = Runtime(manifest, events=events, seed=seed, initial_balance_micro=None,
                 ledger_path=None, router_gamma=0.1)
    items = hashlib.sha256()
    count = 0
    append = rt.ledger.append
    release = (rt.release_digest or "").encode()

    def capture(item):
        nonlocal count
        seq = append(item)
        entry = dict(item, seq=seq)
        if entry.get("kind") == "snapshot":
            entry.pop("state_sha", None)
            entry.pop("bytes", None)
        data = canonical(entry)
        if release:
            data = data.replace(release, b"RELEASE")
        items.update(data)
        items.update(b"\n")
        count += 1
        return seq

    rt.ledger.append = capture
    start = time.perf_counter()
    try:
        summary = rt.run()
    finally:
        rt.ledger.append = append
        rt._ledger_lock.close()
    elapsed = time.perf_counter() - start
    if summary_dir:
        with open(f"{summary_dir}/{world}-{events}-{seed}.json", "w") as fh:
            json.dump(summary, fh, indent=2, sort_keys=True, default=str)
    return {
        "events": events,
        "seed": seed,
        "seconds": round(elapsed, 2),
        "items": count,
        "ledger_sha256": items.hexdigest(),
        "summary_sha256": hashlib.sha256(
            json.dumps(summary, sort_keys=True, default=str).encode()).hexdigest(),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("sizes", nargs="*", type=int, default=[50, 100, 200, 500])
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--world", default="scripted")
    parser.add_argument("--json", default=None, help="also write the rows to this file")
    parser.add_argument("--summaries", default=None,
                        help="write each run's summary to <dir>/<world>-<events>-<seed>.json")
    args = parser.parse_args(argv)
    rows = []
    print(f"{'events':>6} {'seconds':>9} {'items':>7}  ledger_sha256[:16]  summary_sha256[:16]")
    for n in args.sizes:
        row = run_once(n, args.seed, args.world, args.summaries)
        rows.append(row)
        print(f"{n:>6} {row['seconds']:>9.2f} {row['items']:>7}  "
              f"{row['ledger_sha256'][:16]}    {row['summary_sha256'][:16]}", flush=True)
    if args.json:
        with open(args.json, "w") as fh:
            json.dump(rows, fh, indent=2)
    return 0


if __name__ == "__main__":
    sys.exit(main())
