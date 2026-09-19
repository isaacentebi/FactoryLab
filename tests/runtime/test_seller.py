"""C11: the population sells a program's output over x402; every paid call is income."""

import base64
import json
from copy import deepcopy
from dataclasses import replace

import pytest
from eth_account import Account

from factorylab.cortex.request import Return
from factorylab.cortex.sandbox import jail_available
from factorylab.cortex.tools import PopulationTool, ToolRunner
from factorylab.kernel.ledger import Ledger
from factorylab.kernel.queue import PropensityRecord
from factorylab.kernel.wallet import Wallet
from factorylab.runtime.loop import Runtime
from factorylab.runtime.seller import (
    IncomeSpool,
    Seller,
    Service,
    read_income_spool,
    settle_payment,
    spool_earn,
    verify_payment,
)
from factorylab.runtime.worlds import load_manifest
from factorylab.world.exchange import FakeExchange
from factorylab.world.scripted import ScriptedProvider
from factorylab.world.treasury import FakeTreasury
from factorylab.world.x402 import (
    BASE_NETWORK,
    HTTPResponse,
    X402Error,
    parse_quote,
    payment_header,
)
from tests.world.test_x402 import TEST_KEY, FakeHTTP

# Public fixtures only: the buyer's test key and a reserve address that is never a key.
RESERVE = "0x2670b922ef37c7df47158725c0cc407b5382293f"
PAYER = Account.from_key(TEST_KEY).address
TOOL_CODE = ('import json, sys\nargs = json.load(sys.stdin)\n'
             'print(json.dumps({"doubled": args["x"] * 2}))\n')
TOOL = {"kind": "tool", "id": "doubler", "description": "Doubles x",
        "args_schema": {"type": "object", "properties": {"x": {"type": "number"}}},
        "code": TOOL_CODE, "timeout_s": 2}
SERVICE = {"kind": "service", "program_id": "doubler", "price_micro": 2500,
           "description": "Doubling as a service"}


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def deny(*args, **kwargs):
        pytest.fail("real network is forbidden")
    monkeypatch.setattr("urllib.request.OpenerDirector.open", deny)


def settlement(tx="0x" + "ab" * 32):
    return HTTPResponse(200, {"success": True, "transaction": tx, "network": BASE_NETWORK,
                             "payer": PAYER})


def runtime_with_reserve():
    manifest = load_manifest("scripted")
    manifest = replace(manifest, treasury=replace(manifest.treasury, reserve_address=RESERVE))
    return Runtime(manifest, events=0, seed=1, initial_balance_micro=100_000_000,
                   ledger_path=None, drip=False, router_gamma=.1, exchange=FakeExchange(),
                   provider=ScriptedProvider())


def decision(rt, owner="seed-decider"):
    handle = rt.queue.open(
        actor=owner, event_id="seller-test", propensity=PropensityRecord(
            (owner,), (1.,), owner, 0, owner, "test"), channel="verdict",
        deadline_ns=10**15, parent_handle=None, cost_ceiling=10_000_000,
    )
    rt.handle_to_assembly[handle] = owner
    return handle


def register(rt, *proposals):
    rt._manage_reserve_window()
    handle = decision(rt)
    rt._apply_registrations(handle, Return(handle, {"register": list(proposals)}, 0, "ok"))
    return handle


# --- verification reuses the buyer's typed data ----------------------------------

def paid_header(service, pay_to=RESERVE):
    body = {"x402Version": 2, "accepts": [{
        "scheme": "exact", "network": BASE_NETWORK, "amount": str(service.price_micro),
        "asset": "0x833589fcd6edb6e08f4c7c32d4f71b54bda02913", "payTo": pay_to,
        "maxTimeoutSeconds": 300, "extra": {"name": "USD Coin", "version": "2"}}]}
    quote = parse_quote(HTTPResponse(402, body, {}))
    return payment_header(Account.from_key(TEST_KEY), quote)


