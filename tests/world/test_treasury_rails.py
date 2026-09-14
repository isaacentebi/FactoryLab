from copy import deepcopy
from decimal import Decimal
from types import SimpleNamespace

import pytest
from eth_abi import encode
from eth_account import Account

from factorylab.runtime.worlds import TreasurySpec
from factorylab.world.evm import (
    BASE_SEPOLIA,
    CORE_TEST_WALLET,
    HYPEREVM_TESTNET,
    Pending,
    RailError,
    calldata,
    event_topic,
    word_address,
)
from factorylab.world.treasury_rails import LiveRail


class Info:
    def __init__(self):
        self.routes = {"depositRoute": "cctp", "withdrawalRoute": "cctp"}
        self.role = "user"
        self.updates = []
        self.available = "50"
        self.linked = CORE_TEST_WALLET
        self.hype = "1"

    def post(self, path, body):
        return self.routes if body["type"] == "usdcRouting" else {"role": self.role}

    def user_state(self, user):
        return {"marginSummary": {"accountValue": "50"}, "withdrawable": self.available}

    def spot_user_state(self, user):
        return {"balances": [{"coin": "USDC", "total": "2"}, {"coin": "HYPE", "total": self.hype}]}

    def spot_meta(self):
        return {"tokens": [{"name": "USDC", "index": 0, "evmContract": {
            "address": self.linked, "evm_extra_wei_decimals": -2,
        }}]}

    def user_non_funding_ledger_updates(self, user, since):
        return deepcopy(self.updates)

    def all_mids(self):
        return {"ETH": "3000", "HYPE": "40"}


class Chain:
    def __init__(self, config):
        self.chain = config
        self.gas_budget_wei = 10**16
        self.native = 10**17
        self.usdc = 50_000_000
        self.disabled = 0
        self.enabled = 1
        self.log_rows = []
        self.scans = []
        self.scanned_to = 99
        self.proved = None
        self.core_fee = 0
        self.credit_transfer = True
        self.system = None

    def check_chain(self):
        pass

    def balance(self, token=None):
        return self.usdc if token else self.native

    def block(self):
        return 99

    def read(self, contract, data):
        if data == calldata("token()", [], []):
            return word_address(self.chain.usdc)
        values = {
            calldata("isDexForwardingDisabled()", [], []): self.disabled,
            calldata("enabledDestinationDexes(uint32)", ["uint32"], [0]): self.enabled,
        }
        return values.get(data, self.core_fee).to_bytes(32)

    def logs(self, contract, topics, start):
        return self.log_rows

    def scan(self, contract, topics, start, *, max_pages=None):
        self.scans.append((start, max_pages))
        return self.log_rows, self.scanned_to

    def proof(self, txhash):
        return self.proved

    def receipt(self, reference, *, finalized=True):
        return self.proved

    def transferred(self, *args):
        return self.credit_transfer

    def call(self, method, args):
        if method == "eth_gasPrice":
            return hex(100_000_000)
        assert method == "eth_getBlockByNumber"
        return {"timestamp": hex(1000)}

    def system_transfer(self, contract, data, start, time):
        return self.system


def setup():
    rail = LiveRail.__new__(LiveRail)
    rail.testnet = True
    rail.venue_address = Account.create().address
    rail.reserve_address = Account.create().address
    rail.spec = TreasurySpec(reserve_address=rail.reserve_address)
    rail.core = CORE_TEST_WALLET
    rail.hyper, rail.base = Chain(HYPEREVM_TESTNET), Chain(BASE_SEPOLIA)
    rail.exchange = SimpleNamespace(name="hyperliquid-testnet", _info=Info())
    return rail


def test_route_is_native_cctp_and_pots_count_usdc_only():
    rail = setup()
    assert rail.plan("to_reserve") == ("withdraw_burn", "mint_base")
    assert rail.plan("to_venue")[-1] == "deposit_core"
    assert rail.balances()["venue"] == 52_000_000
    assert rail.balances()["reserve"] == 50_000_000


