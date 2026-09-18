"""T21 closure requires the real registration, disclosure and settlement paths."""

import json

import pytest

from factorylab.cortex.request import Return
from factorylab.kernel.events import Event, EventKind
from factorylab.kernel.queue import SettleStatus
from factorylab.world.models import ModelResponse
from tests.audit.test_a1_composition import make_runtime
from tests.helpers import assembly
from tests.runtime.test_child_requests import parent_request


def register_work(rt, *, name="weather-desk", kind="WeatherForecast", shape="forecast",
                  accepts=("Tick",)):
    origin = parent_request(rt).handle
    proposal = assembly(id=name, model_id="fake-haiku", emits=[kind], accepts=list(accepts),
                        schemas={kind: {"type": "object"}}, reward_shapes={kind: shape})
    rt._apply_registrations(origin, Return(origin, {"register": [proposal]}, 0, "ok"))
    assert name in rt.assemblies, rt.registration_feedback


def test_forecast_reward_waits_for_all_predictions_and_is_not_credited_twice(monkeypatch):
    from tests.audit.test_a1_composition import routed

    rt = make_runtime()
    register_work(rt)
    monkeypatch.setattr(rt.provider.target, "complete", lambda req: ModelResponse(
        req.model_id, json.dumps({"forecasts": [
            {"predicate": "wallet_up", "params": {"horizon_events": 1}, "q": 0.8},
            {"predicate": "fill_within", "params": {"horizon_events": 2}, "q": 0.2},
        ]}), 1, 1, "end_turn"))
    handle = routed(rt, "weather-desk", Event("weather", EventKind.TICK, 0, {}, "world"))
    rt.n += 1
    rt.balance_at[:] = [rt.wallet.balance] * (rt.n + 1)
    rt._settle_due_forecasts()
    assert rt.queue.get(handle).status is SettleStatus.PENDING
    rt.n += 1
    rt.balance_at.append(rt.wallet.balance)
    rt._settle_due_forecasts()
    assert rt.queue.history(handle)[-1].score == pytest.approx(0.66)
    rt._settle_due_forecasts()
    assert len(rt.queue.history(handle)) == 1
