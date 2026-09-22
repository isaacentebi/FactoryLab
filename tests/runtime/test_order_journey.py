"""The executed-order journey of PR121 (decisions 264, 273 and 304), offline.

A producer that trades through a venue tool and then reports the trade in its
final answer must keep its effects and its claim: the report is evaluable, it is
never a second instruction, and an identical order the same seat already has
resting is not placed twice.
"""

import json
from decimal import Decimal

from factorylab.kernel.events import EventKind
from factorylab.world.exchange import FakeExchange
from factorylab.world.models import ModelResponse
from tests.runtime.test_loop import (
    _consequence_decision,
    _consequence_diary,
    _consequence_produce,
    _consequence_runtime,
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
    runtime = _consequence_runtime(provider=provider, exchange=_exchange())
    handle, event = _consequence_produce(runtime)
    assert event.payload["status"] == "ok"
    assert _writes(runtime, handle) == []
    items = _consequence_diary(runtime)
    assert any(i["kind"] == "order.refused" and i["handle"] == handle for i in items)


def test_identical_order_already_resting_for_the_same_seat_is_refused():
    """Decision 273: the same seat re-placed the same resting limit order."""
    runtime = _consequence_runtime(exchange=_exchange())
    first = _consequence_decision(runtime, "seed-decider", "verdict")
    runtime.consequences.start(first, 0)
    runtime.handle_to_assembly[first] = "seed-decider"
    result, _ = runtime._run_tool("seed-decider", first, LIMIT, slot="tool:0")
    assert result["status"] == "resting"
    second = _consequence_decision(runtime, "seed-decider", "verdict")
    runtime.consequences.start(second, 1)
    result, _ = runtime._run_tool("seed-decider", second, LIMIT, slot="tool:0")
    assert result["status"] == "rejected"
    assert "already resting" in result["error"] and result["error"].count("order") >= 1
    # A different size is a different order, and another seat is another owner.
    bigger = {**LIMIT, "args": {**LIMIT["args"], "size": "0.02"}}
    result, _ = runtime._run_tool("seed-decider", second, bigger, slot="tool:1")
    assert result["status"] == "resting"
    other = _consequence_decision(runtime, "seed-observer", "verdict")
    runtime.consequences.start(other, 2)
    result, _ = runtime._run_tool("seed-observer", other, LIMIT, slot="tool:0")
    assert result["status"] == "resting"


def test_duplicate_guard_releases_once_the_resting_order_is_cancelled():
    runtime = _consequence_runtime(exchange=_exchange())
    first = _consequence_decision(runtime, "seed-decider", "verdict")
    runtime.consequences.start(first, 0)
    result, _ = runtime._run_tool("seed-decider", first, LIMIT, slot="tool:0")
    order_id = result["order_id"]
    cancel = {"tool": "venue.cancel", "args": {"coin": "BTC", "order_id": order_id}}
    assert runtime._run_tool("seed-decider", first, cancel, slot="tool:1")[0][
        "status"] == "cancelled"
    second = _consequence_decision(runtime, "seed-decider", "verdict")
    runtime.consequences.start(second, 1)
    assert runtime._run_tool("seed-decider", second, LIMIT, slot="tool:0")[0][
        "status"] == "resting"


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
