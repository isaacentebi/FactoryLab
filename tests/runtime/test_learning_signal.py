"""The router's reward line means what it claims (defects 2, 3, 4 and 14).

A router learns from the decisions it sampled, once each, on the evidence the
world produced about them. It does not learn from a draw it never made, from a
decision twice, or from a missing fact read as a zero.
"""

import random

import pytest

from factorylab.kernel.events import Event, EventKind
from factorylab.kernel.queue import PropensityRecord, SettleStatus
from factorylab.runtime.shared import CH_CONFORMITY, NOOP
from tests.conftest import make_runtime


def _tick(n: int = 1) -> Event:
    return Event(f"tick-{n}", EventKind.TICK, n, {}, "test")


def test_a_quiet_tick_keeps_no_snapshot_in_a_keyed_router():
    """Defect 14: a draw that reached nobody opens no round, so it freezes none."""
    rt = make_runtime()
    state = rt._build_router("Tick", "blum_mansour", 0.1)
    rt._asleep = lambda _seat, _ev: "asleep: test"
    rt._route_with(state, _tick())
    assert any(i["kind"] == "tick.quiet" for i in rt.ledger._recovery_items())
    assert state.learner.inner._snapshots == {}


# --- a router's arms and the decisions it drew -------------------------------------------


def _router(rt, kind="ProducerReturn"):
    state = rt.routers[kind][0]
    return state, state.learner.id


def _drawn(rt, state, chosen, *, channel=CH_CONFORMITY, parent=None, hash_="drawn"):
    """Open one decision the router drew ``chosen`` for, with a replayable seed."""
    arms = tuple(state.universe)
    probs = tuple(1 / len(arms) for _ in arms)
    probs = (*probs[:-1], 1 - sum(probs[:-1]))
    seed = next(s for s in range(10_000)
                if random.Random(s).choices(arms, weights=probs, k=1)[0] == chosen)
    lid = state.learner.id
    return rt.queue.open(actor=lid, event_id=f"draw-{chosen}-{rt.stats.decisions}",
                         propensity=PropensityRecord(arms, probs, chosen, seed, lid, hash_),
                         channel=channel, deadline_ns=10**15, parent_handle=parent,
                         cost_ceiling=0)


def _settle(rt, handle, status, score=0.0):
    rt.queue.settle(handle, channel=rt.queue.get(handle).channel, score=score,
                    status=status, definition_version="test-v1", sampling_ref=None)


def _weights(state):
    return dict(state.learner.state()["log_weights"])


def test_a_censored_arm_is_not_penalised_for_being_censored():
    """Defect 2. For gain-based EXP3 a skipped update is a zero reward: the arm whose
    outcomes go unobserved falls behind the arm whose outcomes are read, at the same
    true worth. Censoring is neutral now: the unobserved round is credited the
    router's observed mean raw score (wave 16, D4), never a zero it did not observe."""
    rt = make_runtime()
    state, _lid = _router(rt)
    a, b = [arm for arm in state.universe if arm != NOOP][:2]
    _settle(rt, _drawn(rt, state, a), SettleStatus.SETTLED, 0.6)
    _settle(rt, _drawn(rt, state, b), SettleStatus.CENSORED)
    rt._deliver_returns()
    weights = _weights(state)
    floor = min(weights.values())
    # a credited its 0.6, b the router's observed mean, 0.6, at the same odds.
    assert weights[b] == pytest.approx(weights[a]) and weights[b] > floor


def test_an_abstention_does_not_sink_to_the_exploration_floor():
    """Defect 2. A router abstention settles inapplicable every time; it used to be the one
    arm that was never updated, so every positive reward elsewhere pushed it down."""
    rt = make_runtime()
    state, _lid = _router(rt)
    arm = next(a for a in state.universe if a != NOOP)
    for _ in range(5):
        _settle(rt, _drawn(rt, state, arm), SettleStatus.SETTLED, 0.5)
        _settle(rt, _drawn(rt, state, NOOP), SettleStatus.INAPPLICABLE)
    rt._deliver_returns()
    weights = _weights(state)
    assert weights[NOOP] == pytest.approx(weights[arm])


