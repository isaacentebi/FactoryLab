import base64
import json
import traceback
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime
from io import BytesIO
from urllib import error, request

import pytest
from eth_account import Account
from eth_account.messages import encode_defunct, encode_typed_data
from eth_utils import keccak  # Already a dependency of eth_account; hash primitive only.

from factorylab.world.x402 import (
    BASE_NETWORK,
    BASE_USDC,
    HTTPResponse,
    X402Client,
    X402Error,
    authorization_typed_data,
    eth_balance,
    http_request,
    parse_quote,
    payment_header,
    reserve_address,
    siwe_header,
    usdc_balance,
    venice_balance,
)

# Every signature here goes through the production chokepoint, with a real
# ReserveGuard in this test's temporary lock directory (tests/conftest.py).
pytestmark = pytest.mark.usefixtures("write_ahead")


# Public test fixture only. Never loaded from a real key file or environment.
TEST_KEY = "0x" + "11" * 32


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def deny(*args, **kwargs):
        pytest.fail("unexpected real HTTP request")

    monkeypatch.setattr(request.OpenerDirector, "open", deny)


@pytest.fixture
def quote():
    # Shape captured in docs/research/venice.md, with optional v2 metadata exercised.
    return {
        "x402Version": 2,
        "resource": {"url": "https://fake.test/api/v1/x402/top-up"},
        "extensions": {"test-extension": {"info": {"test": True}}},
        "accepts": [
            {
                "scheme": "exact",
                "network": BASE_NETWORK,
                "amount": "5000000",
                "asset": BASE_USDC,
                "payTo": "0x2670b922ef37c7df47158725c0cc407b5382293f",
                "maxTimeoutSeconds": 300,
                "extra": {"name": "USD Coin", "version": "2"},
            }
        ],
    }


def encoded(value):
    return base64.b64encode(json.dumps(value).encode()).decode()


def decoded(value):
    return json.loads(base64.b64decode(value, validate=True))


@dataclass
class FakeHTTP:
    responses: list
    calls: list = field(default_factory=list)

    def __call__(self, method, url, payload, headers):
        self.calls.append((method, url, payload, headers))
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


@pytest.mark.parametrize("header", [False, True])
def test_quote_body_or_case_insensitive_base64_header(quote, header):
    quote["accepts"].insert(0, {"scheme": "exact", "network": "solana"})
    response = HTTPResponse(
        402, {} if header else quote, {"PaYmEnT-ReQuIrEd": encoded(quote)} if header else {}
    )
    parsed = parse_quote(response)
    assert parsed.accepted == quote["accepts"][1]
    assert parsed.resource == quote["resource"]
    assert parsed.extensions == quote["extensions"]


@pytest.mark.parametrize(
    "field_name,value",
    [
        ("scheme", "upto"),
        ("network", "eip155:1"),
        ("asset", "0x" + "12" * 20),
        ("amount", "-1"),
        ("amount", 5000000),
        ("amount", str(2**256)),
        ("payTo", "0x" + "00" * 20),
        ("payTo", "not-an-address"),
        ("maxTimeoutSeconds", 0),
        ("maxTimeoutSeconds", -1),
        ("maxTimeoutSeconds", True),
        ("extra", {"name": "USDC", "version": "2"}),
        ("extra", {"name": "USD Coin", "version": "1"}),
        ("extra", {"assetTransferMethod": "permit2"}),
    ],
)
def test_quote_rejects_unapproved_authorization(quote, field_name, value):
    quote["accepts"][0][field_name] = value
    with pytest.raises(X402Error):
        parse_quote(HTTPResponse(402, quote))
    with pytest.raises(X402Error):
        authorization_typed_data(quote["accepts"][0], Account.from_key(TEST_KEY).address)


