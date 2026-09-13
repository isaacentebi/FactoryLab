"""T21 closure requires the real registration, disclosure and settlement paths."""

import json
from dataclasses import replace

import pytest

from factorylab.charter.measurement import measure_card
from factorylab.charter.windows import MetricWindow
from factorylab.cortex.request import Return
from factorylab.kernel.events import Event, EventKind
from factorylab.kernel.queue import SettleStatus
from factorylab.runtime.resume import restore_runtime, runtime_state
from factorylab.runtime.shared import PredicateRunner
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


def test_forecast_kind_card_predicate_and_reward_survive_resume(monkeypatch):
    rt = make_runtime()
    origin = parent_request(rt).handle
    rt._close_price_window()
    rt.tool_jail_available = True
    monkeypatch.setattr(PredicateRunner, "run", lambda self, code, facts: (
        code.endswith("True"), None))
    rt._apply_registrations(origin, Return(origin, {"register": [{
        "kind": "predicate", "id": "window-claim", "description": "Window claim.",
        "code": "def resolve(facts): return True",
    }, assembly(model_id="fake-haiku", reward_shapes={"WeatherForecast": "forecast"})]},
        0, "ok"))
    assert rt.stats.registrations_accepted == 2
    card = replace(rt.charter.cards[1], id="weather-quality", answers_for="WeatherForecast",
                   window=MetricWindow("returns", 1, "role"))
    from factorylab.charter.measurement import preflight_measurement

    preflight_measurement(card, rt.observations, registered_kinds=frozenset(rt._kind_rewards()))
    rt.charter = replace(rt.charter, cards=(*rt.charter.cards, card))
    state = rt.routers["Tick"][0]
    monkeypatch.setattr(state.learner, "distribution", lambda feasible: {
        aid: float(aid == "weather-desk") for aid in feasible})
    monkeypatch.setattr(type(rt.provider.target), "complete", lambda self, req: ModelResponse(
        req.model_id, json.dumps({"emits": "WeatherForecast", "forecasts": [{
            "predicate": "window-claim", "params": {"horizon_events": 1}, "q": 0.8,
        }]}), 1, 1, "end_turn"))
    rt._route_with(state, Event("weather-input", EventKind.TICK, rt.clock.now_ns, {}, "world"))
    handle = items(rt, "invocation")[-1]["handle"]
    assert rt.book.pending(), (items(rt, "invocation")[-1], rt.registration_feedback)
    forecast = next(f for f in rt.book.pending() if f.evaluator_id == "weather-desk")
    assert forecast.predicate.version == 1
    assert rt.queue.get(handle).channel == "consequence"
    # Replacing the definition before the outcome cannot rewrite the sealed bet.
    rt._apply_registrations(origin, Return(origin, {"register": [{
        "kind": "predicate", "id": "window-claim", "description": "Changed claim.",
        "code": "def resolve(facts): return False",
    }]}, 0, "ok"))
    assert rt.predicates.get("window-claim").version == 2
    restored = make_runtime()
    restore_runtime(restored, json.loads(json.dumps(runtime_state(rt))))
    assert restored._return_channels("weather-desk") == {"WeatherForecast": "consequence"}
    assert restored._world_block()["work"]["kind_rewards"]["WeatherForecast"] == "forecast"
    assert restored.predicates.get("window-claim", 1).code.endswith("True")
    assert measure_card(card, restored.card_samples) == {"WeatherForecast": 1.0}
    restored.n += 1
    restored.balance_at[:] = [restored.wallet.balance] * (restored.n + 1)
    restored._settle_due_forecasts()
    assert restored.queue.get(handle).status is SettleStatus.SETTLED
    assert restored.queue.history(handle)[-1].score == pytest.approx(0.96)
    restored._deliver_returns()
    assert restored.delivered_seen[state.learner.id] > 0
    assert restored.wallet.check_conservation()


def test_custom_kind_measurement_preflight_is_executable():
    from factorylab.charter.measurement import preflight_measurement

    rt = make_runtime()
    register_work(rt)
    card = replace(rt.charter.cards[1], answers_for="WeatherForecast",
                   window=MetricWindow("returns", 1, "role"))
    preflight_measurement(card, rt.observations, registered_kinds=frozenset(rt._kind_rewards()))


