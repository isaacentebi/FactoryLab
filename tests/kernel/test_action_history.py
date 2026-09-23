"""An unhistoried action is kernel physics (essay II.II.b; ruling R5).

"Some share of compute and write access is usable only in the context of
unhistoried actions (decisions that arrive carrying no propensity record and no
reward trail)." The kernel names an action (a declared label, or a tool of a kind)
and says whether a contract has a reward trail for it; only a settled decision can
give it one.
"""

import pytest

from factorylab.kernel.queue import DecisionQueue, action_key


def _settle(queue, handle, status="settled"):
    queue.settle(handle, channel="outcome", score=0.5, status=status,
                 definition_version="score-v1", sampling_ref=None)


def test_an_action_is_a_label_or_a_tool_of_a_kind_exactly():
    assert action_key(label="hold") == "label:hold"
    assert action_key(tool="venue.positions", kind="venue") == "tool:venue:venue.positions"
    for bad in ({}, {"label": "hold", "tool": "t", "kind": "k"}, {"label": ""},
                {"tool": "t"}, {"tool": "t", "kind": " "}, {"label": 3}):
        with pytest.raises(ValueError):
            action_key(**bad)


def test_only_a_settled_decision_gives_an_action_its_reward_trail(queue, open_decision):
    key = action_key(tool="half-spread", kind="population")
    handles = [open_decision() for _ in range(3)]
    seat = queue.get(handles[0]).propensity.chosen
    for handle in handles:
        queue.record_actions(handle, {key})
    _settle(queue, handles[0], status="censored")
    queue.time_out([handles[1]], 200)
    assert not queue.has_action_history(seat, key)  # missingness is no trail
    _settle(queue, handles[2])
    assert queue.has_action_history(seat, key)
    assert not queue.has_action_history(seat, action_key(label="order"))
    assert not queue.has_action_history("someone-else", key)


def test_actions_cannot_be_forged_onto_a_closed_or_unknown_decision(queue, open_decision):
    handle = open_decision()
    with pytest.raises(ValueError):
        queue.record_actions(handle, {"not-an-action"})
    with pytest.raises(ValueError):
        queue.record_actions(handle, set())
    with pytest.raises(KeyError):
        queue.record_actions("decision-unknown", {action_key(label="hold")})
    _settle(queue, handle)
    with pytest.raises(ValueError, match="final outcome"):
        queue.record_actions(handle, {action_key(label="hold")})
    seat = queue.get(handle).propensity.chosen
    assert not queue.has_action_history(seat, action_key(label="hold"))


def test_a_late_record_on_a_timed_out_decision_counts_when_it_settles(queue, open_decision):
    handle = open_decision()
    queue.time_out([handle], 200)
    key = action_key(label="investigate")
    queue.record_actions(handle, {key})
    _settle(queue, handle)
    assert queue.has_action_history(queue.get(handle).propensity.chosen, key)


def test_action_history_is_ledgered_first_and_survives_a_checkpoint(ledger, clock, queue,
                                                                    open_decision):
    handle = open_decision()
    key = action_key(label="hold")
    queue.record_actions(handle, {key})
    queue.record_actions(handle, {key})  # nothing new: nothing ledgered
    items = [i for i in ledger._recovery_items() if i["kind"] == "decision.actions"]
    assert [i["actions"] for i in items] == [[key]]
    _settle(queue, handle)
    restored = DecisionQueue(ledger, clock_ns=clock)
    restored._restore_state(queue.state())
    seat = queue.get(handle).propensity.chosen
    assert restored.has_action_history(seat, key)
    older = {k: v for k, v in queue.state().items() if k not in ("actions", "settled_actions")}
    fresh = DecisionQueue(ledger, clock_ns=clock)
    fresh._restore_state(older)  # an older checkpoint: no action has a trail yet
    assert not fresh.has_action_history(seat, key)
