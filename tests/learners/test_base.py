import math

import pytest

from factorylab.learners.base import BanditFeedback, FullInfoFeedback, Learner
from factorylab.learners.exp3 import EXP3
from factorylab.learners.hedge import Hedge


@pytest.mark.parametrize("value", [-0.01, 1.01, math.nan, math.inf, -math.inf])
def test_unbounded_feedback_fails(value):
    with pytest.raises(AssertionError):
        FullInfoFeedback({"a": value})
    with pytest.raises(AssertionError):
        BanditFeedback("a", value, 0.5)


@pytest.mark.parametrize("propensity", [0, -1, 1.01, math.nan, math.inf])
def test_invalid_propensities_fail(propensity):
    with pytest.raises(AssertionError):
        BanditFeedback("a", 0.5, propensity)


def test_endpoints_and_defensive_feedback_copy():
    losses = {"a": 0.0, "b": 1.0}
    feedback = FullInfoFeedback(losses)
    losses["a"] = -1
    assert feedback.losses == {"a": 0.0, "b": 1.0}
    assert BanditFeedback("a", 0, 1).reward == 0
    assert BanditFeedback("a", 1, 0.01).reward == 1
    assert isinstance(Hedge(("a", "b"), 0.1), Learner)
    assert isinstance(EXP3(("a", "b"), 0.1), Learner)
