"""A11: registrable observations (seat 1, finding 2; seat 6's open question).

The reproduction: an approved card naming `downside_variance` was admitted, then
yielded `price.unparsed` for an unknown observation, so the population's own
risk criterion received no measured penalty — while launch validation rejected
the very same name. Measurement stopped at the architect's twenty-two.
"""

import json
from types import SimpleNamespace

import pytest

from factorylab.charter.charter import MetricCard
from factorylab.charter.measurement import measurement_catalogue, preflight_card
from factorylab.charter.windows import MetricWindow
from factorylab.cortex.registration import ObservationProposal, Rejected, parse_proposals
from factorylab.cortex.request import Return
from factorylab.cortex.tools import ObservationRunner
from factorylab.runtime.observations import (
    CATALOGUE,
    SEED_IDS,
    ObservationBook,
    observation_for,
    seed_book,
    window_facts,
)
from factorylab.runtime.pricing import MeasureWindow
from tests.cortex.test_jail import require_jail
from tests.runtime.test_loop import _consequence_produce, _consequence_runtime

DOWNSIDE = (
    "def observe(facts):\n"
    "    costs = facts.get('costs') or [0]\n"
    "    mean = sum(costs) / len(costs)\n"
    "    below = [c for c in costs if c < mean]\n"
    "    return sum((mean - c) ** 2 for c in below) / len(costs) / 1_000_000\n"
)


def _proposal(**over):
    return {"kind": "observation", "id": "downside-variance",
            "description": "Semivariance of well-formed return costs.",
            "unit": "micro-USD squared", "range": [0.0, 1.0], "code": DOWNSIDE, **over}


def _parse(item, jail=True):
    return parse_proposals({"register": [item]}, event_kinds=frozenset(),
                           known_models=frozenset(), known_assemblies=frozenset({"seed-decider"}),
                           tool_jail=jail, seed_observations=SEED_IDS)


# --- the proposal contract ----------------------------------------------------


def test_a_well_formed_observation_proposal_is_accepted():
    accepted, rejected = _parse(_proposal())
    assert not rejected
    assert accepted == [ObservationProposal(
        "downside-variance", "Semivariance of well-formed return costs.",
        "micro-USD squared", (0.0, 1.0), DOWNSIDE,
    )]


@pytest.mark.parametrize("item, reason", [
    (_proposal(id="Not a slug"), "id must be a slug of 2-48 chars"),
    # A seed id is not even slug-shaped, so the vocabulary is doubly protected.
    (_proposal(id="cost_per_return"), "id must be a slug of 2-48 chars"),
    (_proposal(description=""), "description is required"),
    (_proposal(unit=""), "unit is required and is at most 64 chars"),
    (_proposal(range=[1.0, 0.0]), "range must have lo below hi"),
    (_proposal(range=[0.0]), "range must be [lo, hi]"),
    (_proposal(range=[0.0, float("inf")]), "range bounds must be finite numbers"),
    # an integer no float can hold is a rejection, not an OverflowError out of math.isfinite
    (_proposal(range=[0, 10 ** 400]), "range bounds must be finite numbers"),
    (_proposal(code="def other(facts): return 1"), "code must define observe(facts)"),
    (_proposal(code=1), "code must be a string"),
])
def test_malformed_observation_proposals_carry_their_reason(item, reason):
    accepted, rejected = _parse(item)
    assert not accepted and rejected == [Rejected(0, reason)]


def test_a_bound_no_float_can_hold_is_rejected_and_the_runtime_survives():
    runtime = _consequence_runtime()
    handle, _event = _consequence_produce(runtime)
    runtime._apply_registrations(
        handle, Return(handle, {"register": [_proposal(range=[0, 10 ** 400])]}, 0, "ok"),
    )
    assert not runtime.registered_observations
    assert any("range bounds must be finite numbers" in f["reason"]
               for f in runtime.registration_feedback)
    # the world goes on: the next decision still lands
    assert _consequence_produce(runtime)[0] != handle


def test_no_jail_means_no_registrable_measurement():
    accepted, rejected = _parse(_proposal(), jail=False)
    assert not accepted and rejected == [Rejected(0, "no jail on this host")]


# --- the seed catalogue is registered the same way ----------------------------


def test_the_twenty_two_seeds_are_registered_contracts_with_a_version():
    runtime = _consequence_runtime()
    for observation in CATALOGUE:
        contract = runtime.registry.get(f"observation:{observation.id}")
        assert contract.kind == "observation" and contract.version == 1
        assert contract.provenance == "seed"
    assert len(SEED_IDS) == 22


def test_the_book_reads_seeds_and_registrations_through_one_vocabulary():
    book = ObservationBook({"downside-variance": {
        "description": "d", "units": "u", "unit_range": [0.0, 2.0], "code": DOWNSIDE,
        "version": 3, "provenance": "population",
    }})
    registered = book.get("  Downside-Variance ")
    assert registered.registered and registered.version == 3 and registered.scale == 2.0
    assert book.get("cost_per_return") is observation_for("cost_per_return")
    assert book.get("nothing-here") is None
    assert len(book.all()) == 23
    assert observation_for("downside-variance") is None  # the seeds alone know nothing of it


