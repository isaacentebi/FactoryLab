"""The one door (``world/polymarket_wire.py``), fuzzed by table.

Architect's decision on Sol's round-7 review of #177: every venue answer the pot reads
is parsed strictly against its documented shape; one that does not conform is malformed
as a whole (a read unread, an acknowledgement uncertain), and only a documented 4xx
refusal frees a commitment. The contradiction scan walks raw JSON first and never
raises. Each row below is a shape Sol sent, or a null, a wrong type, an upper-case
hash, an out-of-range price, an extra or missing field, or a fee under another name.
"""

from decimal import Decimal

import pytest

from factorylab.world import polymarket_wire as wire

OURS = "0x" + "ab" * 32
OTHER = "0x" + "ee" * 32
TOKEN = "100000000000000000000"
SIGNED = {OURS: (Decimal(10), Decimal("0.30"))}


def _without(row, key):
    return {k: v for k, v in row.items() if k != key}


# --- the acknowledgement: resting only for a clean success, else uncertain -------------

GOOD_ACK = {"success": True, "errorMsg": "", "orderID": OURS, "status": "live",
            "makingAmount": "0", "takingAmount": "0"}


@pytest.mark.parametrize("answer,status", [
    (GOOD_ACK, "resting"),
    ({**GOOD_ACK, "orderID": OURS.upper().replace("0X", "0x")}, "resting"),
    ({**GOOD_ACK, "status": "matched"}, "uncertain"),
    ({**GOOD_ACK, "status": "LIVE"}, "uncertain"),
    ({**GOOD_ACK, "orderID": OTHER}, "uncertain"),
    ({**GOOD_ACK, "orderID": "0xo"}, "uncertain"),
    ({**GOOD_ACK, "errorMsg": "not enough balance / allowance"}, "uncertain"),
    ({**GOOD_ACK, "success": "true"}, "uncertain"),
    (_without(GOOD_ACK, "success"), "uncertain"),
    (_without(GOOD_ACK, "orderID"), "uncertain"),
    (_without(GOOD_ACK, "status"), "uncertain"),
    # Sol P1 (round 7) #4: a refusal that names this order and says it rests.
    ({"success": False, "errorMsg": "not enough balance / allowance", "orderID": OURS,
      "status": "live"}, "uncertain"),
    ({"success": False, "errorMsg": "not enough balance / allowance"}, "uncertain"),
    (None, "uncertain"), ([GOOD_ACK], "uncertain"), ("live", "uncertain"),
])
def test_an_acknowledgement_rests_only_when_it_says_so_cleanly(answer, status):
    assert wire.ack(answer, OURS)["status"] == status


# --- the refusal: a whitelist ------------------------------------------------------------

TICK = f"order {OURS} is invalid. Price (0.305) breaks minimum tick size rule: 0.01"


@pytest.mark.parametrize("status,body,refused", [
    (400, {"error": "invalid post-only order: order crosses book"}, True),
    (400, {"error": "not enough balance / allowance"}, True),
    (400, {"error": TICK}, True),
    (400, {"error": TICK.replace(OURS, OURS.upper().replace("0X", "0x"))}, True),
    (400, {"error": TICK.replace(OURS, OTHER)}, False),
    (429, {"error": "Too Many Requests"}, True),
    (400, {"error": "Too Many Requests"}, False),
    (503, {"error": "invalid post-only order: order crosses book"}, False),
    # Sol P1 (round 7) #1: a duplicate beside a balance error proves nothing.
    (400, {"error": "Duplicated", "detail": "not enough balance / allowance"}, False),
    (400, {"error": f"order {OURS} is invalid. Duplicated."}, False),
    (400, {"error": "not enough balance / allowance", "orderID": OURS}, False),
    (400, {"error": "not enough balance / allowance", "success": True}, False),
    (400, {"error": "not enough balance / allowance", "status": "live"}, False),
    (400, {"error": "invalid signature"}, False),
    (400, {"error": "Not enough balance / allowance"}, False),
    (400, {"error": None}, False), (400, None, False), (400, ["error"], False),
    (400, {}, False),
])
def test_only_a_documented_refusal_frees_a_commitment(status, body, refused):
    assert (wire.refusal(status, body, OURS) is not None) is refused


