"""OpenRouter calls expose vendor usage and integer costs without wallet access."""

from __future__ import annotations

import json
import os
from collections.abc import Callable, Iterable, Mapping
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal
from typing import Any
from urllib import error, request

from factorylab.world.models import CatalogueEntry, ModelRequest, ModelResponse


class OpenRouterError(Exception):
    """Provider failures carry an HTTP status, if available, and a sanitized body."""

    def __init__(self, status: int | None, body: str) -> None:
        self.status = status
        self.body = body
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
    ) -> None:
        self._key_env = key_env
        self._base_url = base_url.rstrip("/")
        self._app_name = app_name
        self._reasoning_models = frozenset(reasoning_models)
        self._reasoning_config = {k: dict(v) for k, v in (reasoning_config or {}).items()}
        self._transport = transport if transport is not None else self._default_transport

    def _redact(self, body: str) -> str:
        key = os.environ.get(self._key_env)
        return body.replace(key, "[REDACTED]") if key else body

    def _default_transport(self, method: str, path: str, payload: dict | None) -> dict:
        key = os.environ.get(self._key_env)
        if not key:
            raise OpenRouterError(None, "API key environment variable is not set")
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
        with request.build_opener(_NoRedirect()).open(req, timeout=60) as response:
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
                raise OpenRouterError(exc.status, self._redact(exc.body)) from None
            except (error.URLError, ConnectionError, TimeoutError):
                if attempt + 1 == attempts:
                    raise OpenRouterError(None, "Connection failed") from None
            except Exception:
                # Arbitrary transport/decoder exceptions may contain request headers.
                raise OpenRouterError(None, "Transport or response decoding failed") from None
        raise AssertionError("unreachable")

    def complete(self, req: ModelRequest) -> ModelResponse:
        """Return vendor usage and an upward-rounded reported cost after one POST."""
        payload: dict[str, Any] = {
            "model": req.model_id,
            "messages": [{"role": "system", "content": req.system}, *req.messages],
            "max_tokens": req.max_tokens,
        }
        if req.model_id in self._reasoning_config:
            payload["reasoning"] = dict(self._reasoning_config[req.model_id])
        elif req.effort in {"low", "medium", "high"} and req.model_id in self._reasoning_models:
            payload["reasoning"] = {"effort": req.effort}
        response = self._request("POST", "/chat/completions", payload)
        choice = response["choices"][0]
        content = choice["message"]["content"]
        if isinstance(content, list):
            content = "".join(part["text"] for part in content if part.get("type") == "text")
        usage = response["usage"]
        cost = usage.get("cost")
        cost_micro = None
        if cost is not None:
            amount = Decimal(str(cost))
            if not amount.is_finite() or amount < 0:
                raise OpenRouterError(None, "Invalid reported cost")
            cost_micro = int((amount * 1_000_000).to_integral_value(rounding=ROUND_CEILING))
        raw = {}
        if response.get("id") is not None:
            raw["request_id"] = response["id"]
        details = usage.get("completion_tokens_details") or {}
        if "reasoning_tokens" in details:
            raw["reasoning_tokens"] = details["reasoning_tokens"]
        return ModelResponse(
            model_id=response.get("model") or req.model_id,
            text=content or "",
            input_tokens=usage["prompt_tokens"],
            output_tokens=usage["completion_tokens"],
            stop_reason=choice.get("finish_reason") or "",
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
        return int((Decimal(str(remaining)) * 1_000_000).to_integral_value(rounding=ROUND_FLOOR))
