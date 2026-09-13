"""B6: an uncertain payment closes its hold and gets a reserve observation at the next tick."""

from factorylab.cortex.assembly import Assembly, AssemblySpec
from factorylab.cortex.request import Request
from factorylab.kernel.events import Event, EventKind
from factorylab.world.market import X402MeteredModel, X402Provider
from factorylab.world.models import TokenPrice
from tests.audit.test_audit_money_paths import MODEL, _seller_that_drops_the_paid_request
from tests.conftest import make_runtime


def test_next_tick_reconciles_uncertainty_without_recharging_or_inventing_a_refund(monkeypatch):
    rt = make_runtime()
    try:
        provider = X402Provider(private_key="0x" + "11" * 32,
                                 transport=_seller_that_drops_the_paid_request)
        provider.register(MODEL, 100_000)
        rt.market.target = provider
        rt.prices.register(MODEL, TokenPrice(0, 0, 100_000))
        assembly = Assembly(AssemblySpec("paid", 1, MODEL), X402MeteredModel(
            provider, rt.prices, rt.meter, record=rt._record_market,
            on_unaffordable=lambda handle: None,
        ))
        req = Request("bill", "answer", {}, {}, {"type": "object"}, 10**12,
                      100_000, None, "json", "test", "bill")
        before = rt.wallet.balance
        result = assembly.invoke(req)
        assert result.status == "failed" and result.cost == 100_000
        assert not rt.wallet.state()["reservations"] and rt.unresolved_x402
        monkeypatch.setattr(rt, "_route", lambda event: None)
        assert rt._process_event(Event("next-tick", EventKind.TICK, 1, {}, "test"))
        evidence = rt.ledger._recovery_items()
        reconciled, = [item for item in evidence if item["kind"] == "x402.reconciled"]
        assert reconciled["reserve_micro"] == 50_000_000
        assert reconciled["provisional_micro"] == 100_000
        assert reconciled["payments"][0]["observed_delta_micro"] == 0
        assert reconciled["payments"][0]["expected_delta_micro"] == -100_000
        assert not rt.unresolved_x402
        assert rt.wallet.balance == before - 100_000 and rt.wallet.check_conservation()
        assert rt.wallet.state()["uncertain_bills"]  # A balance alone cannot identify a payment.
    finally:
        rt._ledger_lock.close()


def test_interrupted_market_submission_replays_as_uncertain_without_resending():
    import pytest

    from factorylab.kernel.ledger import Ledger
    from factorylab.runtime.resume import RecoveryJournal
    from factorylab.world.market import PaymentOutcomeUnknown

    class Died(BaseException):
        pass

    ledger = Ledger(clock_ns=lambda: 0)
    journal = RecoveryJournal(ledger, lambda: 0)
    journal.active = True

    def die(*, record):
        record({"kind": "x402.reserve_before", "reserve_micro": 50_000_000})
        record({"kind": "x402.submitted", "amount_micro": 100_000})
        raise Died()

    with pytest.raises(Died):
        journal.call("market.complete", die, (), {"record": journal.append})
    replay = RecoveryJournal(ledger, lambda: 0)
    replay.active = replay.recovering = True
    replay.tail = ledger._recovery_items()
    with pytest.raises(PaymentOutcomeUnknown):
        replay.call("market.complete", lambda **kwargs: pytest.fail("resubmitted"), (),
                    {"record": replay.append})
    assert ledger._recovery_items()[-1]["error"] == "PaymentOutcomeUnknown"