# --- an order read back ------------------------------------------------------------------

GOOD_ORDER = {"id": OURS, "status": "CANCELED", "asset_id": TOKEN, "side": "BUY",
              "price": "0.30", "original_size": "10", "size_matched": "5",
              "owner": "key-1", "outcome": "Yes"}


@pytest.mark.parametrize("answer", [
    None, [], "order", _without(GOOD_ORDER, "size_matched"), _without(GOOD_ORDER, "id"),
    _without(GOOD_ORDER, "original_size"), {**GOOD_ORDER, "size_matched": "garbage"},
    {**GOOD_ORDER, "size_matched": True}, {**GOOD_ORDER, "size_matched": None},
    {**GOOD_ORDER, "size_matched": 5.0}, {**GOOD_ORDER, "size_matched": "11"},
    {**GOOD_ORDER, "size_matched": "-1"}, {**GOOD_ORDER, "price": "1.4"},
    {**GOOD_ORDER, "price": "0"}, {**GOOD_ORDER, "price": "-0.3"},
    {**GOOD_ORDER, "status": "cancelled"}, {**GOOD_ORDER, "status": "EXPIRED"},
    {**GOOD_ORDER, "status": "MATCHED"}, {**GOOD_ORDER, "id": OTHER},
    {**GOOD_ORDER, "id": "0xo"}, {**GOOD_ORDER, "side": "SELL"},
    {**GOOD_ORDER, "side": "buy"}, {**GOOD_ORDER, "original_size": "1"},
    {**GOOD_ORDER, "price": "0.31"}, {**GOOD_ORDER, "asset_id": 100},
])
def test_an_order_read_back_that_does_not_conform_is_malformed(answer):
    with pytest.raises(wire.Malformed):
        wire.order(answer, expect=OURS, signed=SIGNED[OURS])


def test_an_order_read_back_is_normalised_once():
    read = wire.order({**GOOD_ORDER, "id": OURS.upper().replace("0X", "0x"), "price": "0.29",
                       "extra": {"anything": 1}}, expect=OURS, signed=SIGNED[OURS])
    assert (read.order_id, read.status, read.matched, read.price) == (
        OURS, "cancelled", Decimal(5), Decimal("0.29"))


# --- a trades page -----------------------------------------------------------------------

LEG = {"order_id": OURS, "matched_amount": "5", "price": "0.30", "side": "BUY",
       "fee_rate_bps": "0", "owner": "key-1"}
TRADE = {"id": "t-1", "status": "CONFIRMED", "match_time": "100", "taker_order_id": OTHER,
         "side": "SELL", "size": "5", "price": "0.30", "fee_rate_bps": "100",
         "maker_orders": [LEG]}


def _page(*rows, **extra):
    return {"data": list(rows), "next_cursor": "LTE=", **extra}


