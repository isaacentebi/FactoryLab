"""Forward-on-empty: the world chooses its Base mint from its own observed gas position.

Every case is offline: the venue, both chains and Circle are recorded fixtures. The
forwarded branch sends empty ``data`` so Circle's forwarder mints on Base for the
on-chain quoted fee; the self-mint branch keeps ``0x00`` and pays gas itself.
"""

from copy import deepcopy
from types import SimpleNamespace

import pytest
from eth_account import Account

from factorylab.runtime.worlds import TreasurySpec
from factorylab.world.cctp import CCTP
from factorylab.world.evm import (
    BASE_SEPOLIA,
    CORE_TEST_WALLET,
    EVM,
    HYPEREVM_TESTNET,
    Pending,
    RailError,
    calldata,
    event_topic,
    word_address,
)
from factorylab.world.treasury_rails import MINT_GAS_ALLOWANCE, gas_micro
from factorylab.world.x402 import HTTPResponse
from tests.world.test_treasury_rails import setup as base_setup

FEE = "calculateCrossChainWithdrawalFee(bool,uint32)"
QUOTE_SELF = calldata(FEE, ["bool", "uint32"], [False, 6])
QUOTE_FORWARD = calldata(FEE, ["bool", "uint32"], [True, 6])
RECEIVED = event_topic("MessageReceived(address,uint32,bytes32,bytes32,uint32,bytes)")
TRANSFER = event_topic("Transfer(address,address,uint256)")
# The fixture chain quotes 0.1 gwei on both chains, so the self-mint estimate is 4e13 wei.
ESTIMATE = MINT_GAS_ALLOWANCE * 100_000_000 * 2


def setup(*, mode="on_empty_gas", eth=0, quotes=(0, 200_000), **spec):
    rail = base_setup()
    rail.spec = TreasurySpec(reserve_address=rail.reserve_address, cctp_forwarding=mode, **spec)
    rail.base.native = eth
    quoted = dict(zip((QUOTE_SELF, QUOTE_FORWARD), quotes, strict=True))
    read = rail.hyper.read
    rail.hyper.read = lambda contract, data: (
        quoted[data].to_bytes(32) if data in quoted else read(contract, data))
    return rail


def test_withdraw_action_data_byte_selects_forwarding_and_send_checks_the_flag():
    rail = setup()
    signer = Account.create()
    rail.venue_address = signer.address
    posts = []
    rail._sdk = SimpleNamespace(wallet=signer, _post_action=lambda a, s, n: (
        posts.append(deepcopy(a)) or {"status": "ok"}))
    ref = rail.prepare("withdraw_burn", {"received_micro": 10_000_000, "nonce": 5}, {})
    assert ref["forward"] is True and ref["action"]["data"] == "0x"
    assert ref["cctp_max_fee_micro"] == 200_000 and ref["base_start_block"] == 99
    assert ref["gas_route"]["reason"] == "no_base_eth"
    assert ref["gas_route"]["quote_source"] == "CoreDepositWallet.calculateCrossChainWithdrawalFee"
    rail.send("withdraw_burn", ref)
    assert posts[0]["data"] == "0x" and posts[0]["nonce"] == 5
    with pytest.raises(RailError, match="modified"):
        rail.send("withdraw_burn", {**deepcopy(ref), "forward": False})
    assert len(posts) == 1
    funded = setup(eth=10**17)
    funded.venue_address = signer.address
    ref = funded.prepare("withdraw_burn", {"received_micro": 10_000_000, "nonce": 6}, {})
    assert ref["forward"] is False and ref["action"]["data"] == "0x00"
    assert ref["cctp_max_fee_micro"] == 0 and ref["gas_route"]["reason"] == "base_eth_available"


