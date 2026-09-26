"""The standing committee (charter audit C1, C2, P4, M7), in a running scripted world.

Essay II.IV.a: "On the cadence of charter revision, a sample of the factory's
population is seated (no-regret learners, no-swap-regret learners, productive
agents, evaluators, antagonists), and that seat is consistently rotated."
A motion no longer summons its own committee: it waits for the next governance
boundary, where one committee, stratified and at quorum, votes on every waiting
motion. Retirements are internal self-organization with their own queue.
"""

from factorylab.charter.amendment import PredictedEffect
from factorylab.cortex.registration import RetireProposal
from factorylab.kernel.queue import PropensityRecord
from tests.conftest import make_runtime

SEATS = {"seed-observer": "producer", "seed-decider": "producer", "eval-a": "evaluator",
         "eval-b": "evaluator", "antagonist-a": "antagonist", "meta-a": "meta"}


def _handle(rt, seat="seed-decider", event="proposal"):
    prop = PropensityRecord((seat,), (1.0,), seat, 0, "router:Tick", "test")
    handle = rt.queue.open(actor="router:Tick", event_id=event, propensity=prop,
                           channel="verdict", deadline_ns=10**18, parent_handle=None,
                           cost_ceiling=0)
    rt.handle_to_assembly[handle] = seat
    return handle


def _runtime(monkeypatch, eligible=SEATS):
    rt = make_runtime()
    rt._manage_reserve_window()
    pool = dict(eligible)
    monkeypatch.setattr(rt, "_committee_eligible", lambda: dict(pool))
    return rt, pool


def _items(rt, kind):
    return [i for i in rt.ledger._recovery_items() if i["kind"] == kind]


def _to_boundary(rt):
    """Advance the world's clock and event count to the next governance boundary."""
    rt.clock.now_ns = rt.cadence.earliest_ns(rt.tick_clock)
    rt.n = rt.cadence.earliest_event()
    rt.cadence.advance(rt.n)


def _propose_drop(rt, handle, motion="drop-cost"):
    rt._propose_amendment(handle, {
        "kind": "amendment", "id": motion, "remove": ["cost_per_return"],
        "predicted_effect": {"card_id": "well_formed_rate", "direction": "increase",
                             "window": 1}})


def test_a_motion_waits_for_the_boundary_where_one_committee_decides_it(monkeypatch):
    rt, _ = _runtime(monkeypatch)
    handle = _handle(rt)
    _propose_drop(rt, handle)
    # C1: nothing is seated for the motion; it waits on the agenda.
    assert _items(rt, "charter.seat") == [] and _items(rt, "committee.decision") == []
    assert [am.id for am in rt.charter_book.agenda()] == ["drop-cost"]
    assert rt._world_block()["governance"]["agenda"] == ["drop-cost"]
    rt._activate_charter_if_due()
    assert _items(rt, "charter.seat") == [] and rt.charter.edition == 1
    _to_boundary(rt)
    rt._activate_charter_if_due()
    boundary, = _items(rt, "charter.boundary")
    seating, = _items(rt, "charter.seat")
    assert seating["boundary"] == boundary["boundary"] == 1
    assert seating["agenda"] == ["drop-cost"] and seating["quorum"] == 3
    # The proposer's own seat, if drawn, does not vote on its own motion.
    proposer = [s["alias"] for s in seating["seats"] if s["assembly_id"] == "seed-decider"]
    ballots = [i for i in _items(rt, "charter.vote") if i["amendment_id"] == "drop-cost"]
    assert len(ballots) == len(seating["seats"]) - len(proposer)
    assert not {b["alias"] for b in ballots} & set(proposer)
    assert rt.charter.edition == 2
    assert "cost_per_return" not in {c.id for c in rt.charter.cards}
    # Rotation: the next boundary seats a new committee, with an empty agenda.
    _to_boundary(rt)
    rt._activate_charter_if_due()
    first, second = _items(rt, "charter.seat")
    assert second["boundary"] == 2 and second["round"] == 2 and second["agenda"] == []


def test_below_quorum_the_boundary_seats_no_rump_and_the_motion_waits(monkeypatch):
    rt, pool = _runtime(monkeypatch, eligible={"eval-a": "evaluator", "meta-a": "meta"})
    _propose_drop(rt, _handle(rt))
    _to_boundary(rt)
    rt._activate_charter_if_due()
    deferred, = _items(rt, "charter.seat_deferred")
    assert deferred["eligible"] == 2 and deferred["quorum"] == 3
    assert deferred["agenda"] == ["drop-cost"]
    assert _items(rt, "charter.seat") == [] and _items(rt, "charter.vote") == []
    assert rt.charter.edition == 1
    assert [am.id for am in rt.charter_book.agenda()] == ["drop-cost"]
    # The next boundary, with a quorum's worth of eligible seats, decides it.
    pool.update({"eval-b": "evaluator", "antagonist-a": "antagonist"})
    _to_boundary(rt)
    rt._activate_charter_if_due()
    seating, = _items(rt, "charter.seat")
    assert seating["boundary"] == 2 and seating["agenda"] == ["drop-cost"]
    assert rt.charter.edition == 2