def test_the_public_catalogue_publishes_registered_observations_beside_the_seeds():
    book = ObservationBook({"downside-variance": {
        "description": "Semivariance.", "units": "u", "unit_range": [0.0, 1.0],
        "code": DOWNSIDE, "version": 1, "provenance": "population",
    }})
    rows = {row["id"]: row for row in measurement_catalogue(book)}
    assert rows["downside-variance"]["provenance"] == "population"
    assert rows["downside-variance"]["window_kinds"] == ["windows"]
    assert rows["cost_per_return"]["provenance"] == "seed"


# --- what a registered observation may see ------------------------------------


def test_window_facts_are_the_public_window_and_nothing_private():
    window = MeasureWindow(3, 1000, costs=[10, 20], revision_handles={"h"})
    window.decisions["h"] = {"role": "producer", "cost": 10}
    window.closed_values = {"card": 1.0}
    facts = window_facts(window)
    assert "decisions" not in facts and "closed_values" not in facts
    assert "closed_regions" not in facts
    assert facts["costs"] == [10, 20] and facts["index"] == 3
    json.dumps(facts)  # the contract is JSON: the code reads it from stdin


# --- registration runs the code in the jail -----------------------------------


def test_the_runner_measures_a_window_under_the_tool_limits():
    require_jail()
    runner = ObservationRunner()
    facts = window_facts(MeasureWindow(1, 1000, costs=[0, 0, 100]))
    value, error = runner.run(DOWNSIDE, facts)
    assert error is None and value == pytest.approx(0.000741, rel=1e-3)


@pytest.mark.parametrize("code, reason", [
    ("def observe(facts):\n    return None\n", "unsupported"),
    ("def observe(facts):\n    raise ValueError('no')\n", "exit"),
    ("def observe(facts):\n    return float('inf')\n", "exit"),
    ("def observe(facts):\n    while True:\n        pass\n", "timeout"),
    ("def observe(facts):\n"
     "    import urllib.request\n"
     "    return urllib.request.urlopen('http://example.com').status\n", "exit"),
])
def test_an_observation_that_cannot_measure_says_why(code, reason):
    require_jail()
    value, error = ObservationRunner(timeout_s=2).run(code, {"costs": [1]})
    assert value is None and reason in error


def _registered_runtime():
    require_jail()
    runtime = _consequence_runtime()
    runtime._manage_reserve_window()
    handle, _event = _consequence_produce(runtime)
    runtime._close_price_window()  # A11 preflights against the last closed window
    return runtime, handle


def test_registration_preflights_on_the_last_closed_window_and_versions_the_registry():
    runtime, handle = _registered_runtime()
    runtime._register(handle, ObservationProposal(
        "downside-variance", "Semivariance.", "u", (0.0, 1.0), DOWNSIDE))
    entry = runtime.registered_observations["downside-variance"]
    assert entry["version"] == 1 and entry["provenance"] == "population"
    contract = runtime.registry.get("observation:downside-variance")
    assert contract.kind == "observation" and contract.provenance == handle
    assert runtime.observations.get("downside-variance").registered

    # A second registration of the same id is a new version, not a refusal.
    runtime._register(handle, ObservationProposal(
        "downside-variance", "Semivariance, squared costs.", "u", (0.0, 2.0), DOWNSIDE))
    assert runtime.registered_observations["downside-variance"]["version"] == 2
    assert runtime.registry.get("observation:downside-variance").version == 2
    assert runtime.observations.get("downside-variance").scale == 2.0


def test_a_seed_observation_cannot_be_redefined_by_the_population():
    runtime, handle = _registered_runtime()
    with pytest.raises(ValueError, match="seed observation ids cannot be redefined"):
        runtime._register(handle, ObservationProposal(
            "cost_per_return", "Mine now.", "u", (0.0, 1.0), DOWNSIDE))
    assert "cost_per_return" not in runtime.registered_observations


def test_a_measurement_that_fails_its_preflight_is_refused_with_the_reason():
    runtime, handle = _registered_runtime()
    with pytest.raises(ValueError, match="observation preflight failed"):
        runtime._register(handle, ObservationProposal(
            "bad-one", "Never measures.", "u", (0.0, 1.0),
            "def observe(facts):\n    raise KeyError('nope')\n"))
    assert "bad-one" not in runtime.registered_observations


def test_a_measurement_outside_its_declared_range_is_refused_at_registration():
    runtime, handle = _registered_runtime()
    with pytest.raises(ValueError, match="outside its declared range"):
        runtime._register(handle, ObservationProposal(
            "out-of-range", "Always five.", "u", (0.0, 1.0),
            "def observe(facts):\n    return 5.0\n"))
    assert "out-of-range" not in runtime.registered_observations


