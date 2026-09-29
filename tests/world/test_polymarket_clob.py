"""The live Polymarket order venue: signing, auth, identity, fills and its request budget.

Chapter II §II.b (the hard cast): an order is built, signed and sent only for a durable
intent, its identity is its hash, a lost answer is looked up and never resent, and a fill
is booked once, when final. Every request goes to ``FakeClob`` in process; every signer
is generated here. Nothing touches a network or a funded key.
"""

from decimal import Decimal

import pytest
from eth_account import Account

from factorylab.world import polymarket_clob as clob
from factorylab.world.polymarket import PolymarketRefused, PolymarketUnavailable
from tests.world.fake_clob import FakeClob, live_venue, make_signer

# The order and digest checked against the V2 CTF Exchange's own ``hashOrder`` by an
# eth_call on Polygon (0xE111180000d2663C0091e4f400237545B87B996B, read 2026-09-29).
ONCHAIN_ORDER = {
    "salt": 479249096354, "maker": "0x1111111111111111111111111111111111111111",
    "signer": "0x1111111111111111111111111111111111111111",
    "tokenId": "105366567478328005392567869753167454028138413658796937240111452331819204147927",
    "makerAmount": "5200000", "takerAmount": "10000000", "side": 0, "signatureType": 0,
    "timestamp": "1790693394000", "metadata": clob.ZERO32, "builder": clob.ZERO32}
ONCHAIN_DIGEST = "0x683dcb48ddb035f0b7a838120bbf04b90cc69cc17bf995cfa5a3156ca3e52eb0"


def test_the_order_hash_is_the_exchanges_own_hash_order():
    assert clob.order_hash(ONCHAIN_ORDER, False) == ONCHAIN_DIGEST
    assert clob.domain_separator(False).hex() == (
        "3264e159346253e26a64e00b69032db0e7d32f94628de3e6eecb50304d7af3d2")
    assert clob.domain_separator(True).hex() == (
        "9b858f53327b0bd13af8ec14cfb35234fb9eb7b0504d1a4e61f433840d30e81a")
    # The signature is not part of the hash; every signed field is.
    for key, value in (("salt", 1), ("timestamp", "1"), ("makerAmount", "5200001")):
        assert clob.order_hash({**ONCHAIN_ORDER, key: value}, False) != ONCHAIN_DIGEST
    assert clob.order_hash(ONCHAIN_ORDER, True) != ONCHAIN_DIGEST


def test_amounts_are_the_documented_ones_and_an_order_off_its_tick_is_refused():
    d = Decimal
    # place-orders: BUY 10 shares at 0.52 -> makerAmount 5,200,000, takerAmount 10,000,000.
    assert clob.order_amounts(True, d(10), d("0.52"), d("0.01")) == (5_200_000, 10_000_000)
    assert clob.order_amounts(False, d(10), d("0.52"), d("0.01")) == (10_000_000, 5_200_000)
    assert clob.order_price_size(0, 5_200_000, 10_000_000) == (d("0.52"), d(10))
    assert clob.order_amounts(True, d("12.5"), d("0.1234"), d("0.0001")) == (1_542_500,
                                                                             12_500_000)
    for size, price, tick in ((d(10), d("0.525"), d("0.01")), (d(10), d("0.99"), d("0.1")),
                              (d("10.001"), d("0.5"), d("0.01")), (d(0), d("0.5"), d("0.01")),
                              (d(10), d("0.5"), d("0.02")), (d(10), d(1), d("0.01"))):
        with pytest.raises(PolymarketRefused):
            clob.order_amounts(True, size, price, tick)


def test_a_salt_is_fixed_by_its_identity_and_safe_as_a_json_number():
    assert clob.salt_of("ns:nonce:decision-1:tool:0") == clob.salt_of("ns:nonce:decision-1:tool:0")
    assert clob.salt_of("ns:nonce:decision-1:tool:0") != clob.salt_of("ns:other:decision-1:tool:0")
    assert 0 <= clob.salt_of("x") <= 2 ** 53 - 1


