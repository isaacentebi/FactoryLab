"""Honest fills on a recorded tape (money path; factorylab/world/tape.py).

Each test tries to make the replay kinder than the recording and asserts it is not:
a decision never fills against the book its sender read; a resting order fills only
when a recorded mid trades through it, never on a book level that merely sits past it,
and only after this tick's arriving takers; one snapshot's liquidity is never taken
twice; no level is unbounded; the venue's order floor, its immediate-or-cancel market
orders and its maker and taker rates all bind; the last partial hour's funding is
charged.
"""

from decimal import Decimal
from pathlib import Path

import pytest

from factorylab.world.exchange import Order, OrderKind
from factorylab.world.tape import Tape, TapeVenue

T0 = 1_790_000_000 * 10**9
S = 10**9
LONGRUN = Path(__file__).parents[1] / "fixtures" / "tape" / "longrun1-2100.events.json"


#: One recorded BTC book, a second before the tape's first mid (so never current): a
#: 2 bps spread at 100 and one BTC at the top of each side, the synthetic level's size.
DEFAULT_BOOK = {"BTC": [(-1, [(Decimal("99.99"), Decimal(1))],
                         [(Decimal("100.01"), Decimal(1))])]}


def _tape(mids, books=None, instruments=None, funding=None):
    """A tape of BTC (and PURR/USDC) rows at the given second offsets."""
    books = DEFAULT_BOOK if books is None else books
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
    half = Decimal(102) * Decimal(2) / 20_000  # the recorded 2 bps, on a synthetic level
    assert Decimal(fill["px"]) == (Decimal(102) + half).quantize(Decimal("1e-10"))
    assert venue.lookup("d-1").status == "filled"
    assert Decimal(fill["fee_usd"]) == (Decimal("0.2") * Decimal(fill["px"])
                                        * Decimal("0.00045")).quantize(Decimal("0.000001"))


def _level(t, bid, ask, size="1"):
    return (t, [(Decimal(bid), Decimal(size))], [(Decimal(ask), Decimal(size))])


def test_a_book_level_past_a_resting_limit_is_not_a_fill_only_a_mid_through_it_is():
    """The cold review's first finding: a resting buy filled whenever a recorded (or
    synthetic) ask sat below its price, a book snapshot rather than a trade. It fills
    only when a recorded mid after it rested is strictly through its price."""
    tape = _tape({"BTC": [(0, 100), (10, 100), (20, 100), (30, "99.5"), (40, 99)]},
                 books={"BTC": [_level(10, "99.9", "100.1"),
                                # A wide book whose ask is far below the resting buy.
                                _level(20, "95", "96", "5"),
                                _level(30, "99.4", "99.6"),
                                _level(40, "98.9", "99.1", "0.15")]})
    venue = _venue(tape, cash="100000")
    venue.advance(T0)
    venue.place(_buy("0.2", limit="99.5", cid="rest"))
    assert _fills(venue.advance(T0 + 10 * S)) == []  # arrived; the ask is above: rests
    assert _fills(venue.advance(T0 + 20 * S)) == []  # an ask at 96, the mid at 100
    assert _fills(venue.advance(T0 + 30 * S)) == []  # the mid touches 99.5: not through
    assert venue.lookup("rest").status == "resting"
    [fill] = _fills(venue.advance(T0 + 40 * S))  # the mid, 99, is through 99.5
    assert Decimal(fill["px"]) == Decimal("99.5")  # at its own price, as a maker
    assert Decimal(fill["size"]) == Decimal("0.15")  # capped by the top ask's size
    assert Decimal(fill["fee_usd"]) == (Decimal("0.15") * Decimal("99.5")
                                        * Decimal("0.00015")).quantize(Decimal("0.000001"))
    assert venue.open_orders()[0]["size"] == Decimal("0.05")  # the rest keeps resting


def _rested(limit, *, buy=True, mids=(("0", "100.5"), ("10", "100.5")), cash="1000"):
    """A resting limit on a synthetic 2 bps book: sent at 0, rested on arrival at 10."""
    rows = [(int(t), Decimal(px)) for t, px in mids]
    venue = _venue(_tape({"BTC": rows}), cash=cash)
    venue.advance(T0)
    order = (_buy("0.2", limit=limit, cid="rest") if buy
             else _sell("0.2", limit=limit, cid="rest"))
    placed = venue.place(order)
    assert _fills(venue.advance(T0 + 10 * S)) == []
    assert venue.lookup("rest").status == "resting"
    return venue, placed.order_id


