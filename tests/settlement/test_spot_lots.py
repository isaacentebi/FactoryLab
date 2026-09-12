from fractions import Fraction

import pytest

from factorylab.settlement.lots import LotTable


def test_spot_fifo_closer_credit_and_perp_separation():
    table = LotTable()
    for h in ('a', 'b', 'c', 'p'):
        table = table.start(h, 0).finish(h, 1).order(h, h, '1')
    for h, price in [('a', '100'), ('b', '200')]:
        table = table.fill(order_id=h, coin='BTC/USDC', is_buy=True, size='1', px=price,
                           fee_usd='1', market='spot')
    table = table.fill(order_id='p', coin='BTC', is_buy=False, size='1', px='100', fee_usd='0')
    table = table.fill(order_id='c', coin='BTC/USDC', is_buy=False, size='1', px='150',
                       fee_usd='1', market='spot')
    assert table.account('a').realized_micro == table.account('c').realized_micro == 49_000_000
    assert [(lot.handle, lot.size) for lot in table.lots] == [
        ('b', Fraction(1)), ('p', Fraction(1))]
    result = table.resolve(1, 100, {})
    assert result.account('a').payoff.y == result.account('c').payoff.y == 1
    with pytest.raises(ValueError, match='inventory'):
        table.fill(order_id='c', coin='BTC/USDC', is_buy=False, size='2', px='150', fee_usd='0')
    with pytest.raises(ValueError, match='liquidated'):
        table.fill(order_id='c', coin='BTC/USDC', is_buy=False, size='1', px='150',
                   fee_usd='0', liquidation=True)


def test_base_fee_inventory_consumes_gross_order_liability():
    table = LotTable().start('a', 0).order('a', 'a', '1')
    table = table.fill(order_id='a', coin='BTC/USDC', is_buy=True, size='.99',
                       order_size='1', px='100', fee_usd='1', market='spot')
    assert table.orders[0].remaining == 0
    assert table.lots[0].size == Fraction(99, 100)
