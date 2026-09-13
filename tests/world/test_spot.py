from decimal import Decimal as D
from types import SimpleNamespace

import pytest

from factorylab.kernel.ledger import Ledger
from factorylab.kernel.wallet import Wallet
from factorylab.world.exchange import (
    FakeExchange,
    HyperliquidExchange,
    Order,
    OrderKind,
    VenueUnavailable,
)
from factorylab.world.treasury import FakeTreasury, UnconfiguredRail
from factorylab.world.venue_tools import VenueTools


def fake():
    return FakeExchange(coins=('BTC',), spot_pairs=('BTC/USDC',),
                        start_cash_usd=D(1000), start_prices={'BTC': D(100)},
                        spread_bps=D(0), fee_bps=D(0), step_bps=D(0),
                        shocks={1: {'BTC': D('.5')}})


def test_spot_requires_cash_cannot_short_and_is_not_perp_collateral():
    ex = fake()
    assert ex.place(Order('BTC/USDC', True, D(1), market='spot')).status == 'rejected'
    ex.class_transfer(D(900), False)
    assert ex.account().equity_usd == 1000
    assert ex.place(Order('BTC', True, D(4))).status == 'rejected'
    assert ex.place(Order('BTC/USDC', True, D(2), market='spot')).status == 'filled'
    assert ex.place(Order('BTC/USDC', False, D(3), market='spot')).status == 'rejected'
    events = ex.advance(1)
    assert ex.mids()['BTC/USDC'] == ex.mids()['BTC'] == 50
    assert ex.account().equity_usd == 900
    assert not any(e.payload.get('liquidation') for e in events)
    assert ex.close('BTC/USDC', market='spot', client_id='close').status == 'filled'
    assert ex.close('BTC/USDC', market='spot', client_id='close').status == 'filled'
    assert ex.fills(0)[-1].realized == -100
    assert ex._cash == 100


def test_spot_limit_cross_and_tool_defaults():
    ex = fake()
    ex.class_transfer(D(200), False)
    tools = VenueTools(ex, coins=ex.coins, spot_pairs=ex.spot_pairs)
    args = {'coin': 'BTC/USDC', 'market': 'spot', 'side': 'buy', 'size': '1', 'price': '50'}
    assert tools.call('venue.place_limit', args)['status'] == 'resting'
    ex.advance(1)
    assert ex.fills(0)[0].market == 'spot'
    assert tools.call('venue.set_leverage', {'coin': 'BTC/USDC', 'market': 'spot',
                                           'leverage': 2})['error'] == (
        'spot does not support leverage')
    assert tools.call('venue.place_market', {'coin': 'BTC', 'side': 'buy',
                                           'size': '1'})['status'] == 'filled'
    assert ex.account().positions[0].coin == 'BTC'


def live():
    ex = HyperliquidExchange.__new__(HyperliquidExchange)
    ex.coins, ex.spot_pairs = ('BTC',), ('BTC/USDC',)
    ex._sz_decimals, ex._spot_names, ex._spot_tokens = {'BTC': 5}, {}, {}
    ex._configure_spot({'tokens': [{'index': 0, 'name': 'USDC', 'szDecimals': 8},
                                  {'index': 1, 'name': 'BTC', 'szDecimals': 5}],
                        'universe': [{'index': 7, 'name': '@7', 'tokens': [1, 0]}]})
    ex._address = 'synthetic'
    ex._last_account = ex._last_mids = None
    ex._guarded = lambda name, call: call()
    calls = []
    ack = {'status': 'ok', 'response': {'data': {'statuses': [
        {'filled': {'oid': 8, 'totalSz': '1', 'avgPx': '100'}}]}}}
    def send(*args, **kwargs):
        calls.append((args, kwargs))
        return ack
    ex._exchange = SimpleNamespace(market_open=send, order=send)
    ex._info = SimpleNamespace(
        all_mids=lambda: {'BTC': '100', '@7': '101'},
        user_state=lambda _: {'marginSummary': {'accountValue': '50', 'totalMarginUsed': '0'},
                              'withdrawable': '40'},
        spot_user_state=lambda _: {'balances': [
            {'coin': 'USDC', 'total': '20', 'hold': '3'},
            {'coin': 'BTC', 'total': '2', 'hold': '0'}]},
        user_fills_by_time=lambda *_: [{'oid': 8, 'coin': '@7', 'side': 'B', 'sz': '1',
                                       'px': '100', 'fee': '.01', 'feeToken': 'BTC',
                                       'closedPnl': '0', 'time': 1}])
    return ex, calls