def test_a_mid_through_the_limit_with_the_ask_still_above_it_is_no_fill():
    """Sol's re-review: a mid strictly through a resting buy's price is not enough. With
    the mid at 99.995 the synthetic ask is 100.005: nobody offered at 100, and a fill
    there books ask minus limit as profit the market never offered."""
    venue, oid = _rested("100", mids=(("0", "100.5"), ("10", "100.5"), ("20", "99.995")))
    assert venue.order_book("BTC", 1)["asks"][0]["price"] > 100  # still at 10 s
    assert _fills(venue.advance(T0 + 20 * S)) == []
    assert venue.order_book("BTC", 1)["asks"][0]["price"] > 100
    assert venue.lookup("rest").status == "resting"


def test_a_mid_through_the_limit_with_the_ask_at_or_below_it_is_a_maker_fill():
    venue, oid = _rested("100", mids=(("0", "100.5"), ("10", "100.5"), ("20", "99.98")))
    [fill] = _fills(venue.advance(T0 + 20 * S))  # the ask, 99.99, is below 100
    assert Decimal(fill["px"]) == 100 and Decimal(fill["size"]) == Decimal("0.2")
    assert Decimal(fill["fee_usd"]) == (Decimal(20) * Decimal("0.00015")).quantize(
        Decimal("0.000001"))
    assert oid not in venue._rested_ns  # filled whole: no longer resting


def test_a_resting_sell_needs_the_mid_through_and_the_bid_at_or_above_its_price():
    venue, _oid = _rested("100", buy=False, mids=(
        ("0", "99.5"), ("10", "99.5"), ("20", "100.005"), ("30", "100.02")))
    assert _fills(venue.advance(T0 + 20 * S)) == []  # the bid, 99.995, is below 100
    [fill] = _fills(venue.advance(T0 + 30 * S))  # the bid, 100.01, is above it
    assert Decimal(fill["px"]) == 100 and fill["is_buy"] is False


def test_a_resting_orders_rest_instant_is_dropped_whenever_it_stops_resting():
    """Sol's re-review: the rest instant outlived its order on a cancel and a failure."""
    venue, oid = _rested("90")
    assert oid in venue._rested_ns
    assert venue.cancel(oid)["status"] == "cancelled"
    assert oid not in venue._rested_ns and venue.open_orders() == []
    # Too little collateral when the fill comes: the order is refused and dropped.
    poor, poor_oid = _rested("100", cash="5", mids=(
        ("0", "100.5"), ("10", "100.5"), ("20", "99.98")))
    assert _fills(poor.advance(T0 + 20 * S)) == []
    assert poor.lookup("rest").status == "rejected"
    assert poor_oid not in poor._rested_ns and poor.open_orders() == []


def _spot_crossing(spot_cash):
    """A spot buy of 4 PURR at 5.01 meeting 2 PURR offered at 5.00 on arrival."""
    rows = [(0, 5), (10, 5)]
    purr = [(t, [(Decimal("4.99"), Decimal(2))], [(Decimal(5), Decimal(2))]) for t, _ in rows]
    venue = _venue(_tape({"BTC": [(0, 100), (10, 100)], "PURR/USDC": rows},
                         books={"PURR/USDC": purr}))
    venue.class_transfer(Decimal(spot_cash), to_perp=False)
    venue.advance(T0)
    venue.place(_buy("4", limit="5.01", cid="spot", market="spot", coin="PURR/USDC"))
    return venue, venue.advance(T0 + 10 * S)


def test_a_partly_filled_spot_limit_rests_a_remainder_it_can_afford():
    """Codex review of #151: after a partial crossing fill, affordability was checked on
    the whole order at the taker rate, so an affordable remainder was refused and the
    order reported as rejected, zero filled, though its fill had been booked."""
    venue, events = _spot_crossing("20.05")  # 10 + 0.007 paid; 10.02 + 0.004 rests
    [fill] = _fills(events)
    assert Decimal(fill["size"]) == 2 and not _rejections(events)
    looked = venue.lookup("spot")
    assert looked.status == "resting" and looked.filled_size == 2
    assert [o["size"] for o in venue.open_orders()] == [Decimal(2)]


def test_an_unaffordable_spot_remainder_is_cancelled_and_the_fill_still_reported():
    venue, events = _spot_crossing("15")
    [fill] = _fills(events)
    [cancel] = _rejections(events)
    assert cancel["reason"] == "remainder cancelled: insufficient spot balance"
    looked = venue.lookup("spot")
    assert looked.status == "cancelled" and looked.filled_size == Decimal(fill["size"]) == 2
    assert venue.open_orders() == []


def _purr(rows):
    """A PURR/USDC tape whose book is recorded at every row: (t, mid, bid, ask)."""
    return _tape({"BTC": [(t, 100) for t, *_ in rows],
                  "PURR/USDC": [(t, Decimal(mid)) for t, mid, _b, _a in rows]},
                 books={"PURR/USDC": [(t, [(Decimal(b), Decimal(5))], [(Decimal(a), Decimal(5))])
                                      for t, _m, b, a in rows]})


