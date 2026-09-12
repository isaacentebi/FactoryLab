from dataclasses import replace
from types import SimpleNamespace

import pytest

from factorylab.kernel.ledger import Ledger
from factorylab.kernel.reserve import NoveltyReserve
from factorylab.kernel.wallet import Infeasible, Wallet
from factorylab.world.metering import Meter
from factorylab.world.models import ModelResponse
from tests.runtime.test_fa_defects import make_runtime


@pytest.mark.parametrize("initial,ceiling,actual", [
    (1000, 100, 1300), (100, 100, 130), (100, 0, 130), (1000, 100, 1000),
])
def test_reported_overrun_is_fully_debited_before_return(initial, ceiling, actual):
    ledger = Ledger()
    wallet = Wallet(initial, ledger)
    result = Meter(wallet).run(handle="caller", reason="model:vendor", ceiling=ceiling,
                               execute=lambda: "paid response", cost_of=lambda _: actual)
    assert result.cost == actual and result.overrun == actual - ceiling
    assert wallet.balance == initial - actual and wallet.dead
    assert wallet.available == wallet.balance and wallet.check_conservation()
    evidence = ledger._recovery_items()
    assert evidence[-2]["kind"] == "metering.overrun"
    assert evidence[-2]["handle"] == "caller" and evidence[-2]["overrun"] == actual - ceiling
    assert evidence[-1]["kind"] == "wallet.commit" and evidence[-1]["amount"] == actual
    assert sum(ledger.aggregate("spend_by_capability")["spend"].values()) == actual


def test_overrun_does_not_relax_normal_commit_or_reservation_identity():
    wallet = Wallet(1000, Ledger())
    held = wallet.reserve(100, "caller", "model:vendor")
    with pytest.raises(Infeasible):
        wallet.commit(held, 130)
    with pytest.raises(Infeasible):
        wallet.commit_reported(replace(held), 130)
    wallet.commit_reported(held, 130)
    with pytest.raises(Infeasible):
        wallet.commit_reported(held, 130)
    assert wallet.balance == 870


def test_reported_bill_preserves_novelty_protection_and_refunds():
    ledger = Ledger()
    wallet = Wallet(1000, ledger)
    reserve = NoveltyReserve(.1, 100, has_history=lambda _: False,
                             ledger=ledger, clock_ns=lambda: 0)
    wallet.bind_novelty(reserve, lambda handle, _: handle == "fresh")
    reserve.open_window(0, 1000)
    bill = Meter(wallet).run(handle="incumbent", reason="model:vendor", ceiling=800,
                             execute=lambda: "paid", cost_of=lambda _: 850)
    assert bill.cost == 850 and wallet.balance == 150 and reserve.remaining() == 100
    with pytest.raises(Infeasible):
        wallet.reserve(51, "incumbent", "model:vendor")
    hold = wallet.reserve(100, "fresh", "model:vendor")
    wallet.commit_reported(hold, 50)
    assert reserve.remaining() == 50 and wallet.available == 50
    assert wallet.check_conservation()


@pytest.mark.parametrize("invalid", [
    (1.0, "fill2", "exchange_pnl"), (True, "fill2", "exchange_pnl"),
    (100, "fill2", "reward"), (100, "", "funding"),
])
def test_invalid_batch_cannot_partially_write_or_create_money(invalid):
    ledger = Ledger()
    wallet = Wallet(10, ledger)
    before, evidence = wallet.state(), ledger._recovery_items()
    with pytest.raises((TypeError, ValueError)):
        wallet.settle_batch([(-20, "fill1", "exchange_pnl"), invalid])
    assert wallet.state() == before and ledger._recovery_items() == evidence


def test_runtime_records_overrun_and_cannot_execute_its_order_after_exhaustion(monkeypatch):
    rt = make_runtime(balance=1_000_000)
    monkeypatch.setattr(rt.provider.target, "complete", lambda req: ModelResponse(
        req.model_id, '{"action":"order","coin":"BTC","size":"1"}', 1, 1,
        "stop", cost_micro=1_100_000,
    ))
    monkeypatch.setattr(rt.exchange.target, "place", lambda *_: pytest.fail("order after death"))
    req = rt._request("caller", "test", {}, {}, 100, "test")
    ret = rt._invoke("seed-decider", req, "producer")
    assert ret.cost == 1_100_000 and rt.wallet.balance == -100_000
    rt._execute_outputs(ret)
    assert rt._check_termination() and rt.termination.reason == "balance_zero"
    evidence = rt.ledger._recovery_items()
    bill = next(i for i in evidence if i["kind"] == "metering.overrun")
    observed = next(i for i in evidence if i["kind"] == "compute.overrun")
    invocation = next(i for i in evidence if i["kind"] == "invocation")
    assert bill["seq"] < observed["seq"] < invocation["seq"]
    assert observed["overrun"] == bill["overrun"]


def test_fatal_vote_overrun_cannot_invoke_another_seat(monkeypatch):
    rt = make_runtime(balance=1_000_000)
    calls = []

    def provider(req):
        calls.append(req)
        return ModelResponse(req.model_id, '{"vote":true,"reason":"test"}', 1, 1,
                             "stop", cost_micro=1_100_000)

    monkeypatch.setattr(rt.provider.target, "complete", provider)
    monkeypatch.setattr(rt.charter_book, "vote", lambda *_: None)
    monkeypatch.setattr(rt.charter_book, "tally", lambda *_: "failed")
    am = SimpleNamespace(id="test", proposed_prices=(), add=(), replace=(), remove=(),
                         predicted_effect="", tick_interval=None)
    committee = SimpleNamespace(seats=[("seat1", "seed-decider"), ("seat2", "seed-decider")])
    rt._hold_vote(am, committee)
    assert len(calls) == 1 and rt.wallet.balance == -100_000
    assert rt._compute_routed and rt._check_termination()


def test_batch_cash_recovery_cannot_reverse_death_even_after_restore():
    wallet = Wallet(10, Ledger())
    wallet.settle_batch([(-20, "fill1", "exchange_pnl"), (100, "fill2", "exchange_pnl")])
    assert wallet.balance == 90 and wallet.dead and wallet.check_conservation()
    restored = Wallet(10, Ledger())
    restored._restore_state(wallet.state())
    assert restored.balance == 90 and restored.dead
    with pytest.raises(Infeasible):
        restored.reserve(1, "after-death", "model:vendor")


def test_failed_batch_append_keeps_memory_unchanged(monkeypatch):
    ledger = Ledger()
    wallet = Wallet(10, ledger)
    before, append = wallet.state(), ledger.append

    def fail(entry):
        if entry.get("handle") == "fill2":
            raise OSError("synthetic failure")
        return append(entry)

    monkeypatch.setattr(ledger, "append", fail)
    with pytest.raises(OSError):
        wallet.settle_batch([(-20, "fill1", "exchange_pnl"), (-20, "fill2", "exchange_pnl")])
    assert wallet.state() == before
