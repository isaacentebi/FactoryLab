"""Routers learn from what waking a seat was worth (essay II.a, the frontier and the core).

An abstention is worth zero consequence (0.5), never the average the seats earned, so a
seat is woken more only by beating it. A manifest can seed its retentive core with a
no-swap-regret (Blum-Mansour) router, and a router that is replaced hands every round it
still owes a reward for to the router that replaced it.
"""

import random
from dataclasses import replace

import pytest

from factorylab.kernel.queue import PropensityRecord, SettleStatus
from factorylab.learners.base import NEUTRAL_REWARD, ObservedRewards
from factorylab.learners.blum_mansour import BlumMansour
from factorylab.learners.exp3 import EXP3
from factorylab.runtime.loop import Runtime
from factorylab.runtime.resume import restore_runtime, runtime_state
from factorylab.runtime.routing import _KeyedLearner
from factorylab.runtime.shared import NOOP
from factorylab.runtime.worlds import load_manifest, manifest_from_dict
from factorylab.world.exchange import FakeExchange
from factorylab.world.scripted import ScriptedProvider
from tests.conftest import make_runtime
from tests.runtime.test_learning_signal import _drawn, _router, _settle, _weights


def _core_runtime(kinds=("ProducerReturn",)):
    manifest = load_manifest("scripted")
    manifest = replace(manifest, evaluation=replace(manifest.evaluation,
                                                    no_swap_regret_kinds=tuple(kinds)))
    return Runtime(manifest, events=0, seed=1, initial_balance_micro=100_000_000,
                   ledger_path=None, drip=False, router_gamma=.1,
                   exchange=FakeExchange(), provider=ScriptedProvider())


# --- NOOP is worth zero consequence ------------------------------------------------------


def test_an_abstention_is_credited_the_neutral_reward_not_the_pooled_mean():
    """The free-average defect: NOOP was credited the mean of the arms that were scored,
    so doing nothing always looked exactly average. It is credited 0.5 now."""
    rt = make_runtime()
    state, _lid = _router(rt)
    arm = next(a for a in state.universe if a != NOOP)
    _settle(rt, _drawn(rt, state, arm), SettleStatus.SETTLED, 0.9)
    _settle(rt, _drawn(rt, state, NOOP), SettleStatus.INAPPLICABLE)
    rt._deliver_returns()
    weights = _weights(state)
    floor = min(weights.values())
    # Same odds for both draws, so the increments are in the ratio of the rewards.
    assert weights[NOOP] - floor == pytest.approx((weights[arm] - floor) * 0.5 / 0.9)
    assert NOOP not in state.observed.state()  # a fixed credit is no observation


def test_an_abstention_is_credited_on_the_seats_clock_through_a_resume():
    """NOOP settles at once while a seat's score takes the feedback delay; crediting NOOP
    at once would keep it a whole delay ahead of every seat. Its credit waits for the
    mean delay the router's seat rounds took, and a checkpoint keeps what is owed."""
    rt = make_runtime()
    state, _lid = _router(rt)
    arm = next(a for a in state.universe if a != NOOP)
    delay = 5_000_000_000
    seat = _drawn(rt, state, arm)
    rt.clock.now_ns += delay
    _settle(rt, seat, SettleStatus.SETTLED, 0.9)
    rt._deliver_returns()
    assert state.latency == [delay, 1]
    abstain = _drawn(rt, state, NOOP)
    _settle(rt, abstain, SettleStatus.INAPPLICABLE)
    rt._deliver_returns()
    owed = _weights(state)
    assert rt.noop_credits[abstain]["due_ns"] == rt.clock.now_ns + delay
    restored = make_runtime()
    restore_runtime(restored, runtime_state(rt))
    live, _ = _router(restored)
    assert _weights(live) == owed and live.latency == [delay, 1]
    restored.clock.now_ns += delay - 1
    restored._deliver_returns()
    assert _weights(live) == owed  # not yet due
    restored.clock.now_ns += 1
    restored._deliver_returns()
    weights = _weights(live)
    floor = min(weights.values())
    assert weights[NOOP] - floor == pytest.approx((weights[arm] - floor) * 0.5 / 0.9)
    assert not restored.noop_credits
    restored._deliver_returns()
    assert _weights(live) == weights  # credited once


def test_an_unscored_arm_without_its_own_record_is_neutral_not_its_siblings_mean():
    observed = ObservedRewards()
    observed.record("a", 0.9)
    assert observed.neutral("a") == 0.9
    assert observed.neutral("b") == NEUTRAL_REWARD
    assert ObservedRewards().neutral("a") == NEUTRAL_REWARD


def _one_seat_router(rt, seat):
    rt._universe_for = lambda _kind, _ev=None: [seat, NOOP]
    return rt._build_router("ProducerReturn", "exp3", 0.1)


