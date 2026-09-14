"""Read-only SDK preparation failures must not be mistaken for ambiguous venue writes."""

from decimal import Decimal
from types import SimpleNamespace

import pytest
from hyperliquid.exchange import Exchange

from factorylab.world.exchange import HyperliquidExchange, Order


def preparing_exchange():
    calls = []
    info = SimpleNamespace(
        all_mids=lambda: {"BTC": "100"},
        user_state=lambda *_: {"assetPositions": [
            {"position": {"coin": "BTC", "szi": "1"}}]},
        name_to_coin={"BTC": "BTC"}, coin_to_asset={"BTC": 0}, asset_to_sz_decimals={0: 3},
        query_order_by_cloid=lambda *_: {"status": "unknownOid"},
    )
    sdk = Exchange.__new__(Exchange)
    sdk.info, sdk.wallet = info, SimpleNamespace(address="synthetic")
    sdk.account_address = sdk.vault_address = None

    def order(*args, **kwargs):
        calls.append((args, kwargs))
        return {"status": "ok", "response": {"data": {"statuses": [
            {"filled": {"oid": 7, "totalSz": str(args[2]), "avgPx": "100"}}]}}}

    sdk.order = order
    exchange = HyperliquidExchange.__new__(HyperliquidExchange)
    exchange._address, exchange.coins = "synthetic", ("BTC",)
    exchange._sz_decimals = {"BTC": 3}
    exchange._exchange, exchange._info = sdk, info
    return exchange, calls


@pytest.mark.parametrize("operation", ["open", "close"])
def test_quote_timeout_is_a_pre_submission_rejection(operation):
    exchange, calls = preparing_exchange()

    def unavailable():
        raise TimeoutError("private transport details")

    exchange._info.all_mids = unavailable
    result = (exchange.place(Order("BTC", True, Decimal(1), client_id="quote-failed"))
              if operation == "open" else exchange.close("BTC", client_id="quote-failed"))
    assert result.status == "rejected"
    assert calls == []
    assert "private transport details" not in result.error


@pytest.mark.parametrize("mark", ["0", "-1", "NaN", "Infinity", "1e9999", "1e-9999"])
def test_invalid_market_quote_never_reaches_the_write(mark):
    exchange, calls = preparing_exchange()
    exchange._info.all_mids = lambda: {"BTC": mark}
    result = exchange.place(Order("BTC", True, Decimal(1), client_id="invalid-quote"))
    assert result.status == "rejected"
    assert calls == []


def test_empty_position_close_is_a_known_rejection():
    exchange, calls = preparing_exchange()
    exchange._info.user_state = lambda *_: {"assetPositions": []}
    assert exchange.close("BTC", client_id="empty-close").status == "rejected"
    assert calls == []


def test_position_read_timeout_never_creates_an_ambiguous_close():
    exchange, calls = preparing_exchange()

    def unavailable(*_):
        raise TimeoutError("private transport details")

    exchange._info.user_state = unavailable
    result = exchange.close("BTC", client_id="position-failed")
    assert result.status == "rejected" and calls == []
    assert "private transport details" not in result.error


@pytest.mark.parametrize("position", ["NaN", "Infinity", "invalid", "0"])
def test_invalid_or_zero_position_never_reaches_the_close_write(position):
    exchange, calls = preparing_exchange()
    exchange._info.user_state = lambda *_: {"assetPositions": [
        {"position": {"coin": "BTC", "szi": position}}]}
    assert exchange.close("BTC", client_id="bad-position").status == "rejected"
    assert calls == []


def test_a_preparation_failure_allows_a_later_fresh_decision():
    exchange, calls = preparing_exchange()
    exchange._info.all_mids = lambda: {}
    assert exchange.place(Order("BTC", True, Decimal(1), client_id="first")).status == "rejected"
    exchange._info.all_mids = lambda: {"BTC": "100"}
    assert exchange.place(Order("BTC", True, Decimal(1), client_id="second")).status == "filled"
    assert len(calls) == 1
    assert calls[0][0][3] == 105.0  # The SDK's unchanged market-buy preparation.


def test_close_reads_position_once_and_preserves_reduce_only_ioc():
    exchange, calls = preparing_exchange()
    reads = []

    def position(*args):
        reads.append(args)
        return {"assetPositions": [{"position": {"coin": "BTC", "szi": "1"}}]}

    exchange._info.user_state = position
    assert exchange.close("BTC", Decimal("0.5"), client_id="close").status == "filled"
    assert len(reads) == len(calls) == 1
    args, kwargs = calls[0]
    assert args == ("BTC", False, 0.5, 95.0, {"limit": {"tif": "Ioc"}})
    assert kwargs["reduce_only"] is True
    assert kwargs["cloid"].to_raw() == exchange.client_id("close").to_raw()


@pytest.mark.parametrize('is_buy', [True, False])
def test_reduce_only_order_cannot_reverse_its_declared_side_from_cached_position(is_buy):
    exchange, calls = preparing_exchange()
    cached = Decimal(-1 if is_buy else 1)
    fresh = str(-cached)
    exchange.account = lambda: SimpleNamespace(
        positions=(SimpleNamespace(coin='BTC', size=cached),))
    exchange._info.user_state = lambda *_: {'assetPositions': [
        {'position': {'coin': 'BTC', 'szi': fresh}}]}
    result = exchange.place(Order('BTC', is_buy, Decimal(1), reduce_only=True,
                                  client_id='side-must-not-flip'))
    assert result.status == 'rejected' and calls == []


def test_spot_balance_read_failure_is_a_known_preparation_rejection():
    exchange, calls = preparing_exchange()
    exchange.spot_pairs = ('BTC/USDC',)
    exchange._spot_tokens = {'BTC/USDC': 'BTC'}

    def unavailable(*_):
        raise TimeoutError('private transport details')

    exchange.account = unavailable
    exchange._info.spot_user_state = unavailable
    result = exchange.close('BTC/USDC', client_id='spot-close', market='spot')
    assert result.status == 'rejected' and calls == []
    assert 'private transport details' not in result.error


@pytest.mark.parametrize('order_id', ['not-an-id', '0', '-1', True])
def test_invalid_cancel_identity_never_enters_dispatch(order_id):
    exchange, calls = preparing_exchange()

    def cancel(*args):
        calls.append(args)
        return {'status': 'ok', 'response': {'data': {'statuses': ['success']}}}

    exchange._exchange.cancel = cancel
    exchange._info.query_order_by_oid = lambda *_: {'status': 'unknownOid'}
    assert exchange.cancel(order_id, coin='BTC', client_id='bad-cancel')['status'] == 'rejected'
    assert calls == []


def test_unrepresentable_order_size_is_rejected_without_raising():
    exchange, calls = preparing_exchange()
    result = exchange.place(Order('BTC', True, Decimal('1e9999'), client_id='bad-size'))
    assert result.status == 'rejected' and calls == []


@pytest.mark.parametrize('price', ['1e9999', '1e-9999', '1.123456789'])
def test_unrepresentable_limit_price_never_enters_dispatch(price):
    from factorylab.world.exchange import OrderKind

    exchange, calls = preparing_exchange()
    result = exchange.place(Order('BTC', True, Decimal(1), OrderKind.LIMIT,
                                  Decimal(price), client_id='bad-limit'))
    assert result.status == 'rejected' and calls == []
