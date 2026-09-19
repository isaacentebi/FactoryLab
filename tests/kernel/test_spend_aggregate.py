"""The spend aggregate reports what was spent (defect 15a).

It counted an uncertain bill at its ceiling and never subtracted what
``settle_uncertain`` returned to the balance, so the wake's spend by capability
overstated every bill settled below its ceiling.
"""

from factorylab.kernel.ledger import Ledger
from factorylab.kernel.wallet import Wallet


def test_spend_aggregate_nets_the_refund_of_a_settled_uncertain_bill(clock):
    ledger = Ledger()
    wallet = Wallet(1_000, ledger, clock_ns=clock)
    held = wallet.reserve(200, "h", "model:m")
    wallet.commit_uncertain(held)
    assert ledger.aggregate("spend_by_capability") == {"spend": {"model:m": 200}}
    wallet.settle_uncertain(held.id, 30)
    # The bill was 30. The aggregate is what was spent, not the ceiling held for it.
    assert ledger.aggregate("spend_by_capability") == {"spend": {"model:m": 30}}
    assert ledger.aggregate("spend_by_capability", since_ns=0) == {"spend": {"model:m": 30}}
    assert wallet.balance == 970
