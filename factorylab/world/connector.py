"""Credential-free, bounded HTTPS reads outside the population jail."""

from __future__ import annotations

import http.client
import io
import ipaddress
import json
import re
import socket
import ssl
import subprocess
import sys
import time
from dataclasses import dataclass
from urllib.parse import urlsplit

from hyperliquid.utils.constants import MAINNET_API_URL, TESTNET_API_URL

from factorylab.world.evm import BASE, BASE_SEPOLIA, HYPEREVM, HYPEREVM_TESTNET
from factorylab.world.market import DISCOVERY_URL
from factorylab.world.x402 import VENICE_URL


class ConnectorRefused(ValueError):
    """A bounded public refusal contains no remote body or host exception text."""


def url_host(url: str) -> str | None:
    """Return the lowercase host of a rail or seller URL, or None when it names none."""
    try:
        host = urlsplit(url).hostname
    except ValueError:
        return None
    return host.lower() if host else None


# Every endpoint this world's own rails talk to, taken from the modules that
# define them, so a renamed or added rail cannot silently become fetchable.
RAIL_URLS = (
    MAINNET_API_URL, TESTNET_API_URL,                                # world/exchange.py venue
    HYPEREVM.rpc, HYPEREVM_TESTNET.rpc, BASE.rpc, BASE_SEPOLIA.rpc,  # world/evm.py RPC
    "https://openrouter.ai/api/v1",                                  # world/openrouter.py
    "https://api.anthropic.com",                                     # world/models.py
    VENICE_URL,                                                      # world/x402.py
    DISCOVERY_URL,                                                   # world/market.py index
)

DEFAULT_DENYLIST = tuple(dict.fromkeys(h for h in map(url_host, RAIL_URLS) if h))


def origin_host(origin: str) -> str:
    """Accept only a canonical HTTPS origin with a hostname and no other URL components."""
    if not isinstance(origin, str) or len(origin) > 260:
        raise ConnectorRefused("origin must be https://<host>")
    parts = urlsplit(origin)
    host = parts.hostname
    if (not host or parts.scheme != "https" or origin != f"https://{host}"
            or not re.fullmatch(r"[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?", host)
            or any(not label or len(label) > 63 or label.startswith("-") or label.endswith("-")
                   for label in host.split("."))):
        raise ConnectorRefused("origin must be https://<host> without credentials, port or path")
    return host


def validate_denylist(entries) -> None:
    """Deny entries are hostnames or IP networks, never URLs or empty patterns."""
    if not isinstance(entries, (tuple, list)):
        raise ValueError("connectors.origin_denylist must be a list")
    for entry in entries:
        if not isinstance(entry, str) or not entry:
            raise ValueError("invalid connector denylist entry")
        try:
            ipaddress.ip_network(entry)
        except ValueError:
            origin_host(f"https://{entry}")


def check_address(address: str, denylist: tuple[str, ...]) -> None:
    """Only a globally routable unicast address outside every configured network is reached."""
    ip = ipaddress.ip_address(address)
    if (not ip.is_global or ip.is_loopback or ip.is_private or ip.is_link_local
            or ip.is_multicast or getattr(ip, "ipv4_mapped", None) is not None):
        raise ConnectorRefused("private or nonpublic address")
    for entry in denylist:
        try:
            network = ipaddress.ip_network(entry)
        except ValueError:
            continue
        if ip in network:
            raise ConnectorRefused("denylisted address range")


def check_host(host: str, denylist: tuple[str, ...]) -> None:
    """Deny bare addresses, private names, denylisted hosts and any of their subdomains."""
    try:
        ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        # A literal address names no host to verify TLS against and skips DNS policy;
        # loopback and private ranges are refused here and again on every DNS answer.
        check_address(host, denylist)
        raise ConnectorRefused("origin must name a host, not an address")
    if host == "localhost" or host.endswith((".localhost", ".local", ".internal")):
        raise ConnectorRefused("private host")
    for entry in denylist:
        try:
            ipaddress.ip_network(entry)
        except ValueError:
            if host == entry or host.endswith("." + entry):
                raise ConnectorRefused("denylisted host") from None


def validate_path(path: str) -> None:
    """A relative absolute-path reference cannot change origin or inject request headers."""
    if (not isinstance(path, str) or not path.startswith("/") or path.startswith("//")
            or "\\" in path or "#" in path or len(path) > 8192
            or any(ord(c) <= 32 or ord(c) >= 127 for c in path)):
        raise ConnectorRefused("path must start with one / and contain no fragment or whitespace")


@dataclass(frozen=True)
class ConnectorResponse:
    """A response holds bounded bytes only in the caller's process memory."""

    status: int
    body: bytes


# http.client buffers every response header before the body read begins, and its
# own limits (100 lines of 64 KiB) are megabytes. The origin's whole answer,
# headers included, is bounded by max_bytes plus this one-line allowance.
HEADER_ALLOWANCE_BYTES = 65536


class _DeadlineReader(io.RawIOBase):
    """Every socket read observes the same deadline and the same total byte budget."""

    def __init__(self, sock, deadline, budget):
        self.sock, self.deadline, self.budget = sock, deadline, budget
        self.consumed = 0
        self.raw = sock.makefile("rb", buffering=0)

    def close(self):
        self.raw.close()
        super().close()

    def readable(self):
        return True

    def readinto(self, buffer):
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError
        allowed = self.budget - self.consumed
        if allowed <= 0:
            raise ConnectorRefused("response exceeds max_bytes with headers")
        self.sock.settimeout(remaining)
        read = self.raw.readinto(memoryview(buffer)[:allowed])
        self.consumed += read or 0
        return read


