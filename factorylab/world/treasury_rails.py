"""Native CCTP route, isolated from wallet, pot and reconciler accounting.

Sources verified 2026-09-11 (the testnet usdcRouting API reported cctp both ways):
https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/usdc
https://developers.circle.com/cctp/howtos/withdraw-usdc-from-hypercore-to-evm
https://developers.circle.com/cctp/references/coredepositwallet-contract-interface
https://developers.circle.com/cctp/references/hypercore-contract-addresses
https://developers.circle.com/cctp/references/contract-addresses
https://developers.circle.com/cctp/references/technical-guide
https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/hyperevm/json-rpc
https://github.com/hl-archive-node/nanoreth/blob/node-builder/src/node/types/reth_compat.rs
https://github.com/circlefin/hyperevm-circle-contracts/blob/master/src/messages/CrossChainWithdrawalHookData.sol

The only route interface is balances/preflight/plan/prepare/send/poll, plus the
read-only gas_view the pots publish. Its steps and carry data are opaque to
Treasury. Withdrawals use SDK EIP-712 signing and posting with a persisted
nonce, then the protocol burns on HyperEVM. Minting on Base is self-submitted
when the reserve holds ETH; otherwise the withdrawal sends empty data, so
Circle's forwarder mints for the fee quoted on-chain before signing, and the
mint step observes that mint instead of sending one. Return transfers burn on
Base, mint to the reserve on HyperEVM and call depositFor to credit the
declared main wallet's perps account.
HYPE and ETH gas are booked at observed mids when spent, never as USDC principal.
Native gas consumes its separate manifest budget. Its economic cost counts toward
the transfer fee cap, but only actual USDC fees debit the USDC/provider-credit
wallet; the prefunded native inventory was never credited to that wallet.
The official HyperEVM RPC hides system receipts. For a native withdrawal, prove
the exact canonical system call, derive its nanoreth hash, then require a matching
Circle attestation that the destination contract accepts in a read-only eth_call.
Only the subsequent actual mint receipt confirms arrival in the reserve pot.
Base approval is provisional: prepare the following burn while its allowance is
canonical, persist both references, and settle their gas together after finality.
Rebroadcasting those exact nonce-ordered transactions survives a provisional reorg.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from copy import deepcopy
from decimal import ROUND_CEILING, Decimal
from typing import Any

from eth_account import Account
from hyperliquid.utils.signing import sign_user_signed_action

from factorylab.world.cctp import CCTP
from factorylab.world.evm import (
    BASE,
    BASE_SEPOLIA,
    CORE_TEST_WALLET,
    CORE_WALLET,
    EVM,
    HYPEREVM,
    HYPEREVM_TESTNET,
    Pending,
    RailError,
    address,
    calldata,
    decode_log,
    event_topic,
    word_address,
)
from factorylab.world.treasury import ClassTransferRail
from factorylab.world.x402 import Transport, http_request


def gas_micro(wei: int, price: str) -> int:
    return int(
        (Decimal(wei) * Decimal(price) / Decimal(10**12)).to_integral_value(
            rounding=ROUND_CEILING,
        )
    )


# Gas allowance used to estimate one receiveMessage on Base at the observed gas price,
# doubled like the Core charge ceiling. The recorded testnet mint used ~1.06e12 wei.
MINT_GAS_ALLOWANCE = 200_000
# CoreDepositWallet marks a forwarded withdrawal with bytes24("cctp-forward").
FORWARD_MAGIC = b"cctp-forward".ljust(24, b"\0")
FORWARD_FEE_QUOTE = "CoreDepositWallet.calculateCrossChainWithdrawalFee"
MESSAGE_RECEIVED = "MessageReceived(address,uint32,bytes32,bytes32,uint32,bytes)"
# Pages of finalized Base blocks one forwarded-mint wait reads past its cursor: 2,000
# blocks, about an hour of Base, so a resumed or slow world catches up in a few ticks
# while a ten-minute tick reads six pages. A code constant, not a manifest setting.
FORWARD_SCAN_PAGES = 40


AUTHORIZATION_USED = "AuthorizationUsed(address,bytes32)"
#: How far short of "tranche less metered spend" a hybrid top-up's observed Venice credit
#: may fall before financing is held. The comparison is between three imperfect reads:
#: the diary's metered spend is an estimate (a table price when Venice reports no cost),
#: ``venice_balance`` rounds down to the micro, and Venice seats keep spending the credit
#: through the 15-20 minutes Base finality takes. $0.25 is 5% of the tranche: it absorbs
#: all three, and a real missing credit (the whole $5, or most of it) still exceeds it.
CREDIT_TOLERANCE_MICRO = 250_000


def authorization_status(base: EVM, authorizer: str, reference: dict) -> dict:
    """Read, keylessly, whether one Venice top-up authorization can still settle.

    Guarantees ``expired`` only when three independent reads agree: a FINALIZED Base
    block is past ``validBefore`` (EIP-3009 executes only while a block's timestamp is
    before it, and Base timestamps only grow, so nothing later can use it); the USDC
    contract's ``authorizationState(authorizer, nonce)`` at that same block is false;
    and the ``AuthorizationUsed`` log scan from the reference's ``start_block`` covered
    every block up to that one and found nothing. The contract read is the check a
    lagging RPC cannot fake by returning ``[]`` for logs. The runtime clock is never
    consulted and no grace is needed: finality lag delays the answer, never flips it.
    Every read is ``eth_getBlockByNumber``, ``eth_call`` or ``eth_getLogs``: nothing
    here signs, so an operator's script may call it with ``EVM(BASE, None)``.
    """
    auth = reference["authorization"]
    nonce = auth["nonce"]
    final = base.call("eth_getBlockByNumber", ["finalized", False])
    number, timestamp = int(final["number"], 16), int(final["timestamp"], 16)
    data = calldata("authorizationState(address,bytes32)", ["address", "bytes32"],
                    [address(authorizer), bytes.fromhex(nonce.removeprefix("0x"))])
    state = base.call("eth_call", [{"to": address(base.chain.usdc), "data": data}, hex(number)])
    used = int(state, 16) != 0
    topics = [event_topic(AUTHORIZATION_USED), "0x" + word_address(authorizer).hex(), nonce]
    logs, scanned_to = base.scan(base.chain.usdc, topics, int(reference["start_block"]))
    valid_before = int(auth["validBefore"])
    return {"nonce": nonce, "valid_before": valid_before, "finalized_block": number,
            "finalized_timestamp": timestamp, "authorization_used": used,
            "debits": [log.get("transactionHash") for log in logs], "scanned_to": scanned_to,
            "live": timestamp <= valid_before and not used and not logs,
            "expired": (timestamp > valid_before and not used and not logs
                        and scanned_to >= number)}


def hype_text(wei: int) -> str:
    amount = Decimal(wei) / Decimal(10**18)
    whole = amount == amount.to_integral()
    return str(amount.quantize(Decimal(1)) if whole else amount.normalize())


class LiveRail(ClassTransferRail):
    """Only pinned, receipt-confirmed native USDC transfers advance the treasury's opaque plan."""

    name = "hypercore-hyperevm-base-cctp-v2"
    #: A real top-up is submitted once, then only observed (X402Client.top_up's rule for
    #: an unknown outcome): never resent by the retry loop, never replayed on resume.
    poll_only_steps = ("venice_top_up",)

    def __init__(self, exchange: Any, spec: Any, *, transport: Transport = http_request):
        from hyperliquid.utils.constants import MAINNET_API_URL, TESTNET_API_URL

        if exchange.base_url not in (MAINNET_API_URL, TESTNET_API_URL):
            raise RailError("unsupported Hyperliquid endpoint")
        self.testnet = exchange.base_url == TESTNET_API_URL
        self.exchange, self.spec = exchange, spec
        self._transport = transport
        self.reserve_address = address(spec.reserve_address)
        try:
            reserve = Account.from_key(os.environ["RESERVE_PRIVATE_KEY"])
        except Exception:
            raise RailError("reserve signing key is missing or invalid") from None
        if reserve.address.lower() != self.reserve_address.lower():
            raise RailError("reserve key does not match treasury.reserve_address")
        sdk = getattr(exchange, "_exchange", None)
        if sdk is None or sdk.wallet.address.lower() != exchange._address.lower():
            raise RailError("withdrawals require the venue main wallet key")
        if sdk.vault_address:
            raise RailError("treasury withdrawals require the main account, not a subaccount")
        self.venue_address, self._sdk = address(exchange._address), sdk
        hyper, base = (HYPEREVM_TESTNET, BASE_SEPOLIA) if self.testnet else (HYPEREVM, BASE)
        self.hyper = EVM(
            hyper, reserve, transport=transport, gas_budget_wei=spec.hyperevm_gas_budget_wei
        )
        self.base = EVM(base, reserve, transport=transport, gas_budget_wei=spec.base_gas_budget_wei)
        self.cctp = CCTP(testnet=self.testnet, transport=transport)
        self.core = CORE_TEST_WALLET if self.testnet else CORE_WALLET

    def verify_receipt(self, receipt: dict) -> dict | None:
        """Confirm a claimed x402 receipt against Base itself, or decline to say.

        The seller's spool says a paid call was settled. That is the wake host's
        word, not a payment: GPT-6 Pro's third reading, "income-spool trust is
        not payment verification; receipt identity needs chain, transaction,
        log, asset, recipient". This reads the transaction the receipt names and
        looks for the transfer it claims -- USDC, on this chain, to the reserve
        address, for exactly the claimed amount, at the claimed log index when
        one is given.

        Three answers, and only three. ``confirmed: True`` when the chain shows
        that transfer. ``confirmed: False`` with a reason when the chain shows
        something that contradicts the claim -- a reverted transaction, another
        recipient, another amount -- which the treasury fails closed on. ``None``
        while it cannot tell: an unfinalized or unfound transaction, or an RPC
        that would not answer. An unread chain is not evidence of anything, and
        the claim simply stands until the next tick asks again.
        """
        tx_hash = receipt.get("tx")
        if not isinstance(tx_hash, str) or not tx_hash.startswith("0x"):
            return {"confirmed": False, "reason": "receipt names no Base transaction"}
        asset = str(receipt.get("asset") or "USDC").upper()
        if asset != "USDC":
            return {"confirmed": False, "reason": f"unsupported settlement asset {asset}"}
        recipient = receipt.get("recipient") or receipt.get("pay_to") or self.reserve_address
        if str(recipient).lower() != self.reserve_address.lower():
            return {"confirmed": False, "reason": "receipt is not addressed to the reserve"}
        amount = receipt.get("micro")
        if type(amount) is not int or amount <= 0:
            return {"confirmed": False, "reason": "receipt carries no positive amount"}
        try:
            proof = self.base.proof(tx_hash)
        except RailError:
            return None
        except Exception:  # noqa: BLE001 - an unreachable RPC is not a verdict
            return None
        if proof is None:
            return None  # not finalized, or not on this chain yet
        if int(proof.get("status", "0x0"), 16) != 1:
            return {"confirmed": False, "reason": "settlement transaction did not succeed"}
        topic = event_topic("Transfer(address,address,uint256)")
        to_word = "0x" + word_address(self.reserve_address).hex()
        matches = []
        for index, log in enumerate(proof.get("logs", [])):
            topics = [t.lower() for t in log.get("topics", [])]
            if (log.get("address", "").lower() != self.base.chain.usdc.lower()
                    or len(topics) < 3 or topics[0] != topic.lower()
                    or topics[2] != to_word.lower() or log.get("removed", False)):
                continue
            matches.append((int(str(log.get("logIndex", hex(index))), 0),
                            int(log["data"], 16)))
        claimed_index = receipt.get("log_index")
        if claimed_index is not None:
            matches = [m for m in matches if m[0] == int(str(claimed_index), 0)]
        if not matches:
            return {"confirmed": False, "reason": "no USDC transfer to the reserve in this "
                                                  "transaction"}
        exact = [m for m in matches if m[1] == amount]
        if not exact:
            return {"confirmed": False, "reason": "transferred amount differs from the receipt"}
        if len(exact) > 1:
            # Two equal transfers to the reserve in one transaction and a claim that
            # does not say which: confirming either would let the other be claimed and
            # confirmed again. The claim stands unresolved until it names its log.
            return None
        # The identity is the chain's: chain id, transaction, the log the transfer is
        # at, the token contract and the recipient, whatever the claim spelled.
        return {"confirmed": True, "evidence": {"chain": self.base.chain.id, "tx": tx_hash,
                                                "log_index": exact[0][0],
                                                "token": self.base.chain.usdc.lower(),
                                                "asset": "USDC",
                                                "recipient": self.reserve_address.lower(),
                                                "micro": amount}}

    def balances(self) -> dict:
        state = self.exchange._info.user_state(self.venue_address)
        spot = self.exchange._info.spot_user_state(self.venue_address)
        spot_usdc = sum(
            int(Decimal(row["total"]) * 1_000_000)
            for row in spot.get("balances", [])
            if row["coin"] == "USDC"
        )
        result = {
            "venue": int(Decimal(state["marginSummary"]["accountValue"]) * 1_000_000) + spot_usdc,
            "venue_available": int(Decimal(state["withdrawable"]) * 1_000_000),
            "reserve": self.base.balance(self.base.chain.usdc),
            "hyperevm_reserve": self.hyper.balance(self.hyper.chain.usdc),
        }
        if getattr(self.exchange, "spot_pairs", ()):
            result["venue"] = int(self.exchange.account().equity_usd * 1_000_000)
            result["perps"] = int(Decimal(state["marginSummary"]["accountValue"]) * 1_000_000)
            result["spot"] = result["venue"] - result["perps"]
        if not self.testnet:
            try:
                result["venice"] = self._venice_client().venice_balance()
            except Exception:
                result["venice"] = None
        return result

    def plan(self, direction: str) -> tuple[str, ...]:
        if direction in ("spot_to_perps", "perps_to_spot"):
            return (direction,)
        if direction == "to_venice":
            return ("venice_top_up",)
        if direction == "to_reserve":
            return ("withdraw_burn", "mint_base")
        if direction == "to_venue":
            return ("approve_base", "burn_base", "mint_hyper", "approve_core", "deposit_core")
        raise RailError("direction must be to_reserve, to_venue or to_venice")

    def preflight(self, direction: str, amount: int, gas_spent: dict) -> None:
        self.plan(direction)
        if direction in ("spot_to_perps", "perps_to_spot"):
            self.class_preflight(direction, amount)
            return
        if direction == "to_venice":
            from factorylab.world.x402 import TOP_UP_MICRO

            if self.testnet:
                raise RailError("Venice requires Base mainnet USDC; this rail uses Base Sepolia")
            if os.environ.get("VENICE_API_KEY"):
                raise RailError("Venice top-ups credit the reserve wallet, not an API-key account")
            self.base.check_chain()
            if amount != TOP_UP_MICRO:
                raise RailError("to_venice requires the fixed $5 tranche")
            if self.base.balance(self.base.chain.usdc) < amount:
                raise RailError("amount exceeds available reserve pot")
            return
        self.hyper.check_chain()
        self.base.check_chain()
        routes = self.exchange._info.post("/info", {"type": "usdcRouting"})
        route_key = "withdrawalRoute" if direction == "to_reserve" else "depositRoute"
        if routes.get(route_key) != "cctp":
            raise RailError("venue routing changed; native CCTP adapter refuses the transfer")
        token = self.hyper.read(self.core, calldata("token()", [], []))
        if token != word_address(self.hyper.chain.usdc):
            raise RailError("CoreDepositWallet native USDC contract changed")
        linked = [t for t in self.exchange._info.spot_meta()["tokens"]
                  if t.get("name") == "USDC" and t.get("index") == 0]
        if (len(linked) != 1 or linked[0].get("evmContract", {}).get("address", "").lower()
                != self.core.lower()
                or linked[0]["evmContract"].get("evm_extra_wei_decimals") != -2):
            raise RailError("HyperCore USDC linkage changed")
        if amount <= self.spec.cctp_max_fee_micro:
            raise RailError("amount is below the CCTP fee-covered transfer minimum")
        if direction == "to_reserve":
            role = self.exchange._info.post(
                "/info", {"type": "userRole", "user": self.venue_address}
            )
            if role.get("role") != "user":
                raise RailError("withdrawals require a main wallet, not an agent/API key")
            state = self.exchange._info.user_state(self.venue_address)
            available = int(Decimal(state["withdrawable"]) * 1_000_000)
            if amount <= self.spec.withdrawal_fee_micro + self.spec.cctp_max_fee_micro:
                raise RailError("amount is below the fee-covered venue withdrawal minimum")
            self._core_gas_bound(gas_spent)
            route = self.gas_route(gas_spent)
            self._route_bound(route)
            if amount <= self.spec.withdrawal_fee_micro + route["cctp_max_fee_micro"]:
                raise RailError("amount is below the fee-covered venue withdrawal minimum")
            # A forwarded mint is Circle's transaction: the reserve needs no Base gas.
            executing = () if route["forward"] else ("base",)
        else:
            available = self.base.balance(self.base.chain.usdc)
            disabled = int.from_bytes(
                self.hyper.read(
                    self.core,
                    calldata(
                        "isDexForwardingDisabled()",
                        [],
                        [],
                    ),
                )
            )
            enabled = int.from_bytes(
                self.hyper.read(
                    self.core,
                    calldata(
                        "enabledDestinationDexes(uint32)",
                        ["uint32"],
                        [0],
                    ),
                )
            )
            if disabled or not enabled:
                raise RailError("CoreDepositWallet cannot currently forward to the perps account")
            executing = ("base", "hyper")
        if amount > available:
            raise RailError("amount exceeds available source pot")
        for key in executing:
            chain = self._evm(key)
            if self.remaining(key, gas_spent) <= 0:
                raise RailError("native gas budget is not configured or is exhausted")
            if chain.balance() == 0:
                raise RailError(f"reserve requires native {chain.chain.gas_symbol} on {key}")

    def _evm(self, key: str) -> EVM:
        return {"base": self.base, "hyper": self.hyper}[key]

    def remaining(self, key: str, spent: dict) -> int:
        return self._evm(key).gas_budget_wei - spent.get(key, 0)

    def _core_fee(self, forward: bool = False) -> int:
        """The burn's maxFee as CoreDepositWallet quotes it for this branch, read on-chain."""
        return int.from_bytes(
            self.hyper.read(
                self.core,
                calldata(
                    "calculateCrossChainWithdrawalFee(bool,uint32)",
                    ["bool", "uint32"],
                    [forward, self.base.chain.domain],
                ),
            )
        )

    def _core_gas(self, spent: dict) -> tuple[int, int, int]:
        """The documented Core-to-EVM gas ceiling, the venue's spot HYPE and the budget left."""
        gas = 200_000 * int(self.hyper.call("eth_gasPrice", []), 16) * 2
        spot = self.exchange._info.spot_user_state(self.venue_address)
        hype = sum(Decimal(r["total"]) for r in spot["balances"] if r["coin"] == "HYPE")
        return gas, int(hype * 10**18), self.remaining("hyper", spent)

    def _core_gas_bound(self, spent: dict) -> int:
        """Reserve the documented Core-to-EVM gas charge against the HyperEVM budget."""
        gas, hype, remaining = self._core_gas(spent)
        if gas <= 0 or gas > remaining:
            raise RailError("HyperCore transfer gas budget is exhausted")
        if gas > hype:
            raise RailError("venue requires spot HYPE for the Core-to-EVM gas charge")
        return gas

    def gas_route(self, gas_spent: dict) -> dict:
        """Choose the Base mint from the reserve's own observed position; reads only.

        Self-mint needs Base ETH and budget for one receiveMessage; otherwise the
        withdrawal is forwarded and Circle deducts the fee it quotes here.
        """
        mode = self.spec.cctp_forwarding
        eth = self.base.balance()
        remaining = self.remaining("base", gas_spent)
        estimate = MINT_GAS_ALLOWANCE * int(self.base.call("eth_gasPrice", []), 16) * 2
        self_fee, forward_quote = self._core_fee(False), self._core_fee(True)
        core_gas, hype, _ = self._core_gas(gas_spent)
        affordable = 0 < estimate <= min(eth, remaining)
        if mode in ("never", "always"):
            forward, reason = mode == "always", "manifest"
        else:
            forward = not affordable
            reason = ("base_eth_available" if affordable
                      else "no_base_eth" if eth < estimate else "base_gas_budget_exhausted")
        return {
            "forward": forward,
            "reason": reason,
            "mode": mode,
            "base_eth_wei": eth,
            "base_gas_remaining_wei": remaining,
            "base_mint_estimate_wei": estimate,
            "core_hype_wei": hype,
            "core_hype_required_wei": core_gas,
            "self_mint_fee_micro": self_fee,
            "forward_fee_micro": forward_quote - self_fee,
            "cctp_max_fee_micro": forward_quote if forward else self_fee,
            "quote_source": FORWARD_FEE_QUOTE,
        }

    def _route_bound(self, route: dict) -> None:
        """Refuse before signing when the chosen branch is not offered within the manifest."""
        if route["self_mint_fee_micro"] > self.spec.cctp_max_fee_micro:
            raise RailError("venue CCTP fee cap exceeds manifest cap")
        if route["forward"]:
            fee = route["forward_fee_micro"]
            if fee <= 0:
                raise RailError("CoreDepositWallet cannot currently forward the destination mint")
            if fee > self.spec.max_forward_fee_micro:
                raise RailError(
                    f"forwarding fee quote {fee} exceeds treasury.max_forward_fee_micro")
            return
        if route["base_gas_remaining_wei"] <= 0:
            raise RailError("native gas budget is not configured or is exhausted")
        if route["base_eth_wei"] == 0:
            raise RailError("reserve requires native ETH on base")

    def gas_view(self, gas_spent: dict) -> dict:
        """Answer "can the population exit to the reserve now, and what will it cost?"."""
        route = self.gas_route(gas_spent)
        blocked = None
        try:
            self._core_gas_bound(gas_spent)
            self._route_bound(route)
        except RailError as exc:
            blocked = str(exc)
        return {
            "mode": route["mode"],
            "route": "forwarded" if route["forward"] else "self_mint",
            "reason": route["reason"],
            "core_hype": hype_text(route["core_hype_wei"]),
            "core_hype_required": hype_text(route["core_hype_required_wei"]),
            "base_eth_wei": route["base_eth_wei"],
            "base_gas_remaining_wei": route["base_gas_remaining_wei"],
            "base_mint_estimate_wei": route["base_mint_estimate_wei"],
            "forward_fee_micro": route["forward_fee_micro"],
            "cctp_max_fee_micro": route["cctp_max_fee_micro"],
            "minimum_micro": self.spec.withdrawal_fee_micro + route["cctp_max_fee_micro"] + 1,
            "refill_ready": blocked is None,
            "blocked_by": blocked,
        }

    def _withdraw_action(self, amount: int, nonce: int, forward: bool = False) -> dict:
        return {
            "type": "sendToEvmWithData",
            "token": "USDC",
            "amount": str(Decimal(amount) / 1_000_000),
            "sourceDex": "",
            "destinationRecipient": self.reserve_address,
            "addressEncoding": "hex",
            "destinationChainId": self.base.chain.domain,
            "gasLimit": 200_000,
            # Empty data asks Circle's forwarder to mint on Base for its quoted fee;
            # nonempty inert metadata keeps the mint, and its gas, with the reserve.
            "data": "0x" if forward else "0x00",
            "nonce": nonce,
        }

    def _forwarded_mint(
        self, burn: dict, gas_spent: dict, cursor: int | None = None
    ) -> dict | None:
        """Observe Circle's forwarder mint of our burn; None lets the reserve self-mint.

        destinationCaller is zero on this route, so anyone may deliver the message.
        The forwarder's finalized delivery is preferred; a reserve that can pay
        may deliver an unclaimed message itself. Otherwise the step waits, carrying
        the last finalized block it scanned so the next wait pages only the blocks
        after it instead of every block since the burn.
        """
        message, proof, fee = self.cctp.attestation(self.hyper, self.base, burn)
        nonce = "0x" + message[12:44].hex()
        topics = [event_topic(MESSAGE_RECEIVED), None, nonce]
        start = burn["base_start_block"] if cursor is None else cursor + 1
        logs, scanned_to = self.base.scan(
            self.base.chain.transmitter, topics, start, max_pages=FORWARD_SCAN_PAGES
        )
        if len(logs) > 1:
            raise RailError("ambiguous forwarded mint")
        if logs:
            return {
                "network": f"eip155:{self.base.chain.id}",
                "chain_key": "base",
                "forwarded": True,
                "tx_hash": logs[0]["transactionHash"],
                "cctp_nonce": nonce,
                "cctp_fee_micro": fee,
                "fee_ceiling_micro": fee,
                "start_block": burn["base_start_block"],
            }
        estimate = MINT_GAS_ALLOWANCE * int(self.base.call("eth_gasPrice", []), 16) * 2
        if 0 < estimate <= min(self.remaining("base", gas_spent), self.base.balance()):
            data = calldata("receiveMessage(bytes,bytes)", ["bytes", "bytes"], [message, proof])
            try:
                # A used nonce reverts this read: the forwarder has minted, unfinalized.
                unclaimed = self.base.read(self.base.chain.transmitter, data) == (1).to_bytes(32)
            except Pending:
                unclaimed = False
            if unclaimed:
                return None
        raise Pending("awaiting the Circle forwarder's Base mint", carry={"scanned_to": scanned_to})

    def _forwarded_receipt(self, ref: dict, amount: int) -> dict | None:
        """Confirm the forwarder's finalized delivery of our nonce and the exact USDC credit."""
        receipt = self.base.proof(ref["tx_hash"])
        if receipt is None or int(receipt["status"], 16) != 1:
            return None
        transmitter = self.base.chain.transmitter.lower()
        received = [
            log for log in receipt.get("logs", [])
            if log.get("address", "").lower() == transmitter
            and len(log.get("topics", [])) >= 3
            and log["topics"][0].lower() == event_topic(MESSAGE_RECEIVED)
            and log["topics"][2].lower() == ref["cctp_nonce"].lower()
            and not log.get("removed", False)
        ]
        if len(received) != 1:
            raise RailError("forwarded mint receipt lacks a unique MessageReceived for the burn")
        fee = ref["cctp_fee_micro"]
        if not self.cctp.minted(receipt, self.base, amount - fee):
            raise RailError("forwarded mint receipt does not prove the intended USDC credit")
        return {
            "confirmed": True,
            "received_micro": amount - fee,
            "fee_micro": fee,
            "wallet_fee_micro": fee,  # real USDC deducted by Circle; no native gas was spent
            "chain_key": "base",
            "principal_moved": True,
            "evidence": {
                "network": ref["network"],
                "tx_hash": ref["tx_hash"],
                "block_hash": receipt["blockHash"],
                "cctp_nonce": ref["cctp_nonce"],
                "cctp_fee_micro": fee,
                "forwarded": True,
                "forwarder": receipt["from"],
                "credited_micro": amount - fee,
            },
        }

    def prepare(self, step: str, state: dict, gas_spent: dict) -> dict:
        """Return immutable replay references without broadcasting an external write."""
        if step in ("spot_to_perps", "perps_to_spot"):
            return self.class_prepare(step, state)
        if step == "venice_top_up":
            from factorylab.world.venice import prepare_top_up

            client = self._venice_client()
            return {**prepare_top_up(client, now_s=state["started_ns"] // 1_000_000_000,
                                     nonce=os.urandom(32)),
                    "network": "eip155:8453", "start_block": self.base.block(),
                    "fee_ceiling_micro": 0}
        amount = state["received_micro"]
        if step == "burn_base" and state["route_data"].get("prepared_burn"):
            return deepcopy(state["route_data"]["prepared_burn"])
        if step == "withdraw_burn":
            route = self.gas_route(gas_spent)
            self._route_bound(route)
            # The route is read again here, so the minimum is re-applied to the branch
            # actually signed: a burn must exceed the maxFee it carries.
            if amount <= self.spec.withdrawal_fee_micro + route["cctp_max_fee_micro"]:
                raise RailError("amount is below the fee-covered venue withdrawal minimum")
            gas = self._core_gas_bound(gas_spent)
            price = str(self.exchange._info.all_mids()["HYPE"])
            if not Decimal(price).is_finite() or Decimal(price) <= 0:
                raise RailError("HYPE price unavailable for gas accounting")
            return {
                "network": self.exchange.name,
                "sender": self.venue_address,
                "destination": self.reserve_address,
                "nonce": state["nonce"],
                "amount_micro": amount,
                "start_block": self.hyper.block(),
                "base_start_block": self.base.block(),
                "cctp_max_fee_micro": route["cctp_max_fee_micro"],
                "core_gas_ceiling_wei": gas,
                "gas_usd": price,
                "fee_ceiling_micro": self.spec.withdrawal_fee_micro + gas_micro(gas, price),
                "forward": route["forward"],
                "gas_route": route,
                "action": self._withdraw_action(amount, state["nonce"], route["forward"]),
            }
        fallback = False
        if step == "mint_base" and state["route_data"]["burn"].get("forwarded"):
            carried = ((state.get("pending") or {}).get("reference") or {}).get("scanned_to")
            observed = self._forwarded_mint(state["route_data"]["burn"], gas_spent, carried)
            if observed is not None:
                return observed
            fallback = True  # unclaimed and affordable: the reserve delivers it below
        key = "base" if step.endswith("base") else "hyper"
        chain, remaining = self._evm(key), self.remaining(key, gas_spent)
        approval = (
            state.get("route_data", {}).get("pending_approval") if step == "burn_base" else None
        )
        if approval:
            remaining -= approval["gas_ceiling_wei"]
        raw = self.exchange._info.all_mids()
        price = Decimal(str(raw[chain.chain.gas_symbol]))
        if not price.is_finite() or price <= 0:
            raise RailError("native-token price unavailable for gas accounting")
        if step == "approve_base":
            ref = chain.approve(chain.chain.usdc, chain.chain.messenger, amount, remaining)
        elif step == "burn_base":
            ref = self.cctp.burn(chain, self.hyper, amount, remaining, self.spec.cctp_max_fee_micro)
            if approval:
                if ref["tx"]["nonce"] != approval["tx"]["nonce"] + 1:
                    raise Pending("approval nonce changed; retain and reconcile its reference")
                ref["pending_approval"] = deepcopy(approval)
        elif step.startswith("mint_"):
            source = self.hyper if key == "base" else self.base
            ref = self.cctp.mint(source, chain, state["route_data"]["burn"], remaining)
            if fallback:
                ref["fallback"] = "self_mint"
        elif step == "approve_core":
            ref = chain.approve(chain.chain.usdc, self.core, amount, remaining)
        elif step == "deposit_core":
            data = calldata(
                "depositFor(address,uint256,uint32)",
                ["address", "uint256", "uint32"],
                [self.venue_address, amount, 0],
            )
            ref = chain.prepare(self.core, data, gas_remaining_wei=remaining)
            ref["start_ms"] = state["nonce"]
            ref["start_block"] = chain.block()
            updates = self.exchange._info.user_non_funding_ledger_updates(
                self.venue_address, ref["start_ms"]
            )
            ref["credit_before"] = [row["hash"] for row in updates]
        else:
            raise RailError("unsupported native CCTP step")
        ref.update(chain_key=key, gas_symbol=chain.chain.gas_symbol, gas_usd=str(price))
        ref["fee_ceiling_micro"] = gas_micro(ref["gas_ceiling_wei"], str(price)) + ref.get(
            "cctp_fee_micro", 0
        )
        if approval:
            ref["fee_ceiling_micro"] += gas_micro(approval["gas_ceiling_wei"], approval["gas_usd"])
        return ref

    def send(self, step: str, reference: dict) -> dict | None:
        if step in ("spot_to_perps", "perps_to_spot"):
            return self.class_send(reference)
        if step == "venice_top_up":
            from factorylab.world.venice import top_up

            return top_up(self._venice_client(), reference)
        if step != "withdraw_burn":
            if reference.get("forwarded"):
                return None  # Circle's forwarder sends this transaction, never the reserve
            chain = self._evm(reference["chain_key"])
            if approval := reference.get("pending_approval"):
                try:
                    chain.broadcast(approval)
                except Pending:
                    pass  # already mined is normal; the burn retains the following fixed nonce
            chain.broadcast(reference)
            return
        if (
            reference["sender"] != self.venue_address
            or reference["destination"] != self.reserve_address
            or reference["network"] != self.exchange.name
        ):
            raise RailError("withdrawal reference identity mismatch")
        action = self._withdraw_action(
            reference["amount_micro"], reference["nonce"], reference.get("forward", False))
        if action != reference["action"]:
            raise RailError("withdrawal reference was modified")
        names = (
            "hyperliquidChain",
            "token",
            "amount",
            "sourceDex",
            "destinationRecipient",
            "addressEncoding",
            "destinationChainId",
            "gasLimit",
            "data",
            "nonce",
        )
        types = (
            "string",
            "string",
            "string",
            "string",
            "string",
            "string",
            "uint32",
            "uint64",
            "bytes",
            "uint64",
        )
        sign_types = [{"name": name, "type": typ} for name, typ in zip(names, types, strict=True)]
        signature = sign_user_signed_action(
            self._sdk.wallet,
            action,
            sign_types,
            "HyperliquidTransaction:SendToEvmWithData",
            not self.testnet,
        )
        try:
            response = self._sdk._post_action(action, signature, reference["nonce"])
        except Exception:
            raise Pending("withdrawal outcome unknown; reconcile the existing nonce") from None
        if response.get("status") != "ok":
            raise RailError("venue rejected withdrawal")

    #: Hyperliquid accepts an action only while its nonce is within about two days of
    #: the venue's clock. A withdrawal whose nonce is older than this and that no
    #: ledger update shows can never execute.
    WITHDRAWAL_NONCE_WINDOW_MS = 3 * 86_400_000

    def expired(self, step: str, state: dict, now_ns: int) -> str | None:
        """Why a submitted step can no longer execute, or ``None`` while it still could.

        Only steps whose principal has not left are answered: the Venice top-up's
        EIP-3009 authorization and the venue withdrawal's signed action. The
        treasury abandons such a step only after a clean poll found no evidence.
        A top-up is judged on finalized Base alone (``_authorization_expired``), never
        on ``now_ns``: a runtime clock ahead of the chain, or a virtual one, used to
        abandon a real authorization that could still settle.
        """
        reference = state.get("reference") or {}
        if step == "venice_top_up":
            try:
                return self._authorization_expired(state)
            except Exception:  # noqa: BLE001 - an unreadable chain proves nothing expired
                return None
        if step == "withdraw_burn":
            nonce = reference.get("nonce", state.get("nonce"))
            if nonce is None:
                return None
            if now_ns // 1_000_000 > int(nonce) + self.WITHDRAWAL_NONCE_WINDOW_MS:
                return "withdrawal nonce expired unexecuted"
        return None

    def _authorization_expired(self, state: dict) -> str | None:
        """Guarantees a top-up authorization is abandoned only when it can never settle.

        See ``authorization_status``: dead exactly when finalized Base is past its
        ``validBefore``, the USDC contract says its nonce is unused at that block, and
        the log scan covered every block up to it without an ``AuthorizationUsed``.
        """
        reference = state.get("reference") or {}
        if ((reference.get("authorization") or {}).get("validBefore") is None
                or reference.get("start_block") is None):
            return None
        status = authorization_status(self._venice_base(), self.reserve_address, reference)
        return "Venice authorization expired unused on finalized Base" if status[
            "expired"] else None

    def replace(self, step: str, reference: dict, gas_spent: dict) -> dict:
        """A repriced replacement for a stuck EVM step at its original nonce."""
        if (reference.get("forwarded") or reference.get("pending_approval")
                or "tx" not in reference or "chain_key" not in reference):
            raise RailError("this step has no replaceable transaction")
        chain = self._evm(reference["chain_key"])
        replaced = chain.replace(reference, gas_remaining_wei=self.remaining(
            reference["chain_key"], gas_spent))
        replaced["fee_ceiling_micro"] = (
            reference["fee_ceiling_micro"]
            - gas_micro(reference["gas_ceiling_wei"], reference["gas_usd"])
            + gas_micro(replaced["gas_ceiling_wei"], reference["gas_usd"]))
        return replaced

    def _venice_base(self) -> EVM:
        """The chain a Venice top-up debits: this rail's own Base, mainnet by construction."""
        return self.base

    def _venice_client(self):
        """Use the existing reserve signer and x402 client on the committed Base mainnet rail."""
        from factorylab.world.x402 import X402Client

        if self.testnet:
            raise RailError("Venice requires Base mainnet USDC")
        client = X402Client(transport=self._transport)
        if client.address.lower() != self.reserve_address.lower():
            raise RailError("Venice payer differs from the reserve")
        return client

    # Bound by the runtime: metered Venice spend since a purchase started, or None
    # when no diary is available (the rail alone cannot see the population's calls).
    metered_usage_since: Callable[[int], int | None] | None = None

    def _venice_receipt(self, state: dict) -> dict | None:
        """Release principal on the exact canonical debit; the credit balance is advisory.

        The debit is the unique successful AuthorizationUsed for the journal's nonce
        whose receipt transfers exactly the tranche from the reserve to Venice's
        payee. A balance is a stock, not a flow: usage between purchase and
        confirmation lowers it without contradicting the purchase (cold audit F2),
        so the observed credit, the credit before, the amount and the diary's own
        metered usage since the purchase started are recorded beside the receipt
        and never decide it.
        """
        ref = state["reference"]
        auth = ref["authorization"]
        base = self._venice_base()
        # Rescanned from ``start_block`` every poll on purpose, with no cursor: a clean
        # poll persists nothing (only a Pending carries a cursor, and a pending step is
        # never tested for expiry), and a cursor would trust one RPC's empty answer for
        # a range forever, where the rescan asks again and heals a lagging node.
        topics = [event_topic("AuthorizationUsed(address,bytes32)"),
                  "0x" + word_address(self.reserve_address).hex(), auth["nonce"]]
        for log in base.logs(base.chain.usdc, topics, ref["start_block"]):
            if (log.get("removed") or log["address"].lower() != base.chain.usdc.lower()
                    or [t.lower() for t in log["topics"]] != [t.lower() for t in topics]):
                continue
            receipt = base.proof(log["transactionHash"])
            if receipt is None or int(receipt["status"], 16) != 1:
                continue
            if not any(
                event.get("address", "").lower() == base.chain.usdc.lower()
                and [t.lower() for t in event.get("topics", [])] == [t.lower() for t in topics]
                for event in receipt.get("logs", [])
            ):
                continue
            if not base.transferred(receipt, base.chain.usdc, self.reserve_address,
                                    auth["to"], state["amount_micro"]):
                continue
            submission = (state.get("route_data") or {}).get("submission") or {}
            observed, balance_source = submission.get("credit_after_micro"), "acknowledgment"
            if observed is None:
                # The acknowledgment was lost: read the balance now, as advice only.
                balance_source = "balance_read"
                try:
                    observed = self._venice_client().venice_balance()
                except Exception:
                    observed, balance_source = None, "unavailable"
            credit_before, amount = ref.get("credit_before_micro"), state["amount_micro"]
            metered = None
            if self.metered_usage_since is not None:
                try:
                    metered = self.metered_usage_since(int(state.get("started_ns") or 0))
                except Exception:
                    metered = None
            shortfall = None
            if observed is not None and credit_before is not None:
                shortfall = max(0, credit_before + amount - observed)
            return {"confirmed": True, "received_micro": amount,
                    "fee_micro": 0, "principal_moved": True,
                    "evidence": {"network": ref["network"], "tx_hash": log["transactionHash"],
                                 "block_hash": receipt["blockHash"], "nonce": auth["nonce"],
                                 "venice_credit_micro": amount,
                                 "proof": "canonical AuthorizationUsed debit of the tranche",
                                 "credit_before_micro": credit_before,
                                 "amount_micro": amount,
                                 "observed_micro": observed,
                                 "credit_after_micro": observed,
                                 "balance_source": balance_source,
                                 "balance_shortfall_micro": shortfall,
                                 "metered_usage_since_micro": metered}}
        return None

    def _withdrawal(self, ref: dict, amount: int) -> dict | None:
        updates = self.exchange._info.user_non_funding_ledger_updates(
            self.venue_address, ref["nonce"]
        )
        rows = []
        for row in updates:
            delta = row.get("delta", {})
            if (delta.get("nonce") == ref["nonce"] and delta.get("type") == "send"
                    and delta.get("user", "").lower() == self.venue_address.lower()
                    and delta.get("destination", "").lower() ==
                    "0x2000000000000000000000000000000000000000"
                    and delta.get("sourceDex") == "" and delta.get("destinationDex") == "spot"
                    and delta.get("token") == "USDC"
                    and Decimal(delta["amount"]) * 1_000_000 == amount):
                rows.append(row)
        if not rows:
            return None
        if len(rows) != 1:
            raise RailError("ambiguous HyperCore withdrawal debit")
        row, delta = rows[0], rows[0]["delta"]
        fee_decimal = Decimal(delta["fee"]) * 1_000_000
        gas_decimal = Decimal(delta["nativeTokenFee"]) * 10**18
        if (fee_decimal != int(fee_decimal) or gas_decimal != int(gas_decimal)
                or not 0 <= fee_decimal <= self.spec.withdrawal_fee_micro or gas_decimal < 0):
            raise RailError("HyperCore withdrawal fees differ from the supported schedule")
        fee, gas, burned = int(fee_decimal), int(gas_decimal), amount - int(fee_decimal)
        if "core_gas_ceiling_wei" in ref and gas > ref["core_gas_ceiling_wei"]:
            raise RailError("Core-to-EVM gas charge exceeded its reserved ceiling")
        forward = ref.get("forward", False)
        body = b"" if forward else b"\x00"
        data = calldata(
            "coreReceiveWithData(address,bytes32,uint32,uint256,uint64,bytes)",
            ["address", "bytes32", "uint32", "uint256", "uint64", "bytes"],
            [self.venue_address, word_address(self.reserve_address), self.base.chain.domain,
             burned, ref["nonce"], body],
        )
        system = self.hyper.system_transfer(self.core, data, ref["start_block"], row["time"])
        if system is None:
            return None
        # The deployed contract wraps the user data as magic | version | length | from |
        # nonce | data, with the forwarding magic only on empty data, and requests
        # finalized (2000).
        hook = ((FORWARD_MAGIC if forward else bytes(24)) + bytes(4)
                + (len(body) + 28).to_bytes(4) + bytes.fromhex(self.venue_address[2:])
                + ref["nonce"].to_bytes(8) + body)
        message = self.cctp.expected_message(
            self.hyper, self.base, burned,
            sender=self.core,
            max_fee_micro=ref["cctp_max_fee_micro"],
            min_finality=2000,
            hook=hook,
        )
        archive_hash = EVM.archive_hash(system)
        burn = self.cctp.prove_system_burn(self.hyper, self.base, archive_hash, message)
        # Old acceptance journals predate recording the price at prepare. Their
        # first reconciliation records an observed price with the exact gas fee.
        price = ref.get("gas_usd") or str(self.exchange._info.all_mids()["HYPE"])
        if not Decimal(price).is_finite() or Decimal(price) <= 0:
            raise RailError("HYPE price unavailable for gas accounting")
        return {
            "confirmed": True,
            "received_micro": burned,
            "fee_micro": fee + gas_micro(gas, price),
            "wallet_fee_micro": fee,
            "gas_fee_wei": gas,
            "chain_key": "hyper",
            "principal_moved": True,
            "route_data": {"burn": {**burn, "forwarded": forward,
                                    "base_start_block": ref.get("base_start_block")}},
            "evidence": {
                "network": f"eip155:{self.hyper.chain.id}",
                "tx_hash": archive_hash,
                "system_tx_hash": system["hash"],
                "venue_ledger_hash": row["hash"],
                "nonce": ref["nonce"],
                "block_hash": system["blockHash"],
                "burned_micro": burned,
                "gas_fee_wei": gas,
                "gas_symbol": "HYPE",
                "gas_usd": price,
                "proof": "canonical system call and destination-verified Circle attestation",
            },
        }

    def poll(self, step: str, state: dict) -> dict | None:
        if step in ("spot_to_perps", "perps_to_spot"):
            return self.class_poll(state)
        if step == "venice_top_up":
            return self._venice_receipt(state)
        ref, amount = state["reference"], state["received_micro"]
        if step == "withdraw_burn":
            return self._withdrawal(ref, amount)
        if ref.get("forwarded"):
            return self._forwarded_receipt(ref, amount)
        chain = self._evm(ref["chain_key"])
        if step == "approve_base":
            mined = chain.receipt(ref, finalized=False)
            if mined is None:
                return None
            if mined["success"]:
                # Prepare now, while the observed allowance is usable. A failure
                # keeps the approval step live, so its original nonce is retried.
                burn = self.prepare("burn_base", {
                    **state, "route_data": {"pending_approval": ref},
                }, state.get("gas_spent", {}))
                return {
                    "confirmed": True, "received_micro": amount,
                    "fee_micro": 0, "wallet_fee_micro": 0, "principal_moved": False,
                    "route_data": {"prepared_burn": burn},
                    "evidence": {"network": ref["network"], "tx_hash": ref["tx_hash"],
                                 "block_hash": mined["blockHash"],
                                 "confirmation": "provisional approval; gas deferred to burn"},
                }
            # A reverted provisional approval cannot release funds or book fees yet.
        receipt = chain.receipt(ref)
        if receipt is None:
            return None
        gas = receipt["gas_fee_wei"]
        result = {
            "confirmed": receipt["success"],
            "received_micro": amount,
            "fee_micro": gas_micro(gas, ref["gas_usd"]),
            "wallet_fee_micro": 0,
            "gas_fee_wei": gas,
            "chain_key": ref["chain_key"],
            "principal_moved": step in ("burn_base", "deposit_core"),
            "evidence": {
                "network": ref["network"],
                "tx_hash": ref["tx_hash"],
                "block_hash": receipt["blockHash"],
                "gas_fee_wei": gas,
                "gas_symbol": ref["gas_symbol"],
                "gas_usd": ref["gas_usd"],
            },
        }
        if approval := ref.get("pending_approval"):
            settled = chain.receipt(approval)
            if settled is None:
                return None
            approval_gas = settled["gas_fee_wei"]
            result["fee_micro"] += gas_micro(approval_gas, approval["gas_usd"])
            result["gas_fee_wei"] += approval_gas
            result["evidence"]["approval"] = {
                "tx_hash": approval["tx_hash"], "block_hash": settled["blockHash"],
                "gas_fee_wei": approval_gas, "gas_usd": approval["gas_usd"],
                "success": settled["success"], "confirmation": "finalized",
            }
        if not receipt["success"]:
            if ref.get("fallback") == "self_mint" and self.cctp.nonce_used(
                    chain, bytes.fromhex(ref["cctp_nonce"].removeprefix("0x"))):
                # "Nonce already used": Circle delivered before our fallback landed, so
                # the USDC arrived by the forwarder's transaction. Confirm that credit and
                # book only the gas the revert cost; never strand money that is here.
                return self._forwarded_after_fallback(ref, state, amount, receipt)
            return {**result, "principal_moved": False, "reason": "on-chain transaction reverted"}
        if step == "burn_base":
            message = self.cctp.message(
                receipt,
                chain,
                self.hyper,
                amount,
                sender=self.reserve_address,
                max_fee_micro=ref["cctp_max_fee_micro"],
                min_finality=2000,
            )
            result["route_data"] = {
                "burn": {"tx_hash": ref["tx_hash"], "message": "0x" + message.hex()}
            }
        elif step.startswith("mint_"):
            fee = ref["cctp_fee_micro"]
            if not self.cctp.minted(receipt, chain, amount - fee):
                raise RailError("mint receipt does not prove the intended USDC credit")
            result["received_micro"] -= fee
            result["fee_micro"] += fee
            result["wallet_fee_micro"] += fee
            result["evidence"]["cctp_fee_micro"] = fee
            result["evidence"]["cctp_nonce"] = ref["cctp_nonce"]
        elif step == "deposit_core":
            if not chain.transferred(
                receipt, chain.chain.usdc, self.reserve_address, self.core, amount
            ):
                raise RailError("deposit receipt does not prove the intended USDC transfer")
            credit = self._deposit_credit(chain, receipt, ref, amount)
            if credit is None:
                return None
            credited, row = credit
            result["received_micro"] = credited
            result["fee_micro"] += amount - credited
            result["wallet_fee_micro"] += amount - credited
            result["evidence"]["venue_credit_micro"] = credited
            result["evidence"]["venue_ledger_hash"] = row["hash"]
            result["evidence"]["venue_ledger_nonce"] = row["delta"]["nonce"]
        return result

    def _forwarded_after_fallback(
        self, ref: dict, state: dict, amount: int, reverted: dict
    ) -> dict | None:
        """Confirm the forwarder's finalized delivery that beat our reverted fallback mint."""
        burn = state["route_data"]["burn"]
        topics = [event_topic(MESSAGE_RECEIVED), None, ref["cctp_nonce"]]
        logs = self.base.logs(self.base.chain.transmitter, topics, burn["base_start_block"])
        if len(logs) > 1:
            raise RailError("ambiguous forwarded mint")
        if not logs:
            return None  # delivered, not yet finalized: the wait is bounded by finality
        observed = {"network": ref["network"], "tx_hash": logs[0]["transactionHash"],
                    "cctp_nonce": ref["cctp_nonce"], "cctp_fee_micro": ref["cctp_fee_micro"]}
        forwarded = self._forwarded_receipt(observed, amount)
        if forwarded is None:
            return None
        gas = reverted["gas_fee_wei"]
        return {
            **forwarded,
            "fee_micro": forwarded["fee_micro"] + gas_micro(gas, ref["gas_usd"]),
            "gas_fee_wei": gas,
            "evidence": {**forwarded["evidence"], "reverted_fallback": {
                "tx_hash": ref["tx_hash"], "block_hash": reverted["blockHash"],
                "gas_fee_wei": gas, "gas_symbol": ref["gas_symbol"], "gas_usd": ref["gas_usd"]}},
        }

    def _deposit_credit(self, chain: EVM, receipt: dict, ref: dict, amount: int):
        """Require both a unique CoreWriter event and the subsequent real perps credit.

        HyperCore's public testnet ledger was inspected on 2026-09-11: depositFor
        forwarding appears as `send` from CoreDepositWallet (spot -> perps), with
        a HyperCore hash distinct from the EVM hash. Correlate recipient, exact
        amount, direction and a two-minute processing interval. A second matching
        EVM deposit or Core credit makes correlation ambiguous and stays pending.
        """
        topics = [event_topic("SendAsset(address,uint64,uint32)"),
                  "0x" + word_address(self.venue_address).hex()]
        emitted = [log for log in receipt.get("logs", [])
                   if log.get("address", "").lower() == self.core.lower()
                   and [t.lower() for t in log.get("topics", [])] ==
                   [t.lower() for t in topics] and not log.get("removed", False)]
        if len(emitted) != 1:
            raise RailError("deposit receipt lacks a unique perps-forwarding event")
        core_amount, dex = decode_log(emitted[0], ["uint64", "uint32"])
        if dex != 0 or core_amount % 100 or not 0 < core_amount // 100 <= amount:
            raise RailError("deposit forwarding amount or destination differs")
        credited = core_amount // 100
        matching = [log for log in chain.logs(self.core, topics, ref["start_block"])
                    if decode_log(log, ["uint64", "uint32"]) == (core_amount, 0)]
        if len(matching) != 1 or matching[0]["transactionHash"].lower() != ref["tx_hash"].lower():
            raise RailError("ambiguous CoreDepositWallet forwarding correlation")
        block = chain.call("eth_getBlockByNumber", [receipt["blockNumber"], False])
        since = int(block["timestamp"], 16) * 1000
        updates = self.exchange._info.user_non_funding_ledger_updates(self.venue_address, since)
        matched = []
        for row in updates:
            delta = row.get("delta", {})
            if (row.get("hash") in ref["credit_before"]
                    or not since <= row.get("time", 0) <= since + 120_000
                    or delta.get("type") != "send"
                    or delta.get("user", "").lower() != self.core.lower()
                    or delta.get("destination", "").lower() != self.venue_address.lower()
                    or delta.get("sourceDex") != "spot" or delta.get("destinationDex") != ""
                    or delta.get("token") != "USDC"):
                continue
            if Decimal(delta["amount"]) * 1_000_000 == credited:
                matched.append(row)
        if len(matched) > 1:
            raise RailError("ambiguous HyperCore perps credit")
        return (credited, matched[0]) if matched else None


