"""A router round's life, from its draw to the one place it ends (learners design §2.3-2.6).

A round is learned, orphaned (its router was replaced, or its core epoch closed),
withdrawn (a quiet draw) or expired (past its delivery deadline); nothing waits without
bound, a retirement orphans nothing, a menu that grows waits min_ratio delivery bounds,
and the world's draw transforms are measured on every draw they move.
"""

from random import Random

import pytest

from factorylab.kernel.queue import PropensityRecord, SettleStatus
from factorylab.runtime.resume import restore_runtime, runtime_state
from factorylab.runtime.shared import NOOP
from tests.conftest import make_runtime
from tests.helpers import freeze_round


def _items(rt, kind):
    return [i for i in rt.ledger._recovery_items() if i["kind"] == kind]


def _noop(rt, state, *, horizon=10):
    handle = rt.queue.open(actor=state.learner.id, event_id=f"noop-{rt.n}",
                           propensity=PropensityRecord((NOOP,), (1.0,), NOOP, 0,
                                                       state.learner.id, "s"),
                           channel="test", horizon_ticks=horizon, parent_handle=None,
                           cost_ceiling=0)
    freeze_round(rt, handle, state)
    return handle


def test_owed_credit_due_never_exceeds_cutoff():
    """Sol's probe on the design (rev 2): open tick 100, cutoff 110, a router mean latency
    of 100 ticks put the credit due at 200. It is clamped to the round's cutoff."""
    rt = make_runtime()
    state = rt.routers["ProducerReturn"][0]
    state.latency = [100, 1]
    rt.ticks_consumed = 100
    handle = _noop(rt, state)
    cutoff = rt.queue.deadline_tick(handle)
    assert cutoff < 200
    rt._defer_abstention(state, rt.snapshot_keys.pop(handle), rt.queue.get(handle))
    assert rt.noop_credits[handle]["due_tick"] == cutoff


def test_window_outliving_bound_expires_round(monkeypatch):
    """A credit still awaiting its origin window's close at its delivery deadline (the
    window's measured inner loop grew) expires untrained: snapshot released, ledgered."""
    rt = make_runtime()
    state = rt.routers["ProducerReturn"][0]
    handle = _noop(rt, state)
    key = rt.snapshot_keys.pop(handle)
    rt.noop_credits[handle] = {"router": state.learner.id, "due_tick": rt.ticks_consumed,
                               "key": key}
    monkeypatch.setattr(rt, "_abstention_awaits_close", lambda h: h == handle)
    deadline = rt._delivery_deadline(handle)
    rt.ticks_consumed = deadline
    rt._credit_abstentions()
    assert handle in rt.noop_credits  # at the deadline: still owed
    before = state.learner.inner.inner.state()
    rt.ticks_consumed = deadline + 1
    rt._credit_abstentions()
    assert handle not in rt.noop_credits
    assert key not in state.learner.inner.outstanding()
    assert state.learner.inner.inner.state() == before  # trained nothing
    (row,) = _items(rt, "learner.expired")
    assert row["handle"] == handle and row["waited_on"] == "awaiting its window's close"


def test_epoch_change_orphans_in_flight_rounds_across_resume(monkeypatch):
    """An owed abstention of a router a menu phase replaced survives a checkpoint, and at
    its due tick trains nothing, neither the retired router nor its successor; a quiet
    draw is withdrawn, never orphaned; a repeated return credits nothing twice."""
    rt = make_runtime()
    old = rt.routers["ProducerReturn"][0]
    handle = _noop(rt, old, horizon=50)
    rt.queue.settle(handle, channel="test", score=0.0, status=SettleStatus.INAPPLICABLE,
                    definition_version="noop", sampling_ref=None)
    old.latency = [20, 1]
    rt._deliver_returns()
    assert handle in rt.noop_credits  # owed, due in 20 ticks
    grown = [*old.universe[:-1], "new-judge", NOOP]
    monkeypatch.setattr(rt, "_universe_for", lambda *_a, **_k: list(grown))
    rt._open_epoch("ProducerReturn")
    fresh = rt.routers["ProducerReturn"][0]
    assert fresh.learner.id != old.learner.id and old.learner.id in rt.retired_routers
    restored = make_runtime()
    restore_runtime(restored, runtime_state(rt))
    retired = restored.retired_routers[old.learner.id]
    live = restored.routers["ProducerReturn"][0]
    assert handle in restored.noop_credits
    retired_before, live_before = (retired.learner.inner.inner.state(),
                                   live.learner.inner.inner.state())
    restored.ticks_consumed += 20
    restored._deliver_returns()
    assert handle not in restored.noop_credits
    assert retired.learner.inner.inner.state() == retired_before
    assert live.learner.inner.inner.state() == live_before
    assert [i["handle"] for i in _items(restored, "learner.orphaned")] == [handle]
    restored._deliver_returns()  # a repeated delivery credits nothing twice
    assert len(_items(restored, "learner.orphaned")) == 1
    assert old.learner.id not in restored.retired_routers  # drained once closed


