"""Round three, group F2, triage T48 and T49: the venue's own metadata, published.

T48: a fill is classified spot or perp by the venue's spot universe, not by the subset
of pairs this world's manifest happens to trade. Booking a spot fill as a perp settles
inventory the world does not hold against the wallet.

T49: the venue's minimum order value is published with lot and tick size, so a size is
known to be illegal before the population pays to discover it.

Offline: the venue is a recorded metadata and fill response, no socket is opened.
"""

from decimal import Decimal as D
from types import SimpleNamespace

import pytest

from factorylab.world.exchange import (
    MIN_ORDER_VALUE_USD,
    FakeExchange,
    HyperliquidExchange,
    Order,
)

# One configured pair and one the manifest never named, exactly as testnet presents them.
SPOT_META = {
    "tokens": [
        {"index": 0, "name": "USDC", "szDecimals": 8},
        {"index": 1, "name": "BTC", "szDecimals": 5},
        {"index": 2, "name": "PURR", "szDecimals": 0},
    ],
    "universe": [
        {"index": 7, "name": "@7", "tokens": [1, 0]},
        {"index": 0, "name": "PURR/USDC", "tokens": [2, 0]},
    ],
}


def venue(fills):
    ex = HyperliquidExchange.__new__(HyperliquidExchange)
    ex.coins, ex.spot_pairs = ("BTC",), ("BTC/USDC",)
    ex._sz_decimals, ex._spot_names, ex._spot_tokens = {"BTC": 5}, {}, {}
    ex._configure_spot(SPOT_META)
    ex._address = "synthetic"
    ex._guarded = lambda name, call: call()
    ex._info = SimpleNamespace(user_fills_by_time=lambda *_: fills)
    return ex


def test_t48_a_spot_fill_on_an_unconfigured_pair_is_not_booked_as_a_perp():
    """A PURR/USDC fill is spot on the venue whatever this world chose to trade;
    booked as a perp it settles against the wallet as a position the world never had."""
    ex = venue(
        [
            {
                "oid": 9,
                "coin": "PURR/USDC",
                "side": "B",
                "sz": "3",
                "px": "4.6252",
                "fee": "0.0045",
                "feeToken": "PURR",
                "closedPnl": "0",
                "time": 1,
            }
        ]
    )
    fill = ex.fills(0)[0]
    assert fill.market == "spot", "an unconfigured spot pair was booked as a perp"
    assert fill.coin == "PURR/USDC"
    # The spot fee is taken in the base token, so inventory is net of it and the
    # USD fee is the token fee marked at the fill price.
    assert (fill.size, fill.inventory_size, fill.fee) == (D(3), D("2.9955"), D("0.02081340"))


def test_t48_a_configured_pair_and_a_perp_still_classify_as_before():
    ex = venue(
        [
            {
                "oid": 8,
                "coin": "@7",
                "side": "B",
                "sz": "1",
                "px": "100",
                "fee": "0.01",
                "feeToken": "USDC",
                "closedPnl": "0",
                "time": 1,
            },
            {
                "oid": 7,
                "coin": "BTC",
                "side": "A",
                "sz": "0.001",
                "px": "77039",
                "fee": "0.034667",
                "closedPnl": "0",
                "time": 2,
            },
        ]
    )
    first, second = ex.fills(0)
    assert (first.coin, first.market) == ("BTC/USDC", "spot")
    assert (second.coin, second.market) == ("BTC", "perp")


def test_t49_the_venue_minimum_order_value_is_published_with_lot_and_tick_size():
    """A size 2 PURR order is $9.25 and the venue refuses it; the population is told."""
    published = venue([]).instruments()
    assert MIN_ORDER_VALUE_USD == "10"
    for market in ("perp", "spot"):
        assert published[market]
        for row in published[market]:
            assert row["min_order_value_usd"] == MIN_ORDER_VALUE_USD
            assert {"lot_size", "tick_size"} <= row.keys()


def test_t49_the_fake_venue_publishes_and_enforces_the_floor_it_is_given():
    """A rehearsal world can carry the live floor and see the same refusal offline."""
    free = FakeExchange(coins=("BTC",), start_prices={"BTC": D(100)}, spread_bps=D(0))
    assert free.instruments()["perp"][0]["min_order_value_usd"] == "0"
    assert free.place(Order("BTC", True, D("0.01"))).status == "filled"

    floored = FakeExchange(
        coins=("BTC",), start_prices={"BTC": D(100)}, spread_bps=D(0), min_order_value_usd=D(10)
    )
    assert floored.instruments()["perp"][0]["min_order_value_usd"] == "10"
    rejected = floored.place(Order("BTC", True, D("0.05"), client_id="small"))
    assert (rejected.status, rejected.error) == ("rejected", "order below the venue minimum value")
    assert floored.place(Order("BTC", True, D("0.2"), client_id="big")).status == "filled"


@pytest.mark.network
def test_t48_the_live_venue_classifies_a_pair_no_manifest_configured():
    """Testnet, read-only: a perp-only world still knows PURR/USDC is spot."""
    ex = HyperliquidExchange(mainnet=False, coins=("BTC",))
    assert ex.spot_pairs == ()
    assert ex._is_spot("PURR/USDC") and ex._public_coin("PURR/USDC") == "PURR/USDC"
    assert not ex._is_spot("BTC")
