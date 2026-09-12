"""A5: exposure pays only for a real, attributable failure, and is priced (seat 2, finding 2;
seat 6, finding 8a)."""

from dataclasses import replace

import pytest

from factorylab.charter.charter import Charter, MetricCard
from factorylab.kernel.events import Event, EventKind
from factorylab.learners.base import BanditFeedback
from factorylab.runtime.loop import run_world
from factorylab.runtime.worlds import load_manifest
from factorylab.world.scripted import ScriptedProvider
from tests.runtime.test_loop import (
    _consequence_judge,
    _consequence_produce,
    _consequence_runtime,
)


class Provider(ScriptedProvider):
    """An antagonist that holds, and a judge whose verdict is right but whose optional
    forecasts are deliberately wrong (seat 2's three events)."""

    def __init__(self, *, self_payoff=None, judge_payoff=0.0, optional=True):
        super().__init__()
        self.self_payoff, self.judge_payoff, self.optional = self_payoff, judge_payoff, optional

    def _produce(self, desc, inputs):
        reply = {"action": "hold"}
        if self.self_payoff is not None:
            reply["payoff"] = self.self_payoff
        return reply

    def _evaluate(self, req, inputs):
        forecasts = [
            {"predicate": "wallet_up", "params": {"horizon_events": 1}, "q": 1.0},
            {"predicate": "wallet_up", "params": {"horizon_events": 1}, "q": 0.0},
        ] if self.optional else []
        return {"verdict": 0.0, "payoff": self.judge_payoff, "rationale": "right",
                "forecasts": forecasts}


def _three_events(provider):
    runtime = _consequence_runtime(provider=provider)
    about, event = _consequence_produce(runtime, "antagonist-a", "exposure")
    _consequence_judge(runtime, event, "eval-a")
    runtime.balance_at = [runtime.wallet.balance] * (runtime.n + 1)
    # Event 3: the wallet declined, so the q = 1 optional forecast is wrong.
    runtime.wallet.settle_batch([(-39, "test:decline", "exchange_pnl")])
    runtime.n += 1
    runtime.balance_at.append(runtime.wallet.balance)
    runtime._settle_due_forecasts()
    runtime.n = runtime.ev.verdict_timeout_events + 5
    runtime._settle_due_forecasts()
    return runtime, about


def test_seat_2_three_events_award_no_exposure_for_a_wrong_optional_forecast():
    runtime, about = _three_events(Provider(self_payoff=0.0, judge_payoff=0.0))
    items = runtime.ledger._recovery_items()
    optional = [i for i in items if i["kind"] == "decision.settle"
                and i["return"]["channel"] == "consequence"
                and i["return"]["status"] == "settled"]
    assert len(optional) == 4  # self payoff, judge payoff, two optional forecasts
    assert min(i["return"]["score"] for i in optional) == 0.0  # the q = 1 forecast was wrong
    assert about not in runtime.pending_exposure
    assert runtime.queue.history(about)[-1].score == 0.0
    assert runtime.stats.exposures_settled == 1 and runtime.stats.exposures_won == 0
    settled = next(i for i in items if i["kind"] == "exposure.settled")
    assert settled == {**settled, "judge_failed": False, "self_beat": True, "score": 0.0}


def test_exposure_needs_the_judge_to_fail_and_the_antagonist_to_beat_the_baseline():
    # The judge blesses a return that does not pay off; the antagonist said it would not.
    runtime, about = _three_events(Provider(self_payoff=0.0, judge_payoff=1.0, optional=False))
    assert runtime.queue.history(about)[-1].score == 1.0
    # Same judge failure, but the antagonist committed nothing: no attributable exposure.
    runtime, about = _three_events(Provider(self_payoff=None, judge_payoff=1.0, optional=False))
    assert runtime.queue.history(about)[-1].score == 0.0
    # The antagonist forecast wrongly too: the failure is not the judge's alone.
    runtime, about = _three_events(Provider(self_payoff=1.0, judge_payoff=1.0, optional=False))
    assert runtime.queue.history(about)[-1].score == 0.0


