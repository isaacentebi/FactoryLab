"""Round three, merge triage: the defects the merged main carried, each pinned.

Three findings, each reproduced at the mechanism rather than at the world summary:

* ``_settle_due_verdicts`` closed a verdict out *at one* when the window that would
  have judged the return never closed, or when the attribution evidence had been
  released before it could be read. A missing fact became a scored outcome: the
  judge's standing moved, the base rate of unblamed returns moved, and in a world
  whose measurement window is long relative to the consequence backstop — the two
  scripted worlds — every verdict was scored that way. It now carries no
  information and is closed out unscored.
* The executor's own id was added to a request's inputs *after* the caller had
  priced it, so every ceiling derived from that request was smaller than the
  prompt actually sent. The assembly stamps the identity where it renders the
  prompt instead.
* The scripted world's late registrations are counted in producer calls, not
  events, and a 260-event run reached them; they belong past the short runs that
  assert on the one amendment the world proposes early.
"""

from dataclasses import replace

import pytest

from factorylab.runtime.loop import Runtime
from factorylab.runtime.worlds import load_manifest
from factorylab.world.models import ModelResponse
from factorylab.world.scripted import _inputs_from_prompt
from tests.audit.test_r3_g_verdict_consequence import Colluders, _blaming_runtime, _items
from tests.conftest import make_runtime
from tests.runtime.test_loop import (
    _consequence_judge,
    _consequence_produce,
    _consequence_runtime,
)


def _unclosing_runtime(provider, *, backstop=30):
    """A runtime whose measurement window does not close inside the backstop."""
    manifest = load_manifest("scripted")
    manifest = replace(manifest, evaluation=replace(manifest.evaluation,
                                                    consequence_backstop_events=backstop))
    runtime = _consequence_runtime(provider=provider, manifest=manifest)
    runtime._manage_reserve_window()
    return runtime


def test_an_unread_verdict_moves_neither_standing_nor_the_unblamed_base_rate():
    """The window never closed, so nothing judged these returns. Scoring them at one
    would have told every judge it was right and told the base rate that returns are
    never blamed — both out of thin air."""
    runtime = _unclosing_runtime(Colluders(verdict=1.0, payoff=0.0))
    for _ in range(3):
        _, event = _consequence_produce(runtime, "seed-decider")
        _consequence_judge(runtime, event, "eval-a")
    runtime.n = 40
    runtime._settle_due_forecasts()
    assert len(_items(runtime, "verdict.unread")) == 3
    assert _items(runtime, "verdict.consequence") == []
    standing = runtime.standing.snapshot()["eval-a"]
    assert standing["verdict_n"] == 0 and standing["verdict_skill"] == 0.0
    assert runtime.standing.skill("eval-a") == pytest.approx(standing["payoff_skill"])
    # The base rate of unblamed returns never saw them: the next verdict that is
    # really read is scored against the prior, not against three invented ones.
    scored = runtime.settler.settle_verdict(
        evaluator_id="eval-b", about_handle="unrelated", q=1.0, share=0.0)
    assert scored.baseline_brier == 0.75  # brier(0.5, 1.0); an unblamed base rate gives 1.0


def test_a_read_verdict_still_scores_beside_the_unread_ones():
    """The fix removes invented facts, not the mechanism: a window that does close and
    does blame the return still costs the judge that endorsed it."""
    runtime = _blaming_runtime(Colluders(verdict=1.0, payoff=0.0))
    _, event = _consequence_produce(runtime, "seed-decider")
    _consequence_judge(runtime, event, "eval-a")
    runtime.clock.now_ns += runtime.m.novelty.window_ns
    runtime._manage_reserve_window()
    runtime._settle_due_forecasts()
    item = _items(runtime, "verdict.consequence")[0]
    assert item["window_closed"] is True and item["share"] == 1.0
    assert runtime.standing.snapshot()["eval-a"]["verdict_n"] == 1
    assert _items(runtime, "verdict.unread") == []