@pytest.mark.parametrize(
    "body,headers,status",
    [
        ({"x402Version": 1, "accepts": []}, {}, 402),
        ({"x402Version": 2, "accepts": {}}, {}, 402),
        ({}, {"payment-required": "not-base64"}, 402),
        ({}, {"payment-required": encoded([])}, 402),
        ({}, {}, 200),
        ({}, {}, 500),
    ],
)
def test_invalid_quote_envelope(body, headers, status):
    with pytest.raises(X402Error):
        parse_quote(HTTPResponse(status, body, headers))


def test_bad_header_cannot_fall_back_to_valid_body(quote):
    with pytest.raises(X402Error):
        parse_quote(HTTPResponse(402, quote, {"payment-required": "bad"}))


def test_eip3009_hash_matches_independently_encoded_known_vector(quote):
    # Independent oracle: manually concatenate 32-byte ABI words and Keccak each
    # EIP-712 layer. No eth_account typed-data encoder or production type list is
    # used to compute expected. The final digest was also precomputed separately.
    def uint(value):
        return value.to_bytes(32, "big")

    def address(value):
        return bytes.fromhex(value[2:]).rjust(32, b"\x00")

    domain = keccak(
        keccak(
            text="EIP712Domain(string name,string version,uint256 chainId,"
            "address verifyingContract)"
        )
        + keccak(text="USD Coin")
        + keccak(text="2")
        + uint(8453)
        + address("0x833589fcd6edb6e08f4c7c32d4f71b54bda02913")
    )
    struct = keccak(
        keccak(
            text="TransferWithAuthorization(address from,address to,uint256 value,"
            "uint256 validAfter,uint256 validBefore,bytes32 nonce)"
        )
        + address("0x1111111111111111111111111111111111111111")
        + address("0x2670b922ef37c7df47158725c0cc407b5382293f")
        + uint(5_000_000)
        + uint(0)
        + uint(1_750_000_300)
        + bytes(range(32))
    )
    expected = keccak(b"\x19\x01" + domain + struct)
    assert expected.hex() == "a4e49fd6b63087565d7676c4c7cc1b5ec963fe756147f1575f433b9b6e7e4116"
    typed = authorization_typed_data(
        quote["accepts"][0],
        "0x1111111111111111111111111111111111111111",
        now=1_750_000_000,
        nonce=bytes(range(32)),
    )
    signable = encode_typed_data(full_message=typed)
    actual = keccak(b"\x19" + signable.version + signable.header + signable.body)
    assert signable.header == domain and signable.body == struct and actual == expected
    for field_name in ("value", "validAfter", "validBefore"):
        changed = deepcopy(typed)
        changed["message"][field_name] += 1
        assert encode_typed_data(full_message=changed).body != struct


def test_payment_header_v2_envelope_signature_and_fresh_nonce(quote, monkeypatch,
                                                              write_ahead):
    monkeypatch.setattr("factorylab.world.x402.time.time_ns", lambda: 1_750_000_000_000_000_000)
    account = Account.from_key(TEST_KEY)
    selected = parse_quote(HTTPResponse(402, quote))
    payment = decoded(payment_header(account, selected, guard=write_ahead))
    assert set(payment) == {"x402Version", "accepted", "payload", "resource", "extensions"}
    assert payment["x402Version"] == 2
    assert payment["accepted"] == selected.accepted
    assert payment["resource"] == selected.resource
    assert payment["extensions"] == selected.extensions
    auth = payment["payload"]["authorization"]
    assert auth["from"] == account.address and auth["to"] == selected.accepted["payTo"]
    assert (auth["value"], auth["validAfter"], auth["validBefore"]) == (
        "5000000",
        "0",
        "1750000300",
    )
    typed = authorization_typed_data(
        selected.accepted,
        account.address,
        now=1_750_000_000,
        nonce=bytes.fromhex(auth["nonce"][2:]),
    )
    assert (
        Account.recover_message(
            encode_typed_data(full_message=typed),
            signature=payment["payload"]["signature"],
        )
        == account.address
    )
    second = decoded(payment_header(account, selected, guard=write_ahead))
    assert auth["nonce"] != second["payload"]["authorization"]["nonce"]
    assert TEST_KEY[2:] not in json.dumps(payment)


