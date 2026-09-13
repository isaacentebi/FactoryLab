"""T60: a card's accountability scope is the exact spelling its kind was admitted under.

Role aliases are lower-case names of seed populations. A custom kind whose name
differs from an alias only in case must not be able to claim that alias's
measurement scope, so the alias spellings are reserved at registration and a
card's emitted-kind scope keeps its own spelling.
"""

from dataclasses import replace

import pytest

from factorylab.charter.charter import seed_charter
from factorylab.charter.measurement import CardSamples, measure_card
from factorylab.charter.windows import MetricWindow
from factorylab.cortex.registration import measured_role, output_contracts
from factorylab.cortex.request import Return
from tests.audit.test_r3_j_work import assembly, parse

RESERVED = ("Producer", "PRODUCER", "producer", "Evaluator", "META", "Antagonist", "ALL", "all")


@pytest.mark.parametrize("name", RESERVED)
def test_alias_spellings_cannot_be_registered_as_emitted_kinds(name):
    accepted, rejected = parse(assembly(emits=[name], schemas={name: {
        "type": "object", "properties": {}}}))
    assert not accepted
    assert "reserved" in rejected[0].reason


@pytest.mark.parametrize("name", RESERVED)
def test_reserved_names_are_refused_by_the_contract_builder(name):
    with pytest.raises(ValueError, match="reserved"):
        output_contracts([name], {name: {"type": "object", "properties": {}}})


def test_an_admitted_kind_card_cannot_silently_measure_another_population():
    card = replace(seed_charter().cards[0], answers_for="Producer",
                   window=MetricWindow("returns", 1, "role"))
    assert card.answers_for == "Producer"
    samples = CardSamples()
    samples.returned(handle="seed", assembly="seed-decider", role=measured_role("ProducerReturn"),
                     window=1, ret=Return("seed", {}, 999, "ok"))
    assert measure_card(card, samples) == {}
    with pytest.raises(ValueError, match="unregistered emitted kind"):
        card.validate_answers_for(frozenset({"ProducerReturn"}))


def test_an_uppercase_all_is_not_a_population_wide_scope():
    card = replace(seed_charter().cards[1], answers_for="ALL")
    assert card.answers_for == "ALL"
    with pytest.raises(ValueError, match="unregistered emitted kind"):
        card.validate_answers_for(frozenset({"ProducerReturn"}))


@pytest.mark.parametrize("alias,expected", [
    ("producer", "producer"), ("evaluator", "evaluator"), ("meta", "meta"),
    ("antagonist", "antagonist"), ("all", "all"), ("Verdict", "evaluator"),
    ("MetaVerdict", "meta"), ("Exposure", "antagonist"), ("ProducerReturn", "producer"),
    ("WeatherForecast", "WeatherForecast"),
])
def test_alias_and_seed_kind_scopes_are_unchanged(alias, expected):
    assert replace(seed_charter().cards[0], answers_for=alias).answers_for == expected
