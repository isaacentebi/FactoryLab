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
