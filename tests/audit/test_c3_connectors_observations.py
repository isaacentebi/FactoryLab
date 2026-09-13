"""Cold audit round three, seat 3: what a connector body may say, and what a registered
observation costs the kernel.

Each test reproduces one finding in docs/audits/v3/defects-fable.md and fails on the
audited commit. Nothing here touches a network.
"""

from types import SimpleNamespace

import pytest

from factorylab.cortex.request import Return
from factorylab.world.connector import ConnectorProxy
from tests.conftest import make_runtime
from tests.runtime.test_connectors import decision, register
from tests.runtime.test_loop import _consequence_produce, _consequence_runtime
from tests.world.test_connector import Transport

pytestmark = pytest.mark.xfail(strict=False, reason="round three, open: docs/audits/v3/triage.md")


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def deny(*args, **kwargs):
        pytest.fail("real network is forbidden")
    monkeypatch.setattr("socket.create_connection", deny)
    monkeypatch.setattr("socket.getaddrinfo", deny)


@pytest.mark.parametrize("body", [b"65000.5", b"OK", b"1"])
def test_finding_7_a_short_connector_body_makes_any_return_that_mentions_it_malformed(
    monkeypatch, body,
):
    """Final outputs containing the raw body are refused so a body cannot become durable
    output. For a numeric or one-word body (a price feed, a health endpoint) the rule
    refuses every later return whose rationale or size contains that text: the one thing
    the population fetched is the one thing it may not say."""
    rt = make_runtime()
    register(rt, monkeypatch)
    rt.connector_proxy = ConnectorProxy(rt.m.connectors, Transport(body))
    handle = decision(rt)
    text = body.decode()
    calls = iter([
        Return(handle, {}, 0, "ok", tool_calls=({"tool": "connector.fetch",
                "args": {"id": "source", "path": "/price"}},)),
        Return(handle, {"action": "order", "coin": "BTC", "side": "buy", "size": "0.001",
                        "rationale": f"the source says {text}; buying a small position"},
               0, "ok"),
    ])
    monkeypatch.setattr(rt, "_invoke_compute", lambda *a: next(calls))
    req = rt._request(handle, "Produce", {}, {"type": "object"}, 10**15, "verdict")
    ret = rt._invoke("seed-decider", req, "producer")
    assert ret.status == "ok", ret.outputs
    assert ret.outputs["action"] == "order"


def test_finding_8_registered_observations_no_card_names_run_at_every_window_close():
    """Every registered observation is executed in the jail at every window close, whether
    or not a card names it, under the tool limits (5 s wall each). The trial that admits
    one costs the reserve $0.10 once; the kernel pays up to 5 s per registration per
    window for the life of the world."""
    rt = _consequence_runtime()
    rt._manage_reserve_window()
    _consequence_produce(rt)
    rt._close_price_window()
    runs = []
    rt.observation_runner = SimpleNamespace(
        run=lambda code, facts: (runs.append(code), (0.5, None))[1])
    for i in range(5):
        rt.registered_observations[f"unused-{i}"] = {
            "description": "d", "units": "u", "unit_range": [0.0, 1.0],
            "code": f"def observe(facts): return {i}", "version": 1,
            "provenance": "population", "history": [1]}
    assert not any(card.observation.startswith("unused-") for card in rt.charter.cards)
    rt._close_price_window()
    assert runs == [], f"{len(runs)} observations no card names ran at the window close"