def test_l1_and_l2_auth_verify_and_the_hmac_signs_the_exact_body():
    signer = make_signer()
    headers = clob.l1_headers(signer, 1_790_000_000)
    recovered = Account._recover_hash(clob.auth_digest(signer.address, 1_790_000_000, 0),
                                      signature=bytes.fromhex(headers["POLY_SIGNATURE"][2:]))
    assert recovered.lower() == signer.address == headers["POLY_ADDRESS"]
    creds = clob.Credentials("k", FakeClob.SECRET, "p")
    l2 = clob.l2_headers(creds, signer.address, 1, "POST", "/order", '{"a":1}')
    import base64
    import hashlib
    import hmac

    key = base64.urlsafe_b64decode(FakeClob.SECRET)
    expected = base64.urlsafe_b64encode(
        hmac.new(key, b'1POST/order{"a":1}', hashlib.sha256).digest()).decode()
    assert l2["POLY_SIGNATURE"] == expected
    assert clob.l2_headers(creds, signer.address, 1, "POST", "/order", '{"a": 1}')[
        "POLY_SIGNATURE"] != expected
    # A secret without its padding decodes to the same key.
    assert clob.hmac_signature(FakeClob.SECRET.rstrip("="), 1, "GET", "/x", "") == (
        clob.hmac_signature(FakeClob.SECRET, 1, "GET", "/x", ""))
    assert "secret" not in repr(creds).lower() and FakeClob.SECRET not in repr(creds)


def test_the_signer_never_shows_its_key(monkeypatch):
    signer = make_signer(7)
    assert repr(signer) == f"Signer({signer.address})"
    monkeypatch.delenv("POLYMARKET_PRIVATE_KEY", raising=False)
    with pytest.raises(PolymarketRefused, match="not set"):
        clob.Signer.from_environment("POLYMARKET_PRIVATE_KEY")
    monkeypatch.setenv("POLYMARKET_PRIVATE_KEY", "not-a-key-0123")
    with pytest.raises(PolymarketRefused) as refused:
        clob.Signer.from_environment("POLYMARKET_PRIVATE_KEY")
    assert "not-a-key-0123" not in str(refused.value)


def test_a_deposit_wallet_order_carries_the_erc7739_wrapper():
    signer = make_signer(3)
    wallet = "0x" + "22" * 20
    order = {**ONCHAIN_ORDER, "maker": wallet, "signer": wallet, "signatureType": 3}
    signature = clob.order_signature(order, False, signer)
    raw = bytes.fromhex(signature[2:])
    assert len(raw) == 65 + 32 + 32 + len(clob.ORDER_TYPE) + 2
    assert raw[65:97] == clob.domain_separator(False)
    assert raw[-2:] == len(clob.ORDER_TYPE).to_bytes(2, "big")
    assert raw[129:-2].decode() == clob.ORDER_TYPE


# --- the order path ---------------------------------------------------------------------

def _token(server, market="fake-1", side=0):
    return server.fake._markets[market]["tokens"][side]


def _intent(venue, server, client_id, *, side="buy", size="10", price="0.45", market="fake-1"):
    token = _token(server, market)
    facts = {"tick_size": "0.01", "neg_risk": False,
             "fees": {"enabled": True, "rate": "0.05", "exponent": "1"}}
    identity = venue.order_identity(client_id=client_id, token_id=token,
                                    is_buy=side == "buy", size=Decimal(size),
                                    price=Decimal(price), market=facts)
    intent = {"handle": "decision-1", "client_id": client_id,
              "operation": "polymarket.place_limit",
              "args": {"token_id": token, "side": side, "size": size, "price": price},
              "order_hash": identity["order_hash"], "order_identity": identity}
    return token, intent


def _place(venue, token, *, side="buy", size="10", price="0.45", client_id="c-1"):
    return venue.place(client_id=client_id, token_id=token, is_buy=side == "buy",
                       size=Decimal(size), price=Decimal(price))


