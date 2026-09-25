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
def test_any_settled_delivery_is_a_reward_trail(status, queue, open_decision):
    """Wave 16, ruling R10-b: a decline, abstention or censored decision is credited at
    its published price, a reward trail (essay II.II.b: unhistoried actions carry "no
    reward trail"). Its status stays distinct from a score; a timeout alone leaves
    no trail (``test_timeout_has_no_manufactured_outcome...``)."""
    handle = open_decision()
    assert not queue.has_history("new")
    settle(queue, handle, status=status)
    assert queue.history(handle)[0].status == status
    assert queue.has_history("new")


def test_propensity_sequence_inputs_are_detached():
    actions, probs = ["only"], [1.0]
    record = PropensityRecord(actions, probs, "only", 1, "learner", "state-hash")
    probs[0] = 0
    actions[0] = "changed"
    assert record.action_ids == ("only",) and record.probs == (1.0,)


@pytest.mark.parametrize("status", ["censored", "inapplicable"])
def test_retirement_keeps_the_reward_trail_of_an_unscored_settlement(status, queue,
                                                                     open_decision):
    handle = open_decision()
    queue.retire_actor("learner")
    settle(queue, handle, status=status)
    assert queue.history(handle)[0].status == SettleStatus.HISTORICAL
    assert queue.has_history("new")  # ruling R10-b: a settled delivery, whatever it said


def test_returns_for_matches_a_scan_of_every_delivery_the_queue_made(
    queue, open_decision, propensity_factory, clock
):
    """The per-actor index answers exactly what a scan over every return would.

    Ordering and content are pinned twice: against the literal delivery stream
    this script produces, and against a filter over every return the queue holds.
    """
    inherited = open_decision(actor="learner", propensity=propensity_factory(actor="learner"))
    queue.register_successor("learner", "heir", {"outcome": "heir-outcome"})
    settle(queue, inherited, score=0.25)
    own = open_decision(actor="heir", propensity=propensity_factory(actor="heir"))
    other = open_decision(actor="other", propensity=propensity_factory(actor="other"))
    settle(queue, other, score=0.75)
    clock.now += 100
    assert queue.expire(clock.now) == [own]
    settle(queue, own, score=0.5)

    assert [(r.handle, r.channel, r.score, r.status) for r in queue.returns_for("heir")] == [
        (inherited, "heir-outcome", 0.25, SettleStatus.SETTLED),
        (own, "timeout", 0.0, SettleStatus.TIMED_OUT),
        (own, "outcome", 0.5, SettleStatus.SETTLED),
    ]
    assert queue.returns_for("learner") == () and queue.returns_for("absent") == ()

    every = [
        (actor, item)
        for actor, items in queue.state()["deliveries"].items()
        for item in items
    ]
    for actor in ("heir", "other", "learner", "absent"):
        assert queue.returns_for(actor) == tuple(
            item for owner, item in every if owner == actor
        )


def test_time_out_names_its_decisions_and_emits_one_penalty_each(queue, open_decision):
    """The runtime owns the clock a cutoff counts in (ticks, time audit T3); the queue
    owns the penalty. A named pending decision times out once, whatever its wall
    deadline; one already timed out or settled is untouched; an unknown handle raises."""
    early, late, settled = open_decision(), open_decision(), open_decision()
    settle(queue, settled)
    # A deadline far in the future is no protection: the caller's cutoff decides.
    assert queue.time_out([early, settled], 0) == [early]
    assert queue.time_out([early], 1) == []
    assert queue.get(early).status == SettleStatus.TIMED_OUT
    assert queue.get(late).status == SettleStatus.PENDING
    assert queue.get(settled).status == SettleStatus.SETTLED
    assert [r.status for r in queue.history(early)] == [SettleStatus.TIMED_OUT]
    assert queue.returns_for("learner")[-1].channel == "timeout"
    with pytest.raises(KeyError):
        queue.time_out(["decision-404"], 1)
    with pytest.raises(ValueError):
        queue.time_out([late], -1)
    settle(queue, early, score=0.9)  # the late settlement's right survives
    assert queue.get(early).status == SettleStatus.SETTLED


