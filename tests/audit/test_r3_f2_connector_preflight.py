"""Round three, group F2, triage T10: admission preflights reachability, not a 2xx root.

Connector admission preflights ``GET /`` and refused any status outside 200-299, so every
data API whose root answers 404, 403 or 301 — Kraken, CoinGecko, Coinbase, Binance — was
unregistrable while HTML front pages passed. An origin that answered within the manifest's
bounds has proved exactly what the preflight is for; the status is reported, not judged.

Offline, except the last test, which reads one public API root with the real transport and
runs only when network tests are selected.
"""

from dataclasses import replace

import pytest

from factorylab.cortex.request import Return
from factorylab.kernel.queue import PropensityRecord
from factorylab.runtime.worlds import ConnectorsSpec
from factorylab.world.connector import ConnectorProxy, ConnectorResponse
from tests.conftest import make_runtime

KRAKEN_ROOT = b'{"error":["EGeneral:Unknown method"]}'


class RootTransport:
    """A data API: its documented paths answer 200 and its bare root answers 404."""

    def __init__(self, status=404, body=KRAKEN_ROOT):
        self.status, self.body, self.calls = status, body, []

    def get(self, host, path, **bounds):
        self.calls.append((host, path))
        if path == "/":
            return ConnectorResponse(self.status, self.body)
        return ConnectorResponse(200, b'{"result": {"XXBTZUSD": {"c": ["100", "1"]}}}')


def _register(rt, monkeypatch, origin, cid="kraken"):
    rt._manage_reserve_window()
    handle = rt.queue.open(
        actor="seed-decider", event_id="connector-preflight",
        propensity=PropensityRecord(("seed-decider",), (1.,), "seed-decider", 0,
                                    "seed-decider", "test"),
        channel="verdict", deadline_ns=10**15, parent_handle=None, cost_ceiling=10_000_000)
    rt.handle_to_assembly[handle] = "seed-decider"
    monkeypatch.setattr(rt, "_committee_eligible", lambda: {
        "seed-decider": "producer", "eval-a": "evaluator", "meta-a": "meta"})
    rt._apply_registrations(handle, Return(handle, {"register": [{
        "kind": "connector", "id": cid, "description": "Public market data",
        "origin": origin}]}, 0, "ok"))
    return handle


@pytest.mark.parametrize("status", [404, 403, 301])
def test_t10_an_api_whose_root_is_not_2xx_can_still_be_admitted(monkeypatch, status):
    """Kraken 404, Binance 403, Coinbase 301: each answered, so each may be voted on."""
    rt = make_runtime()
    rt.connector_proxy = ConnectorProxy(rt.m.connectors, RootTransport(status))
    _register(rt, monkeypatch, "https://api.kraken.com")
    assert rt.registry.get("connector:kraken").version == 1, (
        f"an origin answering {status} at its root was refused admission")
    assert rt.wallet.check_conservation()


def test_t10_the_preflight_is_still_priced_and_still_reads_only_the_root(monkeypatch):
    """Admission pays the flat price for exactly one root read."""
    rt = make_runtime()
    transport = RootTransport()
    rt.connector_proxy = ConnectorProxy(rt.m.connectors, transport)
    _register(rt, monkeypatch, "https://api.kraken.com")
    calls = [i for i in rt.ledger._recovery_items() if i["kind"] == "connector.call"]
    assert [c["path"] for c in calls] == ["/"] and transport.calls == [("api.kraken.com", "/")]
    assert calls[0]["cost"] == rt.m.connectors.call_price_micro
    assert calls[0]["status"] == 404 and calls[0]["bytes"] == len(KRAKEN_ROOT)


def test_t10_an_origin_that_does_not_answer_within_the_bounds_is_still_refused(monkeypatch):
    """Only the bounds refuse: an oversize root never reaches a vote."""
    rt = make_runtime()
    rt.connector_proxy = ConnectorProxy(replace(rt.m.connectors, max_bytes=2), RootTransport())
    _register(rt, monkeypatch, "https://api.kraken.com")
    assert not rt.registry.available("connector")
    assert "max_bytes" in rt.registration_feedback[-1]["reason"]


@pytest.mark.network
def test_t10_a_real_404_root_api_answers_within_the_bounds():
    """The proxy alone, against a real public API root, with the real transport."""
    result = ConnectorProxy(ConnectorsSpec()).fetch("https://api.kraken.com", "/")
    assert "error" not in result, result
    assert result["status"] not in range(200, 300) and result["bytes"] <= 262144
