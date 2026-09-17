"""R3-D: evaluation and time. The plan's acceptance, one test per clause.

``docs/plans/edition3-r3.md``, R3-D: "no producer return exists for an empty
draw; a judged hold with no commitment settles ``unmeasured``; a forecast at a
0.99 base rate moves no standing; three simultaneous arrivals do not trigger a
tier; an objection resolves through a different judge and reprices the card only
through the population's route", plus the two the workstream adds from the same
reading: a declined commission costs only the call (§6.B), and the
``pending_meta`` case PR #97 exposed settles unmeasured rather than timing out
at zero.

No provider, network, venue, disk diary or key is used.
"""

from __future__ import annotations

import json
from dataclasses import replace
from types import SimpleNamespace

import pytest

from factorylab.kernel.events import Event, EventKind
from factorylab.kernel.ledger import Ledger
from factorylab.kernel.queue import SettleStatus
from factorylab.runtime.cascade import CascadeGate, release_window_ns
from factorylab.runtime.feedback import NORM_COMMITMENT, PendingJudgement
from factorylab.runtime.shared import NOOP
from factorylab.runtime.worlds import load_manifest
from factorylab.settlement.fidelity import (
    FidelityObjection,
    challenge_proposal,
    choose_adjudicator,
)
from factorylab.settlement.forecast import Forecast, ForecastBook
from factorylab.settlement.receipts import Adjudication, ReceiptBook
from factorylab.settlement.scoring import PrevalenceBaseline
from factorylab.settlement.settle import UNINFORMATIVE_REASON, Settler
from factorylab.settlement.standing import ConsequenceStanding
from factorylab.settlement.vocabulary import (
    DECLINED_DEFINITION,
    UNMEASURED_DEFINITION,
    WindowFacts,
)
from factorylab.world.models import ModelResponse
from factorylab.world.scripted import ScriptedProvider
from tests.runtime.test_loop import (
    _consequence_decision,
    _consequence_judge,
    _consequence_produce,
    _consequence_runtime,
)


def _exhaust_novelty(runtime, seat: str) -> None:
    """Take this seat out of its exploratory allowance.

    The allowance is deliberate: while the population is still paying to find
    out what a new seat does, its returns stay evaluable whatever they commit
    to. The clauses below are about a seat the population already knows.
    """
    runtime.stats.consequences_by_assembly[seat] = runtime.m.novelty.trials
    runtime.stats.registered_window[seat] = -runtime.m.novelty.max_lifetime_windows


# ---- the empty draw is not a producer return (the kept regression) ---------------------

def test_no_producer_return_exists_for_an_empty_draw():
    """Kept from R3-A: "no authored decision means no producer return to grade" (§4).

    R3-D's commissions must not reintroduce it by another door: an abstention
    settles inapplicable on its own channel and nothing is invoked, scored or
    judged.
    """
    runtime = _consequence_runtime()
    runtime.n += 1
    handle = _consequence_decision(runtime, "NOOP", "verdict")
    invoked = []
    runtime._invoke = lambda *a, **kw: invoked.append(a)  # never reached
    runtime._assembly_step(
        Event("tick-1", EventKind.TICK, runtime.clock.now_ns, {"index": 0}, "test"),
        handle, SimpleNamespace(chosen=NOOP), runtime.queue.get(handle).deadline_ns)
    assert invoked == []
    assert runtime.queue.get(handle).status is SettleStatus.INAPPLICABLE
    assert runtime.book.outstanding() == 0


# ---- a judged hold with no commitment settles unmeasured -------------------------------

class _Judge(ScriptedProvider):
    """A judge whose answer is fixed, whatever it is asked."""

    def __init__(self, reply):
        super().__init__()
        self.reply = reply

    def complete(self, req):
        text = "\n".join(str(m.get("content", "")) for m in req.messages)
        if "Evaluate" not in text:
            return super().complete(req)
        return ModelResponse(req.model_id, json.dumps(self.reply), 300, 40, "end_turn")


