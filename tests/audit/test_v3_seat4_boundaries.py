"""Seat four: deterministic intended-behavior assertions for reproduced boundaries."""


import pytest

from factorylab.settlement.lots import LotTable


@pytest.mark.parametrize("market,coin", [("perp", "BTC"), ("spot", "BTC/USDC")])
def test_one_decision_round_trip_counts_its_profit_once(market, coin):
    """A decision with 0.60 USD trading profit and 1 USD compute does not pay off."""
    table = LotTable().start("round-trip", 0)
    table = table.order("opening", "round-trip", "1").fill(
        order_id="opening",
        coin=coin,
        is_buy=True,
        size="1",
        px="100",
        fee_usd="0",
        market=market,
    )
    table = table.order("closing", "round-trip", "1").fill(
        order_id="closing",
        coin=coin,
        is_buy=False,
        size="1",
        px="100.60",
        fee_usd="0",
        market=market,
    )
    payoff = table.finish("round-trip", 1_000_000).resolve(1, 200, {}).account("round-trip").payoff
    assert (payoff.net_micro, payoff.y) == (600_000, 0)
