"""The free execution fixture trades on a taker-only tape, without a live venue."""

import json
from decimal import Decimal
from pathlib import Path

from factorylab.world.exchange import Order, OrderKind
from factorylab.world.models import ModelRequest
from factorylab.world.tape import TAPE_FORMAT, Tape, TapeVenue
from scripts.fastloop import PolicyProvider

WORLD = Path(__file__).parents[2] / "worlds" / "edition6-testnet-rehearsal.toml"


def test_scripted_market_legs_realize_pnl_on_a_taker_only_tape():
    """Unmodified demo answers open and close at later recorded books, net of fees."""
    tape = Tape.from_data({
        "format": TAPE_FORMAT, "ticks": [0, 1, 2],
        "mids": {"BTC": [[0, "100000"], [1, "100000"], [2, "101000"]]},
        "books": {"BTC": [
            [ts, [[bid, "1"]], [[ask, "1"]]]
            for ts, bid, ask in ((0, "99990", "100010"),
                                 (1, "99990", "100010"),
                                 (2, "100990", "101010"))]},
        "instruments": {"perp": [{"coin": "BTC", "lot_size": "0.00001",
                                     "tick_size": "1", "min_order_value_usd": "10"}]},
        "fees": {"BTC": {"venue_read": {"taker": [[0, "0.001", []]]}}},
    })
    venue = TapeVenue(tape, coins=("BTC",), start_cash_usd=Decimal("1000"))
    policy = PolicyProvider(WORLD)
    tools = []
    for _ in range(6):
        inputs = {"kind": "Tick", "payload": {"mids": {"BTC": "100000"}}}
        request = ModelRequest("scripted", "", ({"role": "user", "content":
            "INPUTS\n" + json.dumps(inputs) + "\n\nOUTCOME SCHEMA\n{}"},))
        answer = json.loads(policy.complete(request).text)
        for call in answer.get("tool_calls", []):
            if not call["tool"].startswith("venue.place_"):
                continue
            tools.append(call["tool"])
            args = call["args"]
            limit = call["tool"] == "venue.place_limit"
            before = len(venue.fills(0))
            result = venue.place(Order(
                coin=args["coin"], is_buy=args["side"] == "buy", size=Decimal(args["size"]),
                kind=OrderKind.LIMIT if limit else OrderKind.MARKET,
                limit_px=Decimal(args["price"]) if limit else None))
            if limit:
                assert result.status == "rejected"  # No recorded maker fee.
                continue
            assert result.status == "resting"
            assert len(venue.fills(0)) == before  # Never fill the quote the sender saw.
            venue.advance(before + 1)
            assert len(venue.fills(0)) == before + 1

    assert tools.count("venue.place_market") == 2
    assert "venue.place_limit" in tools
    opening, closing = venue.fills(0)
    assert (opening.is_buy, closing.is_buy) == (True, False)
    assert (opening.px, closing.px) == (Decimal("100010"), Decimal("100990"))
    assert closing.realized == Decimal("0.980000")
    assert opening.fee + closing.fee == Decimal("0.201000")
    assert venue.account().positions == ()
    assert venue.account().cash_usd == Decimal("1000.779000")
