"""B2: malformed venue rows are isolated from valid observations and journal errors retain type."""

from decimal import Decimal
from types import SimpleNamespace

import pytest

from factorylab.kernel.ledger import Ledger
from factorylab.runtime.resume import RecoveryJournal
from factorylab.world.exchange import HyperliquidExchange, VenueUnavailable


def test_malformed_fills_and_funding_rows_are_skipped():
    venue = object.__new__(HyperliquidExchange)
    venue._address = "offline-account"
    venue._guarded = lambda name, call: call()
    fill = {"oid": 1, "coin": "BTC", "side": "B", "sz": "1", "px": "100", "time": 10}
    payment = {"time": 10, "hash": "payment-1", "delta": {
        "type": "funding", "coin": "BTC", "usdc": "-0.125", "fundingRate": "0.01"}}
    venue._info = SimpleNamespace(
        user_fills_by_time=lambda *a: [None, {}, {**fill, "px": "NaN"},
                                       {**fill, "sz": "not-a-number"}, fill],
        user_funding_history=lambda *a: [None, {}, {**payment, "time": "bad"},
                                         {**payment, "delta": {**payment["delta"], "usdc": "NaN"}},
                                         payment],
    )
    assert len(venue.fills(0)) == 1
    funded, = venue.funding_payments(0)
    assert funded.paid_usd == Decimal("0.125")


def test_journal_replays_the_same_venue_error_class():
    ledger = Ledger(clock_ns=lambda: 0)
    journal = RecoveryJournal(ledger, lambda: 0)
    journal.active = True

    def outage():
        raise VenueUnavailable("private provider response")

    with pytest.raises(VenueUnavailable):
        journal.call("exchange.mids", outage, (), {})
    replay = RecoveryJournal(ledger, lambda: 0)
    replay.active = replay.recovering = True
    replay.tail = ledger._recovery_items()
    with pytest.raises(VenueUnavailable) as error:
        replay.call("exchange.mids", lambda: pytest.fail("recorded read was repeated"), (), {})
    assert "private provider response" not in str(error.value)
