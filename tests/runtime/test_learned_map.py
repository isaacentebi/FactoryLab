"""Every learner learns one affine map, and no penalty is partly escaped (wave 16, R10-g).

Ruling R10-g widens R10-c: a clip ``max(0, r - p)`` lets a low-reward decision bear
less than its penalty, so a seat's own learner, like every router, learns
``(r + cap - p) / (1 + cap)`` for every round, with ``p`` the penalty the round bears
(0 when none), and no clip anywhere on a learning path. The router's observed mean,
which prices what nothing delivered (D4), is made of raw scores before the penalty.
"""

from types import SimpleNamespace

import pytest

from tests.runtime.test_attributable_blame import _commitments
from tests.runtime.test_refusal_price import _priced_runtime, _refuse


def _assembly_round(monkeypatch, price: float, raw: float = 0.1, censored: int = 4,
                    router_first: bool = False):
    """One settled round of the seat's own learner: raw score ``raw``, the card priced
    from ``price`` at its window's close, violated while ``censored`` > 0."""
    rt = _priced_runtime(monkeypatch)
    rt.controller.set_price("censorship-bound", price, amendment_id="price")
    _state, handle = _refuse(rt)
    updates = []
    rt.assembly_learners["seed-decider"] = SimpleNamespace(
        update_for=lambda h, fb: updates.append((h, fb)), discard_for=lambda h: None,
        observed=SimpleNamespace(record=lambda *_a: None))
    rt.assembly_rounds[handle] = "seed-decider"
    _commitments(rt, "eval-a", censored=censored)
    rt._close_price_window()
    rt._settle_priced(handle, channel="verdict", score=raw, definition_version="verdict-v1",
                      sampling_ref=None, cards="producer")
    penalty = next(row["penalty"] for row in rt.ledger._recovery_items()
                   if row.get("kind") == "price.penalty" and row["handle"] == handle)
    if router_first:
        rt._deliver_returns()  # the router reads the round before the seat's learner
    rt._close_assembly_rounds()
    ((_h, fb),) = updates
    return rt, penalty, fb.reward, handle


def test_a_higher_penalty_on_a_low_reward_round_is_learned_strictly_lower(monkeypatch):
    """r = 0.1 under two penalties both above it: a clip would learn 0 for both."""
    rt_low, p_low, learned_low, _ = _assembly_round(monkeypatch, price=0.0)
    rt_high, p_high, learned_high, handle = _assembly_round(monkeypatch, price=0.05)
    assert 0.1 < p_low < p_high < rt_low.m.prices.penalty_cap
    assert learned_high < learned_low
    cap = rt_low.m.prices.penalty_cap
    assert learned_low == pytest.approx((0.1 + cap - p_low) / (1 + cap))
    assert learned_high == pytest.approx((0.1 + cap - p_high) / (1 + cap))
    # The published settlement still shows the score less its penalty, in [0, 1].
    assert rt_high.queue.history(handle)[-1].score == 0.0


def test_an_uncharged_round_uses_the_same_map(monkeypatch):
    rt, penalty, learned, _ = _assembly_round(monkeypatch, price=0.0, censored=0)
    cap = rt.m.prices.penalty_cap
    assert penalty == 0.0 and learned == pytest.approx((0.1 + cap) / (1 + cap))


def test_the_router_s_observed_mean_is_made_of_raw_scores(monkeypatch):
    """D4: the mean a round that delivered nothing is credited is the router's settled
    scores before their card penalty, which the settlement must keep until the router
    reads it (a regression popped it at once, so the mean was made of penalised
    scores)."""
    rt = _priced_runtime(monkeypatch)
    state, handle = _refuse(rt)
    _commitments(rt, "eval-a", censored=4)
    rt._close_price_window()
    rt._settle_priced(handle, channel="verdict", score=0.7, definition_version="verdict-v1",
                      sampling_ref=None, cards="producer")
    assert rt.queue.history(handle)[-1].score < 0.7  # penalised as published
    rt._deliver_returns()
    assert state.definitions["verdict-v1"] == [1, pytest.approx(0.7)]


def test_the_seat_s_learner_reads_the_penalty_after_the_router_has(monkeypatch):
    """The router's read never consumes what the seat's own learner still needs."""
    rt, penalty, learned, _ = _assembly_round(monkeypatch, price=0.0, router_first=True)
    cap = rt.m.prices.penalty_cap
    assert penalty > 0.1 and learned == pytest.approx((0.1 + cap - penalty) / (1 + cap))


