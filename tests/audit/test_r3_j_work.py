"""T21: population kinds retain their own accountability and reward contracts."""

from dataclasses import replace
from types import SimpleNamespace

import pytest

from factorylab.charter.charter import seed_charter
from factorylab.charter.measurement import CardSamples, measure_card
from factorylab.charter.windows import MetricWindow
from factorylab.cortex.registration import measured_role, parse_proposals, reward_contracts
from factorylab.cortex.request import Return
from factorylab.runtime.routing import RoutingMixin
from factorylab.runtime.shared import return_channel, work_disclosure


def assembly(**changes):
    return {
        "kind": "assembly", "id": "weather-desk", "model_id": "model",
        "system_prompt": "Predict the weather.", "accepts": ["Tick"],
        "emits": ["WeatherForecast"],
        "schemas": {"WeatherForecast": {"type": "object", "properties": {}}},
        **changes,
    }


def parse(item, *, jail=True):
    return parse_proposals(
        {"register": [item]}, event_kinds=frozenset({"Tick"}),
        known_models=frozenset({"model"}), known_assemblies=frozenset(), tool_jail=jail,
    )


def test_card_names_emitted_kind_without_becoming_producer():
    card = replace(seed_charter().cards[0], answers_for="WeatherForecast")
    assert card.answers_for == "WeatherForecast"
    assert measured_role("WeatherForecast") == "WeatherForecast"


def test_assembly_retains_declared_reward_shape():
    accepted, rejected = parse(assembly(reward_shapes={"WeatherForecast": "forecast"}))
    assert not rejected
    assert accepted[0].reward_shapes == {"WeatherForecast": "forecast"}


@pytest.mark.parametrize("shapes", [
    {"WeatherForecast": "self-scored"}, {"OtherKind": "forecast"},
    {"WeatherForecast": None}, "forecast",
])
def test_invalid_reward_shape_returns_feedback(shapes):
    accepted, rejected = parse(assembly(reward_shapes=shapes))
    assert not accepted
    assert "reward" in rejected[0].reason


def test_custom_forecast_routes_to_consequence_channel():
    runtime = SimpleNamespace(
        assemblies={"desk": SimpleNamespace(spec=SimpleNamespace(
            emits=("WeatherForecast",), reward_shapes={"WeatherForecast": "forecast"}))},
        _higher_tier_universe=lambda _: [],
    )
    assert RoutingMixin._return_channels(runtime, "desk") == {"WeatherForecast": "consequence"}


def test_population_predicate_proposal_is_accepted():
    accepted, rejected = parse({
        "kind": "predicate", "id": "cost-fell", "description": "Costs fell.",
        "code": "def resolve(facts): return facts['costs'][-1] < facts['costs'][0]",
    })
    assert not rejected
    assert accepted[0].id == "cost-fell"


def test_kind_scope_admission_rejects_unregistered_names():
    card = replace(seed_charter().cards[0], answers_for="WeatherForecast")
    card.validate_answers_for(frozenset({"WeatherForecast"}))
    with pytest.raises(ValueError, match="unregistered emitted kind"):
        card.validate_answers_for(frozenset({"DifferentKind"}))


def test_measurement_scopes_custom_emitted_kind_independently():
    card = replace(seed_charter().cards[0], answers_for="WeatherForecast",
                   window=MetricWindow("returns", 1, "role"))
    samples = CardSamples()
    samples.returned(handle="weather", assembly="weather-desk",
                     role=measured_role("WeatherForecast"), window=1,
                     ret=Return("weather", {}, 42, "ok"))
    samples.returned(handle="trade", assembly="trading-desk", role=measured_role("ProducerReturn"),
                     window=1, ret=Return("trade", {}, 999, "ok"))
    assert measure_card(card, samples) == {"WeatherForecast": 42.0}


