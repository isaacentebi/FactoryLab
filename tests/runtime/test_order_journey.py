"""The executed-order journey of PR121 (decisions 264, 273 and 304), offline.

A producer that trades through a venue tool and then reports the trade in its
final answer must keep its effects and its claim: the report is evaluable and it
is never a second instruction.

Chapter II rulings, R6. What is kernel physics stays pinned here: a client id is
idempotent (a retry reconciles and never submits twice), a decision acts once (an
answer never executes in place of a write the same decision had refused or
dropped, nor beside one it made), and a batch of writes is weighed whole (one
refused leg holds back every leg; the same order twice in one batch is one
decision acting twice). What was an architect guardrail is gone: an order
identical to one an earlier decision left resting is placed, because the venue
allows it and fees price it.
"""

import copy
import json
from dataclasses import replace
from decimal import Decimal
from types import SimpleNamespace

from factorylab.cortex.request import Return
from factorylab.kernel.events import Event, EventKind
from factorylab.runtime.worlds import load_manifest
from factorylab.world.exchange import FakeExchange
from factorylab.world.models import ModelResponse
from tests.runtime.test_loop import (
    _consequence_decision,
    _consequence_diary,
    _consequence_produce,
    _consequence_runtime,
    lists_nothing,
)


class Scripted:
    """Replies in order, one per provider call."""

    def __init__(self, *replies):
        self.replies = list(replies)
        self.requests = []

    def complete(self, req):
        self.requests.append(req)
        reply = self.replies.pop(0)
        return ModelResponse(req.model_id, json.dumps(reply), 300, 40, "end_turn")


def _exchange():
    return FakeExchange(coins=("BTC", "ETH"),
                        start_prices={"BTC": Decimal("100"), "ETH": Decimal("10")})


LIMIT = {"tool": "venue.place_limit",
         "args": {"coin": "BTC", "side": "sell", "size": "0.01", "price": "150"}}


def _writes(runtime, handle):
    return [i for cid, i in runtime.order_intents.items() if i["handle"] == handle]


def test_tool_order_reported_as_order_is_an_ok_return_not_malformed():
    """Decision 264: a limit order through a tool, then ``action: order`` with no fields."""
    provider = Scripted(
        {"action": "order", "tool_calls": [LIMIT]},
        {"action": "order", "rationale": "BTC sell resting",
         "working_state": {"pending": "limit resting"}},
    )
    runtime = _consequence_runtime(provider=provider, exchange=_exchange())
    handle, event = _consequence_produce(runtime)
    assert event.payload["status"] == "ok"
    assert event.payload["outputs"]["action"] == "order"
    writes = _writes(runtime, handle)
    assert len(writes) == 1 and writes[0]["operation"] == "venue.place_limit"
    assert writes[0]["result"]["status"] == "resting"
    # The judge sees what the decision actually executed, beside what it said.
    executed = event.payload["executed_operations"]
    assert [(e["operation"], e["status"]) for e in executed] == [
        ("venue.place_limit", "resting")]
    assert executed[0]["args"]["size"] == "0.01"


def test_final_answer_order_after_a_tool_write_is_a_report_never_a_second_order():
    """A report that repeats the traded fields must not place a market order too."""
    provider = Scripted(
        {"action": "investigate", "tool_calls": [LIMIT]},
        {"action": "order", "coin": "BTC", "side": "sell", "size": "0.01"},
    )
    runtime = _consequence_runtime(provider=provider, exchange=_exchange())
    handle, event = _consequence_produce(runtime)
    writes = _writes(runtime, handle)
    assert [w["operation"] for w in writes] == ["venue.place_limit"]
    items = _consequence_diary(runtime)
    reports = [i for i in items if i["kind"] == "order.reported"]
    assert len(reports) == 1 and reports[0]["handle"] == handle
    assert event.payload["status"] == "ok"


def test_final_answer_order_without_a_tool_write_still_places_one_market_order():
    provider = Scripted({"action": "order", "coin": "BTC", "side": "buy", "size": "0.01"})
    runtime = _consequence_runtime(provider=provider, exchange=_exchange())
    handle, _ = _consequence_produce(runtime)
    writes = _writes(runtime, handle)
    assert [w["operation"] for w in writes] == ["venue.place_market"]
    assert writes[0]["result"]["status"] == "filled"


def test_order_answer_missing_fields_without_a_write_is_refused_not_malformed():
    """No trade was made and none can be read: the seat is told, the return stands."""
    provider = Scripted({"action": "order", "rationale": "want to buy"})
    runtime = lists_nothing(_consequence_runtime(provider=provider, exchange=_exchange()))
    handle, event = _consequence_produce(runtime)
    assert event.payload["status"] == "ok"
    assert _writes(runtime, handle) == []
    items = _consequence_diary(runtime)
    assert any(i["kind"] == "order.refused" and i["handle"] == handle for i in items)


