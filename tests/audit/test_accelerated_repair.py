"""Accelerated-run failures retain evidence without inventing execution or proposals."""

import json
from decimal import Decimal
from types import SimpleNamespace

from factorylab.world.exchange import HyperliquidExchange
from tests.audit.test_r3_b_authority import _producing_decision
from tests.conftest import make_runtime


def test_unknown_submission_retains_safe_diagnostics_and_never_resubmits():
    exchange = HyperliquidExchange.__new__(HyperliquidExchange)
    exchange._address = "synthetic"
    exchange._info = SimpleNamespace(query_order_by_cloid=lambda *args: {"status": "unknownOid"})
    calls = []

    def submit():
        calls.append(1)
        raise TimeoutError("secret signing material must never reach the journal")

    result = exchange._submit("identity", submit)
    assert result.status == "uncertain"
    assert "submit exception: TimeoutError" in result.error
    assert "order not observed" in result.error
    assert "secret" not in result.error
    assert exchange._submit("identity", submit).status == "uncertain"
    assert calls == [1]
    exchange._info.query_order_by_cloid = lambda *args: {"status": "order", "order": {
        "status": "filled", "order": {"oid": 123, "origSz": "0.01", "sz": "0"}}}
    result = exchange._submit("identity", submit)
    assert result.status == "filled" and result.filled_size == Decimal("0.01")
    assert calls == [1]


def test_uncertain_order_blocks_only_its_identity_and_retains_first_diagnostic(monkeypatch):
    rt = make_runtime()
    h = _producing_decision(rt)
    exchange = rt.exchange.target
    calls = []

    def place(order):
        calls.append(order)
        raise TimeoutError("secret")

    monkeypatch.setattr(exchange, "place", place)
    result = rt._venue_write(h, "venue.place_market", {
        "coin": "BTC", "side": "buy", "size": "0.001"}, slot="output")
    assert result["status"] == "uncertain"
    entries = [i for i in rt.ledger._recovery_items() if i["kind"] == "order.uncertain"]
    assert entries[0]["result"]["error"] == "write exception: TimeoutError"
    assert "secret" not in json.dumps(entries)
    # Repeating the uncertain identity reconciles and never resubmits.
    again = rt._venue_write(h, "venue.place_market", {
        "coin": "BTC", "side": "buy", "size": "0.001"}, slot="output")
    assert again["status"] == "uncertain" and len(calls) == 1
    # Another identity on the same coin is not shut out (fix/venue-money #1).
    h2 = _producing_decision(rt)
    rt._venue_write(h2, "venue.place_market", {
        "coin": "BTC", "side": "buy", "size": "0.001"}, slot="output")
    assert len(calls) == 2
