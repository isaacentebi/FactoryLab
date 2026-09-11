from decimal import Decimal, localcontext

import pytest

from factorylab.kernel.money import money_to_usd, per_token_price, usd_to_money


@pytest.mark.parametrize(
    "usd,micros",
    [("0", 0), ("1.000001", 1_000_001), ("-12.5", -12_500_000), (Decimal("0.000001"), 1)],
)
def test_exact_roundtrip(usd, micros):
    assert usd_to_money(usd) == micros
    assert money_to_usd(micros) == Decimal(usd)


def test_conversion_is_independent_of_decimal_context():
    micros = 10**80 + 1
    with localcontext() as context:
        context.prec = 2
        assert usd_to_money(money_to_usd(micros)) == micros


@pytest.mark.parametrize("value", [1.0, True, 1, None])
def test_invariant_1_reject_float_or_implicit_usd(value):
    with pytest.raises(TypeError):
        usd_to_money(value)
    with pytest.raises(TypeError):
        per_token_price(value)


@pytest.mark.parametrize("value", [1.0, True, Decimal("1"), "1"])
def test_money_to_usd_rejects_non_integer_money(value):
    with pytest.raises(TypeError):
        money_to_usd(value)


@pytest.mark.parametrize("value", ["0.0000001", "NaN", "Infinity", "-Infinity"])
def test_invariant_1_reject_rounding_or_nonfinite_usd(value):
    with pytest.raises(ValueError):
        usd_to_money(value)


def test_vendor_per_million_token_prices():
    assert per_token_price("5") == 5
    assert per_token_price(Decimal("25")) == 25
    for value in ("0.5", "-1", "Infinity"):
        with pytest.raises(ValueError):
            per_token_price(value)
