"""Lifecycle prices, current sampling evidence, and niche settlement (§II.b/III/IV)."""

import pytest

from factorylab.kernel.queue import SettleStatus
from tests.runtime.test_abstention_price import _gap_runtime, _next_gap
from tests.runtime.test_learning_signal import _drawn, _router, _settle


def test_r3_timeout_late_settlement_deferred_credit_pays_held_penalty(monkeypatch):
    """A late outcome cannot erase the neutral timeout's held liability (§II.b/IV.a)."""
    rt, _ = _gap_runtime(monkeypatch)
    rt.window.verdicts = {"subject": {"eval-a": [0.2]}}
    rt._close_price_window()
    _next_gap(rt)
    state, _ = _router(rt)
    handle = _drawn(rt, state, "eval-a")
    rt._contribution(handle, "evaluator")
    rt.queue.expire(10**19)
    rt._learn_router_return(state, rt.queue.history(handle)[0])
    assert handle in rt.noop_credits
    _settle(rt, handle, SettleStatus.SETTLED, 0.9)
    assert rt.queue.get(handle).status is SettleStatus.SETTLED
    assert [row.status for row in rt.queue.history(handle)] == [
        SettleStatus.TIMED_OUT, SettleStatus.SETTLED]
    rt._close_price_window()
    expected = rt._penalty_for("evaluator", handle, abstaining=True)
    assert expected > 0
    rt._credit_abstentions()
    rows = [row for row in rt.ledger._recovery_items()
            if row.get("kind") == "router.unscored_priced" and row["handle"] == handle]
    row, = rows
    assert row["penalty"] == pytest.approx(expected)
    assert row["terms"][0]["held"] is True
    assert handle not in rt.noop_credits
