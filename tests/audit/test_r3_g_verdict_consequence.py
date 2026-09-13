"""T16 (seat 2, finding 3): a verdict is a prediction that the judged return will not be
blamed by the charter. It settles when the return's window closes, its Brier enters the
judge's standing beside payoff skill, the meta is graded against it too, and an antagonist
exposes a judge whose high verdict landed on a return the window blamed."""

from dataclasses import replace
from types import SimpleNamespace

import pytest

from factorylab.charter.charter import Charter, MetricCard
from factorylab.charter.windows import MetricWindow
from factorylab.kernel.events import EventKind
from factorylab.kernel.queue import SettleStatus
from factorylab.runtime.loop import Runtime, run_world
from factorylab.runtime.resume import resume_world, runtime_state
from factorylab.runtime.shared import CH_FAST
from factorylab.runtime.worlds import load_manifest
from factorylab.settlement import ConsequenceStanding
from factorylab.world.scripted import ScriptedProvider
from tests.runtime.test_loop import (
    _consequence_decision,
    _consequence_judge,
    _consequence_produce,
    _consequence_runtime,
)


class Colluders(ScriptedProvider):
    """Seat 2's strategy: the producer does nothing, its evaluator endorses it (verdict 1)
    while accurately predicting it will not pay off (payoff 0), and the meta conforms."""

    def __init__(self, *, verdict=1.0, payoff=0.0, conformity=1.0, self_payoff=None):
        super().__init__()
        self.verdict, self.payoff, self.conformity = verdict, payoff, conformity
        self.self_payoff = self_payoff

    def _produce(self, desc, inputs):
        reply = {"action": "hold"}
        if self.self_payoff is not None:
            reply["payoff"] = self.self_payoff
        return reply

    def _evaluate(self, req, inputs):
        return {"verdict": self.verdict, "payoff": self.payoff, "rationale": "fine",
                "forecasts": []}

    def _meta(self, inputs):
        return {"conformity": self.conformity, "rationale": "conforms"}


def _blaming_runtime(provider, *, answers_for="producer"):
    """A world whose charter blames inaction: a priced noop-share card that the lazy
    return violates alone, measured on the window it worked in."""
    runtime = _consequence_runtime(provider=provider)
    runtime._manage_reserve_window()  # window 1 opens; regions derive from the charter
    charter = runtime.charter
    card = MetricCard("no-inaction", charter.norms[0], "noop share", "fraction",
                      MetricWindow("returns", 1, "role"), "below 0.1", "noop_share",
                      answers_for)
    runtime.charter = Charter(charter.edition, charter.norms, (*charter.cards, card))
    runtime._derive_regions()
    runtime.controller.set_price("no-inaction", 0.5, amendment_id="test")
    return runtime


def _close_window(runtime):
    runtime.clock.now_ns += runtime.m.novelty.window_ns
    runtime._manage_reserve_window()
    runtime._settle_due_forecasts()


def _meta_step(runtime, verdict_event):
    handle = _consequence_decision(runtime, "meta-a", CH_FAST)
    runtime.n += 1
    runtime._meta_step(verdict_event, handle, SimpleNamespace(chosen="meta-a"),
                       runtime.queue.get(handle).deadline_ns)
    return handle


def _items(runtime, kind):
    return [i for i in runtime.ledger._recovery_items() if i["kind"] == kind]


def test_seat_2_strategy_costs_the_evaluator_standing_once_the_window_blames_the_return():
    runtime = _blaming_runtime(Colluders())
    about, event = _consequence_produce(runtime, "seed-decider")  # a hold: y = 0
    judge = _consequence_judge(runtime, event, "eval-a")
    # Before the window closes every tier still scores perfectly: the producer is paid the
    # verdict, the payoff forecast was accurate, nothing has judged the endorsement yet.
    assert runtime.queue.history(about)[0].score == 1.0
    assert runtime.standing.skill("eval-a") > 0
    assert _items(runtime, "verdict.consequence") == []
    _close_window(runtime)
    settled = _items(runtime, "verdict.consequence")
    assert len(settled) == 1
    item = settled[0]
    assert item["handle"] == judge and item["about_handle"] == about
    assert item["evaluator_id"] == "eval-a" and item["q"] == 1.0
    assert item["share"] == 1.0 and item["outcome"] == 0.0  # the window blamed it alone
    assert item["brier"] == 0.0 and item["baseline_brier"] == 0.75
    assert item["window"] == 1 and item["window_closed"] is True
    assert item["terms"][0]["card_id"] == "no-inaction"
    # The endorsement now costs standing: payoff skill +0.25, verdict skill -0.75, pooled.
    standing = runtime.standing.snapshot()["eval-a"]
    assert standing["n"] == 1 and standing["verdict_n"] == 1
    assert standing["payoff_skill"] == pytest.approx(0.25)
    assert standing["verdict_skill"] == pytest.approx(-0.75)
    assert runtime.standing.skill("eval-a") == pytest.approx(-0.25)
    assert runtime.standing.weight("eval-a") == pytest.approx(0.25)
    # The producer's own reward is still the verdict, unchanged.
    assert runtime.queue.history(about)[0].score == 1.0
    # The judge is told, privately.
    memory = next(e for e in runtime.memory["eval-a"] if e["handle"] == judge)
    assert memory["judged_return_blamed"] == 1.0 and memory["your_verdict_brier"] == 0.0
    assert runtime._standing_for("eval-a")["settled_verdicts"] == 1
    assert runtime.ledger.verify()


