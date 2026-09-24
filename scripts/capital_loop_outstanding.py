"""Print what a capital-loop run left outstanding on chain: keyless, and read-only
unless ``--acknowledge``, ``--repair-torn``, ``--repair-damaged``, ``--speed-up`` or
``--cancel-transaction`` is given.

Guarantees nothing is signed and no signing key is read, except by
``--cancel-transaction`` and ``--speed-up``, which alone load the reserve key. The run's
diary is decrypted with its own ledger key file (``<run>/ledger.jsonl.key``), exactly as
``factorylab postmortem`` does; the reserve's key is never loaded. Base mainnet is read
through public ``eth_getBlockByNumber``, ``eth_call`` and ``eth_getLogs`` only.

For every Venice top-up authorization the run journaled (current and superseded) it
prints the nonce, ``validBefore``, the finalized head's timestamp, the USDC contract's
``authorizationState`` for the reserve and nonce, and any ``AuthorizationUsed`` debit;
then every shadow send left unconfirmed. Use it after a crash, before touching the
reserve or relaunching (docs/architecture/capital-loop-rehearsal.md, "After a crash").

    uv run python scripts/capital_loop_outstanding.py work/capital-loop/<run>

``--acknowledge NONCE`` writes to the record: after the operator has settled by hand an
authorization the launch check reports ``recorded_authorization_settled_unbooked`` (real
USDC moved and no diary booked it), it appends a resolution to the reserve's write-ahead
authorization record. It takes the reserve's lock, reads the chain keylessly, and
refuses unless finalized Base shows that recorded authorization used; it signs nothing.

    uv run python scripts/capital_loop_outstanding.py --acknowledge 0x<nonce>

``--repair-torn`` does too: when a launch refuses ``authorization_record_torn`` (a
crash left a fragment at the end of the record), it takes the lock, moves the fragment
to a new ``.torn-<seconds>-<random>`` sidecar (nothing is deleted) and records it as a
``torn`` entry whose nonce-like values stay open until the chain resolves them.

``--repair-damaged`` does the same for every line that is not a whole entry, a damaged
middle line included (a launch refuses ``authorization_record_unreadable``).

A launch refuses ``recorded_transaction_may_still_execute`` while a recorded reserve-key
transaction's account nonce is unused on its chain. Its exits load the reserve key from
``reserve.key`` in the working directory (as every signer reads it) and replace the
transaction at its recorded nonce, recorded ahead and sent under the reserve's lock:

``--speed-up 0xHASH`` re-signs the identical call at a higher fee: the only exit for a
stuck CCTP mint, and the gentle one for anything else.

``--cancel-transaction 0xHASH --i-understand-the-world-step-is-abandoned`` signs a
0-value transfer to the reserve itself at that nonce instead. Never for a mint, and
only once the world that recorded it has ended: that world's treasury step will not
complete, and must be recovered by hand.

    uv run python scripts/capital_loop_outstanding.py --speed-up 0x<hash>

``--rpc-base``, ``--rpc-hyperevm``, ``--rpc-base-sepolia`` and ``--rpc-hyperevm-testnet``
replace each chain's public RPC.
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
    CANCEL_CONSEQUENCE,
    CapitalLoopRefused,
    ReserveLock,
    _chains,
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
#: A replacement's default gas cap: this many times what the replacement itself costs at
#: the node's gas price now (its gas limit, and on Base its L1 data fee), never more than
#: ``MAX_DEFAULT_REPLACEMENT_WEI``. ``--max-gas-wei`` sets it instead.
REPLACEMENT_BUDGET_MULTIPLE = 10
MAX_DEFAULT_REPLACEMENT_WEI = 10**16
#: The chain-id order of the four RPC override flags, as ``rpcs`` maps them.
RPC_FLAGS = (("rpc_base", 8453), ("rpc_hyperevm", 999), ("rpc_base_sepolia", 84532),
             ("rpc_hyperevm_testnet", 998))


def _target(lock: ReserveLock, tx_hash: str, account, reason: str):
    """The open recorded transaction ``tx_hash`` names, and the least price a replacement
    at its nonce must pay: 12.5% above every price the record holds for that nonce.

    Refuses (``reason``) unless it is an open, whole transaction entry of this record,
    with its chain and account nonce known, signed by ``account``'s reserve.
    """
    key = tx_hash.lower()
    entries = lock.authorizations()
    entry = open_transactions(entries).get(key)

    def refused(why: str) -> CapitalLoopRefused:
        return CapitalLoopRefused(reason, {"tx_hash": key, "why": why})

    if entry is None:
        raise refused("not an open transaction of the record")
    if (entry["kind"] != "transaction" or entry.get("chain_id") not in _chains()
            or type(entry.get("tx_nonce")) is not int):
        raise refused("its chain or account nonce is not known; it resolves by waiting")
    if str(entry["from"]).lower() != account.address.lower():
        raise refused("the key given is not the reserve that signed it")
    prices = [e.get("gas_price") for e in entries
              if e["kind"] == "transaction" and e.get("chain_id") == entry["chain_id"]
              and e.get("tx_nonce") == entry["tx_nonce"]
              and str(e.get("from", "")).lower() == str(entry["from"]).lower()]
    floor = max([(p * 9 + 7) // 8 for p in prices if type(p) is int and p > 0] or [0])
    return key, entry, floor, refused


def _replace(lock: ReserveLock, entry: dict, floor: int, *, account, transport, rpcs,
             gas_budget_wei: int | None, to: str, data: str, origin: str) -> dict:
    """Sign ``to``/``data`` at ``entry``'s nonce, recorded ahead and sent under the lock.

    A pure replacement at the recorded nonce: nothing of the mempool is read. Refuses
    ``replacement_needs_native_gas`` (naming the chain and the wei) when the reserve
    cannot pay one replacement on that chain.
    """
    from factorylab.world.evm import EVM, _with_headroom, calldata

    chain_id = entry["chain_id"]
    chain = EVM(_chains()[chain_id], account, transport=transport,
                rpc=(rpcs or {}).get(chain_id))
    chain.transaction_guard = lock.authorization_log(None, origin=origin)
    chain.check_chain()
    gas = 21_000 if data == "0x" else int(chain.call("eth_estimateGas", [{
        "from": account.address, "to": to, "value": "0x0", "data": data}]), 16)
    price = max(_with_headroom(int(chain.call("eth_gasPrice", []), 16)), floor)
    one = (gas * 12 + 9) // 10 * price  # the gas limit prepare signs, at its price
    if chain_id in (8453, 84532):  # and Base's L1 data fee, as prepare bounds it
        size = len(bytes.fromhex(data.removeprefix("0x"))) + 120
        bound = chain.read("0x420000000000000000000000000000000000000F",
                           calldata("getL1FeeUpperBound(uint256)", ["uint256"], [size]))
        one += int.from_bytes(bound) * 2
    held = chain.balance()
    if held < one:
        raise CapitalLoopRefused("replacement_needs_native_gas", {
            "chain_id": chain_id, "reserve": account.address, "needed_wei": one,
            "balance_wei": held, "rpc": chain.rpc})
    budget = (gas_budget_wei if gas_budget_wei is not None
              else min(REPLACEMENT_BUDGET_MULTIPLE * one, MAX_DEFAULT_REPLACEMENT_WEI))
    chain.gas_budget_wei = budget
    reference = chain.prepare(to, data, gas_remaining_wei=budget, nonce=entry["tx_nonce"],
                              min_gas_price=floor)
    chain.broadcast(reference)
    return {**reference, "max_gas_wei": budget}


def cancel_transaction(lock: ReserveLock, tx_hash: str, *, account, transport,
                       rpcs: dict | None = None, gas_budget_wei: int | None = None,
                       abandon: bool = False) -> dict:
    """Consume a recorded, unexecuted reserve-key transaction's nonce with a no-op.

    Guarantees a CCTP mint is never cancelled (its burn would stay with nothing minted;
    its exit is ``speed_up``), nor a transaction whose call was not recorded (it may be
    one), nor one whose world is still running (its diary's writer lock is held), nor
    anything without ``abandon``: the world's step it belonged to then never completes
    (``CANCEL_CONSEQUENCE``). Otherwise refuses ``cancel_refused`` and signs nothing.
    The cancellation is a 0-value transfer from the reserve to itself at the recorded
    nonce, priced 12.5% above every price the record holds for it, recorded ahead
    (origin ``cancel_transaction``) and sent under this held lock.
    """
    key, entry, floor, refused = _target(lock, tx_hash, account, "cancel_refused")
    step = entry.get("step")
    if step == "mint":
        raise refused("a CCTP mint is never cancelled: its burn would stay with nothing "
                      "minted. Its exit is --speed-up")
    if step is None or entry.get("data") is None:
        raise refused("its call was not recorded, so it may be a CCTP mint: it is never "
                      "cancelled; it resolves by waiting")
    ledger = entry.get("ledger")
    if ledger and Path(ledger).exists():
        from factorylab.kernel.ledger import LedgerBusyError, LedgerLock

        try:
            LedgerLock(ledger).close()
        except LedgerBusyError:
            raise refused("the world that recorded it is still running: stop it first"
                          ) from None
    if not abandon:
        raise refused(f"{CANCEL_CONSEQUENCE}; to accept that, pass "
                      "--i-understand-the-world-step-is-abandoned")
    reference = _replace(lock, entry, floor, account=account, transport=transport,
                         rpcs=rpcs, gas_budget_wei=gas_budget_wei, to=account.address,
                         data="0x", origin="cancel_transaction")
    return {"canceled": key, "cancel_tx_hash": reference["tx_hash"],
            "chain_id": entry["chain_id"], "tx_nonce": entry["tx_nonce"],
            "gas_price": reference["tx"]["gasPrice"], "max_gas_wei": reference["max_gas_wei"],
            "consequence": CANCEL_CONSEQUENCE}


def speed_up(lock: ReserveLock, tx_hash: str, *, account, transport,
             rpcs: dict | None = None, gas_budget_wei: int | None = None) -> dict:
    """Re-sign a recorded, unexecuted reserve-key transaction's own call at a higher fee.

    Guarantees the replacement is the identical call (destination, calldata, value 0)
    at the recorded nonce, priced 12.5% above every price the record holds for it,
    recorded ahead (origin ``speed_up``) and sent under this held lock; so whichever of
    them executes does exactly what was recorded. The only exit for a stuck CCTP mint.
    A transaction whose call was not recorded refuses ``speed_up_refused``.
    """
    key, entry, floor, refused = _target(lock, tx_hash, account, "speed_up_refused")
    if entry.get("data") is None or entry.get("to") is None:
        raise refused("its call was not recorded; it cannot be re-signed")
    if int(entry.get("value") or 0) != 0:
        raise refused("only a 0-value call is signed here")
    reference = _replace(lock, entry, floor, account=account, transport=transport,
                         rpcs=rpcs, gas_budget_wei=gas_budget_wei, to=entry["to"],
                         data=entry["data"], origin="speed_up")
    return {"sped_up": key, "speed_up_tx_hash": reference["tx_hash"],
            "chain_id": entry["chain_id"], "tx_nonce": entry["tx_nonce"],
            "step": entry.get("step"), "gas_price": reference["tx"]["gasPrice"],
            "max_gas_wei": reference["max_gas_wei"]}


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
    parser.add_argument("--rpc-base", "--rpc", dest="rpc_base", default=None,
                        help="Base mainnet JSON-RPC URL")
    parser.add_argument("--rpc-hyperevm", default=None, help="HyperEVM JSON-RPC URL")
    parser.add_argument("--rpc-base-sepolia", default=None, help="Base Sepolia JSON-RPC URL")
    parser.add_argument("--rpc-hyperevm-testnet", default=None,
                        help="HyperEVM testnet JSON-RPC URL")
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
    parser.add_argument("--speed-up", metavar="TX_HASH",
                        help="re-sign a recorded, unexecuted reserve-key transaction's own "
                        "call at its nonce with a higher fee (the reserve key from "
                        "reserve.key; takes the reserve's lock)")
    parser.add_argument("--cancel-transaction", metavar="TX_HASH",
                        help="consume a recorded, unexecuted reserve-key transaction's "
                        "nonce with a 0-value self-transfer: never a CCTP mint, only once "
                        "its world has ended, and only with "
                        "--i-understand-the-world-step-is-abandoned")
    parser.add_argument("--i-understand-the-world-step-is-abandoned", dest="abandon",
                        action="store_true", help=CANCEL_CONSEQUENCE)
    parser.add_argument("--max-gas-wei", type=int, default=None,
                        help="the most a replacement may spend on gas (default: "
                        f"{REPLACEMENT_BUDGET_MULTIPLE}x the replacement's own cost at the "
                        f"node's gas price now, at most {MAX_DEFAULT_REPLACEMENT_WEI} wei)")
    parser.add_argument("--lock-dir", type=Path, default=None, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    reserve = load_manifest(str(args.world)).treasury.reserve_address
    if reserve is None:
        print("the world declares no treasury.reserve_address", file=sys.stderr)
        return 2
    rpcs = {chain_id: getattr(args, name) for name, chain_id in RPC_FLAGS
            if getattr(args, name)}
    base_rpc = args.rpc_base or BASE_RPC
    if args.cancel_transaction or args.speed_up:
        import os

        from eth_account import Account

        from factorylab.runtime.cli import _load_dotenv

        _load_dotenv()  # the reserve key from reserve.key, as every signer reads it
        try:
            account = Account.from_key(os.environ["RESERVE_PRIVATE_KEY"])
        except Exception:  # noqa: BLE001 - never echo what was read
            print("the reserve key is missing or invalid (reserve.key)", file=sys.stderr)
            return 2
        what = "cancel" if args.cancel_transaction else "speed_up"
        try:
            with ReserveLock(reserve, lock_dir=args.lock_dir) as lock:
                if args.cancel_transaction:
                    done = cancel_transaction(lock, args.cancel_transaction, account=account,
                                              transport=transport, rpcs=rpcs,
                                              gas_budget_wei=args.max_gas_wei,
                                              abandon=args.abandon)
                else:
                    done = speed_up(lock, args.speed_up, account=account,
                                    transport=transport, rpcs=rpcs,
                                    gas_budget_wei=args.max_gas_wei)
        except CapitalLoopRefused as exc:
            print(json.dumps({"error": exc.reason, **exc.detail}), file=sys.stderr)
            return 2
        except Exception as exc:  # noqa: BLE001 - a rail refusal names no key material
            print(json.dumps({"error": f"{what}_failed", "why": str(exc)}), file=sys.stderr)
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
                                   rpc=base_rpc)
        except CapitalLoopRefused as exc:
            print(json.dumps({"error": exc.reason, **exc.detail}), file=sys.stderr)
            return 2
        print(json.dumps(done))
        return 0
    if args.run_dir is None:
        parser.error("a run directory, or --acknowledge NONCE, is required")
    try:
        report = outstanding(args.run_dir, reserve_address=reserve,
                             base=keyless_base(transport=transport, rpc=base_rpc))
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