def test_forwarded_hook_bytes_match_the_deployed_layout_and_carry_the_quoted_max_fee():
    rail = setup()
    captured, calls = [], []
    rail.cctp = SimpleNamespace(
        expected_message=lambda *a, **k: captured.append(k) or b"message",
        prove_system_burn=lambda s, d, h, m: {"tx_hash": h, "message": "0x" + m.hex()},
    )
    system = rail.hyper.system_transfer
    rail.hyper.system_transfer = lambda c, data, s, t: calls.append(data) or system(c, data, s, t)
    rail.hyper.system = {"type": "0x0", "chainId": hex(998), "nonce": "0x1", "gasPrice": "0x0",
                         "gas": hex(200_000), "value": "0x0", "to": rail.core, "input": "0x00",
                         "from": "0x2000000000000000000000000000000000000000",
                         "hash": "0xsystem", "blockHash": "0xblock"}
    rail.exchange._info.updates = [{"time": 124, "hash": "0xcore", "delta": {
        "nonce": 123, "type": "send", "user": rail.venue_address,
        "destination": "0x2000000000000000000000000000000000000000",
        "sourceDex": "", "destinationDex": "spot", "token": "USDC", "amount": "10",
        "fee": "1", "nativeTokenFee": "0.00002"}}]
    ref = {"nonce": 123, "start_block": 99, "cctp_max_fee_micro": 200_000, "forward": True,
           "base_start_block": 77, "gas_usd": "40"}
    result = rail._withdrawal(ref, 10_000_000)
    assert captured[0]["hook"] == (
        b"cctp-forward" + bytes(12) + bytes(4) + (28).to_bytes(4)
        + bytes.fromhex(rail.venue_address[2:]) + (123).to_bytes(8)
    )
    assert len(captured[0]["hook"]) == 60 and captured[0]["max_fee_micro"] == 200_000
    assert calls[0] == calldata(
        "coreReceiveWithData(address,bytes32,uint32,uint256,uint64,bytes)",
        ["address", "bytes32", "uint32", "uint256", "uint64", "bytes"],
        [rail.venue_address, word_address(rail.reserve_address), 6, 9_000_000, 123, b""])
    burn = result["route_data"]["burn"]
    assert burn["message"] == "0x" + b"message".hex() and burn["tx_hash"].startswith("0x")
    assert burn["forwarded"] is True and burn["base_start_block"] == 77
    # The unforwarded layout is unchanged: a one-byte body and zero magic.
    rail._withdrawal({**ref, "forward": False, "cctp_max_fee_micro": 0}, 10_000_000)
    assert captured[1]["hook"] == (bytes(28) + (29).to_bytes(4)
                                   + bytes.fromhex(rail.venue_address[2:])
                                   + (123).to_bytes(8) + b"\x00")
    assert result["fee_micro"] == 1_000_800 and result["gas_fee_wei"] == 20_000_000_000_000


@pytest.mark.parametrize("mode,eth,spent,forward,reason", [
    ("on_empty_gas", 0, {}, True, "no_base_eth"),
    ("on_empty_gas", ESTIMATE - 1, {}, True, "no_base_eth"),
    ("on_empty_gas", ESTIMATE, {}, False, "base_eth_available"),
    ("on_empty_gas", 10**17, {"base": 10**16 - ESTIMATE + 1}, True, "base_gas_budget_exhausted"),
    ("always", 10**17, {}, True, "manifest"),
    ("never", 10**17, {}, False, "manifest"),
])
def test_branch_follows_the_observed_base_eth_and_remaining_budget(
        mode, eth, spent, forward, reason):
    rail = setup(mode=mode, eth=eth)
    route = rail.gas_route(spent)
    assert route["forward"] is forward and route["reason"] == reason
    assert route["base_eth_wei"] == eth and route["mode"] == mode
    assert route["base_gas_remaining_wei"] == 10**16 - spent.get("base", 0)
    assert route["base_mint_estimate_wei"] == ESTIMATE
    assert route["forward_fee_micro"] == 200_000
    assert route["cctp_max_fee_micro"] == (200_000 if forward else 0)
    assert route["core_hype_wei"] == 10**18 and route["core_hype_required_wei"] == 4 * 10**13
    rail.preflight("to_reserve", 10_000_000, spent)  # no Base gas check on the forwarded branch


def test_never_mode_keeps_the_self_mint_refusals_and_forwarding_needs_no_base_gas():
    rail = setup(mode="never")
    with pytest.raises(RailError, match="native ETH"):
        rail.preflight("to_reserve", 10_000_000, {})
    with pytest.raises(RailError, match="exhausted"):
        setup(mode="never", eth=10**17).preflight("to_reserve", 10_000_000, {"base": 10**16})
    rail = setup()
    rail.preflight("to_reserve", 10_000_000, {"base": 10**16})
    rail.exchange._info.hype = "0"
    with pytest.raises(RailError, match="spot HYPE"):
        rail.preflight("to_reserve", 10_000_000, {})


