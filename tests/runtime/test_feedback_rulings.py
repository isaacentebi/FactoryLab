"""Lifecycle prices, current sampling evidence, and niche settlement (§II.b/III/IV)."""

from dataclasses import replace

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


@pytest.mark.parametrize("change", ["remove", "redefine"])
@pytest.mark.parametrize("restored", [False, True])
def test_r5_obsolete_gap_cannot_raise_sampling_after_identity_change(
        monkeypatch, change, restored):
    """Current charter identity bounds both latched gaps and sampled support (§III/IV.a)."""
    from factorylab.runtime.resume import restore_runtime, runtime_state

    rt, card = _gap_runtime(monkeypatch)
    clock_type = type(rt.clockwork)
    monkeypatch.setattr(clock_type, "due", lambda *_a: False)
    rt.stats.reserve_windows = 1
    rt.card_samples.values[card.id] = 0.2
    rt._sampling_actuator()
    rt.stats.reserve_windows = 2
    rt.card_unmeasured[card.id] = 1
    rt._sampling_actuator()
    assert card.id in rt.sampling_pending_gaps
    replacement = replace(card, observation="verdict_std")
    rt.charter = replace(rt.charter, cards=() if change == "remove" else (replacement,))
    rt._derive_regions()
    if restored:
        twin, _ = _gap_runtime(monkeypatch)
        restore_runtime(twin, runtime_state(rt))
        rt = twin
    before = rt.multi_judge_share
    monkeypatch.setattr(clock_type, "due", lambda *_a: True)
    rt._sampling_actuator()
    assert rt.multi_judge_share == before
    assert card.id not in rt.sampling_pending_gaps
    assert card.id not in rt.card_samples.values
    assert card.id not in rt.sampling_card_support
    if change == "redefine":
        rt.card_unmeasured[card.id] = 1
        assert rt._sampling_gaps() == {}
        rt.card_unmeasured[card.id] = 0
        rt.card_samples.values[card.id] = 0.1
        assert rt._sampling_gaps() == {}
        assert card.id in rt.sampling_card_support
        rt.card_unmeasured[card.id] = 1
        assert card.id in rt._sampling_gaps()
