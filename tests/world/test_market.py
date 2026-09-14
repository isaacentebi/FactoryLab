import base64
import json
import traceback
from dataclasses import replace
from fractions import Fraction
from urllib import parse, request

import pytest

from factorylab.kernel.ledger import Ledger
from factorylab.kernel.wallet import Wallet
from factorylab.world.market import (
    MultiProvider,
    PaymentOutcomeUnknown,
    X402MeteredModel,
    X402Provider,
    discover,
    seller_models,
    split_model_id,
)
from factorylab.world.metering import BillingUncertain, Meter, MeteredModel
from factorylab.world.models import ModelRequest, PriceTable, TokenPrice
from factorylab.world.x402 import (
    BASE_NETWORK,
    BASE_USDC,
    HTTPResponse,
    InsufficientReserve,
    X402Error,
)

# Public deterministic fixture only. No test reads any reserve key file.
TEST_KEY = "0x" + "11" * 32
MODEL = "x402:https://seller.test#model/flash"


def encoded(value):
    return base64.b64encode(json.dumps(value).encode()).decode()


@pytest.fixture(autouse=True)
def no_network_or_credentials(monkeypatch):
    monkeypatch.delenv("RESERVE_PRIVATE_KEY", raising=False)

    def deny(*args, **kwargs):
        pytest.fail("unexpected network request")

    monkeypatch.setattr(request.OpenerDirector, "open", deny)


