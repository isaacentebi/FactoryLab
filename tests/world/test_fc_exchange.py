from decimal import Decimal
from types import SimpleNamespace

import pytest

from factorylab.world.exchange import FakeExchange, HyperliquidExchange, Order, OrderKind


def ack(oid=7):
    return {'status': 'ok', 'response': {'data': {'statuses': [
        {'filled': {'oid': oid, 'totalSz': '1', 'avgPx': '100'}}]}}}


def live_transport(*, operation='place', recover=True):
    ex = HyperliquidExchange.__new__(HyperliquidExchange)
    ex._address = 'synthetic-address'
    ex._sz_decimals = {'BTC': 3}
    ex.coins = ('BTC',)
    sent, queries = [], []

    def submit(*args, **kwargs):
        sent.append((args, kwargs))
        raise TimeoutError('accepted before timeout')

    def query(*args):
        queries.append(args)
        if not recover:
            raise TimeoutError('query unavailable')
        return {'status': 'order', 'order': {'status': 'canceled' if operation == 'cancel'
                                           else 'filled',
                'order': {'oid': 7, 'origSz': '1', 'sz': '0', 'limitPx': '100',
                          'coin': 'BTC'}}}

    ex._exchange = SimpleNamespace(market_open=submit, order=submit, market_close=submit,
                                    cancel=submit)
    ex._info = SimpleNamespace(query_order_by_cloid=query, query_order_by_oid=query)
    return ex, sent, queries


@pytest.mark.parametrize('kind', [OrderKind.MARKET, OrderKind.LIMIT])
def test_lost_ack_uses_cloid_lookup_and_duplicate_submission_is_suppressed(kind):
    ex, sent, queries = live_transport()
    order = Order('BTC', True, Decimal(1), kind, Decimal(100) if kind == OrderKind.LIMIT else None,
                  client_id='decision-17')
    first = ex.place(order)
    assert first.status == 'filled' and first.order_id == '7'
    assert ex.place(order) == first
    assert len(sent) == 1 and queries
    assert sent[0][1]['cloid'].to_raw() == ex.client_id('decision-17').to_raw()


def test_lost_close_ack_retains_client_identity():
    ex, sent, queries = live_transport()
    result = ex.close('BTC', Decimal(1), client_id='decision-18')
    assert result.status == 'filled'
    assert ex.close('BTC', Decimal(1), client_id='decision-18') == result
    assert len(sent) == 1 and queries
    assert sent[0][1]['cloid'].to_raw() == ex.client_id('decision-18').to_raw()


def test_lost_cancel_ack_queries_original_order_identity():
    ex, sent, queries = live_transport(operation='cancel')
    result = ex.cancel('7', coin='BTC', client_id='decision-19')
    assert result['status'] == 'cancelled'
    assert ex.cancel('7', coin='BTC', client_id='decision-19') == result
    assert len(sent) == 1 and queries[-1][-1] == 7


@pytest.mark.parametrize('operation', ['place', 'close', 'cancel'])
def test_no_query_evidence_is_uncertain_never_rejected_or_retried(operation):
    ex, sent, queries = live_transport(operation=operation, recover=False)
    def submit():
        if operation == 'place':
            return ex.place(Order('BTC', True, Decimal(1), client_id='decision-20'))
        if operation == 'close':
            return ex.close('BTC', client_id='decision-20')
        return ex.cancel('7', coin='BTC', client_id='decision-20')
    for _ in range(2):
        result = submit()
        assert (result['status'] if isinstance(result, dict) else result.status) == 'uncertain'
    assert len(sent) == 1 and queries


def test_fake_client_id_is_idempotent_and_queries_follow_fills_and_cancels():
    ex = FakeExchange(start_cash_usd=Decimal(1000), start_prices={'BTC': Decimal(100)})
    order = Order('BTC', True, Decimal(1), client_id='decision-1')
    first = ex.place(order)
    assert ex.place(order) == first and len(ex.fills(0)) == 1
    assert ex.lookup('decision-1') == first
    closing = ex.close('BTC', client_id='decision-2')
    assert ex.close('BTC', client_id='decision-2') == closing and len(ex.fills(0)) == 2
    resting = ex.place(Order('BTC', True, Decimal(1), OrderKind.LIMIT, Decimal(90), 'decision-3'))
    result = ex.cancel(resting.order_id, coin='BTC', client_id='decision-4')
    assert result['status'] == 'cancelled'
    assert ex.cancel(resting.order_id, coin='BTC', client_id='decision-4') == result


@pytest.mark.parametrize('oid', [None, True, -1, 0, 'bad', 1.5])
@pytest.mark.parametrize('status', ['filled', 'resting'])
def test_acknowledgement_without_a_valid_order_identity_remains_uncertain(oid, status):
    response = {'status': 'ok', 'response': {'data': {'statuses': [{status: {
        'oid': oid, 'totalSz': '1', 'avgPx': '100'}}]}}}
    result = HyperliquidExchange._parse_order_response(response)
    assert result.status == 'uncertain' and result.order_id is None
