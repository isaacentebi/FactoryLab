"""Diagnostic selection must not confuse the live adapter's wider discovery catalogue."""

from decimal import Decimal
from types import SimpleNamespace

import pytest

from factorylab.world.probe import probe_hyperliquid


@pytest.mark.parametrize("coins", [("BTC", "ETH"), ("ETH",), ()])
def test_probe_returns_only_requested_market_data(monkeypatch, coins):
    rows = [SimpleNamespace(coin=coin, rate=Decimal("0.001"), premium=Decimal(0), ts_ns=1)
            for coin in ("BTC", "ETH", "SOL")]

    def exchange(**kwargs):
        assert kwargs == {"mainnet": False, "coins": coins}
        return SimpleNamespace(name="hyperliquid-testnet",
                               mids=lambda: {coin: Decimal(1) for coin in ("BTC", "ETH", "SOL")},
                               funding=lambda: rows)

    monkeypatch.setattr("factorylab.world.probe.HyperliquidExchange", exchange)
    result = probe_hyperliquid(coins=coins)
    assert set(result["mids"]) == set(coins)
    assert {row["coin"] for row in result["funding"]} == set(coins)