def test_a_judged_hold_with_no_commitment_settles_unmeasured():
    """§6.B: "a hold is judged only against something it committed to ... never 'looks
    prudent'". A return that held, promised nothing, claimed nothing and spent nothing
    offers no fact to be right about, so the commission concludes unmeasured: no score,
    no standing, no price, and the judged return is not settled by it either."""
    runtime = _consequence_runtime(
        provider=_Judge({"verdict": 0.9, "rationale": "looks prudent", "forecasts": []}))
    about, event = _consequence_produce(runtime, "NOOP")
    _exhaust_novelty(runtime, "NOOP")
    judge = _consequence_judge(runtime, event, "eval-a")
    decision = runtime.queue.get(judge)
    assert decision.status is SettleStatus.INAPPLICABLE
    assert runtime.queue.history(judge)[0].definition_version == UNMEASURED_DEFINITION
    # Nothing was learned about anyone: no standing moved, no verdict priced the hold.
    assert runtime.standing.snapshot().get("eval-a") is None
    assert runtime.queue.get(about).status is SettleStatus.PENDING
    unmeasured = [i for i in runtime.ledger._recovery_items()
                  if i["kind"] == "evaluation.unmeasured" and i["handle"] == judge]
    assert len(unmeasured) == 1 and "committed to nothing" in unmeasured[0]["reason"]


def test_a_hold_that_committed_to_something_is_still_judged():
    """The same clause from the other side: a hold that carried a forecast, a
    subscription change or any other resource decision commits to something, and the
    evaluation of it proceeds exactly as before."""
    runtime = _consequence_runtime(
        provider=_Judge({"verdict": 0.9, "payoff": 0.4, "rationale": "r", "forecasts": []}))
    _exhaust_novelty(runtime, "NOOP")
    about, event = _consequence_produce(runtime, "NOOP")
    # The same quiet action, with a resource decision attached: the seat declined
    # its next three routine wakes, which is a decision about its own money.
    event = replace(event, payload={**event.payload,
                                    "outputs": {"action": "hold", "defer": 3}})
    runtime.return_events[about] = event
    judge = _consequence_judge(runtime, event, "eval-a")
    assert runtime.queue.get(judge).status is SettleStatus.PENDING  # awaiting its meta
    assert runtime.queue.get(about).status is SettleStatus.SETTLED


# ---- easy questions do not pay ---------------------------------------------------------

def test_a_forecast_at_a_099_base_rate_moves_no_standing():
    """§3 further: "forecast-shaped returns can reward easy questions"; §10: "easy
    forecasts dominate". A predicate the world answers 99 times in 100 is not a claim
    anyone can be right about. It settles, it is observed, its receipt says why it was
    not scored, and no standing moves."""
    ledger = Ledger()
    book = ForecastBook(ledger)
    baseline = PrevalenceBaseline()
    standing = ConsequenceStanding(min_coverage=0.0)
    for i in range(100):  # 99 positives in 100: a base rate of 0.99 on real support
        baseline.record("wallet_up", int(i > 0))
    settled_handles = []
    queue = SimpleNamespace(settle=lambda handle, **kw: settled_handles.append((handle, kw)))
    observer = SimpleNamespace(observe=lambda *a, **kw: 1)
    settler = Settler(book, queue, standing, baseline, observer)
    forecast = book.seal(Forecast("d1", "eval-a", "producer-1", "wallet_up",
                                  {"horizon_events": 2}, 0.99, 0, 2))
    (result,) = settler.settle_due(
        5, lambda f: WindowFacts(1, 2, 1, ()))
    assert result.status is SettleStatus.INAPPLICABLE
    assert result.brier is None and result.y == 1
    assert result.excluded == UNINFORMATIVE_REASON
    assert standing.snapshot() == {}  # no skill, no coverage, no weight
    receipt = book.receipts.get(result.receipt)
    assert receipt.score is None and receipt.reason == UNINFORMATIVE_REASON
    assert settled_handles[0][1]["definition_version"] == "uninformative-baseline-v1"
    assert forecast.q == 0.99


