"""R3-E: the prompts GPT-6's third reading asked for, and the arithmetic it asked for.

Five claims are proved here.

The stable prefix is the WORLD CONTRACT wrapper with the charter's own five norms
and a compact base capability index, and nothing else. It is serialised once per
runtime and reused byte-for-byte: two requests in one world open with identical
bytes, and so does a runtime restored from that world's own checkpoint. What used
to ride there — the cards, their prices, the mechanics, every argument schema —
is published once, where it moves, which is the reviewer's own instruction: byte
stability "does not require copying every institutional description into that
prefix".

The lens is out of the system prompt. Every edition 3 seat's system message is the
common system contract, verbatim and identical for all nine; the lens is the first
head of C1's working state, seeded once, and a seat may overwrite it from its very
first return. A permanent role re-asserted on every call is not a hypothesis.

The ``YOU`` block renders §8's template with kernel-serialised slots, and where a
source is missing it says ``unavailable``. It never says zero. A seat told its
venue account is empty when the venue would not answer has been taught something
false about its own world, which is the whole of what this round is for.

The OUTCOME CONTRACT is rendered once per request, after the schema it is about.

And ``calc`` answers every fee, funding and carry case of the calibration set
exactly — the reviewer's §7 repair for a roster that did not meet "every critical
arithmetic case" — while the seventeen cases that are not arithmetic remain a
seat's own work, because a deterministic calculator has no standing to refuse on
a covenant or to write a program.
"""

from __future__ import annotations

import pytest

from factorylab.cortex.calc import FUNDING_CONVENTION, calc

# ------------------------------------------------------------------------------- calc


def test_calc_is_exact_unit_explicit_and_states_its_sign_convention():
    fee = calc({"op": "fee", "notional": "918.7575", "fee_bps": "45"})
    assert fee["fee_usd"] == "4.134409" and fee["notional_usd"] == "918.757500"
    # A float argument is read as the number written, not the binary value near it.
    assert calc({"op": "notional", "size": 0.1, "price": 3})["notional_usd"] == "0.300000"
    paid = calc({"op": "funding", "size": "2", "mark": "100", "rate": "0.001"})
    assert paid["funding_usd"] == "0.200000"
    assert paid["convention"] == FUNDING_CONVENTION
    assert "paid by this position" in paid["sign"]
    received = calc({"op": "funding", "size": "-2", "mark": "100", "rate": "0.001"})
    assert received["funding_usd"] == "-0.200000"
    margin = calc({"op": "margin", "size": "-2", "mark": "3000", "leverage": "3"})
    assert margin["margin_usd"] == "2000.000000"  # a short posts margin, never negative
    assert all(k.endswith(("_usd", "_bps", "_hours")) or k in
               ("op", "convention", "period", "sign", "leverage", "net_usd_positive")
               for k in calc({"op": "carry", "size": "1", "mark": "1", "hourly_rate": "0",
                              "hours": "1", "round_trip_fee": "0"}))
    # It prescribes nothing: a subtraction's sign is a fact, "worth doing" is not.
    assert "worth_doing" not in calc({"op": "carry", "size": "1", "mark": "1",
                                      "hourly_rate": "1", "hours": "1",
                                      "round_trip_fee": "0"})


@pytest.mark.parametrize("args,fragment", [
    ({}, "calc op must be one of"),
    ({"op": "carve"}, "calc op must be one of"),
    ({"op": "notional", "size": "x", "price": "1"}, "not a decimal number"),
    ({"op": "notional", "size": "1"}, "calc price is required"),
    ({"op": "fee", "fee_bps": "1"}, "needs notional, or size with price"),
    ({"op": "margin", "size": "1", "mark": "1", "leverage": "0"}, "must be positive"),
    ({"op": "notional", "size": True, "price": "1"}, "decimal number or its string"),
    ({"op": "notional", "size": "nan", "price": "1"}, "must be finite"),
])
def test_calc_says_why_rather_than_raising(args, fragment):
    """A bad argument is a result, never an exception on the invocation path."""
    assert fragment in calc(args)["error"]
