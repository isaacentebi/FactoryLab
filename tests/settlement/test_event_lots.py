"""Event-market lots: held long, marked at the market's midpoint, redeemed at resolution."""

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


def test_an_event_lot_is_marked_at_the_backstop_like_a_spot_lot():
    table = holding()
    # Before the backstop it waits; without a mid it waits (and its judge falls back).
    assert table.resolve(5, 10, {COIN: "0.55"}, tick=5).account("h").payoff is None
    assert table.resolve(50, 10, {}, tick=50).account("h").payoff is None
    payoff = table.resolve(50, 10, {COIN: "0.55"}, tick=50).account("h").payoff
    assert (payoff.net_micro, payoff.marked, payoff.y) == (1_500_000, True, 1)


def test_a_resolution_after_the_mark_is_booked_late_and_never_rescored():
    table = holding().resolve(50, 10, {COIN: "0.55"}, tick=50)
    marked = table.account("h").payoff
    table, _ = table.redeem(COIN, "0")
    table, late = table.late_realizations()
    assert late == {"h": -4_000_000}
    assert table.account("h").payoff == marked
    assert table.late_realizations()[1] == {}


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
