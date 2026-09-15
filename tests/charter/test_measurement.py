"""Edition 2 (C6): the measurement vocabulary the charter loader reads.

``cost_per_attempt`` joins the catalogue beside ``cost_per_return``, which keeps
its meaning for every ratified charter; ``tool_calls`` is a mean per return.
"""

from dataclasses import replace
from pathlib import Path

import pytest

from factorylab.charter.charter import MetricCard
from factorylab.charter.measurement import (
    CardSamples,
    measure_card,
    measurement_catalogue,
    preflight_card,
    preflight_measurement,
)
from factorylab.charter.windows import MetricWindow
from factorylab.cortex.request import Return
from factorylab.runtime.observations import observation_for
from factorylab.runtime.pricing import MeasureWindow
from factorylab.runtime.worlds import load_manifest

NORM = "care with scarce resources"


def _card(observation, region="at most 5000", *, kind="returns", n=10, per="role"):
    return MetricCard("card", NORM, "d", "u", MetricWindow(kind, n, per), region, observation,
                      "producer")


def _samples(rows, *, window=1):
    samples = CardSamples()
    for i, (cost, ok, calls) in enumerate(rows):
        samples.returned(handle=f"h{i}", assembly="asm", role="producer", window=window,
                         ret=Return(f"h{i}", {}, cost, "ok" if ok else "failed",
                                    tool_calls=tuple({"tool": "t", "args": {}}
                                                     for _ in range(calls))))
    return samples


def test_cost_per_attempt_is_catalogued_measurable_over_returns_and_windows():
    catalogue = {row["id"]: row for row in measurement_catalogue()}
    assert set(catalogue["cost_per_attempt"]["window_kinds"]) == {"windows", "returns"}
    assert catalogue["cost_per_attempt"]["groupable"]
    assert catalogue["cost_per_attempt"]["units"] == "micro-USD per attempt"
    for kind, per in (("returns", "role"), ("returns", "assembly"), ("windows", None),
                      ("windows", "role")):
        preflight_card(_card("cost_per_attempt", kind=kind, n=1, per=per))
        preflight_measurement(_card("cost_per_attempt", kind=kind, n=1, per=per))
    with pytest.raises(ValueError, match="cannot be measured over"):
        preflight_card(_card("cost_per_attempt", kind="forecasts"))


def test_cost_per_attempt_counts_every_response_and_cost_per_return_only_successes():
    samples = _samples([(1_000, True, 0)] * 9 + [(100_000, False, 0)])
    assert measure_card(_card("cost_per_return"), samples) == {"producer": pytest.approx(1_000)}
    assert measure_card(_card("cost_per_attempt"), samples) == {
        "producer": pytest.approx(10_900)}
    # Over whole closed windows the same rows are read from the retained samples.
    window = MeasureWindow(1, 1, costs=[1_000] * 9, invocations=10, ok=9)
    samples.closed(window)
    assert measure_card(_card("cost_per_attempt", kind="windows", n=1, per=None), samples) == {
        "all": pytest.approx(10_900)}
    assert measure_card(_card("cost_per_return", kind="windows", n=1, per=None), samples) == {
        "all": pytest.approx(1_000)}


def test_a_live_window_measures_cost_per_attempt_from_its_own_decisions():
    window = MeasureWindow(1, 1, invocations=3, ok=2,
                           decisions={"a": {"cost": 100, "ok": 1, "invocations": 1},
                                      "b": {"cost": 200, "ok": 1, "invocations": 1},
                                      "c": {"cost": 900, "ok": 0, "invocations": 1},
                                      "rent": {"cost": 300, "ok": 0, "invocations": 0}})
    assert observation_for("cost_per_attempt").measure(window) == pytest.approx(1_500 / 3)
    assert observation_for("cost_per_attempt").measure(MeasureWindow(1, 1)) is None


def test_tool_calls_is_a_mean_per_return_over_returns_and_over_windows():
    samples = _samples([(1, True, 1)] * 10)
    card = replace(_card("tool_calls", "at most 2", per="assembly"), id="tool-discipline")
    assert measure_card(card, samples) == {"asm": pytest.approx(1.0)}
    samples = _samples([(1, True, 3), (1, False, 0)])
    assert measure_card(replace(card, window=MetricWindow("returns", 2, "assembly")),
                        samples) == {"asm": pytest.approx(1.5)}
    window = MeasureWindow(1, 1, invocations=4, ok=4, tool_calls=6)
    assert observation_for("tool_calls").measure(window) == pytest.approx(1.5)


@pytest.mark.parametrize("path", sorted(
    p for p in Path("docs/charter").glob("*.toml")) + [Path("worlds/edition1-example.toml")])
def test_ratified_charters_naming_cost_per_return_and_tool_calls_still_preflight(path):
    import tomllib

    raw = tomllib.loads(path.read_text())
    cards = raw.get("charter", raw).get("cards") or raw.get("cards") or []
    if not cards:
        pytest.skip("no cards in this file")
    norms = raw.get("charter", raw).get("norms") or raw.get("norms") or [NORM]
    seen = set()
    for c in cards:
        window = c["window"]
        if "per" not in window:
            window = {**window, "per": None}  # TOML has no null literal (worlds.py does this)
        card = MetricCard(c["id"], c.get("norm", norms[0]), c["description"], c["units"],
                          window, c["acceptable_region"], c["observation"], c["answers_for"])
        seen.add(card.observation)
        preflight_card(card)
        preflight_measurement(card)
    assert seen & {"cost_per_return", "tool_calls"}


def test_the_edition_one_example_world_loads_with_its_cost_per_return_card_unchanged():
    manifest = load_manifest("worlds/edition1-example.toml")
    card = next(c for c in manifest.charter.cards if c.observation == "cost_per_return")
    assert card.id == "model_cost_efficiency"
