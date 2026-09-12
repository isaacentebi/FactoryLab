from decimal import Decimal
from types import SimpleNamespace

import pytest

from factorylab.runtime.live import LiveVenue
from factorylab.world.exchange import FundingPayment, HyperliquidExchange


def row(i, ms=10, amount="-0.25", coin="ETH"):
    return {
        "time": ms,
        "hash": f"0x{i:064x}",
        "delta": {"type": "funding", "coin": coin, "usdc": amount, "fundingRate": "0.0001"},
    }


def exchange(method):
    e = object.__new__(HyperliquidExchange)
    e._address = "0x" + "1" * 40
    e._info = SimpleNamespace(user_funding_history=method)
    e._guarded = lambda name, fn: fn()
    return e


def test_live_payment_sign_and_inclusive_nanoseconds():
    e = exchange(lambda user, start: [row(1, 9), row(2), row(3, amount="0.5", coin="BTC")])
    result = e.funding_payments(10_000_000)
    assert [p.paid_usd for p in result] == [Decimal("0.25"), Decimal("-0.5")]
    assert all(p.ts_ns == 10_000_000 for p in result)
    assert e.funding_payments(10_000_001) == []


def test_pagination_keeps_all_boundary_peers_without_duplicates():
    first = [row(i, ms=i + 1) for i in range(499)] + [row(499, ms=500)]
    calls = []

    def fetch(user, start):
        calls.append(start)
        return first if start == 0 else [row(499, ms=500), row(500, ms=500), row(501, ms=501)]

    payments = exchange(fetch).funding_payments(0)
    assert calls == [0, 500]
    assert len(payments) == len({p.id for p in payments}) == 502


def test_stalled_pagination_fails_instead_of_losing_funding():
    e = exchange(lambda user, start: [row(i, ms=10) for i in range(500)])
    with pytest.raises(ValueError, match="stalled"):
        e.funding_payments(0)


def test_funding_cursor_avoids_rebooking_history_and_handles_same_timestamp_peers():
    records, payments = [], []
    venue = LiveVenue(
        SimpleNamespace(name="testnet", funding_payments=lambda since: payments),
        ledger=SimpleNamespace(append=records.append),
    )
    assert venue.funding_payments(100) == []
    payments.extend(
        [
            FundingPayment("old", "ETH", Decimal("1"), Decimal("0"), 99),
            FundingPayment("a", "ETH", Decimal("0.1"), Decimal("0"), 101),
        ]
    )
    assert venue.funding_payments(110)[0].payload["paid_usd"] == "0.1"
    assert venue.funding_payments(120) == []
    payments.append(FundingPayment("b", "BTC", Decimal("-0.2"), Decimal("0"), 101))
    assert venue.funding_payments(130)[0].payload["paid_usd"] == "-0.2"
    assert records[-1]["seen"] == ["a", "b"]
    assert venue.funding_payments(140) == []