def fixture_service(price=2500):
    tool = PopulationTool("doubler", "Doubles x", TOOL["args_schema"], TOOL_CODE, 2, "test")
    return Service("doubler", "doubler", price, "Doubling as a service", 1, tool)


def test_verify_payment_accepts_the_buyers_own_signature_and_refuses_every_deviation():
    service = fixture_service()
    header = paid_header(service)
    verified = verify_payment(header, pay_to=RESERVE, amount_micro=2500, now=1)
    assert verified["payer"] == PAYER and verified["amount_micro"] == 2500
    with pytest.raises(X402Error, match="requested micro-USD amount"):
        verify_payment(header, pay_to=RESERVE, amount_micro=2600, now=1)
    with pytest.raises(X402Error, match="addressed to the reserve"):
        verify_payment(header, pay_to="0x" + "12" * 20, amount_micro=2500, now=1)
    with pytest.raises(X402Error, match="not valid at this time"):
        verify_payment(header, pay_to=RESERVE, amount_micro=2500, now=2**40)
    envelope = json.loads(base64.b64decode(header))
    signature = envelope["payload"]["signature"]
    flipped = signature[:10] + ("00" if signature[10:12] != "00" else "01") + signature[12:]
    tampered = deepcopy(envelope)
    tampered["payload"]["signature"] = flipped
    with pytest.raises(X402Error, match="not signed by its payer|cannot be recovered"):
        verify_payment(base64.b64encode(json.dumps(tampered).encode()).decode(),
                       pay_to=RESERVE, amount_micro=2500, now=1)
    tampered = deepcopy(envelope)
    tampered["payload"]["authorization"]["value"] = "2400"
    with pytest.raises(X402Error, match="quoted amount"):
        verify_payment(base64.b64encode(json.dumps(tampered).encode()).decode(),
                       pay_to=RESERVE, amount_micro=2500, now=1)
    tampered = deepcopy(envelope)
    tampered["accepted"]["network"] = "eip155:1"
    with pytest.raises(X402Error, match="exact, eip155:8453"):
        verify_payment(base64.b64encode(json.dumps(tampered).encode()).decode(),
                       pay_to=RESERVE, amount_micro=2500, now=1)


def test_settlement_requires_an_explicit_matching_success():
    verified = verify_payment(paid_header(fixture_service()), pay_to=RESERVE,
                              amount_micro=2500, now=1)
    transport = FakeHTTP([settlement()])
    result = settle_payment(verified, facilitator="https://facilitator.test", transport=transport)
    assert result == {"success": True, "transaction": "0x" + "ab" * 32,
                      "network": BASE_NETWORK, "payer": PAYER}
    method, url, payload, _ = transport.calls[0]
    assert (method, url) == ("POST", "https://facilitator.test/settle")
    assert payload["paymentRequirements"]["payTo"] == RESERVE and "paymentPayload" in payload
    for response, reason in (
        (HTTPResponse(500, {}), "HTTP 500"),
        (HTTPResponse(200, {"success": False, "transaction": "0x1"}), "did not settle"),
        (HTTPResponse(200, {"transaction": "0x1"}), "did not settle"),
        (HTTPResponse(200, {"success": True, "transaction": "0x1", "network": "eip155:1"}),
         "network"),
        (HTTPResponse(200, {"success": True, "transaction": "0x1", "payer": "0x" + "12" * 20}),
         "payer"),
        (HTTPResponse(200, {"success": True}), "transaction reference"),
        (TimeoutError(), "outcome unknown"),
    ):
        with pytest.raises(X402Error, match=reason):
            settle_payment(verified, facilitator="https://facilitator.test",
                           transport=FakeHTTP([response]))


# --- the receipt spool the wake host writes and the runtime books ------------------

