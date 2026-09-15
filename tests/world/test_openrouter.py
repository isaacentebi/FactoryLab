import json
import os
import traceback
from dataclasses import dataclass, field, replace
from fractions import Fraction
from io import BytesIO
from urllib import error, request

import pytest

from factorylab.world.metering import BillingUncertain, Meter, MeteredModel
from factorylab.world.models import ModelRequest, ModelResponse, PriceTable, TokenPrice
from factorylab.world.openrouter import OpenRouterError, OpenRouterProvider


@pytest.fixture
def completion():
    # Shapes: https://openrouter.ai/docs/api/api-reference/chat/create-a-chat-completion
    # Usage: https://openrouter.ai/docs/cookbook/administration/usage-accounting
    return {
        "id": "gen-test-request",
        "object": "chat.completion",
        "created": 1677652288,
        "model": "test/flash",
        "choices": [{
            "index": 0,
            "message": {"role": "assistant", "content": "Hello world"},
            "finish_reason": "stop",
        }],
        "usage": {
            "prompt_tokens": 25,
            "completion_tokens": 10,
            "total_tokens": 35,
            "cost": 0.0000123,
            "completion_tokens_details": {"reasoning_tokens": 5},
        },
    }


@pytest.fixture
def catalogue():
    # https://openrouter.ai/docs/api/api-reference/models/list-all-models-and-their-properties
    return {"data": [{
        "id": "test/flash",
        "name": "Test Flash",
        "context_length": 8192,
        "pricing": {"prompt": "0.00000015", "completion": "0.00000025", "request": "0"},
    }]}


@pytest.fixture
def req():
    return ModelRequest(
        "test/flash", "System text", ({"role": "user", "content": "Hello"},), max_tokens=100
    )


@dataclass
class FakeTransport:
    responses: list
    calls: list = field(default_factory=list)

    def __call__(self, method, path, json):
        self.calls.append((method, path, json))
        result = self.responses.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


def test_complete_payload_usage_and_cost_rounding(completion, req):
    transport = FakeTransport([completion])
    response = OpenRouterProvider(transport=transport).complete(req)
    assert transport.calls == [("POST", "/chat/completions", {
        "model": req.model_id,
        "messages": [{"role": "system", "content": req.system}, *req.messages],
        "max_tokens": 100,
    })]
    assert response == ModelResponse(
        "test/flash", "Hello world", 25, 10, "stop", False,
        {"request_id": "gen-test-request", "reasoning_tokens": 5}, 13,
    )
    assert type(response.cost_micro) is int


@pytest.mark.parametrize("cost, expected", [(0, 0), (0.000012, 12), (0.0000001, 1), (None, None)])
def test_reported_cost_boundaries(completion, req, cost, expected):
    completion["usage"]["cost"] = cost
    response = OpenRouterProvider(transport=FakeTransport([completion])).complete(req)
    assert response.cost_micro == expected


@pytest.mark.parametrize("cost", [-0.000001, float("nan"), float("inf")])
def test_invalid_reported_cost_books_uncertainty_without_a_live_hold(completion, req, cost):
    completion["usage"]["cost"] = cost
    wallet = TinyWallet(1000)
    model = MeteredModel(
        OpenRouterProvider(transport=FakeTransport([completion])),
        PriceTable({req.model_id: TokenPrice(1, 5)}), Meter(wallet),
    )
    with pytest.raises(BillingUncertain, match="billing uncertain"):
        model.complete(req, handle="invalid-cost")
    assert wallet.balance == 1000 - model.ceiling(req) and wallet.reserved == 0
    assert wallet.log == [("reserve", model.ceiling(req)), ("uncertain", model.ceiling(req))]


def test_text_parts_and_optional_fields(completion, req):
    completion["choices"][0]["message"]["content"] = [
        {"type": "text", "text": "Hello "},
        {"type": "image_url", "image_url": {"url": "https://example.com/image.png"}},
        {"type": "text", "text": "world"},
    ]
    completion["choices"][0]["finish_reason"] = None
    del completion["usage"]["cost"]
    del completion["usage"]["completion_tokens_details"]
    del completion["id"]
    del completion["model"]
    response = OpenRouterProvider(transport=FakeTransport([completion])).complete(req)
    assert response.text == "Hello world"
    assert response.cost_micro is None and response.raw == {} and response.stop_reason == ""
    assert response.model_id == req.model_id


