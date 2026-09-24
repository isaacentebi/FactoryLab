"""A population's observation measures per scope, and never sees whose scope (charter audit C3).

Chapter II §IV.a: "The easiest ground within a charter to cede to the factory's
contributory arm is its metrics layer." A proxy the factory writes can now be
measured per role or per assembly, so it carries attributable blame exactly as a
seed proxy does. The kernel partitions; the population's code is handed one
scope's anonymous share of the window facts and returns one number.
"""

import json
from dataclasses import replace

import pytest

from factorylab.charter.charter import Charter, MetricCard
from factorylab.charter.measurement import (
    CardSamples,
    measure_card,
    measurement_catalogue,
    preflight_card,
    scope_facts,
)
from factorylab.charter.windows import MetricWindow
from factorylab.cortex.request import Return
from factorylab.kernel.queue import PropensityRecord
from factorylab.runtime import pricing
from factorylab.runtime.loop import Runtime
from factorylab.runtime.observations import ObservationBook
from factorylab.runtime.worlds import load_manifest

UNRESOLVED = ("def observe(facts):\n"
              "    return facts['censored'] / facts['outcomes'] if facts['outcomes'] else None\n")
OK_SHARE = "def observe(facts):\n    return facts['ok'] / facts['invocations']\n"
GUILTY, PARTLY, INNOCENT = "eval-guilty", "eval-partly", "eval-innocent"


class InProcessRunner:
    """The jail's contract, run in-process for a test: it records every fact it is handed."""

    def __init__(self):
        self.seen: list[dict] = []

    def run(self, code, facts):
        self.seen.append(json.loads(json.dumps(facts)))
        namespace: dict = {}
        exec(code, namespace)  # noqa: S102 - test code, the population's contract
        value = namespace["observe"](facts)
        return (None, "no value") if value is None else (float(value), None)


def _entry(code):
    return {"description": "d", "units": "fraction", "unit_range": [0.0, 1.0], "code": code,
            "version": 1, "provenance": "population", "history": [1], "trial_window": 1}


def _card(observation, per="assembly", **changes):
    return MetricCard(**{"id": "own-proxy", "norm": "truthful commitments",
                         "description": "a population proxy", "units": "fraction",
                         "window": MetricWindow("windows", 1, per),
                         "region": {"rule": "at most", "hi": 0.3},
                         "observation": observation, "answers_for": "all", **changes})


def _samples():
    samples = CardSamples()
    for seat, outcomes in (("seat-alpha", (True, True, False)), ("seat-beta", (False,))):
        for index, ok in enumerate(outcomes):
            samples.returned(handle=f"handle-{seat}-{index}", assembly=seat, role="producer",
                             window=1, ret=Return(f"handle-{seat}-{index}", {}, 5,
                                                  "ok" if ok else "failed"))
    samples.windows.append({"index": 1, "equity_start_micro": 100, "mids": [
        {"coin": "BTC", "ts_ns": 1, "value": 5}], "funding": [], "books": [],
        "wallet_balance_micro": [], "tick_timestamps_ns": []})
    return samples


def test_scoped_facts_carry_the_scope_s_numbers_and_no_identity():
    runner = InProcessRunner()
    book = ObservationBook({"ok-share": _entry(OK_SHARE)}, run=runner.run)
    values = measure_card(_card("ok-share", region={"rule": "at least", "lo": 0.5}),
                          _samples(), book)
    assert values == {"seat-alpha": pytest.approx(2 / 3), "seat-beta": 0.0}
    assert len(runner.seen) == 2
    for facts in runner.seen:
        text = json.dumps(facts)
        # The partition is the kernel's: no assembly, handle or role reaches the code.
        for identity in ("seat-alpha", "seat-beta", "handle-", "\"producer\"", "\"evaluator\""):
            assert identity not in text
        # The world's own series pass through; unattributable counters are null.
        assert facts["mids"] == {"BTC": [[1, 5]]} and facts["fills"] is None


def test_scope_facts_are_the_window_facts_of_the_scope_s_own_rows():
    facts = scope_facts(
        [{"index": 3, "equity_start_micro": 9}],
        [{"handle": "h", "cost": 7, "ok": True, "noop": True, "revision": True,
          "tool_calls": 2, "verdict": 0.4},
         {"handle": "s", "cost": 3, "ok": False, "noop": False, "revision": False,
          "tool_calls": 0, "verdict": None}],
        [{"skill": 0.1, "status": "settled", "predicate": "return_paid_off", "y": 1},
         {"skill": None, "status": "censored", "predicate": "wallet_up", "y": None}])
    assert {key: facts[key] for key in (
        "invocations", "ok", "costs", "tool_calls", "noop_returns", "revision_returns",
        "revised_decisions", "compute_spend_micro", "verdicts",
        "forecast_skills", "outcomes", "censored", "consequences_settled",
        "consequences_paid_off", "index")} == {
        "invocations": 2, "ok": 1, "costs": [7], "tool_calls": 2, "noop_returns": 1,
        "revision_returns": 1, "revised_decisions": 1,
        "compute_spend_micro": 10, "verdicts": [[[0.4]]], "forecast_skills": [0.1],
        "outcomes": 2, "censored": 1, "consequences_settled": 1,
        "consequences_paid_off": 1, "index": 3}


