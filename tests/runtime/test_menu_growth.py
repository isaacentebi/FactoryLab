"""A router's menu grows in place; only a replacement or a raised core kappa opens a phase.

Learners design §2.5 (revision 4): a registration that adds an arm to a router's menu
grows the live learner (Mourtada & Maillard 2017 for the full-information step; the
bandit step is ours), so a founded seat is drawable on the next event of its kind,
inside its novelty trial, and nothing in flight is orphaned. A population router
replacement, or a seat that would raise a core menu's coverage bound kappa, opens a
phase and waits the kind's settle gate. Sol's cold review of #189/#190, P1 1-5.
"""

from dataclasses import replace
from fractions import Fraction
from random import Random

import pytest

from factorylab.cortex.registration import AssemblyProposal, RouterProposal
from factorylab.cortex.request import Return
from factorylab.kernel.events import Event, EventKind
from factorylab.kernel.queue import PropensityRecord, SettleStatus
from factorylab.learners.blum_mansour import BlumMansour
from factorylab.runtime.clockwork import deadline_ticks, delivery_ticks
from factorylab.runtime.loop import Runtime
from factorylab.runtime.resume import restore_runtime, runtime_state
from factorylab.runtime.shared import CH_CONSEQUENCE, CH_EXPOSURE, CH_FAST, NOOP
from factorylab.runtime.worlds import load_manifest
from factorylab.world.exchange import FakeExchange
from factorylab.world.scripted import ScriptedProvider
from tests.conftest import make_runtime


def _items(rt, kind):
    return [i for i in rt.ledger._recovery_items() if i["kind"] == kind]


def _core_tick_runtime():
    """The scripted world with ``Tick`` as its no-swap-regret core, as on edition 8: the
    core's menu holds the antagonist, so kappa is 1 / adversarial_share."""
    manifest = load_manifest("scripted")
    manifest = replace(manifest, evaluation=replace(manifest.evaluation,
                                                    no_swap_regret_kinds=("Tick",)))
    return Runtime(manifest, events=0, seed=1, initial_balance_micro=100_000_000,
                   ledger_path=None, exchange=FakeExchange(), provider=ScriptedProvider())


def _fund_registrations(rt):
    """A whole flow period's novelty share accrued, so a registration's trial is funded."""
    rt._manage_reserve_window()
    rt.clock.now_ns += 1
    rt.reserve.open_window(rt.clock.now_ns, max(0, rt.wallet.unlocked), accrued=Fraction(1))


def _found(rt, seat, accepts=("Tick",)):
    rt._register("proposal", AssemblyProposal(seat, "producer", "fake-haiku",
                                              "Reply with JSON.", accepts, 128, "low"))


def _tick(rt, label):
    rt.n += 1
    rt.ticks_consumed += 1
    return Event(label, EventKind.TICK, rt.clock.now_ns, {}, "test")


def _opened(rt):
    return [i for i in rt.ledger._recovery_items() if i["kind"] == "decision.open"]


def _round(rt, state, key):
    """A routed round on ``state``'s live learner, every seat feasible."""
    state.learner.current_key, state.learner.current_ordinal = key, rt.n
    sample = state.router.route(state.kind, lambda _a: (True, ""), Random(len(key)))
    handle = rt.queue.open(actor=state.learner.id, event_id=key,
                           propensity=rt._propensity(sample), channel="test",
                           horizon_ticks=20, parent_handle=None, cost_ceiling=0)
    rt.snapshot_keys[handle] = key
    return handle, sample


def _settle(rt, handle, score):
    """Settle a round as the world would: a woken seat's is scored, an abstention's is
    inapplicable and credited on its seats' clock."""
    if rt.queue.get(handle).propensity.chosen == NOOP:
        status, score = SettleStatus.INAPPLICABLE, 0.0
    else:
        status = SettleStatus.SETTLED
    rt.queue.settle(handle, channel="test", score=score, status=status,
                    definition_version="1", sampling_ref=None)


@pytest.mark.parametrize("core", [True, False])
def test_a_founded_seat_is_drawable_on_the_next_event_of_its_kind_inside_its_trial(core):
    """Sol P1 1: a seat founded just after another joined the same kind waited the whole
    settle gate (6 h 40 min on edition 8) while its novelty trial expired after an hour.
    Now each grows the live router in place and is offered on the very next Tick."""
    rt = _core_tick_runtime() if core else make_runtime()
    state = rt.routers["Tick"][0]
    assert state.learner.inner.core is core
    lid = state.learner.id
    _fund_registrations(rt)
    rt._route_with(state, _tick(rt, "warm"))
    for seat in ("founded-a", "founded-b"):
        _found(rt, seat)
        assert rt.routers["Tick"][0] is state and state.learner.id == lid  # no new phase
        assert seat in state.universe and seat in state.learner.inner.inner.actions
        rt._route_with(state, _tick(rt, f"after-{seat}"))
        draw = dict(zip(_opened(rt)[-1]["propensity"]["action_ids"],
                        _opened(rt)[-1]["propensity"]["probs"], strict=True))
        assert draw.get(seat, 0) > 0, draw
        assert rt._unhistoried(seat)  # inside its novelty trial
    assert not _items(rt, "epoch.deferred") and not rt.pending_epochs
    grown = _items(rt, "router.grown")
    assert [g["added"] for g in grown] == [["founded-a"], ["founded-b"]]
    assert all(g["learner_id"] == lid and isinstance(g["ordinal"], int) for g in grown)