def test_the_draw_is_stratified_over_roles_and_learner_types(monkeypatch):
    """C2: a Blum-Mansour-routed seat and an EXP3-routed seat are both seated."""
    rt, _ = _runtime(monkeypatch)
    # meta-a is sampled by the Verdict router; make that router no-swap-regret.
    rt._build_router("Verdict", "blum_mansour", 0.1)
    learners = rt._seat_learners(SEATS)
    assert learners["meta-a"] == frozenset({"blum_mansour"})
    assert learners["seed-decider"] == frozenset({"exp3"})
    for boundary in range(1, 6):
        _to_boundary(rt)
        rt._activate_charter_if_due()
        seating = _items(rt, "charter.seat")[-1]
        assert seating["boundary"] == boundary
        coverage = seating["coverage"]
        assert coverage["roles"]["covered"] == coverage["roles"]["present"] == [
            "producer", "evaluator", "meta", "antagonist"]
        assert coverage["learners"] == {"present": ["blum_mansour", "exp3"],
                                        "covered": ["blum_mansour", "exp3"]}
        assert coverage["eligible"] == len(SEATS) and coverage["seats"] == 5


def test_an_assembly_learner_is_its_own_stratum(monkeypatch):
    from factorylab.learners.blum_mansour import BlumMansour
    from factorylab.learners.delayed import SnapshotLearner
    from factorylab.learners.exp3 import EXP3

    rt, _ = _runtime(monkeypatch)
    inner = BlumMansour(lambda acts: EXP3(acts, 0.1), ("hold", "buy"), id="x")
    rt.assembly_learners["seed-observer"] = SnapshotLearner(inner, id="x")
    assert rt._seat_learners({"seed-observer": "producer"}) == {
        "seed-observer": frozenset({"blum_mansour"})}


def test_a_retirement_has_its_own_queue_and_never_stalls_charter_activation(monkeypatch):
    """P4: a passed retirement takes effect at the next window boundary, off the cadence."""
    rt, _ = _runtime(monkeypatch)
    effect = PredictedEffect("well_formed_rate", "increase", 1)
    rt._propose_retirement(_handle(rt, event="retire"), RetireProposal("eval-d"),
                           predicted_effect=effect)
    motion, = rt.retirement_proposals
    assert rt.retirement_proposals[motion]["status"] == "passed"
    _propose_drop(rt, _handle(rt, event="amend"))
    # The retirement is never on the charter cadence.
    assert rt.cadence.world_block(rt.tick_clock)["waiting"] == []
    anchor = rt.cadence.earliest_ns(rt.tick_clock)
    rt._activate_charter_if_due()  # a window boundary that is not a governance boundary
    assert "eval-d" in rt.retired_assemblies
    assert rt.retirement_proposals[motion]["status"] == "activated"
    assert rt.cadence.earliest_ns(rt.tick_clock) == anchor
    assert rt.charter.edition == 1
    _to_boundary(rt)
    rt._activate_charter_if_due()
    assert rt.charter.edition == 2


def test_an_internal_motion_below_quorum_is_refused_not_seated(monkeypatch):
    import pytest

    rt, _ = _runtime(monkeypatch, eligible={"eval-a": "evaluator", "meta-a": "meta",
                                            "seed-decider": "producer"})
    before = rt.budget.entitlement("seed-decider")
    with pytest.raises(ValueError, match="fewer than committee.quorum 3"):
        rt._propose_retirement(_handle(rt), RetireProposal("eval-d"),
                               predicted_effect=PredictedEffect("well_formed_rate",
                                                                "increase", 1))
    assert rt.retirement_proposals == {}
    assert rt.budget.entitlement("seed-decider") == before  # no trial was charged


def test_saturation_statistics_reach_the_world_the_wake_and_the_agenda(monkeypatch):
    """M7; wave 16, R-E: each card's bound, its windows at it and the violation's
    duration, per card, in public."""
    from factorylab.runtime.wake import public_window_item

    rt, _ = _runtime(monkeypatch)
    rt._derive_regions()
    card = "well_formed_rate"  # a floor: 0.0 is deep in violation
    for event in range(1, 30):
        rt.controller.observe(card, 0.0, window_end_event=event * 10)
    stats = rt.controller.saturation(card)
    assert stats["violation_windows"] == 29 and stats["windows_at_bound"] > 0
    assert stats["saturated_windows"] > 0 and stats["bound"] is not None
    world = {row["card_id"]: row for row in rt._world_block()["card_prices"]}
    assert {k: world[card][k] for k in stats} == stats
    public = {row["id"]: row for row in
              public_window_item(rt, window=1, event=rt.n)["charter"]["cards"]}
    assert {k: public[card][k] for k in stats} == stats
    requests = []
    request = rt._request
    monkeypatch.setattr(rt, "_request", lambda handle, description, inputs, *rest: (
        requests.append((description, inputs)), request(handle, description, inputs, *rest))[1])
    _propose_drop(rt, _handle(rt))
    _to_boundary(rt)
    rt._activate_charter_if_due()
    ballots = [inputs for description, inputs in requests if description.startswith("Vote")]
    assert ballots
    for inputs in ballots:
        agenda = inputs["agenda"]
        assert agenda["motions"] == ["drop-cost"]
        assert agenda["penalty_cap"] == rt.m.prices.penalty_cap
        row = next(r for r in agenda["cards"] if r["card_id"] == card)
        assert {k: row[k] for k in stats} == stats
