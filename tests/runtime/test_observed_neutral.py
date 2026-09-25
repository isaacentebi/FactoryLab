"""Nothing happened is priced at what happening earned (wave 16, D4; ruling R-F).

A NOOP draw, a decline and a censored or timed-out decision each delivered nothing
measurable. Each is credited the router's observed mean raw settled score, less the
card penalty its role bears: the one imputation that tilts a mean-based router neither
toward waking a seat nor toward abstaining. No flat 0.5: the published prior
``NEUTRAL_REWARD`` stands only until the router's first settled round, and the observed
mean is cumulative, so a router that stops waking seats keeps its last mean.
"""

from __future__ import annotations

import random

import pytest

from factorylab.kernel.queue import PropensityRecord, SettleStatus
from factorylab.learners.base import NEUTRAL_REWARD
from factorylab.runtime import pricing
from factorylab.runtime.routing import RouterState
from factorylab.runtime.shared import CH_CONFORMITY, NOOP
from tests.runtime.test_attributable_blame import _card, _commitments, _decision, _runtime


def _state():
    from factorylab.learners.exp3 import EXP3
    from factorylab.learners.router import Router

    learner = EXP3(["a", "b", NOOP], 0.1, id="router:X")
    return RouterState("X", ["a", "b", NOOP], learner,
                       Router(learner, lambda _k: ["a", "b"]))


def test_the_neutral_credit_is_the_observed_mean_raw_score_and_the_prior_only_before_it():
    state = _state()
    assert state.neutral() == NEUTRAL_REWARD  # the published prior: nothing observed yet
    for definition, raw in (("verdict-v1", 0.30), ("verdict-v1", 0.40),
                            ("evaluation-v1", 0.20)):
        state.record_round(definition, raw)
    assert state.neutral() == pytest.approx(0.30)
    # Cumulative, never windowed: no later round of any kind brings back the prior.
    restored = RouterState.restore(state.state())
    assert restored.neutral() == pytest.approx(0.30)
    assert restored.definitions == state.definitions
    # A router saved before wave 16 counted rounds without scores: it starts afresh.
    legacy = state.state()
    legacy["definitions"] = {"verdict-v1": 3}
    assert RouterState.restore(legacy).neutral() == NEUTRAL_REWARD


def _drawn(rt, state, seat, channel=CH_CONFORMITY, *, role="evaluator"):
    """A decision ``state``'s router drew for ``seat``, measured in ``role``."""
    feasible = lambda a: (a == seat, "")  # noqa: E731
    sample = next(s for s in (state.router.route(state.kind, feasible, random.Random(i))
                              for i in range(200)) if s.chosen == seat)
    handle = rt.queue.open(actor=state.learner.id, event_id=f"draw-{seat}",
                           propensity=rt._propensity(sample), channel=channel,
                           deadline_ns=10**18, parent_handle=None, cost_ceiling=0)
    rt.handle_to_assembly[handle] = seat
    sample_row = rt._contribution(handle, role)
    sample_row["invocations"] = sample_row["ok"] = 1
    return handle