@pytest.mark.parametrize("page", [
    None, [], {"data": [TRADE]}, {"data": TRADE, "next_cursor": "LTE="},
    _page(None, TRADE),                                    # Sol P1 (round 7) #2
    _page({**TRADE, "maker_orders": [None, LEG]}),         # Sol P1 (round 7) #2
    _page({**TRADE, "maker_orders": [{**LEG, "price": "1.40"}]}),  # Sol P1 (round 7) #5
    _page({**TRADE, "maker_orders": [{**LEG, "price": "0.31"}]}),  # above the limit
    _page({**TRADE, "maker_orders": [{**LEG, "price": "-0.30"}]}),
    _page({**TRADE, "maker_orders": [{**LEG, "matched_amount": "-5"}]}),
    _page({**TRADE, "maker_orders": [{**LEG, "matched_amount": "garbage"}]}),
    _page({**TRADE, "maker_orders": [{**LEG, "matched_amount": "11"}]}),
    _page({**TRADE, "maker_orders": [{**LEG, "side": "SELL"}]}),
    _page({**TRADE, "maker_orders": [_without(LEG, "side")]}),
    _page({**TRADE, "maker_orders": [{**LEG, "order_id": "0xo"}]}),
    _page({**TRADE, "maker_orders": None}), _page(_without(TRADE, "id")),
    _page({**TRADE, "id": ""}), _page({**TRADE, "id": 7}),
    _page(_without(TRADE, "match_time")), _page({**TRADE, "match_time": "-1"}),
    _page({**TRADE, "match_time": True}), _page({**TRADE, "status": "SETTLED"}),
    _page({**TRADE, "taker_order_id": "0xo"}), _page({**TRADE, "price": "1"}),
    _page(_without(TRADE, "side")), _page({**TRADE, "size": "0"}),
    _page(TRADE, next_cursor=None), {"data": [TRADE], "next_cursor": ""},
])
def test_a_trades_page_that_does_not_conform_is_malformed_whole(page):
    with pytest.raises(wire.Malformed):
        wire.trades_page(page, SIGNED)


def test_a_trades_page_keeps_only_this_world_s_legs_normalised():
    upper = OURS.upper().replace("0X", "0x")
    trades, cursor = wire.trades_page(_page(
        {**TRADE, "maker_orders": [{**LEG, "order_id": upper}, {**LEG, "order_id": OTHER}]},
        {**TRADE, "id": "t-2", "maker_orders": [{**LEG, "order_id": OTHER}]},
        {**TRADE, "id": "t-3", "status": "TRADE_STATUS_FAILED", "match_time": 101}),
        SIGNED)
    assert cursor == "LTE="
    assert [(t.trade_id, t.status, [(leg.order_id, leg.size) for leg in t.legs])
            for t in trades] == [("t-1", "CONFIRMED", [(OURS, Decimal(5))]),
                                 ("t-3", "FAILED", [(OURS, Decimal(5))])]


# --- the contradiction scan: raw, defensive, never raising --------------------------------


@pytest.mark.parametrize("page,found", [
    (_page(TRADE), 0),
    (_page(None, {**TRADE, "maker_orders": [{**LEG, "fee_rate_bps": "500"}]}), 1),
    (_page({**TRADE, "maker_orders": [None, {**LEG, "fee_rate_bps": "500"}]}), 1),
    (_page({**TRADE, "maker_orders": [{**LEG, "order_id": OURS.upper().replace("0X", "0x"),
                                       "fee_rate_bps": "500"}]}), 1),
    (_page({**TRADE, "maker_orders": [{**_without(LEG, "fee_rate_bps"),
                                       "feeRateBps": "500"}]}), 1),
    (_page({**TRADE, "maker_orders": [{**LEG, "fees": 0, "fee": "0"}]}), 0),
    (_page({**TRADE, "maker_orders": [{**LEG, "fee": "0.0"}]}), 1),
    (_page({**TRADE, "maker_orders": [{**LEG, "fee": None}]}), 1),
    (_page({**TRADE, "maker_orders": [{**LEG, "matched_amount": "garbage",
                                       "FEE": "1"}]}), 1),
    (_page({**TRADE, "taker_order_id": OURS.upper().replace("0X", "0x"),
            "maker_orders": []}), 1),
    (_page({**TRADE, "maker_orders": [{**LEG, "order_id": OTHER, "fee_rate_bps": "9"}]}), 0),
    (_page({**TRADE, "maker_orders": [{**LEG, "order_id": 7, "fee_rate_bps": "9"}]}), 0),
    ([None, {**TRADE, "taker_order_id": OURS}], 1),
    (None, 0), ("garbage", 0), ({"data": {"x": 1}}, 0),
])
def test_the_scan_finds_every_contradiction_and_never_raises(page, found):
    assert len(wire.scan_contradictions(page, [OURS])) == found


# --- the pot: balance, positions, cancel ----------------------------------------------------


