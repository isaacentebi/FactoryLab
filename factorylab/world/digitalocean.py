"""DigitalOcean: the host's invoices, account and droplet, read exactly, never written.

The factory runs on a DigitalOcean droplet. What that droplet costs is what
DigitalOcean bills for it, line by line, so the books read DigitalOcean's own
per-resource billing and keep only this droplet's lines (world/hosting.py). This
module is the HTTP boundary and nothing else: it parses the wire into integer
micro-USD and plain fields, and decides nothing about money.

What the parsing relies on (docs.digitalocean.com/reference/api/reference/):

* ``GET /v2/customers/my/invoices`` (billing/): ``invoice_preview`` and
  ``invoices[]``, each with ``invoice_uuid``, ``invoice_id``, ``amount`` (a
  decimal string, USD) and ``invoice_period`` (``YYYY-MM``); the preview also has
  ``updated_at``. "An invoice preview is generated daily, which can be accessed
  with the ``preview`` keyword in place of ``$INVOICE_UUID``." Paginated by
  ``per_page`` (1-200) and ``page``, with ``meta.total``.
* ``GET /v2/customers/my/invoices/{invoice_uuid}`` (billing/): ``invoice_items[]``
  with ``product``, ``resource_uuid`` and ``resource_id`` ("if available"),
  ``group_description``, ``description``, ``amount`` (decimal string, USD),
  ``duration``, ``duration_unit``, ``start_time``, ``end_time`` and
  ``project_name``; paginated the same way. A line is this droplet's when its
  ``resource_id`` is the droplet's id and its ``product`` is ``Droplets``; only such
  lines are parsed, strictly, and every other line is only counted, so a line of
  another resource can never make a read fail. Tax is on the invoice, not on the
  lines, so this droplet's lines exclude it.
* ``GET /v2/customers/my/billing_history`` (billing/): ``billing_history[]`` with
  ``description``, ``amount`` (signed decimal string: an invoice positive, a payment
  negative), ``invoice_id``, ``invoice_uuid``, ``date`` and ``type``, one of
  ``ACHFailure``, ``Adjustment``, ``AttemptFailed``, ``Chargeback``, ``Credit``,
  ``CreditExpiration``, ``Invoice``, ``Payment``, ``Refund``, ``Reversal``
  (github.com/digitalocean/openapi, specification/resources/billing/models/
  billing_history.yml). An entry names no resource; it is read leniently, off the burn
  path, and a type outside that list is recorded as ``unknown``.
* ``GET /v2/account`` (account/): ``account.uuid`` is "the unique universal
  identifier for the current user"; ``account.team.uuid``, present "when
  authorized in a team context", identifies the team, and billing is the team's
  when there is one. The email and names are not read.
* ``GET /v2/droplets/{id}`` (droplets/) and ``GET /v2/sizes`` (sizes/): the
  droplet's size, resources, status, region and price, and the published size
  list. A size's free-text ``description`` is not read.
* ``GET http://169.254.169.254/metadata/v1/id`` (metadata/droplet-properties/): the
  droplet's own id as plain text, reachable only from inside it, sent no token.

Only structured facts leave this module: slugs and statuses must look like slugs,
times must parse as ISO 8601, and an invoice line's free-text description is never
read. JSON numbers
are decoded as ``Decimal`` from their own text.

Every call runs under one monotonic deadline (``Deadline``) that covers the name
lookup, the connection, the TLS handshake and every read, so a slow DigitalOcean
costs at most the budget its caller gave, and never a retry. The name is looked up
with the system resolver through ``getent ahostsv4`` under that deadline (a
subprocess with a timeout: no thread, and ``getaddrinfo`` has no timeout of its
own); the connection goes to that address and TLS still validates the host name.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import socket
import ssl
import time
from collections.abc import Callable
from decimal import Decimal
from typing import Any
from urllib.parse import urlsplit

from factorylab.kernel.money import usd_to_micro

BASE_URL = "https://api.digitalocean.com"
METADATA_URL = "http://169.254.169.254/metadata/v1/id"
TOKEN_ENV = "DIGITALOCEAN_TOKEN"
#: The most a page may hold (billing/, sizes/), and the most pages one list follows.
PER_PAGE = 200
#: A safety bound only: a list is paged until an empty page, within the deadline.
MAX_PAGES = 100
#: The product DigitalOcean bills a droplet under.
DROPLET_PRODUCT = "Droplets"
#: Closed invoices reconciled a read, oldest first; the rest wait for the next window.
INVOICES_PER_READ = 2
#: A response larger than this is not a billing answer.
MAX_BODY_BYTES = 8 * 1024 * 1024
HISTORY_TYPES = ("ACHFailure", "Adjustment", "AttemptFailed", "Chargeback", "Credit",
                 "CreditExpiration", "Invoice", "Payment", "Refund", "Reversal")

#: ``http(method, url, headers, body, timeout) -> (status, body)``: the one boundary.
#: ``timeout`` is the whole request's budget in seconds, name lookup included.
Http = Callable[[str, str, dict[str, str], bytes | None, float], tuple[int, bytes]]


class DigitalOceanError(Exception):
    """A failed call: its HTTP status and a redacted, bounded body."""

    def __init__(self, status: int | None, body: str) -> None:
        self.status = status
        self.body = body
        super().__init__(f"DigitalOcean error ({status}): {body}")


class Deadline:
    """One monotonic end for a whole read, however many requests it makes."""

    def __init__(self, budget_s: float) -> None:
        if not budget_s > 0:
            raise ValueError("a deadline needs a positive budget")
        self.end = time.monotonic() + float(budget_s)

    def remaining(self) -> float:
        """Seconds left; raises ``TimeoutError`` once none are."""
        left = self.end - time.monotonic()
        if left <= 0:
            raise TimeoutError("deadline passed")
        return left


# --- the transport ------------------------------------------------------------------------

def lookup_available() -> bool:
    """Whether this host has the bounded name lookup the transport needs (``getent``)."""
    import shutil

    return shutil.which("getent") is not None


def _resolve(host: str, deadline: Deadline) -> str:
    """An IPv4 address for ``host`` from the system resolver, within the deadline.

    ``getent ahostsv4`` asks the same resolver ``getaddrinfo`` would, in a child
    process that ``subprocess.run`` kills when the deadline's remainder runs out;
    nothing here waits on an unbounded call, and no thread is started.
    """
    import ipaddress
    import subprocess

    try:
        ipaddress.IPv4Address(host)
        return host
    except ValueError:
        pass
    try:
        done = subprocess.run(["getent", "ahostsv4", host], capture_output=True,
                              timeout=deadline.remaining(), check=False)
    except subprocess.TimeoutExpired:
        raise TimeoutError("name lookup passed the deadline") from None
    except FileNotFoundError:
        raise OSError("no bounded name lookup on this host (getent)") from None
    for line in done.stdout.decode("ascii", errors="replace").splitlines():
        field = line.split()[0] if line.split() else ""
        try:
            return str(ipaddress.IPv4Address(field))
        except ValueError:
            continue
    raise OSError("name not resolved")


def _dechunk(body: bytes) -> bytes:
    out, at = b"", 0
    while True:
        end = body.index(b"\r\n", at)
        size = int(body[at:end].split(b";")[0], 16)
        if size == 0:
            return out
        out += body[end + 2:end + 2 + size]
        at = end + 2 + size + 2


def deadline_http(method: str, url: str, headers: dict[str, str], body: bytes | None,
                  timeout: float) -> tuple[int, bytes]:
    """One HTTP/1.1 request whose every step fits ``timeout`` seconds from now.

    Guarantees the call returns or raises within the budget: the name lookup, the
    connection, the TLS handshake, the send and each receive are bounded by what
    remains of one monotonic deadline. No redirect is followed.
    """
    deadline = Deadline(timeout)
    parts = urlsplit(url)
    host = parts.hostname or ""
    tls = parts.scheme == "https"
    port = parts.port or (443 if tls else 80)
    address = _resolve(host, deadline)
    sock = socket.create_connection((address, port), timeout=deadline.remaining())
    try:
        if tls:
            sock.settimeout(deadline.remaining())
            sock = ssl.create_default_context().wrap_socket(sock, server_hostname=host)
        path = parts.path + (f"?{parts.query}" if parts.query else "")
        lines = [f"{method} {path or '/'} HTTP/1.1", f"Host: {host}", "Connection: close",
                 "Accept-Encoding: identity",
                 *(f"{k}: {v}" for k, v in headers.items())]
        if body is not None:
            lines.append(f"Content-Length: {len(body)}")
        sock.settimeout(deadline.remaining())
        sock.sendall(("\r\n".join(lines) + "\r\n\r\n").encode() + (body or b""))
        raw = b""
        while True:
            sock.settimeout(deadline.remaining())
            chunk = sock.recv(65536)
            if not chunk:
                break
            raw += chunk
            if len(raw) > MAX_BODY_BYTES:
                raise OSError("response too large")
    finally:
        sock.close()
    head, _, payload = raw.partition(b"\r\n\r\n")
    status_line, *header_lines = head.decode("latin-1").split("\r\n")
    status = int(status_line.split()[1])
    fields = {k.strip().lower(): v.strip() for k, _, v in
              (line.partition(":") for line in header_lines)}
    if fields.get("transfer-encoding", "").lower() == "chunked":
        payload = _dechunk(payload)
    return status, payload


# --- parsing ------------------------------------------------------------------------------

_SLUG = re.compile(r"[a-z0-9][a-z0-9-]{0,63}")
_UUID = re.compile(r"[0-9a-fA-F-]{8,64}")
_PERIOD = re.compile(r"[0-9]{4}-(0[1-9]|1[0-2])")


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


def _period(value: Any, field: str) -> str:
    if not isinstance(value, str) or _PERIOD.fullmatch(value) is None:
        raise DigitalOceanError(None, f"{field} is not a YYYY-MM period")
    return value


def _money(value: Any, field: str) -> int:
    """Exact signed micro-USD from a documented decimal string; anything else is refused."""
    if not isinstance(value, str):
        raise DigitalOceanError(None, f"{field} is not a decimal string")
    try:
        return usd_to_micro(value.strip(), rounding="exact")
    except (ValueError, ArithmeticError):
        raise DigitalOceanError(None, f"{field} is not an exact micro-USD amount") from None


def _price(value: Any, field: str) -> tuple[str, int]:
    """A published price as its own decimal text and as micro-USD rounded up."""
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


def _invoice(raw: Any) -> dict[str, Any] | None:
    """An invoice's uuid and period, or None for a row that names neither clearly."""
    if not isinstance(raw, dict):
        return None
    uuid, period = raw.get("invoice_uuid"), raw.get("invoice_period")
    if not isinstance(uuid, str) or _UUID.fullmatch(uuid) is None:
        return None
    if not isinstance(period, str) or _PERIOD.fullmatch(period) is None:
        return None
    return {"uuid": uuid, "period": period}