class SellerHTTP:
    def __init__(self, *, amount=1734, balance=10_000, catalogue_status=404):
        self.calls = []
        self.balance = balance
        self.quote = {
            "x402Version": 2,
            "accepts": [{
                "scheme": "exact", "network": BASE_NETWORK, "asset": BASE_USDC,
                "amount": str(amount), "payTo": "0x" + "22" * 20,
                "maxTimeoutSeconds": 60, "extra": {"name": "USD Coin", "version": "2"},
            }],
        }
        self.catalogue = HTTPResponse(catalogue_status)
        self.completion = {
            "id": "response-1", "model": "model/flash",
            "choices": [{"message": {"content": '{"action":"hold"}'}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 12, "completion_tokens": 3, "cost": "999"},
            "pricing": {"prompt": "0.000001", "completion": "0.000005"},
        }
        self.paid_response = None

    def __call__(self, method, url, payload, headers):
        self.calls.append((method, url, payload, headers))
        if url.endswith("/v1/models"):
            assert method == "GET" and headers == {}
            return self.catalogue
        if payload.get("method") == "eth_call":
            return HTTPResponse(200, {"id": 1, "result": hex(self.balance)})
        assert (method, url) == ("POST", "https://seller.test/v1/chat/completions")
        if "PAYMENT-SIGNATURE" not in headers:
            assert headers == {}
            return HTTPResponse(402, self.quote)
        if isinstance(self.paid_response, Exception):
            raise self.paid_response
        if self.paid_response is not None:
            return self.paid_response
        return HTTPResponse(200, self.completion, {"PAYMENT-RESPONSE": encoded({
            "success": True, "network": BASE_NETWORK, "transaction": "0xsettlement",
        })})

    @property
    def payments(self):
        return [c for c in self.calls if "PAYMENT-SIGNATURE" in c[3]]


def provider(fake, ceiling=2000):
    p = X402Provider(private_key=TEST_KEY, transport=fake)
    p.register(MODEL, ceiling)
    return p


def test_402_loop_one_payment_and_metering_equals_quote_not_usage():
    fake = SellerHTTP()
    ledger = Ledger()
    wallet = Wallet(10_000, ledger)
    model = MeteredModel(provider(fake), PriceTable({MODEL: TokenPrice(0, 0, 2000)}), Meter(wallet))
    result = model.complete(ModelRequest(MODEL, "system", (), 16), handle="decision-1")
    assert result.cost == result.result.cost_micro == 1734
    assert wallet.balance == 8266 and wallet.available == 8266
    assert result.result.raw["cost_source"] == "x402-quote"
    assert result.result.raw["usage"]["cost"] == "999"
    assert result.result.raw["pricing"]["prompt"] == "0.000001"
    assert result.result.raw["settlement"]["transaction"] == "0xsettlement"
    assert (result.result.input_tokens, result.result.output_tokens) == (12, 3)
    assert len(fake.calls) == 3 and len(fake.payments) == 1
    assert fake.calls[0][:3] == fake.payments[0][:3]
    payment = json.loads(base64.b64decode(fake.payments[0][3]["PAYMENT-SIGNATURE"]))
    assert payment["payload"]["authorization"]["value"] == "1734"
    assert payment["accepted"] == fake.quote["accepts"][0]


def test_ceiling_refusal_precedes_key_loading_balance_read_and_signing(monkeypatch):
    fake = SellerHTTP(amount=2001)
    p = X402Provider(transport=fake)  # no key supplied or loaded
    p.register(MODEL, 2000)
    monkeypatch.setattr(p, "_client", lambda: pytest.fail("must not reach signer"))
    with pytest.raises(X402Error, match="ceiling"):
        p.complete(ModelRequest(MODEL, "", ()))
    assert len(fake.calls) == 1 and not fake.payments


@pytest.mark.parametrize("field,value", [
    ("network", "eip155:1"), ("network", "solana"),
    ("asset", "0x" + "33" * 20), ("scheme", "upto"),
])
def test_wrong_payment_rail_cannot_load_key_or_sign(field, value):
    fake = SellerHTTP()
    fake.quote["accepts"][0][field] = value
    p = X402Provider(transport=fake)
    p.register(MODEL, 2000)
    with pytest.raises(X402Error, match="exact eip155:8453 canonical Base USDC"):
        p.complete(ModelRequest(MODEL, "", ()))
    assert len(fake.calls) == 1 and not fake.payments


def test_insufficient_on_chain_reserve_never_signs():
    fake = SellerHTTP(balance=1733)
    with pytest.raises(InsufficientReserve):
        provider(fake).complete(ModelRequest(MODEL, "", ()))
    assert len(fake.calls) == 2 and not fake.payments


@pytest.mark.parametrize("response", [
    HTTPResponse(402),
    HTTPResponse(200, {}, {"PAYMENT-RESPONSE": encoded({"success": False})}),
])
def test_explicit_payment_rejection_does_not_retry(response):
    fake = SellerHTTP()
    fake.paid_response = response
    with pytest.raises(X402Error):
        provider(fake).complete(ModelRequest(MODEL, "", ()))
    assert len(fake.payments) == 1


def test_uncertain_submission_books_provisionally_and_suppresses_transport_secrets():
    fake = SellerHTTP()
    fake.paid_response = TimeoutError(TEST_KEY)
    ledger = Ledger()
    wallet = Wallet(10_000, ledger)
    events = []
    model = X402MeteredModel(
        provider(fake), PriceTable({MODEL: TokenPrice(0, 0, 2000)}), Meter(wallet),
        record=events.append, on_unaffordable=lambda h: pytest.fail("uncertain, not insolvent"),
    )
    with pytest.raises(BillingUncertain) as error:
        model.complete(ModelRequest(MODEL, "", ()), handle="decision-1")
    assert wallet.balance == wallet.available == 8266
    assert wallet.state()["reservations"] == []
    assert events[-1] == {"kind": "x402.unresolved", "handle": "decision-1",
                          "reserved_micro": 1734, "reservation_id": "wallet-0",
                          "reserve_before_micro": 10_000}
    assert len(fake.payments) == 1
    assert TEST_KEY[2:] not in "".join(traceback.format_exception(error.value))


def test_malformed_paid_response_is_still_debited_and_receipt_is_recorded():
    fake = SellerHTTP()
    fake.completion = {"usage": {"cost": "0"}, "pricing": {"input": "0"}}
    events = []
    wallet = Wallet(10_000, Ledger())
    model = X402MeteredModel(
        provider(fake), PriceTable({MODEL: TokenPrice(0, 0, 2000)}), Meter(wallet),
        record=events.append, on_unaffordable=lambda _: None,
    )
    result = model.complete(ModelRequest(MODEL, "", ()), handle="paid-garbage")
    assert result.result.text == "" and result.result.stop_reason == "malformed"
    assert result.cost == 1734 and result.cost_source == "x402-quote"
    assert result.reserved == 1734  # the current quote, even when the registered ceiling is higher
    assert wallet.balance == 8266
    assert events[-1]["settlement"]["transaction"] == "0xsettlement"
    assert all(e["handle"] == "paid-garbage" for e in events)


def test_public_registration_probe_costs_nothing_and_catalogue_price_avoids_probe():
    fake = SellerHTTP()
    p = X402Provider(transport=fake)
    price, info = p.registration_price(MODEL)
    assert price == TokenPrice(0, 0, 1734) and info["price_source"] == "x402-quote"
    assert len(fake.calls) == 2 and not fake.payments
    fake.calls.clear()
    fake.catalogue = HTTPResponse(200, {"data": [{
        "id": "model/flash", "context_length": 8192,
        "pricing": {"prompt": "0.00000015", "completion": "0.0000005"},
    }]})
    price, info = p.registration_price(MODEL)
    assert price == TokenPrice(0, 0, 3277)
    assert info["price_source"] == "seller-models"
    assert len(fake.calls) == 1 and not fake.payments


def test_seller_models_retains_unpriced_ids_and_exact_fractional_prices():
    def fake(method, url, payload, headers):
        assert (method, url, payload, headers) == (
            "GET", "https://seller.test/v1/models", None, {},
        )
        return HTTPResponse(200, {"data": [
            {"id": "free-of-price-metadata"},
            {"id": "priced", "pricing": {"input": "0.00000015", "output": "0.0000005"}},
        ]})

    a, b = seller_models("https://seller.test/v1/chat/completions", transport=fake)
    assert a.pricing is None and a.id == "free-of-price-metadata"
    assert b.pricing.price() == TokenPrice(Fraction(3, 20), Fraction(1, 2))


@pytest.mark.parametrize("price", ["-1", "NaN", "Infinity", True])
def test_catalogue_refuses_invalid_prices(price):
    fake = SellerHTTP()
    fake.catalogue = HTTPResponse(200, {"data": [{
        "id": "model/flash", "pricing": {"prompt": price, "completion": "0"},
    }]})
    with pytest.raises(X402Error, match="price"):
        seller_models("https://seller.test", transport=fake)


def resource(url, description="", network=BASE_NETWORK):
    return {"resource": url, "description": description, "accepts": [{
        "scheme": "exact", "network": network, "asset": BASE_USDC, "amount": "42",
    }]}


def test_discovery_filters_across_pages_and_preserves_network_asset_base_unit_price():
    calls = []
    pages = [
        [resource("https://weather.test", "forecast"), resource("https://other.test", "search")],
        [resource("https://seller.test/chat", "FAST inference"),
         resource("https://seller.test/chat2", "slow inference", "solana")],
    ]

    def fake(method, url, payload, headers):
        assert method == "GET" and payload is None and headers == {}
        calls.append(parse.parse_qs(parse.urlsplit(url).query))
        offset = int(calls[-1]["offset"][0])
        return HTTPResponse(200, {"items": pages[offset // 2],
                                 "pagination": {"offset": offset, "limit": 2, "total": 4}})

    result = discover("chat", "INFERENCE", limit=2, transport=fake)
    assert [r["resource"] for r in result] == [
        "https://seller.test/chat", "https://seller.test/chat2",
    ]
    assert [r["accepts"][0]["network"] for r in result] == [BASE_NETWORK, "solana"]
    assert result[0]["accepts"][0]["price"] == "42"
    assert result[0]["accepts"][0]["asset"] == BASE_USDC
    assert [c["offset"] for c in calls] == [["0"], ["2"]]


def test_discovery_rejects_invalid_limit_without_http_and_bad_pagination():
    for limit in (True, 0, -1, 101, "20"):
        with pytest.raises(X402Error, match="limit"):
            discover(limit=limit)
    with pytest.raises(X402Error, match="pagination"):
        discover("absent", transport=lambda *a: HTTPResponse(200, {
            "items": [resource("https://x.test")], "pagination": {"limit": 0},
        }))


def test_namespace_routing_and_balances_are_independent():
    calls = []

    class Stub:
        def __init__(self, name, balance):
            self.name, self.balance = name, balance

        def complete(self, req):
            calls.append((self.name, req.model_id))
            return self.name

        def balance_micro(self):
            return self.balance

    fake = SellerHTTP(balance=1000)
    multi = MultiProvider(Stub("openrouter", 0), Stub("venice", 5000), provider(fake))
    multi.complete(ModelRequest("vendor/model", "", ()))
    multi.complete(ModelRequest("venice:model", "", ()))
    assert calls == [("openrouter", "vendor/model"), ("venice", "venice:model")]
    assert not multi.affordable("vendor/model", 1)[0]
    assert multi.affordable("venice:model", 1)[0]
    assert not multi.affordable(MODEL, 1734)[0]
    fake.balance = 10_000
    assert multi.complete(ModelRequest(MODEL, "", ())).cost_micro == 1734
    assert len(fake.payments) == 1


@pytest.mark.parametrize("model", [
    "x402:https://seller.test", "x402:https://seller.test#", "x402:file:///test#m",
    "x402:https://user:secret@seller.test#m", "x402:https://seller.test?key=secret#m",
])
def test_invalid_namespace_cannot_address_a_seller(model):
    with pytest.raises(X402Error):
        split_model_id(model)


def test_completion_cannot_echo_reserve_key():
    fake = SellerHTTP()
    fake.completion["choices"][0]["message"]["content"] = TEST_KEY
    fake.completion["id"] = TEST_KEY[2:]
    assert TEST_KEY[2:] not in repr(provider(fake).complete(ModelRequest(MODEL, "", ())))


def test_market_cli_discovery_never_loads_key_files(monkeypatch, capsys):
    from factorylab.runtime import cli

    monkeypatch.setattr(cli, "_load_dotenv", lambda: pytest.fail("must not load key files"))
    monkeypatch.setattr("factorylab.world.market.http_request", lambda *a: HTTPResponse(200, {
        "items": [resource("https://seller.test/chat")],
        "pagination": {"offset": 0, "limit": 100, "total": 1},
    }))
    assert cli.main(["market", "discover", "--url-substring", "chat"]) == 0
    assert json.loads(capsys.readouterr().out)[0]["resource"] == "https://seller.test/chat"


def test_x402_cli_probe_pays_once_and_prints_cost_quote_and_reference(monkeypatch, capsys):
    from factorylab.runtime import cli

    fake = SellerHTTP()
    monkeypatch.setenv("RESERVE_PRIVATE_KEY", TEST_KEY)
    monkeypatch.setattr(cli, "_load_dotenv", lambda: None)
    monkeypatch.setattr("factorylab.world.market.http_request", fake)
    assert cli.main(["probe", "--provider", "x402", "--seller", "https://seller.test",
                     "--model", "model/flash"]) == 0
    output = capsys.readouterr().out
    result = json.loads(output)
    assert result["cost_micro"] == 1734 and result["cost_source"] == "x402-quote"
    assert result["quote"]["accepted"]["amount"] == "1734"
    assert result["settlement_reference"] == "0xsettlement"
    assert len(fake.payments) == 1 and TEST_KEY[2:] not in output


def test_x402_cli_probe_cap_refuses_before_reserve_read(monkeypatch, capsys):
    from factorylab.runtime import cli

    fake = SellerHTTP(amount=100_001)
    monkeypatch.setattr(cli, "_load_dotenv", lambda: None)
    monkeypatch.setattr("factorylab.world.market.http_request", fake)
    assert cli.main(["probe", "--provider", "x402", "--seller", "https://seller.test",
                     "--model", "model/flash"]) == 2
    assert capsys.readouterr().err == "factorylab probe: quote_above_cap\n"
    assert len(fake.calls) == 1 and not fake.payments


def test_live_builder_includes_market_and_retains_configured_index(monkeypatch):
    from factorylab.runtime.live import build_provider
    from factorylab.runtime.worlds import load_manifest

    manifest = load_manifest("testnet")
    manifest = replace(manifest, treasury=replace(
        manifest.treasury, discovery_url="https://index.test/resources",
    ))
    monkeypatch.setenv("OPENROUTER_API_KEY", "public-test-token")
    result = build_provider(manifest)
    assert isinstance(result, MultiProvider)
    assert result.x402.discovery_url == "https://index.test/resources"


@pytest.mark.parametrize("receipt", [
    {"success": True, "network": "eip155:1"},
    {"success": True, "payer": "0x" + "33" * 20},
    {"transaction": "0xunconfirmed"},
])
def test_untrusted_settlement_receipt_cannot_claim_success(receipt):
    fake = SellerHTTP()
    fake.paid_response = HTTPResponse(200, fake.completion, {
        "PAYMENT-RESPONSE": encoded(receipt),
    })
    with pytest.raises(PaymentOutcomeUnknown):
        provider(fake).complete(ModelRequest(MODEL, "", ()))
    assert len(fake.payments) == 1


def test_json_output_contract_is_sent_only_for_object_requests():
    plain = X402Provider(transport=SellerHTTP())._payload(ModelRequest(MODEL, "system", (), 16))[1]
    asked = X402Provider(transport=SellerHTTP())._payload(
        ModelRequest(MODEL, "system", (), 16, json_object=True))[1]
    assert "response_format" not in plain
    assert asked["response_format"] == {"type": "json_object"}


def test_a_paid_json_request_carries_the_contract_on_the_wire():
    fake = SellerHTTP()
    provider(fake).complete(ModelRequest(MODEL, "system", (), 16, json_object=True))
    bodies = [call[2] for call in fake.calls
              if call[1].endswith("/v1/chat/completions")]
    assert len(bodies) == 2  # the unpaid quote and the paid call
    assert all(body["response_format"] == {"type": "json_object"} for body in bodies)