def test_population_predicate_journal_is_read_only():
    from factorylab.runtime.resume import _read_only

    assert _read_only("predicate.run")


def register_work(rt, *, name="weather-desk", kind="WeatherForecast", shape="forecast",
                  accepts=("Tick",)):
    origin = parent_request(rt).handle
    proposal = assembly(id=name, model_id="fake-haiku", emits=[kind], accepts=list(accepts),
                        schemas={kind: {"type": "object"}}, reward_shapes={kind: shape})
    rt._apply_registrations(origin, Return(origin, {"register": [proposal]}, 0, "ok"))
    assert name in rt.assemblies, rt.registration_feedback


def test_custom_conformity_is_anchored_to_custom_forecast_outcome(monkeypatch):
    from tests.audit.test_a1_composition import routed

    rt = make_runtime()
    register_work(rt)
    register_work(rt, name="weather-check", kind="WeatherCheck", shape="conformity",
                  accepts=("WeatherForecast",))
    response = {"forecasts": [{"predicate": "wallet_up", "params": {"horizon_events": 1},
                               "q": 0.8}]}
    monkeypatch.setattr(rt.provider.target, "complete", lambda req: ModelResponse(
        req.model_id, json.dumps(response), 1, 1, "end_turn"))
    root = routed(rt, "weather-desk", Event("weather", EventKind.TICK, 0, {}, "world"))
    response.clear()
    response["conformity"] = 0.9
    checker = routed(rt, "weather-check", rt.return_events[root])
    assert rt.queue.get(checker).channel == "fast"
    assert rt.queue.get(checker).status is SettleStatus.PENDING
    assert rt.return_events[checker].kind == "WeatherCheck"
    rt.n += 1
    rt.balance_at[:] = [rt.wallet.balance] * (rt.n + 1)
    rt._settle_due_forecasts()
    assert rt.queue.get(checker).status is SettleStatus.SETTLED
    assert rt.queue.history(checker)[-1].score == pytest.approx(0.19)


@pytest.mark.parametrize("shape", ["forecast", "conformity"])
def test_custom_judging_kinds_cannot_route_to_their_own_return(shape):
    rt = make_runtime()
    register_work(rt, shape=shape, accepts=("WeatherForecast",))
    origin = parent_request(rt).handle
    rt.handle_to_assembly[origin] = "weather-desk"
    rt._emit("WeatherForecast", {"about_handle": origin, "outputs": {}, "status": "ok"})
    assert "weather-desk" not in rt._universe_for("WeatherForecast", rt.return_events[origin])


def test_custom_forecast_child_uses_the_forecast_channel(monkeypatch):
    from factorylab.cortex.request import ChildRequest

    rt = make_runtime()
    register_work(rt)
    parent = parent_request(rt)
    monkeypatch.setattr(rt.provider.target, "complete", lambda req: ModelResponse(
        req.model_id, json.dumps({"forecasts": [{"predicate": "wallet_up",
            "params": {"horizon_events": 1}, "q": 0.8}]}), 1, 1, "end_turn"))
    result, cost = rt._invoke_child("seed-decider", parent, ChildRequest(
        "weather-desk", "Predict.", {}, {"type": "object"}), parent.cost_ceiling)
    handle = items(rt, "request.child")[-1]["handle"]
    assert result["result"]["status"] == "ok" and cost > 0
    assert rt.queue.get(handle).channel == "consequence"
    assert handle in rt.forecast_returns
    rt.n += 1
    rt.balance_at[:] = [rt.wallet.balance] * (rt.n + 1)
    rt._settle_due_forecasts()
    assert rt.queue.get(handle).status is SettleStatus.SETTLED


