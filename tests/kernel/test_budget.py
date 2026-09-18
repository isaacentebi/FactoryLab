"""C10: per-seat entitlements are classifications of one wallet's unlocked money."""

import pytest

from factorylab.kernel.budget import BudgetBook, SeatWallet, unlocked_balance
from factorylab.kernel.wallet import Infeasible, Wallet
from factorylab.world.metering import Meter


def invariant(book, wallet) -> bool:
    return (sum(book.entitlements().values()) + book.unallocated()
            == unlocked_balance(wallet) - book.holds()) and book.check_invariant()


def budget_items(ledger):
    return [i for i in ledger._recovery_items() if i["kind"] == "budget"]


def test_unlocked_balance_prefers_the_endowment_wallet_field(ledger, clock):
    wallet = Wallet(1_000, ledger, clock_ns=clock)
    assert unlocked_balance(wallet) == 1_000

    class Endowed:
        balance = 1_000
        unlocked = 400

    assert unlocked_balance(Endowed()) == 400


def test_genesis_grant_debit_credit_transfer_and_unallocated(ledger, clock):
    wallet = Wallet(1_000_000, ledger, clock_ns=clock)
    book = BudgetBook(wallet, ledger, clock_ns=clock, base_share="0.8")
    grants = book.genesis(["c", "a", "b"])
    # 0.8 of the pool, equally, in id order; the integer remainder stays unallocated
    assert grants == {"a": 266_666, "b": 266_666, "c": 266_666}
    assert book.unallocated() == 1_000_000 - 3 * 266_666
    assert invariant(book, wallet)
    with pytest.raises(Infeasible):
        book.genesis(["a"])

    book.grant("a", 100_000, "test")
    assert book.entitlement("a") == 366_666 and book.unallocated() == 100_002
    with pytest.raises(Infeasible):
        book.grant("a", 100_003, "beyond the pool")
    book.debit("a", 66_666, "test")
    assert book.entitlement("a") == 300_000 and book.unallocated() == 166_668
    with pytest.raises(Infeasible):
        book.debit("b", 266_667, "beyond the seat")
    book.transfer("a", "b", 50_000, "test")
    assert book.entitlement("a") == 250_000 and book.entitlement("b") == 316_666
    with pytest.raises(Infeasible):
        book.transfer("c", "a", 266_667, "beyond the source")
    with pytest.raises(ValueError):
        book.transfer("a", "a", 1, "same seat")
    # credit classifies money already in the wallet; it never exceeds the pool
    wallet.settle(20_000, "trade", "exchange_pnl")
    assert book.unallocated() == 186_668
    assert book.credit("c", 30_000, "return_paid_off") == 30_000
    assert book.credit("c", 1_000_000, "return_paid_off") == 156_668
    assert book.unallocated() == 0
    assert invariant(book, wallet)
    ops = [i["op"] for i in budget_items(ledger)]
    assert ops == ["genesis", "grant", "infeasible", "debit", "infeasible", "transfer",
                   "infeasible", "credit", "credit"]
    assert ledger.verify()


def test_charge_debits_a_loss_to_a_floor_of_zero_and_ledgers_the_rest_as_commons(ledger, clock):
    wallet = Wallet(100_000, ledger, clock_ns=clock)
    book = BudgetBook(wallet, ledger, clock_ns=clock)
    book.genesis(["a", "b"])  # 40 000 each, 20 000 unallocated
    wallet.settle(-50_000, "trade", "exchange_pnl")  # the loss is already the wallet's
    assert book.charge("a", 30_000, "return_paid_off") == 30_000
    assert book.entitlement("a") == 10_000 and book.unallocated() == 0
    assert book.charge("a", 25_000, "return_paid_off") == 10_000
    assert book.entitlement("a") == 0 and book.unallocated() == 10_000
    charges = [i for i in budget_items(ledger) if i["op"] == "charge"]
    assert [(c["own"], c["commons"]) for c in charges] == [(30_000, 0), (10_000, 15_000)]
    assert invariant(book, wallet) and ledger.verify()


