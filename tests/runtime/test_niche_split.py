"""A niche decision bears nothing and changes nobody's split, for every card kind (R-E).

Codex on #152: niche decisions were zeroed on their own penalty, but their cost,
well-formedness, tool calls and turnover still sat in the split's denominators and in
the frozen cost shares, so adding one lowered ordinary decisions' penalties. Every
split reads its decisions through one predicate (``PricingMixin._in_split``).

The card's measurement is the world's and may count the niche decision (and the price
the controller closes the window at follows it); this test pins both to the run without
one, so only the split can move A's and B's penalties.
"""

from dataclasses import replace

import pytest

from factorylab.charter.charter import MetricCard
from factorylab.charter.windows import MetricWindow
from factorylab.cortex.request import Return
from factorylab.kernel.queue import PropensityRecord
from factorylab.runtime import pricing
from factorylab.runtime.loop import Runtime
from factorylab.runtime.worlds import load_manifest

#: (observation, region, per): one card of every way a penalty is split.
CARDS = [
    ("cost_per_return", {"rule": "at most", "hi": 50}, None),      # frozen cost shares
    ("cost_per_attempt", {"rule": "at most", "hi": 50}, None),     # exact: cost
    ("well_formed_rate", {"rule": "at least", "lo": 0.9}, None),   # exact: deficit
    ("tool_calls", {"rule": "at most", "hi": 1}, None),            # exact: own calls
    ("turnover", {"rule": "at most", "hi": 0.001}, None),          # exact: own notional
    ("noop_share", {"rule": "at most", "hi": 0.25}, None),         # relief rate
    ("registrations", {"rule": "at most", "hi": 0}, None),         # generic count
    ("noop_share", {"rule": "at most", "hi": 0.25}, "role"),       # scoped: peers
]


def _decision(rt, name, *, ok, cost, calls, notional, action, niche=False, invocations=1):
    handle = rt.queue.open(
        actor="test-router", event_id=name, channel="verdict", deadline_ns=10**18,
        parent_handle=None, cost_ceiling=0,
        propensity=PropensityRecord(("seed-decider",), (1.0,), "seed-decider", 0,
                                    "test-router", "state"))
    rt.handle_to_assembly[handle] = "seed-decider"
    sample = rt._contribution(handle, "producer")
    sample.update(invocations=invocations, ok=int(ok), cost=cost, tool_calls=calls,
                  notional_micro=notional)
    if niche:
        sample["niche"] = True  # as ``_invoke`` marks a decision of the protected trial
    rt.card_samples.returned(handle=handle, assembly="seed-decider", role="producer",
                             window=rt.window.index,
                             ret=Return(handle, {"action": action}, cost,
                                        "ok" if ok else "failed"))
    return handle


def _run(card_spec, monkeypatch, *, niche, measured=None):
    """A's and B's penalties, and the card's measurement, in one closed window."""
    observation, region, per = card_spec
    monkeypatch.setattr(pricing, "close_window", lambda *_a: None)
    card = MetricCard("card", "care with scarce resources", "A reading.", "fraction",
                      MetricWindow("windows", 1, per), region, observation, "producer")
    seed = load_manifest("scripted")
    rt = Runtime(replace(seed, charter=replace(seed.charter, cards=(card,))), events=0,
                 seed=1, initial_balance_micro=None, ledger_path=None, router_gamma=0.1)
    rt._derive_regions()
    rt.controller.set_price("card", 0.05, amendment_id="test")
    a = _decision(rt, "a", ok=True, cost=100, calls=2, notional=1_000_000, action="hold")
    b = _decision(rt, "b", ok=False, cost=100, calls=2, notional=1_000_000, action="buy")
    w = rt.window  # the window's counters are A's and B's, in both runs
    w.costs, w.invocations, w.ok, w.tool_calls = [100], 2, 1, 4
    w.notional_micro, w.producer_returns, w.noop_returns, w.registrations = 2_000_000, 2, 1, 1
    if niche:
        # One well-formed and one malformed invocation: it sits in every numerator
        # and denominator a split could read.
        _decision(rt, "n", ok=True, cost=1_000_000, calls=50, notional=10**9,
                  action="hold", niche=True, invocations=2)
    rt._close_price_window()
    if measured is not None:
        (rt.window.closed_values, rt.window.closed_scopes,
         rt.window.closed_prices) = measured
    penalties = (rt._penalty_for("producer", a), rt._penalty_for("producer", b))
    return penalties, (dict(rt.window.closed_values), dict(rt.window.closed_scopes),
                       dict(rt.window.closed_prices))


@pytest.mark.parametrize("card_spec", CARDS, ids=[f"{o}-{p}" for o, _r, p in CARDS])
def test_a_niche_decision_changes_nobody_s_penalty(card_spec, monkeypatch):
    without, measured = _run(card_spec, monkeypatch, niche=False)
    with_niche, _ = _run(card_spec, monkeypatch, niche=True, measured=measured)
    assert any(p > 0 for p in without), (card_spec, without)  # the card bites
    assert with_niche == pytest.approx(without), card_spec