@pytest.mark.parametrize("effort", ["low", "medium", "high", "none", "max", "HIGH", ""])
@pytest.mark.parametrize("configured", [True, False])
def test_reasoning_requires_supported_effort_and_configured_model(
    completion, req, effort, configured
):
    transport = FakeTransport([completion])
    models = {req.model_id} if configured else {"different/model"}
    provider = OpenRouterProvider(transport=transport, reasoning_models=models)
    models.clear()  # Construction takes a snapshot of the capability configuration.
    provider.complete(replace(req, effort=effort))
    payload = transport.calls[0][2]
    if configured and effort in {"low", "medium", "high"}:
        assert payload["reasoning"] == {"effort": effort}
    else:
        assert "reasoning" not in payload


def test_catalogue_preserves_decimal_quotes_and_rounds_total_up(catalogue):
    transport = FakeTransport([catalogue])
    entry, = OpenRouterProvider(transport=transport).catalogue()
    assert transport.calls == [("GET", "/models", None)]
    assert (entry.id, entry.name, entry.context_length) == ("test/flash", "Test Flash", 8192)
    assert entry.prompt_usd_per_token == "0.00000015"
    assert entry.completion_usd_per_token == "0.00000025"
    price = entry.price()
    assert price == TokenPrice(Fraction(3, 20), Fraction(1, 4))
    assert type(price.input_micro) is Fraction and type(price.output_micro) is Fraction
    assert price.cost(1, 1) == 1  # Round the combined total, not each component.
    assert price.cost(5, 1) == 1
    assert price.cost(6, 1) == 2
    assert price.cost(0, 0) == 0
    assert type(price.cost(1, 1)) is int


def test_catalogue_context_can_be_null_or_missing(catalogue):
    catalogue["data"][0]["context_length"] = None
    catalogue["data"].append({
        k: v for k, v in catalogue["data"][0].items() if k != "context_length"
    })
    entries = OpenRouterProvider(transport=FakeTransport([catalogue])).catalogue()
    assert all(e.context_length is None for e in entries)


def test_fractional_price_preserves_long_decimal_quote():
    quote = "0.000000123456789012345678901234567890123"
    price = TokenPrice.from_per_token(quote, "0")
    assert price.input_micro == Fraction(quote) * 1_000_000
    assert price.cost(10**30, 0) == 123456789012345678901234567891


@pytest.mark.parametrize("remaining, expected", [
    (74.5, 74_500_000), (0.0000129, 12), (0.0000009, 0), (0, 0), (None, None),
])
def test_balance_rounds_down(remaining, expected):
    # https://openrouter.ai/docs/api/api-reference/api-keys/get-current-api-key
    transport = FakeTransport([{"data": {"limit": 100, "limit_remaining": remaining}}])
    assert OpenRouterProvider(transport=transport).balance_micro() == expected
    assert transport.calls == [("GET", "/key", None)]


@pytest.mark.parametrize("failure", [error.URLError("offline"), ConnectionError(), TimeoutError()])
def test_post_never_retried_on_connection_failure(req, failure):
    transport = FakeTransport([failure])
    with pytest.raises(OpenRouterError, match="Connection failed"):
        OpenRouterProvider(transport=transport).complete(req)
    assert len(transport.calls) == 1


@pytest.mark.parametrize("endpoint", ["catalogue", "balance_micro"])
@pytest.mark.parametrize("failure", [error.URLError("offline"), ConnectionError(), TimeoutError()])
def test_get_retries_once_on_connection_failure(catalogue, endpoint, failure):
    body = catalogue if endpoint == "catalogue" else {"data": {"limit_remaining": 1}}
    transport = FakeTransport([failure, body])
    assert getattr(OpenRouterProvider(transport=transport), endpoint)()
    assert len(transport.calls) == 2 and transport.calls[0] == transport.calls[1]


def test_get_stops_after_second_connection_failure():
    transport = FakeTransport([ConnectionError(), ConnectionError()])
    with pytest.raises(OpenRouterError, match="Connection failed"):
        OpenRouterProvider(transport=transport).catalogue()
    assert len(transport.calls) == 2


