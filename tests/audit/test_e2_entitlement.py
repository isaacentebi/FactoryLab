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
    root = rt.wallet.unlocked
    rt._collect_income()
    # GPT-6 second reading, P2-05: a receipt is new money. The root wallet grows by
    # it, the seller is credited from it, and the pool only sees the orphan's part.
    assert rt.wallet.unlocked == root + 3_400 and rt.wallet.check_conservation()
    assert rt.budget.entitlement("seed-decider") == before + 2_500
    assert rt.budget.unallocated() == pool + 900
    items = [i for i in rt.ledger.ledger._recovery_items()
             if i["kind"] in ("income.earned", "budget", "wallet.settle")]
    earned = [i for i in items if i["kind"] == "income.earned"]
    settled = [i for i in items if i["kind"] == "wallet.settle"]
    credits = [i for i in items if i["kind"] == "budget" and i["op"] == "income"]
    assert [e["service"] for e in earned] == ["doubler", "orphan"]
    assert [(s["reason"], s["amount"]) for s in settled] == [("income", 2_500), ("income", 900)]
    assert settled[0]["handle"] == "income:doubler:0x" + "ab" * 32
    assert len(credits) == 1 and credits[0]["assembly_id"] == "seed-decider"
    assert credits[0]["reason"] == "income.earned:doubler" and credits[0]["amount"] == 2_500
    assert items.index(earned[0]) < items.index(settled[0]) < items.index(credits[0])
    assert not any(i["kind"] == "budget" and i["op"] == "credit" for i in items)
    # the tick collects the same spool and books nothing twice
    rt.treasury.tick(rt.clock.now_ns)
    rt._collect_income()
    assert rt.budget.entitlement("seed-decider") == before + 2_500
    assert rt.wallet.unlocked == root + 3_400
    assert rt.treasury.pots()["earned_micro"] == 3_400 and invariant(rt)


def decision(rt, action, channel="verdict"):
    """Open a kernel decision for ``action`` so its handle is a real return."""
    from factorylab.kernel.queue import PropensityRecord

    handle = rt.queue.open(
        actor="test-router", event_id=f"test-{rt.n}-{action}", channel=channel,
        propensity=PropensityRecord((action,), (1.0,), action, 0, "test-router", "state"),
        deadline_ns=rt.clock.now_ns + 100_000_000_000, parent_handle=None,
        cost_ceiling=rt.wallet.available)
    rt.handle_to_assembly[handle] = action
    rt.consequences.start(handle, rt.n)
    rt.consequences.finish(handle, 1_000)
    return handle


def receipt(rt, tmp_path, service, micro, *, tx="0x" + "ab" * 32, owner="seed-decider"):
    from factorylab.world.income import IncomeSpool

    rt.tool_owner[service] = owner
    rt.treasury.income_spool = tmp_path / "income.jsonl"
    IncomeSpool(rt.treasury.income_spool).append({
        "service": service, "micro": micro, "tx": tx, "payer": "0x" + "cd" * 20,
        "program": service, "version": 1, "ts": 7})


def test_an_empty_pool_still_pays_the_seller_the_whole_receipt(tmp_path):
    from tests.conftest import make_runtime

    rt = make_runtime(balance=90_000_000)
    rt.budget.grant("seed-decider", rt.budget.unallocated(), "drain the pool")
    assert rt.budget.unallocated() == 0
    rt.wallet.settle(-1_000, "trade", "exchange_pnl")  # a shared loss: the pool is negative
    assert rt.budget.unallocated() == -1_000
    before, root = rt.budget.entitlement("seed-decider"), rt.wallet.unlocked
    receipt(rt, tmp_path, "doubler", 2_500)
    rt._collect_income()
    assert rt.budget.entitlement("seed-decider") == before + 2_500
    assert rt.wallet.unlocked == root + 2_500 and rt.budget.unallocated() == -1_000
    assert invariant(rt)