def _is_mine(raw: Any, droplet_id: int) -> bool:
    """Whether a raw invoice line is this droplet's: its id and the droplet product.

    Reads two fields and parses nothing else, so no other resource's line, whatever
    it holds, can make a read fail.
    """
    if not isinstance(raw, dict):
        return False
    resource = raw.get("resource_id")
    return (isinstance(resource, (str, int)) and not isinstance(resource, bool)
            and str(resource) == str(int(droplet_id))
            and raw.get("product") == DROPLET_PRODUCT)


def _line(raw: dict, period: str, source: str) -> dict[str, Any]:
    """One of this droplet's lines, strictly: exact amount, its own span, an identity.

    The identity within the month is a digest of the month, the source (the preview
    or an invoice's uuid), the product and the start time; the amount and the end
    time grow while a month accrues, and the description (DigitalOcean's text, a
    rename changes it) is not part of it.
    """
    start = _time(raw.get("start_time"), "start_time")
    end = _time(raw.get("end_time"), "end_time")
    key = hashlib.sha256(json.dumps([period, source, DROPLET_PRODUCT, start],
                                    separators=(",", ":")).encode()).hexdigest()[:24]
    return {"key": key, "source": source, "start_time": start, "end_time": end,
            "amount_micro": _money(raw.get("amount"), "invoice item amount")}


