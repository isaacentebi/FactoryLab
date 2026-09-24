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
        self.head = "0x10"
        self.on_send = None

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
            "eth_blockNumber": self.head,
        }.get(name)
        if name == "eth_sendRawTransaction":
            from eth_utils import keccak

            if self.on_send is not None:
                self.on_send()
            self.sent.append(args[0])
            result = "0x" + keccak(bytes.fromhex(args[0][2:])).hex()
        return HTTPResponse(200, {"result": result}, {})


def setup():
    rpc = RPC()
    chain = EVM(HYPEREVM_TESTNET, Account.create(), transport=rpc, gas_budget_wei=10**15)
    from factorylab.runtime.capital_loop import ReserveGuard

    chain.transaction_guard = ReserveGuard("test")  # written ahead, in the test's lock dir
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


# ---- Wave 10, the reviews of 0b5b487: plain reserve-key transactions on the record


def transactions(chain):
    from factorylab.runtime import capital_loop

    path = (capital_loop.default_lock_dir()
            / f"{chain.account.address.lower()}.authorizations.jsonl")
    if not path.exists():
        return []
    return [e for e in capital_loop.read_authorizations(path) if e["kind"] == "transaction"]


def test_a_reserve_transaction_is_written_ahead_before_it_is_returned():
    rpc, chain, ref = setup()
    [written] = transactions(chain)
    assert written == {
        "kind": "transaction", "tx_hash": ref["tx_hash"].lower(), "chain_id": 998,
        "from": chain.account.address, "to": ref["tx"]["to"], "tx_nonce": 3,
        "data": ref["tx"]["data"], "value": 0, "step": "transfer",
        "gas_price": 125, "start_block": 16, "origin": "test", "run_dir": None, "ledger": None}
    replaced = chain.replace(ref, gas_remaining_wei=10**15)
    assert [t["tx_hash"] for t in transactions(chain)] == [
        ref["tx_hash"].lower(), replaced["tx_hash"].lower()]


def test_no_reserve_transaction_is_prepared_or_sent_unrecorded():
    from factorylab.runtime.capital_loop import ReserveLock

    rpc, chain, ref = setup()
    to = Account.create().address
    guard, chain.transaction_guard = chain.transaction_guard, None
    for attempt in (lambda: chain.transfer(chain.chain.usdc, to, 1, 10**15),
                    lambda: chain.approve(chain.chain.usdc, to, 1, 10**15),
                    lambda: chain.replace(ref, gas_remaining_wei=10**15),
                    lambda: chain.broadcast(ref)):
        with pytest.raises(RailError, match="no write-ahead transaction record"):
            attempt()
    chain.transaction_guard = guard
    rpc.head = None  # the chain head cannot be read: nothing to record, nothing prepared
    with pytest.raises(RailError, match="record refused"):
        chain.transfer(chain.chain.usdc, to, 1, 10**15)
    rpc.head = "0x10"
    # A capital-loop run holds the reserve: nothing is prepared, replaced or broadcast.
    with ReserveLock(chain.account.address):
        for attempt in (lambda: chain.transfer(chain.chain.usdc, to, 1, 10**15),
                        lambda: chain.replace(ref, gas_remaining_wei=10**15)):
            with pytest.raises(RailError, match="capital_loop_reserve_locked"):
                attempt()
        with pytest.raises(RailError, match="capital_loop_reserve_locked"):
            chain.broadcast(ref)
    assert rpc.sent == [] and len(transactions(chain)) == 1  # only setup's own
    chain.broadcast(ref)
    assert len(rpc.sent) == 1


def test_a_live_rail_binds_its_guard_to_every_chain_it_signs_on():
    from factorylab.world.treasury_rails import LiveRail

    rail = LiveRail.__new__(LiveRail)
    rail.hyper, rail.base = SimpleNamespace(), SimpleNamespace()
    guard = object()
    rail.bind_guard(guard)
    assert rail.authorization_log is guard
    assert rail.hyper.transaction_guard is guard and rail.base.transaction_guard is guard


# ---- Wave 10, the reviews of 98fa627


def test_a_transaction_whose_hash_is_not_on_the_record_is_never_broadcast(tmp_path):
    from factorylab.runtime.capital_loop import ReserveGuard

    rpc, chain, ref = setup()
    chain.transaction_guard = ReserveGuard("test", lock_dir=tmp_path / "elsewhere")
    with pytest.raises(RailError, match="transaction_not_on_record"):
        chain.broadcast(ref)
    assert rpc.sent == []


def test_the_reserve_is_held_from_the_record_check_until_the_send_returns():
    # Codex P1: a capital-loop launch between the lock's release and the send could
    # admit a run beside a transaction just leaving the reserve.
    from factorylab.runtime.capital_loop import CapitalLoopRefused, ReserveLock

    rpc, chain, ref = setup()
    seen = []

    def a_launch_during_the_send():
        try:
            ReserveLock(chain.account.address).close()
            seen.append("launched")
        except CapitalLoopRefused as exc:
            seen.append(exc.reason)

    rpc.on_send = a_launch_during_the_send
    chain.broadcast(ref)
    assert seen == ["capital_loop_reserve_locked"] and len(rpc.sent) == 1
    ReserveLock(chain.account.address).close()  # released once the send returned


def test_a_transaction_whose_line_was_damaged_and_repaired_is_still_sendable():
    # The fourth review: after --repair-damaged, a torn transaction's legible hash is the
    # record's, and the world that journaled it may still send it.
    from factorylab.runtime import capital_loop

    rpc, chain, ref = setup()
    chain.transfer(chain.chain.usdc, Account.create().address, 1, 10**15)  # a later line
    path = capital_loop.default_lock_dir() / f"{chain.account.address.lower()}.authorizations.jsonl"
    first, rest = path.read_bytes().split(b"\n", 1)
    assert ref["tx_hash"].lower().encode() in first
    path.write_bytes(first[:first.index(b'"tx_nonce"')] + b"\xff\n" + rest)
    from factorylab.world.evm import BASE, HYPEREVM

    def mainnets(method, url, body, headers):
        # A damaged line's chain is never trusted: both mainnet chains are bounded.
        chain_id = {BASE.rpc: 8453, HYPEREVM.rpc: 999}[url]
        result = {"eth_chainId": hex(chain_id), "eth_getTransactionCount": "0x3"}
        return HTTPResponse(200, {"result": result[body["method"]]}, {})

    with capital_loop.ReserveLock(chain.account.address) as lock:
        repaired = capital_loop.repair_damaged(lock, transport=mainnets)
        assert repaired["open_transactions"] == [ref["tx_hash"].lower()]
    chain.broadcast(ref)
    assert len(rpc.sent) == 1
