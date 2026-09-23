"""Edition 2 (C6): the measurement vocabulary the charter loader reads.

``cost_per_attempt`` joins the catalogue beside ``cost_per_return``, which keeps
its meaning for every ratified charter; ``tool_calls`` is a mean per return.
"""


import pytest

from factorylab.charter.charter import MetricCard
from factorylab.charter.measurement import (
    CardSamples,
    measure_card,
)
from factorylab.charter.windows import MetricWindow
from factorylab.cortex.request import Return
from factorylab.runtime.pricing import MeasureWindow

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


def test_global_windows_merge_flat_counters_and_nested_scores():
    # Edition 6's own charter (23 September 2026) was the first with a global windows
    # card over n > 1; merging calls_by_family, a flat {family: count}, crashed the world.
    samples = CardSamples()
    samples.closed(MeasureWindow(1, 1, calls_by_family={"gpt": 2, "xiaomi": 2}))
    samples.closed(MeasureWindow(2, 1, calls_by_family={"gpt": 4}))
    card = MetricCard("card", NORM, "d", "u", MetricWindow("windows", 2, None), "at most 1",
                      "family_concentration", "all")
    assert measure_card(card, samples) == {"all": pytest.approx(6 / 8)}


def test_a_per_window_total_over_several_windows_is_their_mean():
    # The #139 review: burn_per_window over 25 windows compared their total with a
    # per-window ceiling. A total is averaged over the selected windows; a rate is not.
    samples = CardSamples()
    for index in (1, 2):
        samples.closed(MeasureWindow(index, 1, compute_spend_micro=1_000_000, invocations=4,
                                     ok=2 * index))
    burn = MetricCard("card", NORM, "d", "u", MetricWindow("windows", 2, None), "at most 1",
                      "burn_per_window", "all")
    assert measure_card(burn, samples) == {"all": pytest.approx(1_000_000)}
    rate = MetricCard("card", NORM, "d", "u", MetricWindow("windows", 2, None), "at least 0",
                      "well_formed_rate", "all")
    assert measure_card(rate, samples) == {"all": pytest.approx(6 / 8)}
