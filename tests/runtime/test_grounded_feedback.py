"""Delayed producer feedback is grounded in cited public consequences."""

from dataclasses import asdict, replace
from types import SimpleNamespace

import pytest

from factorylab.charter.charter import Norm
from factorylab.charter.windows import MetricWindow
from factorylab.cortex.assembly import _validate_return, validate_schema
from factorylab.cortex.registration import AssemblyProposal
from factorylab.cortex.request import ChildRequest, Return
from factorylab.kernel.events import Event, EventKind
from factorylab.kernel.queue import PropensityRecord, SettleStatus
from factorylab.runtime.feedback import PendingJudgement
from factorylab.runtime.grounded import (
    GROUNDED_DEFINITION,
    freeze_contract,
    parse_finding,
    public_evidence,
)
from factorylab.runtime.loop import Runtime, _grounded_review
from factorylab.runtime.resume import decode, encode, restore_runtime, runtime_state
from factorylab.runtime.shared import CH_CONFORMITY, CH_FAST, CH_VERDICT
from factorylab.runtime.worlds import load_manifest
from factorylab.settlement.lots import Payoff
from factorylab.settlement.receipts import execution_receipt
from factorylab.settlement.vocabulary import evaluator_answer_schema
from factorylab.world.exchange import FakeExchange
from factorylab.world.scripted import ScriptedProvider
from tests.runtime.test_learning_signal import _drawn, _router, _settle, _weights
from tests.runtime.test_loop import (
    _consequence_decision,
    _consequence_judge,
    _consequence_produce,
    _consequence_runtime,
)


def _runtime(*, provider=None):
    manifest = load_manifest("scripted")
    manifest = replace(
        manifest,
        evaluation=replace(
            manifest.evaluation,
            producer_feedback="realized",
            grounded_horizon_ticks=2,
            verdict_timeout_events=3,
        ),
    )
    return _consequence_runtime(manifest=manifest, provider=provider)


class _GroundedProvider(ScriptedProvider):
    def _produce(self, desc, inputs):
        return {"action": "investigate", "subscribe": {"cadence_floor": 1}}

    def _evaluate(self, req, inputs):
        grounded = inputs.get("realized_consequence")
        if grounded is None:
            return {"verdict": 0.1, "rationale": "provisional low opinion",
                    "forecasts": []}
        refs = [row["ref"] for row in grounded["evidence"]]
        return {
            "verdict": 0.9,
            "rationale": "the receipt supports the frozen useful effect",
            "realized_consequence": {
                "status": "supported", "score": 0.9,
                "evidence": refs, "reason": "an attributable result exists",
            },
        }


class _FullRunProvider(_GroundedProvider):
    def _evaluate(self, req, inputs):
        grounded = inputs.get("realized_consequence")
        if grounded is not None and not grounded["evidence"]:
            return {
                "verdict": 0.5,
                "rationale": "no public evidence decides the frozen contract",
                "realized_consequence": {
                    "status": "unknown", "evidence": [],
                    "reason": "the horizon produced no attributable receipt",
                },
            }
        return super()._evaluate(req, inputs)


class _PolymorphicProducer(_GroundedProvider):
    def _produce(self, desc, inputs):
        return {"emits": "ProducerReturn", "action": "investigate"}


class _FavorableInitial(_GroundedProvider):
    grounded_input = None

    def _evaluate(self, req, inputs):
        if inputs.get("realized_consequence") is None:
            return {"verdict": 0.95, "rationale": "favorable provisional opinion",
                    "forecasts": []}
        self.grounded_input = inputs["realized_consequence"]
        return super()._evaluate(req, inputs)


def _open_contract(rt):
    producer = _consequence_decision(rt, "seed-decider", CH_VERDICT)
    rt.handle_to_assembly[producer] = "seed-decider"
    contract = freeze_contract(rt, producer, "seed-decider", {"action": "investigate"})
    rt.grounded_pending[producer] = contract
    rt.pending[producer] = PendingJudgement(
        producer, CH_VERDICT, rt.n, opened_at_tick=rt.ticks_consumed)
    return producer, contract


def _judge(rt, producer, finding, *, verdict=0.5, action="eval-b"):
    handle = _consequence_decision(rt, action, CH_CONFORMITY)
    rt.handle_to_assembly[handle] = action
    ret = Return(handle, {
        "verdict": verdict,
        "rationale": "interpreted against the frozen criterion",
        "realized_consequence": finding,
    }, 0, "ok")
    evidence = [{"ref": "event:7", "kind": "ForecastSettled",
                 "payload": {"predicate": "fill_within", "y": 1}}]
    rt._complete_grounded_evaluation(handle, action, producer, ret, evidence)
    return handle


def _fix_payoff(rt, handle, *, net, cost, marked=False, censored=None):
    payoff = Payoff(
        handle, int(censored is None and net > cost), net, cost, rt.n + 1,
        marked=marked, liquidated=False, earned_micro=0, censored=censored,
    )
    account = rt.consequences.table.account(handle)
    rt.ledger.append({"kind": "consequence.outcome", **asdict(payoff)})
    rt.consequences.table = rt.consequences.table._accounts({
        handle: replace(account, payoff=payoff),
    })
    return payoff


def test_a_favorable_initial_opinion_cannot_settle_the_producer():
    rt = _runtime()
    producer, contract = _open_contract(rt)
    rt.grounded_pending[producer] = contract.with_initial(
        judge_handle="initial", evaluator_id="eval-a", forecast_handles=())
    assert rt.queue.get(producer).status is SettleStatus.PENDING
    assert not rt.queue.history(producer)


