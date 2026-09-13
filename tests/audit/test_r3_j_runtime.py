"""T21 closure requires the real registration, disclosure and settlement paths."""

import json
from dataclasses import replace

from factorylab.charter.measurement import measure_card
from factorylab.charter.windows import MetricWindow
from factorylab.cortex.request import Return
from factorylab.kernel.events import Event, EventKind
from factorylab.kernel.queue import SettleStatus
from factorylab.settlement.vocabulary import PredicateRunner
from factorylab.world.models import ModelResponse
from tests.audit.test_a1_composition import items, make_runtime
from tests.audit.test_r3_j_work import assembly
from tests.runtime.test_child_requests import parent_request


def test_registered_forecast_kind_is_routed_measured_and_rewarded(monkeypatch):
    rt = make_runtime()
    origin = parent_request(rt).handle
    proposal = assembly(model_id="fake-haiku", reward_shapes={"WeatherForecast": "forecast"})
    rt._apply_registrations(origin, Return(origin, {"register": [proposal]}, 0, "ok"))
    assert "weather-desk" in rt.assemblies
    card = replace(rt.charter.cards[1], id="weather-quality", answers_for="WeatherForecast",
                   window=MetricWindow("returns", 1, "role"))
    card.validate_answers_for(frozenset(rt.assemblies["weather-desk"].spec.emits))

    state = rt.routers["Tick"][0]
    monkeypatch.setattr(state.learner, "distribution", lambda feasible: {
        aid: float(aid == "weather-desk") for aid in feasible})
    monkeypatch.setattr(rt.provider.target, "complete", lambda req: ModelResponse(
        req.model_id, json.dumps({"emits": "WeatherForecast", "forecasts": [{
            "predicate": "wallet_up", "params": {"horizon_events": 1}, "q": 0.8,
        }]}), 1, 1, "end_turn"))
    rt._route_with(state, Event("weather-input", EventKind.TICK, rt.clock.now_ns, {}, "world"))
    invocation = items(rt, "invocation")[-1]
    handle = invocation["handle"]
    assert invocation["status"] == "ok"
    assert rt.queue.get(handle).channel == "consequence"
    assert measure_card(card, rt.card_samples) == {"WeatherForecast": 1.0}
    assert any(f.evaluator_id == "weather-desk" for f in rt.book.pending())

    rt.n += 1
    rt.balance_at[:] = [rt.wallet.balance] * (rt.n + 1)
    rt._settle_due_forecasts()
    assert rt.queue.get(handle).status is SettleStatus.SETTLED
    reward = rt.queue.history(handle)[-1]
    assert reward.channel == "consequence"
    assert abs(reward.score - (1 - 0.8 ** 2)) < 1e-12
    rt._deliver_returns()
    assert rt.delivered_seen[state.learner.id] > 0


def test_runtime_world_block_discloses_the_four_shapes():
    rt = make_runtime()
    work = rt._world_block()["work"]
    assert set(work["reward_shapes"]) == {"judged", "forecast", "conformity", "exposure"}
    assert work["default_reward_shape"] == "judged"
    assert "reward stays outside the loop of the thing rewarded" in work["reward_contract"]


def test_runtime_registers_and_discloses_a_preflighted_predicate(monkeypatch):
    rt = make_runtime()
    origin = parent_request(rt).handle
    rt._close_price_window()
    # Registration lifecycle is independent of the host-jail test in test_r3_j_predicates.
    rt.tool_jail_available = True
    monkeypatch.setattr(PredicateRunner, "run", lambda self, code, facts: (False, None))
    rt._apply_registrations(origin, Return(origin, {"register": [{
        "kind": "predicate", "id": "has-fill", "description": "A fill occurred.",
        "code": "def resolve(facts): return facts['fills'] > 0",
    }]}, 0, "ok"))
    assert rt.stats.registrations_accepted == 1
    assert any(p["id"] == "has-fill" and p["version"] == 1
               for p in rt._world_block()["work"]["predicates"])
    assert items(rt, "predicate.preflight")[-1]["value"] is False
