from copy import deepcopy
from types import SimpleNamespace

import pytest
from eth_account import Account

from factorylab.world.evm import EVM, HYPEREVM_TESTNET, Pending, RailError, event_topic
from factorylab.world.x402 import HTTPResponse


class RPC:
    def __init__(self):
        self.calls = []
        self.chain = hex(998)
        self.receipt = None
        self.final = {"number": "0x10", "hash": "0xabc"}
        self.logs = []
        self.sent = []

    def __call__(self, method, url, body, headers):
        name, args = body["method"], body["params"]
        self.calls.append(name)
        result = {
            "eth_chainId": self.chain,
            "eth_estimateGas": hex(21000),
            "eth_gasPrice": hex(100),
            "eth_getBalance": hex(10**18),
            "eth_getTransactionCount": "0x3",
            "eth_getTransactionReceipt": self.receipt,
            "eth_getBlockByNumber": self.final,
            "eth_getLogs": self.logs,
        }.get(name)
        if name == "eth_sendRawTransaction":
            from eth_utils import keccak

            self.sent.append(args[0])
            result = "0x" + keccak(bytes.fromhex(args[0][2:])).hex()
        return HTTPResponse(200, {"result": result}, {})


def setup():
    rpc = RPC()
    chain = EVM(HYPEREVM_TESTNET, Account.create(), transport=rpc, gas_budget_wei=10**15)
    ref = chain.transfer(chain.chain.usdc, Account.create().address, 5_000_000, 10**15)
    return rpc, chain, ref


def receipt(ref):
    return {
        "transactionHash": ref["tx_hash"],
        "from": ref["sender"],
        "to": ref["tx"]["to"],
        "blockNumber": "0x10",
        "blockHash": "0xabc",
        "gasUsed": hex(20000),
        "effectiveGasPrice": hex(100),
        "status": "0x1",
        "logs": [],
    }


def test_prepare_does_not_broadcast_and_retries_use_same_hash():
    rpc, chain, ref = setup()
    assert rpc.sent == []
    chain.broadcast(ref)
    chain.broadcast(ref)
    assert len(rpc.sent) == 2 and rpc.sent[0] == rpc.sent[1]
    assert "private" not in str(ref).lower()


def test_wrong_chain_or_modified_calldata_is_never_broadcast():
    rpc, chain, ref = setup()
    rpc.chain = hex(84532)
    with pytest.raises(RailError, match="chain ID"):
        chain.broadcast(ref)
    rpc.chain = hex(998)
    ref["tx"]["data"] = "0x00"
    with pytest.raises(RailError, match="modified"):
        chain.broadcast(ref)
    assert rpc.sent == []


def test_gas_ceiling_is_enforced_before_signing():
    rpc, chain, _ = setup()
    chain.account = SimpleNamespace(
        address=chain.account.address, sign_transaction=lambda tx: pytest.fail("signed")
    )
    with pytest.raises(RailError, match="gas budget"):
        chain.transfer(chain.chain.usdc, Account.create().address, 5_000_000, 1)


def test_transport_errors_never_echo_rpc_material():
    _, chain, ref = setup()

    def error(*args):
        raise RuntimeError("sensitive vendor body")

    chain.transport = error
    with pytest.raises(Pending) as exc:
        chain.broadcast(ref)
    assert "sensitive" not in str(exc.value)


def test_receipt_requires_finality_canonical_block_and_exact_identity():
    rpc, chain, ref = setup()
    assert chain.receipt(ref) is None
    rpc.receipt = receipt(ref)
    rpc.final["number"] = "0xf"
    assert chain.receipt(ref) is None
    assert chain.receipt(ref, finalized=False)["success"]
    rpc.final["number"] = "0x10"
    rpc.final["hash"] = "0xdef"
    assert chain.receipt(ref) is None
    assert chain.receipt(ref, finalized=False) is None
    rpc.final["hash"] = "0xabc"
    proved = chain.receipt(ref)
    assert proved["success"] and proved["gas_fee_wei"] == 2_000_000
    rpc.receipt["from"] = Account.create().address
    with pytest.raises(RailError, match="identity"):
        chain.receipt(ref)


