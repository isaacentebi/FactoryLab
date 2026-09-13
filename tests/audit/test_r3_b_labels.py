"""Round three, group B: the action a decision is learned under is the action it took
(triage row T15). Nothing here touches a network."""

import json
from decimal import Decimal

import pytest

from factorylab.cortex.request import Return
from factorylab.kernel.queue import PropensityRecord
from factorylab.learners.base import BanditFeedback
from factorylab.learners.exp3 import EXP3
from factorylab.runtime.propensity import (
    MIN_DECLARED_MASS,
    action_label,
    action_vocabulary,
    declared_record,
    effect_label,
)
from factorylab.runtime.shared import CH_VERDICT
from factorylab.world.exchange import FakeExchange
from factorylab.world.models import ModelResponse
from tests.conftest import make_runtime
from tests.runtime.test_loop import _consequence_produce, _consequence_runtime


def _items(rt, kind):
    return [i for i in rt.ledger._recovery_items() if i["kind"] == kind]


class ToolTrader:
    """Opens through a tool and answers noop, then closes through a tool and answers noop:
    the fixture of tests/runtime/test_loop.py that learned both returns as ``hold``."""

    def __init__(self):
        self.calls = 0

    def complete(self, req):
        replies = [
            {"action": "hold", "propensity": {"hold": 0.5, "buy:BTC:l": 0.5},
             "tool_calls": [{"tool": "venue.place_market",
                             "args": {"coin": "BTC", "side": "buy", "size": "1"}}]},
            {"action": "noop", "propensity": {"hold": 0.5, "buy:BTC:l": 0.5}},
            {"action": "hold", "tool_calls": [{"tool": "venue.close", "args": {"coin": "BTC"}}]},
            {"action": "noop"},
        ]
        reply = replies[self.calls]
        self.calls += 1
        return ModelResponse(req.model_id, json.dumps(reply), 300, 40, "end_turn")


def test_a_trade_through_a_tool_followed_by_noop_is_learned_as_the_trade():
    exchange = FakeExchange(coins=("BTC",), start_prices={"BTC": Decimal("100")},
                            price_path={"BTC": [Decimal("100.005010")]},
                            fee_bps=Decimal(0), spread_bps=Decimal(0))
    rt = _consequence_runtime(provider=ToolTrader(), exchange=exchange)
    rt.tool_specs["venue.place_market"]["price_micro_per_call"] = 11
    opener, _ = _consequence_produce(rt)
    calls = _items(rt, "tool.call")
    assert [(c["tool"], c["outcome"]) for c in calls] == [("venue.place_market", "ok")], calls
    declared = rt.queue.declared_propensity(opener)
    assert declared.chosen == "buy:BTC:l"  # the fill, not the final word
    assert dict(zip(declared.action_ids, declared.probs, strict=True)) == {
        "hold": 0.5, "buy:BTC:l": 0.5}
    rt._settle_exchange_effects(exchange.advance(1_000_000_000))
    closer, _ = _consequence_produce(rt)
    assert rt.queue.declared_propensity(closer).chosen == "close:BTC"
    assert rt.window.tool_calls == rt.window.fills == 2


def test_effects_precede_the_final_answer_and_a_refused_write_is_not_an_action(monkeypatch):
    rt = make_runtime()
    actor = "r3b-router"
    handle = rt.queue.open(
        actor=actor, event_id="r3b", channel=CH_VERDICT, deadline_ns=10**15,
        parent_handle=None, cost_ceiling=10_000_000,
        propensity=PropensityRecord(("seed-decider",), (1.0,), "seed-decider", 0, actor, "t"))
    rt.handle_to_assembly[handle] = "seed-decider"
    rt._start_return(handle)
    calls = iter([
        Return(handle, {}, 0, "ok", tool_calls=(
            {"tool": "venue.set_leverage", "args": {"coin": "BTC", "leverage": 2}},
            {"tool": "venue.place_market", "args": {"coin": "BTC", "side": "buy",
                                                    "size": "0.001"}},
            {"tool": "venue.nothing", "args": {}},
        )),
        Return(handle, {"action": "order", "coin": "ETH", "side": "buy", "size": "0.004",
                        "propensity": {"leverage:BTC+buy:BTC:xs+buy:ETH:xs": 0.9, "hold": 0.1}},
               0, "ok"),
    ])
    monkeypatch.setattr(rt, "_invoke_compute", lambda *a: next(calls))
    req = rt._request(handle, "Trade", {}, {"type": "object"}, 10**15, CH_VERDICT)
    ret = rt._invoke("seed-decider", req, "producer")
    assert ret.status == "ok"
    assert [c["outcome"] for c in _items(rt, "tool.call")] == ["ok", "ok", "failed"]
    declared = rt.queue.declared_propensity(handle)
    assert declared.chosen == "leverage:BTC+buy:BTC:xs+buy:ETH:xs"
    assert not _items(rt, "propensity.refused")
    # A judge cannot write; its refused tool call names no action of its own.
    judge = rt.queue.open(
        actor=actor, event_id="r3b-judge", channel="conformity", deadline_ns=10**15,
        parent_handle=None, cost_ceiling=10_000_000,
        propensity=PropensityRecord(("eval-a",), (1.0,), "eval-a", 0, actor, "t"))
    rt.handle_to_assembly[judge] = "eval-a"
    rt._start_return(judge)
    calls = iter([
        Return(judge, {}, 0, "ok", tool_calls=({"tool": "venue.place_market", "args": {
            "coin": "BTC", "side": "buy", "size": "1"}},)),
        Return(judge, {"verdict": 0.7, "payoff": 0.5, "rationale": "r", "forecasts": []},
               0, "ok"),
    ])
    req = rt._request(judge, "Evaluate", {}, {"type": "object"}, 10**15, "conformity")
    rt._invoke("eval-a", req, "evaluator")
    assert _items(rt, "tool.refused")[-1]["handle"] == judge
    assert rt.queue.declared_propensity(judge).chosen == "verdict:0.7"