def test_a_retirement_opens_no_phase_and_its_round_still_trains():
    """Learners design §2.5: a retirement leaves the router (and its learner) in force; the
    retired seat is infeasible, and a round it drew before still trains the router."""
    rt = make_runtime()
    state = rt.routers["ProducerReturn"][0]
    seat = next(a for a in state.universe if a != NOOP)
    arms = tuple(state.universe)
    probs = tuple(1 / len(arms) for _ in arms)
    probs = (*probs[:-1], 1 - sum(probs[:-1]))
    seed = next(s for s in range(10_000)
                if Random(s).choices(arms, weights=probs, k=1)[0] == seat)
    handle = rt.queue.open(actor=state.learner.id, event_id="before",
                           propensity=PropensityRecord(arms, probs, seat, seed,
                                                       state.learner.id, "s"),
                           channel="test", horizon_ticks=20, parent_handle=None,
                           cost_ceiling=0)
    freeze_round(rt, handle, state)
    rt._retire_assembly(seat, "test")
    rt._open_epoch("ProducerReturn")
    assert rt.routers["ProducerReturn"][0] is state and seat in state.universe
    sample = state.router.route(state.kind, lambda a: (a != seat, "retired"), Random(0))
    assert seat not in sample.action_ids
    rt.queue.settle(handle, channel="test", score=0.9, status=SettleStatus.SETTLED,
                    definition_version="1", sampling_ref=None)
    rt._deliver_returns()
    assert [i["handle"] for i in _items(rt, "router.learned")] == [handle]
    assert not _items(rt, "learner.orphaned")


def test_coverage_is_the_menus_fixed_bound_and_an_adversary_is_infeasible_at_share_zero():
    """Design §2.3: kappa is 1/share with an adversary on the menu, times 1/(1 - s_cap)
    with a forecast evaluator; with adversarial_share 0 no adversary is offered."""
    from dataclasses import replace

    rt = make_runtime()
    tick = rt.routers["Tick"][0]
    assert "antagonist-a" in tick.universe
    assert tick.coverage == pytest.approx(1 / rt.ev.adversarial_share)
    assert rt._coverage(["seed-decider", NOOP]) == 1.0
    rt.ev = replace(rt.ev, adversarial_share=0.0)
    rt.m = replace(rt.m, evaluation=rt.ev)
    assert rt._coverage(tick.universe) == 1.0
    from factorylab.kernel.events import Event, EventKind

    rt._route_with(tick, Event("t", EventKind.TICK, 0, {}, "test"))
    assert all(i.get("reason") != "adversarial share 0"
               for i in _items(rt, "route.excluded"))  # never filed as a family exclusion
    opened = [i for i in rt.ledger._recovery_items() if i["kind"] == "decision.open"]
    assert opened and all("antagonist-a" not in i.get("propensity", {}).get("action_ids", [])
                          or dict(zip(i["propensity"]["action_ids"],
                                      i["propensity"]["probs"], strict=True))
                          .get("antagonist-a", 0) == 0
                          for i in opened)


def test_a_transform_that_moves_the_draw_is_measured_on_it():
    """Design §2.3: the executed policy's extra regret over the learner's own is at most
    twice the total variation the world's transforms moved, ledgered per draw."""
    from factorylab.kernel.events import Event, EventKind

    rt = make_runtime()
    tick = rt.routers["Tick"][0]
    rt._route_with(tick, Event("t", EventKind.TICK, 0, {}, "test"))
    (row,) = [i for i in rt.ledger._recovery_items() if i["kind"] == "compute.route"]
    # A fresh router is uniform over (antagonist, decider, NOOP): the antagonist's 1/3
    # is capped at the adversarial share, a transform of 1/3 - 0.15.
    assert row["transform_tv"] == pytest.approx(1 / 3 - rt.ev.adversarial_share)