def test_an_open_question_still_pays():
    """The bound is on the question, not on the forecaster: a predicate the world
    answers either way is scored exactly as it always was, and a base rate with no
    support behind it is not evidence that anything is easy."""
    ledger = Ledger()
    book = ForecastBook(ledger)
    baseline = PrevalenceBaseline()
    standing = ConsequenceStanding(min_coverage=0.0)
    for i in range(100):
        baseline.record("wallet_up", i % 2)
    settler = Settler(book, {"settle": None} and SimpleNamespace(settle=lambda *a, **kw: None),
                      standing, baseline, SimpleNamespace(observe=lambda *a, **kw: 1))
    book.seal(Forecast("d1", "eval-a", "producer-1", "wallet_up",
                       {"horizon_events": 2}, 0.9, 0, 2))
    (result,) = settler.settle_due(5, lambda f: WindowFacts(1, 2, 1, ()))
    assert result.status is SettleStatus.SETTLED and result.brier == pytest.approx(0.99)
    assert standing.snapshot()["eval-a"]["settled"] == 1


# ---- cascade: time and completed evidence ----------------------------------------------

def _verdict(index, ts_ns, *, about="producer-1"):
    return Event(f"verdict-{index}", EventKind.VERDICT, ts_ns,
                 {"verdict": 1.0, "evaluator_handle": f"judge-{index}", "about_handle": about},
                 "judge")


def test_three_simultaneous_arrivals_do_not_trigger_a_tier():
    """§3: "three messages arriving together satisfy the separation" was a launch
    blocker. They are three arrivals in an empty window now, and the window is a
    duration: nothing is released until it has elapsed."""
    gate = CascadeGate(release_window_ns(3, 0.0, 0.0, 10), opened_ns=100)
    for i in range(3):
        gate, released = gate.add(_verdict(i, 100))
        assert released is None
    assert len(gate.arrivals) == 3
    # Nor does a fourth, a tenth or a hundredth: only time releases the window.
    for i in range(3, 100):
        gate, released = gate.add(_verdict(i, 100))
        assert released is None


def test_the_window_releases_on_elapsed_time_with_its_completed_evidence():
    """§6.C: a tier's upward report aggregates the completed evidence of its window."""
    window_ns = release_window_ns(3, 0.0, 0.0, 10)
    gate = CascadeGate(window_ns, opened_ns=0)
    gate, released = gate.add(_verdict(0, 0))
    assert released is None
    gate, released = gate.add(_verdict(1, window_ns))
    assert released is not None
    assert released.payload["window"]["count"] == 2
    assert released.payload["window"]["elapsed_ns"] == window_ns
    assert list(released.payload["window"]["handles"]) == ["judge-0", "judge-1"]
    assert gate is None


def test_a_window_of_unfinished_evidence_reports_nothing_upward():
    """A verdict whose subject has not settled is an opinion, not evidence. It is named
    in the window — the sibling share still reaches it — but the report waits."""
    window_ns = release_window_ns(3, 0.0, 0.0, 10)
    gate = CascadeGate(window_ns, opened_ns=0)
    gate, released = gate.add(_verdict(0, 0), complete=lambda e: False)
    gate, released = gate.add(_verdict(1, window_ns * 2), complete=lambda e: False)
    assert released is None and len(gate.arrivals) == 2
    _, released = gate.add(_verdict(2, window_ns * 3),
                           complete=lambda e: e.payload["evaluator_handle"] == "judge-2")
    assert dict(released.payload["window"]) == {
        "count": 1, "arrivals": 3, "window_ns": window_ns, "elapsed_ns": window_ns * 3,
        "mean": 1.0, "min": 1.0, "max": 1.0,
        "handles": ("judge-0", "judge-1", "judge-2"),
    }


