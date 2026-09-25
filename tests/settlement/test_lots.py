import math
from dataclasses import FrozenInstanceError
from fractions import Fraction

import pytest

from factorylab.settlement.lots import LotTable


def fill(table, owner, oid, *, size="1", px="100", buy=True, fee="0", coin="BTC"):
    table = table.order(oid, owner, size)
    return table.fill(
        order_id=oid,
        coin=coin,
        is_buy=buy,
        size=size,
        px=px,
        fee_usd=fee,
    )


def test_fifo_partial_closes_split_each_lots_pnl_once_between_opener_and_closer():
    """Edition 2 (cold audit F7): a lot's P&L is credited once, split by notional contributed.

    Before, the opener and a distinct closer each received the whole P&L; the
    paid-off rate could improve while the pair spent more than it made.
    """
    table = LotTable().start("first", 1).finish("first", 100_000)
    table = table.start("second", 2).finish("second", 100_000)
    table = table.start("closer", 3).finish("closer", 100_000)
    original = table
    table = fill(table, "first", "1", size="2", fee="2")
    table = fill(table, "second", "2", px="110", fee="1")
    table = table.funding("BTC", "3")  # first pays 2, second pays 1
    table = fill(table, "closer", "3", px="120", buy=False, fee="1")
    # P&L 20 on the closed unit, split 100:120 by entry and exit notional. First pays
    # half its fee and funding (2); the closer its fee (1). The two credits sum to 20.
    opener_1, closer_1 = Fraction(20) * 100 / 220, Fraction(20) * 120 / 220
    assert opener_1 + closer_1 == 20
    assert table.account("first").realized_micro == (opener_1 - 2) * 1_000_000
    assert table.account("closer").realized_micro == (closer_1 - 1) * 1_000_000
    assert table.account("second").realized_micro == 0
    assert table.resolve(4, 200, {}).account("first").payoff is None
    table = fill(table, "closer", "4", size="2", px="115", buy=False, fee="2")
    table = table.resolve(5, 200, {})
    # First's last unit: P&L 15 split 100:115; second's unit: P&L 5 split 110:115.
    opener_2, closer_2 = Fraction(15) * 100 / 215, Fraction(15) * 115 / 215
    opener_3, closer_3 = Fraction(5) * 110 / 225, Fraction(5) * 115 / 225
    first_net = opener_1 - 2 + opener_2 - 2
    second_net = opener_3 - 2
    closer_net = closer_1 - 1 + closer_2 - 1 + closer_3 - 1
    assert table.account("first").payoff.net_micro == int(first_net * 1_000_000)
    assert table.account("second").payoff.net_micro == int(second_net * 1_000_000)
    assert table.account("closer").payoff.net_micro == int(closer_net * 1_000_000)
    assert table.account("first").payoff.y == table.account("second").payoff.y == 1
    assert table.account("closer").payoff.y == 1 and table.account("closer").closes == 3
    # Conservation: every credit together is exactly the realised P&L less every fee and funding.
    credited = first_net + second_net + closer_net
    assert credited == 20 + 15 + 5 - (2 + 1 + 3) - (1 + 2)
    assert table.lots == () and original.lots == ()
    with pytest.raises(FrozenInstanceError):
        table.account("first").cost_micro = 0


def test_reversal_closes_short_then_opens_only_residual_long_for_new_owner():
    table = LotTable().start("short", 1).finish("short", 0)
    table = table.start("reverse", 2).finish("reverse", 0)
    table = fill(table, "short", "1", buy=False, size="2", fee="2")
    table = fill(table, "reverse", "2", size="3", px="90", fee="3")
    table = table.resolve(3, 20, {"BTC": "90"})
    # P&L 20 on the two closed units, split 100:90 by notional (F7); the short keeps its
    # entry share less its own fee, the reverser's exit share stays with its open account.
    short_share = Fraction(20) * 100 / 190
    assert table.account("short").payoff.net_micro == int((short_share - 2) * 1_000_000)
    assert table.account("short").payoff.y == 1
    assert table.account("reverse").realized_micro == (Fraction(20) * 90 / 190 - 2) * 1_000_000
    assert table.account("reverse").payoff is None
    assert len(table.lots) == 1
    assert (table.lots[0].handle, table.lots[0].size, table.lots[0].charges_micro) == (
        "reverse",
        1,
        1_000_000,
    )