def treasury_setup(tmp_path, monkeypatch):
    monkeypatch.delenv("FACTORYLAB_INCOME_SPOOL", raising=False)
    ledger = Ledger(clock_ns=lambda: 0)
    records = []
    append = ledger.append

    def record(item):
        records.append(deepcopy(item))
        return append(item)

    ledger.append = record
    wallet = Wallet(100_000_000, ledger, clock_ns=lambda: 0)
    treasury = FakeTreasury(ledger, wallet)
    wallet.bind_pots(treasury.pots)
    treasury.income_spool = tmp_path / "income.jsonl"
    return treasury, wallet, records


@pytest.mark.skipif(not jail_available(), reason="population jail unavailable")
def test_spool_receipts_are_booked_once_on_tick_and_survive_snapshots(tmp_path, monkeypatch):
    treasury, wallet, records = treasury_setup(tmp_path, monkeypatch)
    assert wallet.pots()["earned_micro"] == 0 and wallet.pots()["subsidy_micro"] == 0
    assert [r for r in records if r["kind"] == "treasury.subsidy"][0]["micro"] == 0
    spool = IncomeSpool(treasury.income_spool)
    seller = Seller({"doubler": fixture_service()}, pay_to=RESERVE,
                    runner=ToolRunner(), earn=spool_earn(spool),
                    transport=FakeHTTP([settlement()]),
                    facilitator="https://facilitator.test", clock_ns=lambda: 7)
    status, _, output = seller.handle("doubler", b'{"x": 2}',
                                      {"X-PAYMENT": paid_header(fixture_service())},
                                      "http://localhost/service/doubler")
    assert (status, output) == (200, {"doubled": 4})
    with treasury.income_spool.open("a") as stream:
        stream.write('not json\n{"service": "x", "micro": -1, "tx": "t"}\n{"service": "late"')
    assert treasury.tick(1) == []  # no transfer in flight; the receipts are booked below
    earned = [r for r in records if r["kind"] == "income.earned"]
    assert len(earned) == 1 and earned[0]["tx"] == "0x" + "ab" * 32
    assert earned[0]["service"] == "doubler" and earned[0]["served_ns"] == 7
    assert wallet.pots()["earned_micro"] == 2500
    assert treasury.collect_income() == [] and wallet.pots()["earned_micro"] == 2500
    assert len([r for r in records if r["kind"] == "income.earned"]) == 1
    # The offset is durable: a restored treasury does not book the receipt again.
    snapshot = treasury.snapshot()
    assert snapshot["income"]["earned_micro"] == 2500 and snapshot["income"]["spool_offset"] > 0
    restored = FakeTreasury(Ledger(clock_ns=lambda: 0), wallet.__class__(
        100_000_000, Ledger(clock_ns=lambda: 0), clock_ns=lambda: 0))
    restored.income_spool = treasury.income_spool
    restored.restore(snapshot)
    assert restored.income == treasury.income and restored.tick(3) == []
    # Older checkpoints carry no income classes and restore to zero, not to nothing.
    restored.restore({k: v for k, v in snapshot.items() if k != "income"})
    assert restored.income["earned_micro"] == 0 and restored.income["subsidy_micro"] is None
    assert wallet.check_conservation()


def test_spool_reader_reads_only_complete_lines_and_never_rereads(tmp_path):
    path = tmp_path / "income.jsonl"
    assert read_income_spool(str(path), 0) == {"offset": 0, "receipts": []}
    spool = IncomeSpool(path)
    spool.append({"service": "a", "micro": 1, "tx": "t1"})
    first = read_income_spool(str(path), 0)
    assert [r["service"] for r in first["receipts"]] == ["a"]
    spool.append({"service": "b", "micro": 2, "tx": "t2", "payer": PAYER})
    second = read_income_spool(str(path), first["offset"])
    assert [r["service"] for r in second["receipts"]] == ["b"] and second["receipts"][0][
        "payer"] == PAYER
    path.write_text("")  # a replaced spool is not re-read from the start
    assert read_income_spool(str(path), second["offset"]) == {"offset": second["offset"],
                                                              "receipts": []}
    with pytest.raises(ValueError):
        spool.append({"service": "c", "micro": 0, "tx": "t3"})
