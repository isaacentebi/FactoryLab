"""The grounded commission says when its evidence was taken, and how far it reaches.

A judge shown a list of observations cannot otherwise tell a complete record
from a partial one, or know whether the horizon it is judging has closed. The
snapshot states both, and states what it does not cover. It is taken once, when
the evidence is assembled, and travels unchanged inside the emitted event; it
settles nothing and scores nothing.
"""

from dataclasses import replace

from factorylab.kernel.events import EventKind
from factorylab.runtime.feedback import PendingJudgement
from factorylab.runtime.grounded import freeze_contract
from factorylab.runtime.shared import CH_VERDICT
from factorylab.runtime.worlds import load_manifest
from factorylab.settlement.receipts import execution_receipt
from tests.runtime.test_loop import _consequence_decision, _consequence_runtime


def _runtime():
    manifest = load_manifest("scripted")
    manifest = replace(
        manifest,
        evaluation=replace(
            manifest.evaluation, producer_feedback="realized", grounded_horizon_ticks=2
        ),
    )
    return _consequence_runtime(manifest=manifest)


def _open(rt):
    producer = _consequence_decision(rt, "seed-decider", CH_VERDICT)
    rt.handle_to_assembly[producer] = "seed-decider"
    contract = freeze_contract(rt, producer, "seed-decider", {"action": "investigate"})
    rt.grounded_pending[producer] = contract
    rt.pending[producer] = PendingJudgement(
        producer, CH_VERDICT, rt.n, opened_at_tick=rt.ticks_consumed)
    return producer, contract


def _commissions(rt, producer):
    return [
        event for event in rt.internal
        if event.payload.get("grounded_consequence")
        and event.payload.get("about_handle") == producer
    ]


def _receipt(rt, handle, result):
    execution_receipt(rt.consequences.receipts, kind="program_result", handle=handle,
                      owner="seed-decider", at_event=rt.n, facts={"result": result})


def test_the_commission_states_when_its_evidence_was_taken_and_how_far_it_reaches():
    rt = _runtime()
    producer, contract = _open(rt)
    _receipt(rt, producer, "before")
    rt.ticks_consumed = contract.due_tick
    rt._settle_due_grounded()
    snapshot = _commissions(rt, producer)[-1].payload["evidence_snapshot"]
    assert snapshot["as_of_tick"] == contract.due_tick == rt.ticks_consumed
    assert snapshot["event_cursor"] == len(rt.events_log)
    assert snapshot["receipt_cursor"] == rt.consequences.receipts.execution_count()
    assert snapshot["observation_due_tick"] == contract.due_tick
    assert snapshot["assessment_timeout_tick"] == contract.close_tick
    assert "not a proof that anything else did not happen" in snapshot["scope"]


def test_the_due_tick_is_the_observation_horizon_and_the_close_tick_only_bounds_assessment():
    rt = _runtime()
    _producer, contract = _open(rt)
    horizon = rt.ev.grounded_horizon_ticks
    assert contract.due_tick == contract.opened_tick + horizon
    assert contract.close_tick > contract.due_tick
    assert contract.close_tick - contract.due_tick == max(
        horizon + 1, int(rt.ev.verdict_timeout_ticks))


def test_the_snapshot_is_frozen_at_assembly_and_never_re_timed_by_later_facts():
    rt = _runtime()
    producer, contract = _open(rt)
    rt.ticks_consumed = contract.due_tick
    rt._settle_due_grounded()
    emitted = _commissions(rt, producer)[-1].payload["evidence_snapshot"]
    taken = dict(emitted)
    rt.ticks_consumed = contract.due_tick + 3
    _receipt(rt, producer, "after the snapshot")
    rt.events_log.append({"kind": "Tick", "payload": {"index": rt.n + 1}})
    rt._settle_due_grounded()
    assert dict(_commissions(rt, producer)[-1].payload["evidence_snapshot"]) == taken
    assert taken["as_of_tick"] == contract.due_tick
    assert taken["receipt_cursor"] < rt.consequences.receipts.execution_count()


def test_each_bounded_retry_takes_its_own_snapshot():
    rt = _runtime()
    producer, contract = _open(rt)
    rt.ticks_consumed = contract.due_tick
    rt._settle_due_grounded()
    first = dict(_commissions(rt, producer)[-1].payload["evidence_snapshot"])
    # One unusable answer returns the contract to the queue for a fresh judge.
    rt.grounded_pending[producer] = rt.grounded_pending[producer].retry_after("eval-b")
    rt.ticks_consumed = contract.due_tick + 1
    _receipt(rt, producer, "arrived between commissions")
    rt._settle_due_grounded()
    commissions = _commissions(rt, producer)
    second = dict(commissions[-1].payload["evidence_snapshot"])
    assert len(commissions) == 2
    assert dict(commissions[0].payload["evidence_snapshot"]) == first
    assert second["as_of_tick"] == contract.due_tick + 1
    assert second["receipt_cursor"] > first["receipt_cursor"]


def test_the_snapshot_settles_nothing_and_scores_nothing():
    rt = _runtime()
    producer, contract = _open(rt)
    rt.ticks_consumed = contract.due_tick
    balance = rt.wallet.balance
    rt._settle_due_grounded()
    assert not rt.queue.history(producer)
    assert rt.wallet.balance == balance
    assert rt.grounded_pending[producer].final_requested is True
    assert producer not in rt.grounded_closed
    # The timeout still belongs to the close tick, unchanged by the snapshot.
    rt.ticks_consumed = contract.close_tick
    rt._settle_due_grounded()
    assert producer in rt.grounded_closed
    assert [item.definition_version for item in rt.queue.history(producer)] == [
        "realized-consequence-v2-unknown"
    ]


def test_the_commission_event_kind_and_payload_are_otherwise_unchanged():
    rt = _runtime()
    producer, contract = _open(rt)
    rt.ticks_consumed = contract.due_tick
    rt._settle_due_grounded()
    payload = _commissions(rt, producer)[-1].payload
    assert str(payload.get("inputs", {}).get("kind")) == "RealizedConsequence"
    assert payload["cost"] == 0 and payload["status"] == "ok"
    assert set(payload["evidence_snapshot"]) == {
        "as_of_tick", "event_cursor", "receipt_cursor",
        "observation_due_tick", "assessment_timeout_tick", "scope",
    }
    assert EventKind.PRODUCER_RETURN