@pytest.mark.parametrize("answer", [
    None, {}, {"balance": "-1"}, {"balance": "1.5"}, {"balance": True},
    {"balance": Decimal("1.5")}, {"balance": None}, {"balance": ""},
])
def test_a_balance_that_does_not_conform_is_malformed(answer):
    with pytest.raises(wire.Malformed):
        wire.balance(answer)


POSITION = {"asset": TOKEN, "size": "10", "avgPrice": "0.3", "outcomeIndex": 0,
            "outcome": "Yes", "redeemable": False}


@pytest.mark.parametrize("answer", [
    None, {"data": []}, [None], [_without(POSITION, "avgPrice")],
    [_without(POSITION, "size")], [{**POSITION, "size": "-1"}],
    [{**POSITION, "avgPrice": "1.2"}], [{**POSITION, "outcomeIndex": True}],
    [{**POSITION, "outcomeIndex": "0"}], [{**POSITION, "asset": 100}],
    [{**POSITION, "outcome": 3}],
])
def test_a_positions_page_that_does_not_conform_is_malformed(answer):
    with pytest.raises(wire.Malformed):
        wire.positions_page(answer)


@pytest.mark.parametrize("answer,outcome", [
    ({"canceled": [OURS], "not_canceled": {}}, "cancelled"),
    ({"canceled": [OURS.upper().replace("0X", "0x")], "not_canceled": {}}, "cancelled"),
    ({"canceled": [], "not_canceled": {OURS: "matched"}}, "not_canceled"),
    ({"canceled": [OURS], "not_canceled": {OURS: "x"}}, "unknown"),
    ({"canceled": [], "not_canceled": {}}, "unknown"),
])
def test_a_cancel_answer_names_one_outcome_or_none(answer, outcome):
    assert wire.cancel_answer(answer, OURS)[0] == outcome


@pytest.mark.parametrize("answer", [
    None, {"canceled": [OURS]}, {"canceled": OURS, "not_canceled": {}},
    {"canceled": ["0xo"], "not_canceled": {}}, {"canceled": [], "not_canceled": []},
])
def test_a_cancel_answer_that_does_not_conform_is_malformed(answer):
    with pytest.raises(wire.Malformed):
        wire.cancel_answer(answer, OURS)


# --- the market and its book ------------------------------------------------------------------

MARKET = {"id": "123", "conditionId": "0x" + "cd" * 32, "question": "Q?",
          "outcomes": '["Yes", "No"]', "clobTokenIds": f'["{TOKEN}", "100000000000000000001"]',
          "outcomePrices": '["0.4", "0.6"]', "active": True, "closed": False,
          "acceptingOrders": True, "enableOrderBook": True, "negRisk": False,
          "orderPriceMinTickSize": "0.01", "orderMinSize": "5"}


def test_a_conforming_market_parses():
    assert wire.market(MARKET)["tick_size"] == "0.01"


@pytest.mark.parametrize("answer", [
    None, _without(MARKET, "negRisk"), {**MARKET, "negRisk": "false"},
    {**MARKET, "orderPriceMinTickSize": "0.02"}, {**MARKET, "orderMinSize": "0"},
    {**MARKET, "outcomePrices": '["1.4", "0.6"]'}, {**MARKET, "outcomes": '["A","B","C"]'},
    {**MARKET, "clobTokenIds": '["x", "y"]'}, {**MARKET, "outcomes": "not json"},
    {**MARKET, "id": True}, {**MARKET, "umaResolutionStatus": 1},
])
def test_a_market_that_does_not_conform_is_malformed(answer):
    with pytest.raises(wire.Malformed):
        wire.market(answer)


@pytest.mark.parametrize("answer", [
    None, {"bids": []}, {"bids": [None], "asks": []},
    {"bids": [{"price": "1.2", "size": "5"}], "asks": []},
    {"bids": [{"price": "0.4", "size": "0"}], "asks": []},
    {"bids": [], "asks": [{"price": 0.5, "size": "5"}]},
])
def test_a_book_that_does_not_conform_is_malformed(answer):
    with pytest.raises(wire.Malformed):
        wire.book(answer, 1)