def test_a_learned_round_s_evidence_is_dropped_once_nobody_is_owed_it(monkeypatch):
    """The raw score and penalty are kept for the round's learners and no longer: a
    decision the kernel owes nothing and no seat's learner waits on keeps neither, so
    the checkpoint does not grow with them (the plateau)."""
    rt, _penalty, _learned, handle = _assembly_round(monkeypatch, price=0.0)
    rt._deliver_returns()
    rt._prune_price_evidence()
    # Its seat has not read the return it was delivered: the kernel still owes it.
    assert rt.queue.owed(handle) is not None
    assert handle in rt.raw_scores and handle in rt.round_penalties
    owed = rt.queue.owed
    monkeypatch.setattr(rt.queue, "owed", lambda h: None if h == handle else owed(h))
    rt._prune_price_evidence()  # read: nothing is owed any more
    assert handle not in rt.raw_scores and handle not in rt.round_penalties


# --- R10-l: one map per learner, applied exactly once ----------------------------------


def test_an_uncharged_raw_score_is_mapped_once_on_each_learner_s_own_bound():
    """cap 0.5, raw 0.1, P = 0: a router (B = 1.0) learns 0.55 and a seat's own learner
    (B = 0.5) 0.4. Composing the card map and the thrash map gave a router 0.6, a
    scale compressed twice."""
    from tests.conftest import make_runtime

    rt = make_runtime()
    assert rt.m.prices.penalty_cap == 0.5
    router = rt._all_router_states()[0]
    assert rt._learning_value("r-round", 0.1, 0.0, router=router) == pytest.approx(0.55)
    assert rt._learning_value("s-round", 0.1, 0.0) == pytest.approx(0.4)


def test_a_card_share_and_a_thrash_charge_of_equal_size_lower_a_router_equally():
    """Both are charges on the one total P: neither is weighed 1 / (1 + cap) less."""
    from tests.conftest import make_runtime

    rt = make_runtime()
    router = rt._all_router_states()[0]
    uncharged = rt._learning_value("none", 0.3, 0.0, router=router)
    carded = rt._learning_value("card", 0.3, 0.2, router=router)
    rt.thrash_charges["thrash"] = 0.2
    thrashed = rt._learning_value("thrash", 0.3, 0.0, router=router)
    rt.thrash_charges["both"] = 0.2
    both = rt._learning_value("both", 0.3, 0.2, router=router)
    assert carded == pytest.approx(thrashed) and thrashed < uncharged
    assert uncharged - both == pytest.approx(2 * (uncharged - carded))
    # At the bound (both charges at the cap) a zero raw score learns exactly 0.
    rt.thrash_charges["max"] = rt.m.prices.penalty_cap
    assert rt._learning_value("max", 0.0, rt.m.prices.penalty_cap,
                              router=router) == pytest.approx(0.0)


def test_the_map_is_applied_in_exactly_one_place():
    """Grep-level: ``_learned`` (the map) has one caller, ``_learning_value``, and no
    other learning-path code writes the affine formula or maps a value it returns."""
    import ast
    import pathlib

    import factorylab

    def one_plus(node):
        return (isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add)
                and isinstance(node.left, ast.Constant) and node.left.value == 1)

    root = pathlib.Path(factorylab.__file__).parent
    callers, formulas = [], []
    for path in sorted(root.rglob("*.py")):
        tree = ast.parse(path.read_text())
        for fn in ast.walk(tree):
            if not isinstance(fn, ast.FunctionDef | ast.AsyncFunctionDef):
                continue
            for node in ast.walk(fn):
                if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                        and node.func.attr == "_learned"):
                    callers.append((path.name, fn.name))
                # Code dividing by ``1 + x``: the shape of the affine map.
                if (isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div)
                        and one_plus(node.right)):
                    formulas.append((path.name, fn.name))
    assert callers == [("feedback.py", "_learning_value")]
    # On the learning path (the runtime's feedback, pricing and routing), the one
    # formula is the map itself; the bound it takes is ``_charge_bound``'s.
    assert ("pricing.py", "_learned") in formulas
    assert not [f for f in formulas if f[0] in ("feedback.py", "pricing.py", "routing.py")
                and f != ("pricing.py", "_learned")], formulas
    for name in ("_round_learned", "_thrash_charged"):
        assert not any(name in path.read_text() for path in root.rglob("*.py")), name