@pytest.mark.parametrize("score, wakes_more", [(0.8, True), (0.2, False)])
def test_a_one_seat_router_learns_whether_its_seat_beats_doing_nothing(score, wakes_more):
    """With one seat and NOOP the router had no signal at all: NOOP was credited the
    seat's own mean. Now the seat is woken more exactly when it scores above 0.5."""
    rt = make_runtime()
    seat = next(a for a in rt.routers["ProducerReturn"][0].universe if a != NOOP)
    state = _one_seat_router(rt, seat)
    rng = random.Random(7)
    for i in range(200):
        dist = state.learner.distribution(state.universe)
        arms = tuple(state.universe)
        probs = tuple(dist[a] for a in arms)
        seed = rng.getrandbits(64)
        chosen = random.Random(seed).choices(arms, weights=probs, k=1)[0]
        handle = rt.queue.open(
            actor=state.learner.id, event_id=f"one-seat-{i}",
            propensity=PropensityRecord(arms, probs, chosen, seed, state.learner.id, "s"),
            channel="test", deadline_ns=10**15, parent_handle=None, cost_ceiling=0)
        if chosen == NOOP:
            _settle(rt, handle, SettleStatus.INAPPLICABLE)
        else:
            _settle(rt, handle, SettleStatus.SETTLED, score)
        rt._deliver_returns()
    p_seat = state.learner.distribution(state.universe)[seat]
    assert (p_seat > 0.7) if wakes_more else (p_seat < 0.3)


# --- the retentive core ------------------------------------------------------------------


def test_the_manifest_seeds_a_swap_regret_core_only_for_the_kinds_it_names():
    core = _core_runtime()
    assert isinstance(core.routers["ProducerReturn"][0].learner, _KeyedLearner)
    assert all(isinstance(st.learner, EXP3)
               for kind, states in core.routers.items() if kind != "ProducerReturn"
               for st in states)
    created = [i for i in core.ledger._recovery_items() if i["kind"] == "router.created"]
    assert [i.get("learner") for i in created
            if i["event_kind"] == "ProducerReturn"] == ["blum_mansour"]
    plain = make_runtime()
    assert all(isinstance(st.learner, EXP3) for st in plain._all_router_states())
    assert not any("learner" in i for i in plain.ledger._recovery_items()
                   if i["kind"] == "router.created")


def test_a_kind_that_first_gains_an_acceptor_later_is_seeded_in_the_core():
    rt = _core_runtime(kinds=("ProducerReturn", "Later"))
    rt._universe_for = lambda _kind, _ev=None: ["late-judge", NOOP]
    rt._open_epoch("Later")
    assert isinstance(rt.routers["Later"][0].learner, _KeyedLearner)


def test_the_core_key_is_hash_neutral_when_absent_and_named_when_present():
    base = load_manifest("scripted")
    assert "no_swap_regret_kinds" not in base.canonical_json()
    core = replace(base, evaluation=replace(base.evaluation,
                                            no_swap_regret_kinds=("ProducerReturn",)))
    assert "no_swap_regret_kinds" in core.canonical_json()
    assert core.manifest_hash() != base.manifest_hash()


@pytest.mark.parametrize("bad", [("ProducerReturn", "ProducerReturn"), ("",), (3,)])
def test_the_core_key_rejects_a_malformed_list(bad):
    base = load_manifest("scripted")
    with pytest.raises(ValueError, match="no_swap_regret_kinds"):
        replace(base, evaluation=replace(base.evaluation, no_swap_regret_kinds=bad)).validate()


def test_the_core_key_must_be_a_list():
    from factorylab.runtime.worlds import _manifest_kinds

    with pytest.raises(ValueError, match="no_swap_regret_kinds"):
        _manifest_kinds("ProducerReturn")
    assert manifest_from_dict  # the parser this helper serves


def test_the_rehearsal_world_puts_judge_routing_in_the_core():
    world = load_manifest("edition5-testnet-rehearsal")
    assert world.evaluation.no_swap_regret_kinds == ("ProducerReturn",)


# --- a replaced router hands its owed rounds to its successor ----------------------------


def _live_draw(rt, state, ev_id="p"):
    state.learner.current_key = ev_id
    sample = state.router.route(state.kind, lambda _: (True, ""), rt.rng,
                                mix=rt._mix_with_standing)
    handle = rt.queue.open(actor=state.learner.id, event_id=ev_id,
                           propensity=rt._propensity(sample), channel="test",
                           deadline_ns=10**15, parent_handle=None, cost_ceiling=0)
    rt.snapshot_keys[handle] = ev_id
    return handle, sample.chosen