def test_all_coins_and_all_lots_must_close_and_strict_cost_threshold_applies():
    # The opener's share of +2 on BTC (100:102) and of -1 on ETH (100:99), floored once, is
    # its cost to the micro: paying off needs strictly more than that.
    net = int((Fraction(2) * 100 / 202 - Fraction(1) * 100 / 199) * 1_000_000)
    table = LotTable().start("return", 1).finish("return", net)
    table = table.start("other", 1).finish("other", 0)
    table = fill(table, "return", "1")
    table = fill(table, "return", "2", coin="ETH")
    table = fill(table, "other", "3", buy=False, px="102")
    assert table.resolve(3, 200, {}).account("return").payoff is None
    table = fill(table, "other", "4", buy=False, px="99", coin="ETH")
    payoff = table.resolve(4, 200, {}).account("return").payoff
    assert payoff.net_micro == payoff.cost_micro == net == 487_586
    assert payoff.y == 0


@pytest.mark.parametrize("cost", [0, 10, 1_000_000])
def test_no_fill_is_zero_immediately_once_cost_known(cost):
    table = LotTable().start("noop", 1)
    assert table.resolve(1, 200, {}).account("noop").payoff is None
    payoff = table.finish("noop", cost).resolve(1, 200, {}).account("noop").payoff
    assert payoff.y == 0 and payoff.at_event == 1 and not payoff.marked


def test_backstop_marks_only_remainder_with_funding_and_keeps_inventory_owned():
    table = LotTable().start("loser", 1).finish("loser", 10)
    table = table.start("other", 1).finish("other", 0)
    table = fill(table, "loser", "1", size="2", fee="2")
    table = fill(table, "other", "2", buy=False, px="110", fee="1")
    table = table.funding("BTC", "2")
    assert table.resolve(10, 10, {"BTC": "80"}).account("loser").payoff is None
    assert table.resolve(11, 10, {}).account("loser").payoff is None
    table = table.resolve(11, 10, {"BTC": "80"})
    payoff = table.account("loser").payoff
    # Its 100:110 share of the +10 close less its fee on that unit, then the marked unit:
    # -20 less its fee (1) and the funding (2). Floored once.
    expected = (Fraction(10) * 100 / 210 - 1) + (-20 - 1 - 2)
    assert payoff.net_micro == math.floor(expected * 1_000_000) == -19_238_096
    assert payoff.y == 0 and payoff.marked
    table = table.start("late", 12).finish("late", 0)
    table = fill(table, "late", "3", buy=False, px="200")
    table = table.resolve(12, 10, {})
    assert table.account("loser").payoff == payoff  # a mark is fixed; the late close is not
    assert table.account("late").payoff.y == 1  # the closer is credited what it realised
    assert table.lots == ()


@pytest.mark.parametrize("buy,close_px", [(True, "50"), (False, "150")])
def test_liquidation_closes_and_prices_loss_without_opening_a_liquidator_lot(buy, close_px):
    table = LotTable().start("owner", 1).finish("owner", 500)
    table = fill(table, "owner", "1", buy=buy, fee="1")
    table = table.fill(
        order_id="liquidation",
        coin="BTC",
        is_buy=not buy,
        size="1",
        px=close_px,
        fee_usd="2",
        liquidation=True,
    ).resolve(2, 200, {})
    payoff = table.account("owner").payoff
    assert payoff.net_micro == -53_000_000
    assert payoff.y == 0 and payoff.liquidated and not payoff.marked
    assert table.lots == ()


def test_funding_receipts_reduce_cost_and_never_flow_to_closed_lots():
    table = LotTable().start("first", 1).finish("first", 0)
    table = table.start("second", 2).finish("second", 0)
    table = table.start("closer", 2).finish("closer", 0)
    table = fill(table, "first", "1", buy=False)
    table = fill(table, "second", "2", buy=False)
    table = table.funding("BTC", "-2")
    table = fill(table, "closer", "3")
    table = table.funding("BTC", "-3")
    table = fill(table, "closer", "4").resolve(4, 200, {})
    assert table.account("first").payoff.net_micro == 1_000_000
    assert table.account("second").payoff.net_micro == 4_000_000


