import math

import pytest

from factorylab.learners.base import BanditFeedback, FullInfoFeedback
from factorylab.learners.hedge import Hedge


def test_original_multiplicative_update_and_conditioning():
    learner = Hedge(("a", "b", "c"), eta=0.7)
    learner.update(FullInfoFeedback({"a": 1, "b": 0.5, "c": 0}))
    dist = learner.distribution(("a", "b", "c"))
    assert dist["a"] / dist["c"] == pytest.approx(math.exp(-0.7))
    assert dist["b"] / dist["c"] == pytest.approx(math.exp(-0.35))
    filtered = learner.distribution(("b", "a"))
    assert tuple(filtered) == ("b", "a")
    assert filtered == pytest.approx({a: dist[a] / (dist["a"] + dist["b"]) for a in ("b", "a")})
    assert learner.distribution(("a", "b", "c")) == dist


def test_log_weights_survive_underflow_and_recovery():
    learner = Hedge(("a", "b"), 1)
    for _ in range(1000):
        learner.update(FullInfoFeedback({"a": 1, "b": 0}))
    assert learner.distribution(("a",)) == {"a": 1}
    for _ in range(1000):
        learner.update(FullInfoFeedback({"a": 0, "b": 1}))
    assert learner.distribution(("a", "b")) == {"a": 0.5, "b": 0.5}


@pytest.mark.parametrize("actions", [(), ("a", "a"), ("",)])
def test_bad_universe(actions):
    with pytest.raises(ValueError):
        Hedge(actions, 0.1)


@pytest.mark.parametrize("eta", [0, -1, math.nan, math.inf])
def test_bad_rate(eta):
    with pytest.raises(ValueError):
        Hedge(("a", "b"), eta)


@pytest.mark.parametrize("support", [(), ("x",), ("a", "a")])
def test_bad_support(support):
    with pytest.raises(ValueError):
        Hedge(("a", "b"), 0.1).distribution(support)


def test_rejection_does_not_change_weights_and_state_is_deterministic():
    learner = Hedge(("a", "b"), 0.1)
    before = learner.state()
    assert before == Hedge(("a", "b"), 0.1).state()
    with pytest.raises(TypeError):
        learner.update(BanditFeedback("a", 1, 0.5))
    with pytest.raises(ValueError):
        learner.update(FullInfoFeedback({"a": 1}))
    feedback = FullInfoFeedback({"a": 0, "b": 1})
    feedback.losses["a"] = -1
    with pytest.raises(AssertionError):
        learner.update(feedback)
    assert learner.state() == before
