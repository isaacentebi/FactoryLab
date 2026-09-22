"""Judges read the work like a machine: input, output, acts, propensity (essay II.I.b).

Each case builds what a judge is sent and asserts that nothing in it names the
author, reveals the author's role, or repeats the propensity outside the
PROPENSITY block (information audit C1, C2, C7, P5, P8, U5).
"""

import json
from types import SimpleNamespace

from factorylab.cortex.request import Return
from factorylab.kernel.events import Event, EventKind
from factorylab.runtime.shared import CH_CONFORMITY, CH_EXPOSURE, CH_FAST
from tests.runtime.test_loop import (
    _consequence_decision,
    _consequence_runtime,
)

PROPENSITY = {"over": {"hold": 0.6, "order": 0.4}, "chosen": "hold"}


def _captured(rt, monkeypatch):
    captured = []
    request = rt._request

    def capture(*args, **kwargs):
        result = request(*args, **kwargs)
        captured.append(result)
        return result

    monkeypatch.setattr(rt, "_request", capture)
    return captured


def _antagonist_return(rt):
    """An Exposure seat's return, emitted exactly as the producer step emits it."""
    handle = _consequence_decision(rt, "antagonist-a", CH_EXPOSURE)
    rt.handle_to_assembly[handle] = "antagonist-a"
    rt._producer_step(
        Event("tick-judge-view", EventKind.TICK, rt.clock.now_ns, {"index": 0}, "test"),
        handle, SimpleNamespace(chosen="antagonist-a"), rt.queue.get(handle).deadline_ns,
        returned=Return(handle, {"action": "hold", "payoff": 0.93, "propensity": PROPENSITY,
                                 "rationale": "nothing to do"}, 0, "ok"),
    )
    return next(e for e in rt.internal
                if e.kind == EventKind.PRODUCER_RETURN and e.payload["about_handle"] == handle)


def test_first_tier_judge_is_not_told_the_author_role_or_its_payoff(monkeypatch):
    rt = _consequence_runtime()
    event = _antagonist_return(rt)
    captured = _captured(rt, monkeypatch)
    judge = _consequence_decision(rt, "eval-a", CH_CONFORMITY)
    rt._evaluator_step(event, judge, SimpleNamespace(chosen="eval-a"),
                       rt.queue.get(judge).deadline_ns,
                       returned=Return(judge, {"status": "cannot", "reason": "x"}, 0, "ok"))
    req = captured[-1]
    producer = req.inputs["producer"]
    # C1: an event-neutral description, and the sealed self-forecast is not shown.
    assert producer["description"] == f"Respond to event {event.payload['inputs']['kind']} " \
        "on test."
    assert "payoff" not in json.dumps(producer)
    # P8: the propensity is rendered once, in the PROPENSITY block, never in INPUTS.
    assert "propensity" not in json.dumps(producer)
    # P5, U5: no standing, and no null learner slot.
    assert "your_consequence_standing" not in req.inputs
    assert "your_action_policy" not in req.inputs
    assert "antagonist-a" not in json.dumps(producer)


def test_meta_judge_reads_the_machine_view_not_the_world(monkeypatch):
    rt = _consequence_runtime()
    judged = _consequence_decision(rt, "eval-a", CH_CONFORMITY)
    event = Event("verdict-judge-view", EventKind.VERDICT, rt.clock.now_ns, {
        "about_handle": "some-return", "evaluator_handle": judged, "verdict": 0.7,
        "payoff": 0.4, "payoff_handle": None, "rationale": "fine",
        "producer_outputs": {"action": "hold", "payoff": 0.93, "propensity": PROPENSITY},
        "propensity": PROPENSITY,
    }, "runtime")
    captured = _captured(rt, monkeypatch)
    meta = _consequence_decision(rt, "meta-b", CH_FAST)
    rt._meta_step(event, meta, SimpleNamespace(chosen="meta-b"),
                  rt.queue.get(meta).deadline_ns,
                  returned=Return(meta, {"conformity": 0.8, "rationale": "ok"}, 0, "ok"))
    req = captured[-1]
    # C7: the same operating projection first-tier judges get, scoped to this seat.
    assert "world" not in req.inputs
    assert [row["seat_id"] for row in req.inputs["actor_context"]["seats"]] == ["meta-b"]
    # C1, P8 at the tier above: the judged producer's payoff and propensity are not
    # shown, and the judge's own propensity is only in the PROPENSITY block.
    assert req.inputs["producer_outputs"] == {"action": "hold"}
    assert "propensity" not in req.inputs["verdict"]
    assert "your_action_policy" not in req.inputs


def test_ballot_reads_the_machine_view_not_the_world(monkeypatch):
    from factorylab.charter.amendment import PredictedEffect

    rt = _consequence_runtime()
    captured = _captured(rt, monkeypatch)
    monkeypatch.setattr(rt.charter_book, "vote", lambda *_: None)
    monkeypatch.setattr(rt.charter_book, "tally", lambda *_: "failed")
    monkeypatch.setattr(rt, "_invoke", lambda aid, req, role, **_: Return(
        req.handle, {"vote": True, "reason": "x"}, 0, "ok"))
    am = SimpleNamespace(id="test", proposed_prices=(), add=(), replace=(), remove=(),
                         predicted_effect=PredictedEffect("cost_per_return", "decrease", 1),
                         tick_interval=None)
    rt._hold_vote(am, SimpleNamespace(seats=[("seat1", "seed-decider")]))
    req = captured[-1]
    assert "world" not in req.inputs
    assert [row["seat_id"] for row in req.inputs["actor_context"]["seats"]] == ["seed-decider"]
