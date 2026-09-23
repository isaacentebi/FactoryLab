"""Routers learn from what waking a seat was worth (essay II.a, the frontier and the core).

An abstention is worth zero consequence (what a seat that delivered nothing scores on the
router's own scales: 0.5 for producer outcomes, 0.75 for Brier), never the average the
seats earned, so a seat is woken more only by beating it. A manifest can seed its
retentive core with a no-swap-regret (Blum-Mansour) router, and a router that is replaced
hands every round it still owes a reward for to the router that replaced it.
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
                   ledger_path=None, router_gamma=.1,
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
    mean delay the router's seat rounds took, and a checkpoint keeps what is owed. The
    delay is counted in world ticks (time audit T3): wall time alone moves nothing."""
    rt = make_runtime()
    state, _lid = _router(rt)
    arm = next(a for a in state.universe if a != NOOP)
    delay = 5
    seat = _drawn(rt, state, arm)
    rt.ticks_consumed += delay
    rt.clock.now_ns += 10**15  # a stalled loop: an hour of wall time is no tick
    _settle(rt, seat, SettleStatus.SETTLED, 0.9)
    rt._deliver_returns()
    assert state.latency == [delay, 1]
    assert rt.clockwork.latencies[f"router:{state.kind}"] == [delay]
    abstain = _drawn(rt, state, NOOP)
    _settle(rt, abstain, SettleStatus.INAPPLICABLE)
    rt._deliver_returns()
    owed = _weights(state)
    assert rt.noop_credits[abstain]["due_tick"] == rt.ticks_consumed + delay
    restored = make_runtime()
    restore_runtime(restored, runtime_state(rt))
    live, _ = _router(restored)
    assert _weights(live) == owed and live.latency == [delay, 1]
    restored.ticks_consumed += delay - 1
    restored.clock.now_ns += 10**15
    restored._deliver_returns()
    assert _weights(live) == owed  # not yet due
    restored.ticks_consumed += 1
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


# --- what nothing delivered is worth, per score definition ------------------------------

# Every score definition a router's seat round can settle with a score, and what a seat
# that delivered nothing scores on it.
_ZERO = {
    "verdict-v1": 0.5, "evaluation-v1": 0.5, "policy-promise-brier-v2": 0.5,
    "brier-v1": 0.75, "forecast-mean-v1": 0.75, "exposure-v1": 0.5,
    # W4: a composed return settles on its verdict and its requester's score.
    "composed-v1": 0.5,
}


@pytest.mark.parametrize("definition", sorted(_ZERO))
def test_each_score_definition_has_its_own_zero_consequence(definition):
    from factorylab.runtime.routing import ZERO_CONSEQUENCE, zero_consequence

    assert zero_consequence(definition) == ZERO_CONSEQUENCE[definition] == _ZERO[definition]


@pytest.mark.parametrize("definition", ["brier-v1", "forecast-mean-v1"])
def test_a_brier_scale_prices_nothing_at_the_coin_flip_forecasters_score(definition):
    """A coin-flip forecast scores 0.75 whatever happens: on a Brier router NOOP at 0.5
    lost to a seat that knew nothing, a dead arm the router paid to avoid every time."""
    from factorylab.runtime.routing import zero_consequence
    from factorylab.settlement.scoring import brier

    assert zero_consequence(definition) == brier(0.5, 0) == brier(0.5, 1)


def test_the_table_names_every_scored_definition_the_runtime_settles_with():
    from factorylab.runtime import shared
    from factorylab.runtime.routing import ZERO_CONSEQUENCE

    scored = {shared.DEF_VERDICT, shared.DEF_EVALUATION, shared.DEF_EXPOSURE,
              shared.DEF_COMPOSED}
    assert scored <= set(ZERO_CONSEQUENCE) and set(ZERO_CONSEQUENCE) == set(_ZERO)


def test_an_unknown_definition_and_an_unlearned_router_are_worth_the_midpoint():
    from factorylab.runtime.routing import zero_consequence

    assert zero_consequence("test-v1") == NEUTRAL_REWARD
    rt = make_runtime()
    state, _lid = _router(rt)
    assert state.neutral() == NEUTRAL_REWARD