def test_a_later_decisions_identical_order_is_placed_beside_the_resting_one():
    """R6: the cross-decision duplicate refusal was a guardrail; the venue allows it."""
    exchange = _exchange()
    runtime = _consequence_runtime(exchange=exchange)
    first = _consequence_decision(runtime, "seed-decider", "verdict")
    runtime.consequences.start(first, 0)
    runtime.handle_to_assembly[first] = "seed-decider"
    one, _ = runtime._run_tool("seed-decider", first, LIMIT, slot="tool:0")
    assert one["status"] == "resting"
    second = _consequence_decision(runtime, "seed-decider", "verdict")
    runtime.consequences.start(second, 1)
    runtime.handle_to_assembly[second] = "seed-decider"
    two, _ = runtime._run_tool("seed-decider", second, LIMIT, slot="tool:0")
    assert two["status"] == "resting" and two["order_id"] != one["order_id"]
    assert len(exchange.open_orders()) == 2
    assert second not in runtime.venue_attempts  # nothing was refused, so the answer may act
    assert "order.duplicate" not in _kinds(runtime, second)


def test_a_retried_client_id_reconciles_and_never_places_twice():
    """Kernel physics: a write's identity is its decision and slot, and it is idempotent."""
    exchange = _exchange()
    runtime = _consequence_runtime(exchange=exchange)
    handle = _consequence_decision(runtime, "seed-decider", "verdict")
    runtime.consequences.start(handle, 0)
    first, _ = runtime._run_tool("seed-decider", handle, LIMIT, slot="tool:0")
    again, _ = runtime._run_tool("seed-decider", handle, copy.deepcopy(LIMIT), slot="tool:0")
    assert again["order_id"] == first["order_id"]
    assert len(exchange.open_orders()) == 1 and len(_writes(runtime, handle)) == 1
    # The same identity cannot be rebound to a different write.
    other = {**LIMIT, "args": {**LIMIT["args"], "size": "0.02"}}
    refused, _ = runtime._run_tool("seed-decider", handle, other, slot="tool:0")
    assert "client id already binds another intent" in refused["error"]
    assert len(exchange.open_orders()) == 1


def test_continuation_turn_labelled_order_still_dispatches_its_cancel():
    """Decision 304: ``action: order`` beside a valid cancel voided the cancel."""
    provider = Scripted()
    runtime = _consequence_runtime(provider=provider, exchange=_exchange())
    first = _consequence_decision(runtime, "seed-decider", "verdict")
    runtime.consequences.start(first, 0)
    order_id = runtime._run_tool("seed-decider", first, LIMIT, slot="tool:0")[0]["order_id"]
    cancel = {"tool": "venue.cancel", "args": {"coin": "BTC", "order_id": order_id}}
    provider.replies += [{"action": "order", "tool_calls": [cancel]},
                         {"action": "hold", "rationale": "cancelled the oversized sell"}]
    handle, event = _consequence_produce(runtime)
    writes = _writes(runtime, handle)
    assert [(w["operation"], w["result"]["status"]) for w in writes] == [
        ("venue.cancel", "cancelled")]
    assert event.payload["status"] == "ok"
    assert all(e.kind != EventKind.PRODUCER_RETURN or e.payload["status"] == "ok"
               for e in runtime.internal)


def test_judge_sees_the_work_and_its_acts_not_the_producers_world():
    """Essay II.I.b: a judge sees request, answer, acts and propensity — input and output."""
    from tests.runtime.test_loop import _consequence_judge

    provider = Scripted(
        {"action": "order", "tool_calls": [LIMIT]},
        {"action": "order", "rationale": "BTC sell resting"},
        {"verdict": 0.6, "rationale": "the report matches the resting order"},
    )
    runtime = _consequence_runtime(provider=provider, exchange=_exchange())
    _, event = _consequence_produce(runtime)
    _consequence_judge(runtime, event, "eval-a")
    prompt = "\n".join(str(m.get("content", "")) for m in provider.requests[-1].messages)
    assert "WORLD UPDATE" not in prompt  # the producer's world is not the judge's
    assert "OPERATING ACCESS" in prompt  # its own tools remain
    assert '"executed_operations":[{' in prompt and '"status":"resting"' in prompt
    assert "since_you_last_woke" not in prompt


