"""Pinned USDC rails with chain-checked RPC, bounded gas and replayable transaction references.

No web3 dependency: ABI and signing come from the Hyperliquid SDK's existing
eth_abi/eth_account dependencies. Private keys never enter a transaction reference.
"""

from __future__ import annotations

import re
import time
from contextlib import ExitStack
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

from eth_abi import decode, encode
from eth_utils import keccak, to_checksum_address

from factorylab.world.x402 import Transport, http_request


class RailError(RuntimeError):
    """A bounded diagnostic never incorporates an RPC response body or signing material."""


class Pending(RailError):
    """Keep the existing reference live; a new nonce or refund would be unsafe.

    ``carry`` is optional plain data (no RPC bodies, no signing material) the rail
    hands back to the treasury to persist in the transfer's pending record and
    receive again on the next attempt, such as a log-scan cursor. It is journaled
    with the recorded call result, so a resumed world carries it too.
    """

    def __init__(self, message: str = "", carry: dict | None = None) -> None:
        super().__init__(message)
        self.carry = carry


@dataclass(frozen=True)
class Chain:
    id: int
    domain: int
    rpc: str
    usdc: str
    messenger: str
    transmitter: str
    gas_symbol: str


# Native CCTP V2, verified against Circle's contract-address pages on 2026-09-11.
# https://developers.circle.com/cctp/references/contract-addresses
# https://developers.circle.com/stablecoins/usdc-contract-addresses
HYPEREVM_TESTNET = Chain(
    998,
    19,
    "https://rpc.hyperliquid-testnet.xyz/evm",
    "0x2B3370eE501B4a559b57D449569354196457D8Ab",
    "0x8FE6B999Dc680CcFDD5Bf7EB0974218be2542DAA",
    "0xE737e5cEBEEBa77EFE34D4aa090756590b1CE275",
    "HYPE",
)
BASE_SEPOLIA = Chain(
    84532,
    6,
    "https://sepolia.base.org",
    "0x036CbD53842c5426634e7929541eC2318f3dCF7e",
    "0x8FE6B999Dc680CcFDD5Bf7EB0974218be2542DAA",
    "0xE737e5cEBEEBa77EFE34D4aa090756590b1CE275",
    "ETH",
)
HYPEREVM = Chain(
    999,
    19,
    "https://rpc.hyperliquid.xyz/evm",
    "0xb88339CB7199b77E23DB6E890353E22632Ba630f",
    "0x28b5a0e9C621a5BadaA536219b3a228C8168cf5d",
    "0x81D40F21F12A8F0E3252Bccb954D722d4c464B64",
    "HYPE",
)
BASE = Chain(
    8453,
    6,
    "https://mainnet.base.org",
    "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913",
    "0x28b5a0e9C621a5BadaA536219b3a228C8168cf5d",
    "0x81D40F21F12A8F0E3252Bccb954D722d4c464B64",
    "ETH",
)
CORE_TEST_WALLET = "0x0B80659a4076E9E93C7DbE0f10675A16a3e5C206"
CORE_WALLET = "0x6B9E773128f453f5c2C60935Ee2DE2CBc5390A24"
CORE_USDC_SYSTEM = "0x2000000000000000000000000000000000000000"
# Blocks per eth_getLogs page: Hyperliquid's official RPC documents a 50-block range
# limit, and a page is a fixed code constant rather than a manifest setting.
LOG_PAGE_BLOCKS = 50
# A rate-limited read is asked again, at most this many attempts in all, after pauses
# doubling from the first and capped at the last. A paged log scan is a burst a public
# RPC refuses as a whole although it answers each page alone; the bound means a node
# that keeps refusing still leaves the reference ``Pending``.
RATE_LIMIT_ATTEMPTS = 5
RATE_LIMIT_FIRST_PAUSE_S = 0.5
RATE_LIMIT_MAX_PAUSE_S = 8.0
#: The only methods a rate-limit answer is retried for: reads, which change nothing.
#: No write (``eth_sendRawTransaction``) is in this set, so a write is sent exactly once
#: per ``call`` whatever answers; its only retry is the journaled reference's rebroadcast.
RATE_LIMITED_READS = frozenset({
    "eth_chainId", "eth_blockNumber", "eth_getBalance", "eth_call", "eth_estimateGas",
    "eth_gasPrice", "eth_getTransactionCount", "eth_getTransactionReceipt",
    "eth_getBlockByNumber", "eth_getLogs", "eth_getSystemTxsByBlockHash",
})
_RATE_LIMIT_TEXT = re.compile(r"rate[\s_-]?limit|too many requests", re.IGNORECASE)