def test_an_unscored_round_is_credited_zero_consequence_never_the_arms_own_mean():
    """Time audit T4: a population compensated only for long-run averages ceases to
    produce variation (essay II.IV.b), so a round with no observed score is credited the
    router's observed mean raw score (the population's, wave 16 D4), never the arm's own
    mean."""
    rt = make_runtime()
    state, _lid = _router(rt)
    a, b = [arm for arm in state.universe if arm != NOOP][:2]
    _settle(rt, _drawn(rt, state, a), SettleStatus.SETTLED, 0.9)
    _settle(rt, _drawn(rt, state, b), SettleStatus.SETTLED, 0.1)
    _settle(rt, _drawn(rt, state, b), SettleStatus.CENSORED)
    rt._deliver_returns()
    after_b = _weights(state)
    # b's censored round is credited the router's observed mean ((0.9 + 0.1) / 2 = 0.5),
    # not b's own mean (0.1): b gained 0.1 + 0.5, a one increment of 0.9, at equal odds,
    # each learned once on the router's one map, B = 2 * cap (ruling R10-l).
    bound = 2 * rt.m.prices.penalty_cap

    def learned(r):
        return (r + bound) / (1 + bound)

    assert after_b[b] - min(after_b.values()) == pytest.approx(
        (learned(0.1) + learned(0.5)) * (after_b[a] - min(after_b.values())) / learned(0.9))


def test_a_parent_selected_child_never_trains_the_router():
    """Defect 3. A child request carries a propensity of 1.0 its parent chose: the router
    never sampled that round, so it learns nothing from it."""
    rt = make_runtime()
    state, _lid = _router(rt)
    arm = next(a for a in state.universe if a != NOOP)
    parent = _drawn(rt, state, arm)
    lid = state.learner.id
    child = rt.queue.open(actor=lid, event_id=f"child-{parent}",
                          propensity=PropensityRecord((arm,), (1.0,), arm, 0, lid,
                                                      "parent-selected"),
                          channel=CH_CONFORMITY, deadline_ns=10**15, parent_handle=parent,
                          cost_ceiling=0)
    before = _weights(state)
    _settle(rt, child, SettleStatus.SETTLED, 1.0)
    rt._deliver_returns()
    assert _weights(state) == before


def test_a_late_score_after_the_cutoff_trains_nothing_twice():
    """Defect 4. A decision that timed out is learned once, at its cutoff, from the
    neutral evidence the router has; the score that arrives later still settles the
    decision for the kernel (its money, its standing) but trains no router again."""
    rt = make_runtime()
    state, lid = _router(rt)
    a, b = [arm for arm in state.universe if arm != NOOP][:2]
    _settle(rt, _drawn(rt, state, a), SettleStatus.SETTLED, 0.4)
    late = _drawn(rt, state, b)
    rt.queue.expire(10**16)  # the cutoff passes with b's decision unscored
    rt._deliver_returns()
    at_cutoff = _weights(state)
    floor = min(at_cutoff.values())
    # One update for b at the router's observed mean (a's 0.4, the one round it learned),
    # never a zero (wave 16, D4).
    assert at_cutoff[b] == pytest.approx(at_cutoff[a]) and at_cutoff[b] > floor
    _settle(rt, late, SettleStatus.SETTLED, 1.0)
    assert [str(r.status) for r in rt.queue.history(late)] == ["timed_out", "settled"]
    rt._deliver_returns()
    assert _weights(state) == at_cutoff


def test_a_keyed_router_learns_a_timed_out_round_once(monkeypatch):
    """Defect 4 for Blum-Mansour: the late score found its frozen round already consumed
    and was dropped; now the round is consumed once, at the cutoff, on the router's
    observed mean raw score (wave 16, D4; time audit T4)."""
    rt = make_runtime()
    state = rt._build_router("ProducerReturn", "blum_mansour", 0.1)
    arm = next(a for a in state.universe if a != NOOP)
    updates = []
    inner = state.learner.inner
    monkeypatch.setattr(inner, "update_for", lambda key, fb: updates.append((key, fb)))
    first = _drawn(rt, state, arm)
    rt.snapshot_keys[first] = "k-first"
    late = _drawn(rt, state, arm)
    rt.snapshot_keys[late] = "k-late"
    _settle(rt, first, SettleStatus.SETTLED, 0.7)
    rt.queue.expire(10**16)
    rt._deliver_returns()
    _settle(rt, late, SettleStatus.SETTLED, 0.1)
    rt._deliver_returns()
    bound = 2 * rt.m.prices.penalty_cap
    # One map, once, uncharged (ruling R10-l): B = 2 * cap for a router.
    learned = (0.7 + bound) / (1 + bound)
    assert [(key, fb.reward) for key, fb in updates] == [
        ("k-first", pytest.approx(learned)), ("k-late", pytest.approx(learned))]