def test_a_settled_receipt_pays_off_the_return_that_registered_the_service(tmp_path):
    from tests.conftest import make_runtime

    rt = make_runtime(balance=90_000_000)
    handle = decision(rt, "seed-decider")
    assert rt.consequences.bind_service("doubler", handle, rt.n)
    receipt(rt, tmp_path, "doubler", 2_500)
    entitlement = rt.budget.entitlement("seed-decider")
    rt._collect_income()
    assert rt.budget.entitlement("seed-decider") == entitlement + 2_500
    rt.n += rt.ev.consequence_backstop_events
    rt._settle_due_forecasts()
    payoff = rt.consequences.payoff(handle)
    # 2 500 earned against 1 000 of cost, without a trade: a paid-off, unmarked outcome
    assert (payoff.y, payoff.net_micro, payoff.earned_micro, payoff.marked) == (1, 0, 2_500, 0)
    assert rt.consequences.counts()["paid_off"] == 1
    # the receipt was credited once, at receipt: the outcome credits nothing more
    assert rt.budget.entitlement("seed-decider") == entitlement + 2_500
    items = rt.ledger.ledger._recovery_items()
    kinds = [i["kind"] for i in items]
    assert kinds.index("consequence.service") < kinds.index("consequence.income")
    assert kinds.index("consequence.income") < kinds.index("consequence.outcome")
    income = next(i for i in items if i["kind"] == "consequence.income")
    assert (income["service"], income["handle"], income["micro"]) == ("doubler", handle, 2_500)
    assert invariant(rt)