def test_an_honest_verdict_on_a_blamed_return_gains_standing():
    runtime = _blaming_runtime(Colluders(verdict=0.0, payoff=0.0))
    _, event = _consequence_produce(runtime, "seed-decider")
    _consequence_judge(runtime, event, "eval-a")
    _close_window(runtime)
    item = _items(runtime, "verdict.consequence")[0]
    assert item["share"] == 1.0 and item["brier"] == 1.0 and item["baseline_brier"] == 0.75
    assert runtime.standing.snapshot()["eval-a"]["verdict_skill"] == pytest.approx(0.25)
    assert runtime.standing.skill("eval-a") == pytest.approx(0.25)


def test_a_verdict_on_an_unblamed_return_settles_at_one():
    runtime = _consequence_runtime(provider=Colluders(verdict=1.0, payoff=0.0))
    runtime._manage_reserve_window()
    _, event = _consequence_produce(runtime, "seed-decider")
    _consequence_judge(runtime, event, "eval-a")
    _close_window(runtime)
    item = _items(runtime, "verdict.consequence")[0]
    assert item["share"] == 0.0 and item["outcome"] == 1.0 and item["brier"] == 1.0
    assert runtime.standing.snapshot()["eval-a"]["verdict_skill"] == pytest.approx(0.25)


def test_a_verdict_whose_window_never_closes_settles_at_one_at_the_backstop():
    manifest = load_manifest("scripted")
    manifest = replace(manifest, evaluation=replace(manifest.evaluation,
                                                    consequence_backstop_events=30))
    runtime = _consequence_runtime(provider=Colluders(), manifest=manifest)
    runtime._manage_reserve_window()
    _, event = _consequence_produce(runtime, "seed-decider")
    _consequence_judge(runtime, event, "eval-a")
    runtime.n = 29
    runtime._settle_due_forecasts()
    assert _items(runtime, "verdict.consequence") == []
    runtime.n = 31
    runtime._settle_due_forecasts()
    item = _items(runtime, "verdict.consequence")[0]
    assert item["window"] == 1 and item["window_closed"] is False
    assert item["share"] == 0.0 and item["outcome"] == 1.0 and item["terms"] == []


def test_the_meta_is_graded_against_the_verdicts_normative_outcome_too():
    runtime = _blaming_runtime(Colluders())
    _, event = _consequence_produce(runtime, "seed-decider")
    _consequence_judge(runtime, event, "eval-a")
    verdict = next(e for e in runtime.internal if e.kind is EventKind.VERDICT)
    meta = _meta_step(runtime, verdict)
    # The payoff forecast already settled (accurately); the meta still waits for the charter.
    assert runtime.queue.get(meta).status is SettleStatus.PENDING
    assert _items(runtime, "meta.awaiting_consequence")
    _close_window(runtime)
    outcome = runtime.queue.history(meta)[0]
    assert outcome.status is SettleStatus.SETTLED and outcome.score == 0.0
    item = _items(runtime, "meta.consequence")[0]
    assert item["y"] == 0 and item["conformity"] == 1.0
    # A meta arriving after both facts are known settles at once on the same outcome.
    late = _meta_step(runtime, verdict)
    assert runtime.queue.history(late)[0].score == 0.0


def test_the_meta_scores_when_the_verdict_and_the_payoff_forecast_both_beat_baseline():
    runtime = _blaming_runtime(Colluders(verdict=0.0, payoff=0.0, conformity=1.0))
    _, event = _consequence_produce(runtime, "seed-decider")
    _consequence_judge(runtime, event, "eval-a")
    verdict = next(e for e in runtime.internal if e.kind is EventKind.VERDICT)
    meta = _meta_step(runtime, verdict)
    _close_window(runtime)
    assert runtime.queue.history(meta)[0].score == 1.0
    assert _items(runtime, "meta.consequence")[0]["y"] == 1