def _scored(rt, handle, score, definition):
    rt.queue.settle(handle, channel=rt.queue.get(handle).channel, score=score,
                    status=SettleStatus.SETTLED, definition_version=definition,
                    sampling_ref=None)


def test_on_a_brier_router_a_know_nothing_seat_ties_an_abstention():
    """Before: a coin-flip seat earned 0.75 and NOOP 0.5, so NOOP lost 0.25 a round to a
    seat that knew nothing. NOOP is credited the Brier zero now, and the two tie."""
    rt = make_runtime()
    state, _lid = _router(rt)
    arm = next(a for a in state.universe if a != NOOP)
    _scored(rt, _drawn(rt, state, arm), 0.75, "forecast-mean-v1")
    rt._deliver_returns()
    assert state.neutral() == 0.75 and state.definitions == {"forecast-mean-v1": 1}
    _settle(rt, _drawn(rt, state, NOOP), SettleStatus.INAPPLICABLE)
    rt._deliver_returns()
    weights = _weights(state)
    assert weights[NOOP] == pytest.approx(weights[arm])


def test_a_mixed_router_prices_nothing_at_its_own_mix_of_scales():
    rt = make_runtime()
    state, _lid = _router(rt)
    arm = next(a for a in state.universe if a != NOOP)
    for definition in ("forecast-mean-v1", "forecast-mean-v1", "verdict-v1", "exposure-v1"):
        _scored(rt, _drawn(rt, state, arm), 0.6, definition)
    rt._deliver_returns()
    assert state.neutral() == pytest.approx((0.75 * 2 + 0.5 + 0.5) / 4)


def test_an_unscored_seat_without_a_record_is_imputed_the_routers_zero():
    rt = make_runtime()
    state, _lid = _router(rt)
    a, b = [arm for arm in state.universe if arm != NOOP][:2]
    _scored(rt, _drawn(rt, state, a), 0.75, "forecast-mean-v1")
    _settle(rt, _drawn(rt, state, b), SettleStatus.CENSORED)
    rt._deliver_returns()
    weights = _weights(state)
    assert weights[b] == pytest.approx(weights[a])  # both credited 0.75 at equal odds
    assert b not in state.observed.state()


def test_the_scales_a_router_learned_survive_a_resume_and_default_when_absent():
    from factorylab.runtime.routing import RouterState

    rt = make_runtime()
    state, _lid = _router(rt)
    arm = next(a for a in state.universe if a != NOOP)
    _scored(rt, _drawn(rt, state, arm), 0.9, "forecast-mean-v1")
    rt._deliver_returns()
    restored = make_runtime()
    restore_runtime(restored, runtime_state(rt))
    assert _router(restored)[0].definitions == {"forecast-mean-v1": 1}
    old = state.state()
    del old["definitions"]
    assert RouterState.restore(old).definitions == {}
    assert "definitions" not in _router(make_runtime())[0].state()


def test_a_round_that_trains_nothing_books_no_delay_baseline_or_scale():
    """A swap router's round whose frozen snapshot is gone trains nothing; it moved the
    abstention due time and the seat baseline all the same before the fix."""
    rt = _core_runtime()
    state = rt.routers["ProducerReturn"][0]
    arm = next(a for a in state.universe if a != NOOP)
    handle = _drawn(rt, state, arm)  # no snapshot key: nothing to train
    rt.clock.now_ns += 5_000_000_000
    _scored(rt, handle, 0.9, "forecast-mean-v1")
    before = state.learner.inner.inner.state()
    rt._deliver_returns()
    assert state.learner.inner.inner.state() == before
    assert state.latency == [0, 0] and not state.observed.state() and not state.definitions


def test_an_owed_abstention_whose_router_is_gone_is_ledgered_not_dropped():
    rt = make_runtime()
    state, _lid = _router(rt)
    handle = _drawn(rt, state, NOOP)
    rt.noop_credits[handle] = {"router": "router:gone", "due_ns": 0, "p": None,
                               "executed": None}
    rt._credit_abstentions()
    assert not rt.noop_credits
    assert any(i["kind"] == "propensity.unlearned" and i["handle"] == handle
               and i["learner_id"] == "router:gone"
               for i in rt.ledger._recovery_items())


