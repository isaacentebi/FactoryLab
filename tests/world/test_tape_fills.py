"""Honest fills on a recorded tape (money path; factorylab/world/tape.py).

Each test tries to make the replay kinder than the recording and asserts it is not:
a decision never fills against the book its sender read; a resting order never fills
on a touch; one snapshot's liquidity is never taken twice; the venue's order floor,
its immediate-or-cancel market orders and its maker and taker rates all bind.
"""

from decimal import Decimal
from pathlib import Path

import pytest

from factorylab.world.exchange import Order, OrderKind
from factorylab.world.tape import Tape, TapeVenue

T0 = 1_790_000_000 * 10**9
S = 10**9
LONGRUN = Path(__file__).parents[1] / "fixtures" / "tape" / "longrun1-2100.events.json"


def _tape(mids, books=None, instruments=None, funding=None):
    """A tape of BTC (and PURR/USDC) rows at the given second offsets."""
    stamps = sorted({t for series in mids.values() for t, _ in series})
    data = {"format": "factorylab-tape/1", "venue": "test", "declared_tick_ns": 10 * S,
            "ticks": [T0 + t * S for t in stamps],
            "mids": {c: [[T0 + t * S, str(px)] for t, px in rows] for c, rows in mids.items()},
            "funding": funding or {},
            "books": {c: [[T0 + t * S, [[str(p), str(z)] for p, z in bids],
                           [[str(p), str(z)] for p, z in asks]] for t, bids, asks in rows]
                      for c, rows in (books or {}).items()},
            "instruments": instruments}
    return Tape.from_data(data)


def _venue(tape, cash="1000"):
    return TapeVenue(tape, coins=("BTC",), spot_pairs=tuple(m for m in tape.pairs),
                     start_cash_usd=Decimal(cash))


def _buy(size, *, limit=None, cid=None, market="perp", coin="BTC", reduce_only=False):
    return Order(coin, True, Decimal(size), OrderKind.LIMIT if limit else OrderKind.MARKET,
                 None if limit is None else Decimal(limit), cid, reduce_only=reduce_only,
                 market=market)


def _sell(size, *, limit=None, cid=None, reduce_only=False):
    return Order("BTC", False, Decimal(size), OrderKind.LIMIT if limit else OrderKind.MARKET,
                 None if limit is None else Decimal(limit), cid, reduce_only=reduce_only)


def _fills(events):
    return [e.payload for e in events if e.kind == "Fill"]


def _rejections(events):
    return [e.payload for e in events if e.kind == "OrderRejected"]


def test_a_decision_fills_against_the_next_recorded_row_never_the_one_it_read():
    tape = _tape({"BTC": [(0, 100), (20, 102), (30, 120)]})
    venue = _venue(tape)
    venue.advance(T0)
    result = venue.place(_buy("0.2", cid="d-1"))
    assert result.status == "resting" and result.filled_size == 0
    assert _fills(venue.drain_events()) == []  # nothing fills at the quote it was sent at
    # The next tick shows no newer row: the order is still in flight.
    assert _fills(venue.advance(T0 + 10 * S)) == []
    assert venue.lookup("d-1").status == "resting"
    assert venue.open_orders()[0]["in_flight"] is True
    [fill] = _fills(venue.advance(T0 + 20 * S))
    half = Decimal(102) * Decimal(2) / 20_000  # the assumed spread: no book recorded
    assert Decimal(fill["px"]) == (Decimal(102) + half).quantize(Decimal("1e-10"))
    assert venue.lookup("d-1").status == "filled"
    assert Decimal(fill["fee_usd"]) == (Decimal("0.2") * Decimal(fill["px"])
                                        * Decimal("0.00045")).quantize(Decimal("0.000001"))


def test_a_resting_limit_never_fills_on_a_touch_only_on_a_trade_through():
    # Synthetic asks sit at mid * 1.0001; the limit is exactly the ask when the mid is 100.
    touch = Decimal(100) * Decimal("1.0001")
    tape = _tape({"BTC": [(0, 101), (10, 101), (20, 100), (30, 99)]})
    venue = _venue(tape)
    venue.advance(T0)
    venue.place(_buy("0.2", limit=touch, cid="d-1"))
    assert _fills(venue.advance(T0 + 10 * S)) == []  # arrived above the ask: it rests
    assert _fills(venue.advance(T0 + 20 * S)) == []  # the ask touches its price: no fill
    assert venue.lookup("d-1").status == "resting"
    [fill] = _fills(venue.advance(T0 + 30 * S))  # the ask traded through its price
    assert Decimal(fill["px"]) == touch  # at its own price, as a maker
    assert Decimal(fill["fee_usd"]) == (Decimal("0.2") * touch * Decimal("0.00015")).quantize(
        Decimal("0.000001"))