class HybridRail(LiveRail):
    """Guarantees a testnet world buys real Venice credit only when its own pots pay for it.

    The hybrid capital-loop rehearsal (docs/architecture/capital-loop-rehearsal.md):
    trading, the reserve and every other route stay on testnet exactly as ``LiveRail``
    runs them, while ``to_venice`` spends real USDC from the same reserve address on
    Base mainnet. The essay's requirement (II.IV, "a continuous, reciprocal flow of
    capital is an objective requirement") is that profit can become thinking money; a
    rehearsal of that flow is only honest if the profit spent is the profit observed.
    So each conversion has two legs and confirms only when both have:

    1. ``shadow_send``: a Hyperliquid testnet ``usdSend`` of exactly the tranche from the
       venue's main account to ``treasury.venice_shadow_sink``, signed like the class
       transfer (EIP-712 user-signed action, persisted millisecond nonce) and confirmed
       by the venue's own ledger row for that nonce, sink and amount.
    2. ``venice_top_up``: the fixed $5 x402 top-up from the Base mainnet reserve,
       proven by the canonical ``AuthorizationUsed`` debit of the tranche exactly as
       ``LiveRail._venice_receipt`` proves it, but read on Base mainnet.

    The shadow leg goes first. It is testnet money, and a failure there spends nothing
    real: the top-up's authorization is not even prepared until the shadow leg's
    receipt is ledgered. The reverse order would leave real mainnet USDC spent against
    a testnet leg that might never pay, which is the one discrepancy this rail exists to
    prevent. Once the shadow leg paid it is never sent again (the plan has moved past
    it), and the top-up is submitted once and only observed afterwards
    (``poll_only_steps``); a fresh authorization is prepared only when the previous one
    provably can no longer execute.
    """

    name = "hypercore-testnet-venice-base-mainnet-hybrid"

    def __init__(self, exchange: Any, spec: Any, *, transport: Transport = http_request):
        from factorylab.world.x402 import X402Client

        super().__init__(exchange, spec, transport=transport)
        if not self.testnet:
            raise RailError("the hybrid Venice rail rehearses on a testnet venue only")
        if spec.venice_network != "base-mainnet":
            raise RailError("the hybrid Venice rail requires treasury.venice_network")
        self.sink = address(spec.venice_shadow_sink)
        if self.sink.lower() in (self.venue_address.lower(), self.reserve_address.lower()):
            raise RailError("the shadow sink must be outside every observed pot")
        if self.venue_address.lower() == self.reserve_address.lower():
            # One key for both would make the venue's testnet account and the real
            # reserve one identity: a shadow send and a top-up could not be told apart.
            raise RailError("the venue and the reserve must be different accounts")
        if spec.venice_pay_to is None or spec.max_venice_total_micro is None or (
                spec.venice_reserve_floor_micro is None):
            raise RailError("the hybrid Venice rail requires its payee, total and floor")
        self.pay_to = address(spec.venice_pay_to)
        self.reserve_floor_micro = spec.venice_reserve_floor_micro
        # Read-only on mainnet: no gas budget, so this EVM can never sign a transaction.
        self.venice_base = EVM(BASE, self.base.account, transport=transport, gas_budget_wei=0)
        # The x402 client is built once, here, while the reserve key is in the
        # environment, so a runner may clear that variable before the world starts.
        self._x402 = X402Client(transport=transport)
        if self._x402.address.lower() != self.reserve_address.lower():
            raise RailError("Venice payer differs from the reserve")

    @staticmethod
    def now_s() -> int:
        """Wall-clock seconds for an authorization's validity window (journaled with it)."""
        from time import time_ns

        return time_ns() // 1_000_000_000

    def _venice_base(self) -> EVM:
        return self.venice_base

    def _venice_client(self):
        return self._x402

    def balances(self) -> dict:
        """The testnet pots, plus the real Venice credit and the real reserve beside them."""
        result = super().balances()
        try:
            result["venice"] = self._x402.venice_balance()
        except Exception:  # noqa: BLE001 - an unread balance is unknown, never zero
            result["venice"] = None
        try:
            result["venice_reserve"] = self.venice_base.balance(self.venice_base.chain.usdc)
        except Exception:  # noqa: BLE001
            result["venice_reserve"] = None
        return result

    def plan(self, direction: str) -> tuple[str, ...]:
        if direction == "to_venice":
            return ("shadow_send", "venice_top_up")
        return super().plan(direction)

    def preflight(self, direction: str, amount: int, gas_spent: dict) -> None:
        if direction != "to_venice":
            return super().preflight(direction, amount, gas_spent)
        from factorylab.world.x402 import TOP_UP_MICRO

        if amount != TOP_UP_MICRO:
            raise RailError("to_venice requires the fixed $5 tranche")
        if os.environ.get("VENICE_API_KEY"):
            raise RailError("Venice top-ups credit the reserve wallet, not an API-key account")
        info = self.exchange._info
        if info.post("/info", {"type": "userRole", "user": self.venue_address}).get(
                "role") != "user":
            raise RailError("shadow sends require a main wallet, not an agent/API key")
        # usdSend to an address the venue has never seen charges an activation fee, which
        # would make the shadow leg cost more than the tranche: refuse before signing.
        if info.post("/info", {"type": "userRole", "user": self.sink}).get("role") in (
                None, "missing"):
            raise RailError("the shadow sink is not an existing Hyperliquid testnet account")
        state = info.user_state(self.venue_address)
        if amount > int(Decimal(state["withdrawable"]) * 1_000_000):
            raise RailError("amount exceeds available venue pot")
        self._above_floor(amount)

    def _above_floor(self, amount: int) -> None:
        """The real reserve covers the tranche and keeps the manifest's floor after it.

        The floor is read on chain, so it bounds total real spend across runs: a fresh
        world with a fresh counter still cannot take the reserve below it.
        """
        from factorylab.world.treasury import RESERVE_FLOOR

        self.venice_base.check_chain()
        balance = self.venice_base.balance(self.venice_base.chain.usdc)
        if balance < amount:
            raise RailError("amount exceeds available mainnet reserve")
        if balance - amount < self.reserve_floor_micro:
            raise RailError(RESERVE_FLOOR)

    def _shadow_action(self, amount: int, nonce: int) -> dict:
        return {"type": "usdSend", "destination": self.sink,
                "amount": str(Decimal(amount) / 1_000_000), "time": nonce}

    def prepare(self, step: str, state: dict, gas_spent: dict) -> dict:
        if step == "shadow_send":
            amount = state["amount_micro"]
            return {"network": self.exchange.name, "sender": self.venue_address,
                    "destination": self.sink, "nonce": state["nonce"], "amount_micro": amount,
                    "action": self._shadow_action(amount, state["nonce"]),
                    "fee_ceiling_micro": 0}
        if step == "venice_top_up":
            from factorylab.world.venice import prepare_top_up
            from factorylab.world.x402 import X402Error

            self._above_floor(state["amount_micro"])
            # The validity window starts now, not at submission: a top-up re-prepared
            # after an expired authorization must not be born expired.
            try:
                prepared = prepare_top_up(self._x402, now_s=self.now_s(), nonce=os.urandom(32),
                                          pay_to=self.pay_to)
            except X402Error as exc:
                raise RailError(str(exc)) from None  # x402 messages are local, never bodies
            return {**prepared, "network": "eip155:8453",
                    "start_block": self.venice_base.block(), "fee_ceiling_micro": 0}
        return super().prepare(step, state, gas_spent)

    def send(self, step: str, reference: dict) -> dict | None:
        if step == "venice_top_up":
            from factorylab.world.venice import top_up

            # Signed only for the pinned payee, whatever the journal's reference says.
            return top_up(self._x402, reference, pay_to=self.pay_to)
        if step != "shadow_send":
            return super().send(step, reference)
        from hyperliquid.utils.signing import sign_usd_transfer_action

        if (reference["sender"] != self.venue_address or reference["destination"] != self.sink
                or reference["network"] != self.exchange.name):
            raise RailError("shadow send identity mismatch")
        action = self._shadow_action(reference["amount_micro"], reference["nonce"])
        if action != reference["action"]:
            raise RailError("shadow send reference was modified")
        signed = deepcopy(action)  # the SDK adds the chain fields to what it signs
        signature = sign_usd_transfer_action(self._sdk.wallet, signed, not self.testnet)
        try:
            response = self._sdk._post_action(signed, signature, reference["nonce"])
        except Exception:
            raise Pending("shadow send outcome unknown; reconcile the existing nonce") from None
        if response.get("status") != "ok":
            raise RailError("venue rejected withdrawal")
        return None

    def poll(self, step: str, state: dict) -> dict | None:
        if step == "shadow_send":
            return self._shadow_receipt(state)
        if step == "venice_top_up":
            return self._top_up_receipt(state)
        return super().poll(step, state)

    @staticmethod
    def _authorizations(state: dict) -> list[dict]:
        """The current authorization and every one it superseded, newest first."""
        superseded = (state.get("route_data") or {}).get("superseded_references") or ()
        return [ref for ref in (state.get("reference"), *reversed(superseded)) if ref]

    def _top_up_receipt(self, state: dict) -> dict | None:
        """Confirm a debit of any of this conversion's authorizations, and its credit.

        Every authorization the transfer ever had is polled, the superseded ones too,
        so a debit that lands late is still found and booked (and nothing new is signed
        after it). Financing is booked on the proven canonical debit, exactly as the
        ordinary rail books it, and the credit reads are recorded beside it as evidence
        (``credit_shortfall_micro``). The credit only vetoes: the step is held, with the
        numbers carried in its public stall and the principal kept, only when every
        read is known and the credit rose by less than the tranche less the diary's
        metered Venice spend since the authorization less ``CREDIT_TOLERANCE_MICRO``.
        A held step is never re-authorized (a pending step is not tested for expiry).
        """
        from factorylab.world.treasury import CREDIT_SHORT

        amount = state["amount_micro"]
        for ref in self._authorizations(state):
            outcome = self._venice_receipt({**state, "reference": ref})
            if outcome is None:
                continue
            try:
                observed = self._x402.venice_balance()
            except Exception:  # noqa: BLE001 - unread is unknown: the debit still decides
                observed = None
            before = ref.get("credit_before_micro")
            metered = None
            if self.metered_usage_since is not None:
                try:
                    metered = self.metered_usage_since(int(ref.get("created_s") or 0)
                                                       * 1_000_000_000)
                except Exception:  # noqa: BLE001
                    metered = None
            known = all(type(v) is int for v in (observed, before, metered))
            shortfall = max(0, amount - metered - (observed - before)) if known else None
            evidence = {**outcome["evidence"], "credit_before_micro": before,
                        "observed_micro": observed, "credit_after_micro": observed,
                        "balance_source": "balance_read", "metered_usage_since_micro": metered,
                        "credit_shortfall_micro": shortfall,
                        "credit_tolerance_micro": CREDIT_TOLERANCE_MICRO}
            if known and shortfall > CREDIT_TOLERANCE_MICRO:
                raise Pending(CREDIT_SHORT, carry={
                    "tx_hash": evidence.get("tx_hash"), "nonce": evidence.get("nonce"),
                    "credit_before_micro": before, "observed_micro": observed,
                    "metered_usage_since_micro": metered, "required_micro": amount,
                    "shortfall_micro": shortfall, "tolerance_micro": CREDIT_TOLERANCE_MICRO})
            return {**outcome, "evidence": evidence}
        return None

    def _shadow_receipt(self, state: dict) -> dict | None:
        """Confirm the venue's one ledger row sending the tranche from the venue to the sink.

        Hyperliquid has reported a ``usdSend`` both as ``internalTransfer`` (``usdc``)
        and as a ``send`` of USDC between perps dexes (``amount``, ``nonce``); either
        shape is accepted with the same identity: sender, sink, exact amount and a
        hashed row executed after the signed nonce and inside the venue's nonce window,
        after which the signed action can never execute. A row that names its nonce must
        name ours. A row that names none is matched on sender, sink and exact amount alone,
        even when it executes late: that is safe because the treasury runs one transfer at
        a time and frees the slot only once a shadow send confirmed, was refused outright,
        or outlived its nonce window, so no other conversion's send to the sink can land
        after this nonce. The one thing that can is an operator sending exactly the tranche
        to the sink by hand, which the runbook forbids. Two candidates confirm nothing, and
        a fee stalls the transfer publicly instead of booking money the tranche never had.
        """
        ref, amount = state["reference"], state["amount_micro"]
        start = ref["nonce"]
        rows = self.exchange._info.user_non_funding_ledger_updates(self.venue_address, start)
        matches = []
        for row in rows:
            delta, executed = row.get("delta", {}), row.get("time")
            if (not row.get("hash") or type(executed) is not int or executed < start
                    or executed > start + self.WITHDRAWAL_NONCE_WINDOW_MS):
                continue
            if delta.get("type") == "internalTransfer":
                text = delta.get("usdc")
            elif (delta.get("type") == "send" and delta.get("token") == "USDC"
                  and delta.get("sourceDex", "") == "" and delta.get("destinationDex", "") == ""):
                text = delta.get("amount")
            else:
                continue
            if (str(delta.get("user", "")).lower() != self.venue_address.lower()
                    or str(delta.get("destination", "")).lower() != self.sink.lower()):
                continue
            if "nonce" in delta and delta["nonce"] != start:
                continue
            try:
                if Decimal(str(text)) * 1_000_000 != amount:
                    continue
            except ArithmeticError:
                continue
            matches.append(row)
        if not matches:
            return None
        if len(matches) != 1:
            raise RailError("ambiguous shadow send debit")
        row = matches[0]
        try:
            fee = Decimal(str(row["delta"].get("fee", "0")))
        except ArithmeticError:
            fee = None
        if fee != 0:
            raise RailError("shadow send charged a fee; the conversion needs an operator")
        evidence = {"network": ref["network"], "venue_ledger_hash": row["hash"],
                    "nonce": start, "sink": self.sink, "shadow_micro": amount,
                    "proof": "venue ledger row of the signed usdSend to the shadow sink"}
        return {"confirmed": True, "received_micro": amount, "fee_micro": 0,
                "principal_moved": True, "evidence": evidence,
                "route_data": {"shadow": {"sink": self.sink, "micro": amount,
                                          "venue_ledger_hash": row["hash"]}}}

    def expired(self, step: str, state: dict, now_ns: int) -> str | None:
        if step == "shadow_send":
            nonce = (state.get("reference") or {}).get("nonce", state.get("nonce"))
            if nonce is not None and now_ns // 1_000_000 > int(nonce) + (
                    self.WITHDRAWAL_NONCE_WINDOW_MS):
                return "shadow send nonce expired unexecuted"
            return None
        # The top-up (on finalized Base) and the CCTP steps are LiveRail's own rules.
        return super().expired(step, state, now_ns)