def test_a_plain_router_credits_an_abstention_once_whatever_returns_repeat():
    """A keyed router spends its snapshot on the first return; a plain EXP3 router had no
    such guard, so a later return for the same NOOP (a timeout, then a final outcome the
    cutoff rule does not cover) was credited again."""
    rt = make_runtime()
    state, _lid = _router(rt)
    lid = state.learner.id
    arms = tuple(state.universe)
    probs = tuple(1 / len(arms) for _ in arms)
    probs = (*probs[:-1], 1 - sum(probs[:-1]))
    seed = next(s for s in range(10_000)
                if random.Random(s).choices(arms, weights=probs, k=1)[0] == NOOP)
    handle = rt.queue.open(actor=lid, event_id="noop-twice",
                           propensity=PropensityRecord(arms, probs, NOOP, seed, lid, "d"),
                           channel="test", deadline_ns=rt.clock.now_ns + 1, parent_handle=None,
                           cost_ceiling=0)
    rt.clock.now_ns += 1
    rt.ticks_consumed += 1  # the cutoff is a tick (time audit T3)
    assert rt.queue.expire_due() == [handle]
    rt._deliver_returns()
    once = _weights(state)
    assert once[NOOP] > min(once.values())  # credited at its deadline
    rt.queue.settle(handle, channel="test", score=0.0, status=SettleStatus.INAPPLICABLE,
                    definition_version="realized-consequence-v2-x", sampling_ref=None)
    rt._deliver_returns()
    assert _weights(state) == once
    lr = rt.queue.returns_for(lid)[-1]
    rt._learn_router_return(state, lr)  # the same return delivered twice
    rt._credit_abstentions()
    assert _weights(state) == once and not rt.noop_credits


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


def test_the_core_key_is_hashed_absent_or_present():
    base = load_manifest("scripted")
    assert '"no_swap_regret_kinds":[]' in base.canonical_json()
    core = replace(base, evaluation=replace(base.evaluation,
                                            no_swap_regret_kinds=("ProducerReturn",)))
    assert "no_swap_regret_kinds" in core.canonical_json()
    assert core.manifest_hash() != base.manifest_hash()


@pytest.mark.parametrize("bad", [("ProducerReturn", "ProducerReturn"), ("",), (3,)])
def test_the_core_key_rejects_a_malformed_list(bad):
    base = load_manifest("scripted")
    with pytest.raises(ValueError, match="no_swap_regret_kinds"):
        replace(base, evaluation=replace(base.evaluation, no_swap_regret_kinds=bad)).validate()


@pytest.mark.parametrize("typo", [("ProducerRetrun",), ("ProducerReturn", "producerreturn")])
def test_the_core_key_refuses_a_kind_the_world_cannot_route(typo):
    """A misspelt kind seeded no router at all, leaving the core silently empty."""
    base = load_manifest("scripted")
    with pytest.raises(ValueError, match="no_swap_regret_kinds names no event kind"):
        replace(base, evaluation=replace(base.evaluation, no_swap_regret_kinds=typo)).validate()


def test_the_core_key_admits_every_kind_a_seat_accepts_or_emits():
    base = load_manifest("scripted")
    kinds = tuple(sorted({k for a in base.assemblies for k in (*a.accepts, *a.emits)}))
    replace(base, evaluation=replace(base.evaluation, no_swap_regret_kinds=kinds)).validate()


def test_the_core_key_is_a_set_its_order_never_renames_the_world():
    from factorylab.runtime.worlds import _manifest_kinds

    base = load_manifest("scripted")
    one, two = ("ProducerReturn", "Verdict"), ("Verdict", "ProducerReturn")
    worlds = [replace(base, evaluation=replace(base.evaluation, no_swap_regret_kinds=k))
              for k in (one, two)]
    assert worlds[0].manifest_hash() == worlds[1].manifest_hash()
    assert _manifest_kinds(list(two)) == one


