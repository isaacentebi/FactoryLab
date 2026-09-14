"""Public seller discovery and bounded, per-request Base USDC inference purchases."""

from __future__ import annotations

import json
import os
from collections.abc import Callable, Mapping
from copy import deepcopy
from dataclasses import asdict, dataclass
from decimal import Decimal, InvalidOperation
from typing import Any
from urllib import parse

from factorylab.world.metering import BillingUncertain, Infeasible, Metered, MeteredModel
from factorylab.world.models import CatalogueEntry, ModelRequest, ModelResponse, TokenPrice
from factorylab.world.venice import VeniceAndOpenRouter
from factorylab.world.x402 import (
    BASE_NETWORK,
    BASE_RPC,
    HTTPResponse,
    InsufficientReserve,
    PaymentQuote,
    Transport,
    X402Client,
    X402Error,
    _decode,
    _header,
    http_request,
    parse_quote,
    redact,
)

DISCOVERY_URL = "https://api.cdp.coinbase.com/platform/v2/x402/discovery/resources"


class PaymentOutcomeUnknown(X402Error):
    """A submitted authorization may have settled; its provisional debit requires reconciliation."""


def metered_data(meter, handle: str, ceiling: int, execute, record) -> tuple[dict, int]:
    """Data costs debit before return; unknown payments remain provisional and reconcilable."""
    reserve_before = None

    def evidence(item):
        nonlocal reserve_before
        if item["kind"] == "x402.reserve_before":
            reserve_before = item["reserve_micro"]
        record({**item, "handle": handle})

    reservation = meter.wallet.reserve(ceiling, handle, "tool:connector.x402")
    try:
        result = execute(evidence)
    except PaymentOutcomeUnknown:
        meter.wallet.commit_uncertain(reservation)
        evidence({"kind": "x402.unresolved", "reserved_micro": ceiling,
                  "reservation_id": reservation.id, "reserve_before_micro": reserve_before})
        return {"error": "data payment outcome is unknown", "status": "uncertain",
                "bytes": 0, "cost_micro": ceiling}, ceiling
    except Exception:
        meter.wallet.release(reservation)
        return {"error": "data read refused before payment", "status": "refused",
                "bytes": 0, "cost_micro": 0}, 0
    cost = result["cost_micro"]
    meter.wallet.commit(reservation, cost)
    return result, cost


def seller_root(seller_url: str) -> str:
    """A seller URL has no embedded credentials, query, fragment or ambiguous API suffix."""
    if not isinstance(seller_url, str) or any(c.isspace() for c in seller_url):
        raise X402Error("Invalid seller URL")
    try:
        url = parse.urlsplit(seller_url)
        if (
            url.scheme not in {"https", "http"} or not url.hostname or url.port == 0
            or url.username is not None or url.password is not None or url.query or url.fragment
        ):
            raise ValueError
    except ValueError:
        raise X402Error("Seller URL requires HTTP(S) and no credentials/query/fragment") from None
    return seller_url.rstrip("/").removesuffix("/chat/completions").removesuffix("/v1")


def split_model_id(model_id: str) -> tuple[str, str]:
    """An x402 capability identifies exactly one seller root and one opaque model id."""
    if not isinstance(model_id, str) or not model_id.startswith("x402:"):
        raise X402Error("Model id must be x402:<seller_url>#<model>")
    seller, sep, model = model_id[5:].partition("#")
    if not sep or not model or "#" in model or any(c.isspace() for c in model):
        raise X402Error("Model id must be x402:<seller_url>#<model>")
    if len(model_id) > 4096:
        raise X402Error("x402 model id exceeds 4096 characters")
    return seller_root(seller), model


def _request(transport: Transport, method: str, url: str, payload=None, headers=None):
    try:
        return transport(method, url, payload, headers or {})
    except Exception:
        raise X402Error("Market HTTP transport or response decoding failed") from None


