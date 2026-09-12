from factorylab.kernel.ledger import Ledger
from factorylab.kernel.wallet import Wallet
from factorylab.world.treasury import Treasury
from tests.world.test_treasury import MultiStepRail


def test_trading_shock_during_transfer_is_fee_unfunded_and_restorable():
    ledger = Ledger()
    wallet = Wallet(6_400_000, ledger)
    rail = MultiStepRail(wallet)
    treasury = Treasury(ledger, wallet, rail, fee_ceiling_micro=1_000_000)
    treasury.transfer('to_reserve', '5', handle='parent', now_ns=1)
    wallet.settle(-900_000, 'shock', 'exchange_pnl')
    assert wallet.available < 0
    treasury.tick(2)
    assert treasury.state['status'] == 'submitted'
    assert treasury.state['index'] == 1
    assert treasury.fee_hold.amount == 490_000
    assert any(i['kind'] == 'treasury.fee_unfunded' for i in ledger._recovery_items())
    restored_wallet = Wallet(6_400_000, Ledger())
    restored_wallet._restore_state(wallet.state())
    restored_rail = MultiStepRail(restored_wallet)
    restored = Treasury(restored_wallet.ledger, restored_wallet, restored_rail,
                        fee_ceiling_micro=1_000_000)
    restored.restore(treasury.snapshot())
    restored_rail.attestation_ready = True
    restored.tick(3)
    assert restored.tick(4)[0]['status'] == 'confirmed'
    assert restored_wallet.balance == restored_wallet.available == 5_480_000
    assert restored_wallet.check_conservation()


def test_no_available_fee_money_still_reconciles_submitted_receipt():
    ledger = Ledger()
    wallet = Wallet(6_400_000, ledger)
    rail = MultiStepRail(wallet)
    treasury = Treasury(ledger, wallet, rail, fee_ceiling_micro=1_000_000)
    treasury.transfer('to_reserve', '5', handle='parent', now_ns=1)
    wallet.settle(-1_500_000, 'shock', 'exchange_pnl')
    treasury.tick(2)
    assert treasury.fee_hold.amount == 0
    assert treasury.state['status'] == 'submitted'
    assert wallet.check_conservation()