def test_shared_wallet_spending_lands_on_the_pool_and_the_pool_is_signed(ledger, clock):
    wallet = Wallet(100_000, ledger, clock_ns=clock)
    book = BudgetBook(wallet, ledger, clock_ns=clock)
    book.genesis(["a", "b"])
    assert book.unallocated() == 20_000
    wallet.settle(-30_000, "trade", "exchange_pnl")  # a loss no seat is debited for
    assert book.unallocated() == -10_000 and invariant(book, wallet)
    with pytest.raises(Infeasible):
        book.grant("a", 1, "nothing to grant")
    assert book.credit("a", 5, "return_paid_off") == 0  # refused, ledgered with the request
    # a release first refills the hole, then splits what the pool really holds
    wallet.settle(50_000, "funding", "funding")  # stands in for a C1 tranche
    assert book.on_release(50_000) == {"a": 16_000, "b": 16_000}
    assert book.unallocated() == 40_000 - 32_000 and invariant(book, wallet)
    release = [i for i in budget_items(ledger) if i["op"] == "release"][0]
    assert release["amount"] == 50_000 and release["backed"] == 40_000


def test_on_release_splits_base_share_across_live_seats_only(ledger, clock):
    wallet = Wallet(100, ledger, clock_ns=clock)
    book = BudgetBook(wallet, ledger, clock_ns=clock, base_share="0.5")
    book.genesis(["a", "b", "c"])
    assert book.entitlements() == {"a": 16, "b": 16, "c": 16}
    book.retire("c", "vote")
    assert book.seats() == ("a", "b") and book.entitlement("c") == 0
    assert book.unallocated() == 100 - 32
    wallet.settle(40, "tranche", "funding")
    assert book.on_release(40) == {"a": 10, "b": 10}
    assert book.unallocated() == 108 - 20 and invariant(book, wallet)
    # a retired id re-registered as a next version is live again once endowed
    book.transfer("a", "c", 5, "trial:assembly")
    assert book.seats() == ("a", "b", "c") and book.entitlement("c") == 5


def test_seat_wallet_requires_both_the_wallet_and_the_seat(ledger, clock):
    wallet = Wallet(1_000, ledger, clock_ns=clock)
    book = BudgetBook(wallet, ledger, clock_ns=clock)
    book.grant("rich", 900, "test")
    book.grant("poor", 50, "test")
    rich, poor = SeatWallet(wallet, book, "rich"), SeatWallet(wallet, book, "poor")
    # the wallet can cover 100; the poor seat cannot
    with pytest.raises(Infeasible):
        poor.reserve(100, "h1", "model:m")
    assert book.entitlement("poor") == 50 and wallet.available == 1_000
    # the seat can cover 950; the wallet cannot (the rich seat holds only 900 of 1 000)
    book.grant("rich", 50, "test")
    wallet_hold = wallet.reserve(100, "other", "treasury:fees")
    with pytest.raises(Infeasible):
        rich.reserve(950, "h2", "model:m")
    wallet.release(wallet_hold)
    assert invariant(book, wallet)

    reservation = rich.reserve(300, "h3", "model:m")
    assert book.entitlement("rich") == 650 and book.holds() == 300
    assert wallet.available == 700 and invariant(book, wallet)
    rich.commit(reservation, 120)
    assert book.entitlement("rich") == 830 and wallet.balance == 880 and book.holds() == 0
    assert invariant(book, wallet)
    reservation = rich.reserve(200, "h4", "model:m")
    rich.release(reservation)
    assert book.entitlement("rich") == 830 and wallet.balance == 880 and book.holds() == 0
    assert book.last_hold("rich") == 200 and book.last_hold("poor") == 0
    reservation = rich.reserve(100, "h5", "model:m")
    rich.commit_uncertain(reservation)
    assert book.entitlement("rich") == 730 and wallet.balance == 780
    # a reported overrun beyond the hold is debited from the seat as well
    reservation = rich.reserve(100, "h6", "model:m")
    assert rich.commit_reported(reservation, 150) == 150
    assert book.entitlement("rich") == 580 and wallet.balance == 630
    assert invariant(book, wallet) and ledger.verify()
    hold = rich.reserve(10, "h7", "model:m")
    with pytest.raises(Infeasible):
        book.retire("rich", "vote")  # never while a hold is open
    rich.release(hold)
    ops = [i["op"] for i in budget_items(ledger)]
    assert ops.count("hold") == 5 and ops.count("commit") == 3 and ops.count("release_hold") == 2


