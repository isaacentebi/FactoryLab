"""NOOP is not free (ruling R9; versioning P4, primitive F1).

A router may draw "wake nobody", because not spending is a real choice. Its credit
was an architect constant no charter price touched, so a woken seat had to beat
that constant plus its card penalty and the frontier drifted to abstention. The
abstention now bears the same charter prices a woken decision of its role bears in
the window it was drawn in.
"""

import pytest

from factorylab.cortex.registration import measured_role
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


def test_a_mixed_menu_abstention_is_priced_as_the_draw_would_have_woken(monkeypatch):
    """A judge router whose menu also holds a producing seat (a population seat that
    accepts ProducerReturn) stood in for both roles: its abstention bears each role's
    price in the proportion the draw would have woken them, not the price of the
    contract a mixed-menu NOOP is filed under."""
    from factorylab.learners.router import Sample

    monkeypatch.setattr(pricing, "close_window", lambda *_a: None)
    rt = _runtime(_card(per=None))
    sample = Sample(("eval-a", "eval-b", "seed-decider", NOOP), (0.3, 0.3, 0.2, 0.2), NOOP,
                    0, "router:ProducerReturn", "h", ())
    roles = rt._abstention_roles(sample)
    assert roles == pytest.approx({"evaluator": 0.75, "producer": 0.25})
    noop = _abstention(rt)
    rt.window.decisions[noop]["menu_roles"] = roles
    for seat in ("eval-a", "eval-b"):
        _decision(rt, seat)
    _commitments(rt, "eval-a", censored=4)
    rt._close_price_window()
    expected = (0.75 * rt._penalty_for("evaluator", noop)
                + 0.25 * rt._penalty_for("producer", noop))
    assert expected > 0
    reward, charged = rt._priced_abstention(noop, 0.5)
    assert charged == pytest.approx(expected) and reward == pytest.approx(0.5 - expected)
    # A one-role menu is that role alone, and an empty draw weighs its seats equally.
    judges = Sample(("eval-a", "eval-b", NOOP), (0.0, 0.0, 1.0), NOOP, 0, "r", "h", ())
    assert rt._abstention_roles(judges) == {"evaluator": 1.0}


@pytest.mark.gate
def test_every_abstention_a_world_draws_is_priced_on_the_roles_of_its_menu():
    from factorylab.runtime.loop import Runtime

    rt = Runtime(load_manifest("scripted"), events=40, seed=3, initial_balance_micro=None,
                 ledger_path=None, router_gamma=0.1)
    rt.run()
    items = rt.ledger._recovery_items()
    opened = {i["handle"]: i for i in items if i.get("kind") == "decision.open"
              and i["propensity"]["chosen"] == NOOP and i.get("parent_handle") is None}
    assert opened
    # The window still open holds each abstention drawn in it, weighted over the roles
    # of the seats its draw could have woken, and filed under the heaviest of them.
    held = {h: d for h, d in rt.window.decisions.items() if h in opened}
    assert held
    for handle, row in held.items():
        prop = opened[handle]["propensity"]
        seats = [(a, p) for a, p in zip(prop["action_ids"], prop["probs"], strict=True)
                 if a != NOOP and a in rt.assemblies]
        total = sum(p for _a, p in seats)
        expected: dict[str, float] = {}
        for seat, p in seats:
            role = measured_role(rt.assemblies[seat].spec.emits)
            expected[role] = expected.get(role, 0.0) + p / total
        assert row["menu_roles"] == pytest.approx(expected)
        assert row["role"] == max(sorted(expected), key=expected.get)
    priced = [i for i in items if i.get("kind") == "router.abstention_priced"]
    assert priced and {row["handle"] for row in priced} <= set(opened)
    for row in priced:
        assert row["reward"] == pytest.approx(max(0.0, row["neutral"] - row["penalty"]))
