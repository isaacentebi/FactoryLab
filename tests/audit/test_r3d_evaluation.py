"""R3-D: evaluation and time. The plan's acceptance, one test per clause.

``docs/plans/edition3-r3.md``, R3-D: "no producer return exists for an empty
draw; a forecast at a 0.99 base rate moves no standing; three simultaneous
arrivals do not trigger a tier", plus a declined commission costs only the call
(§6.B). The unmeasured answer, the fidelity objection and the charter-window
verdict commitment behind the other clauses were deleted by ruling R1 and
evaluations S1 and U1; what remains here is that a restored runtime settles an
evaluator decision once.

No provider, network, venue, disk diary or key is used.
"""

from __future__ import annotations

from dataclasses import replace

from factorylab.kernel.ledger import Ledger
from factorylab.runtime.worlds import load_manifest
from factorylab.world.scripted import ScriptedProvider
from tests.runtime.test_loop import (
    _consequence_judge,
    _consequence_produce,
    _consequence_runtime,
    lists_nothing,
)

# ---- the four settlement objects -------------------------------------------------------


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


def _verdict_rows(runtime, kind: str) -> list[dict]:
    return [i for i in runtime.ledger._recovery_items() if i["kind"] == kind]


class _Endorser(ScriptedProvider):
    """An evaluator that endorses the return it judges."""

    def _produce(self, desc, inputs):
        return {"action": "hold"}

    def _evaluate(self, req, inputs):
        return {"verdict": 1.0, "rationale": "fine", "forecasts": []}


def test_a_restored_runtime_settles_an_evaluator_decision_once():
    """What the diary already said once, a runtime resumed from it does not say again:
    a judge's two signals close and its decision settles exactly once (ruling R1)."""
    from factorylab.runtime.resume import restore_runtime, runtime_state

    manifest = load_manifest("scripted")
    manifest = replace(manifest, evaluation=replace(manifest.evaluation,
                                                    consequence_backstop_events=30))
    runtime = lists_nothing(_consequence_runtime(provider=_Endorser(), manifest=manifest))
    runtime._manage_reserve_window()
    _, event = _consequence_produce(runtime, "seed-decider")
    judge = _consequence_judge(runtime, event, "eval-a")
    for _ in range(40):  # forty world ticks: the horizons count ticks, not events
        runtime.n += 1
        runtime.ticks_consumed += 1
        runtime._settle_due_forecasts()
    settled = [row for row in _verdict_rows(runtime, "evaluator.settled")
               if row["handle"] == judge]
    assert len(settled) == 1 and judge not in runtime.pending
    state = runtime_state(runtime)
    restored = lists_nothing(_consequence_runtime(provider=_Endorser(), manifest=manifest))
    restore_runtime(restored, state)
    before = len(_verdict_rows(restored, "evaluator.settled"))
    for _ in range(40):
        restored.n += 1
        restored.ticks_consumed += 1
        restored._settle_due_forecasts()
    assert len(_verdict_rows(restored, "evaluator.settled")) == before
