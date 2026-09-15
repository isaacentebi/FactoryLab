"""Edition 2, C10: in the scripted world, mistakes cost their maker.

Nine seeded seats start with equal base shares; a seat's thinking is debited
from its own entitlement; a child's trial moves from its proposer; a retired
seat's entitlement returns to the pool; a settled paid-off return credits its
owner; a seat whose entitlement is exhausted is skipped by routing while the
others keep running; and the classification invariant holds throughout,
including across a crash and resume.
"""

from collections import Counter

import pytest

from factorylab.kernel.budget import unlocked_balance
from factorylab.runtime.worlds import load_manifest

EVENTS = 500


def invariant(rt) -> bool:
    book = rt.budget
    return (sum(book.entitlements().values()) + book.unallocated()
            == unlocked_balance(rt.wallet) - book.holds()) and book.check_invariant()


@pytest.fixture(scope="module")
def world(scripted_runtime_run):
    manifest = load_manifest("scripted")
    record = scripted_runtime_run(manifest, EVENTS, 1, drip=False)
    return record.runtime(manifest), record.entries


def budget(entries, op):
    return [e for e in entries if e["kind"] == "budget" and e["op"] == op]


def test_nine_seats_start_with_equal_base_shares(world):
    rt, entries = world
    # genesis is ledgered in the constructor, before the first event: the recorded
    # run's entries (captured from the first event on) carry no genesis, and the
    # restored runtime's own launch ledger carries exactly one
    assert not budget(entries, "genesis")
    genesis = [i for i in rt.ledger.ledger._recovery_items()
               if i["kind"] == "budget" and i["op"] == "genesis"]
    assert len(genesis) == 1
    seeds = [a.id for a in rt.m.assemblies]
    assert len(seeds) == 9
    share = 80_000_000 // 9
    assert genesis[0]["grants"] == {seat: share for seat in seeds}
    assert genesis[0]["to_unallocated"] == 100_000_000 - 9 * share
    # the first ledgered movement of the run is a seat's own hold, not a grant
    assert budget(entries, "hold")[0]["assembly_id"] in seeds


def test_an_expensive_seat_pays_more_than_a_cheap_one_from_its_own_entitlement(world):
    rt, entries = world
    paid = Counter()
    for item in budget(entries, "commit"):
        paid[item["assembly_id"]] += item["own"]
    expensive, cheap = "eval-d", "meta-a"  # fake-opus versus fake-haiku, both judges' tier
    assert paid[expensive] > 5 * paid[cheap] > 0
    share = 80_000_000 // 9
    assert rt.budget.entitlement(expensive) < rt.budget.entitlement(cheap) < share
    assert share - rt.budget.entitlement(cheap) <= paid[cheap] + 2 * rt.ev.trial_amount_micro
    # every seat's thinking was paid from the seat itself, never the commons
    assert all(item["commons"] == 0 for item in budget(entries, "commit")
               if item["assembly_id"] in {a.id for a in rt.m.assemblies})


def test_registering_a_child_moves_the_trial_from_the_proposer(world):
    rt, entries = world
    transfers = budget(entries, "transfer")
    children = {t["dst"] for t in transfers if t["reason"] == "trial:assembly"}
    assert "funding-watcher" in children and "composition-helper" in children
    seeds = {a.id for a in rt.m.assemblies}
    for transfer in transfers:
        assert transfer["src"] in seeds and transfer["dst"] not in seeds
        assert transfer["amount"] == rt.ev.trial_amount_micro
    # a tool or a learner costs its proposer the same trial; it has no seat, so the
    # amount returns to the pool
    debits = [d for d in budget(entries, "debit") if d["reason"].startswith("trial:")]
    assert debits and {d["reason"] for d in debits} <= {"trial:tool", "trial:learner"}
    assert all(d["amount"] == rt.ev.trial_amount_micro and d["assembly_id"] in seeds
               for d in debits)


def test_retirement_returns_the_entitlement_to_the_pool(world):
    rt, entries = world
    retirements = budget(entries, "retire")
    assert retirements, "the scripted retirement vote passed"
    retired = retirements[0]
    assert retired["assembly_id"] in rt.retired_assemblies
    assert retired["amount"] > 0
    # the pool grows by exactly what the seat held, relative to the previous movement
    movements = [e for e in entries if e["kind"] == "budget" and "unallocated_after" in e]
    previous = movements[movements.index(retired) - 1]
    assert retired["unallocated_after"] == previous["unallocated_after"] + retired["amount"]
    assert rt.budget.entitlement(retired["assembly_id"]) == 0
    assert retired["assembly_id"] not in rt.budget.seats()


def test_a_settled_paid_off_return_credits_its_owner(world):
    rt, entries = world
    credits = [c for c in budget(entries, "credit") if c["reason"] == "return_paid_off"]
    assert credits and any(c["amount"] > 0 for c in credits)
    outcomes = {e["handle"]: e for e in entries if e["kind"] == "consequence.outcome"}
    for credit in credits:
        assert credit["assembly_id"] in rt.assemblies
        assert credit["requested"] > 0
        # the classification never exceeds what the pool held at the time
        assert credit["amount"] <= credit["requested"]
        assert credit["unallocated_after"] >= 0 or credit["amount"] == 0
    assert any(o["y"] == 1 and not o["marked"] for o in outcomes.values())


