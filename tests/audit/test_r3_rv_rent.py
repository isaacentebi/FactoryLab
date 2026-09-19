"""T58: retained storage is a scored liability, not an unattributed wallet debit.

A note's recurring rent leaves the wallet against its writer's handle. Unless
that charge also reaches the writer's consequence cost while its outcome is
open, a trading return can resolve `return_paid_off = 1` on a margin its own
storage already consumed.
"""


import pytest

from factorylab.settlement.lots import LotTable


def resolve(rt):
    rt.consequences.resolve(rt.consequences.backstop + 1)
    return rt.consequences.payoff


def test_a_carried_liability_is_money_and_cannot_reopen_a_fixed_outcome():
    table = LotTable().start("r", 1).finish("r", 5)
    assert table.carry("r", 3).account("r").carried_micro == 3
    assert table.account("r").carried_micro == 0  # the input table never changes
    with pytest.raises(ValueError, match="nonnegative"):
        table.carry("r", -1)
    with pytest.raises(TypeError, match="integer micro-USD"):
        table.carry("r", 1.0)
    with pytest.raises(KeyError):
        table.carry("absent", 1)
    fixed = table.resolve(2, 10, {})
    assert fixed.account("r").payoff is not None
    with pytest.raises(ValueError, match="already final"):
        fixed.carry("r", 1)
