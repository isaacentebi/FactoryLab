from copy import deepcopy

import pytest
from eth_abi import decode, encode
from eth_account import Account

from factorylab.world.cctp import CCTP
from factorylab.world.evm import (
    BASE,
    BASE_SEPOLIA,
    EVM,
    HYPEREVM_TESTNET,
    Pending,
    RailError,
    calldata,
    event_topic,
    word_address,
)
from factorylab.world.x402 import HTTPResponse


def pair():
    from factorylab.runtime.capital_loop import ReserveGuard

    account = Account.create()
    chains = EVM(HYPEREVM_TESTNET, account), EVM(BASE_SEPOLIA, account)
    for chain in chains:  # written ahead, in the test's lock dir
        chain.transaction_guard = ReserveGuard("test")
    return chains


def message(source, dest, amount=5_000_000):
    return b"".join(
        [
            (1).to_bytes(4),
            source.chain.domain.to_bytes(4),
            dest.chain.domain.to_bytes(4),
            bytes(32),
            word_address(source.chain.messenger),
            word_address(dest.chain.messenger),
            bytes(32),
            (2000).to_bytes(4),
            bytes(4),
            (1).to_bytes(4),
            word_address(source.chain.usdc),
            word_address(dest.account.address),
            amount.to_bytes(32),
            word_address(source.account.address),
            (10000).to_bytes(32),
            bytes(32),
            bytes(32),
        ]
    )


def params(source):
    return dict(sender=source.account.address, max_fee_micro=10000, min_finality=2000)


@pytest.mark.parametrize("offset", [0, 4, 8, 44, 76, 108, 140, 148, 152, 184, 216, 248, 280])
def test_message_substitution_is_rejected(offset):
    source, dest = pair()
    cctp = CCTP(testnet=True)
    original = message(source, dest)
    cctp.validate_message(original, source, dest, 5_000_000, **params(source))
    forged = bytearray(original)
    forged[offset] ^= 1
    with pytest.raises(RailError, match="match"):
        cctp.validate_message(bytes(forged), source, dest, 5_000_000, **params(source))


def test_mixed_networks_are_refused_before_a_read_or_write():
    source, dest = pair()
    dest.chain = BASE
    with pytest.raises(RailError, match="mixed"):
        CCTP(testnet=True).burn(source, dest, 5_000_000, 10**15, 10_000)


def test_base_without_fee_switch_prepares_capped_standard_burn_without_missing_accessor():
    dest, source = pair()
    source.balance = lambda token: 5_000_000
    source.read = lambda *args: pytest.fail("Base has no getMinFeeAmount accessor")
    prepared = []
    source.prepare = lambda to, data, **kw: prepared.append(data) or {"tx_hash": "0xburn"}
    result = CCTP(testnet=True).burn(source, dest, 5_000_000, 10**15, 10_000)
    values = decode(["uint256", "uint32", "bytes32", "address", "bytes32", "uint256", "uint32"],
                    bytes.fromhex(prepared[0][10:]))
    assert values[0] == 5_000_000 and values[-2:] == (10_000, 2000)
    assert result["cctp_max_fee_micro"] == 10_000


def test_fee_switch_chains_still_refuse_over_cap_or_unavailable_fee():
    source, dest = pair()
    source.balance = lambda token: 5_000_000
    source.prepare = lambda *args, **kw: pytest.fail("must refuse before preparing")
    source.read = lambda contract, data: (10_001).to_bytes(32)
    with pytest.raises(RailError, match="exceeds"):
        CCTP(testnet=True).burn(source, dest, 5_000_000, 10**15, 10_000)

    def unavailable(contract, data):
        assert data == calldata("getMinFeeAmount(uint256)", ["uint256"], [5_000_000])
        raise Pending("fee accessor unavailable")

    source.read = unavailable
    with pytest.raises(Pending, match="unavailable"):
        CCTP(testnet=True).burn(source, dest, 5_000_000, 10**15, 10_000)


def test_burn_receipt_must_contain_one_message_from_pinned_transmitter():
    source, dest = pair()
    cctp = CCTP(testnet=True)
    raw = message(source, dest)
    log = {
        "address": source.chain.transmitter,
        "topics": [event_topic("MessageSent(bytes)")],
        "data": "0x" + encode(["bytes"], [raw]).hex(),
    }
    assert cctp.message({"logs": [log]}, source, dest, 5_000_000, **params(source)) == raw
    for logs in (
        [],
        [log, log],
        [{**log, "removed": True}],
        [{**log, "address": source.chain.usdc}],
    ):
        with pytest.raises(RailError, match="exactly one"):
            cctp.message({"logs": logs}, source, dest, 5_000_000, **params(source))


def fixture_attestation():
    source, dest = pair()
    raw = message(source, dest)
    attested = bytearray(raw)
    attested[12:44] = (123).to_bytes(32)
    attested[144:148] = (2000).to_bytes(4)
    attested[312:344] = (1000).to_bytes(32)
    row = {
        "status": "complete",
        "message": "0x" + attested.hex(),
        "attestation": "0x" + bytes(65).hex(),
    }
    burn = {"message": "0x" + raw.hex(), "tx_hash": "0x" + "1" * 64}
    return source, dest, row, burn