@pytest.mark.parametrize("endpoint", ["catalogue", "complete"])
@pytest.mark.parametrize("status", [301, 401, 429, 500])
def test_http_errors_are_redacted_and_never_retried(monkeypatch, req, endpoint, status):
    monkeypatch.setenv("TEST_OPENROUTER_KEY", "test-secret-do-not-expose")
    failure = error.HTTPError(
        "https://openrouter.ai/api/v1/models", status, "error", {},
        BytesIO(b'{"error":{"message":"test-secret-do-not-expose denied"}}'),
    )
    transport = FakeTransport([failure])
    provider = OpenRouterProvider(key_env="TEST_OPENROUTER_KEY", transport=transport)
    with pytest.raises(OpenRouterError) as caught:
        getattr(provider, endpoint)(req) if endpoint == "complete" else provider.catalogue()
    assert caught.value.status == status
    assert "[REDACTED] denied" in caught.value.body
    assert "test-secret-do-not-expose" not in str(caught.value)
    assert "test-secret-do-not-expose" not in "".join(traceback.format_exception(caught.value))
    assert len(transport.calls) == 1


@pytest.mark.parametrize("failure_kind", ["provider", "connection", "unexpected"])
def test_injected_transport_exceptions_cannot_expose_key(monkeypatch, req, failure_kind):
    secret = "test-only-injected-secret"
    monkeypatch.setenv("OPENROUTER_API_KEY", secret)
    if failure_kind == "provider":
        failure = OpenRouterError(500, secret)
    elif failure_kind == "connection":
        failure = error.URLError(secret)
    else:
        failure = RuntimeError(secret)
    with pytest.raises(OpenRouterError) as caught:
        OpenRouterProvider(transport=FakeTransport([failure])).complete(req)
    assert secret not in str(caught.value) and secret not in caught.value.body


class HttpResponse(BytesIO):
    def __init__(self, body, status=200):
        super().__init__(json.dumps(body).encode())
        self.status = status


def test_default_transport_url_headers_payload_timeout_and_get_retry(monkeypatch, completion, req):
    monkeypatch.setenv("TEST_OPENROUTER_KEY", "test-wire-key")
    calls = []
    responses = [HttpResponse(completion), error.URLError("offline"),
                 HttpResponse({"data": {"limit_remaining": 1.0000009}})]

    def fake_open(self, wire_req, timeout):
        calls.append((wire_req, timeout))
        response = responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response

    monkeypatch.setattr(request.OpenerDirector, "open", fake_open)
    provider = OpenRouterProvider(
        key_env="TEST_OPENROUTER_KEY", base_url="https://example.com/api/v1/", app_name="lab-test"
    )
    assert provider.complete(req).cost_micro == 13
    assert provider.balance_micro() == 1_000_000
    wire, timeout = calls[0]
    assert timeout == 180 and wire.full_url == "https://example.com/api/v1/chat/completions"
    assert wire.get_method() == "POST"
    assert dict((k.lower(), v) for k, v in wire.header_items()) == {
        "authorization": "Bearer test-wire-key", "http-referer": "lab-test",
        "x-title": "lab-test", "content-type": "application/json",
    }
    assert json.loads(wire.data) == {
        "model": req.model_id, "max_tokens": req.max_tokens,
        "messages": [{"role": "system", "content": req.system}, *req.messages],
    }
    assert len(calls) == 3
    for wire, timeout in calls[1:]:
        assert wire.get_method() == "GET" and wire.data is None and timeout == 180
        assert wire.full_url == "https://example.com/api/v1/key"


@pytest.mark.parametrize("status", [302, 401, 500])
def test_default_transport_rejects_non_success(monkeypatch, req, status):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-http-secret")
    calls = []

    def fake_open(self, wire_req, timeout):
        calls.append(wire_req)
        return HttpResponse({"error": "test-http-secret denied"}, status)

    monkeypatch.setattr(request.OpenerDirector, "open", fake_open)
    with pytest.raises(OpenRouterError) as caught:
        OpenRouterProvider().complete(req)
    assert caught.value.status == status and "test-http-secret" not in str(caught.value)
    assert len(calls) == 1


def test_default_transport_disables_redirects(monkeypatch, req):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    original_build_opener = request.build_opener
    handlers_seen = []

    def inspect_opener(*handlers):
        handlers_seen.extend(handlers)
        return original_build_opener(*handlers)

    def fake_open(self, wire_req, timeout):
        handler, = handlers_seen
        assert handler.redirect_request(wire_req, None, 302, "", {}, "https://other.test") is None
        raise error.HTTPError(wire_req.full_url, 302, "redirect", {}, BytesIO(b"redirect"))

    monkeypatch.setattr(request, "build_opener", inspect_opener)
    monkeypatch.setattr(request.OpenerDirector, "open", fake_open)
    with pytest.raises(OpenRouterError) as caught:
        OpenRouterProvider().complete(req)
    assert caught.value.status == 302 and len(handlers_seen) == 1


