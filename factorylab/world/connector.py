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

DEFAULT_DENYLIST = (
    "hyperliquid.xyz", "openrouter.ai", "anthropic.com", "venice.ai",
    "localhost", "127.0.0.0/8", "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16",
    "169.254.0.0/16", "::1/128", "fc00::/7", "fe80::/10",
)


class ConnectorRefused(ValueError):
    """A bounded public refusal contains no remote body or host exception text."""


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


def check_host(host: str, denylist: tuple[str, ...]) -> None:
    """Deny exact hosts, their subdomains, nonpublic addresses and configured IP networks."""
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        address = None
    if address is not None and (not address.is_global or address.is_multicast
                                or getattr(address, "ipv4_mapped", None) is not None):
        raise ConnectorRefused("private or nonpublic address")
    for entry in denylist:
        try:
            network = ipaddress.ip_network(entry)
        except ValueError:
            if host == entry or host.endswith("." + entry):
                raise ConnectorRefused("denylisted host") from None
        else:
            if address is not None and address in network:
                raise ConnectorRefused("denylisted address range")
    if host == "localhost" or host.endswith((".localhost", ".local", ".internal")):
        raise ConnectorRefused("private host")


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


class _DeadlineReader(io.RawIOBase):
    """Every socket read observes the same deadline, including HTTP header parsing."""

    def __init__(self, sock, deadline):
        self.sock, self.deadline = sock, deadline
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
        self.sock.settimeout(remaining)
        return self.raw.readinto(buffer)


class _DeadlineSocket:
    """HTTP buffering cannot reset the total read deadline with each arriving byte."""

    def __init__(self, sock, deadline):
        self.sock, self.deadline = sock, deadline

    def __getattr__(self, name):
        return getattr(self.sock, name)

    def makefile(self, *args, **kwargs):
        return io.BufferedReader(_DeadlineReader(self.sock, self.deadline), buffer_size=1)

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
        """DNS, connect, headers and body share a deadline; read at most cap+1 bytes."""
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
            check_host(address, denylist)
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
            connection.sock = _DeadlineSocket(secured, deadline)
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

    def __init__(self, bounds, transport=None):
        self.bounds = bounds
        self.transport = transport if transport is not None else HTTPSTransport()

    def validate(self, origin: str, path: str) -> str:
        """Only a permitted origin and path can reach a transport."""
        host = origin_host(origin)
        validate_path(path)
        check_host(host, self.bounds.origin_denylist)
        return host

    def fetch(self, origin: str, path: str) -> dict:
        """Return bounded UTF-8 text or a safe refusal; oversize bodies are never returned."""
        started = time.monotonic()
        try:
            host = self.validate(origin, path)
            response = self.transport.get(
                host, path, max_bytes=self.bounds.max_bytes, timeout_s=self.bounds.timeout_s,
                denylist=self.bounds.origin_denylist,
            )
            if time.monotonic() - started > self.bounds.timeout_s:
                raise ConnectorRefused("connector timeout")
            size = len(response.body)
            if size > self.bounds.max_bytes:
                return {"error": "body exceeds max_bytes", "status": response.status,
                        "bytes": self.bounds.max_bytes + 1}
            if not 200 <= response.status < 300:
                return {"error": "HTTP status refused (redirects disabled)",
                        "status": response.status, "bytes": size}
            return {"body": response.body.decode("utf-8", errors="replace"),
                    "status": response.status, "bytes": size}
        except ConnectorRefused as exc:
            return {"error": str(exc), "status": "refused", "bytes": 0}
        except (TimeoutError, subprocess.TimeoutExpired):
            return {"error": "connector timeout", "status": "refused", "bytes": 0}
        except Exception:
            return {"error": "connector transport failed", "status": "refused", "bytes": 0}