# ---- wave 17: the retained-feedback bound ------------------------------------------


def test_releasing_read_deliveries_keeps_the_count_and_every_decision(queue, open_decision):
    """A consumer's already-read feedback is released; nothing else is lost."""
    handles = [open_decision() for _ in range(4)]
    for handle in handles:
        settle(queue, handle)
    assert queue.delivered_count("learner") == 4
    assert queue.release_delivered("learner", 3) == 3
    assert [r.handle for r in queue.returns_for("learner")] == handles[3:]
    assert queue.delivered_count("learner") == 4
    assert queue.returns_since("learner", 3) == (queue.returns_for("learner"), 4)
    # Every decision, its outcome and its history stay addressable.
    for handle in handles:
        assert queue.get(handle).status is SettleStatus.SETTLED
        assert len(queue.history(handle)) == 1
    # A later delivery lands after the released prefix, counted from it.
    extra = open_decision()
    settle(queue, extra)
    assert queue.returns_since("learner", 3)[1] == 5
    assert [r.handle for r in queue.returns_since("learner", 4)[0]] == [extra]


def test_invariant_a_release_never_passes_what_was_delivered(queue, open_decision):
    """Violation attempt: release a return the actor was never sent."""
    pending = open_decision()
    settle(queue, open_decision())
    with pytest.raises(ValueError):
        queue.release_delivered("learner", 2)  # only one was delivered; one is pending
    with pytest.raises(ValueError):
        queue.release_delivered("absent", 1)
    with pytest.raises(ValueError):
        queue.release_delivered("learner", -1)
    assert queue.delivered_count("learner") == 1
    assert queue.get(pending).status is SettleStatus.PENDING
    # Releasing nothing, or less than already released, changes nothing.
    assert queue.release_delivered("learner", 1) == 1
    assert queue.release_delivered("learner", 0) == 1
    assert queue.delivered_count("learner") == 1


def test_invariant_a_reader_cannot_silently_skip_a_released_return(queue, open_decision):
    """Violation attempt: read from a position inside the released prefix."""
    for _ in range(3):
        settle(queue, open_decision())
    queue.release_delivered("learner", 2)
    with pytest.raises(ValueError):
        queue.returns_since("learner", 1)
    with pytest.raises(ValueError):
        queue.returns_since("learner", 0)


def test_the_release_survives_a_checkpoint(queue, open_decision):
    from factorylab.kernel.ledger import Ledger
    from factorylab.kernel.queue import DecisionQueue

    for _ in range(3):
        settle(queue, open_decision())
    queue.release_delivered("learner", 2)
    twin = DecisionQueue(Ledger())
    twin._restore_state(queue.state())
    assert twin.delivered_count("learner") == 3
    assert twin.returns_for("learner") == queue.returns_for("learner")
    with pytest.raises(ValueError):
        twin.returns_since("learner", 1)
    # A checkpoint written before releases restores with every delivery it holds.
    older = {k: v for k, v in queue.state().items() if k != "released"}
    twin._restore_state(older)
    assert twin.delivered_count("learner") == 1


def test_a_final_decision_s_actions_are_folded_then_forgotten(queue, open_decision):
    """Actions are read only while a decision is open: once final they are folded into
    the contract's history and dropped, and the history answers as before."""
    from factorylab.kernel.queue import action_key

    handle = open_decision()
    key = action_key(tool="venue.place", kind="venue")
    queue.record_actions(handle, [key])
    assert handle in queue.state()["actions"]
    settle(queue, handle)
    assert handle not in queue.state()["actions"]
    assert queue.has_action_history(queue.get(handle).propensity.chosen, key)
    # Nothing may add to a final decision's actions, so nothing can need them.
    with pytest.raises(ValueError):
        queue.record_actions(handle, [key])
    timed_out = open_decision()
    queue.record_actions(timed_out, [key])
    queue.time_out([timed_out], 0)
    assert timed_out in queue.state()["actions"]  # a late settlement may still come
