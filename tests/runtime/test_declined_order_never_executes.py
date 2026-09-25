"""A decline, a malformed reply or a refusal never writes to the venue.

Regression for longrun1-open decision-3293 (antagonist-core, qwen3.8-flash): its
answer was a 0.012 BTC market order whose prose said it could not place one, and
the kernel placed it. The recorded reply carries no refusal form: ``status`` is
absent and the prose sits in a field no contract names (``counterfactual_reason``),
so it is an order of its contract, and the kernel reads fields, never prose
(Chapter II §I.a: the kernel never chooses a seat's action). What the kernel did
not enforce was the refusal form itself: a ``status: "cannot"`` without a string
``reason``, or in another case, still placed the order beside it and ran the tool
calls in it. Each test below tries to make a declining, malformed or refusing
reply write and asserts nothing reaches the venue.
"""

import pytest

from factorylab.cortex.assembly import declines
from factorylab.cortex.request import Return
from tests.runtime.test_loop import _consequence_decision, _consequence_runtime
from tests.runtime.test_order_journey import (
    LIMIT,
    Scripted,
    _exchange,
    _kinds,
    _produce_as,
    _writes,
)

#: decision-3293's reply, exactly as ``invocation.outputs`` recorded it.
RAW_3293 = {
    "action": "order", "coin": "BTC", "side": "buy", "size": "0.012",
    "propensity_score": 0.65,
    "counterfactual_reason": "The venue is currently unavailable due to a previous "
                             "error, preventing the execution of any trading orders. "
                             "Therefore, I cannot place an order at this time.",
    "tool_calls_required": False,
    "working_state": {"last_action": "Attempted to buy BTC but VenueUnavailable.",
                      "lens": "A judge's approval tracks presentation more closely "
                              "than outcome.",
                      "open_questions": ["Wait for venue recovery?"],
                      "position_status": "flat"},
}
ORDER = {"action": "order", "coin": "BTC", "side": "buy", "size": "0.01"}
#: Exposure (the antagonist's kind) and ProducerReturn both own the answer order.
SEATS = ("antagonist-a", "seed-decider")

DECLINES = {
    "published form": {**RAW_3293, "status": "cannot",
                       "reason": RAW_3293["counterfactual_reason"]},
    "no reason": {**RAW_3293, "status": "cannot"},
    "upper case": {**RAW_3293, "status": "CANNOT", "reason": "venue unavailable"},
    "spaced, no reason": {**RAW_3293, "status": " Cannot "},
}


def _no_venue_write(runtime, handle):
    kinds = _kinds(runtime, handle)
    assert _writes(runtime, handle) == []
    assert "order.intent" not in kinds and "consequence.order" not in kinds
    assert runtime.stats.orders_placed == 0
    return kinds


@pytest.mark.parametrize("seat", SEATS)
@pytest.mark.parametrize("name", list(DECLINES))
def test_a_declining_answer_carrying_an_order_places_nothing(seat, name):
    runtime = _consequence_runtime(provider=Scripted(DECLINES[name]), exchange=_exchange())
    handle = _produce_as(runtime, seat)
    kinds = _no_venue_write(runtime, handle)
    # A decline is a decision the population reads, not a malformed return.
    assert "commission.declined" in kinds, name


@pytest.mark.parametrize("seat", SEATS)
@pytest.mark.parametrize("reply", [
    {"status": "cannot", "tool_calls": [LIMIT]},
    {"status": "Cannot", "reason": "venue unavailable", "tool_calls": [LIMIT]},
    {**RAW_3293, "status": "cannot", "tool_calls": [
        {"tool": "venue.place_market",
         "args": {"coin": "BTC", "side": "buy", "size": "0.012"}}]},
])
def test_a_venue_tool_call_inside_a_decline_never_runs(seat, reply):
    provider = Scripted(reply, {"action": "hold"})
    runtime = _consequence_runtime(provider=provider, exchange=_exchange())
    handle = _produce_as(runtime, seat)
    kinds = _no_venue_write(runtime, handle)
    assert "tool.call" not in kinds
    assert len(provider.replies) == 1  # no continuation round was bought


@pytest.mark.parametrize("seat", SEATS)
@pytest.mark.parametrize("reply", [
    {**RAW_3293, "status": "cannot", "reason": 3},  # the refusal form, mistyped
    {**RAW_3293, "side": "long"},
    {**RAW_3293, "size": "0"},
    {**RAW_3293, "size": "0.012 BTC"},
    {**RAW_3293, "price": "85000"},
])
def test_a_malformed_order_answer_places_nothing(seat, reply):
    runtime = _consequence_runtime(provider=Scripted(reply), exchange=_exchange())
    handle = _produce_as(runtime, seat)
    _no_venue_write(runtime, handle)


@pytest.mark.parametrize("name", list(DECLINES))
def test_the_gate_refuses_a_decline_handed_in_unrewritten(name):
    """``_execute_outputs`` is the one gate: a decline handed to it still ``ok``
    (never through the invocation's rewrite) places nothing either."""
    runtime = _consequence_runtime(exchange=_exchange())
    handle = _consequence_decision(runtime, "antagonist-a", "verdict")
    runtime._execute_outputs(Return(handle, dict(DECLINES[name]), 0, "ok"), "Exposure")
    _no_venue_write(runtime, handle)


def test_the_gate_places_nothing_for_a_return_that_is_not_ok():
    runtime = _consequence_runtime(exchange=_exchange())
    handle = _consequence_decision(runtime, "antagonist-a", "verdict")
    for status in ("refused", "malformed", "failed"):
        runtime._execute_outputs(Return(handle, dict(ORDER), 0, status), "Exposure")
    _no_venue_write(runtime, handle)


def test_declines_reads_the_status_field_alone():
    assert all(declines(reply) for reply in DECLINES.values())
    assert declines({"status": "cannot"})
    for reply in (RAW_3293, ORDER, {"status": "observed", **ORDER},
                  {"reason": "I cannot place an order", **ORDER}, None, "cannot"):
        assert not declines(reply), reply


@pytest.mark.parametrize("seat", SEATS)
def test_a_legitimate_answer_order_still_places_one_market_order(seat):
    runtime = _consequence_runtime(provider=Scripted(dict(ORDER)), exchange=_exchange())
    handle = _produce_as(runtime, seat)
    writes = _writes(runtime, handle)
    assert [(w["operation"], w["result"]["status"], w["args"]["size"]) for w in writes] == [
        ("venue.place_market", "filled", "0.01")]


def test_the_recorded_reply_is_an_order_of_its_contract_not_a_decline():
    """decision-3293 as recorded: no refusal form, a valid order form. The kernel
    places the order it names and reads no prose for a meaning the fields do not
    carry. Refusing it would take a contract change (an answer order closed to
    fields its contract does not name), not a reading of its words."""
    assert not declines(RAW_3293)
    runtime = _consequence_runtime(provider=Scripted(dict(RAW_3293)), exchange=_exchange())
    handle = _produce_as(runtime, "antagonist-a")
    writes = _writes(runtime, handle)
    assert [(w["operation"], w["args"]["side"], w["args"]["size"]) for w in writes] == [
        ("venue.place_market", "buy", "0.012")]