def test_a_registered_observation_may_be_scoped_and_says_so_in_public():
    book = ObservationBook({"ok-share": _entry(OK_SHARE)})
    preflight_card(_card("ok-share"), book)
    preflight_card(_card("ok-share", per="role"), book)
    row = next(r for r in measurement_catalogue(book) if r["id"] == "ok-share")
    assert row["groupable"] is True and row["window_kinds"] == ["windows"]
    # A seed window-only observation still has no scoped samples.
    with pytest.raises(ValueError, match="no role/assembly samples"):
        preflight_card(_card("turnover"))


def _runtime(monkeypatch):
    monkeypatch.setattr(pricing, "close_window", lambda *_a: None)
    seed = load_manifest("scripted")
    rt = Runtime(seed, events=1, seed=1, initial_balance_micro=None, ledger_path=None,
                 router_gamma=0.1)
    rt.observation_runner = InProcessRunner()
    rt.registered_observations["unresolved-own"] = _entry(UNRESOLVED)
    card = _card("unresolved-own")
    rt.charter = Charter(1, seed.charter.norms, (card,))
    rt._derive_regions()
    rt.controller.set_price(card.id, 0.8, amendment_id="test")
    return rt


def _decision(rt, assembly):
    handle = rt.queue.open(
        actor="test-router", event_id="test", channel="consequence", deadline_ns=10**18,
        parent_handle=None, cost_ceiling=0,
        propensity=PropensityRecord((assembly,), (1.0,), assembly, 0, "test-router", "state"))
    rt.handle_to_assembly[handle] = assembly
    sample = rt._contribution(handle, "evaluator")
    sample["invocations"] = sample["ok"] = 1
    return handle


def test_a_population_proxy_carries_attributable_blame_like_a_seed_one(monkeypatch):
    rt = _runtime(monkeypatch)
    handles = {seat: _decision(rt, seat) for seat in (GUILTY, PARTLY, INNOCENT)}
    for seat, censored in ((GUILTY, 4), (PARTLY, 2), (INNOCENT, 0)):
        for i in range(4):
            rt.card_samples.forecasts.append({
                "handle": f"f-{seat}-{i}", "assembly": seat, "role": "evaluator",
                "subject_handle": "s", "subject_assembly": "seed-decider",
                "subject_role": "producer", "window": rt.window.index, "skill": None,
                "predicate": "wallet_up", "y": None if i < censored else 1,
                "status": "censored" if i < censored else "settled", "verdict": None,
                "excluded": None})
    rt._close_price_window()
    assert rt.window.closed_scopes["own-proxy"] == {GUILTY: 1.0, PARTLY: 0.5, INNOCENT: 0.0}
    shares = {seat: rt._penalty_terms("all", handle)[0]["share"]
              for seat, handle in handles.items()}
    assert shares[GUILTY] == pytest.approx(7 / 9) and shares[PARTLY] == pytest.approx(2 / 9)
    assert shares[INNOCENT] == 0.0
    assert rt._penalty_terms("all", handles[GUILTY])[0]["owner"] == GUILTY
    for facts in rt.observation_runner.seen:
        assert not {GUILTY, PARTLY, INNOCENT} & set(json.dumps(facts).split('"'))


def test_a_scoped_population_proxy_with_an_interval_waits_for_precision(monkeypatch):
    rt = _runtime(monkeypatch)
    card = replace(rt.charter.cards[0], window=MetricWindow(
        "windows", 1, "assembly", {"level": 0.9, "half_width": 0.1}))
    rt.charter = Charter(1, rt.charter.norms, (card,))
    rt.card_samples.forecasts.append({
        "handle": "f", "assembly": GUILTY, "role": "evaluator", "subject_handle": "s",
        "subject_assembly": "x", "subject_role": "producer", "window": rt.window.index,
        "skill": None, "predicate": "wallet_up", "y": None, "status": "censored",
        "verdict": None, "excluded": None})
    rt._close_price_window()
    # One window is one sample: no interval can be stated, so nothing is measured.
    assert rt.window.closed_scopes == {}