def test_accepted_unfilled_and_partially_filled_orders_wait_until_cancel_or_backstop():
    table = LotTable().start("limit", 1).finish("limit", 1).order("1", "limit", "2")
    table = table.start("other", 1).finish("other", 0)
    assert table.resolve(2, 10, {}).account("limit").payoff is None
    cancelled = table.cancel("1").resolve(2, 10, {})
    assert cancelled.account("limit").payoff.y == 0
    assert table.resolve(11, 10, {}).account("limit").payoff.y == 0
    table = table.fill(order_id="1", coin="BTC", is_buy=True, size="1", px="100", fee_usd="0")
    table = fill(table, "other", "2", px="101", buy=False)
    assert table.resolve(4, 10, {}).account("limit").payoff is None
    assert table.cancel("1").resolve(4, 10, {}).account("limit").payoff.y == 1


def test_fill_without_an_open_account_never_enters_the_shared_fifo():
    with pytest.raises(ValueError, match="open consequence account"):
        LotTable().fill(order_id="external", coin="BTC", is_buy=True, size="1", px="100",
                        fee_usd="0")
    with pytest.raises(ValueError, match="open consequence account"):
        LotTable().order("1", "nobody", "1")
    table = LotTable().start("closer", 1).finish("closer", 0)
    # With nothing owned to close, the "closer" merely opens its own short.
    table = fill(table, "closer", "2", buy=False, px="200").resolve(2, 200, {})
    assert table.account("closer").payoff is None
    assert [lot.handle for lot in table.lots] == ["closer"]


def test_wash_trades_are_negative_after_both_fees():
    table = LotTable().start("wash", 1).finish("wash", 1)
    table = fill(table, "wash", "1", fee="0.01")
    table = fill(table, "wash", "2", buy=False, fee="0.01")
    payoff = table.resolve(1, 200, {}).account("wash").payoff
    assert payoff.net_micro == -20_000 and payoff.y == 0


def test_split_fills_preserve_submicro_charges_and_cannot_round_up_a_payoff():
    table = LotTable().start("owner", 1).finish("owner", 0).start("closer", 1).finish("closer", 0)
    table = fill(table, "owner", "open", size="3", fee="0.000001")
    whole = fill(table, "closer", "close", size="3", px="100.0000005", buy=False)
    split = table
    for i in range(3):
        split = fill(split, "closer", f"close-{i}", px="100.0000005", buy=False)
    # 1.5 micro of P&L split 100:100.0000005 (F7), less the owner's 1 micro opening fee.
    exit_px = Fraction("100.0000005")
    owner_share = Fraction(3, 2) * 100 / (100 + exit_px)
    assert whole.account("owner").realized_micro == owner_share - 1
    assert split.account("owner").realized_micro == whole.account("owner").realized_micro
    assert split.resolve(2, 200, {}).account("owner").payoff.y == 0
    assert whole.resolve(2, 200, {}).account("owner").payoff.y == 0


def test_splitting_returns_cannot_amplify_fractional_gain_or_erase_fractional_loss():
    table = LotTable().start("closer", 0).finish("closer", 0)
    for i in range(3):
        table = table.start(str(i), i).finish(str(i), 0)
        table = fill(table, str(i), f"open-{i}", fee="0.0000006")
    table = fill(table, "closer", "close", size="3", px="100.0000005", buy=False)
    table = table.resolve(4, 200, {})
    openers = [r for r in table.returns if r.handle != "closer"]
    assert all(r.payoff.net_micro == -1 and r.payoff.y == 0 for r in openers)
    closer = table.account("closer").payoff
    # The closer's exit share of 1.5 micro is 0.75, floored once to 0: with a cost of 0 the
    # strict threshold is not cleared, and no split of the fill could round it up.
    assert closer.net_micro == 0 and closer.y == 0


@pytest.mark.parametrize("value", [1.0, True, "NaN", "Infinity", "0", "-1"])
def test_invalid_fill_numbers_fail_without_mutating_the_input(value):
    table = LotTable()
    with pytest.raises((ValueError, OverflowError)):
        table.fill(order_id="1", coin="BTC", is_buy=True, size=value, px="100", fee_usd="0")
    assert table == LotTable()


