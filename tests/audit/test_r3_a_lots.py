"""A handle's economic result counts each own close once, with exact carrying costs."""

from fractions import Fraction

import pytest

from factorylab.settlement.lots import LotTable


def fill(table, handle, oid, size, price, fee, *, buy=True, coin="BTC"):
    return table.order(oid, handle, size).fill(
        order_id=oid, coin=coin, is_buy=buy, size=size, px=price, fee_usd=fee,
    )


@pytest.mark.parametrize("coin", ["BTC", "BTC/USDC"])
def test_own_partial_closes_keep_both_fees_and_carrying_charges(coin):
    table = LotTable().start("a", 0)
    table = fill(table, "a", "open", "2", "100", "2", coin=coin)
    if coin == "BTC":
        table = table.funding(coin, "4")
    table = fill(table, "a", "partial", "0.5", "110", "0.5", buy=False, coin=coin)
    charges = 6 if coin == "BTC" else 2
    assert table.account("a").realized_micro == Fraction(5 * 4 - charges - 2, 4) * 1_000_000
    assert table.lots[0].charges_micro == Fraction(charges * 3, 4) * 1_000_000
    table = fill(table, "a", "rest", "1.5", "90", "1.5", buy=False, coin=coin)
    payoff = table.finish("a", 1).resolve(1, 200, {}).account("a").payoff
    assert payoff.net_micro == (-10 - charges - 2) * 1_000_000
    assert payoff.y == 0
    assert not table.lots


@pytest.mark.parametrize("coin", ["BTC", "BTC/USDC"])
def test_mixed_ownership_deduplicates_only_the_closers_own_lot(coin):
    table = LotTable().start("a", 0).start("b", 0)
    table = fill(table, "a", "a-open", "1", "100", "1", coin=coin)
    table = fill(table, "b", "b-open", "1", "110", "2", coin=coin)
    table = fill(table, "b", "close", "2", "120", "4", buy=False, coin=coin)
    table = table.finish("a", 0).finish("b", 0).resolve(1, 200, {})
    assert table.account("a").payoff.net_micro == 19_000_000
    # b's own 10 plus its closer credit on a's 20, less b's opening 2 and closing 4.
    assert table.account("b").payoff.net_micro == 24_000_000


@pytest.mark.parametrize("buy,reverse,close", [(True, "110", "100"), (False, "90", "100")])
def test_own_reversal_prices_only_the_residual_and_allocates_fees(buy, reverse, close):
    table = LotTable().start("a", 0)
    table = fill(table, "a", "open", "1", "100", "1", buy=buy)
    table = table.funding("BTC", "2")
    table = fill(table, "a", "reverse", "3", reverse, "3", buy=not buy)
    assert table.account("a").realized_micro == 6_000_000
    assert len(table.lots) == 1
    assert table.lots[0].size == 2
    assert table.lots[0].charges_micro == 2_000_000
    table = fill(table, "a", "close", "2", close, "2", buy=buy)
    payoff = table.finish("a", 22_000_000).resolve(1, 200, {}).account("a").payoff
    assert (payoff.net_micro, payoff.y) == (22_000_000, 0)


def test_spot_reversal_is_refused_without_mutation():
    table = LotTable().start("a", 0)
    table = fill(table, "a", "open", "1", "100", "1", coin="BTC/USDC")
    with pytest.raises(ValueError, match="spot sell exceeds long inventory"):
        fill(table, "a", "reverse", "2", "110", "2", buy=False, coin="BTC/USDC")
    assert table.account("a").realized_micro == 0
    assert table.lots[0].size == 1