@pytest.mark.parametrize("core", [True, False])
def test_rounds_in_flight_across_growth_train_through_a_resume(core):
    """Growth orphans nothing: a round drawn over the old menu, still outstanding when
    the menu grows and when the world is checkpointed, trains the same live router."""
    rt = _core_tick_runtime() if core else make_runtime()
    state = rt.routers["Tick"][0]
    before_growth, _ = _round(rt, state, "before")
    _fund_registrations(rt)
    _found(rt, "founded")
    after_growth, sample = _round(rt, state, "after")
    assert "founded" in sample.action_ids
    restored = make_runtime() if not core else _core_tick_runtime()
    restore_runtime(restored, runtime_state(rt))
    live = restored.routers["Tick"][0]
    assert live.learner.id == state.learner.id and "founded" in live.universe
    assert live.learner.inner.inner.actions == state.learner.inner.inner.actions
    for handle in (before_growth, after_growth):
        _settle(restored, handle, 0.8)
    restored._deliver_returns()
    restored.ticks_consumed += 30  # an abstention's credit is due on its seats' clock
    restored._deliver_returns()
    learned = {i["handle"] for i in _items(restored, "router.learned")}
    assert {before_growth, after_growth} <= learned
    assert not _items(restored, "learner.orphaned")
    assert live.learner.inner.outstanding() == []


def test_a_seat_that_raises_a_core_menus_kappa_opens_a_gated_phase():
    """A forecast-shaped evaluator joining the Tick core raises its coverage bound, which
    the core fixes for its life (§2.3): that seat waits for a phase behind the gate, while
    a seat founded meanwhile that keeps kappa grows the live core at once."""
    rt = _core_tick_runtime()
    state = rt.routers["Tick"][0]
    kappa = state.coverage
    judge = next(a.spec for a in rt.assemblies.values() if a.spec.role == "evaluator")
    rt._instantiate(replace(judge, id="tick-forecaster", accepts=frozenset({"Tick"})))
    rt._open_epoch("Tick")
    assert rt._coverage([*state.universe, "tick-forecaster"]) > kappa
    assert "tick-forecaster" not in state.universe and "Tick" in rt.pending_epochs
    assert _items(rt, "epoch.deferred")
    _fund_registrations(rt)
    _found(rt, "plain-seat")
    assert rt.routers["Tick"][0] is state and "plain-seat" in state.universe
    assert state.coverage == kappa
    wait = rt.m.timing.min_ratio * max(rt.clockwork.measured("router:Tick"),
                                       rt._delivery_bound())
    rt.ticks_consumed = rt.clockwork.opened("epoch:Tick") + wait
    rt._open_pending_epochs()
    fresh = rt.routers["Tick"][0]
    assert fresh.learner.id != state.learner.id and "tick-forecaster" in fresh.universe
    assert fresh.coverage > kappa and isinstance(fresh.learner.inner.inner, BlumMansour)
    assert rt.clockwork.opened("epoch:Tick") == rt.ticks_consumed
    assert _items(rt, "epoch")[-1]["cause"] == "coverage"


def test_replacements_wait_for_the_gate_and_orphan_nothing_before_it():
    """Sol P1 2: three population replacements at ticks 1, 2 and 3, while the kind's
    gate was closed, all took effect and orphaned every outstanding round (zero learned).
    Now each waits (the last proposed supersedes), the rounds train, the pending
    replacement survives a checkpoint, and the phase it opens is stamped with its tick."""
    rt = make_runtime()
    state = rt.routers["Tick"][0]
    opened = rt.clockwork.opened("epoch:Tick")
    assert opened == 0  # genesis opens the seeded router's first phase
    _fund_registrations(rt)
    handles = []
    for tick in (1, 2, 3):
        rt.ticks_consumed, rt.n = tick, tick
        handles.append(_round(rt, state, f"r{tick}")[0])
        rt._register(f"author-{tick}", RouterProposal("Tick", "exp3"))
        assert rt.routers["Tick"] == [state]  # nothing replaced yet
    assert rt.pending_routers["Tick"]["by"] == "author-3"
    assert [i["supersedes"] for i in _items(rt, "router.deferred")] == [
        None, "author-1", "author-2"]
    restored = make_runtime()
    restore_runtime(restored, runtime_state(rt))
    assert restored.pending_routers == rt.pending_routers
    for handle in handles:
        _settle(restored, handle, 0.7)
    restored._deliver_returns()
    restored.ticks_consumed += 30  # an abstention's credit is due on its seats' clock
    restored._deliver_returns()
    assert {i["handle"] for i in _items(restored, "router.learned")} == set(handles)
    assert not _items(restored, "learner.orphaned")
    wait = restored.m.timing.min_ratio * max(restored.clockwork.measured("router:Tick"),
                                             restored._delivery_bound())
    restored.ticks_consumed = opened + wait - 1
    restored._open_pending_epochs()
    assert restored.routers["Tick"][0].learner.id == state.learner.id
    restored.ticks_consumed += 1
    restored._open_pending_epochs()
    fresh = restored.routers["Tick"][0]
    assert fresh.learner.id != state.learner.id and not restored.pending_routers
    assert restored.clockwork.opened("epoch:Tick") == restored.ticks_consumed
    assert restored.stats.routers_replaced == 1