def test_order_owner_and_return_cost_cannot_be_replaced():
    table = LotTable().start("owner", 1).finish("owner", 10).order("1", "owner", "1")
    with pytest.raises(ValueError):
        table.order("1", "thief", "1")
    with pytest.raises(ValueError):
        table.finish("owner", 0)
    with pytest.raises(ValueError):
        table.start("owner", 2)
    with pytest.raises(TypeError):
        LotTable().start("other", 1).finish("other", 1.0)


# --- GPT-6 second reading: P2-06 (late realisation) and P2-05 (service income) -----


def test_a_marked_outcome_books_what_its_lots_realise_later_as_a_late_amount():
    """A position marked at the backstop and closed later: the score is fixed at the
    mark, the money is booked when it is realised, once, relative to what was
    booked before."""
    table = LotTable().start("opener", 1).finish("opener", 100_000)
    table = fill(table, "opener", "1", size="1", px="100")
    table = table.resolve(21, 20, {"BTC": "100"})
    payoff = table.account("opener").payoff
    assert payoff.marked and payoff.net_micro == 0 and payoff.y == 0
    table, late = table.late_realizations()
    assert late == {}  # nothing realised yet
    # liquidated at 98: the opener keeps the whole loss
    table = table.fill(order_id="liq", coin="BTC", is_buy=False, size="1", px="98",
                       fee_usd="0", liquidation=True)
    table, late = table.late_realizations()
    assert late == {"opener": -2_000_000}
    assert table.account("opener").late_micro == -2_000_000
    assert table.account("opener").payoff == payoff  # the score never moves
    table, late = table.late_realizations()
    assert late == {}  # booked once
    # an open account realises nothing late: its outcome is not fixed yet
    table = table.start("later", 2).finish("later", 1)
    table = fill(table, "later", "2", size="1", px="100")
    table = table.fill(order_id="liq2", coin="BTC", is_buy=False, size="1", px="103",
                       fee_usd="0", liquidation=True)
    assert table.late_realizations()[1] == {}
    assert table.account("later").realized_micro == 3_000_000


def test_a_service_receipt_is_the_registering_returns_consequence_while_it_is_open():
    table = LotTable().start("registrar", 1).finish("registrar", 1_000)
    with pytest.raises(ValueError):
        table.bind_service("svc", "nobody")
    table = table.bind_service("svc", "registrar")
    assert table.service_return("svc") == "registrar" and table.service_return("x") is None
    assert table.income("unbound", 5) == table
    table = table.income("svc", 600).income("svc", 600)
    account = table.account("registrar")
    assert (account.earned_micro, account.earnings, account.realized_micro) == (1_200, 2, 0)
    table = table.resolve(21, 20, {})
    payoff = table.account("registrar").payoff
    # 1 200 earned against 1 000 of cost: paid off, without a trade, unmarked
    assert (payoff.y, payoff.net_micro, payoff.earned_micro, payoff.marked) == (1, 0, 1_200, 0)
    # a receipt after the outcome is fixed changes nothing here: the money is the
    # seller's at receipt, the score is published
    assert table.income("svc", 9_000) == table
    # a later version of the service rebinds it to the return that registered it
    table = table.start("registrar-2", 2).finish("registrar-2", 5_000)
    table = table.bind_service("svc", "registrar-2")
    assert table.services == (("svc", "registrar-2"),)
    table = table.income("svc", 100).resolve(42, 20, {})
    assert table.account("registrar-2").payoff.y == 0  # earned, but below its cost


def test_a_return_that_neither_traded_nor_earned_does_not_pay_off():
    table = LotTable().start("idle", 1).finish("idle", 0).resolve(21, 20, {})
    payoff = table.account("idle").payoff
    assert payoff.y == 0 and payoff.earned_micro == 0 and not payoff.marked


# --- wave 16, D2 and D7: the horizon on the venue's clock, the liquidation mark ------

TAKER = {"perp": "0.00045", "spot": "0.0007"}


def _open_long(px="100", fee="0.045", *, cost=0):
    """A return that bought one BTC at ``px`` paying ``fee``, opened at venue ns 1000."""
    table = LotTable().start("opener", 1, ns=1_000).finish("opener", cost)
    return fill(table, "opener", "1", size="1", px=px, fee=fee)