def test_a_loss_realised_after_a_marked_outcome_is_charged_to_the_opener(tmp_path):
    """GPT-6 second reading, P2-06: a position marked at the backstop and closed later
    at a loss was booked to the root wallet but never to the opener's entitlement."""
    from fractions import Fraction

    from tests.conftest import make_runtime

    rt = make_runtime(balance=90_000_000)
    opener = "seed-decider"
    first = decision(rt, opener)
    rt.consequences.order_result(first, {"status": "filled", "order_id": "o1",
                                          "filled_size": "1"}, {}, rt.n)
    rt.consequences.observe("Fill", {"order_id": "o1", "coin": "BTC", "is_buy": True,
                                     "size": "1", "px": "100", "fee_usd": "0"}, rt.n)
    rt.consequences.observe("MarketMid", {"coin": "BTC", "mid": "100"}, rt.n)
    rt.n += rt.ev.consequence_backstop_events
    rt._settle_due_forecasts()
    payoff = rt.consequences.payoff(first)
    assert payoff.marked and payoff.net_micro == 0 and payoff.y == 0
    # the opener holds exactly $2; the mark moved nothing
    rt.budget.debit(opener, rt.budget.entitlement(opener) - 2_000_000, "test")
    assert rt.budget.entitlement(opener) == 2_000_000
    # liquidated at 98: the venue books the $2 loss to the wallet
    rt.wallet.settle(-2_000_000, "fill:liq", "exchange_pnl")
    rt.consequences.observe("Fill", {"order_id": "liq", "coin": "BTC", "is_buy": False,
                                     "size": "1", "px": "98", "fee_usd": "0",
                                     "liquidation": True}, rt.n)
    rt.n += 1
    rt._settle_due_forecasts()
    assert rt.budget.entitlement(opener) == 0  # charged, floored at zero
    assert rt.consequences.payoff(first) == payoff  # the score stays frozen
    items = rt.ledger.ledger._recovery_items()
    late = [i for i in items if i["kind"] == "consequence.late"]
    charges = [i for i in items if i["kind"] == "budget" and i["op"] == "charge"]
    assert late == [{**late[0], "handle": first, "micro": -2_000_000}]
    assert len(charges) == 1 and charges[0]["reason"] == "late_consequence"
    assert (charges[0]["amount"], charges[0]["own"], charges[0]["commons"]) == (
        2_000_000, 2_000_000, 0)
    assert items.index(late[0]) < items.index(charges[0])
    # booked once: another settlement pass charges nothing more
    rt.n += 1
    rt._settle_due_forecasts()
    assert len([i for i in rt.ledger.ledger._recovery_items()
                if i["kind"] == "consequence.late"]) == 1
    assert invariant(rt)

    # symmetric: a gain realised later by a distinct closer credits the opener its part
    second = decision(rt, opener)
    rt.consequences.order_result(second, {"status": "filled", "order_id": "o2",
                                           "filled_size": "1"}, {}, rt.n)
    rt.consequences.observe("Fill", {"order_id": "o2", "coin": "BTC", "is_buy": True,
                                     "size": "1", "px": "100", "fee_usd": "0"}, rt.n)
    rt.n += rt.ev.consequence_backstop_events
    rt._settle_due_forecasts()
    assert rt.consequences.payoff(second).marked
    closer = decision(rt, "seed-observer")
    rt.consequences.order_result(closer, {"status": "filled", "order_id": "c1",
                                            "filled_size": "1"}, {}, rt.n)
    rt.wallet.settle(20_000_000, "fill:c1", "exchange_pnl")
    rt.consequences.observe("Fill", {"order_id": "c1", "coin": "BTC", "is_buy": False,
                                     "size": "1", "px": "120", "fee_usd": "0"}, rt.n)
    rt.n += 1
    rt._settle_due_forecasts()
    opener_part = Fraction(20_000_000) * 100 / 220
    assert rt.budget.entitlement(opener) == opener_part.numerator // opener_part.denominator
    credits = [i for i in rt.ledger.ledger._recovery_items()
               if i["kind"] == "budget" and i["op"] == "credit" and i["assembly_id"] == opener]
    assert [(c["reason"], c["amount"]) for c in credits] == [
        ("late_consequence", opener_part.numerator // opener_part.denominator)]
    # a loss the opener cannot cover lands on the commons, ledgered as such
    rt.budget.debit(opener, rt.budget.entitlement(opener) - 500_000, "test")
    rt.wallet.settle(-2_000_000, "fill:liq2", "exchange_pnl")
    third = decision(rt, opener)
    rt.consequences.order_result(third, {"status": "filled", "order_id": "o3",
                                           "filled_size": "1"}, {}, rt.n)
    rt.consequences.observe("Fill", {"order_id": "o3", "coin": "ETH", "is_buy": True,
                                     "size": "1", "px": "100", "fee_usd": "0"}, rt.n)
    rt.consequences.observe("MarketMid", {"coin": "ETH", "mid": "100"}, rt.n)
    rt.n += rt.ev.consequence_backstop_events
    rt._settle_due_forecasts()
    rt.consequences.observe("Fill", {"order_id": "liq2", "coin": "ETH", "is_buy": False,
                                     "size": "1", "px": "98", "fee_usd": "0",
                                     "liquidation": True}, rt.n)
    rt.n += 1
    rt._settle_due_forecasts()
    charge = [i for i in rt.ledger.ledger._recovery_items()
              if i["kind"] == "budget" and i["op"] == "charge"][-1]
    assert (charge["amount"], charge["own"], charge["commons"]) == (2_000_000, 500_000, 1_500_000)
    assert rt.budget.entitlement(opener) == 0 and invariant(rt)


def test_children_join_their_proposers_lineage_in_the_scripted_world(world):
    """GPT-6 second reading, P2-07: releases are split per lineage, not per seat id.

    ``rt`` is restored from the run's checkpoint, so the lineages read here survived
    a resume."""
    rt, entries = world
    adopted = {i["assembly_id"]: i for i in budget(entries, "lineage")}
    assert "funding-watcher" in adopted and "composition-helper" in adopted
    seeds = {a.id for a in rt.m.assemblies}
    for child, item in adopted.items():
        assert item["proposer"] in seeds and item["lineage"] == item["proposer"]
        assert rt.budget.lineage(child) == item["proposer"]
    lineages = rt.budget.lineages()
    assert set(lineages) <= seeds
    assert all(row["head"] == lineage for lineage, row in lineages.items()
               if lineage not in rt.retired_assemblies)
    assert sum(len(row["seats"]) for row in lineages.values()) == len(rt.budget.seats())
    # the wake shows the lineages beside the seats
    from factorylab.runtime.wake import public_window_item

    view = public_window_item(rt, window=rt.stats.reserve_windows, event=rt.n)["entitlements"]
    assert {row["id"] for row in view["lineages"]} == set(lineages)
    assert sum(row["micro"] for row in view["lineages"]) == sum(
        rt.budget.entitlements().values())


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
