"""A10: the deciding agent's propensity rides on the request (seat 6, finding 3).

The reproduction: `Request` had no propensity field, the `ProducerReturn`
carried `{kind, payload, outputs, cost, status}`, and the only propensity in the
world was the router's distribution over which assembly to wake. So no consumer
of any request could price a road not taken, and Blum--Mansour could only form
over executor selection.
"""

import json
import random
from dataclasses import replace
from types import SimpleNamespace

import pytest

from factorylab.cortex.request import Request, validate_propensity
from factorylab.kernel.queue import PropensityRecord
from factorylab.runtime.propensity import action_label, action_vocabulary, declared_record
from factorylab.world.scripted import ScriptedProvider
from tests.runtime.test_loop import (
    _consequence_judge,
    _consequence_produce,
    _consequence_runtime,
)


class Decider(ScriptedProvider):
    """A producer that discloses the field it drew from, and a judge that does too."""

    def __init__(self, propensity=None, judge_propensity=None):
        super().__init__()
        self.propensity = propensity
        self.judge_propensity = judge_propensity
        self.judge_inputs = []

    def _produce(self, desc, inputs):
        reply = {"action": "hold"}
        if self.propensity is not None:
            reply["propensity"] = self.propensity
        return reply

    def _evaluate(self, req, inputs):
        self.judge_inputs.append(inputs)
        reply = super()._evaluate(req, inputs)
        if self.judge_propensity is not None:
            reply = {**reply, "propensity": self.judge_propensity}
        return reply


def _declared(runtime, handle):
    return runtime.queue.declared_propensity(handle)


# --- the record on the handle -------------------------------------------------


def test_a_declared_propensity_is_a_second_record_on_the_same_handle():
    field = {"hold": 0.6, "buy:BTC": 0.3, "sell:BTC": 0.1}
    runtime = _consequence_runtime(provider=Decider(propensity=field))
    handle, _event = _consequence_produce(runtime)
    router, declared = runtime.queue.propensities(handle)
    assert router is runtime.queue.get(handle).propensity
    assert router.source == "sampled" and declared.source == "declared"
    assert dict(zip(declared.action_ids, declared.probs, strict=True)) == pytest.approx(field)
    assert declared.chosen == "hold"
    assert declared.learner_id == "assembly:seed-decider"


def test_no_declaration_is_recorded_as_the_chosen_action_at_one():
    runtime = _consequence_runtime(provider=Decider())
    handle, _event = _consequence_produce(runtime)
    declared = _declared(runtime, handle)
    assert declared.action_ids == ("hold",) and declared.probs == (1.0,)
    assert declared.chosen == "hold" and declared.source == "declared"


def test_a_declaration_that_omits_the_action_taken_is_refused_with_a_reason():
    runtime = _consequence_runtime(provider=Decider(propensity={"buy:BTC": 1.0}))
    handle, _event = _consequence_produce(runtime)
    declared = _declared(runtime, handle)
    assert declared.action_ids == ("hold",)  # degenerate, not the agent's claim
    assert any("propensity must include the action taken (hold)" in f["reason"]
               for f in runtime.registration_feedback)


def test_a_declaration_that_is_not_a_distribution_is_refused():
    runtime = _consequence_runtime(provider=Decider(propensity={"hold": 0.2, "buy:BTC": 0.2}))
    handle, _event = _consequence_produce(runtime)
    assert _declared(runtime, handle).action_ids == ("hold",)
    assert any("sum to one" in f["reason"] for f in runtime.registration_feedback)


def test_a_declared_record_needs_no_seed_but_a_sampled_one_must_replay():
    declared = PropensityRecord(("a", "b"), (0.5, 0.5), "b", 0, "assembly:x", "h",
                                source="declared")
    assert declared.chosen == "b"
    with pytest.raises(ValueError, match="positive mass"):
        PropensityRecord(("a", "b"), (1.0, 0.0), "b", 0, "assembly:x", "h", source="declared")
    drawn = random.Random(0).choices(("a", "b"), weights=(0.5, 0.5), k=1)[0]
    other = "a" if drawn == "b" else "b"
    with pytest.raises(ValueError, match="seeded distribution"):
        PropensityRecord(("a", "b"), (0.5, 0.5), other, 0, "r", "h", source="sampled")


