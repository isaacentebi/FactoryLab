"""Fake x402 data authorizations exercise the existing reserve rail without live payment."""

import json
from dataclasses import replace

import pytest

from factorylab.cortex.request import Return
from factorylab.world.connector import ConnectorProxy, ConnectorResponse
from factorylab.world.market import PaymentOutcomeUnknown, X402Provider
from tests.conftest import make_runtime
from tests.runtime.test_connectors import decision, ledger_items, recording_journal
from tests.world.test_market import TEST_KEY, SellerHTTP


class DataTransport:
    def __init__(self, amount=1734, paid=None):
        self.seller = SellerHTTP(amount=amount)
        self.calls = []
        self.paid = paid or ConnectorResponse(200, b"paid fact")

    def __call__(self, origin, path, signature=None):
        self.calls.append((origin, path, signature))
        if signature is None:
            return ConnectorResponse(402, json.dumps(self.seller.quote).encode())
        if isinstance(self.paid, Exception):
            raise self.paid
        return self.paid

    def get(self, host, path, *, payment_signature=None, **bounds):
        return self(f"https://{host}", path, payment_signature)


def paid_runtime(monkeypatch, transport=None):
    rt = make_runtime()
    rt._manage_reserve_window()
    transport = transport or DataTransport()
    rt.market.target = X402Provider(private_key=TEST_KEY, transport=transport.seller)
    rt.connector_proxy = ConnectorProxy(rt.m.connectors, transport)
    monkeypatch.setattr(rt, "_committee_eligible", lambda: {
        "eval-a": "evaluator", "meta-a": "meta"})
    handle = decision(rt)
    rt._apply_registrations(handle, Return(handle, {"register": [{
        "kind": "connector", "id": "source", "description": "Paid data",
        "origin": "https://example.org", "pay": "x402", "max_call_usd": "0.002",
        "preflight_path": "/data",
        "predicted_effect": {"card_id": "cost_per_return", "direction": "decrease", "window": 1},
    }]}, 0, "ok"))
    assert rt.registry.available("connector"), rt.registration_feedback
    assert len(transport.calls) == 1 and transport.calls[0][2] is None
    transport.calls.clear()
    rt.ledger.active = True
    return rt, transport


def test_paid_fetch_debits_the_runtime_wallet_before_return(monkeypatch):
    rt, transport = paid_runtime(monkeypatch)
    before = rt.wallet.balance
    result, cost = rt._run_tool("seed-decider", decision(rt), {
        "tool": "connector.fetch", "args": {"id": "source", "path": "/data"}})
    assert result["body"] == "paid fact" and cost == 2734
    assert rt.wallet.balance == before - cost and rt.wallet.check_conservation()
    assert ledger_items(rt, "connector.call")[-1]["cost"] == cost
    assert (ledger_items(rt, "wallet.commit")[-1]["seq"]
            < ledger_items(rt, "connector.call")[-1]["seq"])
    assert any(row["name"] == "connector.paid_fetch" for row in ledger_items(rt, "io.call"))
    assert len(transport.calls) == 2


def test_paid_fetch_above_cap_only_bills_the_flat_read(monkeypatch):
    rt, transport = paid_runtime(monkeypatch, DataTransport(amount=2001))
    result, cost = rt._run_tool("seed-decider", decision(rt), {
        "tool": "connector.fetch", "args": {"id": "source", "path": "/data"}})
    assert "cap" in result["error"] and cost == 1000
    assert len(transport.calls) == 1 and not transport.seller.calls


def test_unknown_data_payment_is_provisional_and_reconcilable(monkeypatch):
    rt, transport = paid_runtime(monkeypatch, DataTransport(paid=TimeoutError()))
    before = rt.wallet.balance
    result, cost = rt._run_tool("seed-decider", decision(rt), {
        "tool": "connector.fetch", "args": {"id": "source", "path": "/data"}})
    assert result["status"] == "uncertain" and cost == 3000
    assert rt.wallet.balance == before - cost and rt.unresolved_x402
    assert len(transport.calls) == 2 and rt.wallet.check_conservation()
    assert ledger_items(rt, "x402.unresolved")[-1]["reserved_micro"] == 2000


