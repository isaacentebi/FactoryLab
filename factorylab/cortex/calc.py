"""``calc``: deterministic, unit-explicit arithmetic for a seat that must be exact.

GPT-6's third reading, §7: the final roster does not answer every critical
arithmetic case, and the repair it names is not a better prompt but a
capability — "give seats a deterministic, unit-explicit arithmetic capability
(it prescribes no objective)". This is that capability.

Five operations, all of them the arithmetic a position actually costs:
``notional``, ``fee``, ``funding``, ``carry`` and ``margin``. Every one is exact
``Decimal`` arithmetic quantised to six decimal places — the same quantisation
``scripts/calibrate_seats.py`` scores its cases at — and every numeric field it
returns carries its unit in its own name, so a number cannot cross a contract
boundary without saying what it is. Nothing here reads the world, nothing here
is random, and nothing here recommends anything: ``carry`` reports
``net_usd_positive``, which is a fact about a subtraction, and never
``worth_doing``, which would be a judgement this tool has no standing to make.

The sign convention is stated in the result rather than assumed. ``funding``
returns what *this position* pays over one settlement period under the venue's
stated convention: positive is paid out by the holder, negative is received.
A caller that gets the sign wrong is wrong against a sentence it was handed.
"""

from __future__ import annotations

from decimal import Decimal, DivisionByZero, InvalidOperation
from typing import Any

#: Six decimal places, the same quantum the calibration case set scores at, with
#: the default (half-even) rounding a bare ``quantize`` uses there.
QUANTUM = Decimal("0.000001")

#: The venue convention this tool computes ``funding`` under, stated in every
#: funding and carry result rather than assumed by the reader.
FUNDING_CONVENTION = "positive rate is paid by longs"
FUNDING_PERIOD = "hourly"

OPERATIONS = ("notional", "fee", "funding", "carry", "margin")

CALC_SPEC: dict[str, Any] = {
    "id": "calc",
    "description": (
        "Exact decimal position arithmetic, to six decimal places, with units in every "
        "field name. op=notional(size, price); op=fee(fee_bps and either notional or "
        "size+price); op=funding(size, mark, rate) — what the position pays over one "
        "settlement period, positive when the holder pays, under the convention stated "
        "in the result; op=carry(size, mark, hourly_rate, hours, round_trip_fee); "
        "op=margin(size, mark, leverage). Deterministic, offline, and it prescribes "
        "no objective."
    ),
    "args_schema": {
        "type": "object",
        "properties": {
            "op": {"enum": list(OPERATIONS)},
            "size": {"type": ["string", "number"],
                     "description": "base units; signed for a perp position"},
            "price": {"type": ["string", "number"], "description": "USD per base unit"},
            "mark": {"type": ["string", "number"], "description": "USD per base unit"},
            "notional": {"type": ["string", "number"], "description": "USD"},
            "fee_bps": {"type": ["string", "number"], "description": "basis points"},
            "rate": {"type": ["string", "number"],
                     "description": "funding rate for one settlement period, as a fraction"},
            "hourly_rate": {"type": ["string", "number"],
                            "description": "funding rate per hour, as a fraction"},
            "hours": {"type": ["string", "number"], "description": "window length in hours"},
            "round_trip_fee": {"type": ["string", "number"],
                               "description": "USD paid to open and close"},
            "leverage": {"type": ["string", "number"], "description": "times notional"},
            "convention": {"type": "string",
                           "description": f"echoed back; default {FUNDING_CONVENTION!r}"},
            "period": {"type": "string",
                       "description": f"echoed back; default {FUNDING_PERIOD!r}"},
        },
        "required": ["op"],
        "additionalProperties": False,
    },
    # No rail, no jail, no model: a pure function of its arguments. It is priced at
    # ``prices.tool_micro_per_call`` where a world commits one and is free otherwise,
    # and it is ledgered like every other call either way.
    "price_micro_per_call": 0,
    "kind": "calc",
}


