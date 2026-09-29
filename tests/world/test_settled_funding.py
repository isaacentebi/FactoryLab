"""Live funding truth at the SDK -> event -> named-outcome boundary; no network."""
from decimal import Decimal
from types import SimpleNamespace

import pytest

from factorylab.runtime.feedback import FeedbackMixin
from factorylab.runtime.grounded import advance_funding, funding_due, opportunity_cost
from factorylab.runtime.live import LiveVenue
from factorylab.world.exchange import (
    NS_PER_HOUR,
    FundingEvent,
    HyperliquidExchange,
    VenueUnavailable,
)

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
    rt.m = SimpleNamespace(timing=SimpleNamespace(world_repricing_ns=6 * H))

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
    calls.clear()
    deliver(venue.on_tick(4 * H))
    status, rates = rt._reference_outcome(frozen)
    assert status == "measured"
    assert rates == [("0.001", "100")]
    priced = opportunity_cost([("BTC", "100")], [("BTC", "100")], "0", "0",
                              {"coin": "BTC", "side": "buy"}, rates)
    assert priced["net_bps"] == "-10.0000"
    # Chapter II §III.b: old evidence is still requested, independently of
    # forward reads and other exact-boundary gap retries.
    assert any(start <= H // 1_000_000 <= end for _, start, end in calls)


@pytest.mark.parametrize("bad_row", [
    {}, {"time": 0}, {"fundingRate": "0.001"},
    {"time": None, "fundingRate": "0.001"},
    {"time": [], "fundingRate": "0.001"},
    {"time": 0, "fundingRate": {}},
    {"time": "Infinity", "fundingRate": "0.001"},
    None, [], "not a row",
])
def test_malformed_settlement_keeps_live_cursor_and_recovers(bad_row):
    """Chapter II §III.b/§II.b: malformed evidence is not a settled outside fact."""
    exchange = object.__new__(HyperliquidExchange)
    exchange.name = "synthetic-live"
    good = {"time": H // 1_000_000, "fundingRate": "0.001"}
    published = [good, bad_row]
    calls = []

    def history(coin, start, end):
        calls.append((start, end))
        return published

    exchange._info = SimpleNamespace(
        funding_history=history,
        l2_snapshot=lambda coin: {"time": 0, "levels": [[], []]})
    exchange._guarded = lambda name, call: call()
    exchange.mids = lambda: {"BTC": Decimal(100)}
    exchange.funding = lambda: [FundingEvent("BTC", Decimal("0.09"), None, 2 * H)]
    exchange.funding_payments = lambda since: []
    venue = LiveVenue(exchange)
    venue.through["settled:BTC"] = H
    events = venue.on_tick(2 * H)
    assert not any(event.payload.get("settled") for event in events)
    assert venue.through["settled:BTC"] == H
    assert venue.settled_emitted["BTC"] == {}
    published = [good]
    events = venue.on_tick(2 * H)
    settled = [event for event in events if event.payload.get("settled")]
    assert len(settled) == 1
    assert settled[0].payload["rate"] == "0.001"
    assert settled[0].payload["funding_ns"] == H
    assert venue.through["settled:BTC"] == 2 * H
    assert calls[0] == calls[1]


@pytest.mark.parametrize("raw", [None, {}, {"levels": []},
    {"time": 0, "levels": [[], []]},
    {"time": 0, "levels": [[{}], []]},
    {"time": 0, "levels": [[None], []]},
    {"time": [], "levels": [[], []]},
    {"time": 0, "levels": [[{"px": "NaN", "sz": "1"}], []]},
])
def test_book_parser_normalizes_malformed_response(raw):
    exchange = object.__new__(HyperliquidExchange)
    exchange._guarded = lambda name, call: raw
    if raw == {"time": 0, "levels": [[], []]}:
        assert exchange.order_book("BTC", 20)["bids"] == []
    else:
        with pytest.raises(VenueUnavailable):
            exchange.order_book("BTC", 20)


def test_sdk_unknown_book_market_stays_unavailable_until_metadata_recovers():
    """An SDK lookup failure sends no request, aborts no tick and invents no book."""
    from hyperliquid.info import Info

    exchange = object.__new__(HyperliquidExchange)
    exchange.name = "synthetic-live"
    info = object.__new__(Info)
    info.name_to_coin = {}
    calls = []

    def post(path, payload):
        calls.append((path, payload))
        return {"time": H // 1_000_000, "levels": [[], []]}

    info.post = post
    exchange._info = info
    exchange.mids = lambda: {"PURR/USDC": Decimal(1)}
    exchange.funding = lambda: []
    exchange.funding_payments = lambda since: []
    venue = LiveVenue(exchange)
    with pytest.raises(VenueUnavailable, match="book normalization failed"):
        exchange.order_book("PURR/USDC", 20)
    assert calls == []
    assert venue.on_tick(H)
    assert calls == []
    info.name_to_coin["PURR/USDC"] = "@1"
    # The tick reads no book (Chapter II §IV.c: a tick's cost never grows with the
    # markets); a book is read when asked, and then the recovered metadata answers it.
    venue.on_tick(H + 1)
    assert calls == []
    assert exchange.order_book("PURR/USDC", 20) == {
        "coin": "PURR/USDC", "ts_ns": H, "bids": [], "asks": []}
    assert calls == [("/info", {"type": "l2Book", "coin": "@1"})]


def test_malformed_book_does_not_abort_tick_or_block_settlement_retry():
    exchange = object.__new__(HyperliquidExchange)
    exchange.name = "synthetic-live"
    book = {"levels": []}
    exchange._info = SimpleNamespace(
        l2_snapshot=lambda coin: book,
        funding_history=lambda *args: [{"time": H // 1_000_000, "fundingRate": "0.001"}])
    exchange._guarded = lambda name, call: call()
    exchange.mids = lambda: {"BTC": Decimal(100)}
    exchange.funding = lambda: [FundingEvent("BTC", Decimal("0.09"), None, H)]
    exchange.funding_payments = lambda since: []
    venue = LiveVenue(exchange)
    assert any(event.payload.get("settled") for event in venue.on_tick(H))
    book = {"time": H // 1_000_000, "levels": [[], []]}
    venue.on_tick(H + 1)
    assert exchange.order_book("BTC", 20)["ts_ns"] == H


@pytest.mark.parametrize("raw", [None, [], {"BTC": None}, {"BTC": "NaN"}])
def test_malformed_mids_do_not_refresh_cache(raw):
    exchange = object.__new__(HyperliquidExchange)
    exchange.coins = ("BTC",)
    exchange._last_mids = {"BTC": Decimal(100)}
    exchange._last_mids_ns = 1
    exchange._info = SimpleNamespace(all_mids=lambda: raw)
    exchange._guarded = lambda name, call: call()
    with pytest.raises(VenueUnavailable):
        exchange.mids()
    assert exchange._last_mids == {"BTC": Decimal(100)}
    assert exchange._last_mids_ns == 1


@pytest.mark.parametrize("raw", [None, {}, [None], [{"time": float("inf")}],
    [{"time": 0, "coin": [], "tid": 1}]])
def test_malformed_fills_are_unavailable(raw):
    exchange = object.__new__(HyperliquidExchange)
    exchange._address = "synthetic"
    exchange._guarded = lambda name, call: raw
    with pytest.raises(VenueUnavailable):
        exchange.fills(0)


@pytest.mark.parametrize("raw", [None, [], {}, {"order": None, "status": "order"},
    {"order": {"order": {}, "status": []}, "status": "order"}])
def test_malformed_order_status_remains_uncertain(raw):
    exchange = object.__new__(HyperliquidExchange)
    exchange._address = "synthetic"
    exchange._info = SimpleNamespace(query_order_by_oid=lambda *args: raw)
    assert exchange.lookup("unused", order_id="1").status == "uncertain"


@pytest.mark.parametrize("raw", [None, [], {}, {"userCrossRate": [], "userAddRate": "0"}])
def test_malformed_user_fees_remain_unavailable(raw):
    exchange = object.__new__(HyperliquidExchange)
    exchange._address = "synthetic"
    exchange._info = SimpleNamespace(user_fees=lambda *args: raw)
    assert all(rate.get("fee_rates") == "unavailable"
               for rate in exchange._read_fee_rates().values())


@pytest.mark.parametrize("ctx", [None, [], {}, {"funding": "0.001", "oraclePx": {}},
    {"funding": "0.001", "oraclePx": "NaN"}])
def test_malformed_oracle_context_never_becomes_a_mark(ctx):
    exchange = object.__new__(HyperliquidExchange)
    exchange._info = SimpleNamespace(meta_and_asset_ctxs=lambda: [
        {"universe": [{"name": "BTC"}]}, [ctx]])
    exchange._guarded = lambda name, call: call()
    if not isinstance(ctx, dict) or "funding" not in ctx:
        # A context that states no rate makes the answer incomplete: the read fails
        # (Codex P1 on #178) and nothing, a mark least of all, is invented from it.
        with pytest.raises(VenueUnavailable):
            exchange.funding()
        return
    assert all(row.mark is None for row in exchange.funding())


def test_millisecond_publication_keeps_effective_boundary_and_exact_stamp():
    """HH:00:00.030 remains settled evidence even in an exact-boundary retry."""
    exchange = object.__new__(HyperliquidExchange)
    exchange.name = "synthetic-live"
    published_ns = H + 30_000_000
    calls = []

    def history(coin, start, end):
        calls.append((start, end))
        return ([{"time": published_ns // 1_000_000, "fundingRate": "0.001"}]
                if start <= published_ns // 1_000_000 <= end else [])

    exchange._info = SimpleNamespace(funding_history=history)
    exchange._guarded = lambda name, call: call()
    rows = exchange.settled_funding_history("BTC", H, H)
    assert len(rows) == 1
    assert rows[0].ts_ns == H
    assert rows[0].published_at_ns == published_ns
    venue = LiveVenue(exchange)
    venue.funding_oracles = {"BTC": {H: ("100", H)}}
    events = venue._settled_rates(published_ns, {"BTC"})
    assert len(events) == 1
    assert events[0].ts_ns == published_ns
    assert events[0].payload["funding_ns"] == H
    assert events[0].payload["published_at_ns"] == published_ns
    assert events[0].payload["mark"] == "100"
    assert venue._settled_rates(published_ns + 1, {"BTC"}) == []


def test_settled_history_filters_effective_range_and_keeps_latest_publication():
    exchange = object.__new__(HyperliquidExchange)
    exchange._guarded = lambda name, call: call()
    exchange._info = SimpleNamespace(funding_history=lambda *args: [
        {"time": stamp // 1_000_000, "fundingRate": rate}
        for stamp, rate in [(2 * H + 30_000_000, "0.003"),
                            (H + 40_000_000, "0.002"), (H + 30_000_000, "0.001")]])
    rows = exchange.settled_funding_history("BTC", H, H)
    assert [(row.ts_ns, row.rate, row.published_at_ns) for row in rows] == [
        (H, Decimal("0.002"), H + 40_000_000)]


@pytest.mark.parametrize("offset", [-1, H, 30_000_001])
def test_live_rejects_publication_outside_effective_period_or_in_future(offset):
    row = FundingEvent("BTC", Decimal("0.001"), None, H, published_at_ns=H + offset)
    exchange = SimpleNamespace(name="synthetic", funding_interval_ns=H,
                               settled_funding_history=lambda *args: [row])
    venue = LiveVenue(exchange)
    assert venue._settled_rates(H + 30_000_000, {"BTC"}) == []
    assert venue.settled_emitted["BTC"] == {}


def test_exact_funding_keeps_interpolation_without_live_settlement_requirement():
    state = {"interval": H, "cursor": H - 1, "rate": "0.001", "rates": [],
             "marks": [[H, "100", True, H]]}
    advance_funding(state, H + 1, "0.09")
    assert funding_due(state, H - 1, H + 1) == [("0.001", "100")]