def test_a_bad_read_drops_itself_and_answers_in_the_next_round():
    """PR121 seq 4799: one read missing an argument voided a batch of good reads."""
    provider = Scripted(
        {"tool_calls": [{"tool": "venue.positions", "args": {}},
                        {"tool": "venue.funding_history", "args": {"coin": "ETH"}}]},
        {"action": "hold", "rationale": "positions read; will retry the history"},
    )
    runtime = lists_nothing(_consequence_runtime(provider=provider, exchange=_exchange()))
    _, event = _consequence_produce(runtime)
    assert event.payload["status"] == "ok"
    continuation = "\n".join(str(m.get("content", ""))
                             for m in provider.requests[1].messages)
    assert '"tool":"venue.positions"' in continuation
    assert '"tool":"venue.funding_history"' in continuation
    assert "missing argument n" in continuation


def test_a_turn_whose_every_read_was_wrong_is_still_a_continuation():
    """PR121 seq 11547: two reads, both missing an argument, voided the decision."""
    provider = Scripted(
        {"tool_calls": [{"tool": "venue.funding_history", "args": {"coin": "ETH"}},
                        {"tool": "venue.funding_history", "args": {"coin": "BTC"}}]},
        {"tool_calls": [{"tool": "venue.funding_history", "args": {"coin": "BTC", "n": 5}}]},
        {"action": "hold", "rationale": "funding flat"},
    )
    runtime = lists_nothing(_consequence_runtime(provider=provider, exchange=_exchange()))
    _, event = _consequence_produce(runtime)
    assert event.payload["status"] == "ok" and len(provider.requests) == 3
    second = "\n".join(str(m.get("content", "")) for m in provider.requests[1].messages)
    assert second.count("missing argument n") >= 2  # both slots answered with their error


def test_a_bad_item_in_a_batch_that_writes_still_voids_the_whole_batch():
    bad_limit = {"tool": "venue.place_limit",
                 "args": {"coin": "BTC", "side": "sell", "price": "150"}}  # no size
    provider = Scripted({"action": "hold", "tool_calls": [
        {"tool": "venue.positions", "args": {}}, bad_limit]})
    runtime = lists_nothing(_consequence_runtime(provider=provider, exchange=_exchange()))
    handle, event = _consequence_produce(runtime)
    assert _writes(runtime, handle) == [] and len(provider.requests) == 1
    assert event.payload["status"] == "ok"  # the answer stands; the batch never ran


def test_reads_over_the_turn_limit_are_answered_in_their_slots():
    """Live harness: five outcome.get calls against a four-call limit voided the turn."""
    reads = [{"tool": "venue.positions", "args": {}}] * 5
    provider = Scripted({"tool_calls": reads}, {"action": "hold", "rationale": "read"})
    runtime = lists_nothing(_consequence_runtime(provider=provider, exchange=_exchange()))
    limit = runtime.m.tools.max_tool_calls
    provider.replies[0] = {"tool_calls": [{"tool": "venue.positions", "args": {}}] * (limit + 1)}
    _, event = _consequence_produce(runtime)
    assert event.payload["status"] == "ok" and len(provider.requests) == 2
    second = "\n".join(str(m.get("content", "")) for m in provider.requests[1].messages)
    assert f"more than {limit} tool_calls" in second


def test_a_writing_batch_never_runs_beside_a_refused_call():
    calls = [LIMIT, ""]  # a write beside a malformed item
    provider = Scripted({"action": "hold", "tool_calls": calls})
    runtime = _consequence_runtime(provider=provider, exchange=_exchange())
    handle, event = _consequence_produce(runtime)
    assert _writes(runtime, handle) == [] and len(provider.requests) == 1


def test_a_draft_status_beside_tool_calls_does_not_void_the_turn():
    provider = Scripted(
        {"status": "pending", "tool_calls": [{"tool": "venue.positions", "args": {}}]},
        {"action": "hold", "rationale": "done"})
    runtime = lists_nothing(_consequence_runtime(provider=provider, exchange=_exchange()))
    _, event = _consequence_produce(runtime)
    assert event.payload["status"] == "ok" and len(provider.requests) == 2


def test_an_order_described_only_in_prose_is_refused_with_how_to_place_it():
    provider = Scripted({"action": "order", "rationale": "I submit one tiny limit buy"})
    runtime = lists_nothing(_consequence_runtime(provider=provider, exchange=_exchange()))
    handle, _ = _consequence_produce(runtime)
    refusal = next(i for i in _consequence_diary(runtime)
                   if i["kind"] == "order.refused" and i["handle"] == handle)
    assert "nothing was submitted" in refusal["reason"]
    assert "venue.place_limit" in refusal["reason"]