def test_noop_decline_and_censored_are_one_credit_to_the_micro_unit(monkeypatch):
    """In one window and role, an abstention, a declined commission and a censored
    decision are credited the same amount: r-bar less the same penalty."""
    monkeypatch.setattr(pricing, "close_window", lambda *_a: None)
    rt = _runtime(_card(per=None))
    state = rt.routers["ProducerReturn"][0]
    for raw in (0.62, 0.58, 0.47):
        state.record_round("evaluation-v1", raw)
    woken = [_decision(rt, seat) for seat in ("eval-a", "eval-b")]
    declined = _drawn(rt, state, "eval-a")
    censored = _drawn(rt, state, "eval-b")
    noop = rt.queue.open(actor=state.learner.id, event_id="noop", channel=CH_CONFORMITY,
                         deadline_ns=10**18, parent_handle=None, cost_ceiling=0,
                         propensity=PropensityRecord((NOOP,), (1.0,), NOOP, 0,
                                                     state.learner.id, "state"))
    rt._contribution(noop, "evaluator")
    _commitments(rt, "eval-a", censored=4)
    rt._close_price_window()
    penalty = rt._penalty_for("evaluator", woken[0])
    assert penalty > 0
    rt._settle_declined(declined, "no view")
    rt.queue.settle(censored, channel=CH_CONFORMITY, score=0.0, status=SettleStatus.CENSORED,
                    definition_version="censored-v1", sampling_ref=None)
    rt._deliver_returns()
    rows = {row["handle"]: row for row in rt.ledger._recovery_items()
            if row.get("kind") in ("router.decline_priced", "router.unscored_priced")}
    cap = rt.m.prices.penalty_cap  # the one affine map (ruling R10-g)
    expected = (state.neutral() + cap - penalty) / (1 + cap)
    assert state.neutral() == pytest.approx(0.5566666666666666)
    assert rows[declined]["reward"] == pytest.approx(expected)
    assert rows[censored]["reward"] == pytest.approx(expected)
    reward, charged = rt._priced_abstention(noop, state.neutral())
    assert charged == pytest.approx(penalty) and reward == pytest.approx(expected)
    # Never a flat 0.5: the credit moves with what the router's rounds earned.
    assert abs(expected - (NEUTRAL_REWARD + cap - penalty) / (1 + cap)) > 0.03


def test_a_router_that_stops_waking_seats_keeps_its_last_observed_mean(monkeypatch):
    """Absence is not a reset: after its last settled round, every abstention the router
    draws is priced at the mean it last observed, however long it abstains."""
    monkeypatch.setattr(pricing, "close_window", lambda *_a: None)
    rt = _runtime(_card(per=None))
    state = rt.routers["ProducerReturn"][0]
    state.record_round("evaluation-v1", 0.21)
    for _ in range(5):
        rt._close_price_window()
        rt._manage_reserve_window()
    assert state.neutral() == pytest.approx(0.21)
    assert rt._router_neutral(_drawn(rt, state, "eval-a")) == pytest.approx(0.21)


def test_a_seat_own_learner_credits_an_unscored_decision_as_its_router_does(monkeypatch):
    monkeypatch.setattr(pricing, "close_window", lambda *_a: None)
    rt = _runtime(_card(per=None))
    state = rt.routers["ProducerReturn"][0]
    state.record_round("evaluation-v1", 0.33)
    handle = _drawn(rt, state, "eval-a")
    assert rt._router_neutral(handle) == pytest.approx(0.33)
    assert rt._router_neutral("no-such-handle") == NEUTRAL_REWARD


def test_an_all_abstaining_router_is_credited_the_published_prior_until_it_observes(
        monkeypatch):
    """Addendum item 2: a router that has only ever abstained has no observation, so its
    abstentions are credited the published prior (world.scoring.abstention states it);
    the first settled seat round replaces it for good."""
    monkeypatch.setattr(pricing, "close_window", lambda *_a: None)
    rt = _runtime(_card(per=None))
    state = rt.routers["ProducerReturn"][0]
    noops = [_drawn(rt, state, NOOP) for _ in range(3)]
    for handle in noops:
        rt.queue.settle(handle, channel=CH_CONFORMITY, score=0.0,
                        status=SettleStatus.INAPPLICABLE, definition_version="noop",
                        sampling_ref=None)
    rt._deliver_returns()
    for handle in noops:  # due now, not at the far deadline these draws were given
        rt.noop_credits[handle]["due_tick"] = rt.ticks_consumed
    rt._close_price_window()  # a NOOP's price is its window's, known at the close (D5)
    rt._deliver_returns()
    assert state.neutral() == NEUTRAL_REWARD and not state.definitions
    credited = [r for r in rt.ledger._recovery_items()
                if r.get("kind") == "router.abstention_priced" and r["handle"] in noops]
    assert len(credited) == 3 and all(r["neutral"] == NEUTRAL_REWARD for r in credited)
    published = rt._scoring_block()["abstention"]
    assert f"published prior {NEUTRAL_REWARD}" in published
    assert "before the router's first settled round" in published
    state.record_round("verdict-v1", 0.12)
    assert state.neutral() == pytest.approx(0.12)