def test_on_chain_quote_bounds_the_burn_max_fee_before_anything_is_signed():
    with pytest.raises(RailError, match="CoreDepositWallet cannot currently forward"):
        setup(quotes=(0, 0)).preflight("to_reserve", 10_000_000, {})
    with pytest.raises(RailError, match="quote 250000 exceeds treasury.max_forward_fee_micro"):
        setup(quotes=(0, 250_000)).prepare(
            "withdraw_burn", {"received_micro": 10_000_000, "nonce": 1}, {})
    setup(quotes=(0, 250_000), max_forward_fee_micro=250_000).preflight(
        "to_reserve", 10_000_000, {})
    with pytest.raises(RailError, match="venue CCTP fee cap exceeds manifest cap"):
        setup(quotes=(150_000, 350_000)).preflight("to_reserve", 10_000_000, {})
    # A zero forward quote is irrelevant when the reserve self-mints.
    setup(quotes=(0, 0), eth=10**17).preflight("to_reserve", 10_000_000, {})
    with pytest.raises(RailError, match="fee-covered venue withdrawal minimum"):
        setup().preflight("to_reserve", 1_200_000, {})
    setup().preflight("to_reserve", 1_200_001, {})


def forwarded_burn(fee_executed):
    account = Account.create()
    hyper, base = EVM(HYPEREVM_TESTNET, account), EVM(BASE_SEPOLIA, account)
    cctp = CCTP(testnet=True)
    hook = (b"cctp-forward" + bytes(12) + bytes(4) + (28).to_bytes(4)
            + bytes.fromhex(account.address[2:]) + (123).to_bytes(8))
    expected = cctp.expected_message(hyper, base, 9_000_000, sender=CORE_TEST_WALLET,
                                     max_fee_micro=200_000, min_finality=2000, hook=hook)
    attested = bytearray(expected)
    attested[12:44] = (77).to_bytes(32)
    attested[144:148] = (2000).to_bytes(4)
    attested[312:344] = fee_executed.to_bytes(32)
    row = {"status": "complete", "message": "0x" + attested.hex(),
           "attestation": "0x" + bytes(65).hex()}
    cctp.transport = lambda *args: HTTPResponse(200, {"messages": [row]}, {})
    burn = {"tx_hash": "0x" + "1" * 64, "message": "0x" + expected.hex(), "forwarded": True}
    return cctp, hyper, base, burn


def test_an_executed_forwarding_fee_above_the_quote_is_refused_by_the_attestation_check():
    cctp, hyper, base, burn = forwarded_burn(200_001)
    with pytest.raises(RailError, match="finality or fee"):
        cctp.attestation(hyper, base, burn)
    cctp, hyper, base, burn = forwarded_burn(200_000)
    message, _, fee = cctp.attestation(hyper, base, burn)
    assert fee == 200_000 and message[12:44] == (77).to_bytes(32)


def observed_case(*, eth=0, attested=True):
    rail = setup(eth=eth)
    message = bytearray(376)
    message[12:44] = (77).to_bytes(32)
    nonce = "0x" + (77).to_bytes(32).hex()

    def attestation(source, destination, burn):
        if not attested:
            raise Pending("matching Circle attestation is not yet available")
        return bytes(message), bytes(65), 200_000

    rail.cctp = SimpleNamespace(attestation=attestation, minted=CCTP.minted,
                                mint=lambda *a: pytest.fail("self-mint prepared"))
    rail.base.account = SimpleNamespace(address=rail.reserve_address)  # the mint recipient
    rail.base.broadcast = lambda ref: pytest.fail("a forwarded mint is never broadcast")
    rail.base.read = lambda contract, data: pytest.fail("no unclaimed check without ETH")
    log = {"address": BASE_SEPOLIA.transmitter, "transactionHash": "0xforwarder",
           "blockHash": "0xblock", "blockNumber": "0x70",
           "topics": [RECEIVED, "0x" + word_address(rail.reserve_address).hex(), nonce,
                      "0x" + (2000).to_bytes(32).hex()]}
    credit = {"address": BASE_SEPOLIA.usdc, "topics": [
        TRANSFER, "0x" + bytes(32).hex(), "0x" + word_address(rail.reserve_address).hex()],
        "data": hex(8_800_000)}
    receipt = {"status": "0x1", "blockHash": "0xblock", "blockNumber": "0x70",
               "transactionHash": "0xforwarder", "from": "0xf0rwarder", "logs": [log, credit]}
    state = {"received_micro": 9_000_000, "route_data": {"burn": {
        "tx_hash": "0x" + "1" * 64, "message": "0x" + bytes(376).hex(),
        "forwarded": True, "base_start_block": 77}}}
    return rail, state, log, credit, receipt, nonce


