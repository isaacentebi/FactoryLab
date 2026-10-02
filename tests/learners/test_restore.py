import json
from collections import deque
from random import Random

import pytest

from factorylab.learners.base import BanditFeedback, restore_learner
from factorylab.learners.blum_mansour import BlumMansour
from factorylab.learners.delayed import SnapshotLearner
from factorylab.learners.exp3 import EXP3
from factorylab.learners.router import Router


def round_trip(learner):
    saved = json.loads(json.dumps(learner.state(), allow_nan=False))
    restored = restore_learner(saved)
    assert restored.state() == learner.state()
    return restored


def _run(learner, start, stop, pending, rng, log, *, delay=7, restore_at=None):
    """Drive ``learner`` over rounds [start, stop) with delayed feedback; log results."""
    actions = learner.inner.actions
    for t in range(start, stop):
        if t == restore_at:
            learner = round_trip(learner)
        while pending and pending[0][0] <= t:
            _, handle, feedback = pending.popleft()
            log.append((handle, learner.update_for(handle, feedback)))
        p = learner.distribution_for(f"h{t}", actions, ordinal=t)
        log.append(tuple(sorted(p.items())))
        k = rng.choices(actions, weights=[p[a] for a in actions], k=1)[0]
        pending.append((t + delay, f"h{t}", BanditFeedback(k, rng.random(), p[k])))
    return learner


@pytest.mark.parametrize("core", [False, True])
def test_checkpoint_continues_across_epoch_boundary(core):
    """Checkpoint with rounds of the previous epoch (or many rounds) still in flight;
    the restored run continues bit for bit, orphans included."""
    actions = ("z", "a", "NOOP")

    def make():
        return SnapshotLearner(BlumMansour(actions, first_epoch=6, coverage=1.0) if core
                               else EXP3(actions), id="L")

    horizon = 80
    straight_log, resumed_log = [], []
    straight = _run(make(), 0, horizon, deque(), Random(5), straight_log)
    # The boundary of epoch 0 (6 rounds) passes with feedback 7 rounds late: the
    # restore at round 9 holds rounds of the closed epoch in flight.
    resumed = _run(make(), 0, horizon, deque(), Random(5), resumed_log, restore_at=9)
    assert resumed_log == straight_log
    assert resumed.state() == straight.state()
    outcomes = [x for x in straight_log if isinstance(x[1], bool)]
    assert any(ok for _handle, ok in outcomes)
    if core:
        assert ("h0", False) in outcomes  # a round of epoch 0, orphaned


def test_restore_refuses_a_state_from_another_release():
    with pytest.raises(ValueError):
        restore_learner({"algorithm": "arbitrary.module"})
    with pytest.raises(ValueError):
        restore_learner({"algorithm": "EXP3", "id": "x", "actions": ["a"], "gamma": 0.1,
                         "log_weights": {"a": 0.0}})


def test_router_restore_preserves_logged_distribution_and_rng_sample():
    def lookup(_):
        return ["z", "a"]
    learner = EXP3(("z", "a", "NOOP"))
    learner.open_round()
    learner.update(BanditFeedback("z", 0.4, .7))
    router = Router(learner, lookup)
    restored = Router.restore(json.loads(json.dumps(router.state())), lookup)

    def feasible(a):
        return a != "a", "unavailable" if a == "a" else ""
    assert restored.route("Tick", feasible, Random(13)) == router.route(
        "Tick", feasible, Random(13),
    )
