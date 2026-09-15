import math
from decimal import Decimal

import pytest

from factorylab.runtime.resume import ResumeError, decode, encode
from tests.conftest import make_runtime


@pytest.mark.parametrize('value', [0., -0., 5e-324, 1.7976931348623157e308, -1e308,
                                   Decimal('1e999999'), Decimal('-0'), 10**400])
def test_all_finite_numbers_roundtrip_exactly(value):
    result = decode(encode(value))
    assert type(result) is type(value) and result == value
    if type(value) is float:
        assert math.copysign(1, result) == math.copysign(1, value)


@pytest.mark.parametrize('value', [float('nan'), float('inf'), -float('inf'), Decimal('NaN'),
                                   Decimal('Infinity')])
def test_nonfinite_snapshot_is_refused_without_crashing(value):
    rt = make_runtime()
    rt.spot_inventory['bad'] = [{'value': value}]
    rt._snapshot('test')
    item = rt.ledger._recovery_items()[-1]
    assert item['kind'] == 'snapshot.refused'
    assert item['boundary'] == 'test'
    with pytest.raises(ValueError):
        encode(value)
    # The invalid value is never inserted into the snapshot or refusal evidence.
    assert rt.ledger.verify()


@pytest.mark.parametrize('encoded', [{'$float': 'NaN'}, {'$decimal': 'Infinity'}, float('inf')])
def test_decode_also_refuses_nonfinite_numbers(encoded):
    with pytest.raises(ResumeError):
        decode(encoded)


def test_snapshot_json_roundtrip_preserves_large_finite_integer_and_fraction():
    import json
    from fractions import Fraction

    value = {'integer': 10**5000, 'fraction': Fraction(10**5000, 3)}
    saved = json.dumps(encode(value), allow_nan=False)
    assert decode(json.loads(saved)) == value
