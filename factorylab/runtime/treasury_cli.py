"""Testnet-only treasury acceptance with the same encrypted journal and replay rules as worlds."""

from __future__ import annotations

import fcntl
import json
import os
from pathlib import Path
from time import time_ns

from factorylab.kernel.ledger import Ledger
from factorylab.kernel.money import usd_to_money
from factorylab.kernel.wallet import Wallet
from factorylab.runtime.resume import (
    JournalProxy,
    RecoveryJournal,
    ResumeError,
    _ReplayFault,
    decode,
    encode,
)
from factorylab.runtime.shared import SimClock
from factorylab.world.evm import RailError
from factorylab.world.treasury import Treasury


def world_ledger_exists(root: Path, explicit: str | None = None) -> bool:
    """An explicit ledger or any encrypted world journal in this workspace ends seed access."""
    if explicit is not None and Path(explicit).exists():
        return True
    for directory, names, files in os.walk(root):
        names[:] = [name for name in names if name not in (".git", ".venv", "node_modules")]
        for name in files:
            if not name.endswith(".jsonl"):
                continue
            try:
                with (Path(directory) / name).open("rb") as stream:
                    header = json.loads(stream.readline())
                if (isinstance(header, dict) and header.get("format") == 1
                        and "genesis_hash" in header):
                    return True
            except (OSError, ValueError):
                # A journal in the declared run directory is not safe to bypass
                # merely because creation was interrupted or it cannot be read.
                if Path(directory).name == "runs":
                    return True
    return False


class AcceptanceSession:
    """Persist every input and response; recover bookkeeping without inventing another transfer."""

    def __init__(self, path: Path, rail, config: dict):
        self.path, self.clock, self.config = path, SimClock(), config
        existing = path.exists()
        if existing:
            ledger = Ledger.reopen(path, manifest=config, clock_ns=self.clock)
            items = ledger._recovery_items()
            saved = next((i for i in reversed(items)
                          if i.get("kind") == "treasury.acceptance.snapshot"), None)
            if saved is None:
                raise ResumeError("acceptance journal has no checkpoint; refusing a new transfer")
            initial = decode(saved["wallet"])["initial"]
        else:
            balances = rail.balances()
            initial = balances["venue"] + balances["reserve"]
            ledger = Ledger(path, manifest=config, clock_ns=self.clock,
                            key_path=str(path) + ".key")
            saved, items = None, []
        self.journal = RecoveryJournal(ledger, self.clock)
        self.journal.bootstrap = existing
        self.wallet = Wallet(initial, self.journal, clock_ns=self.clock)
        self.treasury = Treasury(
            self.journal, self.wallet, JournalProxy(rail, self.journal, "treasury.rail"),
            fee_ceiling_micro=config["max_transfer_fee_micro"],
            max_forward_fees_per_window=config.get("max_forward_fees_per_window", 1_000_000),
        )
        self.wallet.bind_pots(self.treasury.pots)
        if saved:
            self.wallet._restore_state(decode(saved["wallet"]))
            self.treasury.restore(decode(saved["treasury"]))
            self.clock.now_ns = saved["now_ns"]
        self.journal.bootstrap = False
        self.journal.active = True
        if saved:
            self.journal.tail = items[saved["seq"] + 1:]
            self.journal.recovering = True
            try:
                while (item := self.journal.peek()) is not None:
                    if item.get("kind") != "treasury.acceptance.input":
                        raise ResumeError("unexpected acceptance journal tail")
                    self.execute(item["operation"], item["now_ns"])
            except _ReplayFault as exc:
                raise ResumeError(str(exc)) from None
            self.journal.recovering = False
            self.journal.tail = []
            self.journal.position = 0
        else:
            self.treasury.refresh_pots()
            self.checkpoint()

    def checkpoint(self):
        """Checkpoint public references and authenticated wallet holds, never signing clients."""
        self.journal.append({"kind": "treasury.acceptance.snapshot", "now_ns": self.clock.now_ns,
                             "wallet": encode(self.wallet.state()),
                             "treasury": encode(self.treasury.snapshot())})

    def execute(self, operation: dict, now_ns: int) -> dict:
        """One transfer or one reconciliation tick is a replayable unit of work."""
        self.clock.now_ns = max(now_ns, self.clock.now_ns)
        self.journal.append({"kind": "treasury.acceptance.input", "operation": operation,
                             "now_ns": self.clock.now_ns})
        if operation["kind"] == "transfer":
            result = self.treasury.transfer(operation["direction"], operation["usd"],
                                            handle="testnet-acceptance", now_ns=self.clock.now_ns)
        elif operation["kind"] == "advance":
            # One advance is one tick of this session's world: a stall's age and a
            # conversion's latency count advances, checkpointed with the treasury, so a
            # resumed session neither strands a stall early nor forgets its age.
            self.treasury.tick_index += 1
            result = {"completed": self.treasury.tick(self.clock.now_ns)}
        else:
            raise ValueError("unknown acceptance operation")
        self.checkpoint()
        return {"result": result, **self.status()}

    def status(self) -> dict:
        """Only confirmed receipt references are reported as completed transactions."""
        state = self.treasury.state
        return {
            "status": state["status"] if state else "idle",
            "step": state["steps"][state["index"]] if state else None,
            "transfer_id": state["id"] if state else None,
            "amount_micro": state["amount_micro"] if state else None,
            "received_micro": state["received_micro"] if state else None,
            "fees_micro": state["fees_micro"] if state else 0,
            "tx_refs": state["receipts"] if state else [],
            "pending_reference": state["reference"] if state and state["status"] == "submitted"
            else None,
            "wallet_micro": self.wallet.balance,
            "pots": self.wallet.pots(),
        }


