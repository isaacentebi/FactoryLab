"""Chapter II §III.b: available settled facts cannot disappear behind polling state."""
from decimal import Decimal
from types import SimpleNamespace

import pytest

from factorylab.runtime.live import LiveVenue
from factorylab.world.exchange import NS_PER_HOUR, FundingEvent

H = NS_PER_HOUR


def venue_with_history():
    """Return a network-free live adapter with inclusive, mutable venue history."""
    rows, calls = [], []

    def history(coin, start, end):
        calls.append((coin, start, end))
        return [row for row in rows if row.coin == coin and start <= row.ts_ns <= end]

    exchange = SimpleNamespace(
        name="review-regression", settled_funding=True, funding_interval_ns=H,
        mids=lambda: {}, order_book=lambda coin, depth: {}, funding=lambda: [],
        funding_payments=lambda start: [], settled_funding_history=history)
    return LiveVenue(exchange, last_funding_ns=H // 2, markets=lambda: ("BTC",)), rows, calls


def test_first_successful_history_poll_retains_launch_bound_after_outage():
    venue, rows, calls = venue_with_history()
    history = venue.exchange.settled_funding_history

    def unavailable(*args):
        raise RuntimeError("history unavailable")

    venue.exchange.settled_funding_history = unavailable
    venue._settled_rates(H, {"BTC"})
    venue.exchange.settled_funding_history = history
    rows.extend(FundingEvent("BTC", Decimal("0.001"), None, stamp) for stamp in (H, 2 * H))
    events = venue._settled_rates(3 * H, {"BTC"})
    assert [event.payload["funding_ns"] for event in events] == [H, 2 * H]
    assert calls[0][1] <= H


def test_first_history_poll_long_after_launch_still_reads_required_boundaries():
    venue, rows, calls = venue_with_history()
    # Account-payment polling can advance independently of settled-rate polling.
    venue.last_funding_ns = 3 * H
    rows.extend(FundingEvent("BTC", Decimal("0.001"), None, stamp) for stamp in (H, 2 * H))
    events = venue._settled_rates(3 * H, {"BTC"})
    assert [event.payload["funding_ns"] for event in events] == [H, 2 * H]
    assert calls[0][1] <= H


@pytest.mark.parametrize("failed", [False, True])
def test_configured_perpetuals_poll_history_despite_missing_current_rates(failed):
    venue, rows, calls = venue_with_history()
    venue.markets = lambda: ("BTC", "ETH", "BTC/USDC")
    venue.through["settled:SOL"] = H

    def funding():
        if failed:
            raise RuntimeError("current funding unavailable")
        return [FundingEvent("ETH", Decimal("0.001"), None, H)]

    venue.exchange.funding = funding
    rows.append(FundingEvent("BTC", Decimal("0.001"), None, H))
    events = [event for event in venue.on_tick(H) if event.payload.get("settled")]
    assert {coin for coin, _, _ in calls} == {"BTC", "ETH", "SOL"}
    assert [event.payload["coin"] for event in events] == ["BTC"]
    assert events[0].payload["mark"] is None
    assert "settled:BTC" in venue.through
