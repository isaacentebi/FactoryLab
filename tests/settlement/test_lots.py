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


def test_fifo_partial_closes_charge_both_fees_and_funding_to_openers():
    table = LotTable().start("first", 1).finish("first", 100_000)
    table = table.start("second", 2).finish("second", 100_000)
    table = table.start("closer", 3).finish("closer", 100_000)
    original = table
    table = fill(table, "first", "1", size="2", fee="2")
    table = fill(table, "second", "2", px="110", fee="1")
    table = table.funding("BTC", "3")  # first pays 2, second pays 1
    table = fill(table, "closer", "3", px="120", buy=False, fee="1")
    assert table.account("first").realized_micro == 17_000_000
    assert table.account("second").realized_micro == 0
    assert table.resolve(4, 200, {}).account("first").payoff is None
    table = fill(table, "closer", "4", size="2", px="115", buy=False, fee="2")
    table = table.resolve(5, 200, {})
    assert table.account("first").payoff.net_micro == 29_000_000
    assert table.account("second").payoff.net_micro == 2_000_000
    assert table.account("first").payoff.y == table.account("second").payoff.y == 1
    assert table.account("closer").payoff.y == 0
    assert table.lots == () and original.lots == ()
    with pytest.raises(FrozenInstanceError):
        table.account("first").cost_micro = 0


def test_reversal_closes_short_then_opens_only_residual_long_for_new_owner():
    table = LotTable().start("short", 1).finish("short", 0)
    table = table.start("reverse", 2).finish("reverse", 0)
    table = fill(table, "short", "1", buy=False, size="2", fee="2")
    table = fill(table, "reverse", "2", size="3", px="90", fee="3")
    table = table.resolve(3, 20, {"BTC": "90"})
    assert table.account("short").payoff.net_micro == 16_000_000
    assert table.account("short").payoff.y == 1
    assert table.account("reverse").payoff is None
    assert len(table.lots) == 1
    assert (table.lots[0].handle, table.lots[0].size, table.lots[0].charges_micro) == (
        "reverse",
        1,
        1_000_000,
    )


def test_all_coins_and_all_lots_must_close_and_strict_cost_threshold_applies():
    table = LotTable().start("return", 1).finish("return", 1_000_000)
    table = fill(table, "return", "1")
    table = fill(table, "return", "2", coin="ETH")
    table = fill(table, "other", "3", buy=False, px="102")
    assert table.resolve(3, 200, {}).account("return").payoff is None
    table = fill(table, "other", "4", buy=False, px="99", coin="ETH")
    payoff = table.resolve(4, 200, {}).account("return").payoff
    assert payoff.net_micro == payoff.cost_micro == 1_000_000
    assert payoff.y == 0


@pytest.mark.parametrize("cost", [0, 10, 1_000_000])
def test_no_fill_is_zero_immediately_once_cost_known(cost):
    table = LotTable().start("noop", 1)
    assert table.resolve(1, 200, {}).account("noop").payoff is None
    payoff = table.finish("noop", cost).resolve(1, 200, {}).account("noop").payoff
    assert payoff.y == 0 and payoff.at_event == 1 and not payoff.marked


def test_backstop_marks_only_remainder_with_funding_and_keeps_inventory_owned():
    table = LotTable().start("loser", 1).finish("loser", 10)
    table = fill(table, "loser", "1", size="2", fee="2")
    table = fill(table, "other", "2", buy=False, px="110", fee="1")
    table = table.funding("BTC", "2")
    assert table.resolve(10, 10, {"BTC": "80"}).account("loser").payoff is None
    assert table.resolve(11, 10, {}).account("loser").payoff is None
    table = table.resolve(11, 10, {"BTC": "80"})
    payoff = table.account("loser").payoff
    assert payoff.net_micro == -15_000_000 and payoff.y == 0 and payoff.marked
    table = table.start("late", 12).finish("late", 0)
    table = fill(table, "late", "3", buy=False, px="200")
    table = table.resolve(12, 10, {})
    assert table.account("loser").payoff == payoff
    assert table.account("late").payoff.y == 0
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
    assert table.resolve(2, 10, {}).account("limit").payoff is None
    cancelled = table.cancel("1").resolve(2, 10, {})
    assert cancelled.account("limit").payoff.y == 0
    assert table.resolve(11, 10, {}).account("limit").payoff.y == 0
    table = table.fill(order_id="1", coin="BTC", is_buy=True, size="1", px="100", fee_usd="0")
    table = fill(table, "other", "2", px="101", buy=False)
    assert table.resolve(4, 10, {}).account("limit").payoff is None
    assert table.cancel("1").resolve(4, 10, {}).account("limit").payoff.y == 1


def test_unknown_open_inventory_cannot_be_appropriated_by_a_later_closer():
    table = (
        LotTable()
        .fill(
            order_id="external",
            coin="BTC",
            is_buy=True,
            size="1",
            px="100",
            fee_usd="0",
        )
        .start("closer", 1)
        .finish("closer", 0)
    )
    table = fill(table, "closer", "2", buy=False, px="200").resolve(2, 200, {})
    assert table.account("closer").payoff.y == 0
    assert table.lots == ()


def test_wash_trades_are_negative_after_both_fees():
    table = LotTable().start("wash", 1).finish("wash", 1)
    table = fill(table, "wash", "1", fee="0.01")
    table = fill(table, "wash", "2", buy=False, fee="0.01")
    payoff = table.resolve(1, 200, {}).account("wash").payoff
    assert payoff.net_micro == -20_000 and payoff.y == 0


def test_split_fills_preserve_submicro_charges_and_cannot_round_up_a_payoff():
    table = LotTable().start("owner", 1).finish("owner", 0)
    table = fill(table, "owner", "open", size="3", fee="0.000001")
    whole = fill(table, "closer", "close", size="3", px="100.0000005", buy=False)
    split = table
    for i in range(3):
        split = fill(split, "closer", f"close-{i}", px="100.0000005", buy=False)
    assert whole.account("owner").realized_micro == Fraction(1, 2)
    assert split.account("owner").realized_micro == whole.account("owner").realized_micro
    assert split.resolve(2, 200, {}).account("owner").payoff.y == 0
    assert whole.resolve(2, 200, {}).account("owner").payoff.y == 0


def test_splitting_returns_cannot_amplify_fractional_gain_or_erase_fractional_loss():
    table = LotTable()
    for i in range(3):
        table = table.start(str(i), i).finish(str(i), 0)
        table = fill(table, str(i), f"open-{i}", fee="0.0000006")
    table = fill(table, "closer", "close", size="3", px="100.0000005", buy=False)
    table = table.resolve(4, 200, {})
    assert all(r.payoff.net_micro == -1 and r.payoff.y == 0 for r in table.returns)


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
