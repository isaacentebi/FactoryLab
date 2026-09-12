from dataclasses import replace
from decimal import localcontext

import pytest

from factorylab.kernel.registry import Registry
from factorylab.kernel.reserve import NoveltyReserve
from factorylab.kernel.wallet import Infeasible, Wallet


def test_invariant_5_incumbents_cannot_consume_novelty_share(ledger, clock, contract_factory):
    history = {"incumbent"}
    reserve = NoveltyReserve(
        0.25, 10, has_history=history.__contains__, ledger=ledger, clock_ns=clock
    )
    reserve.open_window(100, 100)
    with pytest.raises(Infeasible, match="history"):
        reserve.reserve_for(contract_factory(id="incumbent"), 1)
    assert reserve.remaining() == 25
    reserve.reserve_for(contract_factory(), 20)
    with pytest.raises(Infeasible):
        reserve.reserve_for(contract_factory(id="other"), 6)
    assert reserve.remaining() == 5


def test_invariant_5_windows_expire_and_cannot_be_reopened_for_extra_budget(
    ledger,
    clock,
    contract_factory,
):
    reserve = NoveltyReserve(0.25, 10, has_history=lambda _: False, ledger=ledger, clock_ns=clock)
    reserve.open_window(100, 100)
    contract = contract_factory()
    receipt = reserve.reserve_for(contract, 5)
    with pytest.raises(Infeasible, match="overlap"):
        reserve.open_window(100, 1000)
    clock.now = 110
    assert reserve.remaining() == 0
    with pytest.raises(Infeasible):
        reserve.reserve_for(contract, 1)
    with pytest.raises(PermissionError):
        Registry(ledger).register(contract, "h", receipt)
    reserve.open_window(110, 20)
    assert reserve.remaining() == 5
    with pytest.raises(PermissionError):
        Registry(ledger).register(contract, "h", receipt)
    with pytest.raises(AttributeError):
        reserve.share = 0
    with pytest.raises(AttributeError):
        reserve.window_ns = 1


def test_novelty_reservations_are_not_money_or_wallet_holds(ledger, clock, contract_factory):
    wallet = Wallet(100, ledger, clock_ns=clock)
    reserve = NoveltyReserve(0.1, 10, has_history=lambda _: False, ledger=ledger, clock_ns=clock)
    reserve.open_window(100, 100)
    receipt = reserve.reserve_for(contract_factory(), 10)
    with pytest.raises(Infeasible):
        wallet.commit(receipt, 5)
    assert wallet.balance == 100 and wallet.check_conservation()


def test_registration_rechecks_history_and_rejects_receipt_copy(ledger, clock, contract_factory):
    history = set()
    reserve = NoveltyReserve(
        0.5, 10, has_history=history.__contains__, ledger=ledger, clock_ns=clock
    )
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
        reserve = NoveltyReserve(
            0.125, 10, has_history=lambda _: False, ledger=ledger, clock_ns=clock
        )
        reserve.open_window(100, budget)
    assert reserve.remaining() == budget // 8


@pytest.mark.parametrize("share", [0, -0.1, 1.1, float("nan"), True])
def test_reserve_cannot_be_abolished_or_malformed(share, ledger):
    with pytest.raises((ValueError, TypeError)):
        NoveltyReserve(share, 10, has_history=lambda _: False, ledger=ledger)


def protected_wallet(ledger, clock):
    wallet = Wallet(100, ledger, clock_ns=clock)
    reserve = NoveltyReserve(0.25, 10, has_history=lambda _: False,
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
        assert reserve.remaining() == 0 and wallet.balance == 100
        return append(entry)

    monkeypatch.setattr(ledger, "append", capture)
    wallet.commit(hold, 10)
    assert entries[0]["kind"] == "wallet.commit"
    assert reserve.remaining() == 15