def probe(rail, path: Path, config: dict, *, usd: str | None = None) -> dict:
    """Read both balances, the on-chain fee quotes and the branch the next exit would take.

    Seconds, and read-only: no transaction, no signature, no journal write. With an
    amount, the real preflight and the unsigned withdrawal reference are printed and
    discarded, exactly as a world would prepare them. Gas already spent is read from
    the journal's last checkpoint when one exists.
    """
    gas_spent, journal = {}, "absent"
    if path.exists():
        try:
            items = Ledger.reopen(path, manifest=config)._recovery_items()
            saved = next((i for i in reversed(items)
                          if i.get("kind") == "treasury.acceptance.snapshot"), None)
            gas_spent = decode(saved["treasury"])["gas_spent"] if saved else {}
            journal = "read"
        except Exception as exc:  # a journal under another config still leaves the probe read-only
            journal = f"unreadable ({type(exc).__name__})"
    result = {"reserve_address": rail.reserve_address, "venue_address": rail.venue_address,
              "networks": [998, 84532], "forwarding": rail.spec.cctp_forwarding,
              "journal": journal, "gas_spent_wei": gas_spent, "balances": rail.balances(),
              "reserve_hype_wei": rail.hyper.balance(), "gas": rail.gas_view(gas_spent)}
    if usd is not None:
        amount = usd_to_money(str(usd))
        dry_run = {"direction": "to_reserve", "amount_micro": amount,
                   "signed": False, "broadcast": False}
        try:
            rail.preflight("to_reserve", amount, gas_spent)
            reference = rail.prepare("withdraw_burn", {
                "received_micro": amount, "nonce": time_ns() // 1_000_000, "route_data": {},
            }, gas_spent)
            dry_run.update(preflight="ok", reference=reference)
        except RailError as exc:
            dry_run["refused"] = str(exc)
        result["dry_run"] = dry_run
    return result


