from decimal import Decimal, localcontext

import pytest

from factorylab.kernel.money import money_to_usd, usd_to_micro, usd_to_money


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


@pytest.mark.parametrize("value", [1.0, True, Decimal("1"), "1"])
def test_money_to_usd_rejects_non_integer_money(value):
    with pytest.raises(TypeError):
        money_to_usd(value)


@pytest.mark.parametrize("value", ["0.0000001", "NaN", "Infinity", "-Infinity"])
def test_invariant_1_reject_rounding_or_nonfinite_usd(value):
    with pytest.raises(ValueError):
        usd_to_money(value)



@pytest.mark.parametrize("value,expected", [("100", 100_000_000), ("0.000001", 1), (0, 0)])
def test_exact_conversion_is_the_manifest_contract(value, expected):
    assert usd_to_micro(value, rounding="exact") == expected


@pytest.mark.parametrize("rounding,expected", [
    ("floor", (0, -1)), ("ceil", (1, 0)), ("nearest", (0, 0)),
])
def test_every_rounding_is_named_and_symmetric_about_zero(rounding, expected):
    assert usd_to_micro("0.0000005", rounding=rounding) == expected[0]
    assert usd_to_micro("-0.0000005", rounding=rounding) == expected[1]


@pytest.mark.parametrize("rounding", ["exact", "floor", "ceil", "nearest"])
def test_no_rounding_accepts_a_nonfinite_amount(rounding):
    for value in ("NaN", "Infinity", "-Infinity"):
        with pytest.raises(ValueError):
            usd_to_micro(value, rounding=rounding)


def test_an_unnamed_rounding_is_refused():
    with pytest.raises(ValueError, match="rounding must be"):
        usd_to_micro("1", rounding="half-up")


def test_exact_refuses_a_fraction_of_a_micro():
    with pytest.raises(ValueError):
        usd_to_micro("0.0000001", rounding="exact")
