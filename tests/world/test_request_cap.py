from dataclasses import replace

import pytest

from factorylab.cortex.registration import ModelProposal
from factorylab.runtime.worlds import load_manifest, manifest_from_dict
from factorylab.world.market import X402Provider
from factorylab.world.models import TokenPrice
from factorylab.world.x402 import BASE_NETWORK, BASE_USDC, X402Error, authorization_typed_data
from tests.conftest import make_runtime
from tests.world.test_market import MODEL, SellerHTTP


@pytest.mark.parametrize('ceiling', [500001, 10**30])
def test_seller_cannot_register_above_default_request_cap(ceiling):
    provider = X402Provider()
    with pytest.raises(X402Error, match='max_request_micro'):
        provider.register(MODEL, ceiling)
    assert not provider._ceilings


def test_manifest_cap_is_integer_money_with_half_dollar_default():
    assert load_manifest('scripted').treasury.max_request_micro == 500000
    import tomllib
    from pathlib import Path

    source = tomllib.loads(Path('worlds/scripted.toml').read_text())
    source.setdefault('treasury', {})['max_request_micro'] = 250000
    assert manifest_from_dict(source).treasury.max_request_micro == 250000
    for invalid in (True, .5, '500000', -1):
        source['treasury']['max_request_micro'] = invalid
        with pytest.raises(ValueError):
            manifest_from_dict(source)


def test_quote_and_catalogue_bounds_are_checked_before_registration_or_payment():
    fake = SellerHTTP(amount=500001)
    provider = X402Provider(transport=fake)
    with pytest.raises(X402Error, match='max_request_micro'):
        provider.registration_price(MODEL)
    assert not provider._ceilings and not fake.payments
    fake.quote['accepts'][0]['amount'] = '500000'
    price, _ = provider.registration_price(MODEL)
    provider.register(MODEL, price.per_request_micro)
    assert provider._ceilings[MODEL] == 500000


def test_runtime_rechecks_injected_seller_before_registry_mutation(monkeypatch):
    rt = make_runtime()
    rt._manage_reserve_window()
    rt.m = replace(rt.m, treasury=replace(rt.m.treasury, max_request_micro=100))
    monkeypatch.setattr(rt.market.target, 'registration_price',
                        lambda _: (TokenPrice(0, 0, 101), {}))
    before = rt.registry.state()
    with pytest.raises(ValueError, match='max_request_micro'):
        rt._register('caller', ModelProposal(MODEL))
    assert rt.registry.state() == before
    assert MODEL not in rt.prices.prices and MODEL not in rt.sellers


@pytest.mark.parametrize('timeout,expected', [(1, 1), (60, 60), (600, 600), (601, 600),
                                             (86400, 600), (2**256 - 1, 600)])
def test_authorization_expiry_is_within_quote_and_ten_minutes(timeout, expected):
    accepted = {'scheme': 'exact', 'network': BASE_NETWORK, 'asset': BASE_USDC,
                'amount': '1', 'payTo': '0x' + '22' * 20, 'maxTimeoutSeconds': timeout}
    typed = authorization_typed_data(accepted, '0x' + '33' * 20, now=1000, nonce=bytes(32))
    assert typed['message']['validBefore'] == 1000 + expected