def test_forwarded_mint_is_observed_not_sent_and_confirms_only_the_credited_amount():
    rail, state, log, credit, receipt, nonce = observed_case(attested=False)
    with pytest.raises(Pending, match="attestation"):
        rail.prepare("mint_base", state, {})
    rail, state, log, credit, receipt, nonce = observed_case()
    with pytest.raises(Pending, match="forwarder"):
        rail.prepare("mint_base", state, {})
    rail.base.log_rows = [log]
    ref = rail.prepare("mint_base", state, {})
    assert ref == {"network": "eip155:84532", "chain_key": "base", "forwarded": True,
                   "tx_hash": "0xforwarder", "cctp_nonce": nonce, "cctp_fee_micro": 200_000,
                   "fee_ceiling_micro": 200_000, "start_block": 77}
    assert rail.send("mint_base", ref) is None
    state["reference"] = ref
    assert rail.poll("mint_base", state) is None
    rail.base.proved = {**receipt, "logs": [log]}
    with pytest.raises(RailError, match="credit"):
        rail.poll("mint_base", state)
    rail.base.proved = {**receipt, "logs": [credit]}
    with pytest.raises(RailError, match="MessageReceived"):
        rail.poll("mint_base", state)
    rail.base.proved = receipt
    result = rail.poll("mint_base", state)
    assert result["confirmed"] and result["principal_moved"]
    assert result["received_micro"] == 8_800_000
    assert result["fee_micro"] == result["wallet_fee_micro"] == 200_000
    assert "gas_fee_wei" not in result and result["chain_key"] == "base"
    assert result["evidence"] == {
        "network": "eip155:84532", "tx_hash": "0xforwarder", "block_hash": "0xblock",
        "cctp_nonce": nonce, "cctp_fee_micro": 200_000, "forwarded": True,
        "forwarder": "0xf0rwarder", "credited_micro": 8_800_000}
    rail.base.log_rows = [log, {**log, "transactionHash": "0xother"}]
    with pytest.raises(RailError, match="ambiguous"):
        rail.prepare("mint_base", state, {})


def test_a_stranded_forward_falls_back_to_self_mint_only_while_unclaimed_and_affordable():
    rail, state, log, credit, receipt, nonce = observed_case(eth=10**17)
    prepared = []
    rail.cctp.mint = lambda source, destination, burn, remaining: prepared.append(remaining) or {
        "network": "eip155:84532", "sender": rail.reserve_address, "tx_hash": "0xself",
        "tx": {"nonce": 9}, "gas_ceiling_wei": 10**12, "l1_fee_ceiling_wei": 0,
        "cctp_fee_micro": 200_000, "cctp_nonce": nonce}
    reads = []
    rail.base.read = lambda contract, data: reads.append((contract, data)) or (1).to_bytes(32)
    ref = rail.prepare("mint_base", state, {"base": 5})
    assert ref["fallback"] == "self_mint" and ref["tx_hash"] == "0xself"
    assert ref["chain_key"] == "base" and prepared == [10**16 - 5]
    assert ref["fee_ceiling_micro"] == gas_micro(10**12, "3000") + 200_000
    assert reads[0][0] == BASE_SEPOLIA.transmitter and reads[0][1].startswith(
        calldata("receiveMessage(bytes,bytes)", ["bytes", "bytes"], [bytes(376), bytes(65)])[:10])
    # A message the forwarder already claimed is never minted twice.
    rail.base.read = lambda contract, data: bytes(32)
    with pytest.raises(Pending, match="forwarder"):
        rail.prepare("mint_base", state, {"base": 5})
    rail.base.read = lambda contract, data: (_ for _ in ()).throw(Pending("reverted"))
    with pytest.raises(Pending, match="forwarder"):
        rail.prepare("mint_base", state, {"base": 5})
    assert len(prepared) == 1
    # The forwarder's finalized mint always wins over a fallback.
    rail.base.read = lambda contract, data: (1).to_bytes(32)
    rail.base.log_rows = [log]
    assert rail.prepare("mint_base", state, {"base": 5})["forwarded"] is True
    assert len(prepared) == 1
    # No budget left: wait for the forwarder rather than fail.
    rail.base.log_rows = []
    with pytest.raises(Pending, match="forwarder"):
        rail.prepare("mint_base", state, {"base": 10**16})


