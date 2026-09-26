"""The paid-off rate counts acting returns; non-acting outcomes are published apart (R-H).

``consequence_paid_off_rate`` reads the outcomes of returns that executed something:
a hold's ``return_paid_off`` is 0 by the predicate's acting clause, whatever the world
said about it, so counting it would penalise every hold. What the world said about
the returns that acted on nothing is its own published pair of observations: the share
of their fixed outcomes measured with an informative base-rate key, and the mean y of
those. The charter's cards are the charter's to revise.
"""

from __future__ import annotations

from factorylab.charter.measurement import CardSamples, _sample_values
from factorylab.runtime.observations import catalogue, observation_for
from factorylab.runtime.pricing import MeasureWindow
from factorylab.world.exchange import NS_PER_HOUR
from tests.runtime.test_consequence_horizon import S, _named_hold, _walk, _world


def _window_total(rt, name: str) -> int:
    closed = sum(record.get(name) or 0 for record in rt.card_samples.windows)
    return closed + getattr(rt.window, name)


def test_a_non_acting_outcome_is_counted_apart_and_never_as_an_unpaid_consequence():
    rt = _world(10)
    start = 3 * NS_PER_HOUR
    producer, _judge = _named_hold(rt, start)
    _walk(rt, start, 10, 200, lambda s: "90")  # the declined buy would have lost
    assert rt.world_outcomes[producer]["y"] == 1.0
    assert _window_total(rt, "non_acting_outcomes") == 1
    assert _window_total(rt, "non_acting_informative") == 1
    assert _window_total(rt, "non_acting_paid_off") == 1
    assert _window_total(rt, "consequences_settled") == 0  # not an acting return
    assert start + 90 * S <= rt.clock.now_ns


def test_the_typed_paid_off_rate_reads_acting_subjects_only():
    rows = [
        {"predicate": "return_paid_off", "status": "settled", "y": 1, "subject_acted": True},
        {"predicate": "return_paid_off", "status": "settled", "y": 0, "subject_acted": False},
        {"predicate": "return_paid_off", "status": "settled", "y": 0, "subject_acted": None},
    ]
    # The non-acting row is out; an unknown subject counts as before.
    assert _sample_values("consequence_paid_off_rate", rows) == [1.0, 0.0]


def test_the_subject_s_acting_is_recorded_with_its_forecast_row():
    from factorylab.settlement.forecast import Forecast

    samples = CardSamples()
    forecast = Forecast("f", "eval-a", "r", "return_paid_off", {"horizon_events": 1},
                        0.5, 0, 1)
    samples.resolved_forecast(forecast=forecast, role="evaluator", window=1, skill=0.0,
                              y=0, status="settled", source={},
                              subject={"assembly": "seed-decider", "role": "producer",
                                       "acted": False})
    assert samples.forecasts[-1]["subject_acted"] is False


def test_the_two_observations_are_published_and_measured_on_the_window():
    public = {row["id"]: row for row in catalogue()}
    for name in ("non_acting_informative_share", "non_acting_paid_off_rate"):
        assert public[name]["units"] == "fraction" and public[name]["description"]
    assert "acting" in public["consequence_paid_off_rate"]["description"]
    w = MeasureWindow(1, 1, non_acting_outcomes=4, non_acting_informative=2,
                      non_acting_paid_off=1)
    assert observation_for("non_acting_informative_share").measure(w) == 0.5
    assert observation_for("non_acting_paid_off_rate").measure(w) == 0.5
    empty = MeasureWindow(1, 1)
    assert observation_for("non_acting_informative_share").measure(empty) is None
    assert observation_for("non_acting_paid_off_rate").measure(empty) is None
