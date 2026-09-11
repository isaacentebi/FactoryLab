"""Read-only probe against a live venue. Used by ``factorylab probe``."""

from __future__ import annotations

from factorylab.world.exchange import HyperliquidExchange


def probe_hyperliquid(*, mainnet: bool = False, coins: tuple[str, ...] = ("BTC", "ETH")) -> dict:
    """Return live mids and funding for ``coins``. Network required. Never places orders."""
    ex = HyperliquidExchange(mainnet=mainnet, coins=coins)
    return {
        "venue": ex.name,
        "mids": {c: str(m) for c, m in ex.mids().items()},
        "funding": [
            {"coin": f.coin, "rate": str(f.rate), "premium": str(f.premium), "ts_ns": f.ts_ns}
            for f in ex.funding()
        ],
    }