@pytest.mark.parametrize('kind', [OrderKind.MARKET, OrderKind.LIMIT])
def test_sdk_spot_mapping_account_fill_and_idempotency(kind):
    ex, calls = live()
    assert ex.account().equity_usd == 272
    assert ex.account().spot_balances[0].available == 17
    order = Order('BTC/USDC', True, D(1), kind, D(100), client_id='a', market='spot')
    assert ex.place(order).status == 'filled'
    ex.place(order)
    assert len(calls) == 1 and calls[0][0][0] == '@7'
    fill = ex.fills(0)[0]
    assert (fill.coin, fill.market, fill.inventory_size, fill.size, fill.fee) == (
        'BTC/USDC', 'spot', D('.99'), D(1), D(1))
    assert ex.instruments()['spot'][0]['lot_size'] == '0.00001'


def test_fake_class_transfers_are_receipted_and_conserve():
    ledger = Ledger(clock_ns=lambda: 0)
    wallet = Wallet(1000_000_000, ledger, clock_ns=lambda: 0)
    ex = fake()
    treasury = FakeTreasury(ledger, wallet, exchange=ex)
    assert treasury.transfer('perps_to_spot', '200', handle='a', now_ns=1)['status'] == 'submitted'
    assert ex._spot_cash == 0 and wallet.available == 800_000_000
    assert treasury.tick(2)[0]['status'] == 'confirmed'
    assert ex._spot_cash == 200 and treasury.pots()['spot'] == 200_000_000
    assert treasury.tick(3) == []
    assert treasury.transfer('spot_to_perps', '201', handle='b', now_ns=4)['status'] == 'refused'
    treasury.transfer('spot_to_perps', '100', handle='c', now_ns=5)
    treasury.tick(6)
    assert ex._spot_cash == 100 and wallet.balance == wallet.available == 1000_000_000
    assert wallet.check_conservation()


def test_class_receipt_requires_direction_amount_hash_and_a_bounded_execution_time():
    """The venue stamps its own execution time, so the row is matched in a bounded
    window after the signed nonce, never at it, and never when two rows could match."""
    from factorylab.world.treasury import CLASS_EXECUTION_TOLERANCE_MS

    ex, _ = live()
    ex.name = 'fake-live'
    rail = UnconfiguredRail(ex)
    state = {'amount_micro': 10_000_000, 'nonce': 123}
    state['reference'] = rail.class_prepare('perps_to_spot', state)
    row = {'time': 1958, 'hash': '0xabc', 'delta': {
        'type': 'accountClassTransfer', 'usdc': '10', 'toPerp': False}}
    rows = [row]
    ex._info.user_non_funding_ledger_updates = lambda *_: rows
    assert rail.class_poll(state)['evidence'] is row
    row['time'] = 123 + CLASS_EXECUTION_TOLERANCE_MS
    assert rail.class_poll(state)['confirmed']
    for wrong in ({'time': 122}, {'time': 124 + CLASS_EXECUTION_TOLERANCE_MS}, {'hash': ''},
                  {'delta': {**row['delta'], 'toPerp': True}},
                  {'delta': {**row['delta'], 'usdc': '9'}}):
        rows[:] = [{**row, **wrong}]
        assert rail.class_poll(state) is None
    rows[:] = [row, {**row, 'hash': '0xdef'}]  # two candidates confirm nothing
    assert rail.class_poll(state) is None