def test_no_order_is_signed_or_sent_without_its_durable_intent():
    venue, server = live_venue()
    token, intent = _intent(venue, server, "c-1")
    with pytest.raises(PolymarketRefused, match="no durable intent"):
        _place(venue, token)
    assert server.calls == []  # nothing reached the venue, not even credentials
    intents = {"c-1": intent}
    venue.intent_of = intents.get
    # An intent that names another order is no authority for this one.
    with pytest.raises(PolymarketRefused, match="does not name this order"):
        _place(venue, token, size="11")
    intents["c-1"] = {**intent, "order_hash": "0x" + "00" * 32}
    with pytest.raises(PolymarketRefused, match="rebuild"):
        _place(venue, token)
    assert server.calls == []
    intents["c-1"] = intent
    result = _place(venue, token)
    assert result["order_id"] == intent["order_hash"]
    assert ("POST", "/order") in server.calls


def test_an_order_rests_or_is_read_back_by_its_hash_never_by_its_ack():
    venue, server = live_venue()
    token, intent = _intent(venue, server, "c-1", price="0.30")
    venue.intent_of = {"c-1": intent}.get
    assert _place(venue, token, price="0.30")["status"] == "resting"
    token, crossing = _intent(venue, server, "c-2", price="0.45")
    venue.intent_of = {"c-2": crossing}.get
    answer = _place(venue, token, price="0.45", client_id="c-2")
    # "matched" is an ACK, never a fill: the order's own status is read back by hash.
    assert answer["status"] == "uncertain"
    looked = venue.lookup("c-2", order_id=crossing["order_hash"])
    assert looked["status"] == "filled" and looked["filled_size"] == "10"


def test_a_lost_answer_is_looked_up_and_a_repeat_is_never_a_second_order():
    venue, server = live_venue()
    token, intent = _intent(venue, server, "c-1", price="0.30")
    venue.intent_of = {"c-1": intent}.get
    server.lose_answer = True
    with pytest.raises(PolymarketUnavailable):  # the runtime reads it as uncertain
        _place(venue, token, price="0.30")
    assert venue.lookup("c-1", order_id=intent["order_hash"])["status"] == "resting"
    # A resend of the same signed order is refused by the venue as a duplicate and read
    # as uncertain, never as a new order.
    assert _place(venue, token, price="0.30")["status"] == "uncertain"
    assert len(server.fake._all_orders) == 1
    # An order the venue never saw is uncertain, never a negative acknowledgement.
    unknown = venue.lookup("c-9", order_id="0x" + "ab" * 32)
    assert unknown["status"] == "uncertain"


@pytest.mark.parametrize("failure,status", [
    (clob.ClobHttpError(503), "uncertain"), (PolymarketUnavailable("transport"), "uncertain"),
    (clob.ClobHttpError(400, "price breaks the market's tick"), "rejected")])
def test_what_an_order_answer_proves(failure, status):
    venue, server = live_venue()
    token, intent = _intent(venue, server, "c-1", price="0.30")
    venue.intent_of = {"c-1": intent}.get
    venue._credentials()
    server.fail_next = [failure]
    if isinstance(failure, PolymarketUnavailable):
        # A transport failure raises: the runtime records it as uncertain.
        with pytest.raises(PolymarketUnavailable):
            _place(venue, token, price="0.30")
        return
    assert _place(venue, token, price="0.30")["status"] == status


def test_the_pot_s_own_requests_stop_at_their_budget_and_are_not_sent():
    venue, server = live_venue(budget=3)
    token, intent = _intent(venue, server, "c-1", price="0.30")
    venue.intent_of = {"c-1": intent}.get
    venue.mark_book(token)
    venue.mark_book(token)
    venue.mark_book(token)
    sent = len(server.calls)
    with pytest.raises(clob.BudgetSpent):
        venue.mark_book(token)
    result = _place(venue, token, price="0.30")
    assert result["status"] == "rejected" and "budget" in result["error"]
    assert len(server.calls) == sent  # neither left the process


