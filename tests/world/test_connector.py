"""Connector policy and HTTP framing are exercised entirely with offline transports."""

import io
import json
import subprocess
from dataclasses import replace
from types import SimpleNamespace

import pytest

from factorylab.runtime.worlds import ConnectorsSpec
from factorylab.world.connector import (
    DEFAULT_DENYLIST,
    ConnectorProxy,
    ConnectorRefused,
    ConnectorResponse,
    HTTPSTransport,
    _DeadlineReader,
    check_address,
    check_host,
    origin_host,
)


class Transport:
    def __init__(self, body=b'{"value": 42}', status=200, error=None):
        self.body, self.status, self.error = body, status, error
        self.calls = []

    def get(self, host, path, **bounds):
        self.calls.append((host, path, bounds))
        if self.error:
            raise self.error
        return ConnectorResponse(self.status, self.body)


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def deny(*args, **kwargs):
        pytest.fail("real network is forbidden")
    monkeypatch.setattr("socket.create_connection", deny)
    monkeypatch.setattr("socket.getaddrinfo", deny)


@pytest.mark.parametrize("origin", [
    "http://example.org", "https://example.org/", "https://u:p@example.org",
    "https://example.org:443", "https://example.org?q=1", "https://example.org#x",
    "https://example.org\\evil", "https://example.org\n", "https://[::1]", "file:///tmp/x",
])
def test_origin_components_are_refused_before_transport(origin):
    transport = Transport()
    result = ConnectorProxy(ConnectorsSpec(), transport).fetch(origin, "/")
    assert result["error"] and transport.calls == []
    with pytest.raises(ConnectorRefused):
        origin_host(origin)


@pytest.mark.parametrize("host", [
    "openrouter.ai", "api.openrouter.ai", "api.hyperliquid.xyz", "127.0.0.1", "10.1.1.1",
    "169.254.169.254", "localhost", "service.local", "0.0.0.0", "224.0.0.1",
])
def test_denylisted_and_nonpublic_hosts_never_dispatch(host):
    transport = Transport()
    result = ConnectorProxy(ConnectorsSpec(), transport).fetch(f"https://{host}", "/")
    assert result["error"] and not transport.calls


@pytest.mark.parametrize("path", ["https://evil.org/", "//evil.org/x", "/x#f", "/x\r\nX: y",
                                 "/\\evil", "", "/a b"])
def test_path_cannot_escape_the_origin(path):
    transport = Transport()
    result = ConnectorProxy(ConnectorsSpec(), transport).fetch("https://example.org", path)
    assert "error" in result and not transport.calls


def test_body_cap_exact_and_oversize_refusal():
    bounds = replace(ConnectorsSpec(), max_bytes=8)
    transport = Transport(b"12345678")
    proxy = ConnectorProxy(bounds, transport)
    assert proxy.fetch("https://example.org", "/x?q=1")["body"] == "12345678"
    assert transport.calls == [("example.org", "/x?q=1", {
        "max_bytes": 8, "timeout_s": 10, "denylist": bounds.origin_denylist})]
    transport.body += b"9"
    result = proxy.fetch("https://example.org", "/")
    assert result == {"error": "body exceeds max_bytes", "status": 200, "bytes": 9}


@pytest.mark.parametrize("status", [301, 302, 401, 500])
def test_non_2xx_answers_report_their_status_and_never_make_a_second_request(status):
    """An origin that answered within the bounds is not an error: a data API whose root
    is 404, 403 or 301 answered as truly as a 200 front page. The status is always
    reported, no redirect is ever followed, and one fetch is exactly one request."""
    transport = Transport(b"private remote error", status)
    result = ConnectorProxy(ConnectorsSpec(), transport).fetch("https://example.org", "/")
    assert "error" not in result and result["status"] == status
    assert result["bytes"] == len(b"private remote error")
    assert len(transport.calls) == 1


def test_an_answer_outside_the_bounds_is_still_an_error_whatever_its_status():
    """Only the bounds refuse: an oversize body is refused with no body at any status."""
    bounds = replace(ConnectorsSpec(), max_bytes=4)
    transport = Transport(b"12345", 404)
    result = ConnectorProxy(bounds, transport).fetch("https://example.org", "/")
    assert result == {"error": "body exceeds max_bytes", "status": 404, "bytes": 5}


@pytest.mark.parametrize("error,reason", [
    (TimeoutError("remote secret"), "connector timeout"),
    (subprocess.TimeoutExpired("resolver", 1), "connector timeout"),
    (OSError("remote secret"), "connector transport failed"),
])
def test_failures_are_safe_public_reasons(error, reason):
    result = ConnectorProxy(ConnectorsSpec(), Transport(error=error)).fetch(
        "https://example.org", "/")
    assert result == {"error": reason, "status": "refused", "bytes": 0}


def test_dns_all_answers_checked_before_connecting(monkeypatch):
    monkeypatch.setattr("factorylab.world.connector.subprocess.run", lambda *a, **k:
                        SimpleNamespace(stdout='["93.184.216.34", "127.0.0.1"]'))
    result = ConnectorProxy(ConnectorsSpec()).fetch("https://example.org", "/")
    assert "nonpublic" in result["error"]


