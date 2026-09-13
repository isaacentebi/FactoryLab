"""Verdict cards measure the judged return while skill cards measure its forecaster."""

from dataclasses import replace

import pytest

from factorylab.charter.charter import MetricCard
from factorylab.charter.measurement import (
    CardSamples,
    measure_card,
    preflight_measurement,
    record_card_forecasts,
)
from factorylab.cortex.request import Return
from factorylab.kernel.events import Event, EventKind
from factorylab.runtime.pricing import MeasureWindow
from factorylab.runtime.resume import decode, encode
from factorylab.settlement.scoring import PrevalenceBaseline
from tests.audit.test_v3_seat4_boundaries import _decision
from tests.conftest import make_runtime


def verdict_card(*, observation="verdict_mean", kind="forecasts", per="assembly", n=2):
    return MetricCard(
        "verdicts", "truthful commitments", "Judgements of returns", "fraction",
        {"kind": kind, "n": n, "per": per}, "at least 0.5", observation, "producer",
    )


def runtime_samples(*, recorder=None, prune_subject=False):
    rt = make_runtime()
    for assembly, verdict in [("seed-decider", 0.2), ("seed-decider", 0.6),
                              ("seed-observer", 1.0)]:
        subject = _decision(rt, assembly)
        judge = _decision(rt, "eval-a")
        rt.return_kinds[subject] = "ProducerReturn"
        rt.return_kinds[judge] = "Verdict"
        rt.card_samples.returned(handle=subject, assembly=assembly, role="producer", window=0,
                                 ret=Return(subject, {}, 1, "ok"))
        rt.card_samples.returned(handle=judge, assembly="eval-a", role="evaluator", window=0,
                                 ret=Return(judge, {"verdict": verdict}, 1, "ok"))
        forecast = rt.consequences.seal_verdict(
            rt.book, rt.queue, evaluator_handle=judge, evaluator_id="eval-a", about=subject,
            payoff=0.5, event=0, now_ns=0, tick_ns=1,
        )
        rt.internal.clear()
        rt.internal.append(Event(
            "resolved-" + forecast.handle, EventKind.FORECAST_SETTLED, 1,
            {"handle": forecast.handle, "predicate": "return_paid_off",
             "y": 0, "brier": 0.75, "status": "settled"}, "settler",
        ))
        if prune_subject:
            rt.card_samples.returns[:] = [r for r in rt.card_samples.returns
                                          if r["handle"] != subject]
        if recorder is None:
            rt._record_card_forecasts({forecast.handle: forecast}, PrevalenceBaseline())
        else:
            recorder(rt, {forecast.handle: forecast}, PrevalenceBaseline())
    return rt


def test_runtime_verdict_rows_select_subject_and_skill_selects_forecaster():
    rt = runtime_samples()
    try:
        assert measure_card(verdict_card(), rt.card_samples) == {"seed-decider": 0.4}
        skill = replace(verdict_card(observation="forecast_skill"), answers_for="evaluator")
        assert measure_card(skill, rt.card_samples) == {"eval-a": 0.0}
        assert measure_card(replace(skill, answers_for="producer"), rt.card_samples) == {}
    finally:
        rt._ledger_lock.close()


@pytest.mark.parametrize("observation,value", [("verdict_mean", 0.4), ("verdict_std", 0.2)])
def test_subject_scope_survives_pruning_and_snapshot(observation, value):
    rt = runtime_samples()
    try:
        samples = rt.card_samples
        card = verdict_card(observation=observation)
        samples.closed(MeasureWindow(1, 100))
        samples.prune((card,))
        restored = decode(encode(samples))
        assert measure_card(card, restored)["seed-decider"] == pytest.approx(value)
        assert len(restored.forecasts) == 3
    finally:
        rt._ledger_lock.close()


def test_preflight_cannot_manufacture_a_nonexistent_measurement_role():
    with pytest.raises(ValueError, match="answers_for"):
        preflight_measurement(replace(verdict_card(), answers_for="nonexistent-role"))


def test_preflight_uses_the_same_sample_constructor(monkeypatch):
    # Erasing the judged identity must make the producer-scoped preflight fail,
    # just as it makes actual pricing unavailable.
    original = CardSamples.resolved_forecast

    def without_subject(self, **kwargs):
        original(self, **kwargs)
        self.forecasts[-1]["subject_assembly"] = None
        self.forecasts[-1]["subject_role"] = None

    monkeypatch.setattr(CardSamples, "resolved_forecast", without_subject)
    with pytest.raises(ValueError, match="measurement preflight produced no value"):
        preflight_measurement(verdict_card())


@pytest.mark.parametrize("prune_subject", [False, True])
def test_shared_event_constructor_retains_subject_after_return_pruning(prune_subject):
    rt = runtime_samples(recorder=record_card_forecasts, prune_subject=prune_subject)
    try:
        assert measure_card(verdict_card(), rt.card_samples) == {"seed-decider": 0.4}
        assert measure_card(verdict_card(per="role"), rt.card_samples) == {"producer": 0.8}
        skill = replace(verdict_card(observation="forecast_skill"), answers_for="evaluator")
        assert measure_card(skill, rt.card_samples) == {"eval-a": 0.0}
        rt.card_samples.closed(MeasureWindow(0, 100))
        card = verdict_card(kind="windows", n=1)
        assert measure_card(card, rt.card_samples) == {"seed-decider": 0.4, "seed-observer": 1.0}
        rt.card_samples.closed(MeasureWindow(1, 100))
        rt.card_samples.prune((verdict_card(),))
        restored = decode(encode(rt.card_samples))
        assert measure_card(verdict_card(), restored) == {"seed-decider": 0.4}
        preflight_measurement(verdict_card())
    finally:
        rt._ledger_lock.close()
