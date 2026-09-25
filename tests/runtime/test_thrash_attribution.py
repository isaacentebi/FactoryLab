"""Thrash is borne by the tier whose behaviour moved (wave 16, second addendum, I-10).

Ruling R-E: a penalty is attributed to the decisions, and the routers, whose behaviour
the violation measures. In edition 6 the only no-swap-regret router is the producers'
Tick router, so a judge tier oscillating over steady producers was priced on the
producers. Now the organ reads which roles the moving cards measure, and the price
lands on the routers whose seats fill them; only when no role is named does it land on
the core, as essay II.II.b puts it.
"""

from dataclasses import replace

import pytest

from factorylab.charter.charter import MetricCard
from factorylab.charter.windows import MetricWindow
from factorylab.runtime import immune
from tests.conftest import make_runtime
from tests.runtime.test_immune_live import _draw


def _card(cid, observation, answers_for):
    return MetricCard(cid, "truthful commitments", "A reading.", "fraction",
                      MetricWindow("windows", 1, None), {"rule": "at least", "lo": 0.5},
                      observation, answers_for)


def _windows(cards, moving: str, n: int = 9):
    """``n`` windows in which ``moving`` alternates in and out of violation."""
    regions = {f"card:{c.id}": {"kind": "min", "lo": 0.5, "hi": None, "scale": 0.5}
               for c in cards}
    return [{"index": i, "regions": regions, "frontier_invocation": [], "lifespans": [],
             "profile": {f"card:{c.id}": (0.1 if c.id == moving and i % 2 else 0.9)
                         for c in cards}}
            for i in range(n)]


@pytest.mark.parametrize(("observation", "answers_for", "roles"), [
    ("verdict_mean", "producer", ["evaluator"]),  # the judges' verdicts moved
    ("meta_verdict_mean", "evaluator", ["meta"]),
    ("well_formed_rate", "producer", ["producer"]),
    ("well_formed_rate", "all", []),  # no role named: the core bears it
])
def test_the_organ_names_the_roles_whose_behaviour_moved(observation, answers_for, roles):
    rt = make_runtime()
    moving, steady = _card("moving", observation, answers_for), _card("steady",
                                                                      "noop_share",
                                                                      "producer")
    rt.charter = replace(rt.charter, cards=(moving, steady))
    assert immune.thrash_roles(rt, _windows((moving, steady), "moving")) == roles


def test_judge_thrash_charges_the_judges_router_and_the_producers_bear_none():
    rt = make_runtime()
    rt.m = replace(rt.m, evaluation=replace(rt.m.evaluation, no_swap_regret_kinds=("Tick",)))
    rt.stats.thrash = {"lambda": 0.4, "roles": ["evaluator"]}
    judges, producers = rt.routers["ProducerReturn"][0], rt.routers["Tick"][0]
    cap = rt.m.prices.penalty_cap
    for name, state in (("j", judges), ("p", producers)):
        rt._record_movement(state, _draw(state, (0.8, 0.1, 0.1)), f"{name}1")
        rt._record_movement(state, _draw(state, (0.1, 0.8, 0.1)), f"{name}2")  # TV 0.7
    assert rt.thrash_charges["j2"] == pytest.approx(0.4 * 0.7)
    assert "p1" not in rt.thrash_charges and "p2" not in rt.thrash_charges
    # The judges' rounds under the price share one scale; the charged one pays.
    assert rt._thrash_charged(judges, "j1", 0.7) == pytest.approx((0.7 + cap) / (1 + cap))
    assert rt._thrash_charged(judges, "j2", 0.7) == pytest.approx(
        (0.7 + cap - 0.28) / (1 + cap))
    # The producers' core router: every round on the core scale, charged nothing.
    assert rt._thrash_charged(producers, "p2", 0.7) == pytest.approx((0.7 + cap) / (1 + cap))
    charged = {row["router"] for row in rt.ledger._recovery_items()
               if row.get("kind") == "thrash.charged"}
    assert charged == {judges.learner.id}


def test_with_no_role_named_the_core_bears_it_as_before():
    rt = make_runtime()
    rt.m = replace(rt.m, evaluation=replace(rt.m.evaluation, no_swap_regret_kinds=("Tick",)))
    rt.stats.thrash = {"lambda": 0.4, "roles": []}
    judges, producers = rt.routers["ProducerReturn"][0], rt.routers["Tick"][0]
    for name, state in (("j", judges), ("p", producers)):
        rt._record_movement(state, _draw(state, (0.8, 0.1, 0.1)), f"{name}1")
        rt._record_movement(state, _draw(state, (0.1, 0.8, 0.1)), f"{name}2")
    assert "p2" in rt.thrash_charges and "j2" not in rt.thrash_charges
    assert rt._thrash_charged(judges, "j2", 0.7) == 0.7


def test_the_roles_are_published_with_the_price():
    rt = make_runtime()
    rt.stats.thrash = {"lambda": 0.1, "penalty": 0.0, "roles": ["evaluator"]}
    assert rt._adaptive_scoring_block()["thrash_price"]["roles"] == ["evaluator"]
