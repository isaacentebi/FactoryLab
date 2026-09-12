import json
import math
from random import Random

import pytest

from factorylab.learners.base import BanditFeedback, FullInfoFeedback, restore_learner
from factorylab.learners.blum_mansour import BlumMansour
from factorylab.learners.delayed import SnapshotLearner
from factorylab.learners.exp3 import EXP3
from factorylab.learners.hedge import Hedge
from factorylab.learners.router import Router


def round_trip(learner):
    saved = json.loads(json.dumps(learner.state(), allow_nan=False))
    restored = restore_learner(saved)
    assert restored.state() == learner.state()
    return restored


@pytest.mark.parametrize("kind", ["hedge", "exp3", "bm_full", "bm_bandit"])
def test_restore_preserves_exact_queries_pending_rounds_and_future_updates(kind):
    actions = ("z", "a", "NOOP")
    bandit = kind in ("exp3", "bm_bandit")
    if kind == "hedge":
        learner = Hedge(actions, math.nextafter(0.2, 1.0), id="original")
    elif kind == "exp3":
        learner = EXP3(actions, math.nextafter(0.2, 1.0), id="original")
    else:
        learner = BlumMansour(
            lambda a: EXP3(a, 0.2) if bandit else Hedge(a, 0.2), actions, id="original",
        )
    rng = Random(3)
    for i in range(20):
        support = actions if i % 2 else ("NOOP", "z")
        p = learner.distribution(support)
        restored = round_trip(learner)
        assert restored.distribution(support) == p
        a = rng.choices(list(p), weights=list(p.values()))[0]
        feedback = (BanditFeedback(a, rng.random(), p[a]) if bandit else
                    FullInfoFeedback({a: rng.random() for a in actions}))
        learner.update(feedback)
        restored.update(feedback)
        assert restored.state() == learner.state()
    # The returned snapshot owns no mutable weight dictionary in the live learner.
    saved = learner.state()
    saved["id"] = "edited"
    assert learner.id == "original"


def test_expanded_exp3_round_trip_keeps_carried_weights():
    learner = EXP3(("b", "a"), 0.13)
    learner.update(BanditFeedback("b", 0.12345678901234568, 0.51))
    learner = learner.expand(("b", "a", "new", "NOOP"))
    restored = round_trip(learner)
    assert restored.distribution(learner.actions) == learner.distribution(learner.actions)


@pytest.mark.parametrize("bandit", [False, True])
def test_snapshot_restore_rebinds_old_rounds_and_preserves_spent_handles(bandit):
    actions = ("z", "a", "b")
    inner = BlumMansour(lambda a: EXP3(a, .3) if bandit else Hedge(a, .3), actions)
    learner = SnapshotLearner(inner, id="delayed-id")
    policies = {h: learner.distribution_for(h, menu)
                for h, menu in (("spent", actions), ("older", ("b", "z")), ("newer", actions))}

    def feedback(handle):
        a = next(iter(policies[handle]))
        return (BanditFeedback(a, 0.71, policies[handle][a]) if bandit else
                FullInfoFeedback(dict(zip(actions, (.1, .8, .3), strict=True))))

    learner.update_for("spent", feedback("spent"))
    restored = round_trip(learner)
    for handle in ("newer", "older"):
        learner.update_for(handle, feedback(handle))
        restored.update_for(handle, feedback(handle))
        assert learner.state() == restored.state()
    restored = round_trip(restored)
    assert restored.id == "delayed-id" and not restored.state()["snapshots"]
    for handle in policies:
        with pytest.raises(KeyError):
            restored.distribution_for(handle, actions)
        with pytest.raises(KeyError):
            restored.update_for(handle, feedback(handle))


def test_router_restore_preserves_logged_distribution_and_rng_sample():
    def lookup(_):
        return ["z", "a"]
    learner = EXP3(("z", "a", "NOOP"), 0.23)
    learner.update(BanditFeedback("z", 0.4, .7))
    router = Router(learner, lookup)
    restored = Router.restore(json.loads(json.dumps(router.state())), lookup)
    def feasible(a):
        return a != "a", "unavailable" if a == "a" else ""
    assert restored.route("Tick", feasible, Random(13)) == router.route(
        "Tick", feasible, Random(13),
    )


def test_restore_rejects_unknown_algorithm_and_nonfinite_weights():
    with pytest.raises(ValueError):
        restore_learner({"algorithm": "arbitrary.module"})
    state = EXP3(("a",), .1).state()
    state["log_weights"]["a"] = float("inf")
    with pytest.raises(ValueError):
        EXP3.restore(state)
