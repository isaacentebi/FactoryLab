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

The only route interface is balances/preflight/plan/prepare/send/poll. Its steps
and carry data are opaque to Treasury. Withdrawals use SDK EIP-712 signing and
posting with a persisted nonce, then the protocol burns on HyperEVM. Minting on
Base is self-submitted. Return transfers burn on Base, mint to the reserve on
HyperEVM and call depositFor to credit the declared main wallet's perps account.
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
from factorylab.world.x402 import Transport, http_request


def gas_micro(wei: int, price: str) -> int:
    return int(
        (Decimal(wei) * Decimal(price) / Decimal(10**12)).to_integral_value(
            rounding=ROUND_CEILING,
        )
    )


class LiveRail:
    """Only pinned, receipt-confirmed native USDC transfers advance the treasury's opaque plan."""

    name = "hypercore-hyperevm-base-cctp-v2"

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
        if not self.testnet:
            try:
                result["venice"] = self._venice_client().venice_balance()
            except Exception:
                result["venice"] = None
        return result

    def plan(self, direction: str) -> tuple[str, ...]:
        if direction == "to_venice":
            return ("venice_top_up",)
        if direction == "to_reserve":
            return ("withdraw_burn", "mint_base")
        if direction == "to_venue":
            return ("approve_base", "burn_base", "mint_hyper", "approve_core", "deposit_core")
        raise RailError("direction must be to_reserve, to_venue or to_venice")

    def preflight(self, direction: str, amount: int, gas_spent: dict) -> None:
        self.plan(direction)
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
            executing = ("base",)
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

    def _core_fee(self) -> int:
        return int.from_bytes(
            self.hyper.read(
                self.core,
                calldata(
                    "calculateCrossChainWithdrawalFee(bool,uint32)",
                    ["bool", "uint32"],
                    [False, self.base.chain.domain],
                ),
            )
        )

    def _core_gas_bound(self, spent: dict) -> int:
        """Reserve the documented Core-to-EVM gas charge against the HyperEVM budget."""
        gas = 200_000 * int(self.hyper.call("eth_gasPrice", []), 16) * 2
        spot = self.exchange._info.spot_user_state(self.venue_address)
        hype = sum(Decimal(r["total"]) for r in spot["balances"] if r["coin"] == "HYPE")
        if gas <= 0 or gas > self.remaining("hyper", spent):
            raise RailError("HyperCore transfer gas budget is exhausted")
        if Decimal(gas) > hype * 10**18:
            raise RailError("venue requires spot HYPE for the Core-to-EVM gas charge")
        return gas

    def _withdraw_action(self, amount: int, nonce: int) -> dict:
        return {
            "type": "sendToEvmWithData",
            "token": "USDC",
            "amount": str(Decimal(amount) / 1_000_000),
            "sourceDex": "",
            "destinationRecipient": self.reserve_address,
            "addressEncoding": "hex",
            "destinationChainId": self.base.chain.domain,
            "gasLimit": 200_000,
            # Nonempty inert metadata disables Circle's automatic forwarding fee.
            "data": "0x00",
            "nonce": nonce,
        }

    def prepare(self, step: str, state: dict, gas_spent: dict) -> dict:
        """Return immutable replay references without broadcasting an external write."""
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
            cap = self._core_fee()
            if cap > self.spec.cctp_max_fee_micro:
                raise RailError("venue CCTP fee cap exceeds manifest cap")
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
                "cctp_max_fee_micro": cap,
                "core_gas_ceiling_wei": gas,
                "gas_usd": price,
                "fee_ceiling_micro": self.spec.withdrawal_fee_micro + gas_micro(gas, price),
                "action": self._withdraw_action(amount, state["nonce"]),
            }
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
        if step == "venice_top_up":
            from factorylab.world.venice import top_up

            return top_up(self._venice_client(), reference)
        if step != "withdraw_burn":
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
        action = self._withdraw_action(reference["amount_micro"], reference["nonce"])
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

    def _venice_client(self):
        """Use the existing reserve signer and x402 client on the committed Base mainnet rail."""
        from factorylab.world.x402 import X402Client

        if self.testnet:
            raise RailError("Venice requires Base mainnet USDC")
        client = X402Client(transport=self._transport)
        if client.address.lower() != self.reserve_address.lower():
            raise RailError("Venice payer differs from the reserve")
        return client

    def _venice_receipt(self, state: dict) -> dict | None:
        """Release principal only on the exact canonical debit and observed Venice credit."""
        ref = state["reference"]
        auth = ref["authorization"]
        topics = [event_topic("AuthorizationUsed(address,bytes32)"),
                  "0x" + word_address(self.reserve_address).hex(), auth["nonce"]]
        for log in self.base.logs(self.base.chain.usdc, topics, ref["start_block"]):
            if (log.get("removed") or log["address"].lower() != self.base.chain.usdc.lower()
                    or [t.lower() for t in log["topics"]] != [t.lower() for t in topics]):
                continue
            receipt = self.base.proof(log["transactionHash"])
            if receipt is None or int(receipt["status"], 16) != 1:
                continue
            if not any(
                event.get("address", "").lower() == self.base.chain.usdc.lower()
                and [t.lower() for t in event.get("topics", [])] == [t.lower() for t in topics]
                for event in receipt.get("logs", [])
            ):
                continue
            if not self.base.transferred(receipt, self.base.chain.usdc, self.reserve_address,
                                         auth["to"], state["amount_micro"]):
                continue
            observed = state["route_data"].get("submission", {}).get("credit_after_micro")
            if observed is None:
                observed = self._venice_client().venice_balance()
            if observed < ref["credit_before_micro"] + state["amount_micro"]:
                return None
            return {"confirmed": True, "received_micro": state["amount_micro"],
                    "fee_micro": 0, "principal_moved": True,
                    "evidence": {"network": ref["network"], "tx_hash": log["transactionHash"],
                                 "block_hash": receipt["blockHash"], "nonce": auth["nonce"],
                                 "venice_credit_micro": state["amount_micro"],
                                 "credit_after_micro": observed}}
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
        data = calldata(
            "coreReceiveWithData(address,bytes32,uint32,uint256,uint64,bytes)",
            ["address", "bytes32", "uint32", "uint256", "uint64", "bytes"],
            [self.venue_address, word_address(self.reserve_address), self.base.chain.domain,
             burned, ref["nonce"], b"\x00"],
        )
        system = self.hyper.system_transfer(self.core, data, ref["start_block"], row["time"])
        if system is None:
            return None
        # The deployed contract wraps the user data, and requests finalized (2000).
        hook = (bytes(28) + (29).to_bytes(4) + bytes.fromhex(self.venue_address[2:])
                + ref["nonce"].to_bytes(8) + b"\x00")
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
            "route_data": {"burn": burn},
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
        if step == "venice_top_up":
            return self._venice_receipt(state)
        ref, amount = state["reference"], state["received_micro"]
        if step == "withdraw_burn":
            return self._withdrawal(ref, amount)
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