def test_effect_and_action_labels_share_one_vocabulary():
    order = {"coin": "btc", "side": "buy", "size": "0.5"}
    assert effect_label("venue.place_market", order) == "buy:BTC:m"
    assert effect_label("venue.place_limit", {**order, "price": "1"}) == "buy:BTC:m"
    assert effect_label("venue.place_market", order) == action_label(
        "producer", {"action": "order", **order}, "ok")
    assert effect_label("venue.close", {"coin": "eth"}) == "close:ETH"
    assert effect_label("venue.cancel", {"coin": "ETH", "order_id": "1"}) == "cancel:ETH"
    assert effect_label("venue.set_leverage", {"coin": "BTC", "leverage": 3}) == "leverage:BTC"
    assert effect_label("treasury.transfer", {"direction": "perps_to_spot"}) == (
        "transfer:perps_to_spot")
    assert effect_label("catalogue.search", {"substring": "x"}) is None
    assert effect_label("venue.place_market", {"coin": "BTC", "side": "buy"}) == "malformed"
    assert action_label("producer", {"action": "noop"}, "ok", ("buy:BTC:l",)) == "buy:BTC:l"
    assert action_label("producer", {"action": "hold"}, "ok", ("request:helper",)) == (
        "request:helper")
    assert action_label("producer", {"action": "noop"}, "ok", ("malformed",)) == "malformed"
    assert action_label("producer", {"action": "noop"}, "malformed", ("buy:BTC:l",)) == (
        "malformed")
    assert action_label("evaluator", {"verdict": 0.84}, "ok", ("request:x",)) == "verdict:0.8"
    producer = action_vocabulary()["producer"]
    for name in ('"close:<COIN>"', '"request:<assembly id>"', '"transfer:<direction>"', '"+"'):
        assert name in producer


def test_the_mass_declared_on_the_action_taken_is_floored_before_it_weights_a_reward():
    tiny = 1e-6
    record, reason = declared_record(
        "hold", {"hold": tiny, "buy:BTC": 1 - tiny}, learner_id="assembly:x", state_hash="h")
    assert set(record.action_ids) == {"hold", "buy:BTC"} and record.chosen == "hold"
    mass = dict(zip(record.action_ids, record.probs, strict=True))
    # Raising the declared mass to the floor is not enough on its own: normalising
    # {hold: 0.000001, buy:BTC: 0.999999} against an unchanged remainder records
    # about 0.04762, an importance weight of 21. The rest are rescaled around the
    # floor instead, so the recorded mass is the floor and the weight is 20.
    assert mass["hold"] == MIN_DECLARED_MASS
    assert mass["buy:BTC"] == pytest.approx(1 - MIN_DECLARED_MASS)
    assert 1 / mass["hold"] == pytest.approx(20)
    assert sum(record.probs) == pytest.approx(1.0)
    assert "floored" in reason and f"{MIN_DECLARED_MASS}" in reason
    # However lopsided the declaration, the action taken is recorded at or above the floor.
    for declared in ({"hold": 1e-9, "a": 0.5, "b": 0.5 - 1e-9},
                     {"hold": 0.04, "a": 0.96},
                     {"hold": 0.01, "a": 0.33, "b": 0.33, "c": 0.33}):
        lopsided, _ = declared_record("hold", declared, learner_id="assembly:x", state_hash="h")
        recorded = dict(zip(lopsided.action_ids, lopsided.probs, strict=True))
        assert recorded["hold"] >= MIN_DECLARED_MASS
        assert sum(lopsided.probs) == pytest.approx(1.0)
    # An honest declaration at or above the floor is untouched.
    record, reason = declared_record(
        "hold", {"hold": MIN_DECLARED_MASS, "buy:BTC": 1 - MIN_DECLARED_MASS},
        learner_id="assembly:x", state_hash="h")
    assert reason is None and record.probs[record.action_ids.index("hold")] == MIN_DECLARED_MASS
    # With two arms and gamma 0.1, one reward through the recorded mass moves the
    # chosen arm to at most (1 - gamma) * e^(gamma/2/floor) / (1 + e^(gamma/2/floor)) + gamma/2.
    learner = EXP3(("hold", "buy:BTC"), 0.1)
    learner.update(BanditFeedback("hold", 1.0, mass["hold"]))
    assert learner.distribution(("hold", "buy:BTC"))["hold"] < 0.75


def test_a_floored_declaration_is_still_used_and_the_population_is_told():
    from tests.audit.test_a10_propensity import Decider, _register_learner

    tiny = 1e-6
    rt = _consequence_runtime(provider=Decider(propensity={"hold": tiny, "buy:BTC": 1 - tiny}))
    rt._manage_reserve_window()
    _register_learner(rt, "author", ("hold", "buy:BTC"), learner="exp3")
    handle, _event = _consequence_produce(rt)
    declared = rt.queue.declared_propensity(handle)
    assert set(declared.action_ids) == {"hold", "buy:BTC"}
    assert declared.probs[declared.action_ids.index("hold")] >= MIN_DECLARED_MASS * 0.9
    assert rt.assembly_rounds[handle] == "seed-decider"  # the round is open, not discarded
    assert _items(rt, "propensity.floored")[-1]["handle"] == handle
    assert not _items(rt, "propensity.refused")
    assert any("floored" in f["reason"] for f in rt.registration_feedback)