def _discovery_index(*, transport: Transport, discovery_url: str,
                     page_budget: int = 5) -> list[dict]:
    """At most five pages enter the index; repeated resources cannot keep paging alive."""
    out, seen = [], set()
    offset = 0
    for _ in range(page_budget):
        params = parse.urlencode({"type": "http", "limit": 100, "offset": offset})
        response = _request(transport, "GET", discovery_url + "?" + params)
        if response.status != 200:
            raise X402Error(f"Discovery failed (HTTP {response.status})")
        items = response.body.get("items")
        pagination = response.body.get("pagination", {})
        if not isinstance(items, list) or not isinstance(pagination, dict) or len(items) > 100:
            raise X402Error("Invalid discovery page")
        page_offset = pagination.get("offset", offset)
        page_limit = pagination.get("limit", 100)
        total = pagination.get("total")
        if (
            type(page_offset) is not int or page_offset != offset
            or type(page_limit) is not int or not 1 <= page_limit <= 100
            or (total is not None and (type(total) is not int or total < 0))
        ):
            raise X402Error("Invalid discovery pagination")
        before = len(seen)
        for item in items:
            if not isinstance(item, dict) or not isinstance(item.get("resource"), str):
                continue
            resource = item["resource"]
            if resource in seen:
                continue
            seen.add(resource)
            description = item.get("description") or ""
            if not isinstance(description, str):
                description = ""
            accepts = item.get("accepts") or []
            if not isinstance(accepts, list):
                continue
            out.append({
                "resource": resource,
                "accepts": [
                    {"network": a.get("network"), "asset": a.get("asset"),
                     "price": a.get("amount", a.get("maxAmountRequired")),
                     "scheme": a.get("scheme")}
                    for a in accepts if isinstance(a, dict)
                ],
                "description": description[:1000],
            })
        offset += page_limit
        if (len(seen) == before or (total is not None and offset >= total)
                or (total is None and len(items) < page_limit)):
            break
    return out


def filter_sellers(index: list[dict], url_substring=None, query=None, limit=20) -> list[dict]:
    """Filters apply to a detached bounded index without another network read."""
    if type(limit) is not int or not 1 <= limit <= 100:
        raise X402Error("Discovery limit must be an integer in [1, 100]")
    if any(v is not None and not isinstance(v, str) for v in (url_substring, query)):
        raise X402Error("Discovery filters must be strings")
    return deepcopy([
        row for row in index
        if (not url_substring or url_substring.casefold() in row["resource"].casefold())
        and (not query or query.casefold() in
             (row["resource"] + " " + row["description"]).casefold())
    ][:limit])


def discover(url_substring=None, query=None, limit=20, *, transport: Transport | None = None,
             discovery_url: str = DISCOVERY_URL) -> list[dict]:
    """A discovery has a fixed page budget and exact base-unit price strings."""
    filter_sellers([], url_substring, query, limit)  # validate before any I/O
    index = _discovery_index(transport=transport or http_request, discovery_url=discovery_url)
    return filter_sellers(index, url_substring, query, limit)


@dataclass(frozen=True)
class SellerModel:
    """Unpriced model identities survive discovery; listed token prices remain exact."""

    id: str
    name: str
    context_length: int | None
    pricing: CatalogueEntry | None = None


def seller_models(seller_url: str, *, transport: Transport | None = None) -> list[SellerModel]:
    """A public /v1/models catalogue never triggers a payment; absent catalogues yield []."""
    response = _request(transport or http_request, "GET", seller_root(seller_url) + "/v1/models")
    if response.status in {401, 402, 403, 404, 405}:
        return []
    if response.status != 200:
        raise X402Error(f"Seller catalogue failed (HTTP {response.status})")
    try:
        entries = response.body["data"]
        if not isinstance(entries, list):
            raise ValueError
        out = []
        for entry in entries:
            model_id = entry["id"]
            if not isinstance(model_id, str) or not model_id:
                raise ValueError
            context = entry.get("context_length")
            if context is not None and (type(context) is not int or context < 1):
                raise ValueError
            pricing = entry.get("pricing") or {}
            priced = None
            # OpenRouter-compatible USD/token quotes; explicit input/output aliases also work.
            prompt = pricing.get("prompt", pricing.get("input"))
            completion = pricing.get("completion", pricing.get("output"))
            if prompt is not None and completion is not None:
                quotes = [Decimal(str(v)) for v in (prompt, completion)]
                if any(not v.is_finite() or v < 0 for v in quotes):
                    raise ValueError
                priced = CatalogueEntry(
                    model_id, entry.get("name", model_id), str(quotes[0]), str(quotes[1]), context,
                )
            out.append(SellerModel(model_id, entry.get("name", model_id), context, priced))
        return out
    except (KeyError, TypeError, ValueError, AttributeError, InvalidOperation):
        raise X402Error("Invalid seller model catalogue or per-token price") from None


