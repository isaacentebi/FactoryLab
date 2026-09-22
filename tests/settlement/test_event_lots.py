"""Event-market lots: held long, never marked, closed only by their market's resolution."""

from fractions import Fraction

import pytest

from factorylab.settlement.lots import LotTable

COIN = "PM:100000000000000000000"


def holding(size="10", px="0.40", fee="0"):
    table = LotTable().start("h", 0, 0).order("o1", "h", size)
    table = table.fill(order_id="o1", coin=COIN, is_buy=True, size=size, px=px, fee_usd=fee,
                       market="event")
    return table.finish("h", 1_000)


def test_an_event_fill_opens_a_long_lot_and_cannot_sell_what_is_not_held():
    table = holding()
    [lot] = table.lots
    assert (lot.market, lot.size, lot.px) == ("event", Fraction(10), Fraction(2, 5))
    table = table.start("s", 1, 1).order("o2", "s", "11")
    with pytest.raises(ValueError, match="long inventory"):
        table.fill(order_id="o2", coin=COIN, is_buy=False, size="11", px="0.5", fee_usd="0",
                   market="event")
    with pytest.raises(ValueError, match="cannot be liquidated"):
        table.fill(order_id="o2", coin=COIN, is_buy=False, size="1", px="0.5", fee_usd="0",
                   market="event", liquidation=True)


def test_an_event_lot_is_never_marked_whatever_the_backstop_or_the_mid_says():
    table = holding().resolve(10_000, 1, {COIN: "0.99"}, tick=10_000)
    assert table.account("h").payoff is None


def test_a_held_return_stays_open_even_with_nothing_filled():
    table = LotTable().start("h", 0, 0).order("o1", "h", "10").finish("h", 0)
    assert table.resolve(100, 1, {}, tick=100, held=("h",)).account("h").payoff is None
    assert table.resolve(100, 1, {}, tick=100).account("h").payoff is not None


@pytest.mark.parametrize(("payout", "net"), [("1", 6_000_000 - 20_000),
                                             ("0", -4_000_000 - 20_000),
                                             ("0.5", 1_000_000 - 20_000)])
def test_resolution_redeems_every_lot_at_its_payout_net_of_the_opening_fee(payout, net):
    table, credited = holding(fee="0.02").redeem(COIN, payout)
    assert table.lots == () and credited == {"h": Fraction(net)}
    payoff = table.resolve(1, 5, {}, tick=1).account("h").payoff
    assert (payoff.net_micro, payoff.marked) == (net, False)
    assert payoff.y == int(net > 1_000)


def test_redeem_touches_only_event_lots_of_its_own_token_and_refuses_impossible_payouts():
    table = holding()
    assert table.redeem("PM:other", "1") == (table, {})
    with pytest.raises(ValueError):
        table.redeem(COIN, "1.5")