# --- the propensity travels forward -------------------------------------------


def test_the_producer_return_and_the_judge_request_carry_the_propensity():
    field = {"hold": 0.7, "buy:BTC": 0.3}
    provider = Decider(propensity=field)
    runtime = _consequence_runtime(provider=provider)
    handle, event = _consequence_produce(runtime)
    assert event.payload["propensity"]["over"] == pytest.approx(field)
    assert event.payload["propensity"]["chosen"] == "hold"
    _consequence_judge(runtime, event, "eval-a")
    judged = provider.judge_inputs[-1]["producer"]["propensity"]
    assert judged["over"] == pytest.approx(field) and judged["chosen"] == "hold"


def test_the_request_itself_declares_the_propensity_and_renders_it():
    request = Request(
        "h", "Evaluate a producer return.", {"producer": {}}, {}, {"type": "object"},
        1, 0, None, "criterion", "conformity", "h",
        propensity={"hold": 0.7, "buy:BTC": 0.3}, propensity_chosen="hold",
    )
    text = request.prompt_text()
    assert "PROPENSITY" in text and "buy:BTC" in text and "hold" in text
    with pytest.raises(ValueError, match="chosen action must belong"):
        Request("h", "d", {}, {}, {}, 1, 0, None, "c", "ch", "h",
                propensity={"hold": 1.0}, propensity_chosen="buy:BTC")


def test_a_judges_own_verdict_bucket_is_its_declared_action():
    provider = Decider(judge_propensity={"verdict:0.9": 0.8, "verdict:0.1": 0.2})
    runtime = _consequence_runtime(provider=provider)
    _handle, event = _consequence_produce(runtime, "NOOP")
    judge = _consequence_judge(runtime, event, "eval-a")
    declared = _declared(runtime, judge)
    assert declared.chosen == "verdict:0.9"  # eval-a blesses a noop at 0.9
    assert dict(zip(declared.action_ids, declared.probs, strict=True))["verdict:0.1"] == 0.2


def test_action_labels_name_what_a_return_decided():
    assert action_label("producer", {"action": "noop"}, "ok") == "hold"
    assert action_label("producer", {"action": "hold"}, "ok") == "hold"
    assert action_label(
        "producer", {"action": "order", "side": "buy", "coin": "eth", "size": "0.004"}, "ok"
    ) == "buy:ETH:xs"
    assert action_label("producer", {"action": "order"}, "ok") == "malformed"
    # an order with no placeable size named no action the kernel could take
    assert action_label(
        "producer", {"action": "order", "side": "buy", "coin": "ETH"}, "ok"
    ) == "malformed"
    assert action_label(
        "producer", {"action": "order", "side": "buy", "coin": "ETH", "size": "0"}, "ok"
    ) == "malformed"
    assert action_label("evaluator", {"verdict": 0.84}, "ok") == "verdict:0.8"
    assert action_label("meta", {"conformity": 0.25}, "ok") == "conformity:0.2"
    assert action_label("producer", {"action": "hold"}, "malformed") == "malformed"


def test_two_orders_that_differ_only_in_size_are_two_different_actions():
    """A sizing decision is a decision, so a declaration can hold an arm for it."""
    def order(size):
        return action_label(
            "producer", {"action": "order", "side": "buy", "coin": "BTC", "size": size}, "ok",
        )

    assert order("0.005") == "buy:BTC:xs"
    assert order("0.05") == "buy:BTC:s"
    assert order("0.5") == "buy:BTC:m"
    assert order("5") == "buy:BTC:l"
    assert order("50") == "buy:BTC:xl"
    assert order("0.005") != order("0.5")  # the bug: one label for both
    # and a declaration bucketed the same way is accepted rather than refused
    record, reason = declared_record(
        order("0.5"), {"hold": 0.4, "buy:BTC:xs": 0.3, "buy:BTC:m": 0.3},
        learner_id="assembly:x", state_hash="h",
    )
    assert reason is None and record.chosen == "buy:BTC:m"
    assert set(record.action_ids) == {"hold", "buy:BTC:xs", "buy:BTC:m"}