def test_grounded_horizon_is_independent_of_ordinary_forecast_horizon():
    due = []
    for forecast_horizon in (1, 100):
        manifest = load_manifest("scripted")
        manifest = replace(
            manifest,
            evaluation=replace(
                manifest.evaluation,
                producer_feedback="realized",
                grounded_horizon_ticks=4,
                forecast_horizon_events=forecast_horizon,
            ),
        )
        rt = _consequence_runtime(manifest=manifest)
        handle = _consequence_decision(rt, "seed-decider", CH_VERDICT)
        contract = freeze_contract(rt, handle, "seed-decider", {"action": "investigate"})
        due.append(contract.due_tick - contract.opened_tick)
    assert due == [4, 4]


def test_busy_internal_events_do_not_mature_a_grounded_contract_without_ticks():
    rt = _runtime()
    producer, contract = _open_contract(rt)
    opened_tick = rt.ticks_consumed
    rt.n += 1_000

    rt._settle_due_grounded()

    assert rt.ticks_consumed == opened_tick < contract.due_tick
    assert producer in rt.grounded_pending
    assert not any(event.payload.get("grounded_consequence") for event in rt.internal)


def test_real_initial_and_final_evaluator_paths_reverse_the_provisional_opinion():
    rt = _runtime(provider=_GroundedProvider())
    producer, event = _consequence_produce(rt, "seed-decider")
    initial = _consequence_judge(rt, event, "eval-a")
    assert rt.queue.get(producer).status is SettleStatus.PENDING
    assert rt.grounded_pending[producer].initial_judge == initial
    execution_receipt(rt.consequences.receipts, kind="program_result", handle=producer,
                      owner="seed-decider", at_event=rt.n,
                      facts={"result": "independently inspectable"})
    rt.ticks_consumed = rt.grounded_pending[producer].due_tick
    rt._settle_due_grounded()
    final_event = next(event for event in reversed(rt.internal)
                       if event.payload.get("grounded_consequence"))
    final = _consequence_judge(rt, final_event, "eval-b")
    outcome = rt.queue.history(producer)[-1]
    assert outcome.status is SettleStatus.SETTLED and outcome.score == pytest.approx(0.9)
    assert outcome.sampling_ref == final


def test_polymorphic_producer_freezes_before_selecting_its_producing_variant():
    rt = _runtime(provider=_PolymorphicProducer())
    rt._manage_reserve_window()
    rt._register("author", AssemblyProposal(
        "dual", "producer", "fake-haiku", "Reply with JSON.", ("Tick",), 128, "low",
        ("ProducerReturn", "Verdict"), {},
    ))
    channels = rt._return_channels("dual")
    propensity = PropensityRecord(("dual",), (1.0,), "dual", 0, "test-router", "state")
    handle = rt.queue.open(
        actor="test-router", event_id="dual-tick", propensity=propensity,
        channel=next(iter(channels.values())), deadline_ns=rt.clock.now_ns + 10**12,
        parent_handle=None, cost_ceiling=rt.wallet.available, return_channels=channels,
    )
    rt._start_return(handle)
    rt.handle_to_assembly[handle] = "dual"
    rt._producer_step(
        Event("dual-tick", EventKind.TICK, rt.clock.now_ns, {"index": 0}, "test"),
        handle, SimpleNamespace(chosen="dual"), rt.queue.get(handle).deadline_ns,
    )
    assert rt.return_bindings[handle]["selected"] == "ProducerReturn"
    assert rt.queue.get(handle).channel == CH_VERDICT
    assert rt.grounded_pending[handle].producer_outputs["action"] == "investigate"


@pytest.mark.gate
def test_scripted_realized_world_closes_contracts_through_real_dispatch():
    from scripts.edition4_report import build_behavioral_trace

    manifest = load_manifest("scripted")
    manifest = replace(
        manifest,
        evaluation=replace(manifest.evaluation, producer_feedback="realized",
                           grounded_horizon_ticks=2, verdict_timeout_events=3),
    )
    rt = Runtime(
        manifest, events=25, seed=4, initial_balance_micro=100_000_000,
        ledger_path=None, router_gamma=0.1,
        provider=_FullRunProvider(), exchange=FakeExchange(), kill_at_end=True,
    )
    rt.run()
    rows = rt.ledger._recovery_items()
    contracts = [row for row in rows if row["kind"] == "consequence.contract"]
    findings = [row for row in rows if row["kind"] == "consequence.finding"]
    assert contracts and findings
    assert any(any(ref.startswith("economic-outcome:") for ref in row["evidence"])
               for row in findings)
    assert not [row for row in rows if row["kind"] == "consequence.finding_refused"]
    trace = build_behavioral_trace(
        {"status": "completed", "summary": {"terminated": bool(rt.termination.final)}}, rows)
    assert len(trace["final_feedback_chains"]) == len(findings)
    assert all(chain["addressed_to_inbox"] for chain in trace["final_feedback_chains"])
    assert any(chain["next_return_valid"] for chain in trace["final_feedback_chains"])


def test_true_predicate_evidence_is_not_automatic_usefulness_and_contrary_is_zero():
    rt = _runtime()
    producer, _ = _open_contract(rt)
    _judge(rt, producer, {
        "status": "contrary", "evidence": ["event:7"],
        "reason": "the fill happened but did not support the claimed investigation",
    })
    outcome = rt.queue.history(producer)[-1]
    assert outcome.status is SettleStatus.SETTLED
    assert outcome.score == 0
    assert outcome.definition_version == GROUNDED_DEFINITION


