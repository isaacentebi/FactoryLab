"""Grounded evaluation remains singular and independent with additive routers."""

from types import SimpleNamespace

from factorylab.cortex.registration import AssemblyProposal
from factorylab.cortex.request import ChildRequest, Return
from factorylab.kernel.events import Event, EventKind
from factorylab.kernel.queue import PropensityRecord, SettleStatus
from factorylab.learners.router import Sample
from factorylab.runtime.grounded import public_evidence
from factorylab.runtime.resume import decode, encode
from factorylab.runtime.shared import CH_CONFORMITY, CH_FAST, CH_VERDICT, NOOP
from tests.runtime.test_grounded_feedback import (
    _GroundedProvider,
    _open_contract,
    _runtime,
)
from tests.runtime.test_loop import (
    _consequence_decision,
    _consequence_judge,
    _consequence_produce,
)


class _CustomProducer(_GroundedProvider):
    def _produce(self, desc, inputs):
        return {"emits": "Investigation", "action": "investigate", "finding": "bounded"}


_INVESTIGATION_SCHEMA = {
    "type": "object",
    "properties": {
        "action": {"type": "string"},
        "finding": {"type": "string"},
    },
    "required": ["action", "finding"],
}


def _register_custom_producer(rt):
    rt._manage_reserve_window()
    rt._register("author", AssemblyProposal(
        "custom-producer", "producer", "fake-haiku", "Reply with JSON.",
        ("Tick",), 128, "low", ("Investigation",),
        {"Investigation": _INVESTIGATION_SCHEMA},
    ))


def test_provisional_judges_and_forecasts_accumulate_and_restore_legacy_defaults():
    rt = _runtime()
    producer, event = _consequence_produce(rt)
    _consequence_judge(rt, event, "eval-a")
    first_forecasts = rt.grounded_pending[producer].forecast_handles
    _consequence_judge(rt, event, "eval-b")

    contract = rt.grounded_pending[producer]
    assert contract.initial_evaluators == ("eval-a", "eval-b")
    assert first_forecasts
    assert contract.forecast_handles[:len(first_forecasts)] == first_forecasts
    assert len(contract.forecast_handles) > len(first_forecasts)

    accumulated = contract.with_initial(
        judge_handle="third", evaluator_id="eval-c",
        forecast_handles=("forecast-a",),
        forecasts=({"handle": "forecast-a", "predicate": "fill_within"},),
    ).with_initial(
        judge_handle="fourth", evaluator_id="eval-d",
        forecast_handles=("forecast-b",),
        forecasts=({"handle": "forecast-b", "predicate": "fill_within"},),
    )
    assert accumulated.forecast_handles[-2:] == ("forecast-a", "forecast-b")
    assert [row["handle"] for row in accumulated.forecasts[-2:]] == [
        "forecast-a", "forecast-b",
    ]
    rt.events_log.extend([
        {"kind": "ForecastSettled", "payload": {
            "handle": "forecast-a", "status": "settled", "y": 1,
        }},
        {"kind": "ForecastSettled", "payload": {
            "handle": "forecast-b", "status": "settled", "y": 0,
        }},
    ])
    assert {
        row["payload"]["handle"] for row in public_evidence(rt, accumulated)
        if row["kind"] == "ForecastSettled"
    } >= {"forecast-a", "forecast-b"}

    rt.grounded_pending[producer] = accumulated
    rt.ticks_consumed = accumulated.due_tick
    rt._settle_due_grounded()
    assert set(rt.internal[-1].payload["excluded_evaluators"]) >= {
        "eval-a", "eval-b", "eval-c", "eval-d",
    }

    legacy = encode(contract)
    legacy["fields"].pop("initial_evaluators")
    legacy["fields"].pop("subject_kind")
    restored = decode(legacy)
    assert restored.initial_evaluators == (contract.initial_evaluator,)
    assert restored.subject_kind == "ProducerReturn"


