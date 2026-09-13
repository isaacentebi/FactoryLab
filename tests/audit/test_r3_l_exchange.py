"""Rejected anonymous orders cannot poison later client identities."""

from decimal import Decimal

from factorylab.world.exchange import FakeExchange, Order


def test_rejected_auto_client_id_does_not_poison_next_order():
    exchange = FakeExchange()
    assert exchange.place(Order("MISSING", True, Decimal("0.001"))).status == "rejected"
    assert exchange.place(Order("BTC", True, Decimal("0.001"))).status == "filled"


def test_explicit_rejected_client_id_remains_idempotent():
    exchange = FakeExchange()
    rejected = exchange.place(Order("MISSING", True, Decimal("0.001"), client_id="retry"))
    assert exchange.place(Order("BTC", True, Decimal("0.001"), client_id="retry")) == rejected