def test_a_router_added_beside_the_others_is_built_at_once():
    """add=true orphans nothing, so it waits for no gate."""
    rt = make_runtime()
    _fund_registrations(rt)
    rt.ticks_consumed = 1
    rt._register("author", RouterProposal("Tick", "exp3", add=True))
    assert len(rt.routers["Tick"]) == 2 and not rt.pending_routers


def test_the_delivery_bound_is_the_queues_true_delivery():
    """Sol P1 3: the bound read the decision horizon without the cutoff's ratio slack
    (Exposure 160 -> cutoff 214, delivery 856; Forecast 200 -> 267, 1,068; the bound and
    H0 said 800). It is now derived by the same formulas the queue and the deadline use,
    so H0 and the gate are at least the true delivery of the longest router round."""
    rt = make_runtime()
    ratio = rt.m.timing.min_ratio
    longest = rt._decision_horizon({CH_FAST, CH_CONSEQUENCE})
    assert rt._delivery_bound() == delivery_ticks(deadline_ticks(longest, ratio), ratio)
    for channels in ({CH_EXPOSURE}, {CH_CONSEQUENCE}, {CH_FAST}, set()):
        handle = rt.queue.open(actor="router:Tick", event_id=f"e{len(channels)}",
                               propensity=PropensityRecord((NOOP,), (1.0,), NOOP, 0,
                                                           "router:Tick", "s"),
                               channel="test", parent_handle=None,
                               horizon_ticks=rt._decision_horizon(channels), cost_ceiling=0)
        assert rt._delivery_deadline(handle) - rt.queue.opened_tick(handle) <= (
            rt._delivery_bound())
    core = _core_tick_runtime().routers["Tick"][0].learner.inner.inner
    assert core.first_epoch >= rt._delivery_bound()


@pytest.mark.parametrize("kind, refused", [("ProducerReturn", True), ("Verdict", True),
                                           ("Tick", False)])
def test_a_core_router_proposal_needs_a_per_tick_kind(kind, refused):
    """Sol P1 5: load refuses ProducerReturn in no_swap_regret_kinds, but a population
    blum_mansour router for it was admitted, and opened two rounds in one tick under a
    first epoch sized at one draw per tick. The same eligibility now binds proposals,
    before the receipt is spent."""
    rt = make_runtime()
    _fund_registrations(rt)
    registry, remaining = rt.registry.state(), rt.reserve.remaining()
    rt._apply_registrations("author", Return("author", {"register": [
        {"kind": "router", "learner": "blum_mansour", "event_kind": kind}]}, 0, "ok"))
    rejected = _items(rt, "registration.rejected")
    if refused:
        assert rejected and "per-tick draw bound" in rejected[-1]["reason"]
        assert rt.registry.state() == registry and rt.reserve.remaining() == remaining
        assert not any(isinstance(st.learner.inner.inner, BlumMansour)
                       for st in rt.routers.get(kind, []))
    else:
        # Admitted; a replacement then waits for the kind's gate (P1 2).
        assert not rejected and rt.pending_routers[kind]["learner"] == "blum_mansour"


def test_growth_closes_no_core_epoch_and_keeps_its_gamma():
    """Growth in place leaves the core's epoch, its count and its gamma as they were; the
    next epoch reads the grown menu."""
    rt = _core_tick_runtime()
    state = rt.routers["Tick"][0]
    core = state.learner.inner.inner
    _round(rt, state, "one")
    position, gamma = (core.epoch, core.epoch_rounds), core.gamma()
    _fund_registrations(rt)
    _found(rt, "founded")
    assert (core.epoch, core.epoch_rounds) == position and core.gamma() == gamma
    assert core.epoch_n == len(core.actions) - 1
    assert NOOP in state.universe and state.universe[-1] == NOOP