def test_incomplete_attestation_never_prepares_a_mint():
    source, dest, row, burn = fixture_attestation()
    dest.prepare = lambda *args, **kw: pytest.fail("prepared without attestation")
    row["status"] = "pending"
    cctp = CCTP(testnet=True, transport=lambda *args: HTTPResponse(200, {"messages": [row]}, {}))
    with pytest.raises(Pending):
        cctp.mint(source, dest, burn, 10**15)


def test_circle_can_assign_nonce_finality_and_fee_but_cannot_change_burn_intent():
    source, dest, row, burn = fixture_attestation()
    cctp = CCTP(testnet=True, transport=lambda *args: HTTPResponse(200, {"messages": [row]}, {}))
    _, _, fee = cctp.attestation(source, dest, burn)
    assert fee == 1000
    # A different recipient cannot be smuggled in by an otherwise complete API result.
    changed = bytearray.fromhex(row["message"][2:])
    changed[184] ^= 1
    row["message"] = "0x" + changed.hex()
    with pytest.raises(Pending, match="matching"):
        cctp.attestation(source, dest, burn)


@pytest.mark.parametrize("offset,value", [(144, 1000), (312, 10001)])
def test_attestation_below_finality_or_above_fee_cap_never_mints(offset, value):
    source, dest, row, burn = fixture_attestation()
    changed = bytearray.fromhex(row["message"][2:])
    size = 4 if offset == 144 else 32
    changed[offset : offset + size] = value.to_bytes(size)
    row["message"] = "0x" + changed.hex()
    cctp = CCTP(testnet=True, transport=lambda *args: HTTPResponse(200, {"messages": [row]}, {}))
    with pytest.raises(RailError, match="finality or fee"):
        cctp.attestation(source, dest, burn)


def test_expired_attestation_requests_same_burn_again():
    source, dest, row, burn = fixture_attestation()
    changed = bytearray.fromhex(row["message"][2:])
    changed[344:376] = (10).to_bytes(32)
    row["message"] = "0x" + changed.hex()
    dest.block = lambda: 11
    calls = []

    def transport(method, url, payload, headers):
        calls.append((method, url))
        return HTTPResponse(200, {"messages": [deepcopy(row)]}, {})

    with pytest.raises(Pending, match="expired"):
        CCTP(testnet=True, transport=transport).attestation(source, dest, burn)
    assert calls[-1] == (
        "POST",
        "https://iris-api-sandbox.circle.com/v2/reattest/0x" + (123).to_bytes(32).hex(),
    )


def test_mint_requires_credit_from_the_canonical_token():
    _, dest = pair()
    log = {
        "address": dest.chain.usdc,
        "topics": [
            event_topic("Transfer(address,address,uint256)"),
            "0x" + bytes(32).hex(),
            "0x" + word_address(dest.account.address).hex(),
        ],
        "data": hex(5_000_000),
    }
    assert CCTP.minted({"logs": [log]}, dest, 5_000_000)
    for change in (
        {"address": dest.chain.transmitter},
        {"data": hex(4_999_999)},
        {"removed": True},
    ):
        assert not CCTP.minted({"logs": [{**log, **change}]}, dest, 5_000_000)


def test_system_burn_requires_destination_signature_validation_not_just_iris_success():
    source, dest, row, burn = fixture_attestation()
    cctp = CCTP(testnet=True, transport=lambda *args: HTTPResponse(200, {"messages": [row]}, {}))
    expected = cctp.expected_message(source, dest, 5_000_000, **params(source))
    assert expected.hex() == burn["message"][2:]
    dest.read = lambda contract, data: bytes(32)
    with pytest.raises(RailError, match="validate"):
        cctp.prove_system_burn(source, dest, burn["tx_hash"], expected)
    dest.read = lambda contract, data: (1).to_bytes(32)
    proved = cctp.prove_system_burn(source, dest, burn["tx_hash"], expected)
    assert proved["message"] == row["message"]
    row["attestation"] = "0x"
    with pytest.raises(RailError, match="attestation size"):
        cctp.prove_system_burn(source, dest, burn["tx_hash"], expected)


def test_a_zero_nonce_is_never_a_consumed_message():
    source, dest, row, burn = fixture_attestation()
    dest.read = lambda contract, data: (1).to_bytes(32)
    with pytest.raises(RailError, match="nonce"):
        CCTP.consumed(dest, bytes(376))
    with pytest.raises(RailError, match="nonce"):
        CCTP.nonce_used(dest, bytes(32))
    assert CCTP.consumed(dest, bytes(12) + (123).to_bytes(32) + bytes(332))
    assert CCTP.nonce_used(dest, (123).to_bytes(32))
    # An attestation whose nonce is zero cannot prove a burn by the consumed-nonce record.
    attested = bytearray.fromhex(row["message"][2:])
    attested[12:44] = bytes(32)
    row["message"] = "0x" + attested.hex()
    cctp = CCTP(testnet=True, transport=lambda *args: HTTPResponse(200, {"messages": [row]}, {}))
    expected = cctp.expected_message(source, dest, 5_000_000, **params(source))
    with pytest.raises(RailError, match="nonce"):
        cctp.prove_system_burn(source, dest, burn["tx_hash"], expected)
