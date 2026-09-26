"""Realized consequence is nonfungible (wave 16, section 9; essay II.IV.a).

Ruling R-A is reversed: an evaluator's reward is the equal mean of its tier grade and
its consequence score when both exist (D6), the grade alone when no informative
consequence exists (R-B). "Nonfungible" means only this: the consequence score that
grades an evaluator never enters a charter card's price, a λ or a posted token. The
§IV.a marketplace keeps "a central source of value ... intentionally nonfungible (the
realized-consequence reward structure)".
"""

from __future__ import annotations

import ast
import importlib
import inspect
import pkgutil
from dataclasses import replace

import pytest

import factorylab.charter
from factorylab.charter.charter import MetricCard, MetricWindow
from factorylab.runtime import pricing
from factorylab.runtime.feedback import evaluation_reward
from factorylab.runtime.loop import Runtime
from factorylab.runtime.worlds import load_manifest

#: What the realized-consequence reward structure is made of in the runtime: the scores
#: a judge's or a meta's prediction earned, the standing built from them, and the
#: antagonists' reading of them.
CONSEQUENCE_NAMES = frozenset({"consequence_scores", "verdict_skill", "record_verdict",
                               "consequence_score", "exposure_scores", "judge_ordinary",
                               "settle_verdict"})

#: Every path that prices a card, moves a λ, or scores a posted λ.
PRICING_MODULES = ("factorylab.runtime.pricing", "factorylab.runtime.markets",
                   "factorylab.runtime.cards", "factorylab.runtime.observations",
                   "factorylab.runtime.immune", "factorylab.versioning.live",
                   "factorylab.versioning.versions")


def _modules():
    for name in PRICING_MODULES:
        yield importlib.import_module(name)
    for info in pkgutil.iter_modules(factorylab.charter.__path__):
        yield importlib.import_module(f"factorylab.charter.{info.name}")


def _names(module) -> set[str]:
    """Every attribute, name and string key the module's code reads."""
    tree = ast.parse(inspect.getsource(module))
    found = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute):
            found.add(node.attr)
        elif isinstance(node, ast.Name):
            found.add(node.id)
        elif isinstance(node, ast.Subscript) and isinstance(node.slice, ast.Constant):
            found.add(str(node.slice.value))
    return found


@pytest.mark.parametrize("module", list(_modules()), ids=lambda m: m.__name__)
def test_no_charter_or_pricing_path_reads_a_consequence_score(module):
    assert not _names(module) & CONSEQUENCE_NAMES


def _card():
    return MetricCard(id="skill-floor", norm="care with scarce resources", description="d",
                      units="score difference",
                      window=MetricWindow("windows", 1, None), acceptable_region="at least 0.0",
                      observation="forecast_skill", answers_for="all")


def test_the_forecast_skill_a_card_prices_is_the_forecasts_alone(monkeypatch):
    """A judge the world proved right on every verdict moves no card: only the forecasts
    it sealed and the world resolved enter ``forecast_skill``."""
    monkeypatch.setattr(pricing, "close_window", lambda *_a: None)
    seed = load_manifest("scripted")
    rt = Runtime(replace(seed, charter=replace(seed.charter, cards=(_card(),))), events=1,
                 seed=1, initial_balance_micro=None, ledger_path=None, router_gamma=0.1)
    rt._derive_regions()
    judge = next(a for a in rt.assemblies.values()
                 if "Verdict" in a.spec.emits).spec.id
    for _ in range(5):
        rt.standing.record_verdict(judge, 1.0, 0.25)  # right, against a coin-flip base
    rt._close_price_window()
    closes = [i for i in rt.ledger._recovery_items() if i.get("kind") == "price.window"]
    assert "forecast_skill" not in closes[-1]["observations"]
    assert "skill-floor" not in closes[-1]["values"]
    rt.standing.record(judge, 0.64, 0.75)  # one settled forecast, below its base rate
    rt._manage_reserve_window()
    rt._close_price_window()
    closes = [i for i in rt.ledger._recovery_items() if i.get("kind") == "price.window"]
    assert closes[-1]["observations"]["forecast_skill"] == pytest.approx(0.64 - 0.75)


def test_a_posted_lambda_is_never_scored_against_a_judgements_consequence():
    rt = Runtime(load_manifest("scripted"), events=1, seed=1, initial_balance_micro=None,
                 ledger_path=None, router_gamma=0.1)
    rt.margin_windows = {1: {"due": 2, "cards": {}, "decisions": {
        "judge-decision": {"assembly": "eval-a", "role": "evaluator", "cost": 1},
        "producer-decision": {"assembly": "seed-decider", "role": "producer", "cost": 1}}}}
    rt.consequence_scores["judge-decision"] = (0.9, rt.clock.now_ns)
    rt.world_outcomes["producer-decision"] = {"state": "measured", "y": 1.0,
                                              "kind": "return_paid_off", "tick": 0, "ns": 0}
    rt._capture_consequences()
    assert rt.measured_consequences == {"producer-decision": 1.0}


def test_the_evaluator_reward_is_the_equal_mean_and_the_grade_alone_when_uninformative():
    assert evaluation_reward(0.8, 0.2) == pytest.approx(0.5)
    assert evaluation_reward(0.8, None) == 0.8  # no informative consequence: the grade
    assert evaluation_reward(None, 0.2) == 0.2
    assert evaluation_reward(None, None) is None
