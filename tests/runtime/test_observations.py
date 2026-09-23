from copy import deepcopy

import pytest

from factorylab.runtime.observations import CATALOGUE, catalogue, observation_for
from factorylab.runtime.pricing import MeasureWindow


def window():
    return MeasureWindow(
        1, 10_000_000, costs=[100, 300], invocations=4, ok=3,
        notional_micro=25_000_000, forecast_skills=[-0.25, 0.75],
        producer_returns=4, noop_returns=3, revision_returns=2,
        registrations=5, registration_rejections=2, amendments_proposed=3,
        amendments_activated=1, verdicts={"a": {"j1": [0.0], "j2": [1.0]}, "b": {"j1": [0.5]}},
        consequences_settled=4, consequences_paid_off=3, fills=6,
        realized_pnl_micro=-1_250_000, max_position_notional_micro=5_000_000,
        exposures_settled=4, exposures_won=1, meta_verdicts=[0.2, 0.8],
        outcomes=8, censored=2, tool_calls=7, market_purchases=2,
    )


@pytest.mark.parametrize(("name", "expected"), [
    ("cost_per_return", 200.0), ("well_formed_rate", 0.75), ("forecast_skill", 0.25),
    ("turnover", 2.5), ("noop_share", 0.75), ("revision_rate", 0.5),
    ("registrations", 5.0), ("registration_rejections", 2.0), ("amendments_proposed", 3.0),
    ("amendments_activated", 1.0), ("verdict_mean", 0.5), ("verdict_std", (1 / 6) ** 0.5),
    ("evaluator_disagreement", 0.5), ("consequence_paid_off_rate", 0.75), ("fills", 6.0),
    ("realized_pnl_usd", -1.25), ("position_concentration", 0.5), ("exposure_win_rate", 0.25),
    ("meta_verdict_mean", 0.5), ("censored_share", 0.25), ("tool_calls", 7 / 4),
    ("market_purchases", 2.0),
])
def test_each_observation_is_pure_and_has_declared_units(name, expected):
    w = window()
    before = deepcopy(w)
    observation = observation_for(name)
    assert observation is not None and observation.description and observation.units
    value = observation.measure(w)
    assert isinstance(value, float) and value == pytest.approx(expected)
    assert w == before


@pytest.mark.parametrize("observation", CATALOGUE, ids=lambda o: o.id)
def test_empty_window_distinguishes_zero_activity_from_missing_support(observation):
    # tool_calls is a mean per invocation (edition 2, C6): no invocation, no support.
    zero = {
        "registrations", "registration_rejections", "amendments_proposed", "amendments_activated",
        "fills", "realized_pnl_usd", "market_purchases", "turnover", "burn_per_window",
    }
    assert observation.measure(MeasureWindow(1, 0)) == (0.0 if observation.id in zero else None)


def test_nonpositive_equity_is_not_a_supported_denominator():
    for equity in (0, -10):
        w = MeasureWindow(1, equity, notional_micro=1, max_position_notional_micro=1)
        assert observation_for("turnover").measure(w) is None
        assert observation_for("position_concentration").measure(w) is None


def test_disagreement_requires_distinct_judges_and_weights_returns_equally():
    w = MeasureWindow(1, 100, verdicts={"a": {"j1": [0.0, 1.0]}})
    assert observation_for("verdict_mean").measure(w) == 0.5
    assert observation_for("verdict_std").measure(w) == 0.5
    assert observation_for("evaluator_disagreement").measure(w) is None
    w.verdicts["a"]["j2"] = [0.5]
    w.verdicts["b"] = {"j1": [0.0], "j2": [1.0]}
    assert observation_for("evaluator_disagreement").measure(w) == 0.25


def test_catalogue_is_exact_and_public_metadata_cannot_mutate_it():
    public = catalogue()
    # + cost_per_attempt (C6), + avoidably_unresolved_share (edition 3 C3), + burn_per_window
    # (charter audit M6), + ews_variance and ews_autocorrelation (ruling R3, evaluations M2)
    assert len(public) == len({o.id for o in CATALOGUE}) == 27
    # A11: the public row now also names where the observation came from and which
    # version of it this is, because the population can register its own.
    assert all(set(item) == {"id", "description", "units", "unit_range", "scale",
                             "provenance", "version"}
               for item in public)
    assert all(item["provenance"] == "seed" and item["version"] == 1 for item in public)
    public[0]["id"] = "changed"
    assert catalogue()[0]["id"] == "cost_per_return"
    assert observation_for("  NoOp_ShArE \n").id == "noop_share"
    for name in ("", "computed_per_window/turnover", "turnover extra", "noop share"):
        assert observation_for(name) is None
