"""Fake x402 data authorizations exercise the existing reserve rail without live payment."""

import json

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