def test_an_exhausted_seat_is_skipped_by_routing_while_others_keep_running(world):
    rt, entries = world
    # the registered children ran their trials on the protected share and, with
    # only their trial endowment left, are then skipped rather than failed
    invocations = rt.stats.invocations_by_assembly
    assert invocations["funding-watcher"] >= 1
    assert rt.budget.entitlement("funding-watcher") < rt.budget.last_hold("funding-watcher")
    rt.reserve._NoveltyReserve__remaining = 0
    feasible, reason = rt._is_feasible("funding-watcher")
    assert not feasible and reason.startswith("entitlement:")
    # a judge that only pays for its own thinking is still routed; the seeded trader,
    # charged for every loss it made, may well be exhausted too by now
    assert rt._is_feasible("meta-a")[0] and rt._is_feasible("eval-b")[0]
    assert not any(e["kind"] == "invocation" and e.get("status") == "failed"
                   and e.get("assembly_id") == "funding-watcher" for e in entries)
    # an exhausted entitlement is not insolvency: the world is alive and well formed
    assert not rt.termination.final and rt.insolvency_count == 0
    assert rt.stats.last_window_values["well_formed_rate"] == 1.0


def test_a_settled_loss_debits_its_maker_to_a_floor_of_zero(world):
    rt, entries = world
    charges = [c for c in budget(entries, "charge") if c["reason"] == "return_paid_off"]
    assert charges and all(c["assembly_id"] in rt.assemblies for c in charges)
    assert all(c["amount"] == c["own"] + c["commons"] and c["own"] >= 0 for c in charges)
    assert sum(c["own"] for c in charges) > 0
    # the maker paid everything it could: commons appear only once its entitlement is empty
    for charge in charges:
        if charge["commons"]:
            assert charge["entitlement_after"][charge["assembly_id"]] == 0
    assert all(rt.budget.entitlement(seat) >= 0 for seat in rt.budget.seats())


def test_a_released_tranche_is_split_across_live_seats_through_the_hook():
    from tests.audit.test_e2_endowment import INITIAL, TICK, endowed, kinds

    offset = 3 * TICK + TICK // 2
    rt = endowed(INITIAL, ((offset, INITIAL),), events=8)
    # everything locked at launch: genesis had nothing unlocked to classify
    genesis = kinds(rt, "budget")[0]
    assert genesis["op"] == "genesis" and genesis["backed"] == 0 and genesis["grants"] == {}
    assert set(rt.budget.entitlements().values()) == {0}
    rt.run()
    releases = [i for i in kinds(rt, "budget") if i["op"] == "release"]
    seats = [a.id for a in rt.m.assemblies]
    share = 80_000_000 // len(seats)
    assert len(releases) == 1 and releases[0]["amount"] == INITIAL
    assert releases[0]["grants"] == {seat: share for seat in seats}
    assert releases[0]["to_unallocated"] == INITIAL - share * len(seats)
    items = kinds(rt, "release", "budget")
    assert items.index(next(i for i in items if i["kind"] == "release")) < items.index(releases[0])
    assert all(rt.budget.entitlement(seat) <= share for seat in seats)
    assert any(rt.budget.entitlement(seat) < share for seat in seats)  # spent after waking
    assert invariant(rt)


def test_x402_income_credits_the_seat_that_owns_the_service(tmp_path):
    from factorylab.world.income import IncomeSpool
    from tests.conftest import make_runtime

    rt = make_runtime(balance=90_000_000)
    rt.tool_owner["doubler"] = "seed-decider"
    rt.treasury.income_spool = tmp_path / "income.jsonl"
    spool = IncomeSpool(rt.treasury.income_spool)
    spool.append({"service": "doubler", "micro": 2_500, "tx": "0x" + "ab" * 32,
                  "payer": "0x" + "cd" * 20, "program": "doubler", "version": 1, "ts": 7})
    spool.append({"service": "orphan", "micro": 900, "tx": "0x" + "ef" * 32,
                  "payer": "0x" + "cd" * 20, "program": "orphan", "version": 1, "ts": 8})
    before = rt.budget.entitlement("seed-decider")
    pool = rt.budget.unallocated()
    rt._collect_income()
    assert rt.budget.entitlement("seed-decider") == before + 2_500
    assert rt.budget.unallocated() == pool - 2_500  # the pool backs the reclassification
    items = [i for i in rt.ledger.ledger._recovery_items()
             if i["kind"] in ("income.earned", "budget")]
    earned = [i for i in items if i["kind"] == "income.earned"]
    credits = [i for i in items if i["kind"] == "budget" and i["op"] == "credit"]
    assert [e["service"] for e in earned] == ["doubler", "orphan"]
    assert len(credits) == 1 and credits[0]["assembly_id"] == "seed-decider"
    assert credits[0]["reason"] == "income.earned:doubler" and credits[0]["amount"] == 2_500
    assert items.index(earned[0]) < items.index(credits[0])
    # the tick collects the same spool and books nothing twice
    rt.treasury.tick(rt.clock.now_ns)
    rt._collect_income()
    assert rt.budget.entitlement("seed-decider") == before + 2_500
    assert rt.treasury.pots()["earned_micro"] == 3_400 and invariant(rt)


def test_the_invariant_holds_at_every_ledgered_movement_and_at_the_end(world):
    rt, entries = world
    assert invariant(rt)
    for item in budget(entries, "commit"):
        assert item["amount"] == item["own"] + item["commons"]
    # each hold is settled or released exactly once, and nothing is held at the end
    holds = Counter(item["reservation_id"] for item in budget(entries, "hold"))
    settled = Counter(item["reservation_id"]
                      for op in ("commit", "release_hold") for item in budget(entries, op))
    assert holds == settled and rt.budget.holds() == 0
