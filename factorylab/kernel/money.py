"""Exact integer micro-USD at every accounting boundary."""

from decimal import Decimal

Money = int
MICRO_USD_PER_USD = 1_000_000


def require_money(value: Money, *, nonnegative: bool = False) -> Money:
    """Return an integer amount, rejecting booleans, floats and forbidden negatives."""
    if type(value) is not int:
        raise TypeError("money must be integer micro-USD")
    if nonnegative and value < 0:
        raise ValueError("amount must be nonnegative")
    return value


def _decimal(value: Decimal | str) -> Decimal:
    if not isinstance(value, (Decimal, str)):
        raise TypeError("use Decimal or str, never float")
    result = Decimal(value)
    if not result.is_finite():
        raise ValueError("amount must be finite")
    return result


def usd_to_money(value: Decimal | str) -> Money:
    """Preserve USD exactly; reject amounts smaller than an integral micro-USD."""
    numerator, denominator = _decimal(value).as_integer_ratio()
    micros, remainder = divmod(numerator * MICRO_USD_PER_USD, denominator)
    if remainder:
        raise ValueError("amount is not an integral micro-USD")
    return micros


def money_to_usd(value: Money) -> Decimal:
    """Return exact USD, independently of the caller's Decimal precision."""
    require_money(value)
    sign = 1 if value < 0 else 0
    digits = tuple(int(digit) for digit in str(abs(value)))
    return Decimal((sign, digits, -6))


def per_token_price(usd_per_mtok: Decimal | str) -> Money:
    """Return exact micro-USD/token from USD/MTok; reject fractional micro-prices."""
    price = _decimal(usd_per_mtok)
    numerator, denominator = price.as_integer_ratio()
    if numerator < 0 or denominator != 1:
        raise ValueError("price must be nonnegative integral micro-USD/token")
    return numerator
