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

from dataclasses import replace

from factorylab.kernel.ledger import Ledger
from factorylab.runtime.worlds import load_manifest
from factorylab.world.scripted import ScriptedProvider
from tests.runtime.test_loop import (
    _consequence_judge,
    _consequence_produce,
    _consequence_runtime,
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
    """An evaluator that endorses the return it judges and seals a payoff forecast."""

    def _produce(self, desc, inputs):
        return {"action": "hold", "subscribe": {"cadence_floor": 1}}

    def _evaluate(self, req, inputs):
        return {"verdict": 1.0, "payoff": 0.0, "rationale": "fine", "forecasts": []}


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
