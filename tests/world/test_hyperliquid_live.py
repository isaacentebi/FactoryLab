from decimal import Decimal

import pytest

pytestmark = pytest.mark.network


def test_testnet_probe_reads_mids_and_funding() -> None:
    from factorylab.world.probe import probe_hyperliquid

    out = probe_hyperliquid(mainnet=False)
    assert out["venue"] == "hyperliquid-testnet"
    assert Decimal(out["mids"]["BTC"]) > 0 and Decimal(out["mids"]["ETH"]) > 0
    assert {f["coin"] for f in out["funding"]} == {"BTC", "ETH"}


def test_place_without_key_is_rejected_not_raised(monkeypatch) -> None:
    from factorylab.world.exchange import HyperliquidExchange, Order

    monkeypatch.delenv("HL_PRIVATE_KEY", raising=False)
    ex = HyperliquidExchange(mainnet=False)
    r = ex.place(Order("BTC", True, Decimal("0.001")))
    assert r.status == "rejected" and r.error == "no signing key"