def test_siwe_structure_resource_timestamp_signature_and_freshness(monkeypatch):
    monkeypatch.setattr("factorylab.world.x402.time.time_ns", lambda: 1_750_000_000_123_000_000)
    account = Account.from_key(TEST_KEY)
    url = "https://fake.test/api/v1/chat/completions"
    header = decoded(siwe_header(account, url))
    assert set(header) == {"address", "message", "signature", "timestamp", "chainId"}
    assert header["chainId"] == 8453 and header["timestamp"] == 1_750_000_000_123
    message = header["message"]
    assert message.startswith(
        f"fake.test wants you to sign in with your Ethereum account:\n{account.address}\n\n",
    )
    assert f"URI: {url}\nVersion: 1\nChain ID: 8453\n" in message
    fields = dict(line.split(": ", 1) for line in message.splitlines() if ": " in line)
    assert len(fields["Nonce"]) >= 8 and fields["Nonce"].isalnum()
    issued, expires = (datetime.fromisoformat(fields[k]) for k in ("Issued At", "Expiration Time"))
    assert (expires - issued).total_seconds() == 300
    assert (
        Account.recover_message(encode_defunct(text=message), signature=header["signature"])
        == account.address
    )
    assert decoded(siwe_header(account, url))["message"] != message
    assert TEST_KEY[2:] not in json.dumps(header)


def test_balance_calls_use_correct_rpc_abi_and_integer_units():
    address = Account.from_key(TEST_KEY).address
    fake = FakeHTTP(
        [
            HTTPResponse(200, {"id": 1, "result": hex(5_500_000)}),
            HTTPResponse(200, {"id": 1, "result": hex(10**16)}),
            HTTPResponse(200, {"balanceUsd": "4.9999999", "canConsume": True}),
        ]
    )
    assert usdc_balance(address, rpc="https://rpc.test", transport=fake) == 5_500_000
    assert eth_balance(address, rpc="https://rpc.test", transport=fake) == 10**16
    assert (
        venice_balance(
            address, private_key=TEST_KEY, base_url="https://fake.test/api/v1", transport=fake
        )
        == 4_999_999
    )
    first = fake.calls[0]
    assert first[:2] == ("POST", "https://rpc.test") and first[3] == {}
    assert first[2] == {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "eth_call",
        "params": [
            {
                "to": BASE_USDC,
                "data": "0x70a08231" + address[2:].lower().zfill(64),
            },
            "latest",
        ],
    }
    assert fake.calls[1][2]["method"] == "eth_getBalance"
    assert fake.calls[1][2]["params"] == [address, "latest"]
    assert fake.calls[2][1] == f"https://fake.test/api/v1/x402/balance/{address}"
    assert "X-Sign-In-With-X" in fake.calls[2][3]


@pytest.mark.parametrize(
    "body",
    [
        {"error": {"message": TEST_KEY}},
        {"id": 1},
        {"id": 9, "result": "0x5"},
        {"id": 1, "result": "-1"},
        {"id": 1, "result": "0x"},
        {"id": 1, "result": hex(2**256)},
    ],
)
def test_rpc_failure_cannot_be_treated_as_zero_or_leak_key(body):
    with pytest.raises(X402Error) as caught:
        usdc_balance(
            Account.from_key(TEST_KEY).address, transport=FakeHTTP([HTTPResponse(200, body)])
        )
    assert TEST_KEY not in str(caught.value)


@pytest.mark.parametrize("in_header", [True, False])
def test_topup_one_quote_one_submission_returns_settlement(quote, in_header):
    account = Account.from_key(TEST_KEY)
    settlement = {
        "success": True,
        "network": BASE_NETWORK,
        "payer": account.address,
        "transaction": "0x" + "ab" * 32,
    }
    fake = FakeHTTP(
        [
            HTTPResponse(200, {"id": 1, "result": hex(5_500_000)}),
            HTTPResponse(402, quote),
            HTTPResponse(
                200,
                {} if in_header else settlement,
                {"PAYMENT-RESPONSE": encoded(settlement)} if in_header else {},
            ),
        ]
    )
    client = X402Client(private_key=TEST_KEY, base_url="https://fake.test/api/v1", transport=fake)
    assert client.top_up() == settlement
    assert len(fake.calls) == 3
    initial, retry = fake.calls[1:]
    assert initial[:3] == retry[:3] == ("POST", "https://fake.test/api/v1/x402/top-up", {})
    assert "X-402-Payment" not in initial[3]
    assert decoded(retry[3]["X-402-Payment"])["accepted"] == quote["accepts"][0]
    assert initial[3]["X-Sign-In-With-X"] != retry[3]["X-Sign-In-With-X"]