def test_the_size_bands_are_a_closed_vocabulary_the_world_block_publishes():
    from factorylab.runtime.propensity import SIZE_BAND_MAX, SIZE_BANDS

    bands = {name for _edge, name in SIZE_BANDS} | {SIZE_BAND_MAX}
    assert bands == {"xs", "s", "m", "l", "xl"}
    producer = action_vocabulary()["producer"]
    assert "<side>:<COIN>:<size band>" in producer
    for band in bands:
        assert f'"{band}"' in producer


def test_validate_propensity_bounds_the_action_set_without_naming_its_contents():
    assert validate_propensity({"a": 0.5, "b": 0.5}) == {"a": 0.5, "b": 0.5}
    with pytest.raises(ValueError, match="more than 32"):
        validate_propensity({str(i): 1 / 40 for i in range(40)})
    with pytest.raises(ValueError, match="non-empty"):
        validate_propensity({})
    with pytest.raises(ValueError, match="exceed 64"):
        validate_propensity({"x" * 65: 1.0})


def test_a_probability_no_float_can_hold_is_malformed_not_fatal():
    huge = 10 ** 400  # a JSON integer a model may write; no float holds it
    with pytest.raises(ValueError, match="finite numbers"):
        validate_propensity({"hold": huge})
    record, reason = declared_record("hold", {"hold": huge}, learner_id="assembly:x",
                                     state_hash="h")
    assert record.action_ids == ("hold",) and record.probs == (1.0,)
    assert "finite numbers" in reason
    # and the billed reply that carried it is an ordinary degenerate record, not a crash
    runtime = _consequence_runtime(provider=Decider(propensity={"hold": huge}))
    handle, _event = _consequence_produce(runtime)
    assert _declared(runtime, handle).action_ids == ("hold",)
    assert any("finite numbers" in f["reason"] for f in runtime.registration_feedback)


# --- Blum--Mansour at the assembly level --------------------------------------


def _register_learner(runtime, handle, actions, learner="blum_mansour"):
    from factorylab.cortex.registration import LearnerProposal

    runtime._register(handle, LearnerProposal("seed-decider", learner, tuple(actions), 0.1))


def _learner_runtime(propensity):
    runtime = _consequence_runtime(provider=Decider(propensity=propensity))
    runtime._manage_reserve_window()  # the novelty reserve pays for a registration
    return runtime


def test_blum_mansour_registers_over_an_assemblys_own_declared_action_set():
    field = {"hold": 0.5, "buy:BTC": 0.3, "sell:BTC": 0.2}
    runtime = _learner_runtime(field)
    seed, _event = _consequence_produce(runtime)
    _register_learner(runtime, seed, ("hold", "buy:BTC", "sell:BTC"))
    learner = runtime.assembly_learners["seed-decider"]
    assert learner.id == "assembly:seed-decider"
    assert learner.inner.actions == ("hold", "buy:BTC", "sell:BTC")
    assert runtime.registry.get("learner:seed-decider").kind == "router"

    handle, _event = _consequence_produce(runtime)
    assert runtime.assembly_rounds[handle] == "seed-decider"
    rows_before = [tuple(base.distribution(("hold", "buy:BTC", "sell:BTC")).values())
                   for base in learner.inner._bases]
    runtime._close_assembly_round(handle, 1.0)
    rows_after = [tuple(base.distribution(("hold", "buy:BTC", "sell:BTC")).values())
                  for base in learner.inner._bases]
    assert handle not in runtime.assembly_rounds
    # Every copy owns one action and learns from the round the agent actually played:
    # the reward trained the action taken, not the choice of executor.
    assert rows_after != rows_before
    assert all(after[0] > before[0] for before, after in zip(rows_before, rows_after,
                                                             strict=True))


