import json
import traceback
from dataclasses import dataclass, field, replace
from fractions import Fraction
from io import BytesIO
from urllib import error, request

import pytest

from factorylab.world.models import ModelRequest, TokenPrice
from factorylab.world.venice import VeniceError, VeniceProvider

TEST_KEY = "0x" + "11" * 32


@pytest.fixture(autouse=True)
def isolated_http_and_credentials(monkeypatch):
    monkeypatch.delenv("VENICE_API_KEY", raising=False)
    monkeypatch.delenv("RESERVE_PRIVATE_KEY", raising=False)

    def deny(*args, **kwargs):
        pytest.fail("unexpected real HTTP request")

    monkeypatch.setattr(request.OpenerDirector, "open", deny)


@pytest.fixture
def catalogue():
    return {
        "data": [
            {
                "id": "test-flash",
                "type": "text",
                "name": "Test Flash",
                "model_spec": {
                    "availableContextTokens": 32768,
                    "pricing": {"input": {"usd": "0.15"}, "output": {"usd": "0.50"}},
                },
            }
        ]
    }


@pytest.fixture
def completion():
    return {
        "id": "venice-test-1",
        "model": "test-flash",
        "choices": [{"message": {"role": "assistant", "content": "OK"}, "finish_reason": "stop"}],
        "usage": {
            "prompt_tokens": 25,
            "completion_tokens": 10,
            "cost": "99",
            "completion_tokens_details": {"reasoning_tokens": 3},
        },
        "cost": {"usd": "0.0000123", "diem": 0},
    }


@pytest.fixture
def req():
    return ModelRequest("venice:test-flash", "System", ({"role": "user", "content": "Hi"},), 64)


@dataclass
class FakeTransport:
    responses: list
    calls: list = field(default_factory=list)

    def __call__(self, method, path, payload):
        self.calls.append((method, path, payload))
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def test_catalogue_namespaces_prices_and_preserves_fractional_mtok(catalogue):
    fake = FakeTransport([catalogue])
    (entry,) = VeniceProvider(transport=fake).catalogue()
    assert (entry.id, entry.name, entry.context_length) == (
        "venice:test-flash",
        "Test Flash",
        32768,
    )
    assert entry.price() == TokenPrice(Fraction(3, 20), Fraction(1, 2))
    assert entry.price().cost(25, 10) == 9
    assert fake.calls == [("GET", "/models", None)]


def test_catalogue_long_decimal_price_has_no_intermediate_rounding(catalogue):
    quote = "0.123456789012345678901234567890123456789"
    catalogue["data"][0]["model_spec"]["pricing"]["input"]["usd"] = quote
    catalogue["data"][0]["model_spec"].pop("availableContextTokens")
    catalogue["data"].append({"id": "image-model", "type": "image"})
    (entry,) = VeniceProvider(transport=FakeTransport([catalogue])).catalogue()
    assert entry.price().input_micro == Fraction(quote)
    assert entry.context_length is None


@pytest.mark.parametrize("reported,expected", [
    ("0.0000123", 13), ("0", 0), ("0.0000001", 1),
    ("0.00000100000000000000000000000000000001", 2),
])
def test_cost_reads_top_level_usd_not_usage_cost(completion, req, reported, expected):
    completion["cost"]["usd"] = reported
    fake = FakeTransport([completion])
    response = VeniceProvider(transport=fake).complete(req)
    assert response.cost_micro == expected and type(response.cost_micro) is int
    assert response.raw == {
        "request_id": "venice-test-1",
        "reasoning_tokens": 3,
        "cost_source": "reported",
    }
    assert response.model_id == "venice:test-flash" and response.text == "OK"
    assert (response.input_tokens, response.output_tokens) == (25, 10)
    assert fake.calls == [
        (
            "POST",
            "/chat/completions",
            {
                "model": "test-flash",
                "messages": [
                    {"role": "system", "content": "System"},
                    {"role": "user", "content": "Hi"},
                ],
                "max_tokens": 64,
            },
        )
    ]


@pytest.mark.parametrize("reported", [None, "missing"])
def test_missing_cost_uses_catalogue_and_records_estimate(completion, catalogue, req, reported):
    if reported == "missing":
        del completion["cost"]
    else:
        completion["cost"]["usd"] = None
    fake = FakeTransport([completion, catalogue, completion])
    provider = VeniceProvider(transport=fake)
    for _ in range(2):
        response = provider.complete(req)
        assert response.cost_micro == 9
        assert response.raw["cost_source"] == "table"
        assert response.raw["price_model_id"] == "venice:test-flash"
        assert response.raw["cost_scope"] == "tokens_only"
    assert [call[0] for call in fake.calls] == ["POST", "GET", "POST"]


