"""DigitalOcean: the host's billing and droplet endpoints, read exactly, never written.

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
  ``disk``, ``price_monthly``, ``price_hourly`` (JSON numbers), ``regions`` and
  ``available``; paginated by ``per_page`` (at most 200) and ``meta.total``. The
  free-text ``description`` is DigitalOcean-authored prose and is not read.
* ``GET /v2/account`` (account/): ``account.uuid`` is "the unique universal
  identifier for the current user"; ``account.team.uuid``, present "when authorized
  in a team context", identifies the team. Billing belongs to the team when there is
  one, so the team's uuid is the billing account's identity, else the user's. The
  email and names are not read.
* ``GET /v2/droplets``, ``GET /v2/volumes``, ``GET /v2/snapshots`` (droplets/,
  block-storage/, snapshots/): the account's billable resources, counted by
  ``meta.total``, so a world can refuse an account that pays for anything else.
* ``GET http://169.254.169.254/metadata/v1/id`` (metadata/droplet-properties/): the
  droplet's own id as plain text, reachable only from inside that droplet and sent
  no token.

Only structured facts leave this module: slugs and statuses must look like slugs
and times must parse as ISO 8601, so no provider-authored prose can reach a seat.
JSON numbers are decoded as ``Decimal`` from their own text, so a price is never
read through a binary float.
"""

from __future__ import annotations

import json
import os
import re
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
METADATA_URL = "http://169.254.169.254/metadata/v1/id"
TOKEN_ENV = "DIGITALOCEAN_TOKEN"

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


_SLUG = re.compile(r"[a-z0-9][a-z0-9-]{0,63}")
_UUID = re.compile(r"[0-9a-fA-F-]{8,64}")


def _slug(value: Any, field: str) -> str:
    """A DigitalOcean identifier (a size, a region, a status), never free text."""
    if not isinstance(value, str) or _SLUG.fullmatch(value) is None:
        raise DigitalOceanError(None, f"{field} is not a slug")
    return value


def _time(value: Any, field: str) -> str:
    """An ISO 8601 time as DigitalOcean wrote it; anything else is refused."""
    from datetime import datetime

    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (AttributeError, ValueError):
        raise DigitalOceanError(None, f"{field} is not an ISO 8601 time") from None
    return value


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

    Rounded up so a price is never stated below what DigitalOcean published.
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
    if not isinstance(raw, dict):
        raise DigitalOceanError(None, "size is not an object")
    monthly, monthly_micro = _price(raw.get("price_monthly"), "price_monthly")
    hourly, hourly_micro = _price(raw.get("price_hourly"), "price_hourly")
    regions = raw.get("regions", [])
    if not isinstance(regions, list):
        raise DigitalOceanError(None, "size regions are not a list of slugs")
    return {
        "slug": _slug(raw.get("slug"), "size slug"),
        "memory_mb": _int(raw.get("memory"), "memory"),
        "vcpus": _int(raw.get("vcpus"), "vcpus"),
        "disk_gb": _int(raw.get("disk"), "disk"),
        "price_monthly_usd": monthly, "price_monthly_micro": monthly_micro,
        "price_hourly_usd": hourly, "price_hourly_micro": hourly_micro,
        "regions": sorted(_slug(r, "region") for r in regions),
        "available": raw.get("available") is True,
    }


