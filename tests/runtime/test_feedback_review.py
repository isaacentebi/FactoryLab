"""Review regressions: enforced prices (§II.b), sampling (§III, §IV.b), time (§IV.c)."""

from dataclasses import replace

import pytest

from factorylab.charter.windows import MetricWindow
from factorylab.kernel.queue import SettleStatus
from factorylab.runtime import pricing
from tests.runtime.test_abstention_price import _abstention, _gap_runtime, _next_gap
from tests.runtime.test_attributable_blame import _decision
from tests.runtime.test_learning_signal import _settle


def test_s4_noop_decline_censor_timeout_pay_identical_held_price(monkeypatch):
    """Equivalent neutral imputations bear the same supported held liability (§II.b)."""
    rt, _ = _gap_runtime(monkeypatch)
    rt.window.verdicts = {"subject": {"eval-a": [0.2]}}
    rt._close_price_window()
    _next_gap(rt)
    handles = [_abstention(rt), *(_decision(rt, "eval-a") for _ in range(3))]
    rt._close_price_window()
    rt._settle_declined(handles[1], "declined")
    _settle(rt, handles[2], SettleStatus.CENSORED)
    rt.queue.expire(10**19)
    assert rt.queue.get(handles[3]).status is SettleStatus.TIMED_OUT
    prices = [rt._priced_abstention(handle) for handle in handles]
    assert prices[0] > 0
    assert prices == pytest.approx([prices[0]] * 4)
    for handle in handles:
        term, = rt._abstention_price_terms(handle)
        assert term["held"] is True
        assert term["source_window"] == 0
        assert term["window"] == 1


def test_s5_late_fill_abstention_waits_beyond_closed_origin(monkeypatch):
    """Late-fill attribution must freeze before neutral credit is priced (§IV.c)."""
    rt, card = _gap_runtime(monkeypatch)
    rt.predicates.register("calls", "Behavioural fact",
                           "def resolve(facts):\n    return facts['tool_calls'] > 0\n",
                           facts={"tool_calls": 0}, persist=lambda _p: None)
    card = replace(card, observation="turnover", answers_for="producer",
                   window=MetricWindow("windows", 1, None), acceptable_region="at most 1",
                   holdout=("calls@1",))
    rt.charter = replace(rt.charter, cards=(card,))
    rt._derive_regions()
    handle = _decision(rt, "seed-decider")
    rt.window.decisions[handle]["role"] = "producer"
    rt._close_price_window()
    original = rt.window
    rt.window = pricing.MeasureWindow(original.index + 1, 1_000_000)
    rt.price_windows[rt.window.index] = rt.window
    rt.consequences.table = rt.consequences.table.start(handle, rt.n).order(
        "late", handle, "0.1", coin="BTC")
    rt._record_fill_notional({"order_id": "late", "size": "0.1", "px": "1"})
    rt.window.notional_micro = 100_000
    rt._settle_declined(handle, "declined")
    assert original.closed_values is not None
    assert rt.window.closed_values is None
    assert rt._abstention_awaits_close(handle)
    rt._close_price_window()
    assert not rt._abstention_awaits_close(handle)


def test_s7_gap_survives_recovery_and_checkpoint_until_sampling_fires(monkeypatch):
    """A closed-window support loss remains actionable until consumed (§IV.b)."""
    from factorylab.runtime.resume import restore_runtime, runtime_state

    rt, card = _gap_runtime(monkeypatch)
    clock_type = type(rt.clockwork)
    monkeypatch.setattr(clock_type, "due", lambda *_a: False)
    rt.stats.reserve_windows = 1
    rt.card_samples.values[card.id] = 0.2
    rt.card_unmeasured[card.id] = 0
    rt._sampling_actuator()
    rt.stats.reserve_windows = 2
    rt.card_samples.values.clear()
    rt.card_unmeasured[card.id] = 1
    rt._sampling_actuator()  # Gap occurs between firings.
    rt.stats.reserve_windows = 3
    rt.card_samples.values[card.id] = 0.8
    rt.card_unmeasured[card.id] = 0
    rt._sampling_actuator()  # Recovery must not erase the earlier gap.
    twin, _ = _gap_runtime(monkeypatch)
    restore_runtime(twin, runtime_state(rt))
    monkeypatch.setattr(clock_type, "due", lambda *_a: True)
    for branch in (rt, twin):
        before = branch.multi_judge_share
        branch._sampling_actuator()
        assert branch.multi_judge_share == pytest.approx(before + branch.ev.sampling_step)
        raises = [r for r in branch.ledger._recovery_items()
                  if r.get("kind") == "sampling.rate_raise"]
        assert raises[-1]["gaps"][card.id] == {
            "unmeasured_windows": 1, "last_measured_window": 0}
        assert branch.sampling_pending_gaps == {}
        branch._sampling_actuator()
        assert branch.multi_judge_share == pytest.approx(before + branch.ev.sampling_step)