@pytest.mark.parametrize("direction", ["to_reserve", "to_venue"])
def test_no_gas_and_source_pot_shortage_are_refused(direction):
    rail = setup()
    # The self-mint branch is pinned; the forwarded exit is covered in test_gas_route.
    rail.spec = TreasurySpec(reserve_address=rail.reserve_address, cctp_forwarding="never")
    rail.preflight(direction, 10_000_000, {})
    with pytest.raises(RailError, match="source pot"):
        rail.preflight(direction, 51_000_000, {})
    rail.base.native = 0
    with pytest.raises(RailError, match="native ETH"):
        rail.preflight(direction, 10_000_000, {})


def test_route_upgrade_agent_key_or_disabled_forwarding_refuses():
    rail = setup()
    rail.exchange._info.role = "agent"
    with pytest.raises(RailError, match="main wallet"):
        rail.preflight("to_reserve", 10_000_000, {})
    rail.exchange._info.routes["depositRoute"] = "other"
    with pytest.raises(RailError, match="routing changed"):
        rail.preflight("to_venue", 10_000_000, {})
    rail.exchange._info.routes["depositRoute"] = "cctp"
    rail.hyper.disabled = 1
    with pytest.raises(RailError, match="forward"):
        rail.preflight("to_venue", 10_000_000, {})


def test_used_native_budget_is_not_available_again():
    rail = setup()
    rail.spec = TreasurySpec(reserve_address=rail.reserve_address, cctp_forwarding="never")
    with pytest.raises(RailError, match="exhausted"):
        rail.preflight("to_reserve", 10_000_000, {"base": rail.base.gas_budget_wei})


def test_changed_usdc_link_or_missing_venue_spot_hype_is_refused():
    rail = setup()
    rail.exchange._info.linked = rail.reserve_address
    with pytest.raises(RailError, match="linkage"):
        rail.preflight("to_reserve", 10_000_000, {})
    rail.exchange._info.linked = CORE_TEST_WALLET
    rail.exchange._info.hype = "0"
    with pytest.raises(RailError, match="spot HYPE"):
        rail.preflight("to_reserve", 10_000_000, {})


def test_withdrawal_prepared_before_send_and_sdk_reuses_exact_nonce():
    rail = setup()
    signer = Account.create()
    rail.venue_address = signer.address
    posts = []
    rail._sdk = SimpleNamespace(wallet=signer, _post_action=lambda a, s, n: (
        posts.append((deepcopy(a), n)) or {"status": "ok"}
    ))
    state = {"received_micro": 10_000_000, "nonce": 123456}
    ref = rail.prepare("withdraw_burn", state, {})
    assert posts == []
    assert ref["action"]["destinationChainId"] == 6
    assert ref["action"]["data"] == "0x00"
    rail.send("withdraw_burn", ref)
    rail.send("withdraw_burn", ref)
    assert posts[0] == posts[1]
    assert posts[0][1] == 123456
    assert posts[0][0]["hyperliquidChain"] == "Testnet"
    ref["action"]["amount"] = "1000"
    with pytest.raises(RailError, match="modified"):
        rail.send("withdraw_burn", ref)
    assert len(posts) == 2