class _ObservedReserveClient(X402Client):
    """The same affordability read used by signing is recorded before authorization."""

    def __init__(self, *, record=None, **kwargs):
        super().__init__(**kwargs)
        self._record_balance = record

    def usdc_balance(self, address=None) -> int:
        balance = super().usdc_balance(address)
        if self._record_balance is not None:
            self._record_balance({"kind": "x402.reserve_before", "reserve_micro": balance})
        return balance


class X402Provider:
    """Each call signs at most one bounded authorization; quoted USDC is the entire cost."""

    name = "x402"

    def __init__(
        self,
        *,
        private_key: str | None = None,
        transport: Transport | None = None,
        rpc: str = BASE_RPC,
        discovery_url: str = DISCOVERY_URL,
        extra_body: Mapping[str, Any] | None = None,
        max_request_micro: int = 500_000,
    ) -> None:
        if type(max_request_micro) is not int or max_request_micro < 0:
            raise X402Error("Request cap must be nonnegative integer micro-USD")
        self.max_request_micro = max_request_micro
        self._private_key = private_key
        self._transport = transport or http_request
        self.rpc = rpc
        self.discovery_url = discovery_url
        self._ceilings: dict[str, int] = {}
        self._extra_body = deepcopy(dict(extra_body or {}))
        if self._extra_body.keys() & {"model", "messages", "max_tokens", "stream"}:
            raise X402Error("Extra body cannot override the bounded completion request")

    def _client(self, *, record=None) -> X402Client:
        return _ObservedReserveClient(private_key=self._private_key, rpc=self.rpc,
                                      transport=self._transport, record=record)

    def _clean(self, value: Any) -> Any:
        clean = redact(value, (self._private_key or os.environ.get("RESERVE_PRIVATE_KEY", ""),))
        return json.loads(json.dumps(clean, default=str))

    def register(self, model_id: str, ceiling_micro: int) -> None:
        """A registered capability has one immutable nonnegative micro-USD ceiling."""
        split_model_id(model_id)
        if type(ceiling_micro) is not int or ceiling_micro < 0:
            raise X402Error("Per-request ceiling must be nonnegative integer micro-USD")
        if ceiling_micro > self.max_request_micro:
            raise X402Error("Per-request ceiling exceeds treasury.max_request_micro")
        if model_id in self._ceilings and self._ceilings[model_id] != ceiling_micro:
            raise X402Error("Registered per-request ceiling is immutable")
        self._ceilings[model_id] = ceiling_micro

    def discover(self, url_substring=None, query=None, limit=20) -> list[dict]:
        """Discovery uses only this provider's injected transport and configured index."""
        return discover(url_substring, query, limit, transport=self._transport,
                        discovery_url=self.discovery_url)

    def discover_index(self) -> list[dict]:
        """Return the bounded index for one runtime window's local searches."""
        return _discovery_index(transport=self._transport, discovery_url=self.discovery_url)

    def reserve_balance(self) -> int:
        """Return an observed Base USDC balance without authorizing a payment."""
        return self._client().usdc_balance()

    def fetch_data(self, origin: str, path: str, ceiling_micro: int, *, transport,
                   record=None) -> dict:
        """A bounded data GET signs once at most and uses the inference reserve wallet.

        The caller supplies the connector's pinned transport, never a population URL
        transport. Any failure after submission retains an uncertain debit.
        """
        if type(ceiling_micro) is not int or not 0 <= ceiling_micro <= self.max_request_micro:
            raise X402Error("Data cap exceeds treasury.max_request_micro")
        response = transport(origin, path)
        cost = 0
        if response.status == 402:
            body = json.loads(response.body, parse_float=Decimal) if response.body else {}
            quote = parse_quote(HTTPResponse(402, body, response.headers))
            if quote.amount_micro > ceiling_micro:
                return {"error": "Quote exceeds connector per-call cap", "status": 402,
                        "bytes": 0, "cost_micro": 0}
            if record:
                record({"kind": "x402.quote", "quote": self._clean(asdict(quote))})
            client = self._client(record=record)
            signature = client.authorize(quote, ceiling_micro=ceiling_micro)
            if record:
                record({"kind": "x402.submitted", "amount_micro": quote.amount_micro})
            try:
                response = transport(origin, path, signature)
                header = _header(response.headers, "payment-response", "x-payment-response")
                if header is not None:
                    receipt = _decode(header)
                    if (receipt.get("success") is not True
                            or receipt.get("network", BASE_NETWORK) != BASE_NETWORK
                            or str(receipt.get("payer", client.address)).lower()
                            != client.address.lower()):
                        raise ValueError("invalid payment receipt")
                elif not 200 <= response.status < 300:
                    raise ValueError("payment outcome unavailable")
            except Exception:
                raise PaymentOutcomeUnknown("Data payment outcome is unknown") from None
            cost = quote.amount_micro
        result = {"body": response.body.decode("utf-8", errors="replace"),
                  "status": response.status, "bytes": len(response.body), "cost_micro": cost}
        if record:
            record({"kind": "x402.result", "cost_micro": cost, "http_status": response.status})
        return result

    def seller_models(self, seller_url: str) -> list[SellerModel]:
        """Seller catalogue reads use the same injected transport as inference."""
        return seller_models(seller_url, transport=self._transport)

    def _payload(self, req: ModelRequest) -> tuple[str, dict]:
        root, model = split_model_id(req.model_id)
        # A seller on the OpenAI wire receives the same JSON-object contract as
        # every other provider; the quote and the paid call carry identical bodies.
        contract = {"response_format": {"type": "json_object"}} if req.json_object else {}
        return root + "/v1/chat/completions", {
            "model": model, "messages": [{"role": "system", "content": req.system}, *req.messages],
            "max_tokens": req.max_tokens,
            **contract,
            **deepcopy(self._extra_body),
        }

    def quote(self, req: ModelRequest) -> PaymentQuote:
        """An unsigned request obtains a price without loading a key or authorizing payment."""
        url, payload = self._payload(req)
        return parse_quote(_request(self._transport, "POST", url, payload))

    def registration_price(self, model_id: str) -> tuple[TokenPrice, dict]:
        """A catalogue bound or unpaid quote yields a fixed per-request registration price.

        Token catalogues are bounded by their context length (4096 when absent)
        and 4096 output tokens, the largest population assembly output budget.
        Actual calls still require a quote no higher than this fixed ceiling.
        """
        seller, model = split_model_id(model_id)
        entry = next((e for e in self.seller_models(seller) if e.id == model), None)
        if entry is not None and entry.pricing is not None:
            input_tokens = entry.context_length or 4096
            ceiling = entry.pricing.price().cost(input_tokens, 4096)
            metadata = {"price_source": "seller-models", "token_prices": asdict(entry.pricing),
                        "input_token_bound": input_tokens, "output_token_bound": 4096}
        else:
            quote = self.quote(ModelRequest(
                model_id, "Reply briefly.", ({"role": "user", "content": "Reply with OK."},), 32,
            ))
            ceiling = quote.amount_micro
            metadata = {"price_source": "x402-quote", "quote": asdict(quote)}
        if ceiling > self.max_request_micro:
            raise X402Error("Per-request ceiling exceeds treasury.max_request_micro")
        return TokenPrice(0, 0, ceiling), self._clean({
            "seller": seller, "model": model, "network": BASE_NETWORK,
            "per_request_micro": ceiling, **metadata,
        })

    def affordable(self, model_id: str, ceiling_micro: int) -> tuple[bool, str]:
        """Affordability uses the reserve's Base USDC balance, never Venice credit endpoints."""
        split_model_id(model_id)
        balance = self._client().usdc_balance()
        return (True, "") if balance >= ceiling_micro else (
            False, f"compute: reserve {balance} below per-request ceiling {ceiling_micro}",
        )

    def complete(
        self, req: ModelRequest, *, record: Callable[[dict], Any] | None = None,
        quoted: PaymentQuote | None = None,
    ) -> ModelResponse:
        """A quote above the registered ceiling or reserve never reaches the signer.

        Only the unsigned request and one payment submission are sent. Successful
        paid responses remain chargeable even if their completion is malformed.
        Unknown submission outcomes raise separately so metering can book uncertainty.
        """
        if req.model_id not in self._ceilings:
            raise X402Error("x402 model has no registered per-request ceiling")
        url, payload = self._payload(req)
        response = (
            HTTPResponse(402, {"x402Version": 2, "accepts": [quoted.accepted],
                               **({"resource": quoted.resource}
                                  if quoted.resource is not None else {}),
                               **({"extensions": quoted.extensions}
                                  if quoted.extensions is not None else {})})
            if quoted is not None else _request(self._transport, "POST", url, payload)
        )
        quote, settlement = None, None
        if response.status == 402:
            quote = parse_quote(response)
            if quote.amount_micro > self._ceilings[req.model_id]:
                raise X402Error("Quote exceeds registered per-request ceiling")
            if record:
                record({"kind": "x402.quote", "model_id": req.model_id,
                        "quote": self._clean(asdict(quote))})
            client = self._client(record=record)
            encoded = client.authorize(quote, ceiling_micro=self._ceilings[req.model_id])
            if record:
                record({"kind": "x402.submitted", "model_id": req.model_id,
                        "amount_micro": quote.amount_micro})
            try:
                response = _request(self._transport, "POST", url, payload,
                                    {"PAYMENT-SIGNATURE": encoded})
            except X402Error:
                raise PaymentOutcomeUnknown("Submitted payment outcome is unknown") from None
            header = _header(response.headers, "payment-response", "x-payment-response")
            if header is not None:
                try:
                    settlement = _decode(header)
                    if settlement.get("success") is False:
                        raise X402Error("Seller reported failed payment settlement")
                    if (
                        settlement.get("network", BASE_NETWORK) != BASE_NETWORK
                        or settlement.get("success") is not True
                        or str(settlement.get("payer", client.address)).lower()
                        != client.address.lower()
                    ):
                        raise ValueError
                except X402Error:
                    if settlement is not None and settlement.get("success") is False:
                        raise
                    raise PaymentOutcomeUnknown("Invalid payment settlement receipt") from None
                except ValueError:
                    raise PaymentOutcomeUnknown("Invalid payment settlement receipt") from None
            if not 200 <= response.status < 300 and settlement is None:
                if response.status == 402:
                    raise X402Error("Seller refused the payment; no retry")
                raise PaymentOutcomeUnknown("Payment submitted; seller returned no settlement")
        elif not 200 <= response.status < 300:
            raise X402Error(f"Seller request failed (HTTP {response.status})")
        cost = quote.amount_micro if quote else 0
        body = self._clean(response.body)
        raw = {"cost_source": "x402-quote", "quote": self._clean(asdict(quote)) if quote else None,
               "settlement": self._clean(settlement), "http_status": response.status}
        for key in ("usage", "pricing", "cost", "model"):
            if key in body:
                raw[key] = body[key]
        if "id" in body:
            raw["request_id"] = body["id"]
        if record:
            record({"kind": "x402.result", "model_id": req.model_id,
                    "cost_micro": cost, **raw})
        # Parsing failure cannot unpay a settled authorization.
        text, stop, itok, otok = "", "malformed", 0, 0
        try:
            choice = body["choices"][0]
            content = choice["message"].get("content") or ""
            if isinstance(content, list):
                content = "".join(p["text"] for p in content if p.get("type") == "text")
            if not isinstance(content, str):
                raise ValueError
            usage = body.get("usage") or {}
            itok, otok = usage.get("prompt_tokens", 0), usage.get("completion_tokens", 0)
            if any(type(n) is not int or n < 0 for n in (itok, otok)):
                raise ValueError
            text, stop = content, choice.get("finish_reason") or ""
        except (KeyError, IndexError, TypeError, ValueError, AttributeError):
            itok, otok = 0, 0
        return ModelResponse(req.model_id, text, itok, otok, stop, raw=raw, cost_micro=cost)