def test_a_cancel_needs_its_intent_and_answers_what_the_venue_did():
    venue, server = live_venue()
    token, intent = _intent(venue, server, "c-1", price="0.30")
    cancel = {"handle": "decision-2", "client_id": "c-2", "operation": "polymarket.cancel",
              "args": {"order_id": intent["order_hash"]}}
    venue.intent_of = {"c-1": intent}.get
    _place(venue, token, price="0.30")
    with pytest.raises(PolymarketRefused):
        venue.cancel(client_id="c-2", order_id=intent["order_hash"])
    venue.intent_of = {"c-1": intent, "c-2": cancel}.get
    assert venue.cancel(client_id="c-2", order_id=intent["order_hash"])["status"] == "cancelled"
    again = venue.cancel(client_id="c-2", order_id=intent["order_hash"])
    assert again["status"] == "rejected"


def _orders(intent, token):
    return {intent["order_hash"]: {"token_id": token, "side": "buy", "size": "10",
                                   "price": "0.45", "market_id": "fake-1",
                                   "fee_rate": "0.05", "fee_exponent": "1"}}


def test_a_fill_is_reported_once_when_final_and_a_failed_trade_never():
    venue, server = live_venue(confirm=False)
    token, intent = _intent(venue, server, "c-1")
    venue.intent_of = {"c-1": intent}.get
    _place(venue, token)
    orders = _orders(intent, token)
    first = venue.poll(now_ns=1, cursor={}, orders=orders)
    assert first["events"] == [] and first["complete"]  # MATCHED is not final
    server.settle("CONFIRMED")
    second = venue.poll(now_ns=2, cursor=first["cursor"], orders=orders)
    (fill,) = second["events"]
    assert fill["kind"] == "fill" and fill["size"] == "10" and fill["px"] == "0.41"
    # The taker fee at the market's schedule: 10 x 0.05 x 0.41 x 0.59.
    assert Decimal(fill["fee_usd"]) == Decimal("0.12095")
    third = venue.poll(now_ns=3, cursor=second["cursor"], orders=orders)
    assert [e for e in third["events"] if e["kind"] == "fill"] == []
    server.trades[0]["status"] = "FAILED"
    server.trades.append({**server.trades[0], "id": "t-failed", "status": "FAILED"})
    fourth = venue.poll(now_ns=4, cursor=third["cursor"], orders=orders)
    assert [e for e in fourth["events"] if e["kind"] == "fill"] == []


def test_a_fill_of_another_order_is_not_this_worlds():
    venue, server = live_venue()
    token, intent = _intent(venue, server, "c-1")
    venue.intent_of = {"c-1": intent}.get
    _place(venue, token)
    server.extra_fills = [{"id": "t-x", "status": "CONFIRMED", "match_time": "5",
                           "taker_order_id": "0x" + "cd" * 32, "size": "99", "price": "0.5",
                           "maker_orders": []}]
    events = venue.poll(now_ns=1, cursor={}, orders=_orders(intent, token))["events"]
    assert [e["order_id"] for e in events] == [intent["order_hash"]]


def test_a_failed_read_leaves_the_cursor_where_it_was():
    venue, server = live_venue()
    token, intent = _intent(venue, server, "c-1")
    venue.intent_of = {"c-1": intent}.get
    _place(venue, token)
    venue._credentials()
    server.fail_next = [PolymarketUnavailable("transport")]
    answer = venue.poll(now_ns=1, cursor={}, orders=_orders(intent, token))
    assert not answer["complete"] and answer["events"] == []
    assert answer["cursor"]["seen"] == {}
    again = venue.poll(now_ns=2, cursor=answer["cursor"], orders=_orders(intent, token))
    assert len([e for e in again["events"] if e["kind"] == "fill"]) == 1


def test_a_resolution_pays_what_the_pot_holds_of_the_token():
    from tests.runtime.test_polymarket_surface import still_fake

    fake = still_fake(resolutions={"fake-1": (10 ** 12, 0)})
    venue, server = live_venue(fake)
    token, intent = _intent(venue, server, "c-1")
    venue.intent_of = {"c-1": intent}.get
    _place(venue, token)
    orders = _orders(intent, token)
    cursor = venue.poll(now_ns=1, cursor={}, orders=orders)["cursor"]
    server.advance(10 ** 12)
    events = venue.poll(now_ns=10 ** 12, cursor=cursor, orders=orders)["events"]
    (resolution,) = [e for e in events if e["kind"] == "resolution"]
    assert resolution["payout"] == "1" and resolution["size"] == "10"
    assert Decimal(resolution["realized_usd"]) == Decimal("5.9")