def test_reverted_receipt_still_proves_fee():
    rpc, chain, ref = setup()
    rpc.receipt = {**receipt(ref), "status": "0x0"}
    proved = chain.receipt(ref)
    assert not proved["success"] and proved["gas_fee_wei"] == 2_000_000


def test_logs_reject_wrong_contract_topic_removed_and_noncanonical_blocks():
    rpc, chain, _ = setup()
    topic = event_topic("Example(uint256)")
    good = {
        "address": chain.chain.usdc,
        "topics": [topic],
        "blockNumber": "0x10",
        "blockHash": "0xabc",
        "data": "0x1",
    }
    bad = [
        {**good, "removed": True},
        {**good, "address": Account.create().address},
        {**good, "topics": ["0x00"]},
        {**good, "blockHash": "0xdef"},
        {**good, "blockNumber": "0x11"},
    ]
    rpc.logs = [deepcopy(good), *bad]
    assert chain.logs(chain.chain.usdc, [topic], 15) == [good]


def test_log_queries_cover_every_block_within_the_official_fifty_block_limit():
    _, chain, _ = setup()
    pages = []

    def call(method, args):
        if method == "eth_chainId":
            return hex(998)
        if method == "eth_getBlockByNumber":
            return {"number": hex(117)}
        pages.append((int(args[0]["fromBlock"], 16), int(args[0]["toBlock"], 16)))
        return []

    chain.call = call
    assert chain.logs(chain.chain.usdc, [], 10) == []
    assert pages == [(10, 59), (60, 109), (110, 117)]


def test_system_call_requires_exact_calldata_sender_and_canonical_block():
    _, chain, _ = setup()
    tx = {"type": "0x0", "chainId": hex(998), "value": "0x0", "gasPrice": "0x0",
          "to": chain.chain.usdc, "input": "0x1234", "blockHash": "0xb", "blockNumber": "0xb",
          "from": "0x2000000000000000000000000000000000000000"}

    def call(method, args):
        if method == "eth_chainId":
            return hex(998)
        if method == "eth_getBlockByNumber":
            n = 15 if args[0] == "finalized" else int(args[0], 16)
            return {"number": hex(n), "hash": hex(n), "timestamp": hex(1000 + n)}
        assert method == "eth_getSystemTxsByBlockHash"
        return [deepcopy(tx)] if args[0] == "0xb" else []

    chain.call = call
    assert chain.system_transfer(chain.chain.usdc, "0x1234", 8, 1_010_000) == tx
    for key, value in (("input", "0x5678"), ("from", chain.account.address),
                       ("blockHash", "0xabc"), ("chainId", hex(999))):
        old, tx[key] = tx[key], value
        assert chain.system_transfer(chain.chain.usdc, "0x1234", 8, 1_010_000) is None
        tx[key] = old


def test_scan_pages_from_a_cursor_with_a_page_cap_and_reports_the_last_block_read():
    from factorylab.world.evm import LOG_PAGE_BLOCKS

    _, chain, _ = setup()
    pages = []

    def call(method, args):
        if method == "eth_chainId":
            return hex(998)
        if method == "eth_getBlockByNumber":
            return {"number": hex(1_117)}
        pages.append((int(args[0]["fromBlock"], 16), int(args[0]["toBlock"], 16)))
        return []

    chain.call = call
    assert LOG_PAGE_BLOCKS == 50
    assert chain.scan(chain.chain.usdc, [], 10, max_pages=2) == ([], 109)
    assert pages == [(10, 59), (60, 109)]
    del pages[:]
    assert chain.scan(chain.chain.usdc, [], 110, max_pages=2) == ([], 209)
    assert pages == [(110, 159), (160, 209)]
    del pages[:]
    # Without a cap the scan reaches the finalized head, exactly as logs() does.
    assert chain.scan(chain.chain.usdc, [], 1_100) == ([], 1_117)
    assert pages == [(1_100, 1_117)]
    del pages[:]
    # Nothing finalized past the cursor: no page is requested and the cursor holds.
    assert chain.scan(chain.chain.usdc, [], 1_118, max_pages=2) == ([], 1_117)
    assert pages == []
