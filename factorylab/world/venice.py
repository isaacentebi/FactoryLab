"""Venice inference reports integer costs and explicit estimates without wallet access."""

from __future__ import annotations

import base64
import json
import os
from collections.abc import Callable, Iterable, Mapping
from copy import deepcopy
from decimal import Decimal
from typing import Any
from urllib import error

from factorylab.kernel.money import nonnegative_usd_micro
from factorylab.world.models import CatalogueEntry, ModelRequest, ModelResponse, TokenPrice
from factorylab.world.openai_wire import (
    CALL_EXPIRED,
    dispatched,
    expired,
    parse_completion,
    response_format,
)
from factorylab.world.x402 import VENICE_URL, X402Client, http_request, redact

#: How much of a completion's `reasoning_content` the diary keeps. Enough to see
#: what the model actually said when it answered in prose it never returned as
#: content, and bounded so a runaway thought cannot fill the ledger.
MAX_REASONING_CHARS = 2_000


def _positive_int(value: Any) -> int | None:
    """Return provider metadata only when it is a positive, non-boolean integer."""
    return value if type(value) is int and value > 0 else None


def _pinned(pay_to: str | None, payee: Any) -> None:
    """Refuse a quote or authorization naming any payee but the pinned one, when pinned."""
    from factorylab.world.x402 import X402Error

    if pay_to is not None and str(payee).lower() != pay_to.lower():
        raise X402Error("Venice quote payee differs from treasury.venice_pay_to")


def prepare_top_up(client: X402Client, *, now_s: int, nonce: bytes,
                   pay_to: str | None = None) -> dict:
    """Fix a $5 quote and unsigned authorization before the treasury reserves and journals it.

    ``pay_to`` pins the payee: a quote paying anyone else is refused before an
    authorization is even built (the hybrid rail's ``treasury.venice_pay_to``).
    """
    from factorylab.world.x402 import TOP_UP_MICRO, authorization_typed_data, parse_quote

    if client.usdc_balance() < TOP_UP_MICRO:
        raise ValueError("insufficient Base USDC for a $5 Venice top-up")
    credit = client.venice_balance()
    quote = parse_quote(client._request("POST", "/x402/top-up", {}), amount_micro=TOP_UP_MICRO)
    _pinned(pay_to, quote.accepted["payTo"])
    typed = authorization_typed_data(quote.accepted, client.address, now=now_s, nonce=nonce)
    return {"accepted": quote.accepted, "resource": quote.resource, "extensions": quote.extensions,
            "created_s": now_s, "authorization": typed["message"], "credit_before_micro": credit}


def top_up(client: X402Client, reference: dict, *, pay_to: str | None = None) -> dict:
    """The existing x402 transport signs and submits only the journal's exact authorization.

    The CLI client's one-shot method generates a new nonce per call. Treasury retries
    instead reconstruct this fixed nonce and expiry, so an ambiguous reply cannot
    authorize another $5. References contain no signature or private signing material.
    With ``pay_to`` pinned, a reference paying anyone else is refused before signing.
    """
    _pinned(pay_to, reference["accepted"].get("payTo"))
    _pinned(pay_to, reference["authorization"].get("to"))
    from eth_account.messages import encode_typed_data

    from factorylab.world.x402 import (
        BASE_NETWORK,
        TOP_UP_MICRO,
        PaymentQuote,
        X402Error,
        _decode,
        _header,
        authorization_typed_data,
    )

    quote = PaymentQuote(reference["accepted"], reference["resource"], reference["extensions"])
    if quote.amount_micro != TOP_UP_MICRO:
        raise X402Error("only a $5 Venice top-up is supported")
    typed = authorization_typed_data(
        quote.accepted, client.address, now=reference["created_s"],
        nonce=bytes.fromhex(reference["authorization"]["nonce"].removeprefix("0x")),
    )
    if typed["message"] != reference["authorization"]:
        raise X402Error("Venice authorization differs from the journal")
    signature = client._account.sign_message(encode_typed_data(full_message=typed))
    authorization = {k: str(v) if k in ("value", "validAfter", "validBefore") else v
                     for k, v in typed["message"].items()}
    envelope = {"x402Version": 2, "accepted": quote.accepted,
                "payload": {"signature": "0x" + signature.signature.hex(),
                            "authorization": authorization}}
    for key in ("resource", "extensions"):
        if reference[key] is not None:
            envelope[key] = reference[key]
    encoded = base64.b64encode(json.dumps(envelope, separators=(",", ":")).encode()).decode()
    response = client._request("POST", "/x402/top-up", {}, **{"X-402-Payment": encoded})
    if not 200 <= response.status < 300:
        raise X402Error("Venice top-up outcome unknown; reconcile the existing authorization")
    header = _header(response.headers, "payment-response", "x-payment-response")
    settlement = _decode(header) if header else response.body
    data = settlement.get("data", settlement)
    if (not isinstance(data, dict) or settlement.get("success") is False
            or data.get("success") is False or data.get("network", BASE_NETWORK) != BASE_NETWORK
            or str(data.get("payer", client.address)).lower() != client.address.lower()):
        raise X402Error("Venice settlement identity unavailable; reconcile the authorization")
    # Only a public receipt reference and observed balance leave this adapter.
    return {"transaction": data.get("transaction", data.get("transactionHash")),
            "credit_after_micro": client.venice_balance()}