def test_the_account_is_the_custodian_s_word():
    venue, server = live_venue()
    token, intent = _intent(venue, server, "c-1", price="0.30")
    venue.intent_of = {"c-1": intent}.get
    _place(venue, token, price="0.30")
    account = venue.account(markets={token: "fake-1"})
    assert Decimal(account["usdc"]) == Decimal(50)
    assert Decimal(account["usdc_available"]) == Decimal(47)  # 10 x 0.30 rests
    assert account["open_orders"][0]["order_id"] == intent["order_hash"]


def test_a_trade_listed_late_is_read_and_nothing_is_booked_twice():
    venue, server = live_venue()
    token, intent = _intent(venue, server, "c-1")
    venue.intent_of = {"c-1": intent}.get
    _place(venue, token)
    orders = _orders(intent, token)
    server.trades[0]["match_time"] = "5000"
    first = venue.poll(now_ns=1, cursor={}, orders=orders)
    assert len(first["events"]) == 1
    # A second leg of the order, matched earlier but listed only now, inside the overlap.
    server.extra_fills = [{"id": "t-late", "status": "MATCHED", "match_time": "4800",
                           "taker_order_id": "0x" + "cd" * 32, "size": "1", "price": "0.4",
                           "maker_orders": [{"order_id": intent["order_hash"],
                                             "matched_amount": "0", "price": "0.45",
                                             "side": "BUY"}]}]
    second = venue.poll(now_ns=2, cursor=first["cursor"], orders=orders)
    assert second["events"] == [] and second["cursor"]["after"] == 5000 - clob.TRADE_OVERLAP_S
    for poll in range(3):
        again = venue.poll(now_ns=3 + poll, cursor=second["cursor"], orders=orders)
        assert again["events"] == []  # the first fill is never booked again


def test_more_trade_pages_than_one_poll_reads_are_read_over_several_polls():
    """Codex P1 on #177: past MAX_TRADE_PAGES a poll discarded every row it read and left
    the cursor where it was, so every later poll raised again and no fill ever settled."""
    venue, server = live_venue()
    token, intent = _intent(venue, server, "c-1", price="0.30")
    venue.intent_of = {"c-1": intent}.get
    _place(venue, token, price="0.30")  # rests; its fills are the rows below
    server.page_size = 2
    pages = clob.MAX_TRADE_PAGES + 1
    server.trades = [{"id": f"t-{n}", "status": "CONFIRMED", "match_time": str(100 + n),
                      "taker_order_id": "0x" + "cd" * 32, "size": "0.5", "price": "0.3",
                      "maker_orders": [{"order_id": intent["order_hash"],
                                        "matched_amount": "0.5", "price": "0.3",
                                        "side": "BUY"}]}
                     for n in range(2 * pages)]
    orders = _orders(intent, token)
    booked, cursor = [], {}
    for now in range(1, 4):
        answer = venue.poll(now_ns=now, cursor=cursor, orders=orders)
        cursor = answer["cursor"]
        booked += [e for e in answer["events"] if e["kind"] == "fill"]
    assert len(booked) == 2 * pages  # every row, over two polls, none twice
    assert sum(Decimal(e["size"]) for e in booked) == Decimal(pages)


def test_a_first_poll_starts_at_the_world_never_at_the_wallets_history():
    venue, server = live_venue()
    token, intent = _intent(venue, server, "c-1", price="0.30")
    venue.intent_of = {"c-1": intent}.get
    _place(venue, token, price="0.30")
    now_s = 1_790_000_000
    answer = venue.poll(now_ns=now_s * 10**9, cursor={}, orders=_orders(intent, token))
    assert answer["cursor"]["after"] == now_s - clob.TRADE_OVERLAP_S
