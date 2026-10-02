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


def _billed_decisions(ledger, wallet, queue, clock, count, late):
    """``count`` decisions, each billed and settled; ``late`` keeps one uncertain bill per
    decision open until every decision has settled, then settles it."""
    from factorylab.kernel.queue import PropensityRecord

    held = []
    for n in range(count):
        chosen = f"seat-{n % 3}"
        handle = queue.open(actor="learner", event_id=f"e{n}", channel="outcome",
                            propensity=PropensityRecord((chosen,), (1.0,), chosen,
                                                        n, "learner", "a" * 64),
                            deadline_ns=clock.now + 10, parent_handle=None, cost_ceiling=10)
        wallet.commit(wallet.reserve(3, handle, "model:m"), 2)
        if n < late:
            hold = wallet.reserve(5, handle, "model:m")
            wallet.commit_uncertain(hold)
            held.append(hold)
        clock.now += 1
        queue.settle(handle, channel="outcome", score=0.5, status="settled",
                     definition_version="v1", sampling_ref=None)
    for hold in held:  # refunds that land after their decisions settled
        wallet.settle_uncertain(hold.id, 1)


def test_the_spend_fold_holds_outstanding_bills_not_every_decision(clock):
    """The attribution map follows the bills still unresolved, not every decision the
    world opened (essay II.II.b, "memory"), and a refund that lands after its decision
    settled is still spend of the action that decision chose."""
    import tracemalloc

    from factorylab.kernel.queue import DecisionQueue

    peaks = []
    for count in (300, 3000):
        ledger = Ledger(clock_ns=clock)
        wallet = Wallet(10**9, ledger, clock_ns=clock)
        _billed_decisions(ledger, wallet, DecisionQueue(ledger, clock_ns=clock), clock,
                          count, late=3)
        ledger.aggregate("spend_by_capability")  # verify the chain once, outside the peak
        tracemalloc.start()
        spend = ledger.aggregate("spend_by_capability")["spend"]
        peaks.append(tracemalloc.get_traced_memory()[1])
        tracemalloc.stop()
        per_seat = {f"seat-{k}": 2 * len(range(k, count, 3)) for k in range(3)}
        for k in range(3):  # each late bill: 5 held, 1 its true cost
            per_seat[f"seat-{k}"] += 1
        assert spend == per_seat
    assert peaks[1] - peaks[0] < 100_000, peaks
