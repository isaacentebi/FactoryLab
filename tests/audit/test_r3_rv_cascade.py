"""T57: a custom conformity kind is buffered by the same evaluation cascade as the seeds.

The cascade is what keeps each higher evaluatory tier slower than the one below
it. Admission must follow the declared reward shape, not a pair of literal seed
kind names, or a population can buy an immediate next-tier invocation by
registering a new name for the same judgement.
"""

import json
from dataclasses import replace

import pytest

from factorylab.cortex.registration import AssemblyProposal
from factorylab.runtime.cascade import event_tier
from factorylab.runtime.resume import restore_runtime, runtime_state
from factorylab.runtime.routing import PopulationEvent
from factorylab.world.models import ModelResponse
from tests.audit.test_a1_composition import items, make_runtime
from tests.conftest import make_runtime as _plain_runtime
from tests.runtime.test_fidelity import decision

SCHEMA = {"type": "object", "properties": {"conformity": {"type": "number"}}}


def checker(rt, handle, aid, kind, accepts):
    """Register one population checker paid by the conformity shape."""
    rt._register(handle, AssemblyProposal(
        aid, "meta", "fake-haiku", "Check the return against the charter.",
        (accepts,), 128, "low", (kind,), {kind: SCHEMA}, {kind: "conformity"}))
    rt._build_router(kind, "exp3", 0.1)


def chain(rt):
    """A checking chain over three distinct custom conformity kinds."""
    handle = decision(rt, "seed-decider")
    checker(rt, handle, "check-a", "CheckA", "WeatherForecast")
    checker(rt, handle, "check-b", "CheckB", "CheckA")
    checker(rt, handle, "check-c", "CheckC", "CheckB")
    return handle


def arrival(rt, index, *, kind="CheckA", tier=2, score=0.5):
    return PopulationEvent(f"{kind.lower()}-{index}", kind, rt.clock.now_ns, {
        "about": f"judged-{index}", "tier": tier, "score": score,
        "by": f"checker-{index}", "evaluator_handle": f"judge-{index}",
        "rationale": "", "about_handle": f"checker-{index}",
    }, "runtime")


def routed(rt, monkeypatch):
    seen = []
    monkeypatch.setattr(rt, "_route_with", lambda state, ev: seen.append(ev))
    return seen


def test_custom_conformity_arrivals_cannot_invoke_the_next_tier_early(monkeypatch):
    rt = make_runtime()
    chain(rt)
    seen = routed(rt, monkeypatch)
    rt._route(arrival(rt, 0))
    threshold = rt.cascade[2].threshold
    assert threshold >= rt.m.timing.min_ratio >= 3
    assert seen == []
    for index in range(1, threshold - 1):
        rt._route(arrival(rt, index))
        assert seen == []
    rt._route(arrival(rt, threshold - 1))
    assert len(seen) == 1
    window = seen[0].payload["window"]
    assert window["count"] == threshold
    assert list(window["handles"]) == [f"checker-{i}" for i in range(threshold)]
    assert rt.cascade_windows[f"checker-{threshold - 1}"] == [
        f"checker-{i}" for i in range(threshold - 1)]


def test_custom_conformity_buffer_survives_a_checkpoint(monkeypatch):
    rt = make_runtime()
    chain(rt)
    seen = routed(rt, monkeypatch)
    rt._route(arrival(rt, 0))
    threshold = rt.cascade[2].threshold
    for index in range(1, threshold - 1):
        rt._route(arrival(rt, index))
    assert seen == []

    restored = _plain_runtime()
    restore_runtime(restored, runtime_state(rt))
    assert restored.cascade[2].threshold == threshold
    after = routed(restored, monkeypatch)
    assert restored.routers.get("CheckA")
    restored._route(arrival(restored, threshold - 1))
    assert len(after) == 1
    assert after[0].payload["window"]["count"] == threshold


def test_each_custom_tier_buffers_separately_and_keeps_its_own_kind(monkeypatch):
    rt = make_runtime()
    chain(rt)
    seen = routed(rt, monkeypatch)
    rt._route(arrival(rt, 0, kind="CheckA", tier=2))
    threshold_two = rt.cascade[2].threshold
    rt._route(arrival(rt, 0, kind="CheckB", tier=3))
    threshold_three = rt.cascade[3].threshold
    assert seen == []
    for index in range(1, threshold_two):
        rt._route(arrival(rt, index, kind="CheckA", tier=2))
    assert [str(ev.kind) for ev in seen] == ["CheckA"]
    for index in range(1, threshold_three):
        rt._route(arrival(rt, index, kind="CheckB", tier=3))
    assert [str(ev.kind) for ev in seen] == ["CheckA", "CheckB"]


def test_a_custom_forecast_kind_is_not_a_cascade_arrival(monkeypatch):
    """Only judgement-shaped work is buffered; a forecast return is delivered at once."""
    rt = make_runtime()
    handle = decision(rt, "seed-decider")
    rt._register(handle, AssemblyProposal(
        "weather-desk", "producer", "fake-haiku", "Predict.", ("Tick",), 128, "low",
        ("WeatherForecast",), {"WeatherForecast": {"type": "object", "properties": {}}},
        {"WeatherForecast": "forecast"}))
    rt._build_router("WeatherForecast", "exp3", 0.1)
    seen = routed(rt, monkeypatch)
    event = PopulationEvent("weather-1", "WeatherForecast", rt.clock.now_ns,
                            {"about_handle": handle, "outputs": {}, "cost": 0,
                             "status": "ok"}, "runtime")
    rt._route(event)
    assert len(seen) == 1
    assert not rt.cascade


@pytest.mark.parametrize("tier", [2, 5])
def test_event_tier_reads_the_declared_tier_of_a_custom_conformity_kind(tier):
    rt = make_runtime()
    assert event_tier(arrival(rt, 0, tier=tier)) == tier
    with pytest.raises(ValueError, match="Verdict"):
        event_tier(replace(arrival(rt, 0), payload={"about": "x"}))


def test_a_real_chain_pays_the_next_tier_only_at_the_release_threshold(monkeypatch):
    """Nothing is stubbed between the arrival and the invocation it would pay for."""
    rt = make_runtime()
    chain(rt)
    state = rt.routers["CheckA"][0]
    monkeypatch.setattr(state.learner, "distribution", lambda feasible: {
        aid: float(aid == "check-b") for aid in feasible})
    monkeypatch.setattr(rt.provider.target, "complete", lambda req: ModelResponse(
        req.model_id, json.dumps({"conformity": 0.9}), 1, 1, "end_turn"))
    paid, decisions = len(items(rt, "invocation")), rt.stats.decisions

    rt._route(arrival(rt, 0))
    threshold = rt.cascade[2].threshold
    for index in range(1, threshold - 1):
        rt._route(arrival(rt, index))
    assert len(items(rt, "invocation")) == paid
    assert rt.stats.decisions == decisions

    rt._route(arrival(rt, threshold - 1))
    assert rt.stats.decisions == decisions + 1
    invocation = items(rt, "invocation")[paid:]
    assert len(invocation) == 1
    assert rt.handle_to_assembly[invocation[0]["handle"]] == "check-b"