@pytest.mark.gate
def test_permitted_menu_churn_keeps_learning(monkeypatch):
    """Design §2.5, Sol on rev 2: the gate on a growing menu is min_ratio delivery bounds,
    so however fast the population registers, at least 1 - 1/min_ratio of a router's
    rounds are learned inside the phase that drew them. The old gate read only the
    measured round period, which a router whose rounds all orphan never measures: it
    let a phase open every min_ratio ticks and orphaned every round."""
    rt = make_runtime()
    kind = "ProducerReturn"
    delay = 20
    # A shorter delivery bound than the scripted world's longest cutoff, to keep the
    # run short; the rounds below never wait longer than it.
    bound = 4 * (delay + 10)
    monkeypatch.setattr(type(rt), "_delivery_bound", lambda self: bound)
    rng = Random(3)
    grown = list(rt.routers[kind][0].universe)
    pending = []
    phases = 0
    drew = set()
    for t in range(4 * rt.m.timing.min_ratio * rt._delivery_bound() + delay):
        rt.n += 1
        rt.ticks_consumed += 1
        # A new seat asks to join every tick: the most churn the population can attempt.
        grown = [*grown[:-1], f"newcomer-{t}", NOOP]
        monkeypatch.setattr(rt, "_universe_for", lambda *_a, g=list(grown), **_k: g)
        before = rt.routers[kind][0]
        rt._open_epoch(kind)
        phases += rt.routers[kind][0] is not before
        state = rt.routers[kind][0]
        drew.add(state.learner.id)
        key = f"k{t}"
        state.learner.current_key, state.learner.current_ordinal = key, rt.n
        sample = state.router.route(kind, lambda _a: (True, ""), rng)
        handle = rt.queue.open(actor=state.learner.id, event_id=f"e{t}",
                               propensity=rt._propensity(sample), channel="test",
                               horizon_ticks=delay + 10, parent_handle=None, cost_ceiling=0)
        rt.snapshot_keys[handle] = key
        pending.append((rt.ticks_consumed + delay, handle, sample.chosen))
        while pending and pending[0][0] <= rt.ticks_consumed:
            _due, done, chosen = pending.pop(0)
            if chosen == NOOP:
                status, score = SettleStatus.INAPPLICABLE, 0.0
            else:
                status, score = SettleStatus.SETTLED, 0.7
            rt.queue.settle(done, channel="test", score=score, status=status,
                            definition_version="v", sampling_ref=None)
        rt._deliver_returns()
    learned = len(_items(rt, "router.learned"))
    orphaned = len(_items(rt, "learner.orphaned"))
    assert phases >= 3
    assert learned / (learned + orphaned) >= 1 - 1 / rt.m.timing.min_ratio - 0.05
    learners = {i["learner"] for i in _items(rt, "router.learned")}
    # Every completed phase that drew learned something (the one still open at the end
    # may not have heard back yet).
    assert drew - {rt.routers[kind][0].learner.id} <= learners


@pytest.mark.gate
def test_snapshot_count_bounded_by_r_times_L(scripted_runtime_run):
    """Design §2.6, memory: after a world run, every open router round belongs to a live
    decision or an owed credit, none is past its delivery deadline, and every one opened
    within the last delivery bound: the open rounds are the last L ticks' draws at most,
    never a lifetime of them."""
    from factorylab.runtime.worlds import load_manifest

    manifest = load_manifest("scripted")
    rt = scripted_runtime_run(manifest, 200, 1).runtime(manifest)
    owners = {key: handle for handle, key in rt.snapshot_keys.items()}
    owners.update({c["key"]: h for h, c in rt.noop_credits.items() if c.get("key")})
    bound = rt._delivery_bound()
    for state in [*rt._all_router_states(), *rt.retired_routers.values()]:
        open_rounds = state.learner.inner.outstanding()
        for key in open_rounds:
            assert key in owners, (state.learner.id, key)  # no orphaned snapshot
            deadline = rt._delivery_deadline(owners[key])
            assert deadline is None or rt.ticks_consumed <= deadline
        # Each open round's decision opened within the bound: at most the draws of the
        # last L ticks are open, never a lifetime of them.
        for key in open_rounds:
            opened = rt.queue.opened_tick(owners[key])
            assert opened is None or opened >= rt.ticks_consumed - bound