def rate_limited(status: int, body: Any) -> bool:
    """True only for HTTP 429, or a JSON-RPC error whose code is 429 or whose message
    names a rate limit or too many requests; False for every other answer."""
    if status == 429:
        return True
    error = body.get("error") if isinstance(body, dict) else None
    if not isinstance(error, dict):
        return False
    message = error.get("message")
    return error.get("code") == 429 or (
        isinstance(message, str) and _RATE_LIMIT_TEXT.search(message) is not None)


def address(value: str) -> str:
    """Only a nonzero EVM address, with no path or whitespace, can enter a transaction."""
    if not isinstance(value, str) or len(value) != 42 or not value.startswith("0x"):
        raise RailError("invalid EVM address")
    try:
        result = to_checksum_address(value)
    except (ValueError, TypeError):
        raise RailError("invalid EVM address") from None
    if int(result, 16) == 0:
        raise RailError("zero EVM address")
    return result


def word_address(value: str) -> bytes:
    return bytes.fromhex(address(value)[2:]).rjust(32, b"\0")


def calldata(signature: str, types: list[str], values: list) -> str:
    return "0x" + (keccak(text=signature)[:4] + encode(types, values)).hex()


def event_topic(signature: str) -> str:
    return "0x" + keccak(text=signature).hex()


def _with_headroom(price: int) -> int:
    """A quoted gas price plus 25%, rounded up."""
    return (price * 5 + 3) // 4


#: The reserve-key calls this code base signs, by selector: what a stuck one does, and so
#: what replacing it at its nonce costs. A CCTP mint (``receiveMessage``) delivers funds
#: already burned on the other chain, so it is never cancelled, only re-sent.
STEP_SELECTORS = {
    keccak(text="approve(address,uint256)")[:4]: "approve",
    keccak(text="depositForBurn(uint256,uint32,bytes32,address,bytes32,uint256,uint32)")[:4]:
        "burn",
    keccak(text="receiveMessage(bytes,bytes)")[:4]: "mint",
    keccak(text="depositFor(address,uint256,uint32)")[:4]: "deposit",
    keccak(text="transfer(address,uint256)")[:4]: "transfer",
}


def step_kind(sender: str, to: str, data: str) -> str:
    """What a reserve-key call does, read from the call itself.

    Guarantees every ``receiveMessage`` is ``mint``; a 0-value empty call to the sender
    itself is ``cancel``; any selector not in ``STEP_SELECTORS`` is ``other``.
    """
    raw = bytes.fromhex(str(data).removeprefix("0x"))
    if not raw:
        return "cancel" if str(to).lower() == str(sender).lower() else "other"
    return STEP_SELECTORS.get(raw[:4], "other")


