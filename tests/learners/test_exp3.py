import math

import pytest

from factorylab.learners.base import BanditFeedback, FullInfoFeedback
from factorylab.learners.exp3 import EXP3, exploration


def test_schedule_is_t_to_the_minus_half_without_restart():
    assert [exploration(t) for t in (1, 4, 16, 10_000)] == [1.0, 0.5, 0.25, 0.01]
    learner = EXP3(("a", "b", "NOOP"))
    for t in range(1, 50):
        gamma, eta = learner.rates()
        assert gamma == min(1.0, 1 / math.sqrt(t)) and eta == gamma / 3
        assert learner.open_round() == {"gamma": gamma, "eta": eta}
    assert learner.rounds == 49
    learner.withdraw_round()
    assert learner.rounds == 48
    with pytest.raises(ValueError):
        exploration(0)


def test_distribution_is_exploration_mixed_softmax_of_cumulative_losses():
    learner = EXP3(("a", "b", "c"))
    for _ in range(3):
        learner.open_round()
    learner.update(BanditFeedback("b", 0.2, 0.25))  # loss .8 / .25 = 3.2
    gamma, eta = 0.5, 0.5 / 3
    dist = learner.distribution(("a", "b", "c"))
    w = {"a": 1.0, "b": math.exp(-eta * 3.2), "c": 1.0}
    total = sum(w.values())
    for a in w:
        assert dist[a] == pytest.approx((1 - gamma) * w[a] / total + gamma / 3)
    # A feasible subset conditions the same losses and spreads exploration over it.
    sub = learner.distribution(("c", "b"))
    assert sub["c"] == pytest.approx((1 - gamma) / (1 + w["b"]) + gamma / 2)
    # Querying never counts a round.
    assert learner.rounds == 3


def test_truthful_rare_propensity_is_learned_unfloored():
    """Audit s06 #4: the declared .01 enters the estimate as .01, never a floor."""
    learner = EXP3(("hold", "order"))
    learner.update(BanditFeedback("hold", 0.0, 0.01))
    assert learner.state()["losses"] == {"hold": 100.0, "order": 0.0}


def test_off_policy_estimate_is_ix_with_the_rounds_eta():
    learner = EXP3(("hold", "order"), off_policy=True)
    rates = learner.open_round()
    with pytest.raises(ValueError, match="eta"):
        learner.update(BanditFeedback("hold", 0.0, 1e-6))
    learner.update(BanditFeedback("hold", 0.0, 1e-6), eta=rates["eta"])
    loss = learner.state()["losses"]["hold"]
    assert loss == pytest.approx(1 / (1e-6 + rates["eta"] / 2))
    # One update moves a logit by at most eta / beta = 2.
    assert rates["eta"] * loss <= 2


def test_nonfinite_or_foreign_update_is_rejected_without_state_change():
    learner = EXP3(("a", "b"))
    before = learner.state()
    with pytest.raises(ValueError):
        learner.update(BanditFeedback("a", 0, 5e-324))
    with pytest.raises(ValueError):
        learner.update(BanditFeedback("x", 0, 0.5))
    with pytest.raises(TypeError):
        learner.update(FullInfoFeedback({"a": 0, "b": 1}))
    assert learner.state() == before
    with pytest.raises(RuntimeError):
        learner.withdraw_round()


def test_restore_is_exact_and_refuses_another_schedule():
    learner = EXP3(("z", "a"), id="f")
    learner.open_round()
    learner.update(BanditFeedback("z", 0.123456789, 0.51))
    restored = EXP3.restore(learner.state())
    assert restored.state() == learner.state()
    assert restored.distribution(("z", "a")) == learner.distribution(("z", "a"))
    old = {"algorithm": "EXP3", "id": "f", "actions": ["z", "a"], "gamma": 0.1,
           "log_weights": {"z": 0.0, "a": 0.0}}
    with pytest.raises(ValueError):
        EXP3.restore(old)
    bad = learner.state()
    bad["losses"]["a"] = math.inf
    with pytest.raises(ValueError):
        EXP3.restore(bad)