class DigitalOceanClient:
    """Reads parse exactly or raise; nothing here writes to DigitalOcean.

    A GET is tried at most ``attempts`` times on a connection failure; a world's
    own reads use one attempt and a short timeout, so a slow DigitalOcean costs one
    bounded wait and never a retry loop. The bearer token is read from the
    environment at each call (``TOKEN_ENV``, loaded from ``digitalocean.key`` by
    the CLI), is redacted from every error this class raises, and is never sent to
    the metadata service.
    """

    name = "digitalocean"

    def __init__(self, *, token_env: str = TOKEN_ENV, base_url: str = BASE_URL,
                 http: Http | None = None, timeout_s: float = HTTP_TIMEOUT_S,
                 attempts: int = 2) -> None:
        if type(attempts) is not int or attempts < 1:
            raise ValueError("attempts must be a positive integer")
        self._attempts = attempts
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
        attempts = self._attempts if method == "GET" else 1
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
            "generated_at": _time(raw["generated_at"], "generated_at"),
        }

    def identity(self, droplet_id: int) -> dict[str, Any]:
        """Which billing account this token reads, and whether it holds the droplet.

        ``billing_uuid`` is the team's uuid when the token acts for a team, else the
        user's (the balance is the team's when there is one). ``droplet_held`` is
        False when this account answers 404 for the droplet: the second anchor.
        """
        raw = self._request("GET", "/v2/account")
        account = raw.get("account") if isinstance(raw, dict) else None
        if not isinstance(account, dict):
            raise DigitalOceanError(None, "account missing")
        team = account.get("team") if isinstance(account.get("team"), dict) else None
        owner = team.get("uuid") if team is not None else account.get("uuid")
        if not isinstance(owner, str) or _UUID.fullmatch(owner) is None:
            raise DigitalOceanError(None, "account without a uuid")
        try:
            held = self.droplet(droplet_id)["id"] == int(droplet_id)
        except DigitalOceanError as exc:
            if exc.status != 404:
                raise
            held = False
        return {"billing_uuid": owner.lower(), "billing_kind": "team" if team else "user",
                "droplet_id": int(droplet_id), "droplet_held": held}

    def billing(self, droplet_id: int) -> dict[str, Any]:
        """The account's identity and its balance, read together as one observation.

        One call, so a journal records and replays the pair as one read: whether
        a reading may be booked depends on both.
        """
        return {"identity": self.identity(droplet_id), "balance": self.balance()}

    def droplet(self, droplet_id: int) -> dict[str, Any]:
        """One droplet's size, state and price as DigitalOcean reports them."""
        raw = self._request("GET", f"/v2/droplets/{int(droplet_id)}")
        droplet = raw.get("droplet") if isinstance(raw, dict) else None
        if not isinstance(droplet, dict) or not isinstance(droplet.get("size_slug"), str):
            raise DigitalOceanError(None, "droplet without a size")
        size = _size(droplet.get("size"))
        region = droplet.get("region") if isinstance(droplet.get("region"), dict) else {}
        return {
            "id": _int(droplet.get("id"), "id"),
            "status": _slug(droplet.get("status"), "droplet status"),
            "locked": droplet.get("locked") is True,
            "size_slug": _slug(droplet["size_slug"], "size_slug"),
            "memory_mb": _int(droplet.get("memory"), "memory"),
            "vcpus": _int(droplet.get("vcpus"), "vcpus"),
            "disk_gb": _int(droplet.get("disk"), "disk"),
            "region": _slug(region.get("slug"), "region"),
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

    def _total(self, path: str, key: str) -> tuple[int, list]:
        raw = self._request("GET", f"{path}?per_page={PER_PAGE}&page=1")
        rows = raw.get(key) if isinstance(raw, dict) else None
        total = (raw.get("meta") or {}).get("total") if isinstance(raw, dict) else None
        if not isinstance(rows, list) or type(total) is not int:
            raise DigitalOceanError(None, f"{key} list missing")
        return total, rows

    def resources(self) -> dict[str, Any]:
        """The account's billable resources: every droplet id, and volume and snapshot counts."""
        total, rows = self._total("/v2/droplets", "droplets")
        ids = sorted(_int(row.get("id"), "id") for row in rows if isinstance(row, dict))
        if total != len(ids):
            # More droplets than one page holds is already not a dedicated account.
            ids.extend([0] * (total - len(ids)))
        return {"droplets": ids, "volumes": self._total("/v2/volumes", "volumes")[0],
                "snapshots": self._total("/v2/snapshots", "snapshots")[0]}

    def metadata_droplet_id(self) -> int:
        """The id the droplet's own metadata service states; it is sent no token."""
        try:
            status, raw = self._http("GET", METADATA_URL, {}, None, min(self._timeout_s, 5))
        except Exception as exc:  # noqa: BLE001 - off a droplet it is unreachable
            raise DigitalOceanError(None, "metadata service unreachable",
                                    sent=dispatched(exc)) from None
        text = raw.decode("utf-8", errors="replace").strip() if isinstance(raw, bytes) else ""
        if status != 200 or not text.isdigit():
            raise DigitalOceanError(status, "metadata service gave no droplet id")
        return int(text)