def test_a_censored_decision_closes_its_assembly_round_without_evidence():
    runtime = _learner_runtime({"hold": 0.5, "buy:BTC": 0.5})
    seed, _event = _consequence_produce(runtime)
    _register_learner(runtime, seed, ("hold", "buy:BTC"), learner="exp3")
    handle, _event = _consequence_produce(runtime)
    learner = runtime.assembly_learners["seed-decider"]
    before = learner.inner.distribution(("hold", "buy:BTC"))
    runtime._close_assembly_round(handle, None)
    assert handle not in runtime.assembly_rounds
    assert learner.inner.distribution(("hold", "buy:BTC")) == before
    # the same round with evidence does move it
    second, _event = _consequence_produce(runtime)
    runtime._close_assembly_round(second, 1.0)
    assert learner.inner.distribution(("hold", "buy:BTC")) != before


def test_a_declaration_outside_the_registered_action_set_trains_nothing():
    runtime = _learner_runtime({"hold": 0.5, "zzz": 0.5})
    seed, _event = _consequence_produce(runtime)
    _register_learner(runtime, seed, ("hold", "buy:BTC"), learner="exp3")
    handle, _event = _consequence_produce(runtime)
    assert handle not in runtime.assembly_rounds
    assert _declared(runtime, handle).action_ids == ("hold", "zzz")  # still evidence


def test_declared_record_renormalises_and_reports_its_refusals():
    record, reason = declared_record(
        "hold", {"hold": 0.5, "buy:BTC": 0.5}, learner_id="assembly:x", state_hash="h",
    )
    assert reason is None and record.probs == (0.5, 0.5)
    record, reason = declared_record(
        "hold", {"hold": 0.0, "buy:BTC": 1.0}, learner_id="assembly:x", state_hash="h",
    )
    assert record.action_ids == ("hold",) and "positive mass" in reason


def test_the_world_block_states_the_propensity_field_shape_and_its_action_labels():
    runtime = _consequence_runtime(provider=Decider())
    block = runtime._world_block()
    text = block["a_return_may_include"]["propensity"]
    # the shape of the field, and where the data goes: a distribution over the
    # actions declared, including the one taken, read by this return's judges
    assert "{action_id: probability}" in text and "summing to one" in text
    assert "including the action you took" in text and "travels forward" in text
    # never what the kernel does with it: physics is enforced by code, not announced
    for announced in ("second propensity", "learner", "records", "1.0"):
        assert announced not in text
    assert block["action_labels"]["producer"].startswith("hold")
    assert "two propensities" in block["scoring"]["propensity"]


def test_a_published_policy_copied_verbatim_is_an_acceptable_propensity():
    provider = Decider()
    runtime = _consequence_runtime(provider=provider)
    runtime._manage_reserve_window()
    seed, _event = _consequence_produce(runtime)
    _register_learner(runtime, seed, ("hold", "buy:BTC", "sell:BTC"), learner="exp3")
    policy = runtime._action_policy("seed-decider")["over"]
    assert len(policy) == 3  # three equal thirds, rounded independently, sum to 0.999999
    assert validate_propensity(policy) == pytest.approx(policy)
    provider.propensity = policy  # the model returns the policy it was shown
    handle, _event = _consequence_produce(runtime)
    declared = _declared(runtime, handle)
    assert set(declared.action_ids) == {"hold", "buy:BTC", "sell:BTC"}
    assert not [f for f in runtime.registration_feedback if "propensity" in f.get("reason", "")]


def test_routing_still_records_the_routers_own_distribution_unchanged():
    runtime = _consequence_runtime(provider=Decider(propensity={"hold": 1.0}))
    handle, _event = _consequence_produce(runtime)
    router = runtime.queue.get(handle).propensity
    assert router.source == "sampled" and router.learner_id == "test-router"
    assert router.chosen == "seed-decider"  # still the choice of who acts


def test_propensity_cannot_be_added_after_the_decision_is_final():
    runtime = _consequence_runtime(provider=Decider())
    handle, event = _consequence_produce(runtime)
    _consequence_judge(runtime, event, "eval-a")
    record = PropensityRecord(("hold",), (1.0,), "hold", 0, "assembly:seed-decider", "h",
                              source="declared")
    with pytest.raises(ValueError, match="final outcome"):
        runtime.queue.record_propensity(handle, record)