def test_an_open_lot_at_an_unchanged_mid_carries_the_round_trip_and_does_not_pay_off():
    """D7: marked at its liquidation value, the lot pays its exit fee at the venue's
    taker rate beside the opening fee it paid, so an unchanged mid is a loss of the
    whole round trip, exactly the fee a declined trade is charged."""
    table = _open_long().resolve(2, 20, {"BTC": "100"}, now_ns=1_000 + 60, horizon_ns=60,
                                 exit_rates=TAKER)
    payoff = table.account("opener").payoff
    assert payoff.marked and payoff.y == 0
    assert payoff.exit_fee_micro == 45_000 and payoff.net_micro == -90_000
    # Without the exit fee the same mark would read half the round trip.
    bare = _open_long().resolve(2, 20, {"BTC": "100"}, now_ns=1_060, horizon_ns=60)
    assert bare.account("opener").payoff.net_micro == -45_000
    assert bare.account("opener").payoff.exit_fee_micro == 0


def test_the_mark_waits_for_the_horizon_on_the_venue_clock_not_for_events_or_ticks():
    table = _open_long()
    assert table.resolve(10_000, 20, {"BTC": "100"}, tick=10_000, now_ns=1_059,
                         horizon_ns=60, exit_rates=TAKER).account("opener").payoff is None
    assert table.resolve(2, 20, {"BTC": "100"}, tick=2, now_ns=1_060, horizon_ns=60,
                         exit_rates=TAKER).account("opener").payoff is not None


def test_an_unread_exit_rate_fixes_the_outcome_uninformative_never_pending():
    """Ruling R10-i: the mid is fixed at the horizon whatever the fee read; with no
    rate read the outcome is censored fee_unknown, never left waiting."""
    from factorylab.settlement.lots import FEE_UNKNOWN

    table = _open_long().resolve(2, 20, {"BTC": "100"}, now_ns=1_060, horizon_ns=60,
                                 exit_rates={"perp": None, "spot": "0.0007"})
    payoff = table.account("opener").payoff
    assert payoff is not None and payoff.censored == FEE_UNKNOWN and payoff.y == 0
    # A market the rates do not list (an event token) carries no exit fee.
    table = _open_long().resolve(2, 20, {"BTC": "100"}, now_ns=1_060, horizon_ns=60,
                                 exit_rates={"spot": "0.0007"})
    assert table.account("opener").payoff.exit_fee_micro == 0


def test_the_exit_rate_is_the_one_read_at_or_before_the_horizon():
    """Ruling R10-i: asked at the return's opening plus H, a later read never applies,
    and a horizon before any read is fee_unknown."""
    from factorylab.settlement.lots import FEE_UNKNOWN

    reads = [(900, "0.00045"), (1_100, "0.0009")]  # a read after the horizon (1_060)

    def rate_at(market, at_ns):
        before = [rate for ns, rate in reads if ns <= at_ns]
        return before[-1] if before else None

    asked = []
    table = _open_long().resolve(
        2, 20, {"BTC": "100"}, now_ns=1_200, horizon_ns=60,
        exit_rates=lambda market, at: asked.append((market, at)) or rate_at(market, at))
    assert asked == [("perp", 1_060)]
    assert table.account("opener").payoff.exit_fee_micro == 45_000  # 0.00045, not 0.0009
    reads[:] = [(1_100, "0.0009")]
    table = _open_long().resolve(2, 20, {"BTC": "100"}, now_ns=1_200, horizon_ns=60,
                                 exit_rates=rate_at)
    assert table.account("opener").payoff.censored == FEE_UNKNOWN


def test_the_exit_fee_is_never_booked_as_money_and_the_real_close_is_booked_once():
    """The mark is not money: a marked outcome books nothing when it is fixed, and the
    real closing fee the venue charged is booked once, late, with the realised P&L."""
    table = _open_long().resolve(2, 20, {"BTC": "100"}, now_ns=1_060, horizon_ns=60,
                                 exit_rates=TAKER)
    assert table.account("opener").late_micro == 0
    table, late = table.late_realizations()
    assert late == {}
    table = fill(table, "opener", "2", size="1", px="100", buy=False, fee="0.045")
    table, late = table.late_realizations()
    assert late == {"opener": -90_000}  # both real fees, once; the estimate never
    table, late = table.late_realizations()
    assert late == {}
    assert table.account("opener").payoff.net_micro == -90_000  # the mark was the truth