def test_fallback_uses_serving_model_when_catalogued(completion, catalogue, req):
    completion.pop("cost")
    completion["model"] = "served-model"
    catalogue["data"][0]["id"] = "served-model"
    catalogue["data"][0]["model_spec"]["pricing"]["output"]["usd"] = "1.00"
    result = VeniceProvider(transport=FakeTransport([completion, catalogue])).complete(req)
    assert result.model_id == "venice:served-model" and result.cost_micro == 14


@pytest.mark.parametrize("cost", ["-1", "NaN", "Infinity", "not-money", True])
def test_invalid_reported_cost_fails_without_catalogue_fallback(completion, req, cost):
    completion["cost"]["usd"] = cost
    fake = FakeTransport([completion])
    with pytest.raises(VeniceError):
        VeniceProvider(transport=fake).complete(req)
    assert len(fake.calls) == 1


@pytest.mark.parametrize(
    "config,expected",
    [
        ({"effort": "high"}, {"reasoning_effort": "high", "disable_thinking": False}),
        ({"effort": "none"}, {"reasoning": {"enabled": False}, "disable_thinking": True}),
        ({"enabled": False}, {"reasoning": {"enabled": False}, "disable_thinking": True}),
        ({"enabled": True}, {"disable_thinking": False}),
        ({"max_tokens": 200}, {"reasoning_effort": "low", "disable_thinking": False}),
    ],
)
def test_reasoning_tier_mapping_and_budget_substitution(completion, req, config, expected):
    fake = FakeTransport([completion])
    provider = VeniceProvider(transport=fake, reasoning_config={req.model_id: config})
    saved = dict(config)
    config.clear()  # Tier settings are copied, not shared mutable configuration.
    response = provider.complete(req)
    payload = fake.calls[0][2]
    assert payload.get("reasoning_effort") == expected.get("reasoning_effort")
    assert payload["venice_parameters"]["disable_thinking"] == expected["disable_thinking"]
    assert payload.get("reasoning") == expected.get("reasoning")
    if "max_tokens" in saved:
        assert response.raw["reasoning_substitution"] == {
            "requested_max_tokens": 200,
            "reasoning_effort": "low",
            "reason": "token_budget_unsupported",
        }
    else:
        assert "reasoning_substitution" not in response.raw


def test_effort_suffix_and_web_tier_mapping(completion, req):
    fake = FakeTransport([completion])
    provider = VeniceProvider(
        transport=fake,
        reasoning_config={req.model_id: {"max_tokens": 200}},
        web_config={req.model_id + ":online": {"engine": "exa", "max_results": 2}},
    )
    response = provider.complete(replace(req, model_id=req.model_id + ":online@high"))
    payload = fake.calls[0][2]
    assert payload["model"] == "test-flash"
    assert payload["reasoning_effort"] == "high"
    assert payload["venice_parameters"] == {"disable_thinking": False, "enable_web_search": "auto"}
    assert "plugins" not in payload and "reasoning_substitution" not in response.raw


@pytest.mark.parametrize("mode", ["on", "off", "auto"])
def test_explicit_web_modes(completion, req, mode):
    fake = FakeTransport([completion])
    VeniceProvider(transport=fake, web_config={req.model_id: {"enable_web_search": mode}}).complete(
        req
    )
    assert fake.calls[0][2]["venice_parameters"]["enable_web_search"] == mode


def test_function_tools_and_tool_messages_are_preserved(completion, req):
    tools = [{"type": "function", "function": {"name": "price", "parameters": {"type": "object"}}}]
    calls = [{"id": "call-1", "type": "function", "function": {"name": "price", "arguments": "{}"}}]
    messages = (
        {"role": "assistant", "content": None, "tool_calls": calls},
        {"role": "tool", "tool_call_id": "call-1", "content": "42"},
    )
    completion["choices"][0]["message"] = {
        "content": None,
        "tool_calls": calls,
        "reasoning_content": "Reasoning text",
    }
    completion["choices"][0]["finish_reason"] = "tool_calls"
    fake = FakeTransport([completion])
    result = VeniceProvider(transport=fake).complete(
        replace(req, messages=messages),
        tools=tools,
        tool_choice="auto",
        parallel_tool_calls=False,
    )
    payload = fake.calls[0][2]
    assert payload["messages"][1:] == list(messages)
    assert payload["tools"] == tools and payload["tool_choice"] == "auto"
    assert payload["parallel_tool_calls"] is False
    assert result.text == "" and result.raw["tool_calls"] == calls
    assert (
        result.stop_reason == "tool_calls" and result.raw["reasoning_content"] == "Reasoning text"
    )


