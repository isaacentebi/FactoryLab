"""Exact integer micro-USD at every accounting boundary.

Every USD-to-micro-USD conversion in the factory goes through ``usd_to_micro``
and names its rounding, so no two call sites can disagree about what becomes of
a fraction of a micro-USD.
"""

from decimal import ROUND_CEILING, ROUND_FLOOR, ROUND_HALF_EVEN, Decimal
from typing import Any

Money = int
MICRO_USD_PER_USD = 1_000_000


def _mode(rounding: str):
    """Return the decimal rounding mode a caller named, refusing any other name."""
    if rounding == "floor":
        return ROUND_FLOOR
    if rounding == "ceil":
        return ROUND_CEILING
    if rounding == "nearest":
        return ROUND_HALF_EVEN
    raise ValueError("rounding must be exact, floor, ceil or nearest")


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


def usd_to_micro(value: Decimal | str | int | float, *, rounding: str) -> Money:
    """Return integer micro-USD for a USD amount, rounded exactly as ``rounding`` says.

    ``exact`` refuses any amount that is not already an integral micro-USD;
    ``floor`` rounds towards minus infinity, ``ceil`` towards plus infinity and
    ``nearest`` half-to-even. A number is read through its decimal text, never
    its binary expansion, and the shift by a million is exact at any magnitude.
    Non-finite amounts are refused whatever the rounding.
    """
    amount = value if isinstance(value, (Decimal, str)) else Decimal(str(value))
    if rounding == "exact":
        numerator, denominator = _decimal(amount).as_integer_ratio()
        micros, remainder = divmod(numerator * MICRO_USD_PER_USD, denominator)
        if remainder:
            raise ValueError("amount is not an integral micro-USD")
        return micros
    mode = _mode(rounding)
    amount = Decimal(amount)
    if not amount.is_finite():
        raise ValueError("amount must be finite")
    sign, digits, exponent = amount.as_tuple()
    shifted = Decimal((sign, digits, exponent + 6))
    return int(shifted.to_integral_value(rounding=mode))


def nonnegative_usd_micro(value: Any, *, rounding: str) -> Money:
    """Return micro-USD for a finite, nonnegative USD amount; anything else raises ValueError.

    Venues and providers quote prices and balances as wire values of unknown
    shape. This is the one place that decides what a bad one is; each caller
    catches ``ValueError`` and raises its own error type, so no provider's
    pricing fault can surface as another provider's failure.
    """
    try:
        amount = value if isinstance(value, (Decimal, str)) else Decimal(str(value))
        amount = Decimal(amount)
        if not amount.is_finite() or amount < 0:
            raise ValueError("negative or non-finite")
        micros = usd_to_micro(amount, rounding=rounding)
    except (ArithmeticError, TypeError, ValueError) as exc:
        raise ValueError("amount must be a finite nonnegative USD value") from exc
    return micros


def usd_to_money(value: Decimal | str) -> Money:
    """Preserve USD exactly; reject floats and amounts smaller than an integral micro-USD."""
    return usd_to_micro(_decimal(value), rounding="exact")


def money_to_usd(value: Money) -> Decimal:
    """Return exact USD, independently of the caller's Decimal precision."""
    require_money(value)
    sign = 1 if value < 0 else 0
    digits = tuple(int(digit) for digit in str(abs(value)))
    return Decimal((sign, digits, -6))