def test_a_resting_spot_buy_with_exactly_its_notional_maker_fills():
    """Codex review of #151 (ce76eba): a resting spot maker filled while it still held its
    own reservation, so a valid buy needed its notional twice over."""
    venue = _venue(_purr([(0, "5.05", "5.04", "5.06"), (10, "5.05", "5.04", "5.06"),
                          (20, "5.0", "4.99", "5.00")]))
    notional = Decimal(2) * Decimal("5.01")
    exact = notional + (notional * Decimal("0.0004")).quantize(Decimal("0.000001"))
    venue.class_transfer(exact, to_perp=False)  # its notional and its maker fee, no more
    venue.advance(T0)
    venue.place(_buy("2", limit="5.01", cid="b", market="spot", coin="PURR/USDC"))
    assert _fills(venue.advance(T0 + 10 * S)) == []  # arrived below the ask: rests
    [fill] = _fills(venue.advance(T0 + 20 * S))
    assert Decimal(fill["px"]) == Decimal("5.01") and Decimal(fill["size"]) == 2
    assert venue.lookup("b").status == "filled" and venue._spot_cash == 0


def test_a_resting_spot_sell_with_exactly_its_inventory_maker_fills():
    venue = _venue(_purr([(0, "5.0", "4.99", "5.00"), (10, "5.0", "4.99", "5.00"),
                          (20, "5.0", "4.99", "5.00"), (30, "5.06", "5.055", "5.065")]))
    venue.class_transfer(Decimal(50), to_perp=False)
    venue.advance(T0)
    venue.place(_buy("2", limit="5.02", cid="b", market="spot", coin="PURR/USDC"))
    assert len(_fills(venue.advance(T0 + 10 * S))) == 1  # two PURR held, no more
    venue.place(Order("PURR/USDC", False, Decimal(2), OrderKind.LIMIT, Decimal("5.05"), "s",
                      market="spot"))
    assert _fills(venue.advance(T0 + 20 * S)) == []  # arrived above the bid: rests
    [fill] = _fills(venue.advance(T0 + 30 * S))
    assert Decimal(fill["px"]) == Decimal("5.05") and fill["is_buy"] is False
    assert venue.lookup("s").status == "filled"


def test_a_refused_spot_maker_fill_keeps_its_order_resting_and_reserved():
    venue = _venue(_purr([(0, "5.05", "5.04", "5.06"), (10, "5.05", "5.04", "5.06"),
                          (20, "5.0", "4.99", "5.00")]))
    venue.class_transfer(Decimal(11), to_perp=False)
    venue.advance(T0)
    venue.place(_buy("2", limit="5.01", cid="b", market="spot", coin="PURR/USDC"))
    venue.advance(T0 + 10 * S)
    venue._spot_cash = Decimal(1)  # its balance fell below the fill's cost while it rested
    assert _fills(venue.advance(T0 + 20 * S)) == []
    assert venue.lookup("b").status == "resting" and len(venue.open_orders()) == 1


def test_within_a_tick_the_arriving_taker_is_served_before_the_resting_maker():
    tape = _tape({"BTC": [(0, 100), (10, 100), (20, 100), (30, 99)]},
                 books={"BTC": [_level(10, "99.9", "100.1"), _level(20, "99.9", "100.1"),
                                _level(30, "98.9", "99.1", "0.3")]})
    venue = _venue(tape, cash="100000")
    venue.advance(T0)
    venue.place(_buy("0.3", limit="99.5", cid="maker"))
    venue.advance(T0 + 10 * S)  # the maker arrives and rests
    venue.advance(T0 + 20 * S)
    venue.place(_buy("0.2", cid="taker"))  # sent at 20, arrives at 30
    fills = _fills(venue.advance(T0 + 30 * S))
    taker, maker = fills
    assert taker["order_id"] == venue.lookup("taker").order_id
    assert Decimal(taker["size"]) == Decimal("0.2") and Decimal(taker["px"]) == Decimal("99.1")
    # The mid traded through the maker's price, but the taker took the top ask first.
    assert Decimal(maker["size"]) == Decimal("0.1") and Decimal(maker["px"]) == Decimal("99.5")


