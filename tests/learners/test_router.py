import hashlib
import math
from collections import Counter
from random import Random

import pytest

from factorylab.learners.base import FullInfoFeedback
from factorylab.learners.blum_mansour import BlumMansour
from factorylab.learners.hedge import Hedge
from factorylab.learners.router import Router


def test_twenty_thousand_draws_match_logged_probs_and_replay_exactly():
    learner = Hedge(("a", "b", "NOOP"), 1.5)
    learner.update(FullInfoFeedback({"a": 0, "b": 0.5, "NOOP": 1}))
    router = Router(learner, lambda _: ["a", "b"])
    rng = Random(0)
    counts = Counter()
    seeds = set()
    for _ in range(20_000):
        sample = router.route("Tick", lambda _: (True, ""), rng)
        assert sample.chosen == Random(sample.rng_seed).choices(
            sample.action_ids, weights=sample.probs, k=1
        )[0]
        assert sample.learner_state_hash == hashlib.sha256(learner.state()).hexdigest()
        assert sample.learner_id == learner.id
        counts[sample.chosen] += 1
        seeds.add(sample.rng_seed)
    assert len(seeds) == 20_000
    for action, p in zip(sample.action_ids, sample.probs, strict=True):
        # Six binomial standard deviations give a conservative deterministic regression bound.
        assert abs(counts[action] - 20_000 * p) < 6 * math.sqrt(20_000 * p * (1 - p))


@pytest.mark.parametrize("use_reduction", [False, True])
def test_exclusion_changes_sampled_support_and_logs_only_feasible_probabilities(use_reduction):
    actions = ("a", "b", "NOOP")
    learner = BlumMansour(lambda a: Hedge(a, 1), actions) if use_reduction else Hedge(actions, 1)
    router = Router(learner, lambda _: ["a", "b"])
    rng = Random(0)
    first = router.route("Tick", lambda _: (True, ""), rng)
    assert first.action_ids == actions
    learner.update(FullInfoFeedback({"a": 0, "b": 0, "NOOP": 0}))
    seen = set()
    for _ in range(200):
        sample = router.route("Tick", lambda a: (a != "b", "insufficient wallet"), rng)
        assert sample.action_ids == ("a", "NOOP")
        assert sample.probs == pytest.approx((0.5, 0.5))
        assert math.fsum(sample.probs) == pytest.approx(1)
        assert sample.excluded == (("b", "insufficient wallet"),)
        seen.add(sample.chosen)
    assert seen == {"a", "NOOP"}


def test_noop_always_last_unique_and_bypasses_feasibility_even_when_all_excluded():
    calls = []

    def infeasible(action):
        calls.append(action)
        assert action != "NOOP"
        return False, "unavailable"

    router = Router(Hedge(("a", "NOOP"), 0.1), lambda _: ["NOOP", "a", "a", "NOOP"])
    sample = router.route("Tick", infeasible, Random(0))
    assert calls == ["a"]
    assert sample.action_ids == ("NOOP",)
    assert sample.probs == (1,)
    assert sample.chosen == "NOOP"
    assert sample.excluded == (("a", "unavailable"),)
    empty = Router(Hedge(("NOOP",), 0.1), lambda _: [])
    assert empty.route("unknown", infeasible, Random(0)).chosen == "NOOP"


@pytest.mark.parametrize("bad", [
    {"a": 0.5}, {"a": 0.5, "NOOP": 0.4}, {"a": -0.1, "NOOP": 1.1},
    {"a": math.nan, "NOOP": 1}, {"a": 0.5, "NOOP": 0.5, "excluded": 0},
])
def test_router_rejects_invalid_distribution_without_drawing(bad):
    class InvalidLearner(Hedge):
        def distribution(self, feasible):
            return bad

    rng = Random(0)
    before = rng.getstate()
    router = Router(InvalidLearner(("a", "NOOP"), 0.1), lambda _: ["a"])
    with pytest.raises(ValueError):
        router.route("Tick", lambda _: (True, ""), rng)
    assert rng.getstate() == before


def test_seeded_routes_reproduce_the_complete_sample():
    def sample():
        router = Router(Hedge(("a", "NOOP"), 0.1), lambda event: ["a"] if event == "Tick" else [])
        return router.route("Tick", lambda _: (True, ""), Random(0))

    assert sample() == sample()
