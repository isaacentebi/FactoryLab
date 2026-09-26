"""What the population is told about the reward chain is physics, and true.

Smuggling A3, A4, A7, A8, A9 and D3: the producer request names the ledger's
classification rather than a menu and announces no scoring rule; the judge is told
what to give and that it may decline, with no rubric, no reference example and no
evaluability rule; and the published scoring block describes the chain the kernel
runs (ruling R1).
"""

import json
from types import SimpleNamespace

from factorylab.runtime.resume import restore_runtime, runtime_state
from factorylab.runtime.shared import CH_CONFORMITY
from tests.runtime.test_loop import _consequence_decision, _consequence_runtime, lists_nothing
from tests.runtime.test_reward_chain import Population, _unsettled_produce


def _captured(rt, monkeypatch):
    captured = []
    request = rt._request

    def capture(*args, **kwargs):
        result = request(*args, **kwargs)
        captured.append(result)
        return result

    monkeypatch.setattr(rt, "_request", capture)
    return captured


def test_the_producer_request_states_the_ledger_classification_not_a_menu_or_a_score(
        monkeypatch):
    rt = _consequence_runtime(provider=Population())
    captured = _captured(rt, monkeypatch)
    _unsettled_produce(rt)
    (req,) = captured
    assert "The kernel classifies each answer for the ledger" in req.description
    assert "or over your own action ids" in req.description
    assert "Your action is one of" not in req.description
    assert "neutral" not in req.description
    # The wake describes itself as facts and points to where each settlement is
    # published (Chapter II §I), and states no scoring rule of its own.
    assert "A return here is this seat's decision about this event" in req.description
    for key in ("producer_or_custom_return", "declined_return", "verdict_is_a_prediction"):
        assert f"world.scoring.{key}" in req.description
    stripped = req.description
    for key in rt._scoring_block():
        stripped = stripped.replace(f"world.scoring.{key}", "")
    assert "score" not in stripped and "brier" not in stripped.lower()
    counterfactual = req.outcome_schema["properties"]["counterfactual"]
    assert counterfactual["description"] == "a declined trade, coin and side"
    assert "payoff" not in req.outcome_schema["properties"]


def test_the_judge_is_told_what_to_give_and_the_decline_is_a_form_of_its_own(monkeypatch):
    rt = _consequence_runtime(provider=Population(verdicts=(0.5,)))
    _producer, event = _unsettled_produce(rt)
    captured = _captured(rt, monkeypatch)
    judge = _consequence_decision(rt, "eval-a", CH_CONFORMITY)
    rt._evaluator_step(event, judge, SimpleNamespace(chosen="eval-a"),
                       rt.queue.get(judge).deadline_ns)
    (req,) = captured
    assert req.description == "Give verdict 0-1 on the return against the charter."
    text = json.dumps(req.inputs)
    for coaching in ("forecast_example", "committed to", "unmeasured", "Name the claim",
                     "decline"):
        assert coaching not in text and coaching not in req.description
    answer, decline = req.outcome_schema["anyOf"]
    schema = answer["properties"]
    assert "status" not in schema and "payoff" not in schema
    assert set(answer["required"]) == {"verdict", "rationale"}
    assert decline["required"] == ["status"]  # a reason is optional (``declines``)
    assert decline["properties"]["status"] == {"enum": ["cannot"]}
    assert "fidelity_objection" not in schema and "realized_consequence" not in schema


def test_the_published_scoring_is_the_chain_the_kernel_runs():
    rt = _consequence_runtime()
    scoring = rt._scoring_block()
    for key in ("producer_or_custom_return", "verdict_is_a_prediction", "evaluator_return",
                "meta_return", "malformed_judgement", "antagonist_exposure", "abstention"):
        assert key in scoring
    published = json.dumps(scoring)
    for false_physics in ("mandatory payoff", "sibling", "payoff forecast",
                          "halves effective lambda", "settles 0"):
        assert false_physics not in published
    assert "0.5 + 0.5 * (brier - base)" in scoring["verdict_is_a_prediction"]
    assert "payoff" not in rt.A_RETURN_MAY_INCLUDE
    assert "measured outcome" in rt.A_RETURN_MAY_INCLUDE["verdict"]


def test_a_checkpoint_carrying_charter_window_verdict_commitments_restores_without_them():
    """Ruling R1: the charter-window verdict commitment is deleted. An older checkpoint's
    commitments and their fields are read and ignored; everything else restores."""
    rt = _consequence_runtime(provider=Population(verdicts=(0.5,)))
    producer, _event = _unsettled_produce(rt)
    state = runtime_state(rt)
    old = {"$record": "PendingJudgement", "fields": {
        "handle": "decision-9", "channel": "verdict.norm", "opened_at_event": 3,
        "tier": 1, "about": producer, "judge": "decision-8", "evaluator_id": "eval-a",
        "q": 0.7, "cards": "producer", "window": 0, "payoff_beat": None,
        "awaits_payoff": False, "verdict_closed": False, "verdict_beat": None,
        "graded": False, "unmeasured": False, "opened_at_tick": 0}}
    fields = dict(state["runtime"]["$map"])
    fields["pending"]["$map"].append(["decision-9", old])
    for name, value in (("pending_meta", {"$map": []}), ("verdicts_graded", {"$set": []}),
                        ("verdict_outcomes", {"$map": []})):
        state["runtime"]["$map"].append([name, value])
    twin = _consequence_runtime(provider=Population())
    restore_runtime(twin, state)
    assert "decision-9" not in twin.pending and producer in twin.pending
    assert not hasattr(twin, "pending_meta") and not hasattr(twin, "verdicts_graded")


def test_a_return_declined_trade_is_frozen_from_the_mids_already_broadcast():
    """Ruling R2: the benchmark is fixed ex ante, from the world's own broadcast mids."""
    from tests.runtime.test_reward_chain import _mids

    rt = _consequence_runtime(provider=Population(counterfactual={"coin": "BTC",
                                                                   "side": "sell"}))
    _mids(rt, BTC="100", ETH="10")
    producer, _event = _unsettled_produce(rt)
    frozen = rt.reference_mids[producer]
    assert frozen["declined"] == {"coin": "BTC", "side": "sell"}
    assert frozen["mids"] == [["BTC", "100"], ["ETH", "10"]]
    quiet = lists_nothing(_consequence_runtime(provider=Population()))
    handle, _event = _unsettled_produce(quiet)
    assert handle not in quiet.reference_mids  # a bare hold names nothing to price
