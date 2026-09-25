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