def test_declared_shape_cannot_change_after_retirement_and_resume():
    rt = make_runtime()
    register_work(rt)
    rt.retired_assemblies.add("weather-desk")
    resumed = make_runtime()
    restore_runtime(resumed, runtime_state(rt))
    proposal = assembly(id="weather-new", model_id="fake-haiku",
                        schemas={"WeatherForecast": {"type": "object"}},
                        reward_shapes={"WeatherForecast": "exposure"})
    resumed._apply_registrations("decision-0",
                                Return("unused", {"register": [proposal]}, 0, "ok"))
    assert "weather-new" not in resumed.assemblies
    assert "already declared differently" in resumed.registration_feedback[-1]["reason"]


def test_seed_evaluator_can_seal_a_registered_predicate(monkeypatch):
    from tests.audit.test_a1_composition import routed, subject

    rt = make_runtime()
    origin = parent_request(rt).handle
    rt._close_price_window()
    rt.tool_jail_available = True
    monkeypatch.setattr(PredicateRunner, "run", lambda self, code, facts: (True, None))
    rt._apply_registrations(origin, Return(origin, {"register": [{
        "kind": "predicate", "id": "public-claim", "description": "A public claim.",
        "code": "def resolve(facts): return True",
    }]}, 0, "ok"))
    event = subject(rt)
    monkeypatch.setattr(rt.provider.target, "complete", lambda req: ModelResponse(
        req.model_id, json.dumps({"verdict": 0.8, "payoff": 0.2, "rationale": "Evidence.",
                                 "forecasts": [{"predicate": "public-claim",
                                  "params": {"horizon_events": 1}, "q": 0.7}]}),
        1, 1, "end_turn"))
    handle = routed(rt, "eval-a", event)
    assert rt.queue.get(handle).channel == "conformity"
    prediction = next(f for f in rt.book.pending() if f.predicate_id == "public-claim")
    assert prediction.predicate.version == 1
    rt.n += 1
    rt.balance_at[:] = [rt.wallet.balance] * (rt.n + 1)
    rt._settle_due_forecasts()
    assert rt.queue.history(prediction.handle)[-1].score == pytest.approx(0.91)


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


def test_failed_population_resolution_is_unscored(monkeypatch):
    from tests.audit.test_a1_composition import routed

    rt = make_runtime()
    register_work(rt)
    rt._close_price_window()
    rt.tool_jail_available = True
    monkeypatch.setattr(PredicateRunner, "run", lambda self, code, facts: (True, None))
    rt._apply_registrations("decision-0", Return("decision-0", {"register": [{
        "kind": "predicate", "id": "fragile-claim", "description": "Missing evidence.",
        "code": "def resolve(facts): return True",
    }]}, 0, "ok"))
    monkeypatch.setattr(rt.provider.target, "complete", lambda req: ModelResponse(
        req.model_id, json.dumps({"forecasts": [{"predicate": "fragile-claim",
             "params": {"horizon_events": 1}, "q": 0.8}]}), 1, 1, "end_turn"))
    handle = routed(rt, "weather-desk", Event("weather", EventKind.TICK, 0, {}, "world"))
    monkeypatch.setattr(PredicateRunner, "run", lambda self, code, facts: (None, "timeout"))
    rt.n += 1
    rt.balance_at[:] = [rt.wallet.balance] * (rt.n + 1)
    rt._settle_due_forecasts()
    assert rt.queue.get(handle).status is SettleStatus.CENSORED
    event = items(rt, "forecast.seal")[-1]
    assert rt.queue.get(event["handle"]).status is SettleStatus.CENSORED
    assert "fragile-claim" not in rt.baseline._PrevalenceBaseline__counts


def test_custom_card_scope_is_admitted_through_governance(monkeypatch):
    from dataclasses import asdict

    rt = make_runtime()
    register_work(rt)
    card = replace(rt.charter.cards[0], id="weather-cost", answers_for="WeatherForecast",
                   acceptable_region="below 100", window=MetricWindow("returns", 1, "role"))
    voted = []
    monkeypatch.setattr(rt, "_hold_vote", lambda amendment, committee: voted.append(amendment))
    rt._propose_amendment("decision-0", {
        "id": "weather-policy", "add": [asdict(card)],
        "predicted_effect": {"card_id": "weather-cost", "direction": "decrease", "window": 1},
    })
    assert voted[0].add[0].answers_for == "WeatherForecast"
    with pytest.raises(ValueError, match="answers_for"):
        rt._propose_amendment("decision-0", {
            "id": "unknown-policy", "add": [{**asdict(card), "answers_for": "UnknownWork"}],
            "predicted_effect": {"card_id": "weather-cost", "direction": "decrease", "window": 1},
        })


