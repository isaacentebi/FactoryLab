"""Round three, group B: the action a decision is learned under is the action it took
(triage row T15). Nothing here touches a network."""


import pytest

from factorylab.learners.base import BanditFeedback
from factorylab.learners.exp3 import EXP3
from factorylab.runtime.propensity import (
    MIN_DECLARED_MASS,
    declared_record,
)


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
