import random
from dataclasses import FrozenInstanceError, replace

import pytest

from factorylab.kernel.events import Bus
from factorylab.kernel.termination import Termination
from factorylab.kernel.wallet import DripSchedule, Infeasible, Reservation, Wallet


def test_reserve_commit_release_and_drip(ledger, clock):
    wallet = Wallet(100, ledger, DripSchedule(10, 10, 100, 130), clock_ns=clock)
    assert wallet.drip(99) == 0
    assert wallet.drip(100) == 10
    assert wallet.drip(100) == 0
    first = wallet.reserve(30, "h1", "model")
    second = wallet.reserve(20, "h2", "tool")
    third = wallet.reserve(10, "h3", "assembly")
    assert wallet.balance == 110 and wallet.available == 50
    wallet.commit(first, 21)
    wallet.release(second)
    wallet.settle(-5, "trade", "exchange_pnl")
    assert wallet.balance == 84 and wallet.available == 74
    assert wallet.drip(500) == 20
    assert wallet.drip(110) == 0
    assert wallet.balance == 104 and wallet.check_conservation() and ledger.verify()
    wallet.release(third)
    assert wallet.available == 104


@pytest.mark.parametrize("seed", range(4))
def test_invariant_1_randomized_conservation_after_every_operation(seed, ledger, clock):
    rng = random.Random(seed)
    wallet = Wallet(10_000, ledger, DripSchedule(7, 3, 100, 900), clock_ns=clock)
    holds = []
    expected = 10_000
    for _ in range(150):
        clock.now += rng.randrange(1, 5)
        action = rng.randrange(5)
        if action == 0:
            amount = rng.randrange(0, 100)
            if amount <= wallet.available:
                holds.append(wallet.reserve(amount, f"h-{clock.now}", "model"))
        elif action == 1 and holds:
            hold = holds.pop(rng.randrange(len(holds)))
            amount = rng.randrange(hold.amount + 1)
            wallet.commit(hold, amount)
            expected -= amount
        elif action == 2 and holds:
            wallet.release(holds.pop(rng.randrange(len(holds))))
        elif action == 3:
            delta = rng.randrange(-100, 101)
            wallet.settle(delta, "exchange", rng.choice(("exchange_pnl", "funding")))
            expected += delta
        else:
            expected += wallet.drip(clock.now)
        assert wallet.balance == expected
        assert wallet.available == expected - sum(hold.amount for hold in holds)
        assert wallet.check_conservation()
    assert ledger.verify()
    Termination(ledger=ledger, bus=Bus(ledger), clock_ns=clock).kill("audit")
    series = ledger.aggregate("wallet_series")["series"]
    assert series[-1]["balance"] == expected
    # Independently reconcile authenticated evidence after seal release.
    commits = drips = settlements = 0
    seq = 0
    while True:
        try:
            item = ledger.decrypt_item(seq)
        except IndexError:
            break
        if item["kind"].startswith("wallet."):
            assert {"kind", "amount", "balance_after", "handle", "reason", "ts"} <= item.keys()
        commits += item.get("amount", 0) if item["kind"] == "wallet.commit" else 0
        drips += item.get("amount", 0) if item["kind"] == "wallet.drip" else 0
        settlements += item.get("amount", 0) if item["kind"] == "wallet.settle" else 0
        seq += 1
    assert expected == 10_000 + drips + settlements - commits


def test_invariant_1_cannot_create_money_with_an_arbitrary_settlement(ledger):
    wallet = Wallet(100, ledger)
    for reason in ("reward", "donation", "drip", "initial", ""):
        with pytest.raises(ValueError, match="source"):
            wallet.settle(1000, "h", reason)
    with pytest.raises(TypeError):
        wallet.settle(1000, "h")
    with pytest.raises(AttributeError):
        wallet.balance = 1000
    assert wallet.balance == 100 and wallet.check_conservation()


def test_invariant_2_rejects_overreservation_and_logs_infeasibility(ledger, clock):
    wallet = Wallet(100, ledger, clock_ns=clock)
    wallet.reserve(80, "h1", "model")
    with pytest.raises(Infeasible):
        wallet.reserve(21, "h2", "model")
    Termination(ledger=ledger, bus=Bus(ledger), clock_ns=clock).kill("audit")
    assert ledger.decrypt_item(2)["kind"] == "wallet.infeasible"
    assert wallet.balance == 100 and wallet.available == 20


