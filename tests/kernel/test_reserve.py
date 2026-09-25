from dataclasses import replace
from decimal import localcontext

import pytest

from factorylab.kernel.registry import Registry
from factorylab.kernel.reserve import NoveltyReserve
from factorylab.kernel.wallet import Infeasible, Wallet


def test_invariant_5_incumbents_cannot_consume_novelty_share(ledger, clock, contract_factory):
    history = {"incumbent"}
    reserve = NoveltyReserve(0.25, has_history=history.__contains__, ledger=ledger, clock_ns=clock)
    reserve.open_window(100, 100)
    with pytest.raises(Infeasible, match="history"):
        reserve.reserve_for(contract_factory(id="incumbent"), 1)
    assert reserve.remaining() == 25
    reserve.reserve_for(contract_factory(), 20)
    with pytest.raises(Infeasible):
        reserve.reserve_for(contract_factory(id="other"), 6)
    assert reserve.remaining() == 5


def test_invariant_5_windows_cannot_be_reopened_for_extra_budget(
    ledger,
    clock,
    contract_factory,
):
    """The share is a flow (time audit T6): however often windows open, the reserve never
    holds more than one flow period's share, and a window cannot open twice at an instant."""
    from fractions import Fraction

    reserve = NoveltyReserve(0.25, has_history=lambda _: False, ledger=ledger, clock_ns=clock)
    reserve.open_window(100, 100, accrued=Fraction(1, 2))
    assert reserve.remaining() == 12  # half a period of a 25 share
    contract = contract_factory()
    receipt = reserve.reserve_for(contract, 5)
    with pytest.raises(Infeasible, match="overlap"):
        reserve.open_window(100, 1000)
    with pytest.raises(Infeasible, match="overlap"):
        reserve.open_window(99, 1000)
    # Reopening at once, over and over, never raises the entitlement past one share.
    for now in range(101, 111):
        reserve.open_window(now, 100, accrued=Fraction(1))
        assert reserve.remaining() == 25
    reserve.open_window(111, 100, accrued=Fraction(0))
    assert reserve.remaining() == 25  # carried, never more than the cap
    # A receipt from an earlier window does not survive the next opening.
    with pytest.raises(PermissionError):
        Registry(ledger).register(contract, "h", receipt)
    reserve.open_window(112, 20, accrued=Fraction(1))
    assert reserve.remaining() == 5  # the cap follows the budget the window opens on
    for bad in (Fraction(-1, 2), Fraction(3, 2), 0.5):
        with pytest.raises(ValueError, match="accrued"):
            reserve.open_window(200, 100, accrued=bad)
    with pytest.raises(AttributeError):
        reserve.share = 0


def test_novelty_reservations_are_not_money_or_wallet_holds(ledger, clock, contract_factory):
    wallet = Wallet(100, ledger, clock_ns=clock)
    reserve = NoveltyReserve(0.1, has_history=lambda _: False, ledger=ledger, clock_ns=clock)
    reserve.open_window(100, 100)
    receipt = reserve.reserve_for(contract_factory(), 10)
    with pytest.raises(Infeasible):
        wallet.commit(receipt, 5)
    assert wallet.balance == 100 and wallet.check_conservation()


def test_registration_rechecks_history_and_rejects_receipt_copy(ledger, clock, contract_factory):
    history = set()
    reserve = NoveltyReserve(0.5, has_history=history.__contains__, ledger=ledger, clock_ns=clock)
    reserve.open_window(100, 100)
    contract = contract_factory()
    receipt = reserve.reserve_for(contract, 10)
    registry = Registry(ledger)
    with pytest.raises(PermissionError):
        registry.register(contract, "h", replace(receipt))
    history.add(contract.id)
    with pytest.raises(Infeasible):
        registry.register(contract, "h", receipt)


def test_reserve_arithmetic_uses_integers_even_at_low_decimal_precision(ledger, clock):
    budget = 10**60 + 7
    with localcontext() as context:
        context.prec = 2
        reserve = NoveltyReserve(0.125, has_history=lambda _: False, ledger=ledger, clock_ns=clock)
        reserve.open_window(100, budget)
    assert reserve.remaining() == budget // 8