def test_custom_exposure_is_rewarded_for_the_judges_failed_prediction(monkeypatch):
    from tests.audit.test_a1_composition import routed

    rt = make_runtime()
    register_work(rt, shape="exposure")
    response = {"action": "hold", "payoff": 0.1}
    monkeypatch.setattr(rt.provider.target, "complete", lambda req: ModelResponse(
        req.model_id, json.dumps(response), 1, 1, "end_turn"))
    handle = routed(rt, "weather-desk", Event("weather", EventKind.TICK, 0, {}, "world"))
    assert handle in rt.pending_exposure
    assert rt.queue.get(handle).channel == "exposure"
    response.clear()
    response.update(verdict=0.8, payoff=0.9, rationale="A judgement.", forecasts=[])
    routed(rt, "eval-a", rt.return_events[handle])
    rt.n += 1
    rt.balance_at[:] = [rt.wallet.balance] * (rt.n + 1)
    rt._settle_due_forecasts()
    assert rt.queue.get(handle).status is SettleStatus.SETTLED
    assert rt.queue.history(handle)[-1].score == 1.0


def test_forecast_reward_pays_its_emitted_kinds_card_penalty(monkeypatch):
    from tests.audit.test_a1_composition import routed

    rt = make_runtime()
    register_work(rt)
    card = replace(rt.charter.cards[0], id="weather-cost", answers_for="WeatherForecast",
                   acceptable_region="below 1", window=MetricWindow("returns", 1, "role"))
    rt.charter = replace(rt.charter, cards=(*rt.charter.cards, card))
    rt._derive_regions()
    monkeypatch.setattr(rt.provider.target, "complete", lambda req: ModelResponse(
        req.model_id, json.dumps({"forecasts": [{"predicate": "wallet_up",
             "params": {"horizon_events": 1}, "q": 0.2}]}), 1, 1, "end_turn"))
    handle = routed(rt, "weather-desk", Event("weather", EventKind.TICK, 0, {}, "world"))
    rt._close_price_window()
    rt.n += 1
    rt.balance_at[:] = [rt.wallet.balance] * (rt.n + 1)
    rt._settle_due_forecasts()
    penalty = next(p for p in items(rt, "price.penalty") if p["handle"] == handle)
    assert penalty["raw"] == pytest.approx(0.96)
    assert penalty["penalty"] > 0
    assert any(p["card_id"] == "weather-cost" for p in penalty["terms"])
    assert rt.queue.history(handle)[-1].score < penalty["raw"]


def test_pruned_polymorphic_forecast_keeps_its_emitted_measurement_scope(monkeypatch):
    from tests.audit.test_a1_composition import routed

    rt = make_runtime()
    origin = parent_request(rt).handle
    rt._apply_registrations(origin, Return(origin, {"register": [assembly(
        model_id="fake-haiku", emits=["ProducerReturn", "WeatherForecast"],
        reward_shapes={"WeatherForecast": "forecast"})]}, 0, "ok"))
    monkeypatch.setattr(rt.provider.target, "complete", lambda req: ModelResponse(
        req.model_id, json.dumps({"emits": "WeatherForecast", "forecasts": [{
            "predicate": "wallet_up", "params": {"horizon_events": 1}, "q": 0.2}]}),
        1, 1, "end_turn"))
    handle = routed(rt, "weather-desk", Event("weather", EventKind.TICK, 0, {}, "world"))
    assert rt.return_kinds[handle] == "WeatherForecast"
    rt.card_samples.returns.clear()
    rt.n += 1
    rt.balance_at[:] = [rt.wallet.balance] * (rt.n + 1)
    rt._settle_due_forecasts()
    assert rt.card_samples.forecasts[-1]["role"] == "WeatherForecast"
