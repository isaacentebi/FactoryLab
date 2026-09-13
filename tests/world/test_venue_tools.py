import json
from dataclasses import FrozenInstanceError
from decimal import Decimal
from unittest.mock import ANY, Mock

import pytest

from factorylab.world.exchange import (
    NS_PER_HOUR,
    NS_PER_MS,
    AccountState,
    FakeExchange,
    HyperliquidExchange,
    Order,
    OrderKind,
    Position,
)
from factorylab.world.venue_tools import VenueTools


@pytest.fixture
def exchange():
    return FakeExchange(
        coins=("BTC", "ETH"),
        start_prices={"BTC": Decimal(100), "ETH": Decimal(100)},
        price_path={"BTC": [Decimal(100)] * 10, "ETH": [Decimal(100)] * 10},
        fee_bps=Decimal(0),
        spread_bps=Decimal(0),
    )


@pytest.fixture
def venue(exchange):
    return VenueTools(exchange, coins=("BTC", "ETH"))


def test_contracts_are_complete_zero_priced_and_schema_copies(venue):
    specs = venue.contracts()
    assert {spec.id for spec in specs} == {
        f"venue.{name}"
        for name in (
            "candles",
            "order_book",
            "funding_history",
            "open_orders",
            "positions",
            "place_market",
            "place_limit",
            "cancel",
            "close",
            "set_leverage",
            "instruments",
            "mids",
            "funding",
        )
    }
    for spec in specs:
        assert spec.kind == "venue" and type(spec.price_micro_per_call) is int
        assert spec.price_micro_per_call == 0
        assert spec.args_schema["type"] == "object"
        assert spec.args_schema["additionalProperties"] is False
        json.dumps(spec.args_schema, allow_nan=False)
    with pytest.raises(FrozenInstanceError):
        specs[0].id = "other"
    specs[0].args_schema["properties"]["coin"]["enum"].append("DOGE")
    assert "error" in venue.call("venue.candles", {"coin": "DOGE", "interval": "1m", "n": 1})


@pytest.mark.parametrize(
    ("tool", "args"),
    [
        ("missing", {}),
        ("candles", {"coin": "BTC", "interval": "1m"}),
        ("candles", {"coin": "DOGE", "interval": "1m", "n": 1}),
        ("candles", {"coin": "BTC", "interval": "1d", "n": 1}),
        ("candles", {"coin": "BTC", "interval": "1m", "n": 201}),
        ("candles", {"coin": "BTC", "interval": "1m", "n": 0}),
        ("candles", {"coin": "BTC", "interval": "1m", "n": True}),
        ("order_book", {"coin": "BTC", "depth": 21}),
        ("order_book", {"coin": "BTC", "depth": -1}),
        ("order_book", {"coin": "BTC", "depth": 1.5}),
        ("funding_history", {"coin": "BTC", "n": 101}),
        ("funding_history", {"coin": "BTC", "n": "1"}),
        ("open_orders", {"coin": "BTC"}),
        ("positions", []),
        ("place_market", {"coin": "BTC", "side": "long", "size": "1"}),
        ("place_market", {"coin": "BTC", "side": "buy", "size": True}),
        ("place_market", {"coin": "BTC", "side": "buy", "size": "0e1"}),
        ("place_market", {"coin": "BTC", "side": "buy", "size": "NaN"}),
        ("place_market", {"coin": "BTC", "side": "buy", "size": float("inf")}),
        ("place_market", {"coin": "BTC", "side": "buy", "size": float("nan")}),
        ("place_market", {"coin": "BTC", "side": "buy", "size": "-1"}),
        ("place_market", {"coin": "BTC", "side": "buy", "size": "1", "reduce_only": 1}),
        ("place_limit", {"coin": "BTC", "side": "buy", "size": 1}),
        ("place_limit", {"coin": "BTC", "side": "buy", "size": 1, "price": 0}),
        ("cancel", {"coin": "BTC", "order_id": ""}),
        ("cancel", {"coin": "BTC", "order_id": 1}),
        ("close", {"coin": "BTC", "size": 0}),
        ("close", {"coin": "BTC", "size": -1}),
        ("set_leverage", {"coin": "BTC", "leverage": 4}),
        ("set_leverage", {"coin": "BTC", "leverage": 0}),
        ("set_leverage", {"coin": "BTC", "leverage": True}),
    ],
)
def test_invalid_args_are_logged_without_reaching_exchange(tool, args):
    ex = Mock()
    venue = VenueTools(ex, coins=("BTC",))
    result = venue.call(f"venue.{tool}", args)
    assert set(result) == {"error"}
    assert not ex.mock_calls
    assert len(venue.log) == 1
    assert venue.log[0][0] == f"venue.{tool}" and venue.log[0][2] is False