def test_resting_spot_orders_hold_cash_and_inventory_against_transfer_and_oversell():
    ex = fake()
    ex.class_transfer(D(100), False)
    buy = Order('BTC/USDC', True, D(2), OrderKind.LIMIT, D(50), market='spot')
    assert ex.place(buy).status == 'resting'
    assert ex.account().spot_balances[0].available == 0
    with pytest.raises(ValueError, match='cash'):
        ex.class_transfer(D(1), True)
    assert ex.place(Order('BTC/USDC', True, D(1), market='spot')).status == 'rejected'
    ex.advance(1)
    sell = Order('BTC/USDC', False, D(2), OrderKind.LIMIT, D(100), market='spot')
    assert ex.place(sell).status == 'resting'
    assert ex.place(Order('BTC/USDC', False, D(1), market='spot')).status == 'rejected'
    assert ex.account().spot_balances[1].available == 0


def test_class_retry_signs_same_action_and_nonce_after_lost_ack():
    from copy import deepcopy

    from eth_account import Account
    from hyperliquid.utils.constants import TESTNET_API_URL

    ex, _ = live()
    wallet = Account.from_key(bytes([1]) * 32)
    ex._address, ex.name, ex.base_url = wallet.address, 'fake-testnet', TESTNET_API_URL
    sent = []
    def post(action, signature, nonce):
        sent.append((deepcopy(action), signature, nonce))
        raise TimeoutError('synthetic lost acknowledgement')
    ex._exchange = SimpleNamespace(wallet=wallet, vault_address=None, _post_action=post)
    rail = UnconfiguredRail(ex)
    state = {'amount_micro': 10_000_001, 'nonce': 123}
    ref = rail.class_prepare('perps_to_spot', state)
    for _ in range(2):
        with pytest.raises(TimeoutError):
            rail.class_send(ref)
    assert sent[0] == sent[1]
    assert sent[0][0]['amount'] == '10.000001'
    assert sent[0][2] == ref['nonce'] == 123
    assert 'signatureChainId' not in ref['action']


def test_pots_mark_spot_losses_without_moving_them_into_perps():
    ledger = Ledger(clock_ns=lambda: 0)
    wallet = Wallet(1000_000_000, ledger, clock_ns=lambda: 0)
    ex = fake()
    treasury = FakeTreasury(ledger, wallet, exchange=ex)
    treasury.transfer('perps_to_spot', '200', handle='a', now_ns=1)
    treasury.tick(2)
    ex.place(Order('BTC/USDC', True, D(2), market='spot'))
    ex.advance(1)
    pots = treasury.pots()
    assert pots['perps'] == 800_000_000
    assert pots['spot'] == 100_000_000
    assert pots['venue'] == pots['total_micro'] == 900_000_000
    ex.sync_cash(treasury.venue_balance_usd)
    assert ex.account().equity_usd == 900


def test_reserve_withdrawal_cannot_spend_spot_class_cash():
    ledger = Ledger(clock_ns=lambda: 0)
    wallet = Wallet(1000_000_000, ledger, clock_ns=lambda: 0)
    ex = fake()
    treasury = FakeTreasury(ledger, wallet, exchange=ex)
    treasury.transfer('perps_to_spot', '990', handle='a', now_ns=1)
    treasury.tick(2)
    assert treasury.transfer('to_reserve', '20', handle='b', now_ns=3)['status'] == 'refused'


def test_spot_outage_returns_the_last_complete_account():
    ex, _ = live()
    first = ex.account()

    def outage(_):
        raise VenueUnavailable('spot_user_state: ConnectionError')

    ex._info.spot_user_state = outage
    assert ex.account() == first and ex.account_fallbacks == 1
    assert ex.account().spot_balances == first.spot_balances
