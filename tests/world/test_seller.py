"""C11: the population sells a program's output over x402; every paid call is income."""

import base64
import json
import threading
from copy import deepcopy
from dataclasses import replace
from http.client import HTTPConnection

import pytest
from eth_account import Account

from factorylab.cortex.registration import ServiceProposal, parse_proposals
from factorylab.cortex.request import Return
from factorylab.cortex.sandbox import jail_available
from factorylab.cortex.tools import PopulationTool, ToolRunner
from factorylab.kernel.ledger import Ledger
from factorylab.kernel.queue import PropensityRecord
from factorylab.kernel.wallet import Wallet
from factorylab.runtime.loop import Runtime
from factorylab.runtime.worlds import load_manifest
from factorylab.world.exchange import FakeExchange
from factorylab.world.scripted import ScriptedProvider
from factorylab.world.seller import (
    IncomeSpool,
    Seller,
    Service,
    read_income_spool,
    seller_from_runtime,
    serve,
    services_from_items,
    settle_payment,
    spool_earn,
    verify_payment,
)
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


def items(rt, kind):
    return [i for i in rt.ledger._recovery_items() if i["kind"] == kind]


def post(port, path, body=None, headers=None):
    connection = HTTPConnection("127.0.0.1", port, timeout=30)
    connection.request("POST", path, body=json.dumps(body or {}), headers=headers or {})
    response = connection.getresponse()
    data = json.loads(response.read())
    result = (response.status, dict(response.getheaders()), data)
    connection.close()
    return result


# --- registration shape ---------------------------------------------------------

def test_service_proposal_names_a_registered_tool_at_a_bounded_price():
    def parse(item, tools=frozenset({"doubler"})):
        return parse_proposals({"register": [item]}, event_kinds=frozenset(),
                               known_models=frozenset(), known_assemblies=frozenset(),
                               known_tools=tools, tool_jail=True)

    accepted, rejected = parse(SERVICE)
    assert accepted == [ServiceProposal("doubler", 2500, "Doubling as a service")]
    assert not rejected
    for bad, reason in (
        ({**SERVICE, "program_id": "unknown"}, "registered population tool"),
        ({**SERVICE, "price_micro": 0}, "price_micro"),
        ({**SERVICE, "price_micro": "2500"}, "price_micro"),
        ({**SERVICE, "price_micro": 10**9}, "price_micro"),
        ({**SERVICE, "description": " "}, "description"),
        ({**SERVICE, "extra": 1}, "service fields"),
        ({k: v for k, v in SERVICE.items() if k != "description"}, "service fields"),
    ):
        accepted, rejected = parse(bad)
        assert not accepted and reason in rejected[0].reason


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


# --- the acceptance script -------------------------------------------------------