@pytest.mark.parametrize("interval,minutes", [("1m", 1), ("5m", 5), ("15m", 15), ("1h", 60)])
def test_candles_bucket_by_time_and_omit_gaps(interval, minutes):
    ex = FakeExchange(
        coins=("BTC",),
        price_path={
            "BTC": list(map(Decimal, [100, 110, 90, 105, 120, 80])),
        },
    )
    venue = VenueTools(ex, coins=("BTC",))
    args = {"coin": "BTC", "interval": interval, "n": 200}
    assert venue.call("venue.candles", args) == {"candles": []}
    width = minutes * 60 * 1_000_000_000
    for ts in (1, 2, 3, width - 1, width, 3 * width):
        ex.advance(ts)
    candles = venue.call("venue.candles", args)["candles"]
    assert candles[0] == {
        "ts_ns": 0,
        "open": "100",
        "high": "110",
        "low": "90",
        "close": "105",
        "volume": "0",
    }
    assert [c["ts_ns"] for c in candles] == [0, width, 3 * width]
    assert venue.call("venue.candles", {**args, "n": 2})["candles"] == candles[-2:]
    ex.advance(3 * width)
    assert len(ex._mid_history["BTC"]) == 7
    candles[0]["open"] = "changed"
    assert ex.candles("BTC", interval, 200)[0]["open"] == Decimal(100)
    json.dumps(venue.call("venue.candles", args), allow_nan=False)


def test_book_is_deterministic_monotonic_with_geometric_sizes(exchange, venue):
    exchange.spread_bps = Decimal(2)
    args = {"coin": "BTC", "depth": 20}
    book = venue.call("venue.order_book", args)
    assert book == venue.call("venue.order_book", args)
    assert book["coin"] == "BTC" and book["ts_ns"] == 0
    for side in ("bids", "asks"):
        prices = [Decimal(level["price"]) for level in book[side]]
        sizes = [Decimal(level["size"]) for level in book[side]]
        assert len(prices) == 20
        assert prices == sorted(set(prices), reverse=side == "bids")
        assert sizes[0] == 1
        assert all(a == b * 2 for a, b in zip(sizes, sizes[1:], strict=False))
    assert Decimal(book["bids"][0]["price"]) == Decimal("99.98")
    assert Decimal(book["asks"][0]["price"]) == Decimal("100.02")
    json.dumps(book, allow_nan=False)


def test_limit_open_orders_cancel_and_coin_scoping(venue):
    order = venue.call(
        "venue.place_limit",
        {
            "coin": "BTC",
            "side": "buy",
            "size": "1",
            "price": "95",
            "reduce_only": False,
        },
    )
    assert order["status"] == "resting"
    oid = order["order_id"]
    expected = {"order_id": oid, "coin": "BTC", "side": "buy", "size": "1", "price": "95"}
    assert venue.call("venue.open_orders", {}) == {"open_orders": [expected]}
    assert venue.call("venue.cancel", {"coin": "ETH", "order_id": oid})["status"] == "rejected"
    assert venue.call("venue.open_orders", {}) == {"open_orders": [expected]}
    assert venue.call("venue.cancel", {"coin": "BTC", "order_id": oid})["status"] == "cancelled"
    assert venue.call("venue.open_orders", {}) == {"open_orders": []}
    assert venue.call("venue.cancel", {"coin": "BTC", "order_id": oid})["status"] == "rejected"


@pytest.mark.parametrize("side", ["buy", "sell"])
@pytest.mark.parametrize("last_size", [None, "100"])
def test_close_partial_then_full_never_flips(venue, side, last_size):
    assert (
        venue.call(
            "venue.place_market",
            {
                "coin": "BTC",
                "side": side,
                "size": "2",
            },
        )["status"]
        == "filled"
    )
    assert venue.call("venue.close", {"coin": "BTC", "size": ".5"})["filled_size"] == "0.5"
    positions = venue.call("venue.positions", {})["positions"]
    assert positions == [
        {"coin": "BTC", "size": "1.5" if side == "buy" else "-1.5", "entry_px": "100"}
    ]
    assert venue.call("venue.close", {"coin": "BTC", "size": last_size})["filled_size"] == "1.5"
    assert venue.call("venue.positions", {}) == {"positions": []}
    assert venue.call("venue.close", {"coin": "BTC"})["status"] == "rejected"