def test_a_meta_that_conformed_to_an_unread_verdict_is_graded_on_the_payoff_fact_alone():
    """With no normative fact there is one thing the verdict could be right about, and
    the meta is graded on it exactly as it was before verdicts had consequences."""
    from factorylab.kernel.events import EventKind
    from tests.audit.test_r3_g_verdict_consequence import _meta_step

    runtime = _unclosing_runtime(Colluders(verdict=1.0, payoff=0.0, conformity=1.0))
    _, event = _consequence_produce(runtime, "seed-decider")
    _consequence_judge(runtime, event, "eval-a")
    verdict = next(e for e in runtime.internal if e.kind is EventKind.VERDICT)
    meta = _meta_step(runtime, verdict)
    runtime.n = 40
    runtime._settle_due_forecasts()
    assert _items(runtime, "verdict.unread")
    assert _items(runtime, "meta.consequence")[0]["y"] == 1  # the payoff forecast was right
    assert runtime.queue.history(meta)[0].score == 1.0


def test_a_world_whose_window_never_closes_scores_no_verdict_at_all():
    """The world-level shape of the same finding: scripted-crash measures over a day, so
    inside a short run nothing is ever attributed and no judge answers for a verdict."""
    runtime = Runtime(load_manifest("scripted-crash"), events=40, seed=2,
                      initial_balance_micro=None, ledger_path=None, drip=True, router_gamma=.1)
    try:
        runtime.run()
        items = runtime.ledger._recovery_items()
    finally:
        runtime._ledger_lock.close()
    assert not [i for i in items if i["kind"] == "price.window"]
    assert [i for i in items if i["kind"] == "verdict.unread"]
    assert not [i for i in items if i["kind"] == "verdict.consequence"]
    snapshot = runtime.standing.snapshot()
    assert snapshot and all(s["verdict_n"] == 0 for s in snapshot.values())
    assert all(s["skill"] == pytest.approx(s["payoff_skill"]) for s in snapshot.values())


def test_the_ceiling_a_caller_prices_is_the_ceiling_the_meter_enforces(monkeypatch):
    """Whatever the request carries, the assembly's own rendering is what is billed, so a
    ceiling taken from ``build_model_request`` bounds the real call."""
    runtime = make_runtime(balance=1_000_000)
    monkeypatch.setattr(runtime.provider.target, "complete", lambda req: ModelResponse(
        req.model_id, '{"action":"hold"}', 1, 1, "stop", cost_micro=1_100_000))
    request = runtime._request("caller", "test", {}, {}, 100, "test")
    assembly = runtime.assemblies["seed-decider"]
    priced = assembly.build_model_request(request)
    returned = runtime._invoke("seed-decider", request, "producer")
    assert returned.cost == assembly.model.ceiling(priced)
    assert runtime.wallet.balance == 1_000_000 - returned.cost


def test_the_rendered_prompt_names_its_executor_and_no_caller_can_forge_it():
    runtime = make_runtime()
    assembly = runtime.assemblies["seed-observer"]
    honest = runtime._request("caller", "test", {}, {}, 100, "test")
    forged = runtime._request("caller", "test", {"you": "seed-decider"}, {}, 100, "test")
    for request in (honest, forged):
        prompt = assembly.build_model_request(request).messages[-1]["content"]
        assert _inputs_from_prompt(prompt)["you"] == "seed-observer"
    # The stamp lives in the rendering, not in the request: the caller's own object is
    # untouched, so nothing downstream of it sees an identity it did not write.
    assert forged.inputs == {"you": "seed-decider"} and honest.inputs == {}


def test_a_short_scripted_run_only_ever_ballots_the_early_amendment(scripted_runtime_run):
    """The scripted world's late registrations are keyed to producer calls, and a run makes
    several producer calls per event, so "late" has to be counted in the same units. The
    short runs must see only the amendment the world proposes early, with its price."""
    record = scripted_runtime_run(load_manifest("scripted"), 260, 1, record_requests=True)
    ballots = [r["amendment"] for r in record.requests if "amendment" in r]
    assert ballots and {b["id"] for b in ballots} == {"turnover-card"}
    assert all(b["add"][0]["lambda"] == 0.6 for b in ballots)
