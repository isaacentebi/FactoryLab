"""DigitalOcean: the host's billing and droplet endpoints, read exactly, written once.

The factory runs on a DigitalOcean droplet paid from prepaid account credit. That
credit is a pot like OpenRouter's (essay II.II, the one first move): the treasury
reads it here and books what DigitalOcean itself reports as used. This module is
the HTTP boundary and nothing else: it parses the wire into integer micro-USD and
plain fields, and it never decides anything about money.

What the parsing relies on (docs.digitalocean.com/reference/api/reference/):

* ``GET /v2/customers/my/balance`` (billing/): ``month_to_date_balance``,
  ``account_balance`` and ``month_to_date_usage`` are decimal *strings*;
  ``generated_at`` is an ISO 8601 time. ``month_to_date_balance`` "includes the
  ``account_balance`` and ``month_to_date_usage``". The API reference states no sign
  convention; DigitalOcean's own answer is that "a negative balance means that you
  have credit, and a positive balance means this is the amount currently owed"
  (digitalocean.com/community/questions/where-can-i-see-my-account-balance). So the
  credit remaining is ``-month_to_date_balance``, and a positive balance is debt.
* ``GET /v2/droplets/{id}`` (droplets/): ``droplet.size_slug``, ``status``,
  ``memory``, ``vcpus``, ``disk``, ``locked``, ``region.slug`` and the embedded
  ``size`` with ``price_monthly`` / ``price_hourly``.
* ``GET /v2/sizes`` (sizes/): ``sizes[]`` with ``slug``, ``memory``, ``vcpus``,
  ``disk``, ``price_monthly``, ``price_hourly`` (JSON numbers), ``regions``,
  ``available``, ``description``; paginated by ``per_page`` (at most 200) and
  ``meta.total``.
* ``POST /v2/droplets/{id}/actions`` with ``{"type": "resize", "size", "disk"}``
  (droplet-actions/): ``disk: true`` "is a permanent change and cannot be reversed
  as a Droplet's disk size cannot be decreased"; ``disk: false`` changes CPU and
  RAM only. Resizing through the API powers the droplet down first
  (products/droplets/how-to/resize/). The answer is ``{"action": {...}}``.
* ``GET /v2/actions/{id}`` (actions/): ``action.status`` is ``in-progress``,
  ``completed`` or ``errored``; also ``type``, ``started_at``, ``completed_at``,
  ``resource_id``.

JSON numbers are decoded as ``Decimal`` from their own text, so a price is never
read through a binary float.
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from decimal import Decimal
from typing import Any
from urllib import error, request

from factorylab.kernel.money import usd_to_micro
from factorylab.world.openai_wire import dispatched

#: Every DigitalOcean call is a small control-plane request.
HTTP_TIMEOUT_S = 30
#: The most a page may hold (sizes/), and the most pages one price-list read follows.
PER_PAGE = 200
MAX_PAGES = 5
BASE_URL = "https://api.digitalocean.com"
TOKEN_ENV = "DIGITALOCEAN_TOKEN"
#: The three states DigitalOcean gives an action (actions/).
ACTION_STATUSES = ("in-progress", "completed", "errored")

#: ``http(method, url, headers, body, timeout) -> (status, body)``: the one boundary.
Http = Callable[[str, str, dict[str, str], bytes | None, float], tuple[int, bytes]]


class DigitalOceanError(Exception):
    """A failed call: its HTTP status, a redacted bounded body, and whether it was sent.

    ``sent`` is False only on definitive evidence the request never left this
    process; a write whose answer was lost stays ``sent``.
    """

    def __init__(self, status: int | None, body: str, *, sent: bool = True) -> None:
        self.status = status
        self.body = body
        self.sent = sent
        super().__init__(f"DigitalOcean error ({status}): {body}")


class _NoRedirect(request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        """A redirect cannot carry the bearer token or replay a write elsewhere."""
        return None


def _urllib_http(method: str, url: str, headers: dict[str, str], body: bytes | None,
                 timeout: float) -> tuple[int, bytes]:
    req = request.Request(url, data=body, headers=headers, method=method)
    opener = request.build_opener(_NoRedirect())
    try:
        with opener.open(req, timeout=timeout) as response:
            return response.status, response.read()
    except error.HTTPError as exc:
        try:
            return exc.code, exc.read()
        finally:
            exc.close()


def _money(value: Any, field: str) -> int:
    """Exact micro-USD from a documented decimal string; anything else is refused."""
    if not isinstance(value, str):
        raise DigitalOceanError(None, f"{field} is not a decimal string")
    try:
        return usd_to_micro(value.strip(), rounding="exact")
    except (ValueError, ArithmeticError):
        raise DigitalOceanError(None, f"{field} is not an exact micro-USD amount") from None


def _price(value: Any, field: str) -> tuple[str, int]:
    """A published price as its own decimal text and as micro-USD rounded up.

    Rounded up because the one use of the micro figure is a cap the kernel
    enforces: a price that is a fraction of a micro-USD above the cap is above it.
    """
    if isinstance(value, bool) or not isinstance(value, (Decimal, int, str)):
        raise DigitalOceanError(None, f"{field} is not a decimal number")
    try:
        amount = Decimal(value) if not isinstance(value, Decimal) else value
        if not amount.is_finite() or amount < 0:
            raise ValueError
        return format(amount, "f"), usd_to_micro(amount, rounding="ceil")
    except (ValueError, ArithmeticError):
        raise DigitalOceanError(None, f"{field} is not a decimal number") from None


def _int(value: Any, field: str) -> int:
    if type(value) is not int:
        raise DigitalOceanError(None, f"{field} is not an integer")
    return value


def _size(raw: Any) -> dict[str, Any]:
    """One size as published: its slug, resources and prices, never interpreted."""
    if not isinstance(raw, dict) or not isinstance(raw.get("slug"), str):
        raise DigitalOceanError(None, "size without a slug")
    monthly, monthly_micro = _price(raw.get("price_monthly"), "price_monthly")
    hourly, hourly_micro = _price(raw.get("price_hourly"), "price_hourly")
    regions = raw.get("regions", [])
    if not isinstance(regions, list) or not all(isinstance(r, str) for r in regions):
        raise DigitalOceanError(None, "size regions are not a list of slugs")
    return {
        "slug": raw["slug"],
        "memory_mb": _int(raw.get("memory"), "memory"),
        "vcpus": _int(raw.get("vcpus"), "vcpus"),
        "disk_gb": _int(raw.get("disk"), "disk"),
        "price_monthly_usd": monthly, "price_monthly_micro": monthly_micro,
        "price_hourly_usd": hourly, "price_hourly_micro": hourly_micro,
        "regions": sorted(regions),
        "available": raw.get("available") is True,
        "description": str(raw.get("description") or "")[:80],
    }


def _action(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict) or type(raw.get("id")) is not int:
        raise DigitalOceanError(None, "action without an id")
    status = raw.get("status")
    if status not in ACTION_STATUSES:
        raise DigitalOceanError(None, "action status is not one DigitalOcean documents")
    return {"id": raw["id"], "status": status, "type": str(raw.get("type") or ""),
            "started_at": raw.get("started_at"), "completed_at": raw.get("completed_at"),
            "resource_id": raw.get("resource_id")}


class DigitalOceanClient:
    """Reads parse exactly or raise; the one write is sent at most once per call.

    GETs retry once after a connection failure. The POST never retries here: a
    lost answer is the caller's to reconcile by reading, never by resending.
    The bearer token is read from the environment at each call (``TOKEN_ENV``,
    loaded from ``digitalocean.key`` by the CLI) and is redacted from every
    error this class raises.
    """

    name = "digitalocean"

    def __init__(self, *, token_env: str = TOKEN_ENV, base_url: str = BASE_URL,
                 http: Http | None = None, timeout_s: float = HTTP_TIMEOUT_S) -> None:
        self._token_env = token_env
        self._base_url = base_url.rstrip("/")
        self._http = http if http is not None else _urllib_http
        self._timeout_s = timeout_s

    def _redact(self, text: str) -> str:
        token = os.environ.get(self._token_env)
        return text.replace(token, "[REDACTED]") if token else text

    def _request(self, method: str, path: str, payload: dict | None = None) -> dict:
        token = os.environ.get(self._token_env)
        if not token:
            raise DigitalOceanError(None, "API token environment variable is not set",
                                    sent=False)
        headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json",
                   "Accept": "application/json"}
        body = json.dumps(payload).encode() if payload is not None else None
        attempts = 2 if method == "GET" else 1
        for attempt in range(attempts):
            try:
                status, raw = self._http(method, self._base_url + path, headers, body,
                                         self._timeout_s)
            except (error.URLError, ConnectionError, TimeoutError, OSError) as exc:
                if attempt + 1 == attempts:
                    raise DigitalOceanError(None, "Connection failed",
                                            sent=dispatched(exc)) from None
                continue
            except Exception as exc:  # noqa: BLE001 - a transport error may echo headers
                raise DigitalOceanError(None, "Transport failed",
                                        sent=dispatched(exc)) from None
            text = raw.decode("utf-8", errors="replace") if isinstance(raw, bytes) else ""
            if not 200 <= status < 300:
                raise DigitalOceanError(status, self._redact(text)[:300])
            try:
                return json.loads(text, parse_float=Decimal)
            except ValueError:
                raise DigitalOceanError(status, "response is not JSON") from None
        raise AssertionError("unreachable")

    def balance(self) -> dict[str, Any]:
        """The account balance, exact: micro-USD fields and DigitalOcean's own stamp.

        ``credit_micro`` is ``-month_to_date_balance``: prepaid credit remaining when
        positive, an amount owed when negative (see the module note on the sign).
        """
        raw = self._request("GET", "/v2/customers/my/balance")
        if not isinstance(raw, dict) or not isinstance(raw.get("generated_at"), str):
            raise DigitalOceanError(None, "balance without generated_at")
        balance = _money(raw.get("month_to_date_balance"), "month_to_date_balance")
        return {
            "month_to_date_balance_micro": balance,
            "account_balance_micro": _money(raw.get("account_balance"), "account_balance"),
            "month_to_date_usage_micro": _money(raw.get("month_to_date_usage"),
                                                "month_to_date_usage"),
            "credit_micro": -balance,
            "generated_at": raw["generated_at"],
        }

    def droplet(self, droplet_id: int) -> dict[str, Any]:
        """One droplet's size, state and price as DigitalOcean reports them."""
        raw = self._request("GET", f"/v2/droplets/{int(droplet_id)}")
        droplet = raw.get("droplet") if isinstance(raw, dict) else None
        if not isinstance(droplet, dict) or not isinstance(droplet.get("size_slug"), str):
            raise DigitalOceanError(None, "droplet without a size")
        size = _size(droplet.get("size"))
        region = droplet.get("region") if isinstance(droplet.get("region"), dict) else {}
        return {
            "id": _int(droplet.get("id"), "id"), "status": str(droplet.get("status") or ""),
            "locked": droplet.get("locked") is True, "size_slug": droplet["size_slug"],
            "memory_mb": _int(droplet.get("memory"), "memory"),
            "vcpus": _int(droplet.get("vcpus"), "vcpus"),
            "disk_gb": _int(droplet.get("disk"), "disk"),
            "region": str(region.get("slug") or ""),
            "price_monthly_usd": size["price_monthly_usd"],
            "price_monthly_micro": size["price_monthly_micro"],
            "price_hourly_usd": size["price_hourly_usd"],
        }

    def sizes(self) -> list[dict[str, Any]]:
        """The whole published price list, every page, in slug order."""
        found: list[dict[str, Any]] = []
        for page in range(1, MAX_PAGES + 1):
            raw = self._request("GET", f"/v2/sizes?per_page={PER_PAGE}&page={page}")
            rows = raw.get("sizes") if isinstance(raw, dict) else None
            if not isinstance(rows, list):
                raise DigitalOceanError(None, "size list missing")
            found.extend(_size(row) for row in rows)
            total = (raw.get("meta") or {}).get("total") if isinstance(raw, dict) else None
            if not rows or type(total) is not int or len(found) >= total:
                break
        return sorted(found, key=lambda s: s["slug"])

    def resize(self, droplet_id: int, size: str, disk: bool) -> dict[str, Any]:
        """Submit one resize action, once; return DigitalOcean's action."""
        if type(disk) is not bool or not isinstance(size, str) or not size:
            raise DigitalOceanError(None, "resize needs a size slug and a boolean disk",
                                    sent=False)
        raw = self._request("POST", f"/v2/droplets/{int(droplet_id)}/actions",
                            {"type": "resize", "size": size, "disk": disk})
        return _action(raw.get("action") if isinstance(raw, dict) else None)

    def action(self, action_id: int) -> dict[str, Any]:
        """One action's status."""
        raw = self._request("GET", f"/v2/actions/{int(action_id)}")
        return _action(raw.get("action") if isinstance(raw, dict) else None)
