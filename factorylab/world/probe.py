"""Read-only probe against a live venue. Used by ``factorylab probe``."""

from __future__ import annotations

from typing import Any

from factorylab.world.exchange import HyperliquidExchange, VenueUnavailable, live_exchange
from factorylab.world.universe import explicit_markets


def probe_hyperliquid(spec: Any) -> dict:
    """Live mids and funding for a manifest's venue, read as the runtime reads them.

    Network required; never places an order. Guarantees the adapter is built by the
    runtime's own dex-aware path (``live_exchange``: the explicit markets and every
    HIP-3 dex the manifest names), that every named dex answered both the mids and the
    funding read, else ``VenueUnavailable`` naming the dexes that did not, and a report
    of the explicit markets' mids and funding and, per dex, its live markets and how
    many of them the two reads priced.
    """
    ex = live_exchange(spec, HyperliquidExchange)
    mids = ex.mids()
    funding = ex.funding()
    answered = ex.dex_answers()
    missing = sorted(dex for dex in ex.dexes
                     if dex not in answered["mids"] or dex not in answered["rates"])
    if missing:
        raise VenueUnavailable(f"HIP-3 dex reads did not answer: {missing}")
    explicit = explicit_markets(spec.coins) or ("BTC", "ETH")
    rated = {f.coin for f in funding}
    dexes = {}
    for dex in ex.dexes:
        listed = [row["coin"] for row in ex.instruments()["perp"]
                  if row.get("dex") == dex and not row.get("delisted")]
        dexes[dex] = {"markets": len(listed), "mids": sum(c in mids for c in listed),
                      "rates": sum(c in rated for c in listed)}
    shown = [c for c in dict.fromkeys((*explicit, *(c for c in mids if ":" in c)))
             if c in mids]
    return {
        "venue": ex.name,
        "mids": {c: str(mids[c]) for c in shown},
        "funding": [
            {"coin": f.coin, "rate": str(f.rate), "premium": str(f.premium), "ts_ns": f.ts_ns}
            for f in funding if f.coin in explicit
        ],
        "dexes": dexes,
    }