def test_reduce_only_checks_current_position_at_fill_time(exchange, venue):
    reduce = {"coin": "BTC", "side": "sell", "size": "20", "reduce_only": True}
    assert venue.call("venue.place_market", reduce)["status"] == "rejected"
    venue.call("venue.place_market", {"coin": "BTC", "side": "buy", "size": "2"})
    assert venue.call("venue.place_market", {**reduce, "side": "buy"})["status"] == "rejected"
    assert venue.call("venue.place_limit", {**reduce, "price": "110"})["status"] == "resting"
    venue.call("venue.close", {"coin": "BTC", "size": "1"})
    exchange.price_path["BTC"] = [Decimal(110)]
    events = exchange.advance(1)
    assert exchange.account().positions == ()
    assert exchange.fills(0)[-1].size == 1
    assert exchange.open_orders() == []
    assert any(event.kind == "OrderRejected" for event in events)


def test_reduce_only_resting_order_cannot_reopen_flat_account(exchange, venue):
    venue.call("venue.place_market", {"coin": "BTC", "side": "buy", "size": 1})
    venue.call(
        "venue.place_limit",
        {
            "coin": "BTC",
            "side": "sell",
            "size": 1,
            "price": 110,
            "reduce_only": True,
        },
    )
    venue.call("venue.close", {"coin": "BTC"})
    exchange.price_path["BTC"] = [Decimal(110)]
    events = exchange.advance(1)
    assert exchange.account().positions == ()
    assert any(e.payload.get("reason") == "not reducing position" for e in events)


@pytest.mark.parametrize("side,opposite", [("buy", "sell"), ("sell", "buy")])
def test_reduce_only_market_clips_size_and_fees(exchange, venue, side, opposite):
    venue.call("venue.place_market", {"coin": "BTC", "side": side, "size": "1"})
    exchange.fee_bps = Decimal(10)
    result = venue.call(
        "venue.place_market",
        {
            "coin": "BTC",
            "side": opposite,
            "size": "100",
            "reduce_only": True,
        },
    )
    assert result["status"] == "filled" and result["filled_size"] == "1"
    assert exchange.account().positions == ()
    assert exchange.account().cash_usd == Decimal("99.9")


def test_leverage_changes_margin_and_preserves_other_coins(exchange, venue):
    order = {"coin": "BTC", "side": "buy", "size": 2}
    assert venue.call("venue.set_leverage", {"coin": "BTC", "leverage": 1})["status"] == "ok"
    assert venue.call("venue.place_market", order)["status"] == "rejected"
    assert venue.call("venue.set_leverage", {"coin": "BTC", "leverage": 3})["status"] == "ok"
    assert venue.call("venue.place_market", order)["status"] == "filled"
    assert exchange.account().margin_used_usd == Decimal(200) / 3
    assert "error" in venue.call("venue.set_leverage", {"coin": "BTC", "leverage": 4})
    assert exchange.set_leverage("BTC", 4)["status"] == "rejected"
    assert exchange._leverage["BTC"] == 3
    venue.call("venue.close", {"coin": "BTC"})
    venue.call("venue.set_leverage", {"coin": "BTC", "leverage": 1})
    assert venue.call("venue.place_market", order)["status"] == "rejected"
    assert venue.call("venue.place_market", {**order, "coin": "ETH"})["status"] == "filled"
    # The BTC margin setting must not change ETH's margin requirement.
    assert exchange.account().margin_used_usd == Decimal(200) / 3
    assert venue.call("venue.place_market", {**order, "size": ".5"})["status"] == "rejected"
    assert venue.call("venue.place_market", {**order, "size": ".3"})["status"] == "filled"
    assert exchange.account().margin_used_usd == Decimal(200) / 3 + 30


def test_manifest_leverage_limit_is_in_contract_and_validation(exchange):
    venue = VenueTools(exchange, coins=("BTC",), max_leverage=2)
    spec = next(s for s in venue.contracts() if s.id == "venue.set_leverage")
    assert spec.args_schema["properties"]["leverage"]["maximum"] == 2
    assert "error" in venue.call(spec.id, {"coin": "BTC", "leverage": 3})


