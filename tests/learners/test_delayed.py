import pytest

from factorylab.learners.base import BanditFeedback, state_bytes
from factorylab.learners.blum_mansour import BlumMansour
from factorylab.learners.delayed import SnapshotLearner
from factorylab.learners.exp3 import EXP3
from factorylab.learners.hedge import Hedge


def _learner(core: bool, actions=("a", "b", "NOOP")) -> SnapshotLearner:
    return SnapshotLearner(BlumMansour(actions) if core else EXP3(actions), id="L")


@pytest.mark.parametrize("core", [False, True])
def test_handle_lifecycle_and_one_use_handles(core):
    learner = _learner(core)
    actions = learner.inner.actions
    with pytest.raises(KeyError):
        learner.update_for("missing", BanditFeedback("a", 1, 0.5))
    with pytest.raises(TypeError, match="update_for"):
        learner.update(BanditFeedback("a", 1, 0.5))
    p = learner.distribution_for("r", actions, ordinal=0)
    with pytest.raises(KeyError):
        learner.distribution_for("r", actions, ordinal=0)
    with pytest.raises(KeyError):  # outstanding: no later ordinal reopens it either
        learner.distribution_for("r", actions, ordinal=1)
    learner.distribution_for("s", ("a", "NOOP"), ordinal=1)
    assert learner.outstanding() == ["r", "s"]
    assert learner.update_for("r", BanditFeedback("a", 0.5, p["a"])) is True
    with pytest.raises(KeyError):
        learner.update_for("r", BanditFeedback("a", 0.5, p["a"]))
    with pytest.raises(KeyError):
        learner.distribution_for("r", actions, ordinal=0)
    learner.discard_for("s")
    with pytest.raises(KeyError):
        learner.distribution_for("s", actions, ordinal=1)
    assert learner.outstanding() == []


@pytest.mark.parametrize("core", [False, True])
def test_invalid_feedback_keeps_the_snapshot_for_retry(core):
    learner = _learner(core)
    p = learner.distribution_for("r", learner.inner.actions, ordinal=0)
    before = learner.state()
    with pytest.raises(ValueError):
        learner.update_for("r", BanditFeedback("a", 1, p["a"] / 2))
    assert learner.state() == before
    assert learner.update_for("r", BanditFeedback("a", 1, p["a"]))


@pytest.mark.parametrize("core", [False, True])
def test_executed_policy_is_frozen_and_must_be_carried(core):
    learner = SnapshotLearner(BlumMansour(("a", "b"), coverage=2.0) if core
                              else EXP3(("a", "b")))
    learner.distribution_for("r", ("a", "b"), ordinal=0)
    learner.record_executed("r", {"a": 0.75, "b": 0.25})
    with pytest.raises(ValueError):
        learner.update_for("r", BanditFeedback("b", 1, 0.5))
    assert learner.update_for("r", BanditFeedback("b", 1, 0.25))


def test_withdrawn_draw_does_not_advance_t():
    for core in (False, True):
        learner = _learner(core)
        before = learner.inner.state()
        learner.distribution_for("quiet", learner.inner.actions, ordinal=0)
        learner.withdraw_for("quiet")
        assert learner.inner.state() == before
        with pytest.raises(KeyError):  # the handle stays spent
            learner.distribution_for("quiet", learner.inner.actions, ordinal=0)


def test_a_withdrawn_quiet_draw_at_an_epoch_boundary_rolls_nothing_over():
    """Sol on #189/#190, P1 4: with H0 = 3, three rounds outstanding, a quiet draw that
    rolled the epoch and was withdrawn left the position at (1, 0), and the earlier
    rounds' feedback was orphaned. A withdrawal undoes the draw: (0, 3), and they train."""
    learner = SnapshotLearner(BlumMansour(("a", "NOOP"), first_epoch=3), id="core")
    drawn = {h: learner.distribution_for(h, ("a", "NOOP"), ordinal=i)
             for i, h in enumerate(("r0", "r1", "r2"))}
    before = learner.inner.state()
    learner.distribution_for("quiet", ("NOOP",), ordinal=3)
    learner.withdraw_for("quiet")
    assert (learner.inner.epoch, learner.inner.epoch_rounds) == (0, 3)
    assert learner.inner.state() == before
    for handle, p in drawn.items():
        assert learner.update_for(handle, BanditFeedback("a", 0.8, p["a"])) is True
    learner.distribution_for("next", ("a", "NOOP"), ordinal=4)
    assert learner.inner.epoch == 1  # the next real draw rolls the epoch as before


def test_discarded_round_still_counts_as_opened():
    learner = _learner(False)
    learner.distribution_for("r", learner.inner.actions, ordinal=0)
    learner.discard_for("r")
    assert learner.inner.rounds == 1


def test_core_snapshot_is_order_n_and_trains_identically():
    """A core snapshot holds its epoch, p and pi (O(N)), and a delayed update through
    it leaves exactly the rows a synchronous update leaves."""
    actions = tuple(f"s{i}" for i in range(6))
    learner = SnapshotLearner(BlumMansour(actions), id="core")
    direct = BlumMansour(actions)
    p = learner.distribution_for("r", actions, ordinal=0)
    q, saved = direct.open_round(actions)
    assert p == q
    snapshot = learner.state()["snapshots"]["r"]
    assert set(snapshot) == {"epoch", "support", "p", "gamma", "executed"}
    assert len(snapshot["p"]) == len(actions)
    feedback = BanditFeedback("s3", 0.7, p["s3"])
    learner.update_for("r", feedback)
    direct.update_round(saved, feedback)
    assert learner.inner.state() == direct.state()


def test_completed_rounds_leave_bounded_state():
    """Two cohorts of a thousand rounds, each opened and closed, leave the learner's
    state the same size (essay II.II.b, "memory"): it keeps the outstanding rounds and
    the issuance mark, never a tombstone per round."""
    actions = ("a", "b", "NOOP")
    for core in (False, True):
        learner = _learner(core, actions)
        sizes = []
        for cohort in range(2):
            for n in range(1000 * cohort, 1000 * (cohort + 1)):
                learner.distribution_for(f"r:{n}", actions, ordinal=n)
                learner.discard_for(f"r:{n}")
            sizes.append(len(state_bytes(learner.state())))
        # Only the mark's digits and the counters grow: logarithmic, not linear.
        assert sizes[1] - sizes[0] <= 8
        restored = SnapshotLearner.restore(learner.state())
        for spent in (learner, restored):
            for n in (0, 999, 1999):
                with pytest.raises(KeyError):
                    spent.distribution_for(f"r:{n}", actions, ordinal=n)
        restored.distribution_for("r:2000", actions, ordinal=2000)


def test_state_bounded_by_outstanding():
    """Live snapshots are exactly the open rounds; each is O(N) for both classes."""
    actions = ("a", "b", "c", "NOOP")
    for core in (False, True):
        learner = _learner(core, actions)
        for n in range(500):
            p = learner.distribution_for(f"r:{n}", actions, ordinal=n)
            if n >= 20:  # a delay of twenty rounds
                old = f"r:{n - 20}"
                k = "a"
                prop = learner.state()["snapshots"][old]
                prop = (dict(prop["executed"]) if not core else dict(prop["p"]))[k]
                learner.update_for(old, BanditFeedback(k, 0.5, prop))
            del p
        state = learner.state()
        assert len(state["snapshots"]) == 20
        per = max(len(state_bytes(s)) for s in state["snapshots"].values())
        assert per < 400


def test_snapshot_learner_wraps_only_the_two_classes():
    with pytest.raises(TypeError):
        SnapshotLearner(Hedge(("a", "b"), 0.1))