class EVM:
    """Each signed call pins chain, sender, nonce, destination, calldata and maximum gas cost."""

    def __init__(
        self,
        chain: Chain,
        account: Any,
        *,
        transport: Transport = http_request,
        rpc: str | None = None,
        gas_budget_wei: int = 0,
    ):
        self.chain, self.account, self.transport = chain, account, transport
        self.rpc = rpc or chain.rpc
        parsed = urlsplit(self.rpc)
        if (
            parsed.scheme not in {"https", "http"}
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
        ):
            raise RailError("RPC URL must have no credentials, query or fragment")
        if type(gas_budget_wei) is not int or gas_budget_wei < 0:
            raise RailError("gas budget must be nonnegative integer wei")
        self.gas_budget_wei = gas_budget_wei
        # The rate-limit backoff's pause; a test substitutes one that records instead.
        self.sleep = time.sleep
        # The write-ahead guard every transaction this signer prepares, replaces or
        # broadcasts passes (``capital_loop.AuthorizationLog`` or ``ReserveGuard``): the
        # runtime binds it. Unbound, this EVM reads the chain and signs nothing.
        self.transaction_guard: Any = None

    def _guarded(self, unsigned: dict, signed: Any) -> None:
        """Write a signed transaction ahead to the reserve's record, or refuse it.

        Guarantees a transaction signed here is returned (and so can ever be broadcast)
        only after the guard durably recorded it, under the reserve's lock, with the
        chain head read now as its ``start_block``; with no guard, a guard that refuses,
        or an unreadable head, it is dropped unbroadcast and this raises.
        """
        guard = self.transaction_guard
        if guard is None:
            raise RailError("no write-ahead transaction record; nothing was prepared")
        try:
            head = int(self.call("eth_blockNumber", []), 16)
            guard.record_transaction({
                "tx_hash": "0x" + bytes(signed.hash).hex(), "chain_id": self.chain.id,
                "from": self.account.address, "to": unsigned["to"],
                "data": unsigned["data"], "value": unsigned["value"],
                "step": step_kind(self.account.address, unsigned["to"], unsigned["data"]),
                "nonce": unsigned["nonce"], "gas_price": unsigned["gasPrice"],
                "start_block": head})
        except Exception as exc:  # noqa: BLE001 - unrecorded means never used
            raise RailError(f"write-ahead transaction record refused "
                            f"({getattr(exc, 'reason', type(exc).__name__)}); "
                            "nothing was prepared") from None

    def _sending(self, tx_hash: str) -> Any:
        """The guard's hold on this reserve for one send, entered, or a refusal.

        Guarantees the returned context is entered: the reserve's lock is held for this
        signer and ``tx_hash`` is on its record until the caller exits it after the send
        returns. With no guard, a held reserve, or an unrecorded hash, nothing is sent.
        """
        guard = self.transaction_guard
        if guard is None:
            raise RailError("no write-ahead transaction record; nothing was broadcast")
        hold = ExitStack()
        try:
            hold.enter_context(guard.sending(self.account.address, tx_hash))
        except Exception as exc:  # noqa: BLE001 - a held reserve sends nothing
            hold.close()
            raise RailError(f"broadcast refused ({getattr(exc, 'reason', type(exc).__name__)})"
                            ) from None
        return hold

    def call(self, method: str, params: list) -> Any:
        """The RPC's result for one JSON-RPC request, or ``Pending``.

        Guarantees a method in ``RATE_LIMITED_READS`` answered with a rate limit
        (``rate_limited``) is sent at most ``RATE_LIMIT_ATTEMPTS`` times and raises
        ``Pending`` if the last attempt is refused too. Any other failure, and every
        answer to a method outside that set (a write), is final on its first attempt.
        """
        attempts = RATE_LIMIT_ATTEMPTS if method in RATE_LIMITED_READS else 1
        pause = RATE_LIMIT_FIRST_PAUSE_S
        for attempt in range(attempts):
            try:
                result = self.transport(
                    "POST",
                    self.rpc,
                    {
                        "jsonrpc": "2.0",
                        "id": 1,
                        "method": method,
                        "params": params,
                    },
                    {"User-Agent": "FactoryLab/0.4"},
                )
            except Exception:
                raise Pending("RPC transport failed; reference remains pending") from None
            if attempt + 1 < attempts and rate_limited(result.status, result.body):
                self.sleep(pause)
                pause = min(pause * 2, RATE_LIMIT_MAX_PAUSE_S)
                continue
            if result.status != 200 or "error" in result.body or "result" not in result.body:
                raise Pending("RPC call rejected or unavailable")
            return result.body["result"]
        raise AssertionError("unreachable")

    def check_chain(self) -> None:
        if int(self.call("eth_chainId", []), 16) != self.chain.id:
            raise RailError("RPC chain ID does not match the pinned rail")

    def block(self) -> int:
        self.check_chain()
        return int(self.call("eth_blockNumber", []), 16)

    def balance(self, token: str | None = None, owner: str | None = None) -> int:
        self.check_chain()
        owner = address(owner or self.account.address)
        if token is None:
            return int(self.call("eth_getBalance", [owner, "latest"]), 16)
        data = calldata("balanceOf(address)", ["address"], [owner])
        return int(self.call("eth_call", [{"to": address(token), "data": data}, "latest"]), 16)

    def read(self, contract: str, data: str) -> bytes:
        self.check_chain()
        result = self.call("eth_call", [{"to": address(contract), "data": data}, "latest"])
        return bytes.fromhex(result.removeprefix("0x"))

    def prepare(self, to: str, data: str, *, gas_remaining_wei: int, nonce: int | None = None,
                min_gas_price: int = 0) -> dict:
        """A reference is computed before broadcast; signing it again yields the same tx hash.

        ``nonce`` pins the account nonce (a cancellation takes the stuck one's), else the
        pending count is used; ``min_gas_price`` floors the price (a replacement's 12.5%).
        """
        self.check_chain()
        if type(gas_remaining_wei) is not int or gas_remaining_wei <= 0:
            raise RailError("gas budget exhausted")
        if gas_remaining_wei > self.gas_budget_wei:
            raise RailError("gas remaining exceeds declared budget")
        sender, to = address(self.account.address), address(to)
        tx = {"from": sender, "to": to, "value": "0x0", "data": data}
        estimate = int(self.call("eth_estimateGas", [tx]), 16)
        gas = (estimate * 12 + 9) // 10
        # Headroom over the node's quote: a legacy transaction priced at exactly the
        # current gas price stalls in the mempool at the first uptick, and its nonce
        # then blocks every later transfer from this signer.
        price = max(_with_headroom(int(self.call("eth_gasPrice", []), 16)), min_gas_price)
        ceiling = gas * price
        if ceiling <= 0 or ceiling > gas_remaining_wei:
            raise RailError("transaction exceeds remaining gas budget")
        if nonce is None:
            nonce = int(self.call("eth_getTransactionCount", [sender, "pending"]), 16)
        unsigned = {
            "chainId": self.chain.id,
            "nonce": nonce,
            "to": to,
            "value": 0,
            "gas": gas,
            "gasPrice": price,
            "data": data,
        }
        try:
            signed = self.account.sign_transaction(unsigned)
        except Exception:
            raise RailError("transaction signing failed") from None
        l1_ceiling = 0
        if self.chain.id in (8453, 84532):
            # This adapter supports Base's zero operator-fee configuration only.
            # Check before broadcasting, rather than discovering an unsupported fee after spending.
            # https://specs.optimism.io/protocol/isthmus/exec-engine.html (2026-09-11)
            for signature in ("operatorFeeScalar()", "operatorFeeConstant()"):
                value = self.read(
                    "0x4200000000000000000000000000000000000015", calldata(signature, [], [])
                )
                if len(value) != 32 or int.from_bytes(value):
                    raise RailError("Base operator fee configuration is unsupported")
            # Include OP Stack data fees, separately reported as receipt.l1Fee.
            # https://specs.optimism.io/protocol/fjord/predeploys.html (2026-09-11)
            size = len(signed.raw_transaction)
            data = calldata("getL1FeeUpperBound(uint256)", ["uint256"], [size])
            l1_ceiling = (
                int.from_bytes(self.read("0x420000000000000000000000000000000000000F", data)) * 2
            )
            ceiling += l1_ceiling
        if ceiling > gas_remaining_wei or self.balance() < ceiling:
            raise RailError("insufficient native gas balance or remaining budget")
        self._guarded(unsigned, signed)
        return {
            "network": f"eip155:{self.chain.id}",
            "sender": sender,
            "tx_hash": "0x" + bytes(signed.hash).hex(),
            "tx": unsigned,
            "gas_ceiling_wei": ceiling,
            "l1_fee_ceiling_wei": l1_ceiling,
        }

    def replace(self, reference: dict, *, gas_remaining_wei: int) -> dict:
        """The same transaction at the same nonce, repriced to replace one the chain will not mine.

        The replacement's gas price is at least 12.5% above the stuck one (nodes
        require 10%) and at least the current quote with headroom; everything else
        -- chain, signer, nonce, destination, calldata, gas limit -- is the original's,
        so at most one of the two can ever execute. ``replaces`` carries every hash
        this nonce was sent under, and ``receipt`` accepts whichever the chain mined.
        """
        self.check_chain()
        tx = reference["tx"]
        if tx["chainId"] != self.chain.id or reference["sender"] != self.account.address:
            raise RailError("transaction reference belongs to another chain or signer")
        price = max((tx["gasPrice"] * 9 + 7) // 8,
                    _with_headroom(int(self.call("eth_gasPrice", []), 16)))
        unsigned = {**tx, "gasPrice": price}
        ceiling = unsigned["gas"] * price + reference.get("l1_fee_ceiling_wei", 0)
        if ceiling > gas_remaining_wei or self.balance() < ceiling:
            raise RailError("replacement exceeds remaining gas budget or balance")
        try:
            signed = self.account.sign_transaction(unsigned)
        except Exception:
            raise RailError("transaction signing failed") from None
        self._guarded(unsigned, signed)
        return {**reference, "tx": unsigned, "tx_hash": "0x" + bytes(signed.hash).hex(),
                "gas_ceiling_wei": ceiling,
                "replaces": [reference["tx_hash"], *reference.get("replaces", [])]}

    def broadcast(self, reference: dict) -> None:
        """Broadcast only an already-journaled immutable transaction; no new nonce on retry."""
        self.check_chain()
        tx = reference["tx"]
        if (
            tx["chainId"] != self.chain.id
            or reference["sender"] != self.account.address
            or tx.get("value") != 0
        ):
            raise RailError("transaction reference belongs to another chain or signer")
        try:
            signed = self.account.sign_transaction(tx)
        except Exception:
            raise RailError("transaction signing failed") from None
        expected = "0x" + bytes(signed.hash).hex()
        if expected != reference["tx_hash"]:
            raise RailError("transaction reference was modified")
        # The reserve's lock is held from the record check until the send returns: no
        # capital-loop launch can start between them.
        with self._sending(expected):
            result = self.call("eth_sendRawTransaction",
                               ["0x" + bytes(signed.raw_transaction).hex()])
        if not isinstance(result, str) or result.lower() != expected.lower():
            raise Pending("RPC did not acknowledge the prepared transaction hash")

    def receipt(self, reference: dict, *, finalized: bool = True) -> dict | None:
        """Verify canonical identity and fees; provisional receipts never establish settlement.

        A replaced transaction is found under whichever of its hashes the chain mined
        (``reference["replaces"]``): they share one nonce, so at most one exists.
        """
        receipt, mined = None, reference["tx_hash"]
        for candidate in (reference["tx_hash"], *reference.get("replaces", [])):
            receipt = self.proof(candidate, finalized=finalized)
            if receipt is not None:
                mined = candidate
                break
        if receipt is None:
            return None
        if (
            receipt["transactionHash"].lower() != mined.lower()
            or receipt["from"].lower() != reference["sender"].lower()
            or receipt["to"].lower() != reference["tx"]["to"].lower()
        ):
            raise RailError("receipt identity does not match submitted transaction")
        fee = int(receipt["gasUsed"], 16) * int(receipt["effectiveGasPrice"], 16)
        if self.chain.id in (8453, 84532):
            if "l1Fee" not in receipt:
                raise Pending("Base receipt does not report the L1 data fee")
            fee += int(receipt["l1Fee"], 16)
            # Fail closed if a future Base operator-fee schedule is nonzero.
            # Its current receipts must never be treated as zero-cost by omission.
            scalar = int(receipt.get("operatorFeeScalar", "0x0"), 16)
            constant = int(receipt.get("operatorFeeConstant", "0x0"), 16)
            if scalar or constant:
                raise RailError("Base operator fees require a supported accounting schedule")
        if fee > reference["gas_ceiling_wei"]:
            raise RailError("receipt gas cost exceeds reserved ceiling")
        return {**receipt, "gas_fee_wei": fee, "success": int(receipt["status"], 16) == 1}

    def proof(self, tx_hash: str, *, finalized: bool = True) -> dict | None:
        """Require a canonical receipt and, by default, finalized inclusion."""
        self.check_chain()
        receipt = self.call("eth_getTransactionReceipt", [tx_hash])
        if receipt is None:
            return None
        if receipt["transactionHash"].lower() != tx_hash.lower():
            raise RailError("receipt transaction hash mismatch")
        if finalized:
            final = self.call("eth_getBlockByNumber", ["finalized", False])
            if not final or int(final["number"], 16) < int(receipt["blockNumber"], 16):
                return None
        canonical = self.call("eth_getBlockByNumber", [receipt["blockNumber"], False])
        if not canonical or canonical["hash"].lower() != receipt["blockHash"].lower():
            return None
        return receipt

    def logs(self, contract: str, topics: list, start: int) -> list:
        """Read finalized logs only; callers verify event fields as well as the contract."""
        return self.scan(contract, topics, start)[0]

    def scan(
        self, contract: str, topics: list, start: int, *, max_pages: int | None = None,
        end: int | None = None,
    ) -> tuple[list, int]:
        """Read finalized logs from ``start`` and report the last block actually read.

        ``max_pages`` bounds one call to that many ``LOG_PAGE_BLOCKS`` pages; a caller
        that persists the returned block and resumes from the one after it reads every
        finalized block exactly once across calls. With nothing finalized past
        ``start`` no page is requested and ``start - 1`` is reported, so the cursor holds.
        ``end`` names the last block to read instead of the ``finalized`` tag, so a caller
        that read a block's state scans exactly up to that same block.
        """
        self.check_chain()
        if end is None:
            final = self.call("eth_getBlockByNumber", ["finalized", False])
            if not final or int(final["number"], 16) < start:
                return [], start - 1
            end = int(final["number"], 16)
        elif end < start:
            return [], start - 1
        if max_pages is not None:
            end = min(end, start + max_pages * LOG_PAGE_BLOCKS - 1)
        logs = []
        # Bound each RPC page without silently losing events on a provider range limit.
        for first in range(start, end + 1, LOG_PAGE_BLOCKS):
            logs.extend(
                self.call(
                    "eth_getLogs",
                    [
                        {
                            "address": address(contract),
                            "topics": topics,
                            "fromBlock": hex(first),
                            "toBlock": hex(min(first + LOG_PAGE_BLOCKS - 1, end)),
                        }
                    ],
                )
            )
        verified = []
        for log in logs:
            if log.get("removed", False) or log.get("address", "").lower() != contract.lower():
                continue
            actual = log.get("topics", [])
            if len(actual) < len(topics) or any(
                expected is not None and str(expected).lower() != str(got).lower()
                for expected, got in zip(topics, actual, strict=False)
            ):
                continue
            height = int(log["blockNumber"], 16)
            if not start <= height <= end:
                continue
            canonical = self.call("eth_getBlockByNumber", [log["blockNumber"], False])
            if not canonical:
                # A block the node cannot show is not evidence the log was reorged away:
                # the scan is unreadable and is retried, never silently shortened.
                raise Pending("a log's block could not be read")
            if canonical["hash"].lower() == log["blockHash"].lower():
                verified.append(log)
        return verified, end

    def system_transfer(
        self, contract: str, data: str, start: int, core_time_ms: int
    ) -> dict | None:
        """Match a finalized HyperCore system call without mistaking inclusion for execution.

        The official RPC hides system receipts/logs. Locate the call using the
        separately observed HyperCore ledger time, then verify the canonical block
        and exact calldata. The caller must still prove execution with Circle's
        signed attestation; a system transaction alone proves no successful burn.
        https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/hyperevm/json-rpc
        """
        if self.chain.id not in (998, 999):
            raise RailError("system transfer lookup requires HyperEVM")
        self.check_chain()
        final = self.call("eth_getBlockByNumber", ["finalized", False])
        if not final or int(final["number"], 16) < start:
            return None
        low, high = start, int(final["number"], 16)
        first_second, last_second = core_time_ms // 1000 - 2, core_time_ms // 1000 + 10
        while low < high:
            middle = (low + high) // 2
            block = self.call("eth_getBlockByNumber", [hex(middle), False])
            if int(block["timestamp"], 16) < first_second:
                low = middle + 1
            else:
                high = middle
        found = []
        for height in range(low, min(low + 64, int(final["number"], 16) + 1)):
            block = self.call("eth_getBlockByNumber", [hex(height), False])
            if int(block["timestamp"], 16) > last_second:
                break
            for tx in self.call("eth_getSystemTxsByBlockHash", [block["hash"]]):
                if (tx.get("to", "").lower() == contract.lower()
                        and tx.get("input", "").lower() == data.lower()
                        and tx.get("from", "").lower() ==
                        "0x2000000000000000000000000000000000000000"
                        and int(tx.get("chainId", "0x0"), 16) == self.chain.id
                        and int(tx.get("value", "0x1"), 16) == 0
                        and int(tx.get("gasPrice", "0x1"), 16) == 0
                        and tx.get("blockHash", "").lower() == block["hash"].lower()
                        and int(tx.get("blockNumber", "0x0"), 16) == height):
                    found.append(tx)
        if len(found) > 1:
            raise RailError("ambiguous HyperCore system call")
        return found[0] if found else None

    @staticmethod
    def archive_hash(tx: dict) -> str:
        """Reproduce nanoreth's documented system-tx identifier; this signs no payment.

        Source checked 2026-09-11: hl-archive-node/nanoreth
        src/node/types/reth_compat.rs, system_tx_to_reth_transaction.
        This synthetic identifier only locates Circle evidence; it is not itself
        a signature or an execution proof.
        """
        from eth_account._utils.legacy_transactions import (
            encode_transaction,
            serializable_unsigned_transaction_from_dict,
        )

        if int(tx["type"], 16) != 0 or int(tx["gasPrice"], 16) != 0:
            raise RailError("expected a legacy system transaction")
        unsigned = {k: int(tx[k], 16) for k in ("chainId", "nonce", "gasPrice", "gas", "value")}
        unsigned.update(to=address(tx["to"]), data=tx["input"])
        sender = int(tx["from"], 16)
        encoded = encode_transaction(
            serializable_unsigned_transaction_from_dict(unsigned),
            vrs=(2 * unsigned["chainId"] + 36, 1, sender),
        )
        return "0x" + keccak(encoded).hex()

    def approve(self, token: str, spender: str, amount: int, remaining: int) -> dict:
        """Approve exactly the transfer amount, never an unlimited allowance."""
        return self.prepare(
            token,
            calldata(
                "approve(address,uint256)", ["address", "uint256"], [address(spender), amount]
            ),
            gas_remaining_wei=remaining,
        )

    def transfer(self, token: str, to: str, amount: int, remaining: int) -> dict:
        return self.prepare(
            token,
            calldata("transfer(address,uint256)", ["address", "uint256"], [address(to), amount]),
            gas_remaining_wei=remaining,
        )

    def transferred(self, receipt: dict, token: str, source: str, dest: str, amount: int) -> bool:
        topics = [
            event_topic("Transfer(address,address,uint256)"),
            "0x" + word_address(source).hex(),
            "0x" + word_address(dest).hex(),
        ]
        return any(
            log["address"].lower() == token.lower()
            and [t.lower() for t in log["topics"]] == [t.lower() for t in topics]
            and int(log["data"], 16) == amount
            and not log.get("removed", False)
            for log in receipt.get("logs", [])
        )


def decode_log(log: dict, types: list[str]) -> tuple:
    """Decode only ABI event data, with a bounded error that cannot echo RPC material."""
    try:
        return decode(types, bytes.fromhex(log["data"].removeprefix("0x")))
    except Exception:
        raise RailError("invalid event encoding") from None