def test_the_venue_sdks_own_argument_names_are_translated_not_refused():
    """Edition 5 testnet: a funding-spread hedge written with is_buy was voided whole."""
    sdk = {"tool": "venue.place_limit",
           "args": {"coin": "BTC", "is_buy": False, "sz": "0.01", "limit_px": "150"}}
    provider = Scripted({"action": "investigate", "tool_calls": [sdk]},
                        {"action": "order", "rationale": "short resting"})
    runtime = _consequence_runtime(provider=provider, exchange=_exchange())
    handle, _ = _consequence_produce(runtime)
    writes = _writes(runtime, handle)
    assert [(w["args"]["side"], w["args"]["size"], w["args"]["price"]) for w in writes] == [
        ("sell", "0.01", "150")]


def _kinds(runtime, handle):
    return [i["kind"] for i in _consequence_diary(runtime) if i.get("handle") == handle]


def test_an_answer_order_written_in_sdk_names_never_defaults_its_side():
    """Cold review: is_buy false beside no side executed a market BUY."""
    for answer in ({"action": "order", "coin": "BTC", "is_buy": False, "size": "0.01"},
                   {"action": "order", "coin": "BTC", "is_buy": False, "sz": "0.01"},
                   {"action": "order", "coin": "BTC", "size": "0.01"},
                   {"action": "order", "coin": "BTC", "side": "sell", "size": "0.01",
                    "price": "150"}):
        runtime = _consequence_runtime(provider=Scripted(answer), exchange=_exchange())
        handle, _ = _consequence_produce(runtime)
        assert _writes(runtime, handle) == [], answer


def test_a_refused_writing_batch_does_not_trade_through_the_answer():
    """Cold review: the dropped limit's answer filled as a market sell."""
    bad = {"tool": "venue.place_limit",
           "args": {"coin": "BTC", "side": "sell", "size": "0.01", "price": "150", "tif": "Gtc"}}
    provider = Scripted({"action": "order", "coin": "BTC", "side": "sell", "size": "0.01",
                         "tool_calls": [bad]})
    runtime = _consequence_runtime(provider=provider, exchange=_exchange())
    handle, _ = _consequence_produce(runtime)
    assert _writes(runtime, handle) == []


#: An order no collateral in this world can carry: the venue check refuses it.
OVERSIZED = {"tool": "venue.place_limit",
             "args": {"coin": "BTC", "side": "sell", "size": "100000", "price": "150"}}


def test_a_write_refused_as_a_tool_is_not_placed_through_the_answer():
    """A decision acts once: a refused limit may not come back as a market sell."""
    provider = Scripted({"action": "investigate", "tool_calls": [OVERSIZED]},
                        {"action": "order", "coin": "BTC", "side": "sell", "size": "0.01"})
    runtime = _consequence_runtime(provider=provider, exchange=_exchange())
    handle, _ = _consequence_produce(runtime)
    assert _writes(runtime, handle) == []
    refusal = [i for i in _consequence_diary(runtime)
               if i["kind"] == "order.refused" and i.get("handle") == handle]
    assert refusal and "nothing was submitted" in refusal[-1]["reason"]
    assert "collateral" in refusal[-1]["reason"]
    assert "not execute in the write's place" in refusal[-1]["reason"]


def test_a_later_answer_repeating_a_resting_order_is_executed():
    """R6: a later decision's order is its own act, even when it repeats a resting one."""
    resting = {"tool": "venue.place_limit",
               "args": {"coin": "BTC", "side": "sell", "size": "0.01", "price": "150"}}
    provider = Scripted({"action": "investigate", "tool_calls": [resting]},
                        {"action": "hold"},
                        {"action": "order", "coin": "BTC", "side": "sell", "size": "0.01"})
    runtime = _consequence_runtime(provider=provider, exchange=_exchange())
    _consequence_produce(runtime)
    second, _ = _consequence_produce(runtime)
    assert [(w["operation"], w["result"]["status"]) for w in _writes(runtime, second)] == [
        ("venue.place_market", "filled")]
    assert "order.duplicate" not in _kinds(runtime, second)


def test_a_hedge_whose_second_leg_would_be_refused_places_neither_leg():
    """Cold review: the ETH leg filled alone beside a refused leg."""
    leg = {"tool": "venue.place_market", "args": {"coin": "ETH", "side": "buy", "size": "0.5"}}
    provider = Scripted({"action": "investigate", "tool_calls": [leg, OVERSIZED]},
                        {"action": "order", "coin": "ETH", "side": "buy", "size": "0.5"})
    runtime = _consequence_runtime(provider=provider, exchange=_exchange())
    handle, _ = _consequence_produce(runtime)
    assert _writes(runtime, handle) == []  # neither leg, and not the answer either
    refused = [i for i in _consequence_diary(runtime)
               if i["kind"] == "order.batch_refused" and i.get("handle") == handle]
    assert refused and "collateral" in refused[0]["reason"]


