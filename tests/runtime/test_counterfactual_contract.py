"""A producing return that executes nothing names the trade it declined (essay II.III.b).

AGENTS.md rule 6: an evaluator is graded by realized consequence, a fact the world
measures, "the priced road not taken" among them, and the signal that grades an
evaluator sits outside the loop it judges. A return that executed nothing and named
nothing had no world outcome, so the verdicts about it were graded by other models'
readings alone. Naming the declined trade is therefore part of the return's I/O
contract: the kernel refuses a return without it as malformed, with a factual reason,
and the published schematics state the field as a contract fact.
"""

from __future__ import annotations

import json
from collections import deque

import pytest

from factorylab.cortex.assembly import COUNTERFACTUAL_FIELD, with_counterfactual
from factorylab.runtime.grounded import (
    COUNTERFACTUAL_ABSENT,
    COUNTERFACTUAL_SHAPE,
    COUNTERFACTUAL_UNLISTED,
    OPPORTUNITY_DEFINITION,
    counterfactual_refusal,
    declined_trade,
)
from factorylab.world.models import ModelResponse
from factorylab.world.scripted import _description_from_prompt
from tests.runtime.test_loop import _consequence_produce
from tests.runtime.test_reward_chain import Population, _advance, _judge, _mids, _rows


class Seat(Population):
    """Producers answer exactly the queued replies, verbatim; judges as ``Population``."""

    def __init__(self, *replies, verdicts=(0.8,)):
        super().__init__(verdicts=verdicts)
        self.replies = deque(replies)
        self.prompts: list[str] = []

    def complete(self, req):
        text = "\n".join(str(m.get("content", "")) for m in req.messages)
        if not _description_from_prompt(text).startswith("Respond to event"):
            return super().complete(req)
        self.prompts.append(text)
        return ModelResponse(req.model_id, json.dumps(self.replies.popleft()),
                             self.input_tokens, self.output_tokens, "end_turn")


def _world(*replies, verdicts=(0.8,), **mids):
    from tests.runtime.test_reward_chain import _consequence_runtime

    rt = _consequence_runtime(provider=Seat(*replies, verdicts=verdicts))
    rt._manage_reserve_window()
    _mids(rt, **(mids or {"BTC": "100"}))
    return rt


def _returned(rt, handle):
    """The (status, validation reason) the kernel recorded for one producer decision."""
    (invocation,) = [row for row in _rows(rt, "invocation", handle=handle)
                     if row["role"] == "producer"]
    failed = _rows(rt, "return.validation_failed", handle=handle)
    return invocation["status"], (failed[0]["reason"] if failed else None)


# --- the contract, enforced ------------------------------------------------------------


@pytest.mark.parametrize("label", ["hold", "defer", "investigate", "build"])
def test_a_return_that_executes_nothing_and_names_nothing_is_malformed(label):
    """Whatever its action label, a producing final answer that executed no venue
    operation and names no declined trade is malformed, and the reason is a fact."""
    rt = _world({"action": label, "rationale": "no edge"})
    handle, event = _consequence_produce(rt)
    assert _returned(rt, handle) == ("malformed", COUNTERFACTUAL_ABSENT)
    assert event.payload["status"] == "malformed"
    assert handle not in rt.reference_mids
    # The seat is told, in its own inbox, that its return was refused (the ledgered
    # reason above is the body it reads).
    (told,) = [o for o in rt.outcomes.unread("seed-decider")["items"]
               if o.get("kind") == "return_rejected"]
    assert told["handle"] == handle and told["status"] == "malformed"


def test_the_refusal_reasons_state_facts_and_give_no_advice():
    for reason in (COUNTERFACTUAL_ABSENT, COUNTERFACTUAL_SHAPE, COUNTERFACTUAL_UNLISTED):
        lowered = reason.lower()
        assert not any(word in lowered for word in (
            "should", "must", "please", "score", "reward", "consider", "try", "you"))


@pytest.mark.parametrize("coin", ["ETH", "DOGE"])
def test_a_counterfactual_naming_a_coin_the_world_does_not_list_is_malformed(coin):
    rt = _world({"action": "hold", "counterfactual": {"coin": coin, "side": "buy"}})
    handle, _event = _consequence_produce(rt)
    assert _returned(rt, handle) == ("malformed", COUNTERFACTUAL_UNLISTED)
    assert COUNTERFACTUAL_UNLISTED == "counterfactual names a coin the world does not list"


@pytest.mark.parametrize("named", [{"coin": "BTC", "side": "hold"}, {"coin": "BTC"}, "buy:BTC"])
def test_a_counterfactual_that_is_not_a_coin_and_a_side_is_malformed(named):
    rt = _world({"action": "hold", "counterfactual": named})
    handle, _event = _consequence_produce(rt)
    status, _reason = _returned(rt, handle)
    assert status == "malformed"


