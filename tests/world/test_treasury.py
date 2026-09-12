from copy import deepcopy
from decimal import Decimal
from types import SimpleNamespace

import pytest

from factorylab.kernel.ledger import Ledger
from factorylab.kernel.wallet import Wallet
from factorylab.runtime.live import Reconciler
from factorylab.world.evm import Pending
from factorylab.world.treasury import FakeRail, FakeTreasury, Treasury, provider_pots


def setup():
    ledger = Ledger(clock_ns=lambda: 0)
    records = []
    append = ledger.append

    def record(item):
        records.append(deepcopy(item))
        return append(item)

    ledger.append = record
    wallet = Wallet(100_000_000, ledger, clock_ns=lambda: 0)
    return ledger, wallet, records


def test_scripted_transfer_is_pending_until_next_tick_and_conserves_principal():
    ledger, wallet, records = setup()
    treasury = FakeTreasury(ledger, wallet)
    wallet.bind_pots(treasury.pots)
    before = wallet.pots()
    result = treasury.transfer("to_reserve", "10", handle="a", now_ns=1)
    assert result["status"] == "submitted"
    assert wallet.balance == 100_000_000
    assert wallet.available == 89_990_000
    assert wallet.pots()["reserve"] == before["reserve"] == 0
    assert wallet.pots()["pending"] and not wallet.pots()["complete"]
    assert treasury.transfer("to_reserve", "10", handle="b", now_ns=2)["status"] == "refused"
    confirmed = treasury.tick(2)
    assert confirmed[0]["status"] == "confirmed"
    assert wallet.pots()["reserve"] == 9_990_000
    assert wallet.pots()["venue"] == 90_000_000
    assert wallet.balance == wallet.available == 99_990_000
    assert wallet.check_conservation()
    assert treasury.tick(3) == []
    assert wallet.balance == 99_990_000
    assert sum(i["kind"] == "treasury.confirmed" for i in records) == 1
    treasury.transfer("to_venue", "5", handle="c", now_ns=3)
    treasury.tick(4)
    assert wallet.pots()["reserve"] == 4_990_000
    assert wallet.pots()["venue"] == 94_990_000
    assert wallet.balance == 99_980_000


@pytest.mark.parametrize("usd", [True, 5.0, "NaN", "Infinity", "-1", "0", "0.0000001", "4", "101"])
def test_invalid_amounts_never_submit_or_reserve(usd):
    ledger, wallet, records = setup()
    treasury = FakeTreasury(ledger, wallet)
    assert treasury.transfer("to_reserve", usd, handle="a", now_ns=1)["status"] == "refused"
    assert wallet.available == wallet.balance == 100_000_000
    assert not any(i["kind"] == "treasury.submitted" for i in records)


def test_source_pot_shortage_cannot_be_hidden_by_other_pots():
    ledger, wallet, _ = setup()
    treasury = FakeTreasury(ledger, wallet)
    result = treasury.transfer("to_venue", "5", handle="a", now_ns=1)
    assert result["status"] == "refused" and "source pot" in result["error"]


class DelayedRail(FakeRail):
    def __init__(self, wallet, records):
        super().__init__(wallet)
        self.records, self.sent = records, []
        self.ready = False

    def send(self, step, reference):
        assert any(
            i["kind"] in {"treasury.submitted", "treasury.step_submitted"}
            and reference in i["tx_refs"]
            for i in self.records
        )
        self.sent.append(deepcopy(reference))
        raise Pending("simulated transport timeout")

    def poll(self, step, state):
        return super().poll(step, state) if self.ready else None


def test_timeout_holds_principal_and_reconciles_original_reference_once():
    ledger, wallet, records = setup()
    rail = DelayedRail(wallet, records)
    treasury = Treasury(ledger, wallet, rail, fee_ceiling_micro=10_000)
    result = treasury.transfer("to_reserve", "10", handle="a", now_ns=1)
    assert result["status"] == "submitted"
    assert treasury.tick(2) == []
    assert wallet.available == 89_990_000
    original = rail.sent[0]
    rail.ready = True
    treasury.tick(3)
    assert treasury.state["reference"] == original
    assert wallet.balance == 99_990_000
    treasury.tick(4)
    assert wallet.balance == 99_990_000


def test_ledger_failure_prevents_broadcast_and_releases_unsubmitted_holds():
    ledger, wallet, records = setup()
    rail = DelayedRail(wallet, records)
    treasury = Treasury(ledger, wallet, rail)
    append = ledger.append

    def fail_submission(item):
        if item["kind"] == "treasury.submitted":
            raise OSError("disk full")
        return append(item)

    ledger.append = fail_submission
    with pytest.raises(OSError):
        treasury.transfer("to_reserve", "10", handle="a", now_ns=1)
    assert rail.sent == []
    assert wallet.available == wallet.balance


