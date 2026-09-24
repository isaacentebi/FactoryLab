"""Print what a capital-loop run left outstanding on chain: keyless, and read-only
unless ``--acknowledge`` or ``--repair-torn`` is given.

Guarantees nothing is signed and no signing key is read. The run's diary is decrypted
with its own ledger key file (``<run>/ledger.jsonl.key``), exactly as
``factorylab postmortem`` does; the reserve's key is never loaded. Base mainnet is read
through public ``eth_getBlockByNumber``, ``eth_call`` and ``eth_getLogs`` only.

For every Venice top-up authorization the run journaled (current and superseded) it
prints the nonce, ``validBefore``, the finalized head's timestamp, the USDC contract's
``authorizationState`` for the reserve and nonce, and any ``AuthorizationUsed`` debit;
then every shadow send left unconfirmed. Use it after a crash, before touching the
reserve or relaunching (docs/architecture/capital-loop-rehearsal.md, "After a crash").

    uv run python scripts/capital_loop_outstanding.py work/capital-loop/<run>

``--acknowledge NONCE`` is the one write: after the operator has settled by hand an
authorization the launch check reports ``recorded_authorization_settled_unbooked`` (real
USDC moved and no diary booked it), it appends a resolution to the reserve's write-ahead
authorization record. It takes the reserve's lock, reads the chain keylessly, and
refuses unless finalized Base shows that recorded authorization used; it signs nothing.

    uv run python scripts/capital_loop_outstanding.py --acknowledge 0x<nonce>

``--repair-torn`` is the other: when a launch refuses ``authorization_record_torn`` (a
crash left a fragment at the end of the record), it takes the lock, moves the fragment
to a ``.torn-<seconds>`` sidecar (nothing is deleted) and records it as a ``torn`` entry
whose nonce-like values stay open until the chain resolves them.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from factorylab.runtime.capital_loop import (  # noqa: E402
    CapitalLoopRefused,
    ReserveLock,
    acknowledge,
    keyless_base,
    outstanding,
    repair_torn,
)
from factorylab.runtime.worlds import load_manifest  # noqa: E402
from factorylab.world.x402 import BASE_RPC, http_request  # noqa: E402

DEFAULT_WORLD = ROOT / "worlds/edition6-capital-loop.toml"


def verdict(row: dict) -> str:
    """One word an operator can act on."""
    if "unreadable" in row:
        return "UNREADABLE: treat as live"
    if row.get("authorization_used") or row.get("debits"):
        return "settled (debited)"
    if row.get("live"):
        return "LIVE: may still settle"
    return "expired unused" if row.get("expired") else "past validBefore; scan incomplete"


def main(argv: list[str] | None = None, *, transport=http_request) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("run_dir", type=Path, nargs="?")
    parser.add_argument("--world", type=Path, default=DEFAULT_WORLD,
                        help="the manifest whose treasury.reserve_address paid the top-ups")
    parser.add_argument("--rpc", default=None, help="Base mainnet JSON-RPC URL")
    parser.add_argument("--json", action="store_true", help="print one JSON object")
    parser.add_argument("--acknowledge", metavar="NONCE",
                        help="after settling its books by hand: mark a recorded "
                        "authorization finalized Base shows used as resolved (takes the "
                        "reserve's lock, so no capital-loop run may be alive)")
    parser.add_argument("--repair-torn", action="store_true",
                        help="set a torn last line of the write-ahead authorization record "
                        "aside to a .torn-<seconds> sidecar and record its nonces as open "
                        "(takes the reserve's lock; deletes nothing)")
    parser.add_argument("--lock-dir", type=Path, default=None, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    reserve = load_manifest(str(args.world)).treasury.reserve_address
    if reserve is None:
        print("the world declares no treasury.reserve_address", file=sys.stderr)
        return 2
    if args.repair_torn:
        try:
            with ReserveLock(reserve, lock_dir=args.lock_dir) as lock:
                done = repair_torn(lock)
        except CapitalLoopRefused as exc:
            print(json.dumps({"error": exc.reason, **exc.detail}), file=sys.stderr)
            return 2
        print(json.dumps(done))
        return 0
    if args.acknowledge:
        try:
            with ReserveLock(reserve, lock_dir=args.lock_dir) as lock:
                done = acknowledge(lock, args.acknowledge, transport=transport,
                                   rpc=args.rpc or BASE_RPC)
        except CapitalLoopRefused as exc:
            print(json.dumps({"error": exc.reason, **exc.detail}), file=sys.stderr)
            return 2
        print(json.dumps(done))
        return 0
    if args.run_dir is None:
        parser.error("a run directory, or --acknowledge NONCE, is required")
    try:
        report = outstanding(args.run_dir, reserve_address=reserve,
                             base=keyless_base(transport=transport, rpc=args.rpc))
    except CapitalLoopRefused as exc:
        print(json.dumps({"error": exc.reason, **exc.detail}), file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(report, indent=2, default=str))
    else:
        print(f"run {report['run_dir']}  reserve {reserve}")
        for row in report["top_ups"]:
            print(f"top-up {row['transfer_id']}  nonce {row['nonce']}  "
                  f"validBefore {row['valid_before']}  "
                  f"finalized_ts {row.get('finalized_timestamp')}  "
                  f"authorizationState {row.get('authorization_used')}  "
                  f"debits {row.get('debits')}  -> {verdict(row)}")
        if not report["top_ups"]:
            print("no top-up authorization was journaled")
        for shadow in report["shadow_sends"]:
            print(f"shadow send pending {shadow['transfer_id']}  nonce {shadow['nonce']}  "
                  f"sink {shadow['sink']}  amount_micro {shadow['amount_micro']}")
        if not report["shadow_sends"]:
            print("no shadow send pending")
    live = any("unreadable" in r or r.get("live") for r in report["top_ups"])
    return 1 if live or report["shadow_sends"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
