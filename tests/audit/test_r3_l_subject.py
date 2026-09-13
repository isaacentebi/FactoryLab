"""Unknown subjects fall back; forbidden existing targets cost the judge its score."""

from types import SimpleNamespace

import pytest

from factorylab.cortex.request import Return
from factorylab.kernel.queue import PropensityRecord, SettleStatus
from factorylab.runtime.shared import CH_CONFORMITY
from tests.audit.test_r3_b_authority import _items, _producing_decision
from tests.conftest import make_runtime
from tests.runtime.test_loop import _consequence_produce


def _judge(rt, event, about, *, parent=None):
    actor = "l-subject-judge"
    handle = rt.queue.open(
        actor=actor, event_id=event.id, channel=CH_CONFORMITY,
        propensity=PropensityRecord(("eval-a",), (1.0,), "eval-a", 0, actor, "test"),
        deadline_ns=10**15, parent_handle=parent, cost_ceiling=0,
    )
    returned = Return(handle, {
        "verdict": 0.8, "payoff": 0.4, "rationale": "test judgement",
        "forecasts": [], "about_handle": about,
    }, 0, "ok")
    rt._evaluator_step(event, handle, SimpleNamespace(chosen="eval-a"),
                       rt.queue.get(handle).deadline_ns, returned=returned)
    return handle


@pytest.mark.parametrize("about", ["decision-unknown", "the current return looks sound"])
def test_unknown_handle_and_prose_fall_back_with_reason(about):
    rt = make_runtime()
    subject, event = _consequence_produce(rt, "seed-observer")
    judge = _judge(rt, event, about)
    ignored = _items(rt, "about_handle.ignored")
    assert len(ignored) == 1
    assert ignored[0]["handle"] == judge and ignored[0]["about_handle"] == about
    assert ignored[0]["subject"] == subject and ignored[0]["reason"]
    assert any(f["reason"] == f"judgement: {ignored[0]['reason']}"
               for f in rt.registration_feedback)
    assert rt.decision_subjects[judge] == subject
    assert rt.stats.verdicts == 1 and rt.stats.forecasts_sealed == 1
    assert not _items(rt, "return.refused")
    assert rt.queue.get(judge).status is SettleStatus.PENDING


@pytest.mark.parametrize("forbidden,reason", [
    ("own-return", "independent"),
    ("ancestor", "self-judgement: ancestor"),
    ("hindsight", "consequence is still open"),
    ("no-return", "independent"),
])
def test_forbidden_existing_handle_is_refused_and_scores_zero(forbidden, reason):
    rt = make_runtime()
    _subject, event = _consequence_produce(rt, "seed-observer")
    if forbidden == "no-return":
        target = _producing_decision(rt)
    else:
        target, _ = _consequence_produce(
            rt, "NOOP" if forbidden == "hindsight" else "seed-decider")
    if forbidden == "own-return":
        rt.handle_to_assembly[target] = "eval-a"
    if forbidden == "hindsight":
        assert rt.consequences.payoff(target) is not None
    judge = _judge(rt, event, target, parent=target if forbidden == "ancestor" else None)
    refused = _items(rt, "return.refused")
    assert len(refused) == 1
    assert refused[0]["handle"] == judge and refused[0]["about_handle"] == target
    assert reason in refused[0]["reason"]
    assert any(f["reason"] == f"judgement: {refused[0]['reason']}"
               for f in rt.registration_feedback)
    assert not _items(rt, "about_handle.ignored")
    assert rt.queue.get(judge).status is SettleStatus.SETTLED
    history = rt.queue.history(judge)
    assert len(history) == 1 and history[0].score == 0.0
    assert rt.stats.forecasts_sealed == 0 and not list(rt.book.pending())
    assert rt.decision_subjects.get(judge) is None