def test_seat_wallet_protection_and_payer_hooks(ledger, clock):
    wallet = Wallet(10_000, ledger, clock_ns=clock)
    book = BudgetBook(wallet, ledger, clock_ns=clock)
    book.genesis(["seat", "parent"])  # 4 000 each, 2 000 unallocated
    protected = SeatWallet(wallet, book, "seat", protected=lambda h, r: 5_000 if h == "new" else 0)
    with pytest.raises(Infeasible):
        protected.reserve(4_001, "old", "model:m")
    reservation = protected.reserve(6_000, "new", "model:m")
    protected.commit(reservation, 5_000)
    # the seat paid what it had; the rest is commons spend, ledgered as such
    assert book.entitlement("seat") == 0 and wallet.balance == 5_000
    commit = [i for i in budget_items(ledger) if i["op"] == "commit"][-1]
    assert commit["own"] == 4_000 and commit["commons"] == 1_000
    assert book.unallocated() == 5_000 - 4_000 and invariant(book, wallet)

    child = SeatWallet(wallet, book, "seat", payer=lambda h: "parent" if h == "child" else None)
    reservation = child.reserve(1_000, "child", "model:m")
    assert book.entitlement("parent") == 3_000 and book.entitlement("seat") == 0
    child.commit(reservation, 400)
    assert book.entitlement("parent") == 3_600 and invariant(book, wallet)


def test_meter_runs_against_a_seat_wallet(ledger, clock):
    wallet = Wallet(1_000, ledger, clock_ns=clock)
    book = BudgetBook(wallet, ledger, clock_ns=clock)
    book.grant("seat", 300, "test")
    meter = Meter(SeatWallet(wallet, book, "seat"))
    metered = meter.run(handle="h", reason="model:m", ceiling=200, execute=lambda: "ok",
                        cost_of=lambda _: 40)
    assert metered.cost == 40 and book.entitlement("seat") == 260 and wallet.balance == 960
    from factorylab.world.metering import Infeasible as MeterInfeasible

    with pytest.raises(MeterInfeasible):
        meter.run(handle="h", reason="model:m", ceiling=261, execute=lambda: "ok",
                  cost_of=lambda _: 0)
    assert invariant(book, wallet)


def test_a_seat_the_book_never_endowed_is_refused_by_name_not_key_error(ledger, clock):
    """A seat instantiated past registration has no entitlement: its meter reports a
    refusal naming the seat, even when novelty protection would have covered the call,
    and the wallet never books a reservation for it."""
    wallet = Wallet(1_000, ledger, clock_ns=clock)
    book = BudgetBook(wallet, ledger, clock_ns=clock)
    book.grant("known", 100, "test")
    stray = SeatWallet(wallet, book, "stray", protected=lambda _h, _r: 500)
    with pytest.raises(Infeasible, match="'stray' has no entitlement"):
        stray.reserve(10, "h1", "model:m")
    assert wallet.available == 1_000 and book.holds() == 0
    refused = budget_items(ledger)[-1]
    assert refused["op"] == "infeasible" and refused["unknown_seat"] is True
    assert refused["assembly_id"] == "stray" and refused["entitlement"] == 0
    from factorylab.world.metering import Infeasible as MeterInfeasible

    with pytest.raises(MeterInfeasible, match="'stray' has no entitlement"):
        Meter(stray).run(handle="h2", reason="model:m", ceiling=10, execute=lambda: "ok",
                         cost_of=lambda _: 1)
    # once endowed it is an ordinary seat
    book.grant("stray", 50, "test")
    held = stray.reserve(10, "h3", "model:m")
    stray.commit(held, 4)
    assert book.entitlement("stray") == 46 and invariant(book, wallet)
    # a reservation this book never held is refused the same way, not with a KeyError
    foreign = wallet.reserve(5, "h4", "treasury:fees")
    with pytest.raises(Infeasible, match="not held by this budget book"):
        stray.release(foreign)


def test_state_round_trips_exactly(ledger, clock):
    wallet = Wallet(1_000, ledger, clock_ns=clock)
    book = BudgetBook(wallet, ledger, clock_ns=clock, base_share="0.6")
    book.genesis(["a", "b"])
    book.retire("b", "vote")
    seat = SeatWallet(wallet, book, "a")
    reservation = seat.reserve(50, "h", "model:m")
    state = book.state()
    assert state == {"base_share": "0.6", "entitlements": {"a": 300, "b": 0},
                     "holds": {reservation.id: ["a", 50]}, "retired": ["b"],
                     "last_holds": {"a": 50}, "uncertain": {},
                     "lineages": {"a": "a", "b": "b"},
                     "venue_claims": {}, "venue_booked": 0}
    other = BudgetBook(wallet, ledger, clock_ns=clock, base_share="0.6")
    other._restore_state(state)
    assert other.state() == state and other.entitlement("a") == 250 and other.seats() == ("a",)
    with pytest.raises(ValueError):
        BudgetBook(wallet, ledger, clock_ns=clock, base_share="0.5")._restore_state(state)
    with pytest.raises(ValueError):
        BudgetBook(wallet, ledger, base_share=0)


