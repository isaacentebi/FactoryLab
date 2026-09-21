import traceback
from dataclasses import dataclass, field
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
        "top_provider": {"max_completion_tokens": 16384},
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


def test_catalogue_preserves_decimal_quotes_and_rounds_total_up(catalogue):
    transport = FakeTransport([catalogue])
    entry, = OpenRouterProvider(transport=transport).catalogue()
    assert transport.calls == [("GET", "/models", None)]
    assert (entry.id, entry.name, entry.context_length, entry.max_completion_tokens) == (
        "test/flash", "Test Flash", 8192, 16384,
    )
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


@pytest.mark.parametrize("advertised", [None, 0, -1, True, 1.5, "16384", [], {}])
def test_catalogue_ignores_invalid_completion_limits(catalogue, advertised):
    catalogue["data"][0]["top_provider"]["max_completion_tokens"] = advertised
    entry, = OpenRouterProvider(transport=FakeTransport([catalogue])).catalogue()
    assert entry.max_completion_tokens is None


def test_catalogue_does_not_infer_completion_limit_from_context(catalogue):
    catalogue["data"][0].pop("top_provider")
    entry, = OpenRouterProvider(transport=FakeTransport([catalogue])).catalogue()
    assert entry.context_length == 8192
    assert entry.max_completion_tokens is None


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


def test_default_transport_disables_redirects(monkeypatch, req):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    original_build_opener = request.build_opener
    handlers_seen = []

    def inspect_opener(*handlers):
        handlers_seen.extend(handlers)
        return original_build_opener(*handlers)

    def fake_open(self, wire_req, timeout):
        assert timeout is None  # Model processing has no client thinking deadline.
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