def test_the_runtime_gate_is_a_duration_drawn_once_with_its_precommitted_jitter():
    """The jitter the manifest precommits is unchanged; what it now scales is time."""
    runtime = _consequence_runtime()
    for i in range(3):
        assert runtime._cascade_arrival(_verdict(i, runtime.clock.now_ns)) is None
    gate = runtime.cascade[1]
    interval = runtime.tick_clock.interval_ns
    assert gate.window_ns >= runtime.m.timing.min_ratio * interval
    assert gate.window_ns % interval == 0
    assert runtime._cascade_arrival(_verdict(3, runtime.clock.now_ns)) is None
    assert runtime.cascade[1].window_ns == gate.window_ns  # never redrawn mid-window


# ---- a fidelity objection is an adjudication -------------------------------------------

def test_an_objection_resolves_through_a_different_judge():
    """§7: "keep the objection as an unresolved claim until independent adjudication".
    The adjudicator is chosen from the seats that judge, excluding the judge that wrote
    the verdict and the seats the challenged card answers for."""
    objector, owner = "eval-a", "seed-decider"
    candidates = ["eval-a", "eval-b", "meta-1", "seed-decider"]
    who, excluded = choose_adjudicator(
        [c for c in candidates if c not in (objector, owner)],
        author=objector, measurement_owner=owner)
    assert who == "eval-b"
    assert set(excluded) == {objector, owner}
    # With nobody independent left, the claim stays open rather than being decided by
    # someone with an interest in it.
    assert choose_adjudicator([], author=objector, measurement_owner=owner)[0] is None


def test_an_adjudication_produces_a_learning_receipt_for_the_objector():
    """The objector stated a probability that its objection was right; the adjudicator's
    finding is the fact it is scored against, by the same proper score as anything else."""
    book = ForecastBook(Ledger())
    settler = Settler(book, None, ConsequenceStanding(min_coverage=0.0),
                      PrevalenceBaseline(), None)
    objection = FidelityObjection("fidelity", "censorship-bound", "a counterexample", 0.25)
    settler.record_objection(
        "judge-1", [{"handle": "judge-1", "outputs": {"fidelity_objection": {
            "value": "fidelity", "measurement": "censorship-bound",
            "evidence": "a counterexample", "uncertainty": 0.25}}}],
        evaluator_id="eval-a", about_handle="producer-1")
    adjudication = settler.adjudication_for("judge-1")
    assert adjudication.adjudicator is None and adjudication.upheld is None
    resolved, receipt = settler.resolve_adjudication(
        adjudication, adjudicator="eval-b", upheld=True, finding="the proxy misses it")
    assert resolved.id == adjudication.id  # the finding lands on the claim, not beside it
    assert resolved.adjudicator == "eval-b" and resolved.upheld is True
    learning = book.receipts.get(receipt)
    assert learning.assessed == "eval-a" and learning.handle == "judge-1"
    assert learning.score == pytest.approx(1 - (objection.confidence - 1) ** 2)
    assert objection.confidence == 0.75


def test_an_upheld_objection_reprices_the_card_only_through_the_population_route():
    """§7 and C3: a card's price moves when the committee moves it, never because a
    judge objected. What an upheld objection produces is a challenge proposal."""
    runtime = _consequence_runtime()
    card = next(iter(runtime.charter.cards))
    before = runtime.controller.price(card.id)
    adjudication = Adjudication(
        value="fidelity", measurement=card.id, evidence="a counterexample",
        objector="eval-a", objection_handle="judge-1", about_handle="producer-1",
        uncertainty=0.25)
    runtime.settler.receipts().record(adjudication)
    runtime.open_adjudications["eval-b"] = adjudication.id
    registered = []
    runtime._apply_registrations = lambda handle, ret: registered.append(ret.outputs["register"])
    runtime._resolve_adjudication("eval-b", "judge-2", {"upheld": True, "reason": "it does"})
    assert registered == [[challenge_proposal(
        runtime.settler.receipts().get(adjudication.id), replacement=None)]]
    assert registered[0][0]["kind"] == "challenge" and registered[0][0]["card_id"] == card.id
    assert runtime.controller.price(card.id) == before  # no kernel reprice, ever
    assert "eval-b" not in runtime.open_adjudications
    item = runtime.outcomes.get("eval-a", "judge-1")
    assert item["outcome"]["fidelity_objection_upheld"] is True


