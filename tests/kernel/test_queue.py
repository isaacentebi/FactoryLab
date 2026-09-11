from dataclasses import FrozenInstanceError, fields, replace

import pytest

from factorylab.kernel.queue import LearningReturn, PropensityRecord, SettleStatus


def settle(queue, handle, **kwargs):
    arguments = dict(
        channel="outcome",
        score=0.5,
        status="settled",
        definition_version="score-v1",
        sampling_ref="sample-1",
    )
    arguments.update(kwargs)
    queue.settle(handle, **arguments)


def test_invariant_4_open_requires_propensity_and_declared_channel(open_decision):
    with pytest.raises(TypeError):
        open_decision(propensity=None)
    with pytest.raises(ValueError):
        open_decision(channel="")
    with pytest.raises(ValueError):
        open_decision(channel="timeout")
    with pytest.raises(ValueError):
        open_decision(parent_handle="nonexistent")


@pytest.mark.parametrize(
    "change",
    [
        {"probs": (0.5, 0.1)},
        {"probs": (float("nan"), 0.5)},
        {"probs": (-0.1, 1.1)},
        {"probs": (0.0, 1.0)},
        {"chosen": "unknown"},
        {"chosen": "NOOP"},
        {"action_ids": ("new", "new")},
        {"action_ids": ()},
        {"learner_state_hash": ""},
    ],
)
def test_invariant_4_rejects_false_sampling_records(change, propensity_factory):
    with pytest.raises(ValueError):
        replace(propensity_factory(), **change)


def test_invariant_4_actor_must_match_sampling_learner(open_decision, propensity_factory):
    with pytest.raises(ValueError):
        open_decision(propensity=propensity_factory(actor="different"))


def test_persistent_parent_and_thin_immutable_returns(queue, open_decision, clock):
    parent = open_decision()
    child = open_decision(parent_handle=parent)
    assert parent != child
    assert [item.handle for item in queue.outstanding("learner")] == [parent, child]
    assert queue.get(child).parent_handle == parent
    clock.now += 5
    settle(queue, child)
    assert queue.outstanding()[0].handle == parent
    result = queue.returns_for("learner")[0]
    assert {field.name for field in fields(result)} == {
        "handle",
        "channel",
        "score",
        "definition_version",
        "status",
        "sampling_ref",
    }
    assert result.handle == child
    with pytest.raises(FrozenInstanceError):
        result.score = 100
    with pytest.raises(FrozenInstanceError):
        queue.get(parent).actor = "changed"
    with pytest.raises(ValueError):
        settle(queue, child)


def test_invariant_4_retired_actor_receives_compatible_successor_return(
    queue,
    open_decision,
    clock,
):
    handle = open_decision()
    compat = {"outcome": "reward-v2"}
    queue.register_successor("learner", "successor", compat)
    compat["outcome"] = "tampered"
    clock.now += 1_000
    settle(queue, handle)
    assert queue.returns_for("learner") == ()
    assert queue.returns_for("successor") == (
        LearningReturn(handle, "reward-v2", 0.5, "score-v1", SettleStatus.SETTLED, "sample-1"),
    )
    assert queue.history(handle)[0].channel == "outcome"
    assert queue.get(handle).actor == "learner"
    assert queue.get(handle).propensity.learner_id == "learner"
    with pytest.raises(ValueError):
        open_decision(actor="learner")


@pytest.mark.parametrize("mapped", [False, True])
def test_invariant_4_retired_unmapped_settlement_is_historical_never_dropped(
    mapped,
    queue,
    open_decision,
    clock,
):
    handle = open_decision()
    if mapped:
        queue.register_successor("learner", "successor", {"different": "reward"})
    else:
        queue.retire_actor("learner")
    clock.now += 10_000
    settle(queue, handle)
    assert queue.get(handle).status == SettleStatus.HISTORICAL
    assert queue.history(handle)[0].handle == handle
    assert queue.history(handle)[0].score == 0.5
    assert queue.history(handle)[0].status == SettleStatus.HISTORICAL
    assert queue.returns_for("successor") == queue.returns_for("learner") == ()
    assert queue.has_history("new")


def test_successor_chain_and_cycle_rejection(queue, open_decision):
    handle = open_decision()
    queue.register_successor("learner", "next", {"outcome": "next-outcome"})
    queue.register_successor("next", "last", {"next-outcome": "last-outcome"})
    with pytest.raises(ValueError, match="cycle"):
        queue.register_successor("last", "learner", {"last-outcome": "outcome"})
    with pytest.raises(ValueError, match="immutable"):
        queue.register_successor("learner", "other", {})
    settle(queue, handle)
    assert queue.returns_for("last")[0].channel == "last-outcome"


def test_timeout_has_no_manufactured_outcome_and_late_settlement_still_arrives(
    queue,
    open_decision,
    clock,
):
    handle = open_decision()
    assert queue.expire(109) == []
    assert queue.expire(110) == [handle]
    assert queue.expire(111) == []
    assert queue.outstanding() == []
    assert queue.get(handle).status == SettleStatus.TIMED_OUT
    assert queue.history(handle) == (
        LearningReturn(handle, "timeout", 0.0, "timeout-v1", SettleStatus.TIMED_OUT, None),
    )
    assert not queue.has_history("new")
    clock.now = 500
    settle(queue, handle, score=0.8)
    returns = queue.returns_for("learner")
    assert [(item.channel, item.score) for item in returns] == [("timeout", 0.0), ("outcome", 0.8)]
    assert queue.get(handle).status == SettleStatus.SETTLED
    assert queue.has_history("new")


def test_outcome_channel_and_status_cannot_be_rewritten(queue, open_decision):
    handle = open_decision()
    for arguments in (
        {"channel": "new"},
        {"status": "pending"},
        {"status": "timed_out"},
        {"status": "historical"},
        {"score": float("inf")},
    ):
        with pytest.raises(ValueError):
            settle(queue, handle, **arguments)
    assert queue.get(handle).status == SettleStatus.PENDING


@pytest.mark.parametrize("status", [SettleStatus.CENSORED, SettleStatus.INAPPLICABLE])
def test_missing_outcomes_are_distinct_from_settled_performance(status, queue, open_decision):
    handle = open_decision()
    settle(queue, handle, status=status)
    assert queue.history(handle)[0].status == status
    assert not queue.has_history("new")


def test_propensity_sequence_inputs_are_detached():
    actions, probs = ["only"], [1.0]
    record = PropensityRecord(actions, probs, "only", 1, "learner", "state-hash")
    probs[0] = 0
    actions[0] = "changed"
    assert record.action_ids == ("only",) and record.probs == (1.0,)


@pytest.mark.parametrize("status", ["censored", "inapplicable"])
def test_retirement_does_not_turn_missingness_into_performance(status, queue, open_decision):
    handle = open_decision()
    queue.retire_actor("learner")
    settle(queue, handle, status=status)
    assert queue.history(handle)[0].status == SettleStatus.HISTORICAL
    assert not queue.has_history("new")