@pytest.mark.parametrize(
    "failure",
    [
        RuntimeError(TEST_KEY),
        error.URLError(TEST_KEY),
        TimeoutError(TEST_KEY),
        VeniceError(500, TEST_KEY),
    ],
)
def test_completion_never_retries_or_exposes_transport_failures(req, failure):
    fake = FakeTransport([failure])
    with pytest.raises(VeniceError) as caught:
        VeniceProvider(transport=fake).complete(req)
    assert len(fake.calls) == 1
    assert TEST_KEY[2:] not in "".join(traceback.format_exception(caught.value))


def test_get_retries_once_only_for_connection_errors(catalogue):
    fake = FakeTransport([error.URLError("offline"), catalogue])
    assert VeniceProvider(transport=fake).catalogue()
    assert len(fake.calls) == 2
    fake = FakeTransport([error.URLError("offline"), TimeoutError()])
    with pytest.raises(VeniceError):
        VeniceProvider(transport=fake).catalogue()
    assert len(fake.calls) == 2


class WireResponse(BytesIO):
    def __init__(self, body, status=200):
        super().__init__(json.dumps(body).encode())
        self.status, self.headers = status, {}


@pytest.mark.parametrize("api_key", [False, True])
def test_wire_auth_prefers_api_key_otherwise_fresh_siwe(monkeypatch, completion, req, api_key):
    import base64

    monkeypatch.setenv("RESERVE_PRIVATE_KEY", TEST_KEY)
    if api_key:
        monkeypatch.setenv("VENICE_API_KEY", "test-api-secret")
    seen = []

    def fake_open(self, req, timeout):
        seen.append(req)
        return WireResponse(completion)

    monkeypatch.setattr(request.OpenerDirector, "open", fake_open)
    provider = VeniceProvider(base_url="https://fake.test/api/v1/")
    provider.complete(req)
    provider.complete(req)
    headers = [{k.lower(): v for k, v in r.header_items()} for r in seen]
    assert all(r.full_url == "https://fake.test/api/v1/chat/completions" for r in seen)
    if api_key:
        assert all(h["authorization"] == "Bearer test-api-secret" for h in headers)
        assert all("x-sign-in-with-x" not in h for h in headers)
    else:
        assert all("authorization" not in h for h in headers)
        assert headers[0]["x-sign-in-with-x"] != headers[1]["x-sign-in-with-x"]
        auth = json.loads(base64.b64decode(headers[0]["x-sign-in-with-x"]))
        assert auth["chainId"] == 8453 and "fake.test" in auth["message"]


def test_public_catalogue_needs_no_key(monkeypatch, catalogue):
    def fake_open(self, req, timeout):
        assert not req.has_header("Authorization") and not req.has_header("X-sign-in-with-x")
        return WireResponse(catalogue)

    monkeypatch.setattr(request.OpenerDirector, "open", fake_open)
    assert VeniceProvider().catalogue()[0].id == "venice:test-flash"


def test_response_metadata_and_text_cannot_echo_keys(monkeypatch, completion, req):
    monkeypatch.setenv("RESERVE_PRIVATE_KEY", TEST_KEY)
    completion["id"] = TEST_KEY
    completion["choices"][0]["message"]["content"] = TEST_KEY[2:]
    result = VeniceProvider(transport=FakeTransport([completion])).complete(req)
    assert TEST_KEY[2:] not in repr(result) and "[REDACTED]" in repr(result)


def test_missing_auth_and_unprefixed_id_cannot_call_completion(req):
    with pytest.raises(VeniceError):
        VeniceProvider().complete(req)
    fake = FakeTransport([])
    with pytest.raises(VeniceError):
        VeniceProvider(transport=fake).complete(replace(req, model_id="test-flash"))
    assert not fake.calls
