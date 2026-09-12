"""B7: a launch-bound vendor-cost multiple limits disputed liabilities."""

from dataclasses import replace

import pytest

from factorylab.kernel.ledger import Ledger
from factorylab.kernel.wallet import Wallet
from factorylab.runtime.worlds import load_manifest
from factorylab.world.metering import Meter


@pytest.mark.parametrize("multiple,reported,booked", [(2, 200, 200), (2, 201, 100), (7, 701, 100)])
def test_reported_bill_uses_the_configured_multiple(multiple, reported, booked):
    ledger = Ledger()
    wallet = Wallet(1000, ledger, reported_cost_multiple=multiple)
    billed = Meter(wallet).run(handle="d", reason="model:test", ceiling=100,
                               execute=lambda: reported, cost_of=lambda value: value)
    assert billed.cost == booked and wallet.balance == 1000 - booked
    assert wallet.check_conservation() and not wallet.state()["reservations"]
    disputed = [item for item in ledger._recovery_items() if item["kind"] == "metering.disputed"]
    assert bool(disputed) == (reported > 100 * multiple)
    if disputed:
        assert disputed[0]["reported"] == reported and disputed[0]["booked"] == 100


@pytest.mark.parametrize("multiple", [0, -1, True, 1.5])
def test_manifest_rejects_invalid_cost_multiple(multiple):
    m = load_manifest("scripted")
    with pytest.raises(ValueError):
        replace(m, treasury=replace(m.treasury, reported_cost_multiple=multiple)).validate()