# ---- a commission may be declined ------------------------------------------------------

def test_a_declined_commission_costs_only_the_call():
    """§6.B: "a seat may decline a commission by answering ``cannot`` and is charged only
    the call"; no activity quota anywhere. The decision settles inapplicable under the
    declined definition: no score against it, and nothing enters its standing."""
    runtime = _consequence_runtime(
        provider=_Judge({"status": "cannot", "reason": "I cannot measure this"}))
    _, event = _consequence_produce(runtime, "NOOP")
    before = runtime.wallet.balance
    judge = _consequence_judge(runtime, event, "eval-a")
    history = runtime.queue.history(judge)
    assert runtime.queue.get(judge).status is SettleStatus.INAPPLICABLE
    assert history[0].definition_version == DECLINED_DEFINITION
    assert history[0].score == 0.0 and history[0].status is SettleStatus.INAPPLICABLE
    assert runtime.standing.snapshot().get("eval-a") is None
    # The call is the whole cost: what the wallet paid is what the invocation cost.
    charged = before - runtime.wallet.balance
    assert charged == runtime.consequences.table.account(judge).cost_micro > 0
    declined = [i for i in runtime.ledger._recovery_items()
                if i["kind"] == "evaluation.unmeasured" and i["handle"] == judge]
    assert declined and declined[0]["definition"] == DECLINED_DEFINITION


# ---- the pending_meta leak -------------------------------------------------------------

def test_a_meta_whose_judge_closed_unread_settles_unmeasured():
    """PR #97's finding, repaired here: a top meta that conformed to a verdict whose
    normative window closed unread waited in ``pending_meta`` for a payoff fact that
    never came and timed out at zero. It is unmeasured — the fact the runtime owed it
    was never produced, and that is not the meta's failure."""
    runtime = _consequence_runtime()
    runtime.n += 1
    judge = _consequence_decision(runtime, "eval-a", "conformity")
    meta = _consequence_decision(runtime, "meta-1", "fast")
    about = _consequence_decision(runtime, "seed-decider", "verdict")
    key = f"{NORM_COMMITMENT}:{judge}"
    runtime.pending[key] = PendingJudgement(
        key, NORM_COMMITMENT, runtime.n, about=about, judge=judge, evaluator_id="eval-a",
        q=0.9, cards="producer", window=9_999, awaits_payoff=False)
    runtime.pending_meta[judge] = [(meta, 0.8)]
    runtime.n += runtime.ev.consequence_backstop_events + 1
    runtime._settle_due_verdicts()
    assert runtime.pending_meta.get(judge) in (None, [])
    assert runtime.queue.get(meta).status is SettleStatus.INAPPLICABLE
    assert runtime.queue.history(meta)[0].definition_version == UNMEASURED_DEFINITION
    assert runtime.queue.history(meta)[0].score == 0.0
    unread = [i for i in runtime.ledger._recovery_items() if i["kind"] == "verdict.unmeasured"]
    assert unread and unread[0]["handle"] == judge


def test_a_meta_waiting_on_a_judge_that_never_settles_closes_at_the_backstop():
    """The same leak from the other end: a judge with no commitment left open at all
    (its window released, its forecast censored) leaves metas waiting on nothing. Past
    the backstop they close unmeasured while they can still be closed."""
    runtime = _consequence_runtime()
    runtime.n += 1
    meta = _consequence_decision(runtime, "meta-1", "fast")
    runtime.pending_meta["judge-gone"] = [(meta, 0.8)]
    runtime._expire_pending_meta()
    assert runtime.queue.get(meta).status is SettleStatus.PENDING  # the wait has just begun
    runtime.n += runtime.ev.consequence_backstop_events + 1
    runtime._expire_pending_meta()
    assert runtime.queue.get(meta).status is SettleStatus.INAPPLICABLE
    assert runtime.pending_meta == {}


# ---- the four settlement objects -------------------------------------------------------

