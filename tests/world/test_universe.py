"""The market universe: selectors resolved once against the venue's listing, and pinned.

The venue is the world (AGENTS.md); a manifest may name every perp, every USDC spot
pair and named HIP-3 dexes by selector. Each invariant below is attempted and refused.
"""

import tomllib

import pytest

from factorylab.runtime.worlds import WORLDS_DIR, manifest_from_dict
from factorylab.world import universe

LISTING = {
    "perp": [{"coin": "BTC"}, {"coin": "ETH"}, {"coin": "OLD", "delisted": True},
             {"coin": "xyz:TSLA", "dex": "xyz"}, {"coin": "xyz:GOLD", "dex": "xyz"},
             {"coin": "xyz:GONE", "dex": "xyz", "delisted": True},
             {"coin": "flx:OIL", "dex": "flx"}],
    "spot": [{"coin": "PURR/USDC"}, {"coin": "HYPE/USDC"}],
}


def test_selectors_select_live_markets_in_listing_order_after_explicit_names():
    coins, pairs = universe.resolve(["xyz:TSLA", "*", "xyz:*"], ["*/USDC"], LISTING)
    # Explicit first as written, then each selector's live markets; nothing twice.
    assert coins == ("xyz:TSLA", "BTC", "ETH", "xyz:GOLD")
    assert pairs == ("PURR/USDC", "HYPE/USDC")


def test_a_delisted_market_and_an_unnamed_dex_are_never_selected():
    coins, _ = universe.resolve(["*", "xyz:*"], [], LISTING)
    assert "OLD" not in coins and "xyz:GONE" not in coins
    assert "flx:OIL" not in coins  # flx was not named


@pytest.mark.parametrize("coins,pairs", [(["nope:*"], []), ([], ["*/USDC"])])
def test_a_selector_that_selects_nothing_refuses_the_launch(coins, pairs):
    listing = {"perp": LISTING["perp"], "spot": []}
    with pytest.raises(ValueError, match="lists no live market"):
        universe.resolve(coins, pairs, listing)


def test_named_dexes_come_from_selectors_and_from_markets_on_a_dex():
    assert universe.named_dexes(["BTC", "xyz:TSLA", "flx:*", "xyz:*"]) == ("xyz", "flx")
    assert universe.explicit_markets(["*", "BTC", "xyz:*", "xyz:TSLA"]) == ("BTC", "xyz:TSLA")


@pytest.mark.parametrize("coins,pairs,message", [
    (["**"], [], "is not \\* or <dex>:\\*"),
    (["BTC*"], [], "is not \\* or <dex>:\\*"),
    (["xyz:TS:LA"], [], "does not name a perp dex"),
    (["a b:*"], [], "does not name a perp dex"),
    (["*/USDC"], [], "belongs in venue.spot_pairs"),
    ([], ["*/USDT"], "BASE/USDC"),
    ([], ["**/USDC"], "is not \\*/USDC"),
])
def test_a_malformed_selector_is_refused_at_load(coins, pairs, message):
    data = tomllib.loads((WORLDS_DIR / "scripted.toml").read_text())
    data["exchange"]["coins"] = coins
    data.setdefault("venue", {})["spot_pairs"] = pairs
    with pytest.raises(ValueError, match=message):
        manifest_from_dict(data)


def test_a_manifest_with_selectors_loads_and_keeps_them_as_written():
    data = tomllib.loads((WORLDS_DIR / "scripted.toml").read_text())
    data["exchange"]["coins"] = ["*", "xyz:*", "BTC"]
    # A named dex doubles the per-dex reads: 40 a slot covers the heaviest.
    data.setdefault("venue", {}).update(spot_pairs=["*/USDC"],
                                        public_read_weight_per_minute=640)
    m = manifest_from_dict(data)
    assert m.exchange.coins == ("*", "xyz:*", "BTC")
    assert m.exchange.spot_pairs == ("*/USDC",)
