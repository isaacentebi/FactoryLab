"""Final commissions retain their frozen scope without compulsory evaluation."""

from factorylab.kernel.queue import SettleStatus
from tests.runtime.test_grounded_feedback import _FullRunProvider, _open_contract, _runtime
from tests.runtime.test_loop import _consequence_judge


class CaptureFinal(_FullRunProvider):
    final_inputs = None
    final_prompt = None

    def _evaluate(self, req, inputs):
        if "realized_consequence" in inputs:
            self.final_inputs = inputs
            self.final_prompt = str(req.messages)
        return super()._evaluate(req, inputs)


def test_final_commission_does_not_inject_live_world_or_metric_context(monkeypatch):
    provider = CaptureFinal()
    rt = _runtime(provider=provider)
    _, contract = _open_contract(rt)
    monkeypatch.setattr(rt, "_charter_text", lambda: "LATER-CHARTER-MARKER")
    monkeypatch.setattr(rt, "_world_block", lambda: {"marker": "LATER-MARKET-MARKER"})
    monkeypatch.setattr(rt, "_standing_for", lambda _: "LATER-STANDING-MARKER")
    rt.ticks_consumed = contract.due_tick
    rt._settle_due_grounded()
    _consequence_judge(rt, rt.internal[-1], "eval-b")
    inputs = provider.final_inputs
    assert inputs is not None
    assert not {"charter", "world", "predicates", "forecast_example",
                "your_consequence_standing"} & inputs.keys()
    assert "LATER-" not in provider.final_prompt
    assert inputs["realized_consequence"]["frozen_norms"] == list(contract.norms)
    assert inputs["commission"]["horizon_ticks"] == contract.due_tick - contract.opened_tick
    assert "horizon_events" not in inputs["commission"]
    assert "Cite only supplied evidence refs" not in provider.final_prompt
    assert "Answer supported only" not in provider.final_prompt


def test_declined_final_router_draw_stays_voluntary_and_closes_unknown():
    rt = _runtime()
    producer, contract = _open_contract(rt)
    rt.ticks_consumed = contract.due_tick
    rt._settle_due_grounded()
    commission = rt.internal[-1]
    invocations = rt.stats.invocations
    judge = _consequence_judge(rt, commission, "NOOP")
    assert rt.queue.get(judge).status is SettleStatus.INAPPLICABLE
    assert rt.stats.invocations == invocations
    assert rt.grounded_pending[producer].final_requested
    before = len(rt.internal)
    rt._settle_due_grounded()
    assert len(rt.internal) == before
    assert not rt.queue.history(producer)
    rt.ticks_consumed = contract.close_tick
    rt._settle_due_grounded()
    assert producer not in rt.grounded_pending
    assert rt.queue.history(producer)[-1].status is SettleStatus.CENSORED