def test_supported_effect_can_overturn_a_low_initial_verdict_once():
    rt = _runtime()
    producer, contract = _open_contract(rt)
    rt.grounded_pending[producer] = contract.with_initial(
        judge_handle="initial", evaluator_id="eval-a", forecast_handles=())
    judge = _judge(rt, producer, {
        "status": "supported", "score": 0.9, "evidence": ["event:7"],
        "reason": "the later receipt supports the frozen useful effect",
    }, verdict=0.1)
    assert len(rt.queue.history(producer)) == 1
    assert rt.queue.history(producer)[0].sampling_ref == judge
    duplicate = _consequence_decision(rt, "eval-b", CH_CONFORMITY)
    rt.handle_to_assembly[duplicate] = "eval-b"
    rt._complete_grounded_evaluation(
        duplicate, "eval-b", producer,
        Return(duplicate, {"realized_consequence": {
            "status": "supported", "score": 1.0, "evidence": ["event:7"],
            "reason": "duplicate"}}, 0, "ok"),
        [{"ref": "event:7"}],
    )
    assert len(rt.queue.history(producer)) == 1


def test_unknown_grounded_consequence_does_not_impute_an_observed_arm_mean():
    rt = _runtime()
    state, _ = _router(rt)
    arm = next(a for a in state.universe if a != "__noop__")
    observed = _drawn(rt, state, arm, channel=CH_VERDICT)
    _settle(rt, observed, SettleStatus.SETTLED, 0.7)
    rt._deliver_returns()
    before = _weights(state)
    producer = _drawn(rt, state, arm, channel=CH_VERDICT)
    rt.handle_to_assembly[producer] = arm
    contract = freeze_contract(rt, producer, arm, {"action": "investigate"})
    rt.grounded_pending[producer] = contract
    rt.pending[producer] = PendingJudgement(
        producer, CH_VERDICT, rt.n, opened_at_tick=rt.ticks_consumed)
    rt._grounded_unknown(contract, "no observable receipt")
    rt._deliver_returns()
    assert _weights(state) == before
    assert rt.queue.history(producer)[0].status is SettleStatus.CENSORED


def test_wall_timeout_during_tick_horizon_waits_for_the_one_final_update():
    rt = _runtime()
    state, _ = _router(rt)
    arm = next(a for a in state.universe if a != "__noop__")
    producer = _drawn(rt, state, arm, channel=CH_VERDICT)
    rt.handle_to_assembly[producer] = arm
    contract = freeze_contract(rt, producer, arm, {"action": "investigate"})
    rt.grounded_pending[producer] = contract
    rt.pending[producer] = PendingJudgement(
        producer, CH_VERDICT, rt.n, opened_at_tick=rt.ticks_consumed)
    before = _weights(state)
    rt.queue.expire(10**16)
    rt._deliver_returns()
    assert _weights(state) == before
    _judge(rt, producer, {
        "status": "supported", "score": 0.8, "evidence": ["event:7"],
        "reason": "observed after the wall timeout but inside the tick contract",
    })
    rt._deliver_returns()
    after = _weights(state)
    assert after[arm] > max(value for action, value in after.items() if action != arm)
    assert [result.status for result in rt.queue.history(producer)] == [
        SettleStatus.TIMED_OUT, SettleStatus.SETTLED]


def test_wall_timeout_then_unknown_closes_without_imputation_or_open_round(monkeypatch):
    rt = _runtime()
    state = rt._build_router("ProducerReturn", "blum_mansour", 0.1)
    arm = next(a for a in state.universe if a != "__noop__")
    producer = _drawn(rt, state, arm, channel=CH_VERDICT)
    rt.snapshot_keys[producer] = "grounded-timeout"
    rt.handle_to_assembly[producer] = arm
    contract = freeze_contract(rt, producer, arm, {"action": "investigate"})
    rt.grounded_pending[producer] = contract
    rt.pending[producer] = PendingJudgement(
        producer, CH_VERDICT, rt.n, opened_at_tick=rt.ticks_consumed)
    updates, discards = [], []
    monkeypatch.setattr(state.learner.inner, "update_for",
                        lambda key, feedback: updates.append((key, feedback)))
    monkeypatch.setattr(state.learner.inner, "discard_for", discards.append)
    rt.queue.expire(10**16)
    rt._deliver_returns()
    assert producer in rt.snapshot_keys
    rt.ticks_consumed = contract.close_tick
    rt._settle_due_grounded()
    rt._deliver_returns()
    assert producer not in rt.snapshot_keys
    assert updates == [] and discards == ["grounded-timeout"]
    assert [result.status for result in rt.queue.history(producer)] == [
        SettleStatus.TIMED_OUT, SettleStatus.CENSORED]


def test_maturity_commissions_a_fresh_judge_without_mechanically_scoring_evidence():
    rt = _runtime()
    producer, contract = _open_contract(rt)
    rt.grounded_pending[producer] = contract.with_initial(
        judge_handle="initial", evaluator_id="eval-a", forecast_handles=("forecast-1",))
    rt.events_log.append({"kind": "ForecastSettled", "payload": {
        "handle": "forecast-1", "predicate": "fill_within", "y": 1}})
    rt.ticks_consumed = contract.due_tick
    rt._settle_due_grounded()
    event = rt.internal[-1]
    assert event.payload["grounded_consequence"] is True
    assert event.payload["evidence"][0]["payload"]["y"] == 1
    assert "eval-a" in event.payload["excluded_evaluators"]
    assert rt.queue.get(producer).status is SettleStatus.PENDING