def test_no_level_is_unbounded_a_no_book_coin_fills_at_most_the_smallest_recorded_level():
    """The cold review's second finding: with no recorded book a level held any size."""
    tape = _tape({"BTC": [(0, 100), (10, 100)], "ETH": [(0, 2000), (10, 2000)]},
                 books={"BTC": [(-1, [(Decimal(99), Decimal("0.5"))],
                                 [(Decimal(101), Decimal("0.2"))])]})
    depth = Decimal(101) * Decimal("0.2") / 2000  # the smallest notional, in ETH at its mid
    assert tape.level_size("ETH") == depth
    venue = TapeVenue(tape, coins=("BTC", "ETH"), start_cash_usd=Decimal("1000000000"))
    venue.advance(T0)
    venue.place(Order("ETH", True, Decimal(10_000), client_id="huge"))
    events = venue.advance(T0 + 10 * S)
    [fill] = _fills(events)
    assert Decimal(fill["size"]) == depth < 10_000  # never whole
    assert venue.lookup("huge").status == "cancelled"  # immediate-or-cancel remainder
    row = next(r for r in venue.instruments()["perp"] if r["coin"] == "ETH")
    assert Decimal(row["synthetic_level_size"]) == depth
    assert row["synthetic_level_source"] == "smallest_recorded_level_on_the_tape"


def test_a_tape_that_recorded_no_book_refuses_every_order_and_says_so():
    from factorylab.world.tape import NO_LIQUIDITY

    tape = _tape({"BTC": [(0, 100), (10, 100)]}, books={})
    venue = _venue(tape, cash="1000000000")
    venue.advance(T0)
    refused = venue.place(Order("BTC", True, Decimal(10_000), client_id="huge"))
    assert refused.status == "rejected" and refused.error == NO_LIQUIDITY
    [row] = venue.instruments()["perp"]
    assert row["liquidity"] == NO_LIQUIDITY and row["synthetic_level_size"] is None
    assert venue.order_book("BTC", 5) == {"coin": "BTC", "ts_ns": T0, "source": "none",
                                          "bids": [], "asks": []}


def test_the_last_partial_hours_funding_is_charged_on_what_was_held():
    """The cold review's third finding: funding charged only at hour boundaries left the
    last partial hour free. At the world's end the position-hours held since the last
    boundary are charged at the last recorded rate and mid, once."""
    hour = 3600
    start = T0 - T0 % (hour * S)  # an hour boundary
    offset = (start - T0) // S
    rate = [[start + 60 * S, "0.001", None]]
    tape = _tape({"BTC": [(offset + 60, 100), (offset + 900, 100), (offset + 2700, 100)]},
                 funding={"BTC": rate})
    venue = _venue(tape, cash="100000")
    venue.advance(start + 60 * S)
    venue.place(_buy("1", cid="long"))
    venue.advance(start + 900 * S)  # filled at 15 minutes into the hour
    assert venue.account().positions[0].size == 1
    venue.advance(start + 2700 * S)
    cash = venue._cash
    [event] = [e for e in venue.settle_accrued_funding(start + 2700 * S)
               if e.payload["coin"] == "BTC"]
    held = Decimal(1800) / 3600  # held from 15 to 45 minutes: half an hour
    assert Decimal(event.payload["paid_usd"]) == held * 100 * Decimal("0.001")
    assert venue._cash == cash - held * 100 * Decimal("0.001")
    assert venue.settle_accrued_funding(start + 2700 * S) == []  # nothing is charged twice


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


def test_closing_the_recording_cancels_what_can_no_longer_arrive_and_refuses_more():
    venue = _venue(_tape({"BTC": [(0, 100), (10, 100)]}))
    venue.advance(T0 + 10 * S)  # the last recorded row
    ioc = venue.place(_buy("0.2", cid="ioc"))
    limit = venue.place(_buy("0.2", limit="90", cid="lim"))
    assert venue.cancel(ioc.order_id)["status"] == "rejected"  # an IOC is not cancellable
    events = venue.close_recording(T0 + 20 * S)
    cancels = {payload["order_id"]: payload for payload in _rejections(events)}
    assert set(cancels) == {ioc.order_id, limit.order_id}
    assert cancels[ioc.order_id]["reason"] == "the recorded market ended before the order arrived"
    assert venue.lookup("ioc").status == venue.lookup("lim").status == "cancelled"
    assert venue.open_orders() == []
    # Between the close and the seal (the kill's wind-down) an order meets the last
    # recorded book at once, as an immediate-or-cancel taker; after the seal, nothing.
    wound = venue.place(_buy("0.2", cid="wind-down"))
    assert wound.status == "filled" and wound.filled_size == Decimal("0.2")
    assert Decimal(_fills(venue.drain_events())[0]["px"]) == (
        Decimal(100) + Decimal(100) * 2 / 20_000).quantize(Decimal("1e-10"))
    venue.seal_recording()
    assert venue.place(_buy("0.2", cid="late")).error == "the recorded market has ended"