def test_late_provisional_judge_is_retained_for_open_grounded_contract():
    rt = _runtime()
    producer, event = _consequence_produce(rt)
    rt.queue.expire(10**16)
    assert rt.queue.get(producer).status is SettleStatus.TIMED_OUT

    _consequence_judge(rt, event, "eval-a")

    assert rt.grounded_pending[producer].initial_evaluators == ("eval-a",)


def test_unmeasurable_commitment_does_not_make_provisional_judge_fresh(monkeypatch):
    rt = _runtime()
    producer, event = _consequence_produce(rt)
    monkeypatch.setattr(rt, "_judged_commitment", lambda *_args: None)
    judge = _consequence_judge(rt, event, "eval-a")
    assert rt.queue.get(judge).status is SettleStatus.INAPPLICABLE
    contract = rt.grounded_pending[producer]
    assert contract.initial_evaluators == ("eval-a",)
    assert contract.forecast_handles == ()
    rt.ticks_consumed = contract.due_tick
    rt._settle_due_grounded()
    final = rt.internal[-1]
    assert "eval-a" in final.payload["excluded_evaluators"]
    assert "eval-a" not in rt._universe_for(str(final.kind), final)


def test_custom_judged_kind_is_frozen_and_receives_the_final_commission():
    rt = _runtime(provider=_CustomProducer())
    _register_custom_producer(rt)
    rt._register("author", AssemblyProposal(
        "custom-eval", "evaluator", "fake-haiku", "Reply with JSON.",
        ("Investigation",), 128, "low", ("Verdict",), {},
    ))
    channels = rt._return_channels("custom-producer")
    handle = rt.queue.open(
        actor="test-router", event_id="custom-tick",
        propensity=PropensityRecord(
            ("custom-producer",), (1.0,), "custom-producer", 0,
            "test-router", "state",
        ),
        channel=next(iter(channels.values())), deadline_ns=rt.clock.now_ns + 10**12,
        parent_handle=None, cost_ceiling=rt.wallet.available, return_channels=channels,
    )
    rt._start_return(handle)
    rt.handle_to_assembly[handle] = "custom-producer"
    rt._producer_step(
        Event("custom-tick", EventKind.TICK, rt.clock.now_ns, {"index": 0}, "test"),
        handle, SimpleNamespace(chosen="custom-producer"), rt.queue.get(handle).deadline_ns,
    )
    contract = rt.grounded_pending[handle]
    assert contract.subject_kind == "Investigation"

    rt.ticks_consumed = contract.due_tick
    rt._settle_due_grounded()
    commission = rt.internal[-1]
    assert str(commission.kind) == "Investigation"
    assert commission.payload["grounded_consequence"] is True
    state = rt._build_router("Investigation", "exp3", 0.1)
    monkey_sample = Sample(
        ("custom-eval", NOOP), (1.0, 0.0), "custom-eval", 7,
        state.learner.id, "forced-test-state", (),
    )
    state.router.route = lambda *_args, **_kwargs: monkey_sample
    before = rt.stats.invocations
    rt._route(commission)
    assert rt.stats.invocations == before + 1
    assert "custom-eval" in rt.handle_to_assembly.values()


def test_custom_child_freezes_its_selected_kind_for_final_commission():
    rt = _runtime(provider=_CustomProducer())
    _register_custom_producer(rt)
    parent = _consequence_decision(rt, "seed-decider", CH_VERDICT)
    request = rt._request(
        parent, "parent", {}, {"type": "object"}, 10**18, CH_VERDICT,
    )

    _result, cost = rt._invoke_child(
        "seed-decider", request,
        ChildRequest(
            "custom-producer", "child investigation", {}, _INVESTIGATION_SCHEMA,
        ),
        rt.wallet.available,
    )

    row = next(
        item for item in reversed(rt.ledger._recovery_items())
        if item["kind"] == "request.child"
    )
    child = row["handle"]
    assert cost > 0
    assert rt.grounded_pending[child].subject_kind == "Investigation"