@pytest.mark.parametrize(
    ("net", "cost", "expected_y"),
    ((8_000, 5_000, 1), (-3_000, 5_000, 0)),
)
def test_same_favorable_opinion_reaches_final_judge_with_exact_profit_or_loss(
    net, cost, expected_y,
):
    provider = _FavorableInitial()
    rt = _runtime(provider=provider)
    frozen_norms = tuple(Norm(norm.id, f"frozen definition {index}")
                         for index, norm in enumerate(rt.charter.norms))
    rt.charter = replace(rt.charter, norms=frozen_norms)
    producer, event = _consequence_produce(rt, "seed-decider")
    initial = _consequence_judge(rt, event, "eval-a")
    assert rt.grounded_pending[producer].initial_judge == initial
    assert rt.queue.get(producer).status is SettleStatus.PENDING
    rt.charter = replace(
        rt.charter, edition=rt.charter.edition + 1,
        norms=tuple(Norm(norm.id, "later changed definition") for norm in rt.charter.norms),
    )
    _fix_payoff(rt, producer, net=net, cost=cost)
    rt.ticks_consumed = rt.grounded_pending[producer].due_tick
    rt._settle_due_grounded()
    commission = next(ev for ev in reversed(rt.internal)
                      if ev.payload.get("grounded_consequence"))
    outcome = next(row for row in commission.payload["evidence"]
                   if row["kind"] == "EconomicOutcome")
    assert outcome["ref"].startswith("economic-outcome:")
    assert outcome["payload"] == {
        "handle": producer,
        "y": expected_y,
        "net_micro": net,
        "cost_micro": cost,
        "at_event": rt.n + 1,
        "marked": False,
        "liquidated": False,
        "earned_micro": 0,
        "censored": None,
        "status": "fixed",
        "observed": True,
        "amount_unit": "micro_usd",
        "net_is_signed": True,
        "cash_realized": True,
        "fill_is_profit": False,
        "y_is_observation": True,
    }
    _consequence_judge(rt, commission, "eval-b")
    grounded = provider.grounded_input
    assert grounded["frozen_norms"] == [
        {"id": norm.id, "definition": norm.definition} for norm in frozen_norms
    ]
    assert all(row["definition"] != "later changed definition"
               for row in grounded["frozen_norms"])
    assert grounded["producer_claim"]["action"] == "investigate"
    assert {row["id"] for row in grounded["price_constraints"]} == {
        card.id for card in rt.charter.cards
    }
    assert grounded["frozen_norms"] and grounded["price_constraints"]
    assert "contract" not in grounded


def test_marked_and_censored_outcomes_cannot_read_as_realized_cash_or_observed_zero():
    marked_rt = _runtime()
    marked, marked_contract = _open_contract(marked_rt)
    marked_rt.consequences.start(marked, marked_rt.n)
    _fix_payoff(marked_rt, marked, net=7_000, cost=5_000, marked=True)
    marked_fact = next(row for row in public_evidence(marked_rt, marked_contract)
                       if row["kind"] == "EconomicOutcome")
    assert marked_fact["payload"]["status"] == "fixed"
    assert marked_fact["payload"]["marked"] is True
    assert marked_fact["payload"]["cash_realized"] is False

    censored_rt = _runtime()
    censored, censored_contract = _open_contract(censored_rt)
    censored_rt.consequences.start(censored, censored_rt.n)
    _fix_payoff(
        censored_rt, censored, net=0, cost=5_000,
        censored="external_unobservable",
    )
    censored_fact = next(row for row in public_evidence(censored_rt, censored_contract)
                         if row["kind"] == "EconomicOutcome")
    assert censored_fact["payload"]["status"] == "censored_unknown"
    assert censored_fact["payload"]["observed"] is False
    assert censored_fact["payload"]["y_is_observation"] is False
    assert censored_fact["payload"]["censored"] == "external_unobservable"


def test_absent_economic_outcome_remains_missing_instead_of_becoming_zero():
    rt = _runtime()
    producer, contract = _open_contract(rt)
    assert not [row for row in public_evidence(rt, contract)
                if row["kind"] == "EconomicOutcome"]
    rt.ticks_consumed = contract.due_tick
    rt._settle_due_grounded()
    commission = next(ev for ev in reversed(rt.internal)
                      if ev.payload.get("grounded_consequence"))
    assert not commission.payload["evidence"]
    _judge(rt, producer, {
        "status": "unknown", "evidence": [],
        "reason": "no fixed economic outcome or other effect was observed",
    })
    outcome = rt.queue.history(producer)[-1]
    assert outcome.status is SettleStatus.CENSORED


def test_findings_cannot_cite_unsupplied_evidence():
    with pytest.raises(ValueError, match="outside its commission"):
        parse_finding({"status": "supported", "score": 1,
                       "evidence": ["event:missing"], "reason": "claimed"}, set())


@pytest.mark.parametrize("evidence", [
    [{"ref": "economic:censored", "kind": "EconomicOutcome", "payload": {
        "status": "censored_unknown", "observed": False,
        "censored": "external_unobservable", "y": 0,
    }}],
    [{"ref": "forecast:unresolved", "kind": "ForecastSettled", "payload": {
        "status": "censored", "y": None,
    }}],
])
def test_unobserved_evidence_cannot_ground_a_numeric_finding(evidence):
    rt = _runtime()
    producer, _ = _open_contract(rt)
    judge = _consequence_decision(rt, "eval-b", CH_CONFORMITY)
    rt.handle_to_assembly[judge] = "eval-b"
    ref = evidence[0]["ref"]
    rt._complete_grounded_evaluation(
        judge, "eval-b", producer,
        Return(judge, {"verdict": 0.8, "rationale": "treated absence as success",
                       "realized_consequence": {
                           "status": "supported", "score": 0.8,
                           "evidence": [ref], "reason": "numeric claim",
                       }}, 0, "ok"),
        evidence,
    )
    assert rt.queue.get(producer).status is SettleStatus.PENDING
    refused = next(row for row in rt.ledger._recovery_items()
                   if row["kind"] == "consequence.finding_refused")
    assert "independently observed" in refused["reason"]