def test_an_antagonist_exposes_a_high_verdict_on_a_return_the_window_blamed():
    runtime = _blaming_runtime(Colluders(self_payoff=0.0), answers_for="antagonist")
    about, event = _consequence_produce(runtime, "antagonist-a", "exposure")
    _consequence_judge(runtime, event, "eval-a")
    runtime.n = runtime.ev.verdict_timeout_events + 5
    runtime._settle_due_forecasts()
    # The payoff facts alone would settle 0 (the judge predicted the payoff correctly); the
    # exposure waits while the verdict is still unsettled.
    assert about in runtime.pending_exposure
    _close_window(runtime)
    assert about not in runtime.pending_exposure
    settled = _items(runtime, "exposure.settled")[0]
    assert settled == {**settled, "judge_failed": False, "self_beat": True,
                       "verdict_exposed": True, "score": 1.0}
    assert runtime.stats.exposures_won == 1
    assert runtime.queue.history(about)[-1].status is SettleStatus.SETTLED


def test_a_low_verdict_on_a_blamed_antagonist_return_is_not_exposed():
    runtime = _blaming_runtime(Colluders(verdict=0.7, self_payoff=0.0),
                               answers_for="antagonist")
    about, event = _consequence_produce(runtime, "antagonist-a", "exposure")
    _consequence_judge(runtime, event, "eval-a")
    runtime.n = runtime.ev.verdict_timeout_events + 5
    _close_window(runtime)
    settled = _items(runtime, "exposure.settled")[0]
    assert settled == {**settled, "verdict_exposed": False, "score": 0.0}
    assert _items(runtime, "verdict.consequence")[0]["share"] == 1.0


def test_two_judges_of_one_return_share_its_pre_outcome_baseline():
    runtime = _blaming_runtime(Colluders())
    _, event = _consequence_produce(runtime, "seed-decider")
    _consequence_judge(runtime, event, "eval-a")
    runtime.provider.target.verdict = 0.2
    _consequence_judge(runtime, event, "eval-c")
    _close_window(runtime)
    items = _items(runtime, "verdict.consequence")
    assert [i["evaluator_id"] for i in items] == ["eval-a", "eval-c"]
    assert [i["baseline_brier"] for i in items] == [0.75, 0.75]
    assert items[1]["brier"] == pytest.approx(0.96)
    assert runtime.baseline.baseline_q("verdict_not_blamed") == 0.0  # recorded once


def test_verdict_standing_is_pooled_with_payoff_standing_and_coverage_is_payoff_only():
    standing = ConsequenceStanding(0.75)
    standing.set_requested("judge", 1)
    standing.record("judge", 1.0, 0.75)
    standing.record_verdict("judge", 0.0, 0.75)
    assert standing.skill("judge") == pytest.approx(-0.25)
    assert standing.coverage("judge") == 1.0
    assert standing.weight("judge") == pytest.approx(0.25)
    snapshot = standing.snapshot()["judge"]
    assert snapshot["n"] == 1 and snapshot["verdict_n"] == 1
    assert snapshot["payoff_skill"] == pytest.approx(0.25)
    assert snapshot["verdict_skill"] == pytest.approx(-0.75)
    only = ConsequenceStanding(0.75)
    only.record_verdict("judge", 1.0, 0.5)
    assert only.skill("judge") == 0.5 and only.weight("judge") == 0.5  # no payoff coverage


class ProcessDeath(BaseException):
    pass


def _short_window_manifest():
    base = load_manifest("scripted")
    return replace(base, novelty=replace(base.novelty, window_ns=2 * base.tick_interval_ns))


@pytest.mark.slow
def test_resume_after_a_verdict_consequence_item_replays_identically(tmp_path):
    m = _short_window_manifest()
    path = tmp_path / "verdict.jsonl"
    rt = Runtime(m, events=12, seed=1, initial_balance_micro=None, ledger_path=str(path),
                 drip=True, router_gamma=.1)
    append = rt.ledger.append
    seen = []

    def crash_after_append(entry):
        seq = append(entry)
        if entry["kind"] == "verdict.consequence":
            seen.append(entry)
            raise ProcessDeath
        return seq

    rt.ledger.append = crash_after_append
    with pytest.raises(ProcessDeath):
        rt.run()
    assert seen
    resumed = resume_world(m, str(path))
    resumed["stats"]["resumes"] = 0
    assert resumed == run_world(m, events=12, seed=1)


@pytest.mark.slow
def test_pending_verdict_commitments_survive_a_checkpoint(tmp_path):
    from factorylab.runtime.resume import resume_runtime

    m = _short_window_manifest()
    path = tmp_path / "commitments.jsonl"
    rt = Runtime(m, events=12, seed=1, initial_balance_micro=None, ledger_path=str(path),
                 drip=True, router_gamma=.1)
    original = rt._process_event

    def interrupted(event):
        result = original(event)
        if any(p.channel == "verdict.norm" for p in rt.pending.values()):
            raise ProcessDeath  # a verdict is committed and its window has not closed
        return result

    rt._process_event = interrupted
    with pytest.raises(ProcessDeath):
        rt.run()
    restored = resume_runtime(m, str(path))
    restored.stats.resumes = 0
    assert runtime_state(restored) == runtime_state(rt)
    assert any(p.channel == "verdict.norm" for p in restored.pending.values())
    restored.stats.resumes = 1
    assert restored.run()["ledger_verify"]