def test_invariant_2_rejects_unmetered_excess_reuse_and_forgery(ledger):
    wallet = Wallet(100, ledger)
    hold = wallet.reserve(20, "h", "model")
    for forged in (replace(hold, amount=100), Reservation(hold.id, 20, "h", "model")):
        with pytest.raises(Infeasible):
            wallet.commit(forged, 10)
    with pytest.raises(Infeasible, match="exceeds"):
        wallet.commit(hold, 21)
    wallet.commit(hold, 10)
    for operation in (lambda: wallet.commit(hold, 1), lambda: wallet.release(hold)):
        with pytest.raises(Infeasible):
            operation()
    assert wallet.balance == 90 and wallet.check_conservation()


@pytest.mark.parametrize("value", [True, 1.0, "1"])
def test_money_mutations_reject_non_integer_amounts(value, ledger):
    with pytest.raises(TypeError):
        Wallet(value, ledger)
    wallet = Wallet(100, ledger)
    hold = wallet.reserve(10, "h", "model")
    for operation in (
        lambda: wallet.reserve(value, "h", "model"),
        lambda: wallet.commit(hold, value),
        lambda: wallet.settle(value, "h", "funding"),
    ):
        with pytest.raises(TypeError):
            operation()
    assert wallet.balance == 100


@pytest.mark.parametrize("death_by", ["commit", "settle"])
def test_invariant_3_dead_wallet_cannot_debit_or_revive(death_by, ledger):
    wallet = Wallet(10, ledger, DripSchedule(100, 10, 0, 100))
    hold = wallet.reserve(10, "h", "model")
    if death_by == "commit":
        wallet.commit(hold, 10)
    else:
        wallet.settle(-11, "h", "exchange_pnl")
    assert wallet.dead
    for operation in (
        lambda: wallet.reserve(0, "h", "model"),
        lambda: wallet.commit(hold, 0),
        lambda: wallet.settle(100, "h", "funding"),
        lambda: wallet.drip(50),
    ):
        with pytest.raises(Infeasible):
            operation()
    assert wallet.check_conservation()


def test_invariant_1_single_wallet_and_invariant_10_immutable_drip(ledger):
    schedule = DripSchedule(1, 10, 0, 10)
    wallet = Wallet(10, ledger, schedule)
    with pytest.raises(ValueError, match="one wallet"):
        Wallet(1000, ledger)
    with pytest.raises(FrozenInstanceError):
        schedule.amount = 1000
    with pytest.raises(AttributeError):
        wallet.drip_schedule = DripSchedule(1000, 10, 0, 10)
    assert wallet.drip(100) == 1


@pytest.mark.parametrize("args", [(-1, 1, 0, 1), (1, 0, 0, 1), (1, 1, -1, 1), (1, 1, 2, 1)])
def test_invalid_schedule(args):
    with pytest.raises(ValueError):
        DripSchedule(*args)


def test_invariant_1_negative_reserve_and_commit_cannot_mint_money(ledger):
    wallet = Wallet(100, ledger)
    hold = wallet.reserve(50, "h", "model")
    with pytest.raises(ValueError):
        wallet.reserve(-10, "h", "model")
    with pytest.raises(ValueError):
        wallet.commit(hold, -10)
    assert wallet.balance == 100 and wallet.available == 50 and wallet.check_conservation()


def test_prior_exchange_loss_does_not_erase_a_reserved_bill(ledger):
    wallet = Wallet(100, ledger)
    hold = wallet.reserve(80, "h", "model")
    wallet.settle(-90, "trade", "exchange_pnl")
    with pytest.raises(Infeasible):
        wallet.reserve(0, "new", "model")
    wallet.commit(hold, 80)
    assert wallet.balance == -70 and wallet.dead and wallet.check_conservation()


def test_zero_initial_balance_is_dead_even_with_due_drip(ledger):
    wallet = Wallet(0, ledger, DripSchedule(100, 10, 0, 100))
    assert wallet.dead
    with pytest.raises(Infeasible):
        wallet.drip(0)


def test_release_after_exhaustion_preserves_money_and_audit(ledger):
    wallet = Wallet(10, ledger)
    hold = wallet.reserve(10, "h", "model")
    wallet.settle(-10, "trade", "exchange_pnl")
    wallet.release(hold)
    assert wallet.balance == 0 and wallet.available == 0 and wallet.check_conservation()
