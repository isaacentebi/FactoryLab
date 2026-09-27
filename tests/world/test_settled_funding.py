"""Live funding truth at the SDK -> event -> named-outcome boundary; no network."""
from decimal import Decimal
from types import SimpleNamespace

import pytest

from factorylab.runtime.feedback import FeedbackMixin
from factorylab.runtime.grounded import advance_funding, funding_due, opportunity_cost
from factorylab.runtime.live import LiveVenue
from factorylab.world.exchange import NS_PER_HOUR, FundingEvent, HyperliquidExchange

H = NS_PER_HOUR


@pytest.mark.parametrize("missing", ["empty", "outage", "later-only"])
def test_live_named_outcome_waits_for_backdated_settled_rate(missing):
    """Predictions and payment-rate rows cannot fix an outcome; delayed public truth can.

    Existing fake/tape tests have exact streams, not publication gaps. This uses
    the real SDK adapter and feedback owner, with only the wire replaced.
    """
    exchange = object.__new__(HyperliquidExchange)
    exchange.name = "synthetic-live"
    calls = []
    published = []

    def history(coin, start, end):
        calls.append((coin, start, end))
        if published is None:
            raise OSError("unavailable")
        return published

    exchange._info = SimpleNamespace(
        funding_history=history,
        l2_snapshot=lambda coin: {"coin": coin, "time": 0, "levels": [[], []]})
    exchange._guarded = lambda name, call: call()
    exchange.mids = lambda: {"BTC": Decimal(100)}
    exchange.funding = lambda: [FundingEvent("BTC", Decimal("0.09"), None, 3 * H)]
    exchange.funding_payments = lambda since: []
    venue = LiveVenue(exchange)
    # First observed boundary is reread; an empty response must not skip it.
    venue.on_tick(H - 1)
    published = (None if missing == "outage" else
                 [{"time": 2 * H // 1_000_000, "fundingRate": "0.002"}]
                 if missing == "later-only" else [])
    state = {"interval": H, "cursor": H - 1, "rate": "0.09", "rates": [],
             "marks": [[H, "100", True, H]], "strict": True}
    frozen = {"coin": "BTC", "open_ns": H - 1, "due_ns": H + 1,
              "ns": H - 1, "res": [H + 1, "100"], "funding": state}
    rt = FeedbackMixin()
    rt.exchange = exchange
    rt.reference_mids = {"named": frozen}
    rt.funding_prints = {}
    rt.venue_marks = {"BTC": [H - 1, "100"]}
    rt.clock = SimpleNamespace(now_ns=4 * H)
    rt.tick_through_ns = 4 * H
    rt._stream_watermark = lambda stream: 4 * H
    rt._patience_ns = lambda: H

    def deliver(events):
        for event in events:
            if event.kind == "Funding":
                p = event.payload
                rt._observe_funding(p["coin"], p.get("funding_ns", event.ts_ns),
                                    p["rate"], p.get("mark"), settled=p.get("settled", False))

    deliver(venue.on_tick(3 * H))
    assert rt._reference_outcome(frozen) == ("open", None)
    # A settled payment from the account is not the public-history contract either.
    rt._observe_funding("BTC", H, "0.08")
    assert rt._reference_outcome(frozen) == ("open", None)
    # Simulate a previously provisional boundary: settled truth must replace it even
    # after the measuring mid and cursor have passed the horizon.
    state["rates"] = [[H, "0.09"]]
    state["cursor"] = 3 * H
    assert rt._reference_outcome(frozen) == ("open", None)
    published = [{"time": H // 1_000_000, "fundingRate": "0.001"}]
    deliver(venue.on_tick(4 * H))
    status, rates = rt._reference_outcome(frozen)
    assert status == "measured"
    assert rates == [("0.001", "100")]
    priced = opportunity_cost([("BTC", "100")], [("BTC", "100")], "0", "0",
                              {"coin": "BTC", "side": "buy"}, rates)
    assert priced["net_bps"] == "-10.0000"
    assert calls[-1][1] <= H // 1_000_000


def test_exact_funding_keeps_interpolation_without_live_settlement_requirement():
    state = {"interval": H, "cursor": H - 1, "rate": "0.001", "rates": [],
             "marks": [[H, "100", True, H]]}
    advance_funding(state, H + 1, "0.09")
    assert funding_due(state, H - 1, H + 1) == [("0.001", "100")]