def _entry(raw: Any) -> dict[str, Any] | None:
    """One billing history entry, leniently: an unknown type is ``unknown``, and an
    entry with no readable amount or date is skipped, never an error."""
    if not isinstance(raw, dict):
        return None
    kind = raw.get("type") if raw.get("type") in HISTORY_TYPES else "unknown"
    try:
        amount = _money(raw.get("amount"), "entry amount")
        date = _time(raw.get("date"), "date")
    except DigitalOceanError:
        return None
    uuid = raw.get("invoice_uuid")
    uuid = uuid if isinstance(uuid, str) and _UUID.fullmatch(uuid) else None
    key = hashlib.sha256(json.dumps([kind, amount, date, uuid],
                                    separators=(",", ":")).encode()).hexdigest()[:24]
    return {"type": kind, "amount_micro": amount, "date": date, "invoice_uuid": uuid,
            "key": key}


# --- the client ---------------------------------------------------------------------------

def _deadline(budget: Deadline | float) -> Deadline:
    """A caller's budget in seconds (what a journal records), or a deadline already running."""
    return budget if isinstance(budget, Deadline) else Deadline(budget)


class DigitalOceanClient:
    """Reads parse exactly or raise; nothing here writes to DigitalOcean.

    Each public read takes ``budget_s``: one monotonic deadline for every request
    it makes. Nothing is retried. The bearer token is read from the environment at
    each call (``TOKEN_ENV``, loaded from ``digitalocean.key`` by the CLI), is
    redacted from every error, and is never sent to the metadata service.
    """

    name = "digitalocean"

    def __init__(self, *, token_env: str = TOKEN_ENV, base_url: str = BASE_URL,
                 http: Http | None = None) -> None:
        self._token_env = token_env
        self._base_url = base_url.rstrip("/")
        self._http = http if http is not None else deadline_http

    def _redact(self, text: str) -> str:
        token = os.environ.get(self._token_env)
        return text.replace(token, "[REDACTED]") if token else text

    def _get(self, path: str, deadline: Deadline) -> Any:
        token = os.environ.get(self._token_env)
        if not token:
            raise DigitalOceanError(None, "API token environment variable is not set")
        headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
        try:
            status, raw = self._http("GET", self._base_url + path, headers, None,
                                     deadline.remaining())
        except TimeoutError:
            raise DigitalOceanError(None, "deadline passed") from None
        except Exception:  # noqa: BLE001 - a transport error may echo headers
            raise DigitalOceanError(None, "connection failed") from None
        text = raw.decode("utf-8", errors="replace") if isinstance(raw, bytes) else ""
        if not 200 <= status < 300:
            raise DigitalOceanError(status, self._redact(text)[:300])
        try:
            return json.loads(text, parse_float=Decimal)
        except ValueError:
            raise DigitalOceanError(status, "response is not JSON") from None

    def _pages(self, path: str, key: str, deadline: Deadline) -> tuple[list, dict]:
        """Every page of one list, and the first page's whole answer.

        Pages until an empty page, or until ``meta.total`` rows when it is stated; a
        missing total never stops it early. The deadline bounds it.
        """
        rows: list = []
        first: dict = {}
        joiner = "&" if "?" in path else "?"
        for page in range(1, MAX_PAGES + 1):
            raw = self._get(f"{path}{joiner}per_page={PER_PAGE}&page={page}", deadline)
            found = raw.get(key) if isinstance(raw, dict) else None
            if not isinstance(found, list):
                raise DigitalOceanError(None, f"{key} list missing")
            first = first or raw
            rows.extend(found)
            meta = raw.get("meta") if isinstance(raw.get("meta"), dict) else {}
            total = meta.get("total")
            if not found or (type(total) is int and len(rows) >= total):
                return rows, first
        raise DigitalOceanError(None, f"{key} list longer than {MAX_PAGES} pages")

    # ---- reads

    def metadata_droplet_id(self, deadline: Deadline | float) -> int:
        """The id the droplet's own metadata service states; it is sent no token."""
        deadline = _deadline(deadline)
        try:
            status, raw = self._http("GET", METADATA_URL, {}, None, deadline.remaining())
        except TimeoutError:
            raise DigitalOceanError(None, "deadline passed") from None
        except Exception:  # noqa: BLE001 - off a droplet it is unreachable
            raise DigitalOceanError(None, "metadata service unreachable") from None
        text = raw.decode("utf-8", errors="replace").strip() if isinstance(raw, bytes) else ""
        if status != 200 or not text.isdigit():
            raise DigitalOceanError(status, "metadata service gave no droplet id")
        return int(text)

    def droplet(self, droplet_id: int, deadline: Deadline | float) -> dict[str, Any]:
        """One droplet's size, state and price as DigitalOcean reports them."""
        deadline = _deadline(deadline)
        raw = self._get(f"/v2/droplets/{int(droplet_id)}", deadline)
        droplet = raw.get("droplet") if isinstance(raw, dict) else None
        if not isinstance(droplet, dict):
            raise DigitalOceanError(None, "droplet missing")
        size = _size(droplet.get("size"))
        region = droplet.get("region") if isinstance(droplet.get("region"), dict) else {}
        return {
            "id": _int(droplet.get("id"), "id"),
            "status": _slug(droplet.get("status"), "droplet status"),
            "locked": droplet.get("locked") is True,
            "size_slug": _slug(droplet.get("size_slug"), "size_slug"),
            "memory_mb": _int(droplet.get("memory"), "memory"),
            "vcpus": _int(droplet.get("vcpus"), "vcpus"),
            "disk_gb": _int(droplet.get("disk"), "disk"),
            "region": _slug(region.get("slug"), "region"),
            "price_monthly_usd": size["price_monthly_usd"],
            "price_monthly_micro": size["price_monthly_micro"],
            "price_hourly_usd": size["price_hourly_usd"],
        }

    def sizes(self, deadline: Deadline | float) -> list[dict[str, Any]]:
        """The whole published price list, every page, in slug order."""
        deadline = _deadline(deadline)
        rows, _ = self._pages("/v2/sizes", "sizes", deadline)
        return sorted((_size(row) for row in rows), key=lambda s: s["slug"])

    def identity(self, droplet_id: int, deadline: Deadline | float) -> dict[str, Any]:
        """Which billing account the token reads, where this process runs, and the droplet.

        ``billing_uuid`` is the team's uuid when the token acts for a team, else the
        user's. ``droplet_held`` is False when the account answers 404 for the
        droplet; ``metadata_id`` is the id the local metadata service states.
        """
        deadline = _deadline(deadline)
        raw = self._get("/v2/account", deadline)
        account = raw.get("account") if isinstance(raw, dict) else None
        if not isinstance(account, dict):
            raise DigitalOceanError(None, "account missing")
        team = account.get("team") if isinstance(account.get("team"), dict) else None
        owner = team.get("uuid") if team is not None else account.get("uuid")
        if not isinstance(owner, str) or _UUID.fullmatch(owner) is None:
            raise DigitalOceanError(None, "account without a uuid")
        try:
            droplet = self.droplet(droplet_id, deadline)
        except DigitalOceanError as exc:
            if exc.status != 404:
                raise
            droplet = None
        return {"billing_uuid": owner.lower(), "billing_kind": "team" if team else "user",
                "droplet_id": int(droplet_id),
                "droplet_held": droplet is not None and droplet["id"] == int(droplet_id),
                "metadata_id": self.metadata_droplet_id(deadline), "droplet": droplet}

    def lines(self, invoice: str, period: str, droplet_id: int,
              deadline: Deadline | float) -> dict[str, Any]:
        """One invoice's lines (``preview`` for the month so far): this droplet's, parsed
        strictly, and how many other lines it had, unparsed."""
        deadline = _deadline(deadline)
        target = "preview" if invoice == "preview" else invoice
        if target != "preview" and _UUID.fullmatch(target) is None:
            raise DigitalOceanError(None, "invoice uuid is malformed")
        rows, _ = self._pages(f"/v2/customers/my/invoices/{target}", "invoice_items",
                              deadline)
        mine = [_line(row, period, target) for row in rows if _is_mine(row, droplet_id)]
        return {"mine": mine, "others": len(rows) - len(mine)}

    def billing(self, droplet_id: int, *, since: str, done: list[str],
                budget_s: float) -> dict[str, Any]:
        """One window's billing observation, whole or not at all, within ``budget_s``.

        Returns the identity (account, droplet, metadata), the droplet itself, the
        published sizes, the preview's period and this droplet's preview lines, up to
        ``INVOICES_PER_READ`` closed invoices not yet reconciled (every one with a
        period at or after ``since`` and a uuid not in ``done``, oldest first, each
        with this droplet's lines), how many invoices are still waiting, and the
        billing history's first page, read leniently. One call, so a journal records
        and replays it as one read.
        """
        deadline = Deadline(budget_s)
        identity = self.identity(droplet_id, deadline)
        droplet = identity.pop("droplet")
        rows, listing = self._pages("/v2/customers/my/invoices", "invoices", deadline)
        preview = listing.get("invoice_preview")
        period = _period(preview.get("invoice_period") if isinstance(preview, dict) else None,
                         "invoice_period")
        invoices = [inv for inv in (_invoice(row) for row in rows) if inv is not None]
        waiting = sorted((inv for inv in invoices
                          if inv["period"] >= since and inv["uuid"] not in done),
                         key=lambda inv: (inv["period"], inv["uuid"]))
        closed = []
        for invoice in waiting[:INVOICES_PER_READ]:
            found = self.lines(invoice["uuid"], invoice["period"], droplet_id, deadline)
            closed.append({**invoice, **found})
        current = self.lines("preview", period, droplet_id, deadline)
        sizes = self.sizes(deadline)
        raw = self._get(f"/v2/customers/my/billing_history?per_page={PER_PAGE}&page=1",
                        deadline)
        history = raw.get("billing_history") if isinstance(raw, dict) else None
        entries = [e for e in (_entry(row) for row in history or []) if e is not None]
        return {"identity": identity, "droplet": droplet, "sizes": sizes,
                "period": period, "lines": current["mine"], "others": current["others"],
                "closed": closed, "waiting": len(waiting) - len(closed),
                "unreadable_invoices": len(rows) - len(invoices), "history": entries}
