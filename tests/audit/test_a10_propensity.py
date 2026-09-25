"""A10: the deciding agent's propensity rides on the request (seat 6, finding 3).

The reproduction: `Request` had no propensity field, the `ProducerReturn`
carried `{kind, payload, outputs, cost, status}`, and the only propensity in the
world was the router's distribution over which assembly to wake. So no consumer
of any request could price a road not taken, and Blum--Mansour could only form
over executor selection.
"""

import random

import pytest

from factorylab.kernel.queue import PropensityRecord
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
        self.producer_inputs = []

    def _produce(self, desc, inputs):
        self.producer_inputs.append(inputs)
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


def test_a_declaration_that_omits_the_action_taken_is_refused_with_a_reason():
    runtime = _consequence_runtime(provider=Decider(propensity={"buy:BTC": 1.0}))
    handle, _event = _consequence_produce(runtime)
    declared = _declared(runtime, handle)
    assert declared.action_ids == ("hold",)  # degenerate, not the agent's claim
    # The reason reaches the declaring seat's own inbox under this handle, and no
    # other seat's (information audit C5).
    told = {seat: [runtime.outcomes.body(r["sha"])["outcome"] for r in records
                   if r["handle"] == handle]
            for seat, records in runtime.outcomes.items.items()}
    assert any("propensity must include the action taken (hold)" in item["reason"]
               for item in told["seed-decider"])
    assert not any(items for seat, items in told.items() if seat != "seed-decider")
    assert "return_feedback" not in runtime._world_block()


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


# --- Blum--Mansour at the assembly level --------------------------------------


def _register_learner(runtime, handle, actions, learner="blum_mansour"):
    from factorylab.cortex.registration import LearnerProposal

    runtime._register(handle, LearnerProposal("seed-decider", learner, tuple(actions), 0.1))


def _learner_runtime(propensity):
    runtime = _consequence_runtime(provider=Decider(propensity=propensity))
    runtime._manage_reserve_window()  # the novelty reserve pays for a registration
    return runtime


def test_propensity_cannot_be_added_after_the_decision_is_final():
    runtime = _consequence_runtime(provider=Decider())
    handle, event = _consequence_produce(runtime)
    _consequence_judge(runtime, event, "eval-a")
    record = PropensityRecord(("hold",), (1.0,), "hold", 0, "assembly:seed-decider", "h",
                              source="declared")
    with pytest.raises(ValueError, match="final outcome"):
        runtime.queue.record_propensity(handle, record)


class PinnedDraw(random.Random):
    """The runtime's stream, except that a draw over ``actions`` returns ``want``."""

    def __init__(self, state, actions, want):
        super().__init__()
        self.setstate(state)
        self.actions, self.want = set(actions), want

    def choices(self, population, *args, **kwargs):
        if set(population) == self.actions:
            return [self.want]
        return super().choices(population, *args, **kwargs)


def _learned(runtime, handle):
    marker = runtime.ledger.append({"kind": "test.marker"})
    runtime.termination.kill("test")
    items = [runtime.ledger.decrypt_item(i) for i in range(marker)]
    return ([i for i in items if i["kind"] == "propensity.learned" and i["handle"] == handle],
            [i for i in items if i["kind"] == "propensity.learner" and i["handle"] == handle])


def _decide_with_learner(want):
    """A second decision after a two-action EXP3 learner is registered, its draw pinned."""
    runtime = _learner_runtime({"hold": 0.6, "buy:BTC": 0.4})
    seed, _event = _consequence_produce(runtime)
    _register_learner(runtime, seed, ("hold", "buy:BTC"), learner="exp3")
    runtime.rng = PinnedDraw(runtime.rng.getstate(), ("hold", "buy:BTC"), want)
    handle, event = _consequence_produce(runtime)
    return runtime, handle, event


def test_a_seat_with_a_learner_is_shown_one_draw_never_the_distribution():
    """R4 / information P3: the kernel samples the learner and discloses {recommended, p}."""
    runtime = _learner_runtime(None)
    seed, _event = _consequence_produce(runtime)
    _register_learner(runtime, seed, ("hold", "buy:BTC"), learner="exp3")
    _consequence_produce(runtime)
    shown = runtime.provider.producer_inputs[-1]["your_action_policy"]
    assert set(shown) == {"recommended", "p", "note"}
    assert shown["recommended"] in ("hold", "buy:BTC") and 0 < shown["p"] <= 1
    assert "declare a propensity" not in shown["note"]
    assert "your_action_policy" not in runtime.provider.producer_inputs[0]  # no learner yet


def test_following_the_draw_records_the_learners_probability():
    """A seat that took its learner's recommendation is recorded at the learner's p."""
    runtime, handle, event = _decide_with_learner("hold")
    declared = _declared(runtime, handle)
    policy = dict(zip(declared.action_ids, declared.probs, strict=True))
    assert declared.chosen == "hold" and set(policy) == {"hold", "buy:BTC"}
    assert policy["hold"] != pytest.approx(0.6)  # the seat's own 0.6 is not what stands
    _consequence_judge(runtime, event, "eval-a")
    runtime._deliver_returns()
    assert handle not in runtime.assembly_rounds
    learned, learner = _learned(runtime, handle)
    assert learner and learner[0]["p"] == pytest.approx(policy["hold"])
    assert learned and learned[0]["action"] == "hold"
    assert learned[0]["propensity"] == pytest.approx(policy["hold"])


def test_the_reward_that_settles_a_decision_reaches_the_assemblys_own_learner():
    """A seat that departed from its learner's draw keeps its own declaration."""
    runtime, handle, event = _decide_with_learner("buy:BTC")
    _consequence_judge(runtime, event, "eval-a")  # settles the producer's verdict channel
    learner = runtime.assembly_learners["seed-decider"]
    before = learner.inner.distribution(("hold", "buy:BTC"))
    runtime._deliver_returns()
    assert handle not in runtime.assembly_rounds
    assert learner.inner.distribution(("hold", "buy:BTC")) != before
    learned, recommended = _learned(runtime, handle)
    assert not recommended
    assert learned and learned[0]["action"] == "hold"
    assert learned[0]["propensity"] == pytest.approx(0.6)


def test_a_draw_from_a_policy_that_has_since_moved_is_not_recorded_as_the_learners():
    """The learner's p stands only while it is the p the seat was shown."""
    from types import SimpleNamespace

    runtime = _learner_runtime(None)
    seed, _event = _consequence_produce(runtime)
    _register_learner(runtime, seed, ("hold", "buy:BTC"), learner="exp3")
    stale = SimpleNamespace(handle=seed, inputs={
        "your_action_policy": {"recommended": "hold", "p": 0.123456}})
    assert runtime._followed_recommendation("seed-decider", stale, ("hold",), "h") is None
    shown = runtime._action_policy("seed-decider")
    fresh = SimpleNamespace(handle=seed, inputs={"your_action_policy": shown})
    record = runtime._followed_recommendation("seed-decider", fresh, (shown["recommended"],),
                                              "h")
    assert record is not None and record.chosen == shown["recommended"]
    other = "buy:BTC" if shown["recommended"] == "hold" else "hold"
    assert runtime._followed_recommendation("seed-decider", fresh, (other,), "h") is None
