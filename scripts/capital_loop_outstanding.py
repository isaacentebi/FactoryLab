"""Print what a capital-loop run left outstanding on chain: READ-ONLY and keyless.

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
    keyless_base,
    outstanding,
)
from factorylab.runtime.worlds import load_manifest  # noqa: E402
from factorylab.world.x402 import http_request  # noqa: E402

DEFAULT_WORLD = ROOT / "worlds/edition5-capital-loop.toml"


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
    parser.add_argument("run_dir", type=Path)
    parser.add_argument("--world", type=Path, default=DEFAULT_WORLD,
                        help="the manifest whose treasury.reserve_address paid the top-ups")
    parser.add_argument("--rpc", default=None, help="Base mainnet JSON-RPC URL")
    parser.add_argument("--json", action="store_true", help="print one JSON object")
    args = parser.parse_args(argv)
    reserve = load_manifest(str(args.world)).treasury.reserve_address
    if reserve is None:
        print("the world declares no treasury.reserve_address", file=sys.stderr)
        return 2
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