@pytest.mark.parametrize("share", [0, -0.1, 1.1, float("nan"), True])
def test_reserve_cannot_be_abolished_or_malformed(share, ledger):
    with pytest.raises((ValueError, TypeError)):
        NoveltyReserve(share, has_history=lambda _: False, ledger=ledger)


def protected_wallet(ledger, clock):
    wallet = Wallet(100, ledger, clock_ns=clock)
    reserve = NoveltyReserve(0.25, has_history=lambda _: False,
                             ledger=ledger, clock_ns=clock)
    wallet.bind_novelty(reserve, lambda handle, reason: handle == "new" and reason == "model")
    reserve.open_window(clock.now, 100)
    return wallet, reserve


@pytest.mark.parametrize("reason", ["model", "tool", "order", "treasury:principal"])
def test_protected_money_is_not_available_to_historied_spending(ledger, clock, reason):
    wallet, reserve = protected_wallet(ledger, clock)
    assert wallet.available == 75
    hold = wallet.reserve(75, "incumbent", reason)
    wallet.commit(hold, 75)
    with pytest.raises(Infeasible):
        wallet.reserve(1, "incumbent", reason)
    assert reserve.remaining() == 25 and wallet.balance == 25
    # A fresh actor cannot launder protected compute into tool calls or orders either.
    with pytest.raises(Infeasible):
        wallet.reserve(1, "new", "tool")


def test_unused_compute_ceiling_returns_to_protection_not_to_incumbents(ledger, clock):
    wallet, reserve = protected_wallet(ledger, clock)
    hold = wallet.reserve(30, "new", "model")
    assert reserve.remaining() == 0 and wallet.available == 70
    wallet.commit(hold, 10)
    assert reserve.remaining() == 15 and wallet.available == 75
    hold = wallet.reserve(15, "new", "model")
    wallet.release(hold)
    assert reserve.remaining() == 15 and wallet.available == 75
    assert wallet.check_conservation()


def test_compute_hold_prevents_double_allocation_to_registration(ledger, clock, contract_factory):
    wallet, reserve = protected_wallet(ledger, clock)
    hold = wallet.reserve(25, "new", "model")
    with pytest.raises(Infeasible):
        reserve.reserve_for(contract_factory(), 1)
    wallet.release(hold)
    assert reserve.reserve_for(contract_factory(), 25).amount == 25


def test_old_compute_refund_cannot_replenish_the_next_window(ledger, clock):
    wallet, reserve = protected_wallet(ledger, clock)
    hold = wallet.reserve(25, "new", "model")
    clock.now += 10
    reserve.open_window(clock.now, 20)
    wallet.release(hold)
    assert reserve.remaining() == 5
    assert wallet.balance == 100 and wallet.available == 95


def test_protection_changes_only_after_wallet_ledger_item(ledger, clock, monkeypatch):
    wallet, reserve = protected_wallet(ledger, clock)
    append = ledger.append

    def fail(_):
        raise RuntimeError("ledger unavailable")

    with monkeypatch.context() as patch:
        patch.setattr(ledger, "append", fail)
        with pytest.raises(RuntimeError):
            wallet.reserve(25, "new", "model")
    assert wallet.available == 75 and reserve.remaining() == 25
    hold = wallet.reserve(25, "new", "model")
    for operation in (lambda: wallet.commit(hold, 10), lambda: wallet.release(hold)):
        with monkeypatch.context() as patch:
            patch.setattr(ledger, "append", fail)
            with pytest.raises(RuntimeError):
                operation()
        assert wallet.balance == 100 and reserve.remaining() == 0
    entries = []

    def capture(entry):
        entries.append(entry)
        # Every record precedes the change it records: the reserve moves last.
        assert reserve.remaining() == 0
        if entry["kind"] == "wallet.commit":
            assert wallet.balance == 100
        return append(entry)

    monkeypatch.setattr(ledger, "append", capture)
    wallet.commit(hold, 10)
    assert [e["kind"] for e in entries] == ["wallet.commit", "novelty.compute"]
    # The niche's use is recorded (ruling R5): what the hold was entitled to and used.
    assert entries[1]["protected"] == 25 and entries[1]["used"] == 10
    assert entries[1]["handle"] == "new" and entries[1]["reason"] == "model"
    assert reserve.remaining() == 15