class X402MeteredModel(MeteredModel):
    """Paid and uncertain amounts commit before return; no payment leaks an open hold."""

    def __init__(self, provider, prices, meter, *, record, on_unaffordable) -> None:
        super().__init__(provider, prices, meter)
        self.record = record
        self.on_unaffordable = on_unaffordable

    def complete(self, req: ModelRequest, *, handle: str) -> Metered[ModelResponse]:
        """Every payment has handle-linked ledger evidence, without any signing material."""
        reserve_before = None

        def record(item):
            nonlocal reserve_before
            if item["kind"] == "x402.reserve_before":
                reserve_before = item["reserve_micro"]
            self.record({**item, "handle": handle})

        ceiling = self.ceiling(req)
        quote = self.provider.quote(req)
        if quote.amount_micro > ceiling:
            raise X402Error("Quote exceeds registered per-request ceiling")
        # Catalogue prices are admission bounds. The actual wallet reservation is
        # the current quote, obtained without payment before the wallet is touched.
        reserved = quote.amount_micro
        try:
            reservation = self.meter.wallet.reserve(reserved, handle, f"model:{req.model_id}")
        except Exception as exc:
            self.on_unaffordable(handle)
            raise Infeasible(str(exc)) from None
        try:
            response = self.provider.complete(req, record=record, quoted=quote)
        except PaymentOutcomeUnknown as exc:
            self.meter.wallet.commit_uncertain(reservation)
            record({"kind": "x402.unresolved", "reserved_micro": reserved,
                    "reservation_id": reservation.id, "reserve_before_micro": reserve_before})
            raise BillingUncertain(reserved, exc) from None
        except Exception as exc:
            self.meter.wallet.release(reservation)
            if isinstance(exc, InsufficientReserve):
                self.on_unaffordable(handle)
            raise
        self.meter.wallet.commit(reservation, response.cost_micro)
        return Metered(response, response.cost_micro, reserved, handle, cost_source="x402-quote")


