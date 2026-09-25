"""A decline, a malformed reply or a refusal never writes to the venue.

Regression for longrun1-open decision-3293 (antagonist-core, qwen3.8-flash): its
answer was a 0.012 BTC market order whose prose said it could not place one, and
the kernel placed it. The recorded reply carries no refusal form: ``status`` is
absent and the prose sits in a field no contract names (``counterfactual_reason``),
so it is an order of its contract, and the kernel reads fields, never prose
(Chapter II §I.a: the kernel never chooses a seat's action). What the kernel did
not enforce was the refusal form itself: a ``status: "cannot"`` without a string
``reason``, or in another case, still placed the order beside it and ran the tool
calls in it.

The rule now, in one place (``declines``): a decline is ``status`` reading
``cannot`` in any case; its ``reason`` is optional, and one that is not a string
fails the envelope (malformed). The wire's refusal form requires ``status`` alone,
so the published contract is the enforced one. Each test below tries to make a
declining, malformed or refusing reply write and asserts nothing reaches the venue.
"""

from dataclasses import replace

import pytest

from factorylab.cortex.assembly import declines, validate_return_sections
from factorylab.cortex.request import Return
from factorylab.kernel.queue import SettleStatus
from factorylab.runtime.worlds import load_manifest
from factorylab.settlement.vocabulary import DECLINED_DEFINITION, evaluator_answer_schema
from tests.runtime.test_kind_requests import TASK, _children, _world
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
    assert "commission.declined" in kinds and "return.validation_failed" not in kinds, name


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
    kinds = _no_venue_write(runtime, handle)
    assert "return.validation_failed" in kinds


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


# --- one definition of a decline: every site that recognises one agrees ------------

#: A decline in another case, and one without a reason, each beside an order and a
#: venue write: both are declines (``declines``), never malformed and never a write.
CONTINUED_DECLINES = (
    {**ORDER, "status": "CANNOT", "reason": "venue unavailable", "tool_calls": [LIMIT]},
    {**ORDER, "status": "cannot", "tool_calls": [LIMIT]},
)


def _multi_kind_runtime(provider):
    """seed-decider answers as ProducerReturn or a population kind: a decline names neither."""
    base = load_manifest("scripted")
    finding = {"type": "object", "properties": {"note": {"type": "string"}}}
    seats = tuple(replace(a, emits=("ProducerReturn", "Finding"), schemas={"Finding": finding})
                  if a.id == "seed-decider" else a for a in base.assemblies)
    return _consequence_runtime(provider=provider, exchange=_exchange(),
                                manifest=replace(base, assemblies=seats))


def _settled_declined(runtime, handle):
    items = [i for i in runtime.ledger._recovery_items() if i.get("handle") == handle]
    assert not any(i["kind"] == "return.validation_failed" for i in items)
    assert any(i["kind"] == "evaluation.declined" and i["definition"] == DECLINED_DEFINITION
               for i in items)
    assert runtime.queue.get(handle).status is SettleStatus.INAPPLICABLE


@pytest.mark.parametrize("reply", CONTINUED_DECLINES)
def test_a_decline_in_a_multi_kind_tool_continuation_is_declined_not_malformed(reply):
    provider = Scripted(reply, {"action": "hold"})
    runtime = _multi_kind_runtime(provider)
    handle = _produce_as(runtime, "seed-decider")
    kinds = _no_venue_write(runtime, handle)
    assert "tool.call" not in kinds and "commission.declined" in kinds
    assert len(provider.replies) == 1  # no continuation round was bought
    _settled_declined(runtime, handle)


@pytest.mark.parametrize("reply", [{"status": "CANNOT"}, {"status": " Cannot ", "reason": "x"}])
def test_a_decline_is_read_in_the_published_spelling_a_status_enum_admits(reply):
    """A judge's contract pins ``status`` to ``["cannot"]``: a decline in another case,
    or without a reason, is that decline, never a malformed judgement."""
    schema = evaluator_answer_schema({"type": "array"}, {"type": "array"})
    parsed, dropped = validate_return_sections(dict(reply), schema, kind="Verdict")
    assert parsed["status"] == "cannot" and dropped == ()
    assert declines(parsed)


def test_a_decline_whose_reason_is_not_a_string_is_malformed():
    schema = evaluator_answer_schema({"type": "array"}, {"type": "array"})
    with pytest.raises(ValueError):
        validate_return_sections({"status": "CANNOT", "reason": 3}, schema, kind="Verdict")


@pytest.mark.parametrize("multi_kind", [False, True])
@pytest.mark.parametrize("reply", CONTINUED_DECLINES)
def test_a_child_that_declines_so_settles_declined_and_places_nothing(
        monkeypatch, reply, multi_kind):
    rt, req = _world(monkeypatch, reply=reply)
    rt._retire_assembly("seed-observer", "test")  # helper-a is then the one executor
    spec = rt.assemblies["seed-decider"].spec
    rt._instantiate(replace(spec, id="helper-a", **(
        {"emits": ("ProducerReturn", "Finding"),
         "schemas": {"Finding": {"type": "object"}}} if multi_kind else {})))
    rt._invoke_child("seed-decider", req, TASK, req.cost_ceiling)
    (child,) = _children(rt)
    handle = child["handle"]
    assert child["target"] == "helper-a" and rt.order_intents == {}
    (invocation,) = [i for i in rt.ledger._recovery_items()
                     if i["kind"] == "invocation" and i.get("handle") == handle]
    assert invocation["status"] == "refused"
    if not multi_kind:
        # A refusal is not consumed ok, so no requester holds it: unjudged, it
        # settles declined once the verdict timeout passes, as a routed refusal does.
        # A contract of several kinds binds none, so it settles declined at once.
        assert rt.pending[handle].declined and rt.pending[handle].requester is None
        rt.ticks_consumed += rt.ev.verdict_timeout_ticks + 1
        rt._censor_stale_judgements()
    _settled_declined(rt, handle)
    assert rt.order_intents == {}