def test_unknown_may_cite_only_censored_evidence_without_turning_it_into_zero():
    rt = _runtime()
    producer, _ = _open_contract(rt)
    judge = _consequence_decision(rt, "eval-b", CH_CONFORMITY)
    rt.handle_to_assembly[judge] = "eval-b"
    evidence = [{"ref": "economic:censored", "kind": "EconomicOutcome", "payload": {
        "status": "censored_unknown", "observed": False,
        "censored": "external_unobservable", "y": 0,
    }}]
    rt._complete_grounded_evaluation(
        judge, "eval-b", producer,
        Return(judge, {"verdict": 0.5, "rationale": "outcome unavailable",
                       "realized_consequence": {
                           "status": "unknown", "evidence": ["economic:censored"],
                           "reason": "the signed amount was not observable",
                       }}, 0, "ok"),
        evidence,
    )
    outcome = rt.queue.history(producer)[-1]
    assert outcome.status is SettleStatus.CENSORED
    finding = next(row for row in rt.ledger._recovery_items()
                   if row["kind"] == "consequence.finding")
    assert finding["status"] == "unknown" and finding["score"] is None


def test_mixed_context_accepts_numeric_finding_only_with_a_cited_observed_fact():
    rt = _runtime()
    producer, _ = _open_contract(rt)
    judge = _consequence_decision(rt, "eval-b", CH_CONFORMITY)
    rt.handle_to_assembly[judge] = "eval-b"
    evidence = [
        {"ref": "economic:censored", "kind": "EconomicOutcome", "payload": {
            "status": "censored_unknown", "observed": False,
            "censored": "external_unobservable", "y": 0,
        }},
        {"ref": "execution:actual", "kind": "ExecutionReceipt:program_result",
         "payload": {"status": "executed"}},
    ]
    rt._complete_grounded_evaluation(
        judge, "eval-b", producer,
        Return(judge, {"verdict": 0.7, "rationale": "used the actual execution fact",
                       "realized_consequence": {
                           "status": "supported", "score": 0.7,
                           "evidence": ["economic:censored", "execution:actual"],
                           "reason": "observed use supports a bounded effect",
                       }}, 0, "ok"),
        evidence,
    )
    outcome = rt.queue.history(producer)[-1]
    assert outcome.status is SettleStatus.SETTLED
    assert outcome.score == pytest.approx(0.7)


def test_unknown_may_cite_insufficient_public_evidence_without_becoming_a_score():
    rt = _runtime()
    producer, _ = _open_contract(rt)
    judge = _consequence_decision(rt, "eval-b", CH_CONFORMITY)
    rt.handle_to_assembly[judge] = "eval-b"
    rt._complete_grounded_evaluation(
        judge, "eval-b", producer,
        Return(judge, {
            "verdict": 0.5,
            "rationale": "the receipt exists but does not decide usefulness",
            "realized_consequence": {
                "status": "unknown", "evidence": ["event:7"],
                "reason": "the supplied fact is insufficient",
            },
        }, 0, "ok"),
        [{"ref": "event:7", "kind": "ExecutionReceipt:program_result", "payload": {}}],
    )
    outcome = rt.queue.history(producer)[-1]
    assert outcome.status is SettleStatus.CENSORED
    assert outcome.definition_version.endswith("-unknown")
    finding = next(row for row in rt.ledger._recovery_items()
                   if row["kind"] == "consequence.finding")
    assert finding["score"] is None and finding["evidence"] == ["event:7"]


def test_failed_final_evaluator_is_rejected_even_if_called_post_hoc():
    rt = _runtime()
    producer, contract = _open_contract(rt)
    rt.grounded_pending[producer] = contract.requested().retry_after("eval-b")
    judge = _consequence_decision(rt, "eval-b", CH_CONFORMITY)
    rt.handle_to_assembly[judge] = "eval-b"
    rt._complete_grounded_evaluation(
        judge, "eval-b", producer,
        Return(judge, {
            "verdict": 1.0, "rationale": "post-hoc retry",
            "realized_consequence": {
                "status": "supported", "score": 1.0, "evidence": ["event:7"],
                "reason": "attempted second answer",
            },
        }, 0, "ok"),
        [{"ref": "event:7", "kind": "ExecutionReceipt:program_result", "payload": {}}],
    )
    assert rt.queue.get(producer).status is SettleStatus.PENDING
    assert not rt.queue.history(producer)
    assert rt.queue.history(judge)[-1].status is SettleStatus.INAPPLICABLE


@pytest.mark.parametrize("status", ["supported", "contrary", "unknown"])
def test_realized_field_is_published_only_on_the_final_commission_schema(status):
    legacy = evaluator_answer_schema({}, {})
    realized = evaluator_answer_schema({}, {}, include_realized=True)
    assert "realized_consequence" not in legacy["properties"]
    assert "realized_consequence" in realized["properties"]
    assert "realized_consequence" in realized["required"]
    assert {"payoff", "forecasts"} <= set(legacy["properties"])
    assert {"payoff", "forecasts"}.isdisjoint(realized["properties"])
    assert "verdict" not in legacy["required"]
    answer = {"rationale": "Read the supplied evidence", "realized_consequence": {
        "status": status, "score": 0.8, "evidence": ["event:7"], "reason": "Evidence"}}
    with pytest.raises(ValueError, match="required field absent"):
        validate_schema(answer, realized)
    validate_schema({**answer, "verdict": 0.8}, realized)
    with pytest.raises(ValueError, match="required field absent"):
        validate_schema({"rationale": "missing finding", "verdict": 0.8}, realized)
    # A retrieval turn is partial by contract; the required final finding applies
    # after its tool continuation, not before the evidence has been fetched.
    _validate_return({"tool_calls": [{"tool": "outcome.get", "args": {}}]}, realized)
    _validate_return({"status": "cannot", "reason": "declined"}, realized)


def test_one_malformed_final_gets_one_bounded_fresh_retry():
    rt = _runtime()
    producer, contract = _open_contract(rt)
    rt.grounded_pending[producer] = contract.requested()
    bad = _consequence_decision(rt, "eval-b", CH_CONFORMITY)
    rt.handle_to_assembly[bad] = "eval-b"
    rt._complete_grounded_evaluation(
        bad, "eval-b", producer, Return(bad, {"rationale": "missing finding"}, 0, "ok"), [])
    retried = rt.grounded_pending[producer]
    assert retried.final_requested is False and retried.final_attempts == 1
    assert retried.final_evaluators == ("eval-b",)
    rt.grounded_pending[producer] = retried.requested()
    _judge(rt, producer, {
        "status": "supported", "score": 0.6, "evidence": ["event:7"],
        "reason": "the bounded retry supplied a valid finding",
    }, action="eval-c")
    assert len(rt.queue.history(producer)) == 1