class VeniceError(Exception):
    """Provider failures retain HTTP status but never transport bodies or credentials.

    ``sent`` is False only with definitive evidence the request body never left this
    process; it stays True whenever the provider may already have billed the call.
    """

    def __init__(self, status: int | None, message: str, *, sent: bool = True) -> None:
        self.status = status
        self.sent = sent
        super().__init__(f"Venice error ({status}): {message}")


class VeniceProvider:
    """Completions make one billed POST; only connection-failed GETs retry once.

    ``raw.cost_source`` is ``reported`` for cost.usd, or ``table`` for a catalogue
    estimate. Both yield integer ``cost_micro`` so existing metering charges the
    amount; its generic populated-cost flag does not distinguish these sources.
    """

    name = "venice"

    def __init__(
        self,
        *,
        key_env: str = "VENICE_API_KEY",
        base_url: str = VENICE_URL,
        transport: Callable[[str, str, dict | None], dict] | None = None,
        reasoning_models: Iterable[str] = (),
        reasoning_config: Mapping[str, Mapping[str, Any]] | None = None,
        web_config: Mapping[str, Mapping[str, Any]] | None = None,
        schema_models: Iterable[str] = (),
    ) -> None:
        self._key_env = key_env
        self._base_url = base_url.rstrip("/")
        self._transport = transport or self._default_transport
        # The deadline of the completion in flight (``ModelRequest.timeout_s``), set
        # only for the duration of that one call.
        self._call_timeout: float | None = None
        self._reasoning_models = frozenset(reasoning_models)
        self._reasoning_config = deepcopy(dict(reasoning_config or {}))
        self._web_config = deepcopy(dict(web_config or {}))
        self._prices: dict[str, TokenPrice] = {}
        # The model ids whose manifest ``contract`` is ``json_schema`` (Chapter II §II.b).
        self._schema_models = frozenset(schema_models)

    def _default_transport(self, method: str, path: str, payload: dict | None) -> dict:
        key = os.environ.get(self._key_env)
        if key:
            headers = {"Authorization": f"Bearer {key}"}
        elif os.environ.get("RESERVE_PRIVATE_KEY"):
            headers = X402Client(base_url=self._base_url).auth_headers(path)
        elif method == "GET" and path == "/models":
            headers = {}  # The Venice catalogue is public.
        else:
            raise VeniceError(None, "Set VENICE_API_KEY or RESERVE_PRIVATE_KEY", sent=False)
        response = http_request(method, self._base_url + path, payload, headers,
                                timeout=self._call_timeout)
        if not 200 <= response.status < 300:
            raise VeniceError(response.status, "HTTP request failed")
        return response.body

    def _request(self, method: str, path: str, payload: dict | None = None) -> dict:
        for attempt in range(2 if method == "GET" else 1):
            try:
                response = self._transport(method, path, payload)
                return redact(
                    response,
                    (
                        os.environ.get(self._key_env, ""),
                        os.environ.get("RESERVE_PRIVATE_KEY", ""),
                    ),
                )
            except error.HTTPError as exc:
                exc.close()
                raise VeniceError(exc.code, "HTTP request failed") from None
            except VeniceError as exc:
                # Even injected provider exceptions must not echo request credentials.
                raise VeniceError(
                    exc.status, "Request failed; check authentication and status", sent=exc.sent
                ) from None
            except (error.URLError, ConnectionError, TimeoutError) as exc:
                if method != "GET" or attempt == 1:
                    raise VeniceError(None, CALL_EXPIRED if expired(exc) else "Connection failed",
                                      sent=dispatched(exc)) from None
            except Exception as exc:
                raise VeniceError(
                    None, "Transport or response decoding failed", sent=dispatched(exc)
                ) from None
        raise AssertionError("unreachable")

    def _configuration(self, req: ModelRequest) -> tuple[str, dict, dict]:
        if not req.model_id.startswith("venice:"):
            raise VeniceError(None, "Venice model ids must start with venice:")
        tier, _, override = req.model_id.partition("@")
        base = tier.removesuffix(":online")
        reasoning = dict(
            self._reasoning_config.get(
                req.model_id, self._reasoning_config.get(tier, self._reasoning_config.get(base, {}))
            )
        )
        if override:
            reasoning = {"effort": override}
        elif not reasoning and base in self._reasoning_models:
            reasoning = {"effort": req.effort}
        payload: dict[str, Any] = {}
        params: dict[str, Any] = {}
        metadata: dict[str, Any] = {}
        effort = reasoning.get("effort")
        if reasoning.get("enabled") is False or effort == "none":
            # Venice's own toggle: it withholds reasoning parameters from the upstream
            # model instead of forwarding an effort. ``reasoning_effort: none`` is
            # forwarded, may become the model's minimum effort, and is ignored by
            # ``reasoning.enabled`` (an effort level takes precedence over it). The
            # recorded probe with ``reasoning_effort: none`` still spent the whole
            # budget on hidden reasoning; this shape is the documented switch.
            payload["reasoning"] = {"enabled": False}
            params["disable_thinking"] = True
            metadata["thinking"] = "disabled"
        else:
            if "max_tokens" in reasoning:
                effort = "low"
                metadata["reasoning_substitution"] = {
                    "requested_max_tokens": reasoning["max_tokens"],
                    "reasoning_effort": "low",
                    "reason": "token_budget_unsupported",
                }
            if effort is not None:
                if effort not in {"minimal", "low", "medium", "high", "xhigh", "max"}:
                    raise VeniceError(None, "Unsupported reasoning effort")
                payload["reasoning_effort"] = effort
                params["disable_thinking"] = False
            elif reasoning.get("enabled") is True:
                params["disable_thinking"] = False
        web = next(
            (self._web_config[k] for k in (req.model_id, tier, base) if k in self._web_config), None
        )
        if web is not None:
            mode = web.get("enable_web_search", "auto")
            if mode not in {"auto", "on", "off"}:
                raise VeniceError(None, "Unsupported web search mode")
            params["enable_web_search"] = mode
            metadata["web_search"] = mode
        if params:
            payload["venice_parameters"] = params
        return base.removeprefix("venice:"), payload, metadata

    def complete(
        self,
        req: ModelRequest,
        *,
        tools: Iterable[dict] | None = None,
        tool_choice: str | dict | None = None,
        parallel_tool_calls: bool | None = None,
    ) -> ModelResponse:
        """Usage, cost provenance, tool calls and reasoning substitutions survive one POST.

        Optional tool keywords extend the unchanged ModelRequest protocol; assistant
        tool calls and tool-result messages also pass through unchanged.
        """
        wire_id, options, raw = self._configuration(req)
        payload = {
            "model": wire_id,
            "messages": [{"role": "system", "content": req.system}, *req.messages],
            "max_tokens": req.max_tokens,
            **options,
        }
        # Venice speaks the OpenAI wire: a request for a JSON object says so there, and
        # on a route whose manifest contract is json_schema it carries the schema.
        tier = req.model_id.partition("@")[0]
        contract = response_format(req, schema_route=any(
            k in self._schema_models for k in (req.model_id, tier, tier.removesuffix(":online"))))
        if contract is not None:
            payload["response_format"] = contract
        if tools is not None:
            payload["tools"] = list(tools)
        if tool_choice is not None:
            payload["tool_choice"] = tool_choice
        if parallel_tool_calls is not None:
            payload["parallel_tool_calls"] = parallel_tool_calls
        self._call_timeout = req.timeout_s
        try:
            response = self._request("POST", "/chat/completions", payload)
        finally:
            self._call_timeout = None
        wire = parse_completion(response, error=VeniceError)
        try:
            serving_id = "venice:" + (wire.model or wire_id).removeprefix("venice:")
            reported = (response.get("cost") or {}).get("usd")
            if reported is not None:
                cost = nonnegative_usd_micro(reported, rounding="ceil")
                raw["cost_source"] = "reported"
            else:
                if serving_id not in self._prices:
                    self.catalogue()
                price_id = serving_id if serving_id in self._prices else "venice:" + wire_id
                cost = self._prices[price_id].cost(wire.input_tokens, wire.output_tokens)
                raw.update(cost_source="table", price_model_id=price_id, cost_scope="tokens_only")
            if wire.request_id is not None:
                raw["request_id"] = wire.request_id
            if wire.reasoning_tokens is not None:
                raw["reasoning_tokens"] = wire.reasoning_tokens
            if wire.cached_tokens is not None:
                raw["cached_tokens"] = wire.cached_tokens
            for key in ("tool_calls", "reasoning_content", "reasoning_details"):
                if key in wire.message:
                    value = wire.message[key]
                    raw[key] = (value[:MAX_REASONING_CHARS]
                                if key == "reasoning_content" and isinstance(value, str)
                                else value)
            stop_reason = wire.stop_reason
            if not wire.text and raw.get("reasoning_content") and not raw.get("tool_calls"):
                # The model spent its whole output budget thinking and answered
                # nothing. The answer is malformed either way -- there is no
                # content to parse -- but the diary should say why rather than
                # record an empty `stop`: the reasoning it kept is the evidence.
                # A message that called a tool answered with the call, not with
                # content, and is not this failure.
                stop_reason = "reasoning_only"
            return ModelResponse(
                serving_id,
                wire.text,
                wire.input_tokens,
                wire.output_tokens,
                stop_reason,
                False,
                raw,
                cost,
            )
        except VeniceError:
            raise
        except Exception:
            raise VeniceError(None, "Invalid completion, cost or catalogue price") from None

    def catalogue(self) -> list[CatalogueEntry]:
        """Text model ids are namespaced and fractional per-Mtok prices stay exact."""
        response = self._request("GET", "/models")
        try:
            entries = []
            for model in response["data"]:
                if model.get("type", "text") != "text":
                    continue
                spec = model["model_spec"]
                prices = spec["pricing"]
                quotes = []
                for key in ("input", "output"):
                    amount = Decimal(str(prices[key]["usd"]))
                    if not amount.is_finite() or amount < 0:
                        raise ValueError
                    # Change only the decimal exponent; no context rounding of long quotes.
                    sign, digits, exponent = amount.as_tuple()
                    quotes.append(str(Decimal((sign, digits, exponent - 6))))
                entries.append(
                    CatalogueEntry(
                        id="venice:" + model["id"].removeprefix("venice:"),
                        name=model.get("name") or spec.get("name") or model["id"],
                        prompt_usd_per_token=quotes[0],
                        completion_usd_per_token=quotes[1],
                        context_length=spec.get("availableContextTokens"),
                        max_completion_tokens=_positive_int(spec.get("maxCompletionTokens")),
                    )
                )
            self._prices = {entry.id: entry.price() for entry in entries}
            return entries
        except Exception:
            raise VeniceError(None, "Invalid catalogue") from None

    def balance_micro(self) -> int | None:
        """Wallet authentication exposes wallet credits; API-key credit scope is unknown."""
        if os.environ.get(self._key_env):
            return None
        return X402Client(base_url=self._base_url).venice_balance()

    def balance_of(self, model_id: str) -> int | None:
        """The balance that pays for a Venice model is the wallet's Venice credit."""
        return self.balance_micro()


class VeniceAndOpenRouter:
    """Namespaced model ids dispatch to Venice; all other ids dispatch to OpenRouter."""

    name = "venice+openrouter"

    def __init__(self, venice: VeniceProvider, openrouter: Any) -> None:
        self._venice = venice
        self._openrouter = openrouter

    def complete(self, req: ModelRequest) -> ModelResponse:
        """Exactly one provider receives each completion."""
        provider = self._venice if req.model_id.startswith("venice:") else self._openrouter
        return provider.complete(req)

    def catalogue(self) -> list[CatalogueEntry]:
        """Both catalogues coexist with disjoint Venice-prefixed identities."""
        return [*self._openrouter.catalogue(), *self._venice.catalogue()]

    def balance_of(self, model_id: str) -> int | None:
        """The balance of the one provider that pays for ``model_id``; None when unbounded.

        Read lazily by the bill settlement: before it settles an uncertain bill and
        at launch. An x402 seller is paid per request from the reserve and has no
        balance to settle against.
        """
        if model_id.startswith("x402:"):
            return None
        provider = self._venice if model_id.startswith("venice:") else self._openrouter
        if provider is None or not hasattr(provider, "balance_micro"):
            return None
        return provider.balance_micro()