def test_a_pending_core_round_survives_replacement_and_resume_into_the_successor():
    rt = _core_runtime()
    old = rt.routers["ProducerReturn"][0]
    handle, chosen = _live_draw(rt, old)
    grown = [*old.universe[:-1], "new-judge", NOOP]
    rt._universe_for = lambda _kind, _ev=None: grown
    rt._open_epoch("ProducerReturn")
    fresh = rt.routers["ProducerReturn"][0]
    assert fresh.learner.id != old.learner.id and old.successor == fresh.learner.id
    restored = _core_runtime()
    restore_runtime(restored, runtime_state(rt))
    retired = restored.retired_routers[old.learner.id]
    live = restored.routers["ProducerReturn"][0]
    assert retired.successor == live.learner.id
    retired_before = retired.learner.inner.inner.state()
    live_before = live.learner.inner.inner.state()
    restored.queue.settle(handle, channel="test", score=0.9, status=SettleStatus.SETTLED,
                          definition_version="1", sampling_ref=None)
    restored._deliver_returns()
    assert live.learner.inner.inner.state() != live_before
    assert retired.learner.inner.inner.state() == retired_before
    assert not retired.learner.inner.state()["snapshots"]
    assert live.observed.state()[chosen] == [0.9, 1]
    assert old.learner.id not in restored.retired_routers  # drained once learned


def test_a_hand_over_follows_a_chain_of_replacements():
    rt = make_runtime()
    first = rt.routers["Tick"][0]
    action = next(a for a in first.universe if a != NOOP)
    handle = rt.queue.open(
        actor=first.learner.id, event_id="chain",
        propensity=PropensityRecord((action,), (1.0,), action, 1, first.learner.id, "s"),
        channel="test", deadline_ns=10**15, parent_handle=None, cost_ceiling=0)
    second = rt._build_router("Tick", "exp3", .1)
    rt.retired_routers.pop(second.learner.id, None)
    third = rt._build_router("Tick", "exp3", .1)
    assert first.successor == third.learner.id
    before = third.learner.state()
    rt.queue.settle(handle, channel="test", score=1.0, status=SettleStatus.SETTLED,
                    definition_version="1", sampling_ref=None)
    rt._deliver_returns()
    assert third.learner.state() != before


def test_an_arm_the_successor_no_longer_holds_trains_nothing_and_says_so():
    rt = make_runtime()
    old = rt.routers["Tick"][0]
    gone = next(a for a in old.universe if a != NOOP)
    handle = rt.queue.open(
        actor=old.learner.id, event_id="gone",
        propensity=PropensityRecord((gone,), (1.0,), gone, 1, old.learner.id, "s"),
        channel="test", deadline_ns=10**15, parent_handle=None, cost_ceiling=0)
    rt._universe_for = lambda _kind, _ev=None: [a for a in old.universe if a != gone]
    rt._open_epoch("Tick")
    fresh = rt.routers["Tick"][0]
    before = fresh.learner.state()
    rt.queue.settle(handle, channel="test", score=1.0, status=SettleStatus.SETTLED,
                    definition_version="1", sampling_ref=None)
    rt._deliver_returns()
    assert fresh.learner.state() == before
    assert any(i["kind"] == "propensity.unlearned" and i["handle"] == handle
               for i in rt.ledger._recovery_items())


# --- the swap learner keeps what it learned across a roster change -----------------------


def test_a_reshaped_swap_learner_keeps_surviving_weights():
    learner = BlumMansour(lambda a: EXP3(a, .2), ("a", "b", NOOP), id="bm")
    for base in learner._bases:
        base._log_weights = {"a": 0.0, "b": -1.0, NOOP: -2.0}
    grown = learner.reshaped(("a", "b", "c", NOOP), id="bm@1")
    for action, base in zip(grown.actions, grown._bases, strict=True):
        weights = base.state()["log_weights"]
        assert weights["b"] - weights["a"] == pytest.approx(-1.0), action
        assert weights["c"] - weights["a"] == pytest.approx(-1.0), action  # at the mean
        assert base.gamma == .2


def test_a_carried_round_rejects_an_arm_outside_the_universe_or_a_wrong_propensity():
    from factorylab.learners.base import BanditFeedback

    learner = BlumMansour(lambda a: EXP3(a, .1), ("a", NOOP), id="bm")
    before = learner.state()
    executed = {"a": 0.5, "gone": 0.5}
    with pytest.raises(ValueError):
        learner.update_carried(executed, executed, BanditFeedback("gone", 1.0, 0.5))
    with pytest.raises(ValueError):
        learner.update_carried(executed, executed, BanditFeedback("a", 1.0, 0.25))
    assert learner.state() == before
    learner.update_carried(executed, executed, BanditFeedback("a", 1.0, 0.5))
    assert learner.state() != before