def test_one_order_written_twice_in_one_batch_places_nothing():
    provider = Scripted({"action": "investigate", "tool_calls": [LIMIT, copy.deepcopy(LIMIT)]},
                        {"action": "hold"})
    runtime = _consequence_runtime(provider=provider, exchange=_exchange())
    handle, _ = _consequence_produce(runtime)
    assert _writes(runtime, handle) == []


# --- primitive audit F7: the answer order belongs to the producer kinds alone ------


def _produce_as(runtime, seat):
    """One decision by ``seat`` on a tick, through the real producer path."""
    runtime.n += 1
    handle = _consequence_decision(runtime, seat, "verdict")
    runtime._producer_step(
        Event(f"tick-{runtime.n}", EventKind.TICK, runtime.clock.now_ns, {"index": 0}, "test"),
        handle, SimpleNamespace(chosen=seat), runtime.queue.get(handle).deadline_ns)
    return handle


def _custom_kind_runtime(provider):
    base = load_manifest("scripted")
    schema = {"type": "object", "properties": {"action": {"type": "string"},
                                               "coin": {"type": "string"},
                                               "side": {"type": "string"},
                                               "size": {"type": "string"}}}
    seats = tuple(replace(a, emits=("Finding",), schemas={"Finding": schema})
                  if a.id == "seed-decider" else a for a in base.assemblies)
    return _consequence_runtime(provider=provider, exchange=_exchange(),
                                manifest=replace(base, assemblies=seats))


def test_a_population_kinds_order_word_is_its_own_and_never_trades():
    """F7: a kind that does not own the answer order gives ``action: order`` its own
    meaning. The return stands, is published as its own kind, and places nothing."""
    order = {"action": "order", "coin": "BTC", "side": "buy", "size": "0.01"}
    runtime = lists_nothing(_custom_kind_runtime(Scripted(order)))
    handle = _produce_as(runtime, "seed-decider")
    assert _writes(runtime, handle) == []
    kinds = _kinds(runtime, handle)
    assert "order.refused" not in kinds and "order.intent" not in kinds
    event = next(e for e in runtime.internal if e.payload.get("about_handle") == handle)
    assert str(event.kind) == "Finding" and event.payload["status"] == "ok"
    assert event.payload["outputs"]["action"] == "order"


def test_a_population_kind_still_trades_through_venue_tools_and_acts_once():
    """F7 moves the answer order, not the venue: a tool write is the kind's act."""
    runtime = _custom_kind_runtime(Scripted(
        {"action": "investigate", "tool_calls": [LIMIT]},
        {"action": "order", "coin": "BTC", "side": "sell", "size": "0.01"}))
    handle = _produce_as(runtime, "seed-decider")
    assert [w["operation"] for w in _writes(runtime, handle)] == ["venue.place_limit"]


def test_the_antagonists_answer_order_is_still_one_market_order():
    """Exposure is a producer kind: its answer order is placed exactly once."""
    runtime = _consequence_runtime(
        provider=Scripted({"action": "order", "coin": "BTC", "side": "buy", "size": "0.01"}),
        exchange=_exchange())
    handle = _produce_as(runtime, "antagonist-a")
    writes = _writes(runtime, handle)
    assert [(w["operation"], w["result"]["status"]) for w in writes] == [
        ("venue.place_market", "filled")]


def test_an_answer_order_in_sdk_names_places_nothing_on_the_antagonists_kind_either():
    for answer in ({"action": "order", "coin": "BTC", "is_buy": False, "size": "0.01"},
                   {"action": "order", "coin": "BTC", "side": "sell", "size": "0.01",
                    "price": "150"}):
        runtime = _consequence_runtime(provider=Scripted(answer), exchange=_exchange())
        handle = _produce_as(runtime, "antagonist-a")
        assert _writes(runtime, handle) == [], answer


def test_order_fields_written_into_a_verdict_place_nothing():
    """A judge's reply is a Verdict: an order written into it is not an order."""
    runtime = _consequence_runtime(exchange=_exchange())
    handle = _consequence_decision(runtime, "eval-a", "verdict")
    runtime.return_kinds[handle] = "Verdict"
    runtime._execute_outputs(Return(handle, {"verdict": 0.5, "action": "order", "coin": "BTC",
                                             "side": "buy", "size": "0.01"}, 0, "ok"))
    assert _writes(runtime, handle) == []
