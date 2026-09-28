"""Review regressions: enforced prices (§II.b), sampling (§III, §IV.b), time (§IV.c)."""

import pytest

from factorylab.kernel.queue import SettleStatus
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
