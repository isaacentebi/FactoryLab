"""A router round's life, from its draw to the one place it ends (learners design §2.3-2.6).

A round is learned, orphaned (its router was replaced, or its core epoch closed),
withdrawn (a quiet draw) or expired (past its delivery deadline); nothing waits without
bound, a retirement orphans nothing, a menu that grows grows the live learner and
orphans nothing, and the world's draw transforms are measured on every draw they move.
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


def test_epoch_change_orphans_in_flight_rounds_across_resume():
    """An owed abstention of a router a replacement's phase retired survives a
    checkpoint, and at its due tick trains nothing, neither the retired router nor its
    successor; a repeated return credits nothing twice."""
    rt = make_runtime()
    rt._manage_reserve_window()
    rt.ticks_consumed = rt.m.timing.min_ratio * rt._delivery_bound()  # the gate is open
    old = rt.routers["ProducerReturn"][0]
    handle = _noop(rt, old, horizon=50)
    rt.queue.settle(handle, channel="test", score=0.0, status=SettleStatus.INAPPLICABLE,
                    definition_version="noop", sampling_ref=None)
    old.latency = [20, 1]
    rt._deliver_returns()
    assert handle in rt.noop_credits  # owed, due in 20 ticks
    from factorylab.cortex.registration import RouterProposal

    rt._register("population", RouterProposal("ProducerReturn", "exp3"))
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
    """Design §2.5, revision 4: a menu that grows grows the live learner, so however fast
    the population registers, no phase opens, nothing in flight is orphaned, and every
    round the router draws is learned. The phase-per-growth design of revision 3 waited
    min_ratio delivery bounds between growths and still orphaned up to 1/min_ratio of
    each phase's rounds; the gate-free one before it orphaned every round."""
    rt = make_runtime()
    kind = "ProducerReturn"
    delay = 20
    rng = Random(3)
    grown = list(rt.routers[kind][0].universe)
    pending = []
    first = rt.routers[kind][0]
    ticks = 4 * rt.m.timing.min_ratio * (delay + 10) + delay
    for t in range(ticks):
        rt.n += 1
        rt.ticks_consumed += 1
        # A new seat asks to join every tick: the most churn the population can attempt.
        grown = [*grown[:-1], f"newcomer-{t}", NOOP]
        monkeypatch.setattr(rt, "_universe_for", lambda *_a, g=list(grown), **_k: g)
        rt._open_epoch(kind)
        state = rt.routers[kind][0]
        assert state is first and f"newcomer-{t}" in state.universe
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
    for _ in range(2 * (delay + 10)):  # the last rounds' returns and credits come due
        rt.ticks_consumed += 1
        while pending and pending[0][0] <= rt.ticks_consumed:
            _due, done, chosen = pending.pop(0)
            status = SettleStatus.INAPPLICABLE if chosen == NOOP else SettleStatus.SETTLED
            rt.queue.settle(done, channel="test", score=0.0 if chosen == NOOP else 0.7,
                            status=status, definition_version="v", sampling_ref=None)
        rt._deliver_returns()
    learned = len(_items(rt, "router.learned"))
    assert not _items(rt, "learner.orphaned") and not _items(rt, "learner.expired")
    assert learned == ticks  # the learned fraction is 1
    assert not _items(rt, "epoch") or all(i["event_kind"] != kind for i in _items(rt, "epoch"))
    assert len([i for i in _items(rt, "router.grown") if i["event_kind"] == kind]) == ticks


@pytest.mark.gate
def test_snapshot_count_bounded_by_r_times_L(scripted_runtime_run):
    """Design §2.6, memory: after a world run, every open router round belongs to a live
    decision or an owed credit, none is past its delivery deadline, and each one's
    delivery span is within the delivery bound L: the open rounds are the last L ticks'
    draws at most, never a lifetime of them. The world is the shared 100-event scripted
    run (``scripted_runtime_run``), which already holds open rounds of every kind;
    a 200-event run of its own put this file over its gate CPU budget (Sol on #191),
    and with L = 1,068 ticks no longer run proved nothing more."""
    from factorylab.runtime.worlds import load_manifest

    manifest = load_manifest("scripted")
    rt = scripted_runtime_run(manifest, 100, 1).runtime(manifest)
    owners = {key: handle for handle, key in rt.snapshot_keys.items()}
    owners.update({c["key"]: h for h, c in rt.noop_credits.items() if c.get("key")})
    bound = rt._delivery_bound()
    checked = 0
    for state in [*rt._all_router_states(), *rt.retired_routers.values()]:
        for key in state.learner.inner.outstanding():
            assert key in owners, (state.learner.id, key)  # no orphaned snapshot
            deadline = rt._delivery_deadline(owners[key])
            assert deadline is None or rt.ticks_consumed <= deadline
            # Each open round is learned within L of its opening: at most the draws of
            # the last L ticks are open, never a lifetime of them.
            opened = rt.queue.opened_tick(owners[key])
            assert deadline is None or opened is None or deadline - opened <= bound
            checked += 1
    assert checked  # the run left rounds open: nothing above is vacuous