class _DeadlineSocket:
    """HTTP buffering cannot reset the total read deadline or budget with each byte."""

    def __init__(self, sock, deadline, budget):
        self.sock, self.deadline, self.budget = sock, deadline, budget

    def __getattr__(self, name):
        return getattr(self.sock, name)

    def makefile(self, *args, **kwargs):
        return io.BufferedReader(
            _DeadlineReader(self.sock, self.deadline, self.budget), buffer_size=1)

    def sendall(self, data):
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError
        self.sock.settimeout(remaining)
        return self.sock.sendall(data)


class HTTPSTransport:
    """GET stays in the runtime process, with checked DNS, pinned IP and verified TLS."""

    def get(self, host: str, path: str, *, max_bytes: int, timeout_s: int,
            denylist: tuple[str, ...]) -> ConnectorResponse:
        """DNS, connect, headers and body share one deadline and one total byte budget."""
        deadline = time.monotonic() + timeout_s
        # libc DNS has no deadline API. Only resolution uses a disposable, bounded
        # helper process; HTTP, TLS and policy remain in this runtime process.
        resolved = subprocess.run(
            [sys.executable, "-I", "-c",
             "import json,socket,sys; print(json.dumps(sorted({r[4][0] for r in "
             "socket.getaddrinfo(sys.argv[1],443,type=socket.SOCK_STREAM)})))", host],
            capture_output=True, text=True, timeout=timeout_s, check=True, env={},
        )
        addresses = json.loads(resolved.stdout)
        if not addresses:
            raise ConnectorRefused("DNS returned no addresses")
        for address in addresses:
            check_address(address, denylist)
        connection = http.client.HTTPSConnection(host, timeout=timeout_s)
        try:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError
            raw = socket.create_connection((addresses[0], 443), timeout=remaining)
            try:
                context = ssl.create_default_context()
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError
                raw.settimeout(remaining)
                secured = context.wrap_socket(raw, server_hostname=host)
            except BaseException:
                raw.close()
                raise
            connection.sock = _DeadlineSocket(
                secured, deadline, max_bytes + HEADER_ALLOWANCE_BYTES)
            # Host is required HTTP framing. These are the only application headers.
            connection.putrequest("GET", path, skip_accept_encoding=True)
            connection.putheader("Accept", "*/*")
            connection.putheader("User-Agent", "FactoryLab-Connector/1")
            connection.endheaders()
            response = connection.getresponse()
            try:
                chunks = bytearray()
                while len(chunks) <= max_bytes:
                    if time.monotonic() >= deadline:
                        raise TimeoutError
                    chunk = response.read1(min(65536, max_bytes + 1 - len(chunks)))
                    if not chunk:
                        break
                    chunks.extend(chunk)
                return ConnectorResponse(response.status, bytes(chunks))
            finally:
                response.close()
        finally:
            connection.close()


class FakeConnectorTransport:
    """Scripted HTTPS reads are deterministic and never open a socket."""

    def get(self, host: str, path: str, **bounds) -> ConnectorResponse:
        """Return the scripted body through the same cap and status validation as live reads."""
        return ConnectorResponse(200, b'{"value": 42}')


class ConnectorProxy:
    """No keys, credentials, cookies, cache, redirect followups or user-supplied headers."""

    def __init__(self, bounds, transport=None, sellers=None):
        self.bounds = bounds
        self.transport = transport if transport is not None else HTTPSTransport()
        self.sellers = sellers if sellers is not None else (lambda: ())

    def denylist(self) -> tuple[str, ...]:
        """The manifest's entries plus the host of every seller registered so far."""
        return tuple(self.bounds.origin_denylist) + tuple(
            dict.fromkeys(h for h in map(url_host, self.sellers()) if h))

    def validate(self, origin: str, path: str) -> str:
        """Only a permitted origin and path can reach a transport."""
        host = origin_host(origin)
        validate_path(path)
        check_host(host, self.denylist())
        return host

    def fetch(self, origin: str, path: str) -> dict:
        """An origin that answered within the bounds returns its status and bounded text.

        The status is reported, never judged: a data API whose root is 404, 403 or
        301 answered as truly as an HTML front page that is 200, and no redirect is
        ever followed. Only an unanswered, oversize or out-of-time read is an error.
        """
        started = time.monotonic()
        try:
            host = self.validate(origin, path)
            response = self.transport.get(
                host, path, max_bytes=self.bounds.max_bytes, timeout_s=self.bounds.timeout_s,
                denylist=self.denylist(),
            )
            if time.monotonic() - started > self.bounds.timeout_s:
                raise ConnectorRefused("connector timeout")
            size = len(response.body)
            if size > self.bounds.max_bytes:
                return {"error": "body exceeds max_bytes", "status": response.status,
                        "bytes": self.bounds.max_bytes + 1}
            return {"body": response.body.decode("utf-8", errors="replace"),
                    "status": response.status, "bytes": size}
        except ConnectorRefused as exc:
            return {"error": str(exc), "status": "refused", "bytes": 0}
        except (TimeoutError, subprocess.TimeoutExpired):
            return {"error": "connector timeout", "status": "refused", "bytes": 0}
        except Exception:
            return {"error": "connector transport failed", "status": "refused", "bytes": 0}