def test_withdrawal_requires_exact_debit_system_call_and_verified_attestation():
    rail = setup()
    captured = []
    rail.cctp = SimpleNamespace(
        expected_message=lambda *a, **k: captured.append((a, k)) or b"message",
        prove_system_burn=lambda s, d, h, m: {"tx_hash": h, "message": "0x" + m.hex()},
    )
    ref = {"nonce": 123, "start_block": 99, "cctp_max_fee_micro": 0}
    assert rail._withdrawal(ref, 10_000_000) is None
    row = {"time": 124, "hash": "0xcore", "delta": {
        "nonce": 123, "type": "send", "user": rail.venue_address,
        "destination": "0x2000000000000000000000000000000000000000",
        "sourceDex": "", "destinationDex": "spot", "token": "USDC", "amount": "10",
        "fee": "1", "nativeTokenFee": "0.00002",
    }}
    rail.exchange._info.updates = [row]
    assert rail._withdrawal(ref, 10_000_000) is None
    rail.hyper.system = {"type": "0x0", "chainId": hex(998), "nonce": "0x1", "gasPrice": "0x0",
                         "gas": hex(200_000), "value": "0x0", "to": rail.core, "input": "0x00",
                         "from": row["delta"]["destination"], "hash": "0xsystem",
                         "blockHash": "0xblock"}
    result = rail._withdrawal(ref, 10_000_000)
    assert result["received_micro"] == 9_000_000 and result["fee_micro"] == 1_000_800
    assert result["gas_fee_wei"] == 20_000_000_000_000
    assert result["principal_moved"]
    assert captured[0][1]["sender"] == rail.core
    assert captured[0][1]["min_finality"] == 2000
    assert captured[0][1]["hook"] == (
        bytes(28) + (29).to_bytes(4) + bytes.fromhex(rail.venue_address[2:])
        + (123).to_bytes(8) + b"\x00"
    )
    row["delta"]["destination"] = rail.reserve_address
    assert rail._withdrawal(ref, 10_000_000) is None
    row["delta"]["destination"] = "0x2000000000000000000000000000000000000000"
    row["delta"]["fee"] = "2"
    with pytest.raises(RailError, match="fees"):
        rail._withdrawal(ref, 10_000_000)


def deposit_case():
    rail = setup()
    ref = {"chain_key": "hyper", "gas_usd": "40", "gas_symbol": "HYPE", "network": "eip155:998",
           "tx_hash": "0xdeposit", "start_ms": 999000, "start_block": 99, "credit_before": []}
    log = {"address": rail.core, "transactionHash": ref["tx_hash"], "topics": [
        event_topic("SendAsset(address,uint64,uint32)"),
        "0x" + word_address(rail.venue_address).hex(),
    ], "data": "0x" + encode(["uint64", "uint32"], [10**9, 0]).hex()}
    receipt = {"success": True, "gas_fee_wei": 10**12, "logs": [log],
               "blockHash": "0xblock", "blockNumber": "0x64"}
    row = {"time": 1_000_450, "hash": "0xhypercore", "delta": {
        "type": "send", "user": rail.core.lower(), "destination": rail.venue_address.lower(),
        "sourceDex": "spot", "destinationDex": "", "token": "USDC", "amount": "10.0",
        "usdcValue": "10.0", "nonce": 12345,
    }}
    rail.hyper.log_rows, rail.hyper.proved = [log], receipt
    rail.exchange._info.updates = [row]
    state = {"reference": ref, "received_micro": 10_000_000}
    return rail, state, row


def test_deposit_requires_hypercore_credit_beyond_the_evm_receipt():
    rail, state, row = deposit_case()
    rail.exchange._info.updates = []
    assert rail.poll("deposit_core", state) is None
    rail.exchange._info.updates = [row]
    result = rail.poll("deposit_core", state)
    assert result["confirmed"] and result["received_micro"] == 10_000_000
    assert result["fee_micro"] == 40
    assert result["evidence"]["venue_ledger_hash"] == "0xhypercore"
    assert result["evidence"]["tx_hash"] == "0xdeposit"


@pytest.mark.parametrize("field,value", [
    ("user", "0xwrong"), ("destination", "0xwrong"), ("destinationDex", "spot"),
    ("sourceDex", ""), ("amount", "9"), ("type", "deposit"), ("token", "HYPE"),
])
def test_wrong_core_credit_never_confirms(field, value):
    rail, state, row = deposit_case()
    row["delta"][field] = value
    assert rail.poll("deposit_core", state) is None


def test_duplicate_core_credits_or_evm_deposits_never_guess_correlation():
    rail, state, row = deposit_case()
    rail.exchange._info.updates.append({**deepcopy(row), "hash": "0xother"})
    with pytest.raises(RailError, match="ambiguous"):
        rail.poll("deposit_core", state)
    rail.exchange._info.updates = [row]
    rail.hyper.log_rows.append({**deepcopy(rail.hyper.log_rows[0]), "transactionHash": "0xother"})
    with pytest.raises(RailError, match="ambiguous"):
        rail.poll("deposit_core", state)