@pytest.mark.parametrize("scope", ["", None, [], "two kinds", "bad\x00kind"])
def test_invalid_card_scope_is_rejected(scope):
    with pytest.raises(ValueError, match="answers_for"):
        replace(seed_charter().cards[0], answers_for=scope)


def test_new_kind_defaults_to_judged_and_existing_kind_retains_its_declaration():
    assert reward_contracts(("WeatherForecast",)) == {"WeatherForecast": "judged"}
    known = {"WeatherForecast": "forecast"}
    assert reward_contracts(("WeatherForecast",), registered=known) == known
    with pytest.raises(ValueError, match="already declared differently"):
        reward_contracts(("WeatherForecast",), {"WeatherForecast": "judged"}, registered=known)


def test_existing_kind_conflict_reaches_registration_feedback():
    accepted, rejected = parse_proposals(
        {"register": [assembly(reward_shapes={"WeatherForecast": "judged"})]},
        event_kinds=frozenset({"Tick"}), known_models=frozenset({"model"}),
        known_assemblies=frozenset(), known_reward_shapes={"WeatherForecast": "forecast"},
    )
    assert not accepted
    assert "reward shape already declared differently" in rejected[0].reason


@pytest.mark.parametrize("kind,shape", [
    ("ProducerReturn", "forecast"), ("Verdict", "judged"),
    ("MetaVerdict", "exposure"), ("Exposure", "conformity"),
])
def test_seed_reward_shapes_cannot_be_redefined(kind, shape):
    with pytest.raises(ValueError, match="built-in reward shapes"):
        reward_contracts((kind,), {kind: shape})


@pytest.mark.parametrize("shape,higher,expected", [
    ("judged", False, "verdict"), ("forecast", False, "consequence"),
    ("conformity", False, "fast"), ("conformity", True, "conformity"),
    ("exposure", False, "exposure"),
])
def test_each_declared_shape_uses_an_existing_channel(shape, higher, expected):
    assert return_channel("WeatherForecast", shape, higher=higher) == expected


def test_world_disclosure_has_exactly_four_shapes_and_registered_predicates():
    predicates = [{"id": "has-fill", "version": 2, "params": {"horizon_events": 1}}]
    block = work_disclosure({"WeatherForecast": "forecast"}, predicates)
    assert set(block["reward_shapes"]) == {"judged", "forecast", "conformity", "exposure"}
    assert block["default_reward_shape"] == "judged"
    assert "reward stays outside the loop of the thing rewarded" in block["reward_contract"]
    assert block["kind_rewards"] == {"WeatherForecast": "forecast"}
    assert block["predicates"] == predicates
    block["predicates"][0]["params"]["horizon_events"] = 99
    assert predicates[0]["params"]["horizon_events"] == 1


def test_custom_exposure_shapes_share_the_adversarial_router_cap():
    runtime = SimpleNamespace(
        assemblies={"desk": SimpleNamespace(spec=SimpleNamespace(
            emits=("WeatherForecast",), reward_shapes={"WeatherForecast": "exposure"}))},
        ev=SimpleNamespace(adversarial_share=0.15),
    )
    assert RoutingMixin._cap_adversarial(runtime, {"desk": 0.9, "NOOP": 0.1}) == {
        "desk": 0.15, "NOOP": 0.85,
    }


def test_custom_forecast_shapes_receive_consequence_standing_mix():
    runtime = SimpleNamespace(
        assemblies={"desk": SimpleNamespace(spec=SimpleNamespace(
            emits=("WeatherForecast",), reward_shapes={"WeatherForecast": "forecast"}))},
        consequence_mix=0.3, standing=SimpleNamespace(weight=lambda aid: 1),
    )
    mixed = RoutingMixin._mix_with_standing(runtime, {"desk": 0.5, "NOOP": 0.5})
    assert mixed == pytest.approx({"desk": 0.65, "NOOP": 0.35}, abs=1e-15, rel=0)
    assert sum(mixed.values()) == 1.0