def test_evidence_starts_before_action_but_excludes_claims_and_opinions():
    rt = _runtime()
    producer = _consequence_decision(rt, "seed-decider", CH_VERDICT)
    execution_receipt(rt.consequences.receipts, kind="program_result", handle=producer,
                      owner="seed-decider", at_event=0, facts={"result": "before"})
    contract = freeze_contract(rt, producer, "seed-decider", {})
    execution_receipt(rt.consequences.receipts, kind="program_result", handle=producer,
                      owner="seed-decider", at_event=1, facts={"result": "after"})
    rt.events_log.extend([
        {"kind": "ProducerReturn", "payload": {"about_handle": producer}},
        {"kind": "Verdict", "payload": {"about_handle": producer, "verdict": 1}},
        {"kind": "Registered", "payload": {"about_handle": producer}},
    ])
    rows = public_evidence(rt, contract)
    assert [row["payload"]["facts"]["result"] for row in rows] == ["after"]


def test_grounded_receipt_lookup_never_scans_the_lifetime_book(monkeypatch):
    rt = _runtime()
    producer = _consequence_decision(rt, "seed-decider", CH_VERDICT)
    book = rt.consequences.receipts

    def full_scan_forbidden(*_args, **_kwargs):
        raise AssertionError("grounded feedback scanned the lifetime receipt book")

    monkeypatch.setattr(book, "ids", full_scan_forbidden)
    monkeypatch.setattr(book, "all", full_scan_forbidden)
    contract = freeze_contract(rt, producer, "seed-decider", {})
    execution_receipt(book, kind="program_result", handle=producer,
                      owner="seed-decider", at_event=1, facts={"result": "bounded"})
    evidence = public_evidence(rt, contract)
    assert [row["payload"]["facts"]["result"] for row in evidence] == ["bounded"]


def test_supported_settlement_survives_restore_and_cannot_settle_twice():
    rt = _runtime()
    producer, contract = _open_contract(rt)
    rt.grounded_pending[producer] = contract.with_initial(
        judge_handle="initial", evaluator_id="eval-a", forecast_handles=())
    judge = _consequence_decision(rt, "eval-b", CH_CONFORMITY)
    rt.handle_to_assembly[judge] = "eval-b"
    restored = _runtime()
    restore_runtime(restored, runtime_state(rt))
    restored._complete_grounded_evaluation(
        judge, "eval-b", producer,
        Return(judge, {
            "verdict": 0.6,
            "rationale": "interpreted after restore",
            "realized_consequence": {
                "status": "supported", "score": 0.75, "evidence": ["event:7"],
                "reason": "restored public evidence supports the effect",
            },
        }, 0, "ok"),
        [{"ref": "event:7", "kind": "ExecutionReceipt:program_result", "payload": {}}],
    )
    again = _runtime()
    restore_runtime(again, runtime_state(restored))
    again._settle_due_grounded()
    assert producer in again.grounded_closed
    assert producer not in again.grounded_pending
    assert len(again.queue.history(producer)) == 1


def test_legacy_grounded_contract_restores_without_inventing_current_norms():
    rt = _runtime()
    _producer, contract = _open_contract(rt)
    serialized = encode(contract)
    serialized["fields"].pop("norms")
    restored = decode(serialized)
    assert restored.norms == ()
    assert restored.charter_edition == contract.charter_edition
    assert restored.criteria == contract.criteria


def test_unknown_settlement_survives_restore_and_cannot_settle_twice():
    rt = _runtime()
    producer, contract = _open_contract(rt)
    rt.ticks_consumed = contract.close_tick
    restored = _runtime()
    restore_runtime(restored, runtime_state(rt))
    restored._settle_due_grounded()
    again = _runtime()
    restore_runtime(again, runtime_state(restored))
    again._settle_due_grounded()
    assert len(again.queue.history(producer)) == 1
    assert again.queue.history(producer)[0].status is SettleStatus.CENSORED


def test_child_producer_freezes_before_invoke_and_waits_for_grounded_feedback():
    rt = _runtime()
    parent = _consequence_decision(rt, "seed-decider", CH_VERDICT)
    request = rt._request(
        parent, "parent", {}, {"type": "object"}, 10**18, CH_VERDICT)
    result, cost = rt._invoke_child(
        "seed-decider", request,
        ChildRequest("self", "child producer", {}, {"type": "object"}),
        rt.wallet.available,
    )
    row = next(item for item in reversed(rt.ledger._recovery_items())
               if item["kind"] == "request.child")
    child = row["handle"]
    assert result["result"]["status"] == "ok" and cost > 0
    assert child in rt.grounded_pending
    assert rt.grounded_pending[child].event_cursor < len(rt.events_log) + 1
    assert rt.queue.get(child).status is SettleStatus.PENDING
    assert not rt.queue.history(child)


class _EvidenceReadingJudge(_GroundedProvider):
    """A final judge whose words never change and whose finding follows the evidence."""

    meta_inputs = None
    producer_inputs = None

    def _produce(self, desc, inputs):
        self.producer_inputs = inputs
        return super()._produce(desc, inputs)

    def _evaluate(self, req, inputs):
        grounded = inputs.get("realized_consequence")
        if grounded is None:
            return {"verdict": 0.5, "rationale": "provisional opinion", "forecasts": []}
        rows = grounded["evidence"]
        adopted = any(
            (row.get("payload") or {}).get("facts", {}).get("result") == "adopted"
            for row in rows
        )
        return {
            "verdict": 0.8,
            "rationale": "the same words either way",
            "realized_consequence": {
                "status": "supported" if adopted else "contrary",
                "score": 0.8 if adopted else 0.0,
                "evidence": [row["ref"] for row in rows],
                "reason": "read from the supplied receipts",
            },
        }

    def _meta(self, inputs):
        self.meta_inputs = inputs
        return {"conformity": 0.7, "rationale": "scripted meta"}