def test_final_commission_uses_one_additive_router_but_recursive_verdict_uses_all(monkeypatch):
    rt = _runtime()
    producer, contract = _open_contract(rt)
    rt._build_router("ProducerReturn", "exp3", 0.1, replace=False)
    rt.ticks_consumed = contract.due_tick
    rt._settle_due_grounded()
    commission = rt.internal[-1]

    states = rt.routers["ProducerReturn"]
    routed = []
    original = rt._route_with

    def route_once(state, event):
        routed.append(state.learner.id)
        sample = Sample(
            (NOOP,), (1.0,), NOOP, 9, state.learner.id, "forced-test-state", (),
        )
        monkeypatch.setattr(state.router, "route", lambda *_args, **_kwargs: sample)
        return original(state, event)

    monkeypatch.setattr(rt, "_route_with", route_once)
    before_decisions = rt.stats.decisions
    rt._route(commission)
    assert routed == [states[0].learner.id]
    assert rt.stats.decisions == before_decisions + 1
    selection = next(
        row for row in reversed(rt.ledger._recovery_items())
        if row["kind"] == "consequence.final_router"
    )
    assert selection["policy"] == "first-active-router-v1"
    assert len(selection["eligible_routers"]) == 2

    rt._build_router("Verdict", "exp3", 0.1, replace=False)
    routed.clear()
    recursive = Event(
        "recursive-grounded", EventKind.VERDICT, rt.clock.now_ns,
        {
            "about_handle": producer,
            "grounded_consequence": True,
            "grounded_contract": {},
        },
        "runtime",
    )
    monkeypatch.setattr(rt, "_cascade_arrival", lambda event: event)
    monkeypatch.setattr(rt, "_route_with", lambda state, event: routed.append(state.learner.id))
    rt._route(recursive)
    assert routed == [state.learner.id for state in rt.routers["Verdict"]]
    assert "meta-a" in rt._universe_for("Verdict", recursive)


def test_recursive_grounded_prompt_reviews_the_immediate_meta(monkeypatch):
    rt = _runtime()
    rt.outcomes.append("meta-b", handle="unseen", outcome={"kind": "message"})
    immediate = _consequence_decision(rt, "meta-a", CH_CONFORMITY)
    rt.handle_to_assembly[immediate] = "meta-a"
    event = Event(
        "recursive-grounded-meta", EventKind.META_VERDICT, rt.clock.now_ns,
        {
            "about": "original-final-judge",
            "by": immediate,
            "tier": 2,
            "score": 0.7,
            "rationale": "the final judge used the frozen evidence correctly",
            "evaluator_handle": "original-final-judge",
            "realized_finding": {
                "status": "supported", "score": 0.9,
                "evidence": ["event:7"], "reason": "receipt supports the claim",
            },
            "grounded_contract": {
                "norms": ["useful inquiry"],
                "producer_outputs": {"action": "investigate"},
            },
            "grounded_evidence": [{"ref": "event:7", "kind": "ForecastSettled"}],
        },
        "runtime",
    )
    captured = []
    request = rt._request

    def capture(*args, **kwargs):
        result = request(*args, **kwargs)
        captured.append(result)
        return result

    monkeypatch.setattr(rt, "_request", capture)
    reviewer = _consequence_decision(rt, "meta-b", CH_FAST)
    rt._meta_step(
        event, reviewer, SimpleNamespace(chosen="meta-b"),
        rt.queue.get(reviewer).deadline_ns,
        returned=Return(reviewer, {"conformity": 0.8, "rationale": "conforms"}, 0, "ok"),
    )

    req = captured[-1]
    assert req.inputs["meta_verdict"]["by"] == immediate
    assert req.inputs["realized_consequence"]["finding"]["score"] == 0.9
    assert "world" not in req.inputs and "charter" not in req.inputs
    assert req.inputs["actor_context"]["seats"][0]["seat_id"] == "meta-b"
    assert "catalogue.search" in req.stable_prefix()
    assert "your_state" not in req.inputs and "unread_outcomes" not in req.inputs
    rt.outcomes.ack_through("meta-b", "outcome:1")
    assert rt.outcomes.cursors.get("meta-b", 0) == 0
    assert "Assess the immediate meta verdict in meta_verdict" in req.description
    assert "original finding as a new first-tier review" in req.description
    assert "Assess the final grounded judgement" not in req.description