def test_the_core_key_must_be_a_list():
    from factorylab.runtime.worlds import _manifest_kinds

    with pytest.raises(ValueError, match="no_swap_regret_kinds"):
        _manifest_kinds("ProducerReturn")
    assert manifest_from_dict  # the parser this helper serves


@pytest.mark.parametrize("name", ["edition5-testnet-rehearsal", "edition5-capital-loop"])
def test_the_judge_tier_is_mean_based_in_the_edition5_worlds(name):
    """Ruling R10: II.III asks for "a significantly higher population of mean-based
    no-regret judges than ... swap-based judges", so judge routing is EXP3."""
    world = load_manifest(name)
    assert "ProducerReturn" not in world.evaluation.no_swap_regret_kinds
    # The world runs on a live venue; its evaluation cast is seeded into the scripted one.
    scripted = replace(load_manifest("scripted"), evaluation=world.evaluation)
    rt = Runtime(scripted, events=0, seed=1, initial_balance_micro=None, ledger_path=None,
                 router_gamma=0.1)
    assert all(isinstance(state.learner, EXP3) for state in rt.routers["ProducerReturn"])


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


# --- a round drawn over a larger universe moves a weight by at most one ----------------


def test_a_shrunk_router_steps_a_carried_round_at_the_drawers_size_and_ledgers_it():
    """Drawn at the floor of a five-arm router, X = 1/0.02 = 50; stepped at gamma/N_new =
    0.05 by a two-arm successor, one round moved a log weight by 2.5. It is stepped at
    the drawer's gamma/N_old now, at most one."""
    rt = make_runtime()
    old = rt.routers["ProducerReturn"][0]
    arms = tuple(old.universe)
    kept = arms[0]
    floor = 0.1 / len(arms)  # gamma / N_old: the least the predecessor ever drew an arm
    probs = tuple(floor if a == kept else (1 - floor) / (len(arms) - 1) for a in arms)
    seed = next(s for s in range(100_000)
                if random.Random(s).choices(arms, weights=probs, k=1)[0] == kept)
    handle = rt.queue.open(
        actor=old.learner.id, event_id="floor",
        propensity=PropensityRecord(arms, probs, kept, seed, old.learner.id, "s"),
        channel="test", deadline_ns=10**15, parent_handle=None, cost_ceiling=0)
    rt._universe_for = lambda _kind, _ev=None: [kept, NOOP]
    rt._open_epoch("ProducerReturn")
    fresh = rt.routers["ProducerReturn"][0]
    before = fresh.learner.state()["log_weights"]
    rt.queue.settle(handle, channel="test", score=1.0, status=SettleStatus.SETTLED,
                    definition_version="verdict-v1", sampling_ref=None)
    rt._deliver_returns()
    after = fresh.learner.state()["log_weights"]
    assert (after[kept] - after[NOOP]) - (before[kept] - before[NOOP]) == pytest.approx(1.0)
    items = rt.ledger._recovery_items()
    assert any(i["kind"] == "router.step_rescaled" and i["handle"] == handle
               and i["learner_id"] == fresh.learner.id and i["drawn_universe"] == 5
               and i["learning_universe"] == 2 and i["stepped_as"] == pytest.approx(0.4)
               for i in items)
    assert any(i["kind"] == "router.carried" and i["handle"] == handle
               and i["reward"] == 1.0 for i in items)


def test_a_shrunk_swap_router_steps_every_row_at_the_drawers_size():
    rt = _core_runtime()
    old = rt.routers["ProducerReturn"][0]
    handle, chosen = _live_draw(rt, old)
    rt._universe_for = lambda _kind, _ev=None: [chosen, NOOP]
    rt._open_epoch("ProducerReturn")
    fresh = rt.routers["ProducerReturn"][0]
    rows = [dict(b.state()["log_weights"]) for b in fresh.learner.inner.inner._bases]
    rt.queue.settle(handle, channel="test", score=1.0, status=SettleStatus.SETTLED,
                    definition_version="forecast-mean-v1", sampling_ref=None)
    rt._deliver_returns()
    for base, row in zip(fresh.learner.inner.inner._bases, rows, strict=True):
        now = base.state()["log_weights"]
        assert (now[chosen] - now[NOOP]) - (row[chosen] - row[NOOP]) <= 1.0
    assert any(i["kind"] == "router.step_rescaled" and i["handle"] == handle
               for i in rt.ledger._recovery_items())