def test_paid_fetch_cap_fits_the_remaining_request_before_any_read(monkeypatch):
    rt, transport = paid_runtime(monkeypatch)
    handle = decision(rt)
    responses = iter([
        Return(handle, {}, 1, "ok", tool_calls=({"tool": "connector.fetch",
                "args": {"id": "source", "path": "/data"}},)),
        Return(handle, {"action": "hold"}, 0, "ok"),
    ])
    monkeypatch.setattr(rt, "_invoke_compute", lambda *a: next(responses))
    req = rt._request(handle, "Produce", {}, {"type": "object"}, 10**15, "verdict")
    ret = rt._invoke("seed-decider", replace(req, cost_ceiling=3000), "producer")
    assert ret.cost == 1 and not transport.calls


@pytest.mark.parametrize("cut", [False, True], ids=["complete", "interrupted-after-submit"])
def test_paid_data_replay_never_resubmits_an_authorization(cut):
    transport = DataTransport()
    rail = X402Provider(private_key=TEST_KEY, transport=transport.seller)
    journal, rows = recording_journal()
    arguments = ("https://example.org", "/data", 2000)

    def fetch(origin, path, cap, *, record):
        return rail.fetch_data(origin, path, cap, transport=transport, record=record)

    result = journal.call("connector.paid_fetch", fetch, arguments, {"record": journal.append})
    if cut:
        rows = rows[:next(i for i, row in enumerate(rows) if row["kind"] == "x402.submitted") + 1]
    replay, _ = recording_journal()
    replay.tail = [{**row, "seq": index, "ts": 0} for index, row in enumerate(rows)]

    def refuse(*args, **kwargs):
        pytest.fail("a recorded or uncertain data payment must not execute again")

    if cut:
        with pytest.raises(PaymentOutcomeUnknown):
            replay.call("connector.paid_fetch", refuse, arguments, {"record": replay.append})
    else:
        assert replay.call("connector.paid_fetch", refuse, arguments,
                           {"record": replay.append}) == result
    assert len(transport.calls) == 2 and replay.peek() is None


@pytest.mark.parametrize("cap", [True, 0.01, "-0.01", "0.0000001", "bad", "NaN", "Infinity"])
def test_paid_connector_refuses_inexact_or_malformed_caps(cap):
    from tests.audit.test_r3_k_world_access import parse

    accepted, rejected = parse({"kind": "connector", "id": "source", "description": "Data",
        "origin": "https://example.org", "pay": "x402", "max_call_usd": cap})
    assert not accepted and rejected


@pytest.mark.parametrize("origin,path", [
    ("https://169.254.169.254", "/metadata"), ("https://localhost", "/"),
    ("https://api.hyperliquid.xyz", "/exchange"), ("https://example.org", "//private.local"),
])
def test_paid_connector_uses_the_same_host_and_path_refusals(origin, path):
    from factorylab.runtime.worlds import ConnectorsSpec
    from factorylab.world.connector import ConnectorRefused

    transport = DataTransport()
    proxy = ConnectorProxy(ConnectorsSpec(), transport)
    with pytest.raises(ConnectorRefused):
        proxy.payment_transport(origin, path)
    assert not transport.calls and not transport.seller.calls


def test_data_uses_same_reserve_and_exact_quote():
    transport = DataTransport()
    rail = X402Provider(private_key=TEST_KEY, transport=transport.seller)
    entries = []
    result = rail.fetch_data("https://example.org", "/data", 2000,
                             transport=transport, record=entries.append)
    assert result == {"body": "paid fact", "status": 200, "bytes": 9, "cost_micro": 1734}
    assert len(transport.calls) == 2 and transport.calls[1][2]
    assert any(row["kind"] == "x402.reserve_before" for row in entries)
    assert [r["kind"] for r in entries][-2:] == ["x402.submitted", "x402.result"]


def test_above_cap_never_signs_or_reads_reserve():
    transport = DataTransport(amount=2001)
    rail = X402Provider(private_key=TEST_KEY, transport=transport.seller)
    result = rail.fetch_data("https://example.org", "/data", 2000, transport=transport)
    assert result["cost_micro"] == 0 and "cap" in result["error"]
    assert len(transport.calls) == 1 and transport.seller.calls == []


@pytest.mark.parametrize("paid", [TimeoutError(), ConnectorResponse(302, b"redirect")])
def test_submitted_failure_is_uncertain_without_retry(paid):
    transport = DataTransport(paid=paid)
    rail = X402Provider(private_key=TEST_KEY, transport=transport.seller)
    with pytest.raises(PaymentOutcomeUnknown):
        rail.fetch_data("https://example.org", "/data", 2000, transport=transport)
    assert len(transport.calls) == 2