@pytest.mark.skipif(not jail_available(), reason="population jail unavailable")
def test_register_serve_pay_run_and_ledger_income():
    rt = runtime_with_reserve()
    register(rt, TOOL)
    assert "doubler" in rt.population_tools
    handle = register(rt, SERVICE)
    contract = rt.registry.get("service:doubler")
    assert contract.kind == "service" and contract.version == 1
    assert contract.provenance == handle and contract.price.units["call"] == 2500
    registered = items(rt, "service.registered")
    assert registered[-1]["code"] == TOOL_CODE and registered[-1]["price_micro"] == 2500
    assert registered[-1]["owner"] == "seed-decider" and registered[-1]["handle"] == handle
    # A second registration of the same program is its next version, never a rewrite.
    register(rt, {**SERVICE, "price_micro": 3000})
    assert rt.registry.get("service:doubler").version == 2

    transport = FakeHTTP([settlement()])
    seller = seller_from_runtime(rt, transport=transport, facilitator="https://facilitator.test")
    assert seller.pay_to == RESERVE and seller.catalogue()[0]["price_micro"] == 3000
    assert "code" not in json.dumps(seller.catalogue())
    server = serve(seller)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        status, headers, body = post(port, "/service/doubler", {"x": 3})
        assert status == 402
        quote = parse_quote(HTTPResponse(402, body, headers))
        assert quote.amount_micro == 3000 and quote.accepted["payTo"] == RESERVE
        assert parse_quote(HTTPResponse(402, {}, headers)).accepted == quote.accepted
        assert post(port, "/service/nothing", {"x": 3})[0] == 404

        header = payment_header(Account.from_key(TEST_KEY), quote)
        status, headers, body = post(port, "/service/doubler", {"x": 3},
                                     {"PAYMENT-SIGNATURE": header})
        assert (status, body) == (200, {"doubled": 6})
        receipt = json.loads(base64.b64decode(headers["PAYMENT-RESPONSE"]))
        assert receipt["success"] is True and receipt["transaction"] == "0x" + "ab" * 32
        assert len(transport.calls) == 1
        earned = items(rt, "income.earned")
        assert len(earned) == 1
        assert {k: earned[0][k] for k in ("service", "micro", "tx", "payer", "program",
                                          "version")} == {
            "service": "doubler", "micro": 3000, "tx": "0x" + "ab" * 32, "payer": PAYER,
            "program": "doubler", "version": 2}
        pots = rt.wallet.pots()
        assert pots["earned_micro"] == 3000 and pots["converted_from_principal_micro"] == 0
        assert pots["subsidy_micro"] == 0
        # The same authorization cannot buy a second run.
        status, _, body = post(port, "/service/doubler", {"x": 4},
                               {"PAYMENT-SIGNATURE": header})
        assert status == 402 and body["error"] == "Authorization already used"
        assert len(items(rt, "income.earned")) == 1 and len(transport.calls) == 1
        # A facilitator that does not settle earns nothing and runs nothing.
        transport.responses.append(HTTPResponse(200, {"success": False}))
        quote = parse_quote(HTTPResponse(402, post(port, "/service/doubler")[2], {}))
        status, _, body = post(port, "/service/doubler", {"x": 5},
                               {"PAYMENT-SIGNATURE": payment_header(
                                   Account.from_key(TEST_KEY), quote)})
        assert status == 402 and "did not settle" in body["error"]
        assert len(items(rt, "income.earned")) == 1
    finally:
        server.shutdown()
        server.server_close()
    assert rt.wallet.check_conservation()


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


def test_conversion_from_principal_is_counted_when_the_venice_tranche_confirms(tmp_path,
                                                                                 monkeypatch):
    treasury, wallet, records = treasury_setup(tmp_path, monkeypatch)
    assert treasury.transfer("to_reserve", "10", handle="a", now_ns=1)["status"] == "submitted"
    treasury.tick(2)
    assert wallet.pots()["converted_from_principal_micro"] == 0
    assert treasury.transfer("to_venice", "5", handle="b", now_ns=3)["status"] == "submitted"
    treasury.tick(4)
    pots = wallet.pots()
    assert pots["converted_from_principal_micro"] == 5_000_000
    assert pots["sellers"]["venice"] == 5_000_000 and pots["subsidy_micro"] == 0
    assert pots["earned_micro"] == 0


def test_catalogue_is_rebuilt_from_the_ledger_and_the_script_refuses_without_a_reserve(
        scripted_run, tmp_path):
    registered = {"kind": "service.registered", "id": "doubler", "version": 1,
                  "program_id": "doubler", "price_micro": 2500, "description": "Doubling",
                  "handle": "h", "owner": "seed-decider", "code": TOOL_CODE,
                  "args_schema": TOOL["args_schema"], "timeout_s": 2}
    services = services_from_items([
        {"kind": "other"}, registered, {**registered, "version": 2, "price_micro": 3000},
        {"kind": "service.registered", "id": "broken"},
    ])
    assert set(services) == {"doubler"} and services["doubler"].price_micro == 3000
    assert services["doubler"].tool.code == TOOL_CODE and services["doubler"].version == 2
    import deploy.serve as script

    world = scripted_run("scripted", 3, 1).copy_to(tmp_path / "world")
    assert script.main(["--ledger", str(world), "--spool", str(tmp_path / "s.jsonl")]) == 2
    assert script.main(["--ledger", str(tmp_path / "none.jsonl"),
                        "--spool", str(tmp_path / "s.jsonl")]) == 1
