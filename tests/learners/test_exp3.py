import json
import math

import pytest

from factorylab.learners.base import BanditFeedback, FullInfoFeedback
from factorylab.learners.exp3 import EXP3


def test_logged_propensity_not_recomputed_and_exact_exploration():
    learner = EXP3(("a", "b", "c"), gamma=0.3)
    learner.distribution(("a", "b", "c"))
    # The logged 0.1 intentionally differs from the learner's current 1/3.
    learner.update(BanditFeedback("b", 0.8, 0.1))
    dist = learner.distribution(("a", "b", "c"))
    weight = math.exp(0.3 / 3 * 0.8 / 0.1)
    assert dist["b"] == pytest.approx(0.7 * weight / (2 + weight) + 0.1)
    assert dist["a"] == pytest.approx(0.7 / (2 + weight) + 0.1)
    assert all(p >= 0.1 for p in dist.values())
    filtered = learner.distribution(("c", "b"))
    assert filtered["c"] == pytest.approx(0.7 / (1 + weight) + 0.15)
    assert math.fsum(filtered.values()) == pytest.approx(1)


def test_delayed_feedback_does_not_use_latest_distribution():
    learner = EXP3(("a", "b"), 0.2)
    learner.update(BanditFeedback("a", 1, 0.5))
    learner.distribution(("a",))
    learner.update(BanditFeedback("a", 1, 0.25))
    logs = json.loads(learner.state())["log_weights"]
    assert logs["a"] - logs["b"] == pytest.approx(0.6)


@pytest.mark.parametrize("gamma", [0, -1, 1.1, math.nan, math.inf])
def test_invalid_gamma(gamma):
    with pytest.raises(ValueError):
        EXP3(("a", "b"), gamma)


def test_gamma_one_zero_reward_and_state():
    learner = EXP3(("a", "b"), 1)
    before = learner.state()
    learner.update(BanditFeedback("a", 0, 0.01))
    assert before == learner.state() == EXP3(("a", "b"), 1).state()
    learner.update(BanditFeedback("a", 1, 0.01))
    assert learner.distribution(("a", "b")) == {"a": 0.5, "b": 0.5}
    with pytest.raises(TypeError):
        learner.update(FullInfoFeedback({"a": 0, "b": 1}))
    with pytest.raises(ValueError):
        learner.update(BanditFeedback("x", 1, 0.5))


def test_nonfinite_update_is_rejected_without_state_corruption():
    learner = EXP3(("a", "b"), 1)
    before = learner.state()
    with pytest.raises(ValueError):
        learner.update(BanditFeedback("a", 1, 5e-324))
    assert learner.state() == before