@pytest.mark.parametrize("named,listed", [("BTC", {"BTC": "100"}),
                                          ("kPEPE", {"kPEPE": "100", "BTC": "5"})])
def test_a_named_declined_trade_is_priced_at_the_backstop_and_its_judges_are_world_graded(
        named, listed):
    """A valid counterfactual is priced at the consequence backstop from the mids frozen
    when the return was made, and the verdict about it is scored against that world
    fact (``opportunity-cost-v2``), never only by the tier above. The coin is matched
    in the world's own spelling, a mixed-case listing included."""
    rt = _world({"action": "hold", "counterfactual": {"coin": named, "side": "buy"}},
                verdicts=(0.9,), **listed)
    producer, event = _consequence_produce(rt)
    assert _returned(rt, producer) == ("ok", None)
    assert rt.reference_mids[producer]["declined"] == {"coin": named, "side": "buy"}
    judge = _judge(rt, event)
    rt._settle_arrived_verdicts()
    _mids(rt, **{named: "102"})
    _advance(rt, rt.ev.consequence_backstop_ticks + 1)
    (priced,) = _rows(rt, "consequence.opportunity", handle=producer)
    assert priced["declined"] == {"coin": named, "side": "buy"}
    assert priced["horizon_ticks"] == rt.ev.consequence_backstop_ticks
    assert rt.world_outcomes[producer]["kind"] == OPPORTUNITY_DEFINITION
    scored = _rows(rt, "verdict.consequence", handle=judge)
    assert scored and scored[0]["outcome"] == OPPORTUNITY_DEFINITION


def test_an_acting_return_needs_no_counterfactual():
    """A return that executes a venue operation is measured by return_paid_off and is
    held to nothing new: an answer order, and a report after a venue tool's write,
    stand without a counterfactual. The same report from a decision that wrote nothing
    executed nothing, and is malformed."""
    rt = _world({"action": "order", "coin": "BTC", "side": "buy", "size": "0.0001"})
    ordered, _event = _consequence_produce(rt)
    assert _returned(rt, ordered) == ("ok", None)
    assert rt.executed_operations(ordered) and rt._acted(ordered)

    limit = {"coin": "BTC", "side": "buy", "size": "0.0001", "price": "30000"}
    rt = _world({"action": "order", "tool_calls": [
        {"tool": "venue.place_limit", "args": limit}]}, {"action": "order"})
    wrote, _event = _consequence_produce(rt)
    assert _returned(rt, wrote) == ("ok", None)
    assert rt.executed_operations(wrote)

    rt = _world({"action": "order"})
    reported, _event = _consequence_produce(rt)
    assert _returned(rt, reported) == ("malformed", COUNTERFACTUAL_ABSENT)


LIMIT = {"coin": "BTC", "side": "buy", "size": "0.0001", "price": "30000"}


def _venue_answers(rt, monkeypatch, answer):
    """The venue answers every placement with ``answer`` (an OrderResult, or an exception
    raised on placement and on lookup alike)."""
    from factorylab.world.exchange import OrderResult

    def place(order):
        if isinstance(answer, Exception):
            raise answer
        return OrderResult(None, answer, 0, None, "venue rejected order")

    def lookup(client_id, *, order_id=None):
        raise answer if isinstance(answer, Exception) else RuntimeError("no lookup")

    monkeypatch.setattr(rt.exchange, "place", place)
    monkeypatch.setattr(rt.exchange, "lookup", lookup)


def test_a_rejected_venue_write_followed_by_a_bare_report_is_malformed(monkeypatch):
    """A write the venue rejected executed nothing: the report that follows owes a
    counterfactual, like any return that executed nothing."""
    rt = _world({"action": "order", "tool_calls": [{"tool": "venue.place_limit",
                                                    "args": LIMIT}]}, {"action": "order"})
    _venue_answers(rt, monkeypatch, "rejected")
    handle, _event = _consequence_produce(rt)
    assert [row["status"] for row in rt.executed_operations(handle)] == ["rejected"]
    assert not rt._acted(handle)
    assert _returned(rt, handle) == ("malformed", COUNTERFACTUAL_ABSENT)


def test_a_rejected_venue_write_with_a_counterfactual_is_priced_by_the_named_trade(
        monkeypatch):
    rt = _world({"action": "order", "tool_calls": [{"tool": "venue.place_limit",
                                                    "args": LIMIT}]},
                {"action": "hold", "counterfactual": {"coin": "BTC", "side": "sell"}},
                verdicts=(0.4,))
    _venue_answers(rt, monkeypatch, "rejected")
    producer, event = _consequence_produce(rt)
    assert _returned(rt, producer) == ("ok", None)
    assert not rt._acted(producer)
    judge = _judge(rt, event)
    rt._settle_arrived_verdicts()
    _mids(rt, BTC="99")
    _advance(rt, rt.ev.consequence_backstop_ticks + 1)
    (priced,) = _rows(rt, "consequence.opportunity", handle=producer)
    assert priced["declined"] == {"coin": "BTC", "side": "sell"}
    assert rt.world_outcomes[producer]["kind"] == OPPORTUNITY_DEFINITION
    (scored, *_) = _rows(rt, "verdict.consequence", handle=judge)
    assert scored["outcome"] == OPPORTUNITY_DEFINITION