def test_the_four_objects_are_independently_addressable_and_ledgered():
    """§6.A. Each is addressed by an id of its own, written down before it is
    addressable, and the same object recorded twice is one object."""
    from factorylab.settlement.receipts import (
        Commitment,
        ExecutionReceipt,
        LearningReceipt,
    )

    ledger = Ledger()
    book = ReceiptBook(ledger)
    objects = [
        ExecutionReceipt("fill", "d1", "alice", 3, {"coin": "BTC", "size": "1"}),
        LearningReceipt("d1", "eval-a", "brier", "brier-v1", 10, 1, 0.96, 0.75),
        Commitment("close the position by tick 40", "alice", 40, "a Fill on BTC",
                   ("the venue does not answer",), "d1"),
        Adjudication("fidelity", "censorship-bound", "evidence", "eval-a", "judge-1",
                     "d1", 0.25),
    ]
    ids = [book.record(o) for o in objects]
    assert len(set(ids)) == 4
    assert [book.record(o) for o in objects] == ids  # recording twice records once
    assert len(book) == 4
    assert [book.get(i) for i in ids] == objects
    assert book.ids("execution") == [ids[0]] and book.ids("learning") == [ids[1]]
    assert book.ids("commitment") == [ids[2]] and book.ids("adjudication") == [ids[3]]
    rows = ledger._recovery_items()
    assert [r["kind"] for r in rows] == [
        "receipt.execution", "receipt.learning", "receipt.commitment", "receipt.adjudication"]
    # The object's own fields are the row's payload, never merged into it: an
    # execution receipt's own kind survives beside the row's.
    assert [r["id"] for r in rows] == ids
    assert rows[0]["receipt"]["kind"] == "fill"
    assert rows[2]["receipt"]["unobservable_when"] == ["the venue does not answer"]


def test_a_fill_emits_an_execution_receipt_without_changing_what_settles():
    """"Existing settlement paths emit them ... without changing what settles.\""""
    from factorylab.settlement.consequence import ReturnConsequences

    consequences = ReturnConsequences(Ledger(), 10)
    consequences.start("d1", 0)
    consequences.order_result("d1", {"status": "filled", "order_id": "o1",
                                     "filled_size": "1"}, {"size": "1"}, 0)
    consequences.observe("Fill", {"order_id": "o1", "coin": "BTC", "is_buy": True,
                                  "size": "1", "px": "100", "fee_usd": "0.1"}, 1)
    (receipt,) = consequences.receipts.all("execution")
    assert receipt.kind == "fill" and receipt.handle == "d1" and receipt.at_event == 1
    assert receipt.facts["coin"] == "BTC" and receipt.facts["px"] == "100"
    # The lot accounting is exactly what it was: one open lot on the same account.
    assert consequences.table.account("d1").opened_lots == 1


# ---- the commissioned-child-judge route ------------------------------------------------

def test_a_commissioned_child_judge_is_refused_with_a_public_reason():
    """§3 further: "the commissioned-child-judge path has incompatible exclusions (remove
    the suggestion it is usable)". It is refused before a decision is opened or a call is
    made, and the population is told why."""
    runtime = _consequence_runtime()
    parent = SimpleNamespace(handle="d1", deadline_ns=1, inputs={})
    item = SimpleNamespace(target="eval-a", inputs={}, description="judge this",
                           outcome_schema={})
    before = len(runtime.queue.outstanding())
    result, cost = runtime._invoke_child("seed-decider", parent, item, 1_000)
    assert cost == 0
    assert "cannot be commissioned as a child" in result["result"]["error"]
    assert len(runtime.queue.outstanding()) == before  # nothing was opened
    assert any("cannot be commissioned" in f["reason"]
               for f in runtime.registration_feedback)
    # A producing contract is still commissionable: only judging work is refused.
    assert runtime._commissioned_judge_refusal("seed-decider") is None