def _decimal(args: dict[str, Any], name: str) -> Decimal:
    """One argument as an exact ``Decimal``, or say which argument was not a number.

    Floats are read through ``str`` so ``0.1`` is the number the caller wrote and
    not the binary value nearest it: this tool's whole claim is exactness, and a
    silent binary widening would break it on the first funding rate.
    """
    if name not in args or args[name] is None:
        raise ValueError(f"calc {name} is required")
    value = args[name]
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        raise ValueError(f"calc {name} must be a decimal number or its string")
    try:
        parsed = Decimal(str(value).strip())
    except (InvalidOperation, ValueError):
        raise ValueError(f"calc {name} is not a decimal number") from None
    if not parsed.is_finite():
        raise ValueError(f"calc {name} must be finite")
    return parsed


def _six(value: Decimal) -> str:
    """Six decimal places, exactly, as text — never a float."""
    return str(value.quantize(QUANTUM))


def calc(args: Any) -> dict[str, Any]:
    """Answer one arithmetic question exactly, or say why it is not one.

    Guarantees the result is a pure function of ``args``: the same object always
    produces the same bytes, no clock and no world is read, and a bad argument
    returns ``{"error": ...}`` rather than raising, so a tool call can never
    become an exception on the invocation path. Every numeric field names its
    own unit and every funding figure carries the convention it was computed
    under.
    """
    if not isinstance(args, dict):
        return {"error": "calc args must be an object"}
    op = args.get("op")
    if op not in OPERATIONS:
        return {"error": f"calc op must be one of {', '.join(OPERATIONS)}"}
    try:
        return {"op": op, **_dispatch(op, args)}
    except ValueError as exc:
        return {"error": str(exc)}
    except (InvalidOperation, DivisionByZero, OverflowError):
        return {"error": "calc arguments are out of range for exact arithmetic"}


def _dispatch(op: str, args: dict[str, Any]) -> dict[str, Any]:
    if op == "notional":
        size, price = _decimal(args, "size"), _decimal(args, "price")
        return {"notional_usd": _six(size * price)}
    if op == "fee":
        notional = _notional_for_fee(args)
        bps = _decimal(args, "fee_bps")
        return {"notional_usd": _six(notional),
                "fee_bps": str(bps),
                "fee_usd": _six(notional * bps / Decimal(10_000))}
    if op == "funding":
        size, mark, rate = (_decimal(args, "size"), _decimal(args, "mark"),
                            _decimal(args, "rate"))
        return {"notional_usd": _six(size * mark),
                "funding_usd": _six(size * mark * rate),
                "convention": str(args.get("convention") or FUNDING_CONVENTION),
                "period": str(args.get("period") or FUNDING_PERIOD),
                "sign": "positive funding_usd is paid by this position; negative is "
                        "received by it"}
    if op == "carry":
        size, mark = _decimal(args, "size"), _decimal(args, "mark")
        rate, hours = _decimal(args, "hourly_rate"), _decimal(args, "hours")
        fee = _decimal(args, "round_trip_fee")
        carry_usd = size * mark * rate * hours
        net = carry_usd - fee
        return {"notional_usd": _six(size * mark),
                "carry_usd": _six(carry_usd),
                "round_trip_fee_usd": _six(fee),
                "net_usd": _six(net),
                # A fact about a subtraction. Whether it is worth doing is the
                # seat's decision and this tool has no standing to make it.
                "net_usd_positive": net > 0,
                "window_hours": str(hours),
                "convention": str(args.get("convention") or FUNDING_CONVENTION),
                "period": str(args.get("period") or FUNDING_PERIOD)}
    size, mark, leverage = (_decimal(args, "size"), _decimal(args, "mark"),
                            _decimal(args, "leverage"))
    if leverage <= 0:
        raise ValueError("calc leverage must be positive")
    notional = size * mark
    return {"notional_usd": _six(notional),
            "leverage": str(leverage),
            "margin_usd": _six(abs(notional) / leverage)}


def _notional_for_fee(args: dict[str, Any]) -> Decimal:
    """The notional a fee is charged on: given directly, or from size and price.

    One call answers a whole fee question. A seat that has a size and a price
    should not have to spend two tool calls and an intermediate rounding to
    learn what the fill costs it, and an intermediate rounding is exactly how
    the last digit of a six-decimal answer goes wrong.
    """
    if args.get("notional") is not None:
        return _decimal(args, "notional")
    if args.get("size") is None or (args.get("price") is None and args.get("mark") is None):
        raise ValueError("calc fee needs notional, or size with price")
    price = "price" if args.get("price") is not None else "mark"
    return _decimal(args, "size") * _decimal(args, price)
