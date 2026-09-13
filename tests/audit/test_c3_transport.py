"""Cold audit round three, seat 3: the connector transport against adversarial framing.

Reproduces one finding in docs/audits/v3/defects-fable.md with in-memory sockets; nothing
here opens a real connection (the socket and resolver are replaced before any call).
"""

from types import SimpleNamespace

import pytest

import factorylab.world.connector as connector_module
from factorylab.world.connector import HTTPSTransport

pytestmark = pytest.mark.xfail(strict=False, reason="round three, open: docs/audits/v3/triage.md")


class Raw:
    """A socket file that hands out one byte per read and counts what was consumed."""

    def __init__(self, data):
        self.data, self.pos = data, 0

    def readinto(self, buf):
        if self.pos >= len(self.data):
            return 0
        buf[0] = self.data[self.pos]
        self.pos += 1
        return 1

    def close(self):
        pass


class Sock:
    def __init__(self, raw):
        self.raw = raw

    def settimeout(self, _s):
        pass

    def sendall(self, _data):
        pass

    def makefile(self, *_a, **_k):
        return self.raw

    def close(self):
        pass


def test_finding_11_response_headers_are_not_bounded_by_max_bytes(monkeypatch):
    """``max_bytes`` bounds the body only; ``http.client`` buffers every header line before
    the body read starts (its own per-line and 100-header limits still allow megabytes).
    A hostile origin makes the proxy read and hold far more than the manifest bound
    on the world's behalf, on every call, at the connector price."""
    max_bytes = 1000
    wire = (b"HTTP/1.1 200 OK\r\n"
            + b"".join(b"X-Pad-%d: %s\r\n" % (i, b"a" * 60000) for i in range(90))
            + b"Content-Length: 1\r\n\r\ny")
    raw = Raw(wire)
    monkeypatch.setattr(connector_module.subprocess, "run",
                        lambda *a, **k: SimpleNamespace(stdout='["93.184.216.34"]'))
    monkeypatch.setattr(connector_module.socket, "create_connection", lambda *a, **k: Sock(raw))
    monkeypatch.setattr(connector_module.ssl, "create_default_context",
                        lambda: SimpleNamespace(wrap_socket=lambda sock, **k: sock))
    try:
        HTTPSTransport().get("example.org", "/", max_bytes=max_bytes, timeout_s=10, denylist=())
    except Exception:
        pass  # a refusal is fine; what matters is how much the origin could make us read
    assert raw.pos <= 64 * 1024 + max_bytes, (
        f"the transport consumed {raw.pos} bytes of a response bounded to {max_bytes}")