def test_the_catalogue_no_longer_offers_a_judge_as_a_request_target():
    """"Remove the commissioned-child-judge route from the catalogue ... until it can
    execute without self-judgement." The addressing text says so where the ids are."""
    runtime = _consequence_runtime()
    addressing = runtime._world_block()["addressing"]
    assert "cannot be commissioned as a child" in addressing
    assert "the router's sampling, the adversarial share and the cascade" in addressing


def _verdict_rows(runtime, kind: str) -> list[dict]:
    return [i for i in runtime.ledger._recovery_items() if i["kind"] == kind]


class _Endorser(ScriptedProvider):
    """An evaluator that endorses the return it judges and seals a payoff forecast."""

    def _produce(self, desc, inputs):
        return {"action": "hold", "subscribe": {"cadence_floor": 1}}

    def _evaluate(self, req, inputs):
        return {"verdict": 1.0, "payoff": 0.0, "rationale": "fine", "forecasts": []}


def test_a_verdict_closes_unread_once_while_its_payoff_forecast_is_still_pending():
    """Rehearsal 5's loudest defect: 12,672 ``verdict.unread`` rows over 47 judges, one
    of them 960 times. The judge's payoff forecast stays pending in the book after its
    commitment closed unread, so the per-event commitment pass re-created the same
    commitment, closed it unread again and finalized it unmeasured again, every event
    for the rest of the run. A judge handle closes unread once and is graded once."""
    manifest = load_manifest("scripted")
    manifest = replace(manifest, evaluation=replace(manifest.evaluation,
                                                    consequence_backstop_events=30))
    runtime = _consequence_runtime(provider=_Endorser(), manifest=manifest)
    runtime._manage_reserve_window()
    # One unacknowledged order at the venue, as rehearsal 5 had: while an order's
    # identity is unknown no return\'s outcome may be fixed, so every judge\'s payoff
    # forecast stays pending in the book while its normative window closes unread.
    runtime.consequences.order_intent("cid-1", "decision-0", "BTC")
    _, event = _consequence_produce(runtime, "seed-decider")
    judge = _consequence_judge(runtime, event, "eval-a")
    for _ in range(300):
        runtime.n += 1
        runtime._settle_due_forecasts()
    assert [i["handle"] for i in _verdict_rows(runtime, "verdict.unread")] == [judge]
    assert [i["handle"] for i in _verdict_rows(runtime, "verdict.unmeasured")] == [judge]
    assert runtime.book.pending(predicate_id="return_paid_off")  # still owed, still quiet
    assert judge in runtime.verdicts_closed_out and judge in runtime.verdicts_graded
    # And the commitment left ``pending``: nothing waits on it any more.
    assert not [p for p in runtime.pending.values() if p.judge == judge]


def test_a_restored_runtime_does_not_re_emit_a_verdict_it_already_closed_out():
    """The same guarantee across a restore: what the diary already said once, a runtime
    resumed from it does not say again."""
    from factorylab.runtime.resume import restore_runtime, runtime_state

    manifest = load_manifest("scripted")
    manifest = replace(manifest, evaluation=replace(manifest.evaluation,
                                                    consequence_backstop_events=30))
    runtime = _consequence_runtime(provider=_Endorser(), manifest=manifest)
    runtime._manage_reserve_window()
    runtime.consequences.order_intent("cid-1", "decision-0", "BTC")
    _, event = _consequence_produce(runtime, "seed-decider")
    judge = _consequence_judge(runtime, event, "eval-a")
    for _ in range(40):
        runtime.n += 1
        runtime._settle_due_forecasts()
    assert len(_verdict_rows(runtime, "verdict.unread")) == 1
    state = runtime_state(runtime)
    restored = _consequence_runtime(provider=_Endorser(), manifest=manifest)
    restore_runtime(restored, state)
    assert judge in restored.verdicts_closed_out
    before = len(_verdict_rows(restored, "verdict.unread"))
    for _ in range(40):
        restored.n += 1
        restored._settle_due_forecasts()
    assert len(_verdict_rows(restored, "verdict.unread")) == before
    assert not [i for i in _verdict_rows(restored, "verdict.unmeasured") if i["handle"] == judge]