def test_an_accurate_judge_on_the_first_return_is_not_exposed_by_the_antagonists_outcome():
    """Codex finding: the antagonist's self-forecast is sealed and scored first, and its
    outcome used to enter the prevalence baseline before the judge's forecast about the
    same return was scored. On the first return (y = 0) the baseline moved from 0.5 to 0,
    so a judge saying q = 0.1 (Brier 0.99) lost to a baseline Brier of 1.0 and was marked
    judge_failed. Every forecast about one outcome is scored against the same pre-outcome
    baseline."""
    runtime, about = _three_events(Provider(self_payoff=0.0, judge_payoff=0.1, optional=False))
    items = runtime.ledger._recovery_items()
    outcomes = [i for i in items if i["kind"] == "forecast.consequence"
                and i["about_handle"] == about]
    assert [i["y"] for i in outcomes] == [0, 0]
    assert [i["baseline_brier"] for i in outcomes] == [0.75, 0.75]  # one pre-outcome snapshot
    assert [i["brier"] for i in outcomes] == [1.0, 0.99]  # antagonist, then the judge
    settled = next(i for i in items if i["kind"] == "exposure.settled")
    assert settled == {**settled, "judge_failed": False, "self_beat": True, "score": 0.0}
    assert runtime.queue.history(about)[-1].score == 0.0
    assert runtime.stats.exposures_won == 0
    # The outcome still enters the base rate for later returns.
    assert runtime.baseline.baseline_q("return_paid_off") == 0.0


def test_exposure_settles_through_the_antagonist_cards():
    runtime = _consequence_runtime(
        provider=Provider(self_payoff=0.0, judge_payoff=1.0, optional=False)
    )
    charter = runtime.charter
    card = MetricCard("quiet", charter.norms[0], "noop share", "fraction", "window",
                      "below 0.1", "noop_share", "antagonist")
    runtime.charter = Charter(charter.edition, charter.norms, (*charter.cards, card))
    runtime._derive_regions()
    runtime.controller.set_price("quiet", 0.5, amendment_id="test")
    runtime.stats.last_window_values = {"noop_share": 1.0}
    about, event = _consequence_produce(runtime, "antagonist-a", "exposure")
    _consequence_judge(runtime, event, "eval-a")
    penalty = next(i for i in runtime.ledger._recovery_items()
                   if i["kind"] == "price.penalty" and i["handle"] == about)
    assert penalty["channel"] == "exposure" and penalty["raw"] == 1.0
    assert 0 < penalty["penalty"] and penalty["effective"] < 1.0
    assert runtime.queue.history(about)[-1].score == penalty["effective"]


def test_three_hundred_fake_wins_cannot_move_the_antagonist_past_the_cap():
    runtime = _consequence_runtime()
    state = runtime.routers["Tick"][0]
    assert "antagonist-a" in state.universe and "seed-decider" in state.universe
    for _ in range(300):
        dist = state.learner.distribution(state.universe)
        state.learner.update(BanditFeedback("antagonist-a", 1.0, dist["antagonist-a"]))
    raw = state.learner.distribution(state.universe)
    assert raw["antagonist-a"] > 0.8  # the learner alone would hand it the traffic
    capped = runtime._cap_adversarial(raw)
    assert abs(capped["antagonist-a"] - runtime.ev.adversarial_share) < 1e-9
    assert abs(sum(capped.values()) - 1) < 1e-9
    assert all(capped[a] > raw[a] for a in raw if a != "antagonist-a")
    # The logged propensity is the capped distribution the router actually drew from.
    runtime.n += 1
    tick = Event("tick", EventKind.TICK, runtime.clock.now_ns, {"index": 0}, "test")
    runtime._route_with(state, tick)
    opened = [d for d in runtime.queue.state()["decisions"].values()
              if d.event_id == "tick"]
    record = opened[-1].propensity
    assert record.probs[record.action_ids.index("antagonist-a")] <= runtime.ev.adversarial_share


def test_scripted_run_exposure_win_rate_is_below_sixty_percent():
    summary = run_world(load_manifest("scripted"), events=400, seed=1)
    stats = summary["stats"]
    assert stats["exposures_settled"] >= 10
    assert stats["exposures_won"] / stats["exposures_settled"] < 0.6


def test_antagonist_self_forecast_is_sealed_about_its_own_return():
    runtime = _consequence_runtime(provider=Provider(self_payoff=0.25))
    about, _ = _consequence_produce(runtime, "antagonist-a", "exposure")
    seal = next(i for i in runtime.ledger._recovery_items() if i["kind"] == "forecast.seal")
    assert seal["evaluator_id"] == "antagonist-a" and seal["about_handle"] == about
    assert seal["q"] == 0.25 and seal["predicate_id"] == "return_paid_off"
    # A producer's payoff field is not sealed: only antagonists forecast about themselves.
    runtime = _consequence_runtime(provider=Provider(self_payoff=0.25))
    _consequence_produce(runtime, "seed-decider")
    assert not [i for i in runtime.ledger._recovery_items() if i["kind"] == "forecast.seal"]


def test_adversarial_share_is_a_hard_cast_in_every_manifest():
    for name in ("scripted", "testnet", "edition1-example"):
        manifest = load_manifest(name)
        assert manifest.evaluation.adversarial_share == 0.15
    base = load_manifest("scripted")
    bad = replace(base, evaluation=replace(base.evaluation, adversarial_share=1.5))
    with pytest.raises(ValueError, match="adversarial_share"):
        bad.validate()