class _UnknownWithEvidence(_EvidenceReadingJudge):
    def _meta(self, inputs):
        self.meta_inputs = inputs
        return {"conformity": 0.2, "rationale": "the evidence was not read"}

    def _evaluate(self, req, inputs):
        grounded = inputs.get("realized_consequence")
        if grounded is None:
            return {"verdict": 0.5, "rationale": "provisional opinion", "forecasts": []}
        return {
            "verdict": 0.8,
            "rationale": "the same words either way",
            "realized_consequence": {
                "status": "unknown", "evidence": [],
                "reason": "the receipts do not decide the frozen claim",
            },
        }


def _final_judgement(rt, result=None, *, action="eval-b"):
    """Run the production route: commission, real final evaluator, cascade release."""
    producer, contract = _open_contract(rt)
    rt._start_return(producer)
    if result is not None:
        execution_receipt(rt.consequences.receipts, kind="program_result", handle=producer,
                          owner="seed-decider", at_event=rt.n, facts={"result": result})
    rt.ticks_consumed = contract.due_tick
    rt._settle_due_grounded()
    commission = next(e for e in reversed(rt.internal)
                      if e.payload.get("grounded_consequence")
                      and e.payload.get("about_handle") == producer)
    judge = _consequence_decision(rt, action, CH_CONFORMITY)
    rt._evaluator_step(commission, judge, SimpleNamespace(chosen=action),
                       rt.queue.get(judge).deadline_ns)
    verdict = next((e for e in reversed(rt.internal)
                    if e.kind is EventKind.VERDICT
                    and e.payload.get("about_handle") == producer), None)
    return producer, judge, verdict


@pytest.mark.parametrize(
    "provider_type,result,status,score",
    [
        (_EvidenceReadingJudge, "adopted", "supported", 0.8),
        (_EvidenceReadingJudge, "ignored", "contrary", 0.0),
        (_UnknownWithEvidence, "adopted", "unknown", None),
    ],
)
def test_final_grounded_finding_reaches_the_producers_next_request_once(
    provider_type, result, status, score
):
    provider = provider_type()
    rt = _runtime(provider=provider)
    producer, judge, verdict = _final_judgement(rt, result)
    finding = dict(verdict.payload["realized_finding"])
    contract = freeze_contract(rt, producer, "seed-decider", {"action": "investigate"})

    # A repeated delivery attempt (including after replay) names the same final
    # judge fact and therefore cannot create a second inbox item.
    before = len(rt.outcomes.items["seed-decider"])
    rt._deliver_grounded_finding_to_inbox(contract, finding, judge_handle=judge)
    assert len(rt.outcomes.items["seed-decider"]) == before

    _consequence_produce(rt, "seed-decider")
    delivered = [
        item for item in provider.producer_inputs["unread_outcomes"]["items"]
        if item["handle"] == producer
        and item.get("kind") == "grounded_evaluation"
    ]
    assert len(delivered) == 1
    item = delivered[0]
    assert item["evidence"] == judge
    assert item["score"] == score
    assert rt.outcomes.get("seed-decider", item["outcome_id"])["outcome"] == {
        "kind": "grounded_evaluation",
        "phase": "final",
        "judge_handle": judge,
        "status": status,
        "score": score,
        "evidence": list(finding["evidence"]),
        "reason": finding["reason"],
    }
    if status == "unknown":
        assert rt.queue.history(producer)[0].status is SettleStatus.CENSORED
        assert rt.queue.history(producer)[0].definition_version.endswith("-unknown")


def _release(rt, event):
    """Release one verdict through the real cascade, as the loop routes it upward."""
    assert rt._cascade_arrival(event) is None
    later = replace(event, id=f"{event.id}-later", payload=dict(event.payload),
                    ts_ns=event.ts_ns + 1000 * rt.tick_clock.interval_ns)
    released = rt._cascade_arrival(later)
    assert released is not None
    return released


def _meta_review(rt, released, *, action="meta-a", channel=CH_FAST):
    handle = _consequence_decision(rt, action, channel)
    rt._meta_step(released, handle, SimpleNamespace(chosen=action),
                  rt.queue.get(handle).deadline_ns)
    return handle


def test_the_meta_reads_the_final_judges_evidence_and_frozen_norms_not_the_live_charter():
    supported, contradicted = {}, {}
    for result, seen in (("adopted", supported), ("ignored", contradicted)):
        provider = _EvidenceReadingJudge()
        rt = _runtime(provider=provider)
        _producer, judge, verdict = _final_judgement(rt, result)
        _meta_review(rt, _release(rt, verdict))
        seen.update(provider.meta_inputs)
        assert seen["verdict"]["verdict"] == 0.8
        assert seen["verdict"]["rationale"] == "the same words either way"
        assert seen["realized_consequence"]["frozen_norms"]
        assert "charter" not in seen and "world" not in seen
        assert seen["realized_consequence"]["producer_claim"] == {"action": "investigate"}
        commission = next(e for e in rt.internal if e.payload.get("evidence_snapshot"))
        snapshot = seen["realized_consequence"]["evidence_snapshot"]
        assert snapshot == dict(commission.payload["evidence_snapshot"])
        assert (seen["realized_consequence"]["observation_contract"]["due_tick"]
                == commission.payload["contract"]["due_tick"])
        assert seen["realized_consequence"]["evidence_omitted"] == 0
        assert judge
    assert supported["realized_consequence"]["finding"]["status"] == "supported"
    assert contradicted["realized_consequence"]["finding"]["status"] == "contrary"
    assert (supported["realized_consequence"]["evidence"]
            != contradicted["realized_consequence"]["evidence"])
    # A finding that cites more rows than the context bound keeps every cited row.
    rows = [{"ref": f"execution:{i}", "kind": "ExecutionReceipt:program_result"}
            for i in range(60)]
    wide = _grounded_review({
        "realized_finding": {"status": "supported", "score": 0.5,
                             "evidence": [row["ref"] for row in rows[:50]]},
        "grounded_contract": {"norms": [], "producer_outputs": {}},
        "grounded_evidence": rows,
    })
    assert [row["ref"] for row in wide["evidence"][:50]] == [row["ref"] for row in rows[:50]]
    assert wide["evidence_omitted"] == 60 - len(wide["evidence"])