def test_old_credit_and_expired_processing_window_never_confirm():
    rail, state, row = deposit_case()
    state["reference"]["credit_before"] = [row["hash"]]
    assert rail.poll("deposit_core", state) is None
    state["reference"]["credit_before"] = []
    row["time"] += 120000
    assert rail.poll("deposit_core", state) is None


def test_reverted_transaction_books_gas_without_moving_principal():
    rail, state, _ = deposit_case()
    rail.hyper.proved["success"] = False
    result = rail.poll("deposit_core", state)
    assert not result["confirmed"] and not result["principal_moved"]
    assert result["fee_micro"] == 40 and result["gas_fee_wei"] == 10**12
    assert Decimal(result["received_micro"]) == 10_000_000


def provisional_approval_case():
    rail = setup()
    approval = {"chain_key": "base", "gas_usd": "3000", "gas_symbol": "ETH",
                "network": "eip155:84532", "tx_hash": "0xapproval",
                "gas_ceiling_wei": 2 * 10**12, "tx": {"nonce": 3}}
    burn = {"network": "eip155:84532", "tx_hash": "0xburn", "tx": {"nonce": 4},
            "gas_ceiling_wei": 3 * 10**12, "cctp_max_fee_micro": 100_000}
    observed = {"approval_final": False, "burn_final": False, "remaining": None}

    def prepare(source, destination, amount, remaining, cap):
        observed["remaining"] = remaining
        return deepcopy(burn)

    def receipt(reference, *, finalized=True):
        approving = reference["tx_hash"] == "0xapproval"
        if finalized and not observed["approval_final" if approving else "burn_final"]:
            return None
        return {"success": True, "blockHash": "0xblock", "blockNumber": "0x64",
                "gas_fee_wei": (1 if approving else 2) * 10**12}

    rail.base.receipt = receipt
    rail.cctp = SimpleNamespace(burn=prepare, message=lambda *a, **k: b"message")
    state = {"reference": approval, "received_micro": 10_000_000, "route_data": {},
             "gas_spent": {"base": 123}}
    return rail, state, observed, burn


def test_approval_and_burn_share_finality_without_early_money_or_gas_settlement():
    rail, state, observed, _ = provisional_approval_case()
    provisional = rail.poll("approve_base", state)
    assert provisional["fee_micro"] == 0 and not provisional["principal_moved"]
    assert "provisional" in provisional["evidence"]["confirmation"]
    assert observed["remaining"] == rail.base.gas_budget_wei - 123 - 2 * 10**12
    state["route_data"] = provisional["route_data"]
    prepared = rail.prepare("burn_base", state, state["gas_spent"])
    assert prepared["tx"]["nonce"] == 4
    assert prepared["pending_approval"]["tx"]["nonce"] == 3
    state["reference"] = prepared
    assert rail.poll("burn_base", state) is None
    observed["burn_final"] = True
    assert rail.poll("burn_base", state) is None  # approval must also be finalized
    observed["approval_final"] = True
    settled = rail.poll("burn_base", state)
    assert settled["fee_micro"] == 9000 and settled["wallet_fee_micro"] == 0
    assert settled["gas_fee_wei"] == 3 * 10**12 and settled["principal_moved"]
    assert settled["evidence"]["approval"]["confirmation"] == "finalized"


def test_provisional_reorg_rebroadcasts_both_original_nonce_ordered_transactions():
    rail, state, _, _ = provisional_approval_case()
    prepared = rail.poll("approve_base", state)["route_data"]["prepared_burn"]
    sent = []

    def broadcast(ref):
        sent.append(deepcopy(ref))
        if ref["tx_hash"] == "0xapproval":
            raise Pending("already known or outcome unknown")

    rail.base.broadcast = broadcast
    rail.send("burn_base", prepared)
    rail.send("burn_base", prepared)
    assert [ref["tx"]["nonce"] for ref in sent] == [3, 4, 3, 4]
    assert sent[:2] == sent[2:]


def test_changed_nonce_keeps_the_approval_pending_instead_of_replacing_it():
    rail, state, _, burn = provisional_approval_case()
    burn["tx"]["nonce"] = 3
    with pytest.raises(Pending, match="nonce changed"):
        rail.poll("approve_base", state)