@pytest.mark.parametrize(
    "failure",
    [
        TimeoutError(TEST_KEY),
        error.URLError(TEST_KEY),
        RuntimeError(TEST_KEY),
        HTTPResponse(402, {"error": TEST_KEY}),
        HTTPResponse(500, {"error": TEST_KEY}),
        HTTPResponse(200, {"success": False, "errorReason": TEST_KEY}),
        HTTPResponse(200, {"success": True, "network": "eip155:1"}),
        HTTPResponse(200, {"success": True, "payer": "0x" + "22" * 20}),
    ],
)
def test_submitted_payment_never_retries_or_leaks_secrets(quote, failure):
    fake = FakeHTTP(
        [
            HTTPResponse(200, {"id": 1, "result": hex(5_500_000)}),
            HTTPResponse(402, quote),
            failure,
        ]
    )
    with pytest.raises(X402Error) as caught:
        X402Client(private_key=TEST_KEY, transport=fake).top_up()
    assert len(fake.calls) == 3
    assert TEST_KEY[2:] not in "".join(traceback.format_exception(caught.value))


def test_insufficient_funds_or_unapproved_amount_cannot_request_payment():
    fake = FakeHTTP([HTTPResponse(200, {"id": 1, "result": hex(4_999_999)})])
    client = X402Client(private_key=TEST_KEY, transport=fake)
    with pytest.raises(X402Error, match="Insufficient"):
        client.top_up()
    for amount in (1, 10_000_000, True, "5000000"):
        with pytest.raises(X402Error):
            client.top_up(amount)
    assert len(fake.calls) == 1


class WireResponse(BytesIO):
    def __init__(self, body, status=200, headers=None):
        super().__init__(body)
        self.status = status
        self.headers = headers or {}


def test_real_transport_retains_402_headers_and_decimal_json(monkeypatch, quote):
    calls = []
    responses = [
        error.HTTPError(
            "https://fake.test",
            402,
            "Payment Required",
            {"payment-required": encoded(quote)},
            BytesIO(json.dumps(quote).encode()),
        ),
        WireResponse(b'{"cost":{"usd":0.000000000000000123456789}}'),
    ]

    def fake_open(self, req, timeout):
        calls.append((req, timeout))
        response = responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response

    monkeypatch.setattr(request.OpenerDirector, "open", fake_open)
    response = http_request("POST", "https://fake.test", {}, {"test": "header"})
    assert parse_quote(response).accepted == quote["accepts"][0]
    response = http_request("GET", "https://fake.test", None, {})
    assert str(response.body["cost"]["usd"]) == "1.23456789E-16"
    assert calls[0][0].data == b"{}" and calls[0][1] == 180


def test_redirect_handler_refuses_replaying_authentication():
    from factorylab.world.x402 import _NoRedirect

    assert _NoRedirect().redirect_request(None, None, 307, "", {}, "https://elsewhere") is None


def test_invalid_private_key_has_no_secret_in_traceback(monkeypatch):
    monkeypatch.setenv("RESERVE_PRIVATE_KEY", "private-invalid-fixture")
    with pytest.raises(X402Error) as caught:
        reserve_address()
    assert "private-invalid-fixture" not in "".join(traceback.format_exception(caught.value))