def test_missing_key_fails_before_network(monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)

    def unexpected_open(*args, **kwargs):
        pytest.fail("network must not run without a key")

    monkeypatch.setattr(request.OpenerDirector, "open", unexpected_open)
    with pytest.raises(OpenRouterError, match="API key environment variable is not set"):
        OpenRouterProvider().catalogue()


@dataclass
class TinyWallet:
    balance: int
    reserved: int = 0
    log: list = field(default_factory=list)

    def reserve(self, amount, handle, reason):
        assert type(amount) is int and 0 <= amount <= self.balance - self.reserved
        self.reserved += amount
        self.log.append(("reserve", amount))
        return amount

    def commit(self, reservation, actual):
        assert type(actual) is int and 0 <= actual <= reservation
        self.reserved -= reservation
        self.balance -= actual
        self.log.append(("commit", actual))

    def commit_reported(self, reservation, actual):
        self.reserved -= reservation
        self.balance -= actual
        self.log.append(("commit", actual))

    def commit_uncertain(self, reservation):
        self.reserved -= reservation
        self.balance -= reservation
        self.log.append(("uncertain", reservation))

    def release(self, reservation):
        self.reserved -= reservation
        self.log.append(("release", reservation))


@pytest.mark.parametrize(
    "reported, expected, source", [(True, 13, "reported"), (False, 75, "table")]
)
def test_metered_model_cost_source(completion, req, reported, expected, source):
    if not reported:
        del completion["usage"]["cost"]
    wallet = TinyWallet(1000)
    prices = PriceTable({req.model_id: TokenPrice(1, 5)})
    provider = OpenRouterProvider(transport=FakeTransport([completion]))
    metered = MeteredModel(provider, prices, Meter(wallet))
    result = metered.complete(req, handle="decision-1")
    assert result.cost == expected and result.cost_source == source
    assert result.reserved == metered.ceiling(req)
    assert wallet.balance == 1000 - expected and wallet.reserved == 0
    assert wallet.log == [("reserve", result.reserved), ("commit", expected)]


@pytest.mark.parametrize("cost, expected", [(0, 0), (0.005, 5000)])
def test_reported_zero_and_overrun_preserve_accounting(completion, req, cost, expected):
    completion["usage"]["cost"] = cost
    completion["model"] = "unregistered/serving-model"
    wallet = TinyWallet(1000)
    model = MeteredModel(
        OpenRouterProvider(transport=FakeTransport([completion])),
        PriceTable({req.model_id: TokenPrice(1, 5)}), Meter(wallet),
    )
    result = model.complete(req, handle="overrun")
    assert result.cost_source == "reported"
    assert result.cost == expected
    assert result.overrun == max(0, expected - model.ceiling(req))
    assert wallet.balance == 1000 - result.cost and wallet.reserved == 0


@pytest.mark.parametrize("registered, expected", [(True, 45), (False, 75)])
def test_table_fallback_prices_serving_model_when_registered(completion, req, registered, expected):
    del completion["usage"]["cost"]
    completion["model"] = "another/serving-model"
    prices = PriceTable({req.model_id: TokenPrice(1, 5)})
    if registered:
        prices.register(completion["model"], TokenPrice(1, 2))
    model = MeteredModel(
        OpenRouterProvider(transport=FakeTransport([completion])), prices, Meter(TinyWallet(1000))
    )
    result = model.complete(req, handle="fallback")
    assert result.cost == expected and result.cost_source == "table"


@pytest.mark.network
@pytest.mark.skipif(
    not os.environ.get("OPENROUTER_API_KEY"), reason="OPENROUTER_API_KEY is not set"
)
def test_live_catalogue_has_seed_flash_models():
    catalogue = {entry.id: entry for entry in OpenRouterProvider().catalogue()}
    for model_id in ("z-ai/glm-5.3-flash", "deepseek/deepseek-v4.1-flash"):
        assert model_id in catalogue
        price = catalogue[model_id].price()
        assert price.input_micro > 0 and price.output_micro > 0