def command(args) -> int:
    """The acceptance CLI can only select Hyperliquid testnet, HyperEVM testnet and Base Sepolia."""
    from eth_account import Account

    from factorylab.runtime.worlds import TreasurySpec
    from factorylab.world.exchange import HyperliquidExchange
    from factorylab.world.treasury_rails import LiveRail

    reserve = Account.from_key(os.environ["RESERVE_PRIVATE_KEY"]).address
    spec = TreasurySpec(reserve_address=reserve, hyperevm_gas_budget_wei=5 * 10**16,
                        base_gas_budget_wei=10**15,
                        cctp_forwarding=getattr(args, "forwarding", "on_empty_gas"))
    exchange = HyperliquidExchange(mainnet=False, coins=("ETH",))
    rail = LiveRail(exchange, spec)
    # A real check, not an assert: ``python -O`` strips asserts, and this is the
    # only thing between the acceptance CLI and a mainnet rail.
    if not (rail.testnet and rail.hyper.chain.id == 998 and rail.base.chain.id == 84532):
        raise RailError("the acceptance CLI runs on testnet only (HyperEVM 998, Base Sepolia)")
    from factorylab.runtime.capital_loop import ReserveGuard

    # Every reserve-key transaction is written ahead under the reserve's lock, or never
    # prepared: a capital-loop run holding the reserve refuses it.
    rail.bind_guard(ReserveGuard("treasury_cli"))
    config = {"name": "treasury-testnet-acceptance", "format": 1, "venue": rail.venue_address,
              "reserve": reserve, "networks": [998, 84532],
              "max_transfer_fee_micro": spec.max_transfer_fee_micro,
              "withdrawal_fee_micro": spec.withdrawal_fee_micro,
              "cctp_max_fee_micro": spec.cctp_max_fee_micro,
              "hyperevm_gas_budget_wei": spec.hyperevm_gas_budget_wei,
              "base_gas_budget_wei": spec.base_gas_budget_wei,
              # The forwarding mode is a per-transfer choice recorded in treasury.gas_route,
              # so advance and status reopen the journal without repeating the flag.
              "max_forward_fee_micro": spec.max_forward_fee_micro,
              "max_forward_fees_per_window": spec.max_forward_fees_per_window}
    path = Path(args.ledger)
    if args.treasury_command == "probe":
        print(json.dumps(probe(rail, path, config, usd=args.usd), default=str))
        return 0
    if args.treasury_command == "status" and path.exists():
        # Status must not replay an interrupted write. Advance is the explicit recovery command.
        items = Ledger.reopen(path, manifest=config)._recovery_items()
        saved = next(i for i in reversed(items)
                     if i.get("kind") == "treasury.acceptance.snapshot")
        state = decode(saved["treasury"])["state"]
        tail = len(items) - saved["seq"] - 1
        print(json.dumps({"status": "recovery_required" if tail else
                          state["status"] if state else "idle", "pending_tail_items": tail,
                          "state": state, "reserve_address": reserve, "networks": [998, 84532]}))
        return 0
    if args.treasury_command == "status" and not path.exists():
        balances = rail.balances()
        print(json.dumps({"reserve_address": reserve, "networks": [998, 84532],
                          **balances, "hype_wei": rail.hyper.balance(),
                          "base_sepolia_eth_wei": rail.base.balance()}))
        return 0
    if args.treasury_command != "transfer" and not path.exists():
        raise RailError("no acceptance journal exists; submit a transfer first")
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(path) + ".lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        session = AcceptanceSession(path, rail, config)
        if args.treasury_command == "status":
            output = session.status()
        else:
            operation = {"kind": args.treasury_command}
            if args.treasury_command == "transfer":
                if not args.direction or args.usd is None:
                    raise RailError("transfer requires --direction and --usd")
                operation.update(direction=args.direction, usd=args.usd)
            output = session.execute(operation, time_ns())
        print(json.dumps(output, default=str))
        return 2 if output["status"] in ("failed", "stranded") else 0
    finally:
        os.close(fd)
