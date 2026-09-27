"""Live funding never substitutes mids or later-hour oracles for a boundary poll."""
from decimal import Decimal
from types import SimpleNamespace

import pytest

from factorylab.runtime.grounded import FUNDING_PENDING, advance_funding, funding_due, funding_mark
from factorylab.runtime.live import LiveVenue
from factorylab.world.exchange import NS_PER_HOUR, FundingEvent, HyperliquidExchange

H = NS_PER_HOUR


@pytest.mark.parametrize("oracle,expected", [("123.456789123", Decimal("123.456789123")),
                                            (None, None), ("NaN", None), ("-1", None)])
def test_asset_context_preserves_exact_oracle_or_unknown(monkeypatch, oracle, expected):
    exchange = object.__new__(HyperliquidExchange)
    exchange._info = SimpleNamespace(meta_and_asset_ctxs=lambda: [
        {"universe": [{"name": "BTC"}]},
        [{"funding": "0.0001", "premium": "0", "oraclePx": oracle}]])
    exchange._guarded = lambda name, call: call()
    monkeypatch.setattr("time.time_ns", lambda: H)
    [row] = exchange.funding()
    assert row.mark == expected
    assert row.ts_ns == H


def test_delayed_settlement_keeps_boundary_oracle_and_never_fills_a_skipped_hour():
    now, price, published = H, Decimal("123.456789123"), []
    exchange = SimpleNamespace(
        name="synthetic-live", funding_interval_ns=H, settled_funding=True,
        mids=lambda: {"BTC": Decimal(900)},
        funding=lambda: [FundingEvent("BTC", Decimal("0.9"), None, now, price)],
        settled_funding_history=lambda coin, start, end: published,
        funding_payments=lambda start: [])
    venue = LiveVenue(exchange)
    venue.on_tick(now)
    now, price = 3 * H, Decimal(500)
    published = [FundingEvent("BTC", Decimal("0.001"), None, H),
                 FundingEvent("BTC", Decimal("0.002"), None, 2 * H)]
    events = [e for e in venue.on_tick(now) if e.payload.get("settled")]
    assert events[0].payload["mark"] == "123.456789123"
    assert events[0].payload["oracle_observed_at_ns"] == H
    assert events[1].payload["mark"] is None
    state = {"interval": H, "cursor": H - 1, "rate": None, "rates": [],
             "marks": [], "strict": True}
    advance_funding(state, H, "0.001", settled=True)
    funding_mark(state, H - 1, H, H, "900")
    assert funding_due(state, H - 1, H) == FUNDING_PENDING
    advance_funding(state, H, "0.001", events[0].payload["mark"], settled=True)
    assert funding_due(state, H - 1, H) == [("0.001", "123.456789123")]