def test_an_uncertain_venue_write_is_acting_and_needs_no_counterfactual(monkeypatch):
    """A write whose answer never came may have executed: it counts as acting."""
    rt = _world({"action": "order", "tool_calls": [{"tool": "venue.place_limit",
                                                    "args": LIMIT}]}, {"action": "order"})
    _venue_answers(rt, monkeypatch, ConnectionError("lost"))
    handle, _event = _consequence_produce(rt)
    assert [row["status"] for row in rt.executed_operations(handle)] == ["uncertain"]
    assert rt._acted(handle)
    assert _returned(rt, handle) == ("ok", None)


def test_nothing_is_required_while_the_world_lists_no_coin():
    """With no coin listed no trade can be named: a bare return then stands, and has no
    world outcome, as a delisted coin at the horizon has none."""
    from tests.runtime.test_reward_chain import _consequence_runtime

    rt = _consequence_runtime(provider=Seat({"action": "hold"}))
    rt._manage_reserve_window()
    assert not rt.recent_mids
    handle, _event = _consequence_produce(rt)
    assert _returned(rt, handle) == ("ok", None)
    assert handle not in rt.reference_mids


def test_the_contract_is_checked_against_the_listing_alone():
    assert counterfactual_refusal({"action": "hold"}, ["BTC"]) == COUNTERFACTUAL_ABSENT
    assert counterfactual_refusal({"action": "hold"}, []) is None
    named = {"counterfactual": {"coin": "btc", "side": "SELL"}}
    assert counterfactual_refusal(named, ["BTC"]) is None
    assert declined_trade(named, ["BTC"]) == {"coin": "BTC", "side": "sell"}
    assert counterfactual_refusal(named, []) == COUNTERFACTUAL_UNLISTED
    assert counterfactual_refusal({"counterfactual": "sell:BTC"}, ["BTC"]) == (
        COUNTERFACTUAL_SHAPE)
    # Two listings that differ only in case: the exact spelling is the one named.
    assert declined_trade({"counterfactual": {"coin": "kPEPE", "side": "buy"}},
                          ["KPEPE", "kPEPE"]) == {"coin": "kPEPE", "side": "buy"}


# --- the contract, published ---------------------------------------------------------


def test_a_producing_request_publishes_the_field_and_a_judging_one_does_not():
    rt = _world({"action": "hold", "counterfactual": {"coin": "BTC", "side": "buy"}})
    _consequence_produce(rt)
    (prompt,) = rt.provider.prompts
    schema = json.loads(prompt.split("OUTCOME SCHEMA\n", 1)[1].split("\n", 1)[0])
    assert schema["properties"]["counterfactual"] == COUNTERFACTUAL_FIELD
    assert "counterfactual" not in schema.get("required", [])  # the kernel says when
    for seat, asm in rt.assemblies.items():
        contract = rt._contract_schema(seat)
        shapes = contract.get("anyOf", [contract])
        for kind, shape in zip(asm.spec.emits, shapes, strict=True):
            published = "counterfactual" in shape["properties"]
            assert published == (kind in ("ProducerReturn", "Exposure")), (seat, kind)


def test_a_declared_producing_kind_carries_the_field_even_in_a_closed_schema():
    closed = {"type": "object", "properties": {"answer": {"type": "integer"}},
              "required": ["answer"], "additionalProperties": False}
    opened = with_counterfactual(closed)
    assert opened["properties"]["counterfactual"] == COUNTERFACTUAL_FIELD
    assert opened["additionalProperties"] is False and "counterfactual" not in closed[
        "properties"]
    union = with_counterfactual({"anyOf": [closed, {"type": "string"}]})
    assert "counterfactual" in union["anyOf"][0]["properties"]
    assert union["anyOf"][1] == {"type": "string"}


def test_the_schematics_publish_the_field_as_a_contract_fact_without_advice():
    rt = _world()
    text = rt.institution_section("a_return_may_include")["counterfactual"]
    for fact in ("ProducerReturn", "Exposure", "judged", "exposure", '"side": "buy" | "sell"',
                 "recent_mids", "required", "no venue operation", "malformed",
                 "not required while recent_mids is empty"):
        assert fact in text, fact
    lowered = text.lower()
    # "reward shape" is the published name of a kind's settlement; no scoring is here.
    for advice in ("should", "must", "please", "consider", "you ", "your", "score",
                   "rewarded", "graded", "priced", "benefit", "better", "worse"):
        assert advice not in lowered, advice
    assert "counterfactual" in rt._stable_prefix_text() or rt._prompt_mode() == "compact"
