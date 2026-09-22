"""NOOP is not free (ruling R9; versioning P4, primitive F1).

A router may draw "wake nobody", because not spending is a real choice. Its credit
was an architect constant no charter price touched, so a woken seat had to beat
that constant plus its card penalty and the frontier drifted to abstention. The
abstention now bears the same charter prices a woken decision of its role bears in
the window it was drawn in.
"""

import pytest

from factorylab.kernel.queue import PropensityRecord
from factorylab.runtime import pricing
from factorylab.runtime.shared import NOOP
from factorylab.runtime.worlds import load_manifest
from tests.runtime.test_attributable_blame import _card, _commitments, _decision, _runtime


def _abstention(rt, role="evaluator"):
    handle = rt.queue.open(
        actor="test-router", event_id="noop", channel="conformity", deadline_ns=10**18,
        parent_handle=None, cost_ceiling=0,
        propensity=PropensityRecord((NOOP,), (1.0,), NOOP, 0, "test-router", "state"),
    )
    rt._contribution(handle, role)
    return handle


def test_an_abstention_bears_the_price_a_woken_decision_of_its_role_bears(monkeypatch):
    monkeypatch.setattr(pricing, "close_window", lambda *_a: None)
    rt = _runtime(_card(per=None))
    woken = [_decision(rt, seat) for seat in ("eval-a", "eval-b")]
    noop = _abstention(rt)
    _commitments(rt, "eval-a", censored=4)
    rt._close_price_window()
    penalty = rt._penalty_for("evaluator", woken[0])
    assert penalty > 0
    assert rt._penalty_for("evaluator", noop) == pytest.approx(penalty)
    reward, charged = rt._priced_abstention(noop, 0.5)
    assert charged == pytest.approx(penalty) and reward == pytest.approx(0.5 - penalty)
    # A woken seat that delivered what an abstention is worth is no worse off than it.
    assert max(0.0, 0.5 - rt._penalty_for("evaluator", woken[1])) == pytest.approx(reward)


def test_an_abstention_the_window_never_recorded_is_credited_unpriced():
    rt = _runtime(_card(per=None))
    handle = rt.queue.open(
        actor="test-router", event_id="noop", channel="conformity", deadline_ns=10**18,
        parent_handle=None, cost_ceiling=0,
        propensity=PropensityRecord((NOOP,), (1.0,), NOOP, 0, "test-router", "state"),
    )
    assert rt._priced_abstention(handle, 0.5) == (0.5, 0.0)


@pytest.mark.gate
def test_every_abstention_a_world_draws_is_priced_on_its_role():
    from factorylab.runtime.loop import Runtime

    rt = Runtime(load_manifest("scripted"), events=40, seed=3, initial_balance_micro=None,
                 ledger_path=None, router_gamma=0.1)
    rt.run()
    items = rt.ledger._recovery_items()
    noops = {i["handle"]: i["actor"] for i in items if i.get("kind") == "decision.open"
             and i["propensity"]["chosen"] == NOOP and i.get("parent_handle") is None}
    assert noops
    # The window still open holds each abstention drawn in it, in the role it would
    # have filled: a judge router's abstention is an evaluator's, a tick router's a
    # producer's.
    held = {h: d["role"] for h, d in rt.window.decisions.items() if h in noops}
    assert held
    for handle, role in held.items():
        expected = "evaluator" if noops[handle].startswith("router:ProducerReturn") else None
        assert role in ("producer", "evaluator", "meta", "antagonist")
        if expected:
            assert role == expected
    priced = [i for i in items if i.get("kind") == "router.abstention_priced"]
    assert priced and {row["handle"] for row in priced} <= set(noops)
    for row in priced:
        assert row["reward"] == pytest.approx(max(0.0, row["neutral"] - row["penalty"]))