def test_settling_an_uncertain_bill_refunds_the_seat_what_it_paid(ledger, clock):
    """C10: the ceiling an uncertain bill charged comes back to the seat that paid it, as
    far as the seat paid; what the commons bridged stays with the pool."""
    wallet = Wallet(1_000, ledger, clock_ns=clock)
    book = BudgetBook(wallet, ledger, clock_ns=clock, base_share="0.6")
    book.genesis(["a", "b"])  # 300 each, 400 unallocated
    seat = SeatWallet(wallet, book, "a")
    reservation = seat.reserve(200, "h", "model:m")
    seat.commit_uncertain(reservation)
    assert book.entitlement("a") == 100 and wallet.balance == 800
    assert book.state()["uncertain"] == {reservation.id: ["a", 200]}
    assert seat.settle_uncertain(reservation.id, 30, balance_before=5_000, balance_after=4_970,
                                 spent_since_before=0) == 170
    assert wallet.balance == 970 and book.entitlement("a") == 270
    assert book.unallocated() == 400 and invariant(book, wallet)
    assert book.state()["uncertain"] == {} and wallet.uncertain_bills == {}
    item = next(i for i in budget_items(ledger) if i["op"] == "settle_uncertain")
    assert item["assembly_id"] == "a" and item["amount"] == 170 and item["own"] == 200
    assert item["released"] == 170 and item["reservation_id"] == reservation.id
    # A seat that only partly paid (protected exploration beyond its entitlement) is
    # refunded only its own part; the commons keeps the rest.
    explorer = SeatWallet(wallet, book, "b", protected=lambda _h, _r: 200)
    reservation = explorer.reserve(400, "h2", "model:m")
    explorer.commit_uncertain(reservation)
    assert book.entitlement("b") == 0
    assert book.state()["uncertain"] == {reservation.id: ["b", 300]}
    assert explorer.settle_uncertain(reservation.id, 50) == 350
    assert book.entitlement("b") == 300 and invariant(book, wallet) and ledger.verify()
    # The book's memory of who paid survives a checkpoint.
    reservation = seat.reserve(10, "h3", "model:m")
    seat.commit_uncertain(reservation)
    other = BudgetBook(wallet, ledger, clock_ns=clock, base_share="0.6")
    other._restore_state(book.state())
    assert other.state() == book.state()
    assert other._refund_uncertain(reservation.id, 10, "settle_uncertain") == 10
    # A bill the book never saw refunds no seat.
    assert book._refund_uncertain("wallet-99", 10, "settle_uncertain") == 0


# --- lineages (GPT-6 second reading, P2-07): replication never buys subsidy ---------


def test_a_release_is_split_per_lineage_so_nine_children_take_no_larger_share(ledger, clock):
    """The reviewer's case: a founder registers nine children; before, the next tranche
    was split per seat id and the founder's lineage took ten of eleven shares."""
    wallet = Wallet(1_000, ledger, clock_ns=clock)
    book = BudgetBook(wallet, ledger, clock_ns=clock, base_share="1")
    book.genesis(["founder", "other"])
    assert book.lineages() == {"founder": {"head": "founder", "seats": ["founder"]},
                               "other": {"head": "other", "seats": ["other"]}}
    for i in range(9):
        book.adopt(f"child-{i}", "founder", "trial:assembly")
        book.transfer("founder", f"child-{i}", 10, "trial:assembly")
    assert book.lineage("child-3") == "founder" and book.heads() == ("founder", "other")
    assert len(book.lineages()["founder"]["seats"]) == 10
    wallet.settle(200, "tranche", "funding")
    grants = book.on_release(200)
    # the same share the founder took before it had children; the children are
    # funded by the founder, not by the tranche
    assert grants == {"founder": 100, "other": 100}
    assert all(book.entitlement(f"child-{i}") == 10 for i in range(9))
    release = [i for i in budget_items(ledger) if i["op"] == "release"][0]
    assert release["lineages"] == {"founder": "founder", "other": "other"}
    adopted = [i for i in budget_items(ledger) if i["op"] == "lineage"]
    assert [(i["assembly_id"], i["lineage"], i["proposer"]) for i in adopted][:2] == [
        ("child-0", "founder", "founder"), ("child-1", "founder", "founder")]
    # a grandchild registered by a child belongs to the founder's lineage too
    book.adopt("grandchild", "child-0", "trial:assembly")
    assert book.lineage("grandchild") == "founder"
    # a seat this book never placed roots its own lineage; adopting it twice is a no-op
    assert book.adopt("stray", None, "trial:assembly") == "stray"
    assert book.adopt("stray", "founder", "again") == "stray"
    assert invariant(book, wallet) and ledger.verify()


