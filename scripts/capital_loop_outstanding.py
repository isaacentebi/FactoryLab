"""Print what a capital-loop run left outstanding on chain: keyless, and read-only
unless ``--acknowledge``, ``--repair-torn``, ``--repair-damaged`` or
``--cancel-transaction`` is given.

Guarantees nothing is signed and no signing key is read, except by
``--cancel-transaction``, which alone loads the reserve key. The run's diary is decrypted
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

``--repair-damaged`` does the same for every line that is not a whole entry, a damaged
middle line included (a launch refuses ``authorization_record_unreadable``).

``--cancel-transaction 0xHASH`` is the exit for a recorded reserve-key transaction that
was dropped unmined (a launch refuses ``recorded_transaction_may_still_execute`` while
its account nonce is unused on its chain): with ``RESERVE_PRIVATE_KEY`` set, it signs a
0-value transfer to the reserve itself at that nonce, priced at least 12.5% above the
stuck one, records it ahead like every reserve-key transaction, and sends it under the
reserve's lock. Once either is finalized the nonce is consumed and both resolve.

    RESERVE_PRIVATE_KEY=... uv run python scripts/capital_loop_outstanding.py \
        --cancel-transaction 0x<hash>
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
    open_transactions,
    outstanding,
    repair_damaged,
    repair_torn,
)
from factorylab.runtime.worlds import load_manifest  # noqa: E402
from factorylab.world.x402 import BASE_RPC, http_request  # noqa: E402

DEFAULT_WORLD = ROOT / "worlds/edition6-capital-loop.toml"
#: The most a cancellation may spend on gas, in wei, unless --max-gas-wei says otherwise.
DEFAULT_CANCEL_GAS_WEI = 10**15


def cancel_transaction(lock: ReserveLock, tx_hash: str, *, account, transport,
                       gas_budget_wei: int = DEFAULT_CANCEL_GAS_WEI) -> dict:
    """Consume a recorded, unmined reserve-key transaction's account nonce with a no-op.

    Guarantees only an open transaction of this reserve's record, with its chain and
    account nonce known and signed by ``account``, is cancelled, and only while that
    nonce is unused at the chain's latest block; anything else refuses
    (``cancel_refused``) and nothing is signed. The cancellation is a 0-value transfer
    from the reserve to itself at the same nonce, priced at least 12.5% above what the
    record or the node knows of the stuck one, prepared through ``EVM.prepare`` (so it is
    recorded ahead, origin ``cancel_transaction``) and sent through ``EVM.broadcast``
    under this held lock. Whichever of the two the chain mines consumes the nonce.
    """
    from factorylab.runtime.capital_loop import _chains
    from factorylab.world.evm import EVM

    key = tx_hash.lower()
    entry = open_transactions(lock.authorizations()).get(key)

    def refused(why: str) -> CapitalLoopRefused:
        return CapitalLoopRefused("cancel_refused", {"tx_hash": key, "why": why})

    if entry is None:
        raise refused("not an open transaction of the record")
    if entry.get("chain_id") not in _chains() or type(entry.get("tx_nonce")) is not int:
        raise refused("its chain or account nonce is not known; it resolves by waiting")
    if str(entry["from"]).lower() != account.address.lower():
        raise refused("the key given is not the reserve that signed it")
    chain = EVM(_chains()[entry["chain_id"]], account, transport=transport,
                gas_budget_wei=gas_budget_wei)
    chain.transaction_guard = lock.authorization_log(None, origin="cancel_transaction")
    used = int(chain.call("eth_getTransactionCount", [account.address, "latest"]), 16)
    if used > entry["tx_nonce"]:
        raise refused("its nonce is already used at the latest block: wait for finality")
    prices = [entry.get("gas_price")]
    seen = chain.call("eth_getTransactionByHash", [key])
    if isinstance(seen, dict) and seen.get("gasPrice"):
        prices.append(int(seen["gasPrice"], 16))
    floor = max([(p * 9 + 7) // 8 for p in prices if type(p) is int and p > 0] or [0])
    reference = chain.prepare(account.address, "0x", gas_remaining_wei=gas_budget_wei,
                              nonce=entry["tx_nonce"], min_gas_price=floor)
    chain.broadcast(reference)
    return {"canceled": key, "cancel_tx_hash": reference["tx_hash"],
            "chain_id": entry["chain_id"], "tx_nonce": entry["tx_nonce"],
            "gas_price": reference["tx"]["gasPrice"]}


def verdict(row: dict) -> str:
    """One word an operator can act on."""
    if "unreadable" in row:
        return "UNREADABLE: treat as live"
    if row.get("canceled"):
        return "canceled (dead)"
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
    parser.add_argument("--repair-damaged", action="store_true",
                        help="set every line of the record that is not a whole entry aside "
                        "to its own sidecar and keep what it may have recorded open (takes "
                        "the reserve's lock; deletes nothing)")
    parser.add_argument("--cancel-transaction", metavar="TX_HASH",
                        help="with RESERVE_PRIVATE_KEY set: consume a recorded, unmined "
                        "reserve-key transaction's nonce with a 0-value self-transfer "
                        "(takes the reserve's lock)")
    parser.add_argument("--max-gas-wei", type=int, default=DEFAULT_CANCEL_GAS_WEI,
                        help="the most --cancel-transaction may spend on gas")
    parser.add_argument("--lock-dir", type=Path, default=None, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    reserve = load_manifest(str(args.world)).treasury.reserve_address
    if reserve is None:
        print("the world declares no treasury.reserve_address", file=sys.stderr)
        return 2
    if args.cancel_transaction:
        import os

        from eth_account import Account

        try:
            account = Account.from_key(os.environ["RESERVE_PRIVATE_KEY"])
        except Exception:  # noqa: BLE001 - never echo what was read
            print("RESERVE_PRIVATE_KEY is missing or invalid", file=sys.stderr)
            return 2
        try:
            with ReserveLock(reserve, lock_dir=args.lock_dir) as lock:
                done = cancel_transaction(lock, args.cancel_transaction, account=account,
                                          transport=transport, gas_budget_wei=args.max_gas_wei)
        except CapitalLoopRefused as exc:
            print(json.dumps({"error": exc.reason, **exc.detail}), file=sys.stderr)
            return 2
        except Exception as exc:  # noqa: BLE001 - a rail refusal names no key material
            print(json.dumps({"error": "cancel_failed", "why": str(exc)}), file=sys.stderr)
            return 2
        print(json.dumps(done))
        return 0
    if args.repair_torn or args.repair_damaged:
        try:
            with ReserveLock(reserve, lock_dir=args.lock_dir) as lock:
                done = (repair_damaged if args.repair_damaged else repair_torn)(lock)
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
    if "--cancel-transaction" in sys.argv:
        from factorylab.runtime.cli import _load_dotenv

        _load_dotenv()  # the reserve key from its file, as every signer reads it
    raise SystemExit(main())