def test_funding_history_contains_applied_events_only(exchange, venue):
    args = {"coin": "BTC", "n": 100}
    exchange.funding()  # a quote is not an applied event
    assert venue.call("venue.funding_history", args) == {"funding_history": []}
    venue.call("venue.place_market", {"coin": "BTC", "side": "buy", "size": "1"})
    for hour in range(1, 4):
        exchange.advance(hour * NS_PER_HOUR)
        exchange.advance(hour * NS_PER_HOUR + 1)
    history = venue.call("venue.funding_history", args)["funding_history"]
    assert history == [
        {"coin": "BTC", "rate": "0.0001", "premium": None, "ts_ns": hour * NS_PER_HOUR}
        for hour in range(1, 4)
    ]
    assert exchange.account().cash_usd == Decimal("99.97")
    assert venue.call("venue.funding_history", {**args, "n": 2})["funding_history"] == history[-2:]
    # Preserve the fake's existing single application when time jumps over intervals.
    exchange.advance(10 * NS_PER_HOUR)
    assert len(exchange.funding_history("BTC", 100)) == 4


def test_log_records_every_tool_and_snapshots_original_args(exchange, venue):
    attempts = [
        ("candles", {"coin": "BTC", "interval": "1m", "n": 1}),
        ("order_book", {"coin": "BTC", "depth": 1}),
        ("funding_history", {"coin": "BTC", "n": 1}),
        ("open_orders", {}),
        ("positions", {}),
        ("place_market", {"coin": "BTC", "side": "buy", "size": "1"}),
        ("place_limit", {"coin": "BTC", "side": "buy", "size": "1", "price": "95"}),
        ("cancel", {"coin": "BTC", "order_id": "2"}),
        ("close", {"coin": "BTC"}),
        ("set_leverage", {"coin": "BTC", "leverage": 2}),
    ]
    for tool, args in attempts:
        json.dumps(venue.call(f"venue.{tool}", args), allow_nan=False)
    assert venue.log == [(f"venue.{tool}", args, True) for tool, args in attempts]
    attempts[0][1]["n"] = 20
    assert venue.log[0][1]["n"] == 1
    venue.call("venue.close", {"coin": "BTC"})
    venue.call("venue.positions", {"unexpected": []})
    venue.call("unknown", {})
    assert [entry[2] for entry in venue.log[-3:]] == [False] * 3


def test_exchange_exceptions_are_results_and_logged():
    ex = Mock()
    ex.place.side_effect = RuntimeError("venue unavailable")
    ex.order_book.side_effect = RuntimeError("venue unavailable")
    venue = VenueTools(ex, coins=("BTC",))
    assert venue.call("venue.place_market", {"coin": "BTC", "side": "buy", "size": 1}) == {
        "status": "rejected",
        "error": "RuntimeError: venue unavailable",
    }
    assert venue.call("venue.order_book", {"coin": "BTC", "depth": 1}) == {
        "error": "RuntimeError: venue unavailable",
    }
    assert [entry[2] for entry in venue.log] == [False, False]


@pytest.fixture
def live_stub():
    # Bypass SDK construction, which fetches metadata; all I/O below is a stub.
    ex = HyperliquidExchange.__new__(HyperliquidExchange)
    ex.coins = ("BTC", "ETH")
    ex._address = "test-address"
    ex._info = Mock()
    ex._exchange = Mock()
    ex._sz_decimals = {"BTC": 3}
    return ex


