"""Exact cold-review scenarios for §II.b/§IV.a reward pricing."""

from dataclasses import asdict, replace
from types import SimpleNamespace

import pytest

from factorylab.charter.charter import MetricCard
from factorylab.charter.controller import CardRegion
from factorylab.charter.measurement import metric_identity
from factorylab.charter.windows import MetricWindow
from factorylab.runtime.pricing import MeasureWindow, PricingMixin


@pytest.mark.parametrize("closed", [False, True])
@pytest.mark.parametrize(("old_floor", "floor", "expected"), [(0.9, 0.5, 0), (0.5, 0.9, 0.1)])
def test_held_raw_measurement_uses_current_region(old_floor, floor, expected, closed):
    card = MetricCard("c", "n", "d", "fraction", MetricWindow("windows", 1, None),
                      {"rule": "at least", "lo": floor}, "forecast_skill", "evaluator")
    window = MeasureWindow(2, 0)
    fact = {"identity": list(metric_identity(card)), "source_window": 1, "value": 0.8,
            "region": asdict(CardRegion("c", "min", old_floor, None, 1.0)),
            "violation": max(0, old_floor - 0.8)}
    rt = PricingMixin()
    rt.charter = SimpleNamespace(cards=(card,))
    rt.price_origins = {}
    rt.card_samples = SimpleNamespace(values={})
    rt.regions = {"c": CardRegion("c", "min", floor, None, 1.0)}
    rt.card_held = {"c": fact}
    if closed:
        window.closed_values = {}
        window.closed_held = rt.card_held
        window.closed_regions = {"c": CardRegion("c", "min", old_floor, None, 1.0)}
    rt.m = SimpleNamespace(prices=SimpleNamespace(penalty_cap=0.9))
    rt._priced_cards = lambda _: [(card, SimpleNamespace(id="forecast_skill"), window, 1.0)]
    terms = rt._penalty_terms("evaluator", None, abstaining=True)
    assert sum(t["weight"] for t in terms) == pytest.approx(expected)


def attribution_runtime(field="notional_micro", count=2):
    from factorylab.charter.measurement import CardSamples
    from factorylab.kernel.ledger import Ledger
    from factorylab.settlement.vocabulary import PredicateBook
    from tests.runtime.test_holdouts import InProcessPredicates

    rt = PricingMixin()
    rt.window = MeasureWindow(1, 0)
    rt.card_samples = CardSamples()
    rt.card_samples.closed(rt.window)
    card = MetricCard("c", "n", "d", "fraction", MetricWindow("windows", 1, None),
                      {"rule": "at least", "lo": 0.5}, "revision_rate", "producer",
                      holdout=("hold@1",))
    rt.charter = SimpleNamespace(cards=(card,))
    rt.predicate_runner = InProcessPredicates()
    rt.predicates = PredicateBook(run=rt.predicate_runner.run)
    rt.predicates.register("hold", "constraint",
                           f"def resolve(facts):\n    return facts['{field}'] == 0\n",
                           facts={field: 0}, persist=lambda _: None)
    rt.window.decisions = {str(i): {"role": "producer", field: i + 1} for i in range(count)}
    rt.price_origins = {h: {"origin": 1} for h in rt.window.decisions}
    rt._split_decisions = lambda w: w.decisions
    rt._resolution_step = lambda _: 0.02
    rt.ledger = Ledger(None)
    rt.clock = SimpleNamespace(now_ns=0)
    return rt


@pytest.mark.parametrize("field", ["notional_micro", "amendments_proposed", "market_purchases"])
def test_all_recorded_behavioural_contributions_are_attributed_once(field):
    rt = attribution_runtime(field)
    attribution = rt._holdout_attribution({"c": {"results": {"hold@1": False}}})["c"]
    assert attribution["violation"] == 0.02
    assert attribution["shares"] == {"0": 0.5, "1": 0.5}


def test_unsupported_holdout_has_an_explicit_ownerless_ledger():
    rt = attribution_runtime("max_position_notional_micro")
    for sample in rt.window.decisions.values():
        sample.pop("max_position_notional_micro")
    rt.window.closed_holdout_attribution = rt._holdout_attribution(
        {"c": {"results": {"hold@1": False}}})
    rt._ledger_unattributed()
    row, = rt.ledger._recovery_items()
    assert row["kind"] == "price.unattributed"
    assert row["predicate"] == "hold@1"
    assert row["reason"] == "no_supported_owner"


def test_mixed_menu_abstention_bears_each_role_holdout_at_ordinary_weights():
    rt = attribution_runtime(count=1)
    rt.window.decisions["0"].update(role="evaluator",
                                    menu_roles={"evaluator": 0.75, "producer": 0.25})
    producer = rt.charter.cards[0]
    evaluator = replace(producer, id="e", answers_for="evaluator")
    rt.charter.cards = (producer, evaluator)
    attribution = rt._holdout_attribution({
        c.id: {"results": {"hold@1": False}} for c in rt.charter.cards})
    assert attribution["c"]["shares"] == {"0": 1.0}
    assert attribution["e"]["shares"] == {"0": 1.0}
    # Ordinary abstention pricing applies menu weights once, outside each role price.
    weights = rt.window.decisions["0"]["menu_roles"]
    assert weights["producer"] * attribution["c"]["violation"] == pytest.approx(0.005)
    assert weights["evaluator"] * attribution["e"]["violation"] == pytest.approx(0.015)
    from factorylab.runtime.feedback import FeedbackMixin

    rt.price_windows = {1: rt.window}
    rt.window.closed_values = {"c": 0.8, "e": 0.8}
    rt.window.closed_regions = {c.id: CardRegion(c.id, "min", 0.5, None, 1.0)
                                for c in rt.charter.cards}
    rt.window.closed_holdouts = {"c": 0.02, "e": 0.02}
    rt.window.closed_holdout_attribution = attribution
    rt.m = SimpleNamespace(prices=SimpleNamespace(penalty_cap=0.9))
    rt._is_price_abstention = lambda _: True
    rt._is_niche = lambda *args: False
    rt._decision_share = lambda *args, **kwargs: 0
    rt._attributed_share = lambda *args, **kwargs: None
    rt._relievers = lambda *args: set()
    rt._priced_cards = lambda _: [(c, SimpleNamespace(id=c.observation), rt.window,
                                  2.0 if c.id == "c" else 1.0) for c in rt.charter.cards]
    assert FeedbackMixin._priced_abstention(rt, "0") == pytest.approx(0.25 * 0.04 + 0.75 * 0.02)