def _priced_runtime(provider):
    """A runtime whose cards measure one window, so a window can really close."""
    manifest = load_manifest("scripted")
    manifest = replace(
        manifest,
        charter=replace(manifest.charter, cards=tuple(
            replace(card, window=MetricWindow("windows", 1, None))
            for card in manifest.charter.cards)),
        evaluation=replace(
            manifest.evaluation, producer_feedback="realized",
            grounded_horizon_ticks=2, verdict_timeout_events=3),
    )
    return _consequence_runtime(manifest=manifest, provider=provider)


def _close_subjects_window(rt, handle, *, ok):
    """Close the judged return's own pricing window on a real, attributable measurement."""
    sample = rt._contribution(handle, "producer")
    sample["invocations"], sample["ok"], sample["cost"] = 10, ok, 1_000
    rt.window.invocations, rt.window.ok = 10, ok
    rt._derive_regions()
    rt._close_price_window()


def test_the_final_grounded_judge_settles_against_an_independent_normative_fact():
    rt = _runtime(provider=_EvidenceReadingJudge())
    _producer, judge, verdict = _final_judgement(rt, "adopted")
    meta = _meta_review(rt, _release(rt, verdict))
    commitment = rt.pending.get(f"verdict.norm:{judge}")
    assert commitment is not None and commitment.judge == judge
    assert commitment.awaits_payoff is False and commitment.q == 0.8
    # The commitment is keyed to the judged return's own opening and window, taken
    # when that return opened, not to the tick the late judge spoke at.
    assert commitment.opened_at_tick == 0 and rt.ticks_consumed == 2
    assert rt.pending_meta.get(judge) == [(meta, 0.7)]
    rt.ticks_consumed += rt.ev.consequence_backstop_ticks + 1
    rt._settle_due_verdicts()
    # No window ever judged this return, so there is no fact: the judge is graded
    # once, unmeasured, and the meta waiting on it is released with it rather than
    # left for the expiration path to censor. Nothing is imputed.
    assert judge in rt.verdicts_graded and judge not in rt.pending_meta
    assert judge not in rt.verdict_outcomes
    assert rt.queue.get(meta).status is not SettleStatus.PENDING
    assert not any(item.definition_version == "censored-v1"
                   for item in rt.queue.history(meta))


@pytest.mark.parametrize("ok, blamed, outcome", [(10, 0.0, 1), (2, 1.0, 0)])
def test_a_closed_window_decides_the_final_judge_and_the_meta_that_conformed(
    ok, blamed, outcome
):
    rt = _priced_runtime(_EvidenceReadingJudge())
    producer, judge, verdict = _final_judgement(rt, "adopted")
    _close_subjects_window(rt, producer, ok=ok)
    meta = _meta_review(rt, _release(rt, verdict))
    commitment = rt.pending[f"verdict.norm:{judge}"]
    terms = rt._penalty_terms(commitment.cards, commitment.about)
    weight = sum(term["weight"] for term in terms)
    share = sum(term["weight"] * term["share"] for term in terms) / weight if weight else 0.0
    assert [term["window"] for term in terms] == [rt.price_origins[producer]["origin"]]
    assert share == blamed
    rt.ticks_consumed += rt.ev.consequence_backstop_ticks + 1
    rt._settle_due_verdicts()
    y, _at, anchor = rt.verdict_outcomes[judge]
    assert y == outcome and anchor == commitment.handle
    graded = rt.queue.history(meta)[-1]
    assert graded.definition_version == "meta-consequence-v1"
    assert graded.status is SettleStatus.SETTLED
    assert (graded.score > 0.75) is bool(outcome)


def test_an_evidence_present_unknown_stays_reviewable_without_scoring_the_producer():
    rt = _runtime(provider=_UnknownWithEvidence())
    producer, judge, verdict = _final_judgement(rt, "adopted")
    assert verdict is not None and verdict.payload["realized_finding"]["status"] == "unknown"
    assert verdict.payload["realized_finding"]["score"] is None
    assert rt.queue.history(producer)[0].definition_version == f"{GROUNDED_DEFINITION}-unknown"
    assert f"verdict.norm:{judge}" in rt.pending
    _meta_review(rt, _release(rt, verdict))


def test_an_evasive_unknown_can_be_corrected_where_it_counts():
    rt = _runtime(provider=_UnknownWithEvidence())
    _producer, judge, verdict = _final_judgement(rt, "adopted")
    assert rt.queue.get(judge).status is SettleStatus.PENDING
    meta = _meta_review(rt, _release(rt, verdict))
    meta_event = next(e for e in reversed(rt.internal) if e.kind is EventKind.META_VERDICT)
    rt._deliver_meta_verdict(meta_event)
    settled = rt.queue.history(judge)[-1]
    assert settled.status is SettleStatus.SETTLED and settled.sampling_ref == meta
    assert settled.score <= 0.3 and meta