def test_live_reads_normalize_sdk_shapes_and_millisecond_windows(live_stub, monkeypatch):
    ex = live_stub
    now = 10 * NS_PER_HOUR + 123 * NS_PER_MS
    monkeypatch.setattr("time.time_ns", lambda: now)
    ex._info.candles_snapshot.return_value = [
        {"t": t, "o": "100", "h": "110", "l": "90", "c": "105", "v": "2"}
        for t in (120000, 0, 60000)
    ]
    ex._info.l2_snapshot.return_value = {
        "time": 42,
        "levels": [
            [{"px": "98", "sz": "2"}, {"px": "99", "sz": "1"}],
            [{"px": "102", "sz": "2"}, {"px": "101", "sz": "1"}],
        ],
    }
    ex._info.funding_history.return_value = [
        {"time": t, "fundingRate": "0.001", "premium": "0.002"} for t in (3, 1, 2)
    ]
    ex._info.open_orders.return_value = [
        {"oid": 7, "coin": "BTC", "side": "B", "sz": "1", "limitPx": "95"},
    ]
    ex._info.user_state.return_value = {
        "marginSummary": {"accountValue": "100", "totalMarginUsed": "30"},
        "assetPositions": [{"position": {"coin": "BTC", "szi": "-1", "entryPx": "100"}}],
    }
    venue = VenueTools(ex, coins=("BTC",))
    candles = venue.call("venue.candles", {"coin": "BTC", "interval": "1m", "n": 2})
    assert [c["ts_ns"] for c in candles["candles"]] == [60000 * NS_PER_MS, 120000 * NS_PER_MS]
    assert candles["candles"][0]["open"] == "100"
    ex._info.candles_snapshot.assert_called_once_with("BTC", "1m", 35940000, now // NS_PER_MS)
    book = venue.call("venue.order_book", {"coin": "BTC", "depth": 1})
    assert book == {
        "coin": "BTC",
        "ts_ns": 42 * NS_PER_MS,
        "bids": [{"price": "99", "size": "1"}],
        "asks": [{"price": "101", "size": "1"}],
    }
    ex._info.l2_snapshot.assert_called_once_with("BTC")
    history = venue.call("venue.funding_history", {"coin": "BTC", "n": 2})["funding_history"]
    assert [f["ts_ns"] for f in history] == [2 * NS_PER_MS, 3 * NS_PER_MS]
    ex._info.funding_history.assert_called_once_with(
        "BTC",
        (now - 2 * NS_PER_HOUR) // NS_PER_MS,
        now // NS_PER_MS,
    )
    assert venue.call("venue.open_orders", {}) == {
        "open_orders": [
            {"order_id": "7", "coin": "BTC", "side": "buy", "size": "1", "price": "95"},
        ]
    }
    ex._info.open_orders.assert_called_once_with("test-address")
    assert venue.call("venue.positions", {}) == {
        "positions": [
            {"coin": "BTC", "size": "-1", "entry_px": "100"},
        ]
    }
    ex._info.user_state.assert_called_once_with("test-address")
    json.dumps([candles, book, history], allow_nan=False)


@pytest.mark.parametrize(
    "tool,args",
    [
        ("place_market", {"coin": "BTC", "side": "buy", "size": 1}),
        ("place_market", {"coin": "BTC", "side": "sell", "size": 1, "reduce_only": True}),
        ("place_limit", {"coin": "BTC", "side": "buy", "size": 1, "price": 95}),
        ("cancel", {"coin": "BTC", "order_id": "7"}),
        ("close", {"coin": "BTC"}),
        ("set_leverage", {"coin": "BTC", "leverage": 2}),
    ],
)
def test_every_live_write_without_key_rejects_without_io(live_stub, tool, args):
    live_stub._exchange = None
    venue = VenueTools(live_stub, coins=("BTC",))
    assert venue.call(f"venue.{tool}", args) == {"status": "rejected", "error": "no signing key"}
    assert not live_stub._info.mock_calls
    assert venue.log == [(f"venue.{tool}", args, False)]


def test_live_write_sdk_arguments_and_reduce_only(live_stub):
    ex = live_stub
    filled = {
        "status": "ok",
        "response": {
            "data": {
                "statuses": [
                    {"filled": {"oid": 1, "totalSz": "1", "avgPx": "100"}},
                ]
            }
        },
    }
    ex._exchange.market_open.return_value = filled
    ex._exchange.order.return_value = filled
    ex._exchange.market_close.return_value = filled
    ex._exchange.cancel.return_value = {
        "status": "ok",
        "response": {"data": {"statuses": ["success"]}},
    }
    ex._exchange.update_leverage.return_value = {"status": "ok", "response": {"type": "default"}}
    assert ex.place(Order("BTC", True, Decimal("1.0009"))).status == "filled"
    ex._exchange.market_open.assert_called_once_with("BTC", True, 1.0, cloid=ANY)
    assert (
        ex.place(
            Order("BTC", False, Decimal(1), OrderKind.LIMIT, Decimal(110), reduce_only=True)
        ).status
        == "filled"
    )
    ex._exchange.order.assert_called_once_with(
        "BTC",
        False,
        1.0,
        110.0,
        {"limit": {"tif": "Gtc"}},
        reduce_only=True,
        cloid=ANY,
    )
    assert ex.close("BTC").status == "filled"
    ex._exchange.market_close.assert_called_with("BTC", sz=None, cloid=ANY)
    assert ex.close("BTC", Decimal("0.5009")).status == "filled"
    ex._exchange.market_close.assert_called_with("BTC", sz=0.5, cloid=ANY)
    assert ex.cancel("7", coin="ETH")["status"] == "cancelled"
    ex._exchange.cancel.assert_called_once_with("ETH", 7)
    assert ex.set_leverage("BTC", 3)["status"] == "ok"
    ex._exchange.update_leverage.assert_called_once_with(3, "BTC", is_cross=True)
    ex.account = Mock(
        return_value=AccountState(
            Decimal(100),
            Decimal(100),
            (Position("BTC", Decimal(1), Decimal(100)),),
            Decimal(0),
        )
    )
    assert ex.place(Order("BTC", False, Decimal(2), reduce_only=True)).status == "filled"
    ex._exchange.market_close.assert_called_with("BTC", sz=1.0, cloid=ANY)
    calls = ex._exchange.market_close.call_count
    assert ex.place(Order("BTC", True, Decimal(1), reduce_only=True)).status == "rejected"
    assert ex._exchange.market_close.call_count == calls


@pytest.mark.parametrize("failure", [RuntimeError("venue unavailable"), {"status": "err"}])
def test_live_write_failures_distinguish_uncertainty_from_explicit_rejections(live_stub, failure):
    for method in ("market_open", "order", "market_close", "cancel", "update_leverage"):
        stub = getattr(live_stub._exchange, method)
        if isinstance(failure, Exception):
            stub.side_effect = failure
        else:
            stub.return_value = failure
    expected = "uncertain" if isinstance(failure, Exception) else "rejected"
    assert live_stub.place(Order("BTC", True, Decimal(1))).status == expected
    assert (
        live_stub.place(Order("BTC", True, Decimal(1), OrderKind.LIMIT, Decimal(95))).status
        == expected
    )
    assert live_stub.close("BTC").status == expected
    assert live_stub.cancel("7", coin="BTC")["status"] == expected
    assert live_stub.set_leverage("BTC", 2)["status"] == "rejected"


def test_live_cancel_detects_nested_rejection(live_stub):
    live_stub._exchange.cancel.return_value = {
        "status": "ok",
        "response": {"data": {"statuses": [{"error": "unknown order"}]}},
    }
    assert live_stub.cancel("7", coin="BTC")["status"] == "rejected"


def test_live_tiny_close_is_rejected_instead_of_becoming_full_close(live_stub):
    assert live_stub.close("BTC", Decimal("0.0001")).status == "rejected"
    assert live_stub.place(Order("BTC", True, Decimal("0.0001"))).status == "rejected"
    assert not live_stub._exchange.mock_calls


def test_live_malformed_fill_requires_reconciliation(live_stub):
    live_stub._exchange.market_open.return_value = {
        "status": "ok",
        "response": {
            "data": {
                "statuses": [
                    {"filled": {"oid": 1, "totalSz": "invalid", "avgPx": "100"}},
                ]
            }
        },
    }
    assert live_stub.place(Order("BTC", True, Decimal(1))).status == "uncertain"


@pytest.mark.network
def test_testnet_btc_candles_and_order_book(monkeypatch):
    monkeypatch.delenv("HL_PRIVATE_KEY", raising=False)
    venue = VenueTools(HyperliquidExchange(mainnet=False, coins=("BTC",)), coins=("BTC",))
    result = venue.call("venue.candles", {"coin": "BTC", "interval": "1m", "n": 5})
    assert "error" not in result
    candles = result["candles"]
    assert 1 <= len(candles) <= 5
    assert all(Decimal(c["close"]) > 0 for c in candles)
    assert [c["ts_ns"] for c in candles] == sorted(c["ts_ns"] for c in candles)
    book = venue.call("venue.order_book", {"coin": "BTC", "depth": 5})
    assert "error" not in book
    assert 1 <= len(book["bids"]) <= 5 and 1 <= len(book["asks"]) <= 5
    assert Decimal(book["bids"][0]["price"]) < Decimal(book["asks"][0]["price"])