def test_ownerless_holdout_reports_charged_pressure_below_controller_cap():
    from factorylab.charter.controller import PriceController
    from factorylab.kernel.ledger import Ledger

    rt = attribution_runtime()
    rt.regions = {"c": CardRegion("c", "min", 0.5, None, 1.0)}
    rt.m = SimpleNamespace(prices=SimpleNamespace(penalty_cap=0.9))
    ledger = Ledger(None)
    rt.controller = PriceController(ledger, eta=0.5, decay=0.1,
                                    penalty_cap=0.9, min_window_events=1, kp=0.5)
    rt.controller.register(rt.regions["c"])
    rt.controller.set_price("c", 90.0, amendment_id="test")
    rt.controller.observe("c", 0.49, 1, holdout=1.0, pressure=0.9, charged_violation=0.01)
    rt.window.closed_values = {"c": 0.49}
    rt.window.closed_regions = rt.regions
    rt.window.closed_holdouts = {"c": 1.0}
    rt.window.closed_holdout_attribution = {"c": {"violation": 0.0}}
    rt.window.closed_cards = rt.charter.cards
    rt.window.closed_prices = {"c": rt.controller.price("c")}
    rt.price_windows = {1: rt.window}
    rt.card_unmeasured = {}
    state = rt._card_observed("c")
    assert state["charged_pressure"] < 0.9
    assert state["controller_pressure"] == pytest.approx(0.9)
    assert state["charged_pressure"] == pytest.approx(rt.controller.price("c") * 0.01)
    assert rt.controller.price("c") == pytest.approx(0.9 / 1.01)
    row = [r for r in ledger._recovery_items() if r["kind"] == "price.update"][-1]
    assert row["at_cap"] is True
    assert row["violation"] == pytest.approx(1.01)
    assert row["charged_pressure"] == pytest.approx(state["charged_pressure"])


def test_ten_predicates_five_hundred_decisions_use_ten_jails(monkeypatch):
    import contextlib
    import io
    import json
    import sys

    from factorylab.cortex import sandbox
    from factorylab.runtime.shared import PredicateRunner

    rt = attribution_runtime(count=500)
    runner = PredicateRunner()
    runner.available = True
    rt.predicate_runner = runner
    calls = []

    def jail(code, *, stdin, **kwargs):
        calls.append(1)
        stream = io.StringIO()
        with monkeypatch.context() as patch:
            patch.setattr(sys, "stdin", io.StringIO(stdin))
            with contextlib.redirect_stdout(stream):
                exec(code, {})  # noqa: S102 - deterministic jail boundary test
        return SimpleNamespace(timed_out=False, returncode=0, stdout=stream.getvalue())

    monkeypatch.setattr(sandbox, "run_python", jail)
    rt.predicates._run = runner.run
    entries = []
    for i in range(10):
        name = f"batch-{i}"
        rt.predicates.register(name, "constraint",
                               f"def resolve(facts):\n    return facts['notional_micro'] <= {i}\n",
                               facts={"notional_micro": 0}, persist=lambda _: None)
        entries.append(f"{name}@1")
    rt.charter.cards = (replace(rt.charter.cards[0], holdout=tuple(entries)),)
    rt.window.notional_micro = sum(range(1, 501))
    rt.card_samples.windows.clear()
    rt.card_samples.closed(rt.window)
    calls.clear()
    results = rt._holdout_results({"c": 0.8})
    attribution = rt._holdout_attribution(results)["c"]
    assert len(calls) <= 10
    expected = {}
    for i, entry in enumerate(entries):
        failures = [str(j) for j in range(500) if j + 1 > i]
        assert attribution["predicates"][entry]["attributees"] == sorted(failures)
        for handle in failures:
            expected[handle] = expected.get(handle, 0) + 1 / (10 * len(failures))
    assert attribution["shares"] == pytest.approx(expected)
    assert json.dumps(attribution, allow_nan=False)


def test_batch_cannot_attribute_nonbehavioural_predicates():
    rt = attribution_runtime()
    rt.predicates.register("constant", "unsupported", "def resolve(facts):\n    return False\n",
                           facts={}, persist=lambda _: None)
    rt.charter.cards = (replace(rt.charter.cards[0], holdout=("constant@1",)),)
    result = rt._holdout_results({"c": 0.8})
    assert result["c"]["results"]["constant@1"] is None
    assert rt._holdout_attribution(result)["c"]["violation"] == 0


@pytest.mark.gate
def test_predicate_batch_real_jail_five_hundred_facts():
    from factorylab.runtime.shared import PredicateRunner

    runner = PredicateRunner()
    if not runner.available:
        pytest.skip("no jail on this host")
    facts = [{"notional_micro": i} for i in range(501)]
    assert runner.run_batch("def resolve(facts):\n    return facts['notional_micro'] <= 250\n",
                            facts) == [i <= 250 for i in range(501)]