class MultiStepRail(FakeRail):
    def __init__(self, wallet):
        super().__init__(wallet)
        self.fail_mint = False
        self.attestation_ready = False

    def plan(self, direction):
        return ("burn_arb", "mint_base")

    def prepare(self, step, state, gas_spent):
        if step == "mint_base" and not self.attestation_ready:
            raise Pending("await attestation")
        return {"network": "scripted", "tx_hash": step}

    def poll(self, step, state):
        return {
            "confirmed": not (step == "mint_base" and self.fail_mint),
            "principal_moved": step == "burn_arb",
            "fee_micro": 10_000,
            "received_micro": state["received_micro"],
            "evidence": state["reference"],
        }


def test_attestation_wait_does_not_recharge_burn_and_failure_quarantines_principal():
    ledger, wallet, records = setup()
    rail = MultiStepRail(wallet)
    treasury = Treasury(ledger, wallet, rail, fee_ceiling_micro=30_000)
    treasury.transfer("to_reserve", "10", handle="a", now_ns=1)
    treasury.tick(2)
    assert wallet.balance == 99_990_000
    assert treasury.state["reference"] is None
    for tick in range(3, 8):
        treasury.tick(tick)
    assert wallet.balance == 99_990_000
    rail.attestation_ready = rail.fail_mint = True
    treasury.tick(8)
    treasury.tick(9)
    assert treasury.state["status"] == "stranded"
    assert wallet.balance == 99_980_000 and wallet.available == 89_980_000
    assert treasury.pots()["pending"]
    assert treasury.transfer("to_reserve", "10", handle="b", now_ns=10)["status"] == "refused"
    assert any(
        i["kind"] == "treasury.failed" and i["stranded_micro"] == 10_000_000 for i in records
    )


def test_native_gas_is_a_booked_fee_without_destroying_unspent_usdc():
    ledger, wallet, records = setup()
    rail = MultiStepRail(wallet)
    poll = rail.poll
    rail.poll = lambda step, state: {
        **poll(step, state), "fee_micro": 10_000, "wallet_fee_micro": 0,
        "gas_fee_wei": 123, "chain_key": "hyper",
    }
    treasury = Treasury(ledger, wallet, rail, fee_ceiling_micro=30_000)
    treasury.transfer("to_reserve", "10", handle="a", now_ns=1)
    treasury.tick(2)
    assert treasury.state["fees_micro"] == 10_000
    assert treasury.gas_spent == {"hyper": 123}
    assert wallet.balance == 100_000_000 and wallet.check_conservation()
    assert wallet.available == 89_980_000
    assert any(i["kind"] == "treasury.step_confirmed" and
               i["outcome"]["wallet_fee_micro"] == 0 for i in records)
    rail.attestation_ready = True
    treasury.tick(3)
    treasury.tick(4)
    assert wallet.balance == wallet.available == 100_000_000
    assert treasury.pots()["total_micro"] == wallet.balance
    assert treasury.state["fees_micro"] == 20_000 and treasury.gas_spent["hyper"] == 246


def test_pots_are_read_only_and_never_create_kernel_money():
    ledger, wallet, _ = setup()
    treasury = FakeTreasury(ledger, wallet)
    wallet.bind_pots(treasury.pots)
    result = wallet.pots()
    result["venue"] = 10**20
    assert wallet.pots()["venue"] == wallet.balance == 100_000_000
    with pytest.raises(ValueError):
        wallet.bind_pots(lambda: {})


def test_reconciler_half_dollar_tolerance_and_incomplete_views():
    ledger, wallet, records = setup()
    pots = {
        "venue": 60_000_000,
        "reserve": 20_000_000,
        "seed": 10_000_000,
        "sellers": {"venice": 9_500_000},
        "pending": False,
    }
    snap = Reconciler.snapshot(wallet.balance, None, None, pots_view=pots, ledger=ledger)
    assert snap["within_tolerance"] is True
    assert not any(i["kind"] == "reconcile.drift" for i in records)
    pots["sellers"]["venice"] -= 1
    snap = Reconciler.snapshot(wallet.balance, None, None, pots_view=pots, ledger=ledger)
    assert snap["within_tolerance"] is False
    assert records[-1]["kind"] == "reconcile.drift"
    assert wallet.balance == 100_000_000
    pots["reserve"] = None
    assert Reconciler.snapshot(wallet.balance, None, None, pots_view=pots)["pots_micro"] is None
    pots["reserve"], pots["pending"] = 20_000_000, True
    assert (
        Reconciler.snapshot(wallet.balance, None, None, pots_view=pots)["within_tolerance"] is None
    )


def test_seller_seed_and_reserve_are_not_double_counted():
    provider = SimpleNamespace(
        openrouter=SimpleNamespace(balance_micro=lambda: 10),
        venice=SimpleNamespace(balance_micro=lambda: 20),
    )
    assert provider_pots(provider) == (10, {"venice": 20})
    venice = SimpleNamespace(name="venice", balance_micro=lambda: 20)
    assert provider_pots(venice) == (0, {"venice": 20})
    assert Decimal("0.000020") * 1_000_000 == 20
