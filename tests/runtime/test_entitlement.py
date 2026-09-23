"""C10 in the runtime: genesis split, routing feasibility, manifest field, resume."""


import pytest

from factorylab.kernel.budget import unlocked_balance
from factorylab.runtime.loop import Runtime
from factorylab.runtime.resume import resume_runtime, runtime_state
from factorylab.runtime.worlds import load_manifest
from tests.conftest import make_runtime


class ProcessDeath(BaseException):
    pass


def stop_after(rt, predicate):
    original = rt._process_event

    def interrupted(event):
        result = original(event)
        if predicate(rt, event):
            raise ProcessDeath
        return result

    rt._process_event = interrupted
    with pytest.raises(ProcessDeath):
        rt.run()


def invariant(rt) -> bool:
    book = rt.budget
    return (sum(book.entitlements().values()) + book.unallocated()
            == unlocked_balance(rt.wallet) - book.holds()) and book.check_invariant()


def test_genesis_splits_base_share_equally_across_seeded_seats():
    rt = make_runtime(balance=90_000_000)
    seats = [a.id for a in rt.m.assemblies]
    assert len(seats) == 9
    assert rt.budget.entitlements() == {seat: 8_000_000 for seat in seats}
    assert rt.budget.unallocated() == 18_000_000 and invariant(rt)
    genesis = [i for i in rt.ledger.ledger._recovery_items()
               if i["kind"] == "budget" and i["op"] == "genesis"]
    assert len(genesis) == 1 and genesis[0]["amount"] == 90_000_000


def test_exhausted_entitlement_is_infeasible_for_routing_not_insolvency():
    rt = make_runtime()
    seat, other = "seed-decider", "antagonist-a"
    assert rt._is_feasible(seat)[0] and rt._is_feasible(other)[0]
    rt.budget.debit(seat, rt.budget.entitlement(seat), "test")
    # a seed is protected until its first settled record: its trial calls may draw
    # on the unallocated pool, so routing still admits it
    assert rt._is_feasible(seat)[0]
    rt.budget.grant(other, rt.budget.unallocated(), "test")  # the pool is spent
    feasible, reason = rt._is_feasible(seat)
    assert not feasible and reason.startswith("entitlement:") and rt._is_feasible(other)[0]
    assert not rt.wallet.dead and invariant(rt)
    rt.budget.transfer(other, seat, 1_000_000, "test")
    assert rt._is_feasible(seat)[0]
    # a historied seat spends only its own: the pool does not admit it
    rt.budget.debit(seat, rt.budget.entitlement(seat), "test")
    rt.budget.debit(other, 1_000_000, "test")
    rt._unhistoried = lambda action_id: False
    feasible, reason = rt._is_feasible(seat)
    assert not feasible and reason.startswith("entitlement:")


def test_entitlements_restore_exactly_after_a_crash(tmp_path):
    m = load_manifest("scripted")
    path = tmp_path / "entitlement.jsonl"
    rt = Runtime(m, events=140, seed=1, initial_balance_micro=None, ledger_path=str(path),
                 router_gamma=.1)
    rt.events_budget = 8
    stop_after(rt, lambda r, e: r.ticks_consumed == 5 and str(e.kind) == "Tick")
    before = rt.budget.state()
    assert any(before["entitlements"][seat] < 8_888_888 for seat in before["entitlements"])
    assert invariant(rt)
    restored = resume_runtime(m, str(path))
    assert restored.budget.state() == before and invariant(restored)
    # every seeded seat roots its own lineage, and the lineages come back with the book
    assert before["lineages"] == {seat: seat for seat in [a.id for a in m.assemblies]}
    assert restored.budget.lineages() == rt.budget.lineages()
    restored.stats.resumes = 0
    assert runtime_state(restored)["budget"] == runtime_state(rt)["budget"]
    assert restored.run()["ledger_verify"]


def test_a_stale_routing_estimate_is_bridged_by_the_pool_never_a_failed_return():
    rt = Runtime(load_manifest("scripted"), events=30, seed=1, initial_balance_micro=None,
                 ledger_path=None, router_gamma=.1)
    items = []
    append = rt.ledger.append

    def capture(item):
        items.append(dict(item))
        return append(item)

    rt.ledger.append = capture
    stop_after(rt, lambda r, e: r.n == 12)
    seat = "seed-observer"
    real = rt.seat_ceilings[seat]["ceiling"]
    assert real > 0
    # the seat is historied, its recorded ceiling is stale by a lot, and its entitlement
    # covers the stale figure but not the rendered request
    rt._unhistoried = lambda action_id: False
    rt.seat_ceilings[seat] = {"ceiling": 1, "world_chars": rt._current_world_chars()}
    rt.budget.debit(seat, rt.budget.entitlement(seat) - real // 2, "test")
    assert rt._is_feasible(seat)[0]
    rt.run()
    bridges = [i for i in items if i.get("kind") == "budget" and i.get("op") == "bridge"
               and i["assembly_id"] == seat]
    assert bridges and all(b["backed"] == b["amount"] > 0 for b in bridges)
    assert bridges[0]["reason"] == "routing estimate"
    invocations = [i for i in items if i.get("kind") == "invocation"
                   and i["assembly_id"] == seat and items.index(i) > items.index(bridges[0])]
    assert invocations and all(i["status"] == "ok" for i in invocations)
    assert rt.seat_ceilings[seat]["ceiling"] >= real  # the evidence is refreshed
    assert rt.budget.holds() == 0 and invariant(rt)