def test_a_round_learned_over_the_same_universe_is_not_rescaled():
    rt = make_runtime()
    state, _lid = _router(rt)
    arm = next(a for a in state.universe if a != NOOP)
    _scored(rt, _drawn(rt, state, arm), 1.0, "verdict-v1")
    rt._deliver_returns()
    assert not any(i["kind"] == "router.step_rescaled" for i in rt.ledger._recovery_items())


# --- learning death, observed ----------------------------------------------------------


def _draw_at(state, p_noop):
    from factorylab.learners.router import Sample

    seats = [a for a in state.universe if a != NOOP]
    rest = (1 - p_noop) / len(seats)
    return Sample((*seats, NOOP), (*(rest for _ in seats), p_noop), NOOP, 1,
                  state.learner.id, "h", ())


def test_a_window_whose_every_draw_parked_at_noop_is_the_frontier_signal():
    """Ruling R9 (versioning U1, time T16): the router's own watch is the frontier
    signal of the one learning-death diagnosis; no separate ledger kind exists."""
    rt = make_runtime()
    state, lid = _router(rt)
    window = rt.window.index
    for p in (0.95, 0.92, 0.97):
        rt._watch_abstention(state, _draw_at(state, p))
    assert state.watch == {"window": window, "draws": 3, "min_p": 0.92}
    (row,) = [r for r in rt.frontier_invocation() if r["router"] == lid]
    assert row["uninvoked"] and row["draws"] == 3 and row["min_p_noop"] == 0.92
    assert row["floor"] == pytest.approx(0.9)
    rt.window.index += 1
    rt._deliver_returns()
    assert not state.watch  # a closed window's watch is dropped, nothing ledgered
    assert not [i for i in rt.ledger._recovery_items() if i["kind"] == "router.learning_death"]
    assert not [r for r in rt.frontier_invocation() if r["router"] == lid]


def test_one_draw_that_woke_a_seat_in_earnest_keeps_the_frontier_invoked():
    rt = make_runtime()
    state, lid = _router(rt)
    for p in (0.95, 0.5, 0.97):
        rt._watch_abstention(state, _draw_at(state, p))
    (row,) = [r for r in rt.frontier_invocation() if r["router"] == lid]
    assert not row["uninvoked"]
    rt.window.index += 1
    rt._watch_abstention(state, _draw_at(state, 0.99))  # a new window's draw starts anew
    assert state.watch == {"window": rt.window.index, "draws": 1, "min_p": 0.99}


def test_the_immune_window_records_which_routers_left_their_frontier_uninvoked():
    from factorylab.versioning.versions import frontier_evidence

    rows = [{"router": "router:A", "uninvoked": True}, {"router": "router:B",
                                                         "uninvoked": False}]
    window = {"profile": {"registrations": 0, "revision": 0}, "regions": {},
              "frontier_invocation": rows}
    evidence = frontier_evidence([window, window])
    assert evidence["uninvoked_routers"] == ["router:A"]
    # An older window without the signal leaves the evidence as it was.
    assert "uninvoked_routers" not in frontier_evidence([{**window, "frontier_invocation": rows},
                                                         {"profile": window["profile"],
                                                          "regions": {}}])


def test_the_watch_is_observation_only_and_survives_a_resume():
    from factorylab.runtime.routing import RouterState

    rt = make_runtime()
    state, _lid = _router(rt)
    before = state.learner.state()
    rt._watch_abstention(state, _draw_at(state, 0.95))
    assert state.learner.state() == before and state.neutral() == NEUTRAL_REWARD
    restored = make_runtime()
    restore_runtime(restored, runtime_state(rt))
    assert _router(restored)[0].watch == state.watch
    old = state.state()
    del old["watch"]
    assert RouterState.restore(old).watch == {}
    assert "watch" not in _router(make_runtime())[0].state()
