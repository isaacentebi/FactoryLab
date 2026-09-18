"""A11: registrable observations (seat 1, finding 2; seat 6's open question).

The reproduction: an approved card naming `downside_variance` was admitted, then
yielded `price.unparsed` for an unknown observation, so the population's own
risk criterion received no measured penalty — while launch validation rejected
the very same name. Measurement stopped at the architect's twenty-two; edition 2's
``cost_per_attempt`` (C6) and edition 3's ``avoidably_unresolved_share`` (C3)
make twenty-four seeds.
"""

import json
from types import SimpleNamespace

from factorylab.runtime.pricing import MeasureWindow
from tests.runtime.test_loop import (
    _consequence_judge,
    _consequence_produce,
    _consequence_runtime,
)

DOWNSIDE = (
    "def observe(facts):\n"
    "    costs = facts.get('costs') or [0]\n"
    "    mean = sum(costs) / len(costs)\n"
    "    below = [c for c in costs if c < mean]\n"
    "    return sum((mean - c) ** 2 for c in below) / len(costs) / 1_000_000\n"
)


# --- what a registered observation may see ------------------------------------


def test_no_identity_the_runtime_knows_survives_into_an_observations_facts():
    """A registered observation is population code: it may read numbers, not topology."""
    runtime = _consequence_runtime()
    seen: list[dict] = []
    runtime.observation_runner = SimpleNamespace(
        run=lambda code, facts: (seen.append(facts), (0.5, None))[1]
    )
    runtime.registered_observations["downside-variance"] = {
        "description": "Semivariance.", "units": "u", "unit_range": [0.0, 1.0],
        "code": DOWNSIDE, "version": 1, "provenance": "population", "history": [1],
        "trial_window": runtime.window.index,
    }
    handle, event = _consequence_produce(runtime)
    _consequence_judge(runtime, event, "eval-a")
    runtime._close_price_window()
    assert seen, "the registered observation was never measured"

    identities = {handle, *runtime.assemblies, *runtime.prices.prices,
                  *runtime.handle_to_assembly, *runtime.handle_to_assembly.values()}
    identities |= {f"assembly:{a}" for a in runtime.assemblies}
    identities = {i for i in identities if isinstance(i, str) and i}

    def strings(value, path="facts"):
        if isinstance(value, str):
            yield path, value
        elif isinstance(value, dict):
            for key, sub in value.items():
                yield from strings(key, f"{path}.<key>")
                yield from strings(sub, f"{path}.{key}")
        elif isinstance(value, list):
            for index, sub in enumerate(value):
                yield from strings(sub, f"{path}[{index}]")

    for facts in seen:
        # the fact names themselves are the published vocabulary; their contents
        # are the strong form: every value is a number, so nothing can hide in one
        found = [hit for key, value in facts.items() for hit in strings(value, f"facts.{key}")]
        assert not found, f"a window fact carried text: {found[:3]}"
        for identity in identities:
            assert identity not in json.dumps(facts), identity


# --- registration runs the code in the jail -----------------------------------


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


# --- a card may then name it --------------------------------------------------