def _booked(asks, *, mids=((0, 100), (10, 100), (20, 100), (30, 100))):
    """A tape whose BTC book is recorded at every row (so it is always current)."""
    return _tape({"BTC": list(mids)},
                 books={"BTC": [(t, [(Decimal(99), Decimal("0.5"))], asks) for t, _ in mids]})


def test_one_snapshots_liquidity_is_taken_once():
    asks = [(Decimal(101), Decimal("1")), (Decimal(102), Decimal("1"))]
    venue = _venue(_booked(asks), cash="100000")
    venue.advance(T0)
    venue.place(_buy("0.8", cid="a"))
    venue.place(_buy("0.8", cid="b"))
    first, second = _fills(venue.advance(T0 + 10 * S))
    assert Decimal(first["px"]) == 101
    # The second met what the first left of 101 and then 102: never the full 1 again.
    assert Decimal(second["px"]) == ((Decimal("0.2") * 101 + Decimal("0.6") * 102)
                                     / Decimal("0.8")).quantize(Decimal("1e-10"))
    assert venue.order_book("BTC", 5)["asks"] == [{"price": Decimal(101), "size": Decimal(0)},
                                                  {"price": Decimal(102),
                                                   "size": Decimal("0.4")}]
    # The next snapshot is a fresh recording and offers its own liquidity.
    venue.place(_buy("0.5", cid="c"))
    [third] = _fills(venue.advance(T0 + 20 * S))
    assert Decimal(third["px"]) == 101  # a new snapshot offers its own liquidity


def test_a_market_order_is_immediate_or_cancel_and_its_remainder_is_cancelled():
    asks = [(Decimal(101), Decimal("0.1")), (Decimal(104), Decimal("0.1")),
            (Decimal(106), Decimal("9"))]  # 106 is beyond 5% of the 100 mid sent at
    venue = _venue(_booked(asks), cash="100000")
    venue.advance(T0)
    venue.place(_buy("0.5", cid="ioc"))
    events = venue.advance(T0 + 10 * S)
    [fill] = _fills(events)
    [cancel] = _rejections(events)
    assert Decimal(fill["size"]) == Decimal("0.2")
    assert cancel["reason"] == "immediate-or-cancel remainder cancelled"
    assert Decimal(cancel["cancelled_size"]) == Decimal("0.3")
    looked = venue.lookup("ioc")
    assert looked.status == "cancelled" and looked.filled_size == Decimal("0.2")
    assert venue.open_orders() == []  # nothing rests
    # With nothing inside the bound, a market order fills nothing and is refused.
    tape = _booked([(Decimal(106), Decimal("9"))])
    empty = _venue(tape, cash="100000")
    empty.advance(T0)
    empty.place(_buy("0.5", cid="x"))
    [refused] = _rejections(empty.advance(T0 + 10 * S))
    assert "no liquidity" in refused["reason"] and empty.lookup("x").status == "rejected"


def test_a_crossing_limit_takes_at_the_books_prices_and_rests_the_rest():
    asks = [(Decimal(101), Decimal("0.1")), (Decimal(103), Decimal("0.1"))]
    venue = _venue(_booked(asks), cash="100000")
    venue.advance(T0)
    venue.place(_buy("0.3", limit="102", cid="x"))
    [fill] = _fills(venue.advance(T0 + 10 * S))
    assert Decimal(fill["px"]) == 101 and Decimal(fill["size"]) == Decimal("0.1")
    assert Decimal(fill["fee_usd"]) == (Decimal("10.1") * Decimal("0.00045")).quantize(
        Decimal("0.000001"))  # taking liquidity pays the taker rate
    [resting] = venue.open_orders()
    assert resting["size"] == Decimal("0.2") and "in_flight" not in resting


