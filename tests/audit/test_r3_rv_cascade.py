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


def windows_of(rt, tier):
    """How many observation windows this tier's open gate covers.

    Restated for R3-D: a tier's separation is a duration, not a count of
    arrivals (GPT-6 third reading §6.C). The gate's window is the jittered
    ``timing.min_ratio`` counted in tick intervals, so an arrival per interval
    reaches the release at the same place the old arrival count did.
    """
    return rt.cascade[tier].window_ns // rt.tick_clock.interval_ns


def tick(rt):
    """Advance the world's clock by one observation window."""
    rt.clock.now_ns += rt.tick_clock.interval_ns


def test_custom_conformity_arrivals_cannot_invoke_the_next_tier_early(monkeypatch):
    rt = make_runtime()
    chain(rt)
    seen = routed(rt, monkeypatch)
    rt._route(arrival(rt, 0))
    windows = windows_of(rt, 2)
    assert windows >= rt.m.timing.min_ratio >= 3
    assert seen == []
    for index in range(1, windows):
        tick(rt)
        rt._route(arrival(rt, index))
        assert seen == []  # however many arrive, nothing releases before the time is up
    tick(rt)
    rt._route(arrival(rt, windows))
    assert len(seen) == 1
    window = seen[0].payload["window"]
    assert window["count"] == windows + 1
    assert window["elapsed_ns"] >= window["window_ns"]
    assert list(window["handles"]) == [f"checker-{i}" for i in range(windows + 1)]
    assert rt.cascade_windows[f"checker-{windows}"] == [
        f"checker-{i}" for i in range(windows)]


def test_custom_conformity_buffer_survives_a_checkpoint(monkeypatch):
    rt = make_runtime()
    chain(rt)
    seen = routed(rt, monkeypatch)
    rt._route(arrival(rt, 0))
    windows = windows_of(rt, 2)
    for index in range(1, windows):
        tick(rt)
        rt._route(arrival(rt, index))
    assert seen == []

    restored = _plain_runtime()
    restore_runtime(restored, runtime_state(rt))
    # The window's duration and the moment it opened both survive the checkpoint,
    # so a restored runtime neither redraws its jitter nor restarts its clock.
    assert restored.cascade[2].window_ns == rt.cascade[2].window_ns
    assert restored.cascade[2].opened_ns == rt.cascade[2].opened_ns
    after = routed(restored, monkeypatch)
    assert restored.routers.get("CheckA")
    restored.clock.now_ns = rt.clock.now_ns + restored.tick_clock.interval_ns
    restored._route(arrival(restored, windows))
    assert len(after) == 1
    assert after[0].payload["window"]["count"] == windows + 1


def test_each_custom_tier_buffers_separately_and_keeps_its_own_kind(monkeypatch):
    rt = make_runtime()
    chain(rt)
    seen = routed(rt, monkeypatch)
    rt._route(arrival(rt, 0, kind="CheckA", tier=2))
    windows_two = windows_of(rt, 2)
    rt._route(arrival(rt, 0, kind="CheckB", tier=3))
    windows_three = windows_of(rt, 3)
    assert seen == []
    for index in range(1, windows_two + 1):
        tick(rt)
        rt._route(arrival(rt, index, kind="CheckA", tier=2))
    assert [str(ev.kind) for ev in seen] == ["CheckA"]
    # Each tier keeps its own duration and its own kind: the CheckB window opened
    # at the same moment and releases only when its own time is up.
    for index in range(1, windows_three + 1):
        tick(rt)
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
    windows = windows_of(rt, 2)
    for index in range(1, windows):
        tick(rt)
        rt._route(arrival(rt, index))
    assert len(items(rt, "invocation")) == paid
    assert rt.stats.decisions == decisions

    tick(rt)
    rt._route(arrival(rt, windows))
    assert rt.stats.decisions == decisions + 1
    invocation = items(rt, "invocation")[paid:]
    assert len(invocation) == 1
    assert rt.handle_to_assembly[invocation[0]["handle"]] == "check-b"