def test_retiring_a_root_hands_the_lineage_to_its_oldest_live_child_or_the_pool(ledger, clock):
    wallet = Wallet(1_000, ledger, clock_ns=clock)
    book = BudgetBook(wallet, ledger, clock_ns=clock, base_share="1")
    book.genesis(["founder", "other"])
    book.adopt("elder", "founder", "trial:assembly")
    book.transfer("founder", "elder", 10, "trial:assembly")
    book.adopt("younger", "founder", "trial:assembly")
    book.transfer("founder", "younger", 10, "trial:assembly")
    book.retire("founder", "vote")
    assert book.lineages()["founder"] == {"head": "elder", "seats": ["elder", "younger"]}
    handoff = [i for i in budget_items(ledger) if i["op"] == "lineage_handoff"]
    assert handoff == [{**handoff[0], "lineage": "founder", "retired": "founder",
                        "head": "elder", "reason": "vote"}]
    wallet.settle(100, "tranche", "funding")
    assert book.on_release(100) == {"elder": 50, "other": 50}
    # a member that is not the head retires without a handoff
    book.retire("younger", "vote")
    assert len([i for i in budget_items(ledger) if i["op"] == "lineage_handoff"]) == 1
    # the last member retires: the lineage's future share stays in the pool
    book.retire("elder", "vote")
    assert "founder" not in book.lineages() and book.heads() == ("other",)
    assert [i for i in budget_items(ledger) if i["op"] == "lineage_handoff"][-1]["head"] is None
    wallet.settle(100, "tranche", "funding")
    assert book.on_release(100) == {"other": 100}
    # the retired root re-registered as a next version is live in its own lineage again
    book.transfer("other", "founder", 5, "trial:assembly")
    assert book.lineages()["founder"] == {"head": "founder", "seats": ["founder"]}
    state = book.state()
    assert state["lineages"] == {"founder": "founder", "other": "other", "elder": "founder",
                                 "younger": "founder"}
    restored = BudgetBook(wallet, ledger, clock_ns=clock, base_share="1")
    restored._restore_state(state)
    assert restored.lineages() == book.lineages() and restored.state() == state
    # a checkpoint from before lineages roots every seat on its own
    legacy = BudgetBook(wallet, ledger, clock_ns=clock, base_share="1")
    legacy._restore_state({k: v for k, v in state.items() if k != "lineages"})
    assert legacy.lineage("elder") == "elder" and set(legacy.lineages()) == {"founder", "other"}
    assert invariant(book, wallet) and ledger.verify()


# --- income (GPT-6 second reading, P2-05): service income is new money -------------


def test_earned_income_is_new_money_credited_whatever_the_pool_holds(ledger, clock):
    wallet = Wallet(100, ledger, clock_ns=clock)
    book = BudgetBook(wallet, ledger, clock_ns=clock, base_share="1")
    book.genesis(["seller"])
    assert book.entitlement("seller") == 100 and book.unallocated() == 0
    # the wallet books the receipt like venue P&L, then the seat is credited from it
    wallet.settle(40, "income:svc:0xtx", "income")
    book.earn("seller", 40, "income.earned:svc")
    assert wallet.balance == 140 and book.entitlement("seller") == 140
    assert book.unallocated() == 0 and invariant(book, wallet)
    # with a negative pool (shared spending overran it) the seller still gets it all
    wallet.settle(-30, "trade", "exchange_pnl")
    assert book.unallocated() == -30
    wallet.settle(25, "income:svc:0xtx2", "income")
    book.earn("seller", 25, "income.earned:svc")
    assert book.entitlement("seller") == 165 and book.unallocated() == -30
    assert invariant(book, wallet)
    item = [i for i in budget_items(ledger) if i["op"] == "income"][-1]
    assert item["amount"] == 25 and item["entitlement_after"] == {"seller": 165}
    with pytest.raises(ValueError):
        wallet.settle(-1, "income:svc:bad", "income")
    with pytest.raises(ValueError):
        wallet.settle(1, "h", "gift")
    assert wallet.check_conservation() and ledger.verify()