def test_the_order_floor_binds_and_comes_from_the_recorded_listing():
    listing = {"perp": [{"coin": "BTC", "lot_size": "0.00001", "tick_size": "0.1",
                         "min_order_value_usd": "25"}], "spot": []}
    venue = _venue(_tape({"BTC": [(0, 100), (10, 100)]}, instruments=listing))
    venue.advance(T0)
    refused = venue.place(_buy("0.2", cid="small"))  # $20 of notional
    assert refused.status == "rejected" and "minimum value" in refused.error
    assert venue.place(_buy("0.3", cid="ok")).status == "resting"
    [row] = venue.instruments()["perp"]
    assert row["min_order_value_usd"] == "25" and row["lot_size"] == "0.00001"
    default = _venue(_tape({"BTC": [(0, 100), (10, 100)]}))
    default.advance(T0)
    assert default.place(_buy("0.099", cid="s")).status == "rejected"  # the $10 floor
    assert default.instruments()["perp"][0]["min_order_value_usd"] == "10"


def test_fees_are_the_tapes_maker_and_taker_rates_by_market():
    listing = {"perp": [{"coin": "BTC", "taker_fee_rate": "0.0005", "maker_fee_rate": "0.0001"}],
               "spot": [{"coin": "PURR/USDC", "taker_fee_rate": "0.0008",
                         "maker_fee_rate": "0.0003"}]}
    tape = _tape({"BTC": [(0, 100), (10, 100)], "PURR/USDC": [(0, 5), (10, 5)]},
                 instruments=listing)
    venue = _venue(tape)
    venue.class_transfer(Decimal(500), to_perp=False)  # spot cash to buy with
    venue.advance(T0)
    venue.place(_buy("0.2", cid="p"))
    venue.place(_buy("4", cid="s", market="spot", coin="PURR/USDC"))
    perp, spot = _fills(venue.advance(T0 + 10 * S))
    assert Decimal(perp["fee_usd"]) == (Decimal("0.2") * Decimal(perp["px"])
                                        * Decimal("0.0005")).quantize(Decimal("0.000001"))
    assert Decimal(spot["fee_usd"]) == (Decimal("4") * Decimal(spot["px"])
                                        * Decimal("0.0008")).quantize(Decimal("0.000001"))
    row = venue.instruments()["perp"][0]
    assert (row["taker_fee_rate"], row["maker_fee_rate"]) == ("0.0005", "0.0001")
    assert "userFees" in row["fee_basis"] and row["execution"] == TapeVenue.EXECUTION


def test_an_immediate_or_cancel_in_flight_cannot_be_cancelled_a_limit_can():
    venue = _venue(_tape({"BTC": [(0, 100), (10, 100)]}))
    venue.advance(T0)
    market = venue.place(_buy("0.2", cid="m"))
    limit = venue.place(_buy("0.2", limit="90", cid="l"))
    assert venue.cancel(market.order_id)["status"] == "rejected"
    assert venue.cancel(limit.order_id)["status"] == "cancelled"
    assert venue.lookup("l").status == "cancelled"
    assert [o["order_id"] for o in venue.open_orders()] == [market.order_id]


def test_orders_in_flight_hold_margin_and_reduce_only_is_checked_on_arrival():
    venue = _venue(_tape({"BTC": [(0, 100), (10, 100), (20, 100)]}))
    venue.advance(T0)
    before = venue.collateral_view("BTC")["open_order_holds_usd"]
    venue.place(_sell("0.3", cid="r", reduce_only=True))  # nothing to reduce yet
    venue.place(_buy("0.3", cid="m"))
    assert venue.collateral_view("BTC")["open_order_holds_usd"] == before + Decimal(10)
    events = venue.advance(T0 + 10 * S)  # orders arrive in the order they were sent
    assert len(_fills(events)) == 1 and _fills(events)[0]["is_buy"] is True
    [refused] = _rejections(events)
    assert refused["reason"] == "not reducing position"


def test_the_fixture_tape_publishes_its_rules_and_answers_its_own_book():
    tape = Tape.load(LONGRUN)
    venue = TapeVenue(tape, coins=("BTC", "ETH"))
    venue.advance(tape.ticks[0])
    book = venue.order_book("BTC", 3)
    assert book["source"] == "synthetic" and book["asks"][0]["size"] == tape.level_size("BTC")
    # 21:05:25, when the run read BTC's book mid-tick: fresher than the recorded mid.
    venue.advance(1790283925694000000)
    assert venue.order_book("BTC", 3)["source"] == "recorded"
    # At the next recorded mid the book is older than the price: a synthetic level again.
    venue.advance(next(ts for ts in tape.ticks if ts > 1790283925694000000))
    assert venue.order_book("BTC", 3)["source"] == "synthetic"
    with pytest.raises(ValueError):
        venue.order_book("BTC", 0)