def test_window_facts_never_carry_per_decision_attribution():
    from factorylab.runtime.observations import window_facts

    facts = window_facts(SimpleNamespace(index=1, decisions={"h": {}}, closed_values=None,
                                         costs=[1], revision_handles={"h"}))
    assert "decisions" not in facts and "closed_values" not in facts
    # the handles that were revised are a count, never the handles themselves
    assert "revision_handles" not in facts
    assert facts["revised_decisions"] == 1 and facts["costs"] == [1]


def test_a_continuation_carries_the_propensity_the_first_call_was_given(monkeypatch):
    """The billed second call produces the final verdict, so it sees the same field."""
    from factorylab.world.models import ModelResponse
    from tests.runtime.test_fa_defects import make_runtime
    from tests.runtime.test_fc_children import parent_request

    rt = make_runtime()
    field = {"hold": 0.7, "buy:BTC:xs": 0.3}
    req = replace(parent_request(rt), propensity=field, propensity_chosen="hold")
    calls = []

    def provider(request):
        text = request.messages[-1]["content"]
        calls.append(text)
        body = ({"action": "hold"} if "tool_results" in text else
                {"requests": [{"target": "nobody", "description": "d", "inputs": {},
                               "outcome_schema": {"type": "object"}}]})
        return ModelResponse(request.model_id, json.dumps(body), 1, 1, "stop")

    monkeypatch.setattr(rt.provider.target, "complete", provider)
    ret = rt._invoke("seed-decider", req, "producer")
    assert ret.status == "ok" and len(calls) == 2  # first call, then the continuation
    assert all("PROPENSITY" in text and "buy:BTC:xs" in text for text in calls)
    follow = req.continuation(inputs={"tool_results": []}, cost_ceiling=1)
    assert follow.propensity == field and follow.propensity_chosen == "hold"
    assert follow.handle == req.handle and follow.scoring_channel == req.scoring_channel


def test_the_reward_that_settles_a_decision_reaches_the_assemblys_own_learner():
    runtime = _learner_runtime({"hold": 0.6, "buy:BTC": 0.4})
    seed, _event = _consequence_produce(runtime)
    _register_learner(runtime, seed, ("hold", "buy:BTC"), learner="exp3")
    handle, event = _consequence_produce(runtime)
    _consequence_judge(runtime, event, "eval-a")  # settles the producer's verdict channel
    learner = runtime.assembly_learners["seed-decider"]
    before = learner.inner.distribution(("hold", "buy:BTC"))
    runtime._deliver_returns()
    assert handle not in runtime.assembly_rounds
    assert learner.inner.distribution(("hold", "buy:BTC")) != before
    marker = runtime.ledger.append({"kind": "test.marker"})
    runtime.termination.kill("test")
    items = [runtime.ledger.decrypt_item(i) for i in range(marker)]
    learned = [i for i in items if i["kind"] == "propensity.learned" and i["handle"] == handle]
    assert learned and learned[0]["action"] == "hold"
    assert learned[0]["propensity"] == pytest.approx(0.6)


def test_an_assemblys_own_learner_policy_is_disclosed_only_to_that_assembly():
    provider = Decider(propensity={"hold": 0.6, "buy:BTC": 0.4})
    runtime = _consequence_runtime(provider=provider)
    runtime._manage_reserve_window()
    seed, _event = _consequence_produce(runtime)
    assert runtime._action_policy("seed-decider") is None  # nothing registered yet
    _register_learner(runtime, seed, ("hold", "buy:BTC"), learner="exp3")
    policy = runtime._action_policy("seed-decider")
    assert set(policy["over"]) == {"hold", "buy:BTC"}
    assert sum(policy["over"].values()) == pytest.approx(1.0)
    # reading it never disturbs a round that is waiting for its reward
    handle, event = _consequence_produce(runtime)
    assert runtime.assembly_rounds[handle] == "seed-decider"
    runtime._action_policy("seed-decider")
    _consequence_judge(runtime, event, "eval-a")
    runtime._deliver_returns()
    assert handle not in runtime.assembly_rounds
    # no other assembly sees it
    assert runtime._action_policy("eval-a") is None
    assert "your_action_policy" not in provider.judge_inputs[-1]["producer"]