def test_venice_balance_unwraps_official_sdk_data_envelope():
    # https://github.com/veniceai/x402-client/blob/main/src/index.ts getBalance()
    fake = FakeHTTP([HTTPResponse(200, {"data": {"balanceUsd": "4.999999999999999999999999999"}})])
    assert X402Client(private_key=TEST_KEY, transport=fake).venice_balance() == 4_999_999


def test_topup_preserves_venice_data_envelope(quote):
    settlement = {"success": True, "data": {"newBalance": "5", "transactionHash": "0x" + "ab" * 32}}
    fake = FakeHTTP([
        HTTPResponse(200, {"id": 1, "result": hex(5_500_000)}),
        HTTPResponse(402, quote), HTTPResponse(200, settlement),
    ])
    assert X402Client(private_key=TEST_KEY, transport=fake).top_up() == settlement


@pytest.mark.parametrize("amount", [0, 1, 1734, 5_000_001, 10**30])
def test_general_quote_signs_exact_arbitrary_amount(quote, amount, write_ahead):
    quote["accepts"][0]["amount"] = str(amount)
    selected = parse_quote(HTTPResponse(402, quote))
    assert selected.amount_micro == amount and type(selected.amount_micro) is int
    account = Account.from_key(TEST_KEY)
    typed = authorization_typed_data(selected.accepted, account.address)
    assert typed["message"]["value"] == amount
    header = decoded(payment_header(account, selected, guard=write_ahead))
    assert header["payload"]["authorization"]["value"] == str(amount)


@pytest.mark.parametrize("amount", [4_999_999, 5_000_001])
def test_venice_topup_still_requires_explicit_five_dollar_quote(quote, amount):
    quote["accepts"][0]["amount"] = str(amount)
    with pytest.raises(X402Error):
        parse_quote(HTTPResponse(402, quote), amount_micro=5_000_000)
    fake = FakeHTTP([
        HTTPResponse(200, {"id": 1, "result": hex(6_000_000)}), HTTPResponse(402, quote),
    ])
    with pytest.raises(X402Error):
        X402Client(private_key=TEST_KEY, transport=fake).top_up()
    assert len(fake.calls) == 2


def test_generic_authorize_ceiling_and_reserve_precede_signature(quote, monkeypatch):
    quote["accepts"][0]["amount"] = "123"
    selected = parse_quote(HTTPResponse(402, quote))
    fake = FakeHTTP([HTTPResponse(200, {"id": 1, "result": hex(122)})])
    client = X402Client(private_key=TEST_KEY, transport=fake)
    monkeypatch.setattr("factorylab.world.x402.payment_header", lambda *a: pytest.fail("signed"))
    with pytest.raises(X402Error, match="ceiling"):
        client.authorize(selected, ceiling_micro=122)
    assert not fake.calls
    with pytest.raises(X402Error, match="Reserve"):
        client.authorize(selected, ceiling_micro=123)
    assert len(fake.calls) == 1


def test_payment_header_survives_decimal_extensions(quote, write_ahead):
    """A seller's extension blob decoded with Decimal floats (FarOuter's price info) must not
    break the envelope: the proof of 12 September failed here before any payment was sent."""
    from decimal import Decimal

    account = Account.from_key(TEST_KEY)
    body = dict(quote)
    body["extensions"] = {"bazaar": {"faroutQuote": {"info": {"usd": Decimal("0.001")}}}}
    selected = parse_quote(HTTPResponse(402, body))
    payment = decoded(payment_header(account, selected, guard=write_ahead))
    assert payment["extensions"]["bazaar"]["faroutQuote"]["info"]["usd"] == "0.001"


@pytest.mark.parametrize("method,path,expected", [
    ("POST", "/api/v1/chat/completions", 900),
    ("GET", "/api/v1/models", 180),
    ("POST", "/settle", 180),
])
def test_completion_processing_has_a_long_finite_deadline(monkeypatch, method, path, expected):
    def open_request(self, req, timeout):
        assert timeout == expected
        return WireResponse(b'{}')
    monkeypatch.setattr(request.OpenerDirector, "open", open_request)
    http_request(method, "https://fake.test" + path, {}, {})
