"""OpenRouter calls expose vendor usage and integer costs without wallet access."""

from __future__ import annotations

import json
import os
from collections.abc import Callable, Iterable, Mapping
from typing import Any
from urllib import error, request

from factorylab.kernel.money import nonnegative_usd_micro, usd_to_micro
from factorylab.world.models import CatalogueEntry, ModelRequest, ModelResponse
from factorylab.world.openai_wire import dispatched, parse_completion

#: A completion may legitimately take minutes (long context, 3,000-token replies);
#: a transport timeout is billed as uncertain at the ceiling, so it must be rarer than
#: a slow reply. The ten-minute tick absorbs it.
MODEL_HTTP_TIMEOUT_S = 180


class OpenRouterError(Exception):
    """Provider failures carry an HTTP status, a sanitized body, and whether it was sent.

    ``sent`` is False only with definitive evidence the request body never left this
    process; it stays True whenever the provider may already have billed the call.
    """

    def __init__(self, status: int | None, body: str, *, sent: bool = True) -> None:
        self.status = status
        self.body = body
        self.sent = sent
        super().__init__(f"OpenRouter error ({status}): {body}")


class _NoRedirect(request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        """Redirects cannot replay a billed request or forward credentials."""
        return None


class OpenRouterProvider:
    """POSTs run at most once; GETs retry at most once after a connection failure."""

    name = "openrouter"

    def __init__(
        self,
        *,
        key_env: str = "OPENROUTER_API_KEY",
        base_url: str = "https://openrouter.ai/api/v1",
        transport: Callable[[str, str, dict | None], dict] | None = None,
        app_name: str = "factorylab",
        reasoning_models: Iterable[str] = (),
        reasoning_config: Mapping[str, Mapping[str, Any]] | None = None,
        web_config: Mapping[str, Mapping[str, Any]] | None = None,
    ) -> None:
        self._key_env = key_env
        self._base_url = base_url.rstrip("/")
        self._app_name = app_name
        self._reasoning_models = frozenset(reasoning_models)
        self._reasoning_config = {k: dict(v) for k, v in (reasoning_config or {}).items()}
        self._web_config = {k: dict(v) for k, v in (web_config or {}).items()}
        self._transport = transport if transport is not None else self._default_transport

    def _redact(self, body: str) -> str:
        key = os.environ.get(self._key_env)
        return body.replace(key, "[REDACTED]") if key else body

    def _default_transport(self, method: str, path: str, payload: dict | None) -> dict:
        key = os.environ.get(self._key_env)
        if not key:
            raise OpenRouterError(None, "API key environment variable is not set", sent=False)
        req = request.Request(
            self._base_url + path,
            data=json.dumps(payload).encode("utf-8") if payload is not None else None,
            headers={
                "Authorization": f"Bearer {key}",
                "HTTP-Referer": self._app_name,
                "X-Title": self._app_name,
                "Content-Type": "application/json",
            },
            method=method,
        )
        opener = request.build_opener(_NoRedirect())
        with opener.open(req, timeout=MODEL_HTTP_TIMEOUT_S) as response:
            body = response.read().decode("utf-8", errors="replace")
            if not 200 <= response.status < 300:
                raise OpenRouterError(response.status, self._redact(body))
            return json.loads(body)

    def _request(self, method: str, path: str, payload: dict | None = None) -> dict:
        attempts = 2 if method == "GET" else 1
        for attempt in range(attempts):
            try:
                return self._transport(method, path, payload)
            except error.HTTPError as exc:
                try:
                    body = exc.read().decode("utf-8", errors="replace")
                except Exception:
                    body = "HTTP error body unavailable"
                finally:
                    exc.close()
                raise OpenRouterError(exc.code, self._redact(body)) from None
            except OpenRouterError as exc:
                raise OpenRouterError(exc.status, self._redact(exc.body), sent=exc.sent) from None
            except (error.URLError, ConnectionError, TimeoutError) as exc:
                if attempt + 1 == attempts:
                    raise OpenRouterError(
                        None, "Connection failed", sent=dispatched(exc)
                    ) from None
            except Exception as exc:
                # Arbitrary transport/decoder exceptions may contain request headers.
                raise OpenRouterError(
                    None, "Transport or response decoding failed", sent=dispatched(exc)
                ) from None
        raise AssertionError("unreachable")

    def complete(self, req: ModelRequest) -> ModelResponse:
        """Return vendor usage and an upward-rounded reported cost after one POST."""
        payload: dict[str, Any] = {
            "model": req.model_id,
            "messages": [{"role": "system", "content": req.system}, *req.messages],
            "max_tokens": req.max_tokens,
        }
        if req.json_object:
            payload["response_format"] = {"type": "json_object"}
        # "<id>@<effort>" selects a reasoning level as its own capability; ":online" adds web.
        wire_id, effort_override = req.model_id, None
        if "@" in wire_id:
            wire_id, effort_override = wire_id.rsplit("@", 1)
        payload["model"] = wire_id
        base_id = wire_id[:-7] if wire_id.endswith(":online") else wire_id
        if req.model_id in self._web_config or wire_id in self._web_config:
            payload["plugins"] = [
                {
                    "id": "web",
                    **self._web_config.get(req.model_id, self._web_config.get(wire_id, {})),
                }
            ]
        if effort_override is not None:
            payload["reasoning"] = {"effort": effort_override}
        elif base_id in self._reasoning_config:
            payload["reasoning"] = dict(self._reasoning_config[base_id])
        elif req.effort in {"low", "medium", "high"} and base_id in self._reasoning_models:
            payload["reasoning"] = {"effort": req.effort}
        wire = parse_completion(
            self._request("POST", "/chat/completions", payload), error=OpenRouterError
        )
        cost = wire.usage.get("cost")
        cost_micro = None
        if cost is not None:
            try:
                cost_micro = nonnegative_usd_micro(cost, rounding="ceil")
            except ValueError:
                raise OpenRouterError(None, "Invalid reported cost") from None
        raw = {}
        if wire.request_id is not None:
            raw["request_id"] = wire.request_id
        if wire.reasoning_tokens is not None:
            raw["reasoning_tokens"] = wire.reasoning_tokens
        # The provider's own count of the input it served from its prompt cache.
        # Cost is not adjusted here: OpenRouter's reported ``usage.cost`` already
        # reflects the discount, and this is the diary's view of the hit rate.
        if wire.cached_tokens is not None:
            raw["cached_tokens"] = wire.cached_tokens
        return ModelResponse(
            model_id=wire.model or req.model_id,
            text=wire.text,
            input_tokens=wire.input_tokens,
            output_tokens=wire.output_tokens,
            stop_reason=wire.stop_reason,
            refused=False,
            raw=raw,
            cost_micro=cost_micro,
        )

    def catalogue(self) -> list[CatalogueEntry]:
        """Return model identities, context limits, and unchanged decimal price quotes."""
        response = self._request("GET", "/models")
        return [
            CatalogueEntry(
                id=entry["id"],
                name=entry["name"],
                prompt_usd_per_token=entry["pricing"]["prompt"],
                completion_usd_per_token=entry["pricing"]["completion"],
                context_length=entry.get("context_length"),
            )
            for entry in response["data"]
        ]

    def balance_micro(self) -> int | None:
        """Return remaining key allowance rounded down to micro-USD, or None if unlimited."""
        remaining = self._request("GET", "/key")["data"].get("limit_remaining")
        if remaining is None:
            return None
        return usd_to_micro(remaining, rounding="floor")