def test_a_value_that_leaves_its_declared_range_is_unsupported_never_clamped():
    runtime = _consequence_runtime()
    # a registration whose code drifts out of the range it declared, after admission
    runtime.observation_runner = SimpleNamespace(run=lambda code, facts: (5.0, None))
    runtime.registered_observations["downside-variance"] = {
        "description": "Semivariance.", "units": "u", "unit_range": [0.0, 1.0],
        "code": DOWNSIDE, "version": 1, "provenance": "population", "history": [1],
    }
    book = runtime.observations
    assert book.value(book.get("downside-variance"), MeasureWindow(1, 1000, costs=[1])) is None
    runtime._close_price_window()
    assert "downside-variance" not in runtime.stats.last_window_values  # not 1.0, not 5.0
    marker = runtime.ledger.append({"kind": "test.marker"})
    runtime.termination.kill("test")
    items = [runtime.ledger.decrypt_item(i) for i in range(marker)]
    rejected = [i for i in items if i["kind"] == "observation.out_of_range"]
    assert rejected and rejected[0]["observation"] == "downside-variance"
    assert rejected[0]["value"] == 5.0 and rejected[0]["range"] == [0.0, 1.0]


def test_one_runtimes_registrations_are_invisible_to_another():
    first, second = _consequence_runtime(), _consequence_runtime()
    first.registered_observations["downside-variance"] = {
        "description": "Semivariance.", "units": "u", "unit_range": [0.0, 1.0],
        "code": DOWNSIDE, "version": 1, "provenance": "population",
    }
    assert first.observations.get("downside-variance") is not None
    assert second.observations.get("downside-variance") is None
    assert len(second.observations.all()) == len(CATALOGUE)
    assert observation_for("downside-variance") is None
    assert "downside-variance" not in {row["id"] for row in measurement_catalogue()}
    # and no default path hands out a book anyone else can register into
    borrowed = seed_book()
    borrowed.registered["downside-variance"] = dict(first.registered_observations[
        "downside-variance"])
    assert not seed_book().registered
    assert len(measurement_catalogue()) == len(CATALOGUE)


def test_nothing_is_registrable_before_a_window_has_closed():
    require_jail()
    runtime = _consequence_runtime()
    runtime._manage_reserve_window()
    handle, _event = _consequence_produce(runtime)
    with pytest.raises(ValueError, match="no closed window to preflight"):
        runtime._register(handle, ObservationProposal(
            "downside-variance", "Semivariance.", "u", (0.0, 1.0), DOWNSIDE))


# --- a card may then name it --------------------------------------------------


def _card(observation, card_id="risk-card"):
    return MetricCard(card_id, "care with scarce resources", "downside risk", "u",
                      MetricWindow("windows", 1, None), "below 0.2", observation, "producer")


def test_an_amendment_naming_an_unregistered_observation_is_refused_with_a_reason():
    with pytest.raises(ValueError, match="observation: unregistered observation"):
        preflight_card(_card("downside_variance"))
    runtime = _consequence_runtime()
    with pytest.raises(ValueError, match="unregistered observation"):
        preflight_card(_card("downside_variance"), runtime.observations)


def test_a_registered_observation_gives_the_card_a_region_and_a_measured_value():
    runtime, handle = _registered_runtime()
    runtime._register(handle, ObservationProposal(
        "downside-variance", "Semivariance.", "u", (0.0, 1.0), DOWNSIDE))
    card = _card("downside-variance")
    preflight_card(card, runtime.observations)  # no longer refused

    from factorylab.runtime.cards import region_for

    region = region_for(card, rolling={}, observations=runtime.observations)
    assert region is not None and region.hi == 0.2 and region.scale == 1.0

    from factorylab.charter.measurement import measure_card

    values = measure_card(card, runtime.card_samples, runtime.observations)
    assert values and all(isinstance(v, float) for v in values.values())


def test_a_priced_card_on_a_registered_observation_is_no_longer_unparsed():
    runtime, handle = _registered_runtime()
    runtime._register(handle, ObservationProposal(
        "downside-variance", "Semivariance.", "u", (0.0, 1.0), DOWNSIDE))
    from factorylab.charter.charter import Charter

    card = _card("downside-variance")
    runtime.charter = Charter(runtime.charter.edition, runtime.charter.norms,
                              (*runtime.charter.cards, card))
    runtime._derive_regions()
    assert "risk-card" in runtime.regions and "risk-card" in runtime.priced
    marker = runtime.ledger.append({"kind": "test.marker"})
    runtime.termination.kill("test")
    items = [runtime.ledger.decrypt_item(i) for i in range(marker)]
    assert not [i for i in items if i["kind"] == "price.unparsed"
                and i["card_id"] == "risk-card"]


def test_the_world_block_tells_the_population_how_to_register_a_measurement():
    runtime = _consequence_runtime()
    block = runtime._world_block()
    assert block["proposal_shapes"]["observation"]["kind"] == "observation"
    assert "observe(facts)" in block["proposal_shapes"]["observation"]["code"]
    assert "costs" in block["observation_facts"]
    assert "tool jail" in block["scoring"]["observations"]
    assert {"observation", "learner"} <= set(
        runtime._register_schema()["items"]["properties"]["kind"]["enum"]
    )