def test_https_uses_pinned_ip_verified_host_get_and_only_public_headers(monkeypatch):
    wire = b"HTTP/1.1 200 OK\r\nContent-Length: 3\r\nConnection: close\r\n\r\nabc"

    class Sock:
        def __init__(self):
            self.sent = b""
            self.timeouts = []

        def settimeout(self, seconds):
            self.timeouts.append(seconds)

        def sendall(self, data):
            self.sent += data

        def makefile(self, *args, **kwargs):
            return io.BytesIO(wire)

        def close(self):
            pass

    sock = Sock()
    connected, verified, resolved = [], [], []

    def resolver(*args, **kwargs):
        resolved.append(kwargs)
        return SimpleNamespace(stdout=json.dumps(["93.184.216.34"]))

    def connect(address, timeout):
        connected.append(address)
        return sock

    def tls(raw, *, server_hostname):
        assert raw is sock
        verified.append(server_hostname)
        return sock

    monkeypatch.setattr("factorylab.world.connector.subprocess.run", resolver)
    monkeypatch.setattr("socket.create_connection", connect)
    monkeypatch.setattr("ssl.create_default_context", lambda: SimpleNamespace(wrap_socket=tls))
    result = ConnectorProxy(ConnectorsSpec()).fetch("https://example.org", "/data?q=1")
    assert result == {"body": "abc", "bytes": 3, "status": 200}
    assert connected == [("93.184.216.34", 443)] and verified == ["example.org"]
    assert resolved[0]["timeout"] == 10 and resolved[0]["env"] == {}
    lines = sock.sent.decode().split("\r\n")
    assert lines[0] == "GET /data?q=1 HTTP/1.1"
    assert set(lines[1:]) == {"Host: example.org", "Accept: */*",
                             "User-Agent: FactoryLab-Connector/1", ""}
    assert all(0 < timeout <= 10 for timeout in sock.timeouts)


def test_total_deadline_applies_to_each_header_read(monkeypatch):
    sock = SimpleNamespace(makefile=lambda *a, **k: io.BytesIO(b"HTTP"),
                           settimeout=lambda _: None)
    reader = _DeadlineReader(sock, 10, 1024)
    monkeypatch.setattr("factorylab.world.connector.time.monotonic", lambda: 11)
    with pytest.raises(TimeoutError):
        reader.readinto(bytearray(1))
    reader.close()


def test_public_ipv6_dns_and_configured_networks():
    check_address("2606:4700:4700::1111", ())
    for address in ("::1", "fc00::1", "fe80::1", "::ffff:127.0.0.1"):
        with pytest.raises(ConnectorRefused):
            check_address(address, ())
    with pytest.raises(ConnectorRefused, match="denylisted address"):
        check_address("93.184.216.34", ("93.184.216.0/24",))
    # However public it resolves to be, an origin names a host the TLS name is checked against.
    with pytest.raises(ConnectorRefused, match="not an address"):
        check_host("93.184.216.34", ())


@pytest.mark.parametrize("host", [
    "api.hyperliquid.xyz", "rpc.hyperliquid.xyz", "api.hyperliquid-testnet.xyz",
    "rpc.hyperliquid-testnet.xyz", "mainnet.base.org", "sepolia.base.org", "openrouter.ai",
    "api.venice.ai", "api.cdp.coinbase.com", "api.anthropic.com", "node.rpc.hyperliquid.xyz",
    "10.0.0.1", "127.0.0.1", "192.168.1.1", "::1", "localhost",
])
def test_default_denylist_covers_the_rails_on_both_networks_and_every_address(host):
    with pytest.raises(ConnectorRefused):
        check_host(host, DEFAULT_DENYLIST)


def test_public_hosts_pass_and_a_registered_seller_is_denied_while_it_is_registered():
    check_host("example.org", DEFAULT_DENYLIST)
    transport = Transport()
    registered = []
    proxy = ConnectorProxy(ConnectorsSpec(), transport, sellers=lambda: list(registered))
    assert proxy.fetch("https://seller.example.net", "/v1/models")["body"]
    registered.append("https://seller.example.net/v1")
    assert proxy.fetch("https://seller.example.net", "/v1/models")["error"] == "denylisted host"
    assert proxy.fetch("https://api.seller.example.net", "/")["error"] == "denylisted host"
    assert proxy.fetch("https://example.org", "/")["body"]


def test_transport_caps_stream_read_without_content_length(monkeypatch):
    # Reuse actual http.client framing with in-memory sockets; never a network request.
    class Sock:
        def settimeout(self, _):
            pass

        def sendall(self, _):
            pass

        def makefile(self, *args, **kwargs):
            return io.BytesIO(b"HTTP/1.1 200 OK\r\n\r\n" + b"a" * 1000)

        def close(self):
            pass

    monkeypatch.setattr("factorylab.world.connector.subprocess.run", lambda *a, **k:
                        SimpleNamespace(stdout='["93.184.216.34"]'))
    monkeypatch.setattr("socket.create_connection", lambda *a, **k: Sock())
    monkeypatch.setattr("ssl.create_default_context", lambda:
                        SimpleNamespace(wrap_socket=lambda raw, **k: raw))
    response = HTTPSTransport().get("example.org", "/", max_bytes=8, timeout_s=10, denylist=())
    assert response.body == b"a" * 9