class MultiProvider(VeniceAndOpenRouter):
    """Bare, venice: and x402: ids dispatch to exactly one provider namespace."""

    name = "market"

    def __init__(self, openrouter=None, venice=None, x402: X402Provider | None = None) -> None:
        super().__init__(venice, openrouter)
        self.openrouter, self.venice = openrouter, venice
        self.x402 = x402 if x402 is not None else X402Provider()

    def _provider(self, model_id: str):
        if model_id.startswith("x402:"):
            return self.x402
        if model_id.startswith("venice:"):
            return self.venice
        return self.openrouter

    def complete(self, req: ModelRequest) -> ModelResponse:
        """One namespace receives the original request with no cross-provider retry."""
        provider = self._provider(req.model_id)
        if provider is None:
            raise X402Error("No provider for this model namespace")
        return provider.complete(req)

    def catalogue(self) -> list[CatalogueEntry]:
        """Available catalogues survive another vendor's catalogue outage."""
        entries = []
        for provider in (self.openrouter, self.venice):
            if provider is not None and hasattr(provider, "catalogue"):
                try:
                    entries.extend(provider.catalogue())
                except Exception:
                    continue
        return entries

    def affordable(self, model_id: str, ceiling_micro: int) -> tuple[bool, str]:
        """Known seed, seller credit or reserve shortages exclude only their own namespace."""
        provider = self._provider(model_id)
        if provider is None:
            return False, "provider: namespace unavailable"
        if model_id.startswith("x402:"):
            return self.x402.affordable(model_id, ceiling_micro)
        if hasattr(provider, "balance_micro"):
            balance = provider.balance_micro()
            if balance is not None and balance < ceiling_micro:
                return False, f"compute: provider balance {balance} below ceiling {ceiling_micro}"
        return True, ""

    def balance_micro(self) -> int | None:
        """The existing reconciler's seed view remains scoped to OpenRouter credits."""
        if self.openrouter is not None and hasattr(self.openrouter, "balance_micro"):
            return self.openrouter.balance_micro()
        return None
