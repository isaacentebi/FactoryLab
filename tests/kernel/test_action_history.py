"""An unhistoried action is kernel physics (essay II.II.b; ruling R5).

"Some share of compute and write access is usable only in the context of
unhistoried actions (decisions that arrive carrying no propensity record and no
reward trail)." The kernel names an action (a tool of a kind; never a free-text
label, which a fresh string would make new) and says whether a contract has
history for it: a decision that took it carrying a propensity record or any
delivered return, censored work included (the #134 review).
"""

import pytest

from factorylab.kernel.queue import DecisionQueue, PropensityRecord, action_key

KEY = action_key(tool="half-spread", kind="population")


def _settle(queue, handle, status="settled"):
    queue.settle(handle, channel="outcome", score=0.5, status=status,
                 definition_version="score-v1", sampling_ref=None)


def _declared(seat):
    return PropensityRecord((seat, "other"), (0.5, 0.5), seat, 0, "assembly", "d",
                            source="declared")


def test_an_action_is_a_tool_of_a_kind_and_never_a_label():
    assert KEY == "tool:population:half-spread"
    for bad in ({"tool": "t", "kind": " "}, {"tool": "", "kind": "k"}):
        with pytest.raises(ValueError):
            action_key(**bad)
    with pytest.raises(TypeError):
        action_key(label="hold")


@pytest.mark.parametrize("status", ["settled", "censored", "inapplicable", "timed_out"])
def test_any_delivered_return_makes_the_action_historied(queue, open_decision, status):
    handle = open_decision()
    seat = queue.get(handle).propensity.chosen
    queue.record_actions(handle, {KEY})
    assert not queue.has_action_history(seat, KEY)  # open, no record, no return yet
    if status == "timed_out":
        queue.time_out([handle], 200)
    else:
        _settle(queue, handle, status=status)
    assert queue.has_action_history(seat, KEY)  # censored work closes the niche too
    assert not queue.has_action_history("someone-else", KEY)


def test_a_propensity_record_alone_makes_the_action_historied(queue, open_decision):
    handle = open_decision()
    seat = queue.get(handle).propensity.chosen
    queue.record_actions(handle, {KEY})
    queue.record_propensity(handle, _declared(seat))
    assert queue.has_action_history(seat, KEY)
    other = open_decision()  # record first, actions after: the same
    queue.record_propensity(other, _declared(seat))
    second = action_key(tool="calc", kind="calc")
    queue.record_actions(other, {second})
    assert queue.has_action_history(seat, second)


def test_actions_cannot_be_forged_onto_a_closed_or_unknown_decision(queue, open_decision):
    handle = open_decision()
    for bad in ({"not-an-action"}, set(), {"label:hold"}, {"tool::x"}):
        with pytest.raises(ValueError):
            queue.record_actions(handle, bad)
    with pytest.raises(KeyError):
        queue.record_actions("decision-unknown", {KEY})
    _settle(queue, handle)
    with pytest.raises(ValueError, match="final outcome"):
        queue.record_actions(handle, {KEY})
    assert not queue.has_action_history(queue.get(handle).propensity.chosen, KEY)


def test_action_history_is_ledgered_first_and_survives_a_checkpoint(ledger, clock, queue,
                                                                    open_decision):
    handle = open_decision()
    queue.record_actions(handle, {KEY})
    queue.record_actions(handle, {KEY})  # nothing new: nothing ledgered
    items = [i for i in ledger._recovery_items() if i["kind"] == "decision.actions"]
    assert [i["actions"] for i in items] == [[KEY]]
    _settle(queue, handle)
    restored = DecisionQueue(ledger, clock_ns=clock)
    restored._restore_state(queue.state())
    seat = queue.get(handle).propensity.chosen
    assert restored.has_action_history(seat, KEY)
    older = {k: v for k, v in queue.state().items() if k not in ("actions", "settled_actions")}
    fresh = DecisionQueue(ledger, clock_ns=clock)
    fresh._restore_state(older)  # an older checkpoint: no action has history yet
    assert not fresh.has_action_history(seat, KEY)