def test_gas_view_reports_the_position_the_route_and_the_exact_blocker():
    view = setup().gas_view({})
    assert view == {
        "mode": "on_empty_gas", "route": "forwarded", "reason": "no_base_eth",
        "core_hype": "1", "core_hype_required": "0.00004",
        "base_eth_wei": 0, "base_gas_remaining_wei": 10**16, "base_mint_estimate_wei": ESTIMATE,
        "forward_fee_micro": 200_000, "cctp_max_fee_micro": 200_000,
        "minimum_micro": 1_200_001, "refill_ready": True, "blocked_by": None}
    view = setup(eth=10**17).gas_view({"base": 7})
    assert view["route"] == "self_mint" and view["reason"] == "base_eth_available"
    assert view["base_gas_remaining_wei"] == 10**16 - 7 and view["minimum_micro"] == 1_000_001
    assert view["forward_fee_micro"] == 200_000 and view["cctp_max_fee_micro"] == 0
    rail = setup()
    rail.exchange._info.hype = "0"
    view = rail.gas_view({})
    assert not view["refill_ready"]
    assert view["blocked_by"] == "venue requires spot HYPE for the Core-to-EVM gas charge"
    assert view["core_hype"] == "0" and view["route"] == "forwarded"
    view = setup(quotes=(0, 0)).gas_view({})
    assert view["blocked_by"] == "CoreDepositWallet cannot currently forward the destination mint"
    view = setup(mode="never").gas_view({})
    assert view["route"] == "self_mint"
    assert view["blocked_by"] == "reserve requires native ETH on base"


def already_delivered(used):
    """Circle's forwarder has minted our message, so receiveMessage now reverts on Base.

    The live testnet run saw exactly this: the forwarder delivered nonce
    0x33f8ff37... five Base Sepolia blocks after the burn, and every later
    ``eth_call`` of ``receiveMessage`` answered "execution reverted: Nonce
    already used", which ``EVM.call`` reports as ``Pending``.
    """
    rail = setup()
    rail.base.account = SimpleNamespace(address=rail.reserve_address)
    cctp = CCTP(testnet=True)
    hook = (b"cctp-forward" + bytes(12) + bytes(4) + (28).to_bytes(4)
            + bytes.fromhex(rail.venue_address[2:]) + (123).to_bytes(8))
    expected = cctp.expected_message(rail.hyper, rail.base, 9_000_000, sender=CORE_TEST_WALLET,
                                     max_fee_micro=200_000, min_finality=2000, hook=hook)
    attested = bytearray(expected)
    attested[12:44] = (77).to_bytes(32)
    attested[144:148] = (2000).to_bytes(4)
    attested[312:344] = (200_000).to_bytes(32)
    row = {"status": "complete", "message": "0x" + bytes(attested).hex(),
           "attestation": "0x" + bytes(65).hex()}
    cctp.transport = lambda *args: HTTPResponse(200, {"messages": [row]}, {})
    rail.cctp = cctp
    used_call = calldata("usedNonces(bytes32)", ["bytes32"], [(77).to_bytes(32)])
    reads = []

    def read(contract, data):
        reads.append(data)
        if data == used_call:
            return used.to_bytes(32)
        raise Pending("RPC call rejected or unavailable")

    rail.base.read = read
    rail.hyper.system = {"type": "0x0", "chainId": hex(998), "nonce": "0x1", "gasPrice": "0x0",
                         "gas": hex(200_000), "value": "0x0", "to": rail.core, "input": "0x00",
                         "from": "0x2000000000000000000000000000000000000000",
                         "hash": "0xsystem", "blockHash": "0xblock"}
    rail.exchange._info.updates = [{"time": 124, "hash": "0xcore", "delta": {
        "nonce": 123, "type": "send", "user": rail.venue_address,
        "destination": "0x2000000000000000000000000000000000000000",
        "sourceDex": "", "destinationDex": "spot", "token": "USDC", "amount": "10",
        "fee": "1", "nativeTokenFee": "0.00002"}}]
    ref = {"nonce": 123, "start_block": 99, "cctp_max_fee_micro": 200_000, "forward": True,
           "base_start_block": 77, "gas_usd": "40"}
    return rail, ref, reads, used_call


def test_a_forwarded_burn_the_forwarder_already_delivered_confirms_instead_of_stalling():
    rail, ref, reads, used_call = already_delivered(1)
    result = rail._withdrawal(ref, 10_000_000)
    assert result["confirmed"] and result["principal_moved"]
    assert result["route_data"]["burn"]["forwarded"] is True
    assert result["route_data"]["burn"]["message"] == "0x" + bytes(
        rail.cctp.attestation(rail.hyper, rail.base, {
            "tx_hash": "0x" + "1" * 64,
            "message": result["route_data"]["burn"]["message"]})[0]).hex()
    # A consumed nonce is the transmitter's own record: no dry run is attempted.
    assert reads == [used_call]


def test_an_unconsumed_nonce_still_requires_the_destination_to_validate_the_attestation():
    rail, ref, reads, used_call = already_delivered(0)
    with pytest.raises(Pending):
        rail._withdrawal(ref, 10_000_000)
    assert len(reads) == 2 and reads[0] == used_call and reads[1] != used_call
