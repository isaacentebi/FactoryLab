from decimal import Decimal

import pytest

pytestmark = pytest.mark.network


def test_place_without_key_is_rejected_not_raised(monkeypatch) -> None:
    from factorylab.world.exchange import HyperliquidExchange, Order

    monkeypatch.delenv("HL_PRIVATE_KEY", raising=False)
    ex = HyperliquidExchange(mainnet=False)
    r = ex.place(Order("BTC", True, Decimal("0.001")))
    assert r.status == "rejected" and r.error == "no signing key"
