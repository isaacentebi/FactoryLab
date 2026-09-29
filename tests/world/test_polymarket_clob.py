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


#: A Deposit Wallet (signature type 3) order signed by the official client's own code,
#: py-clob-client-v2 ``order_utils/exchange_order_builder_v2.py`` (main, read 2026-09-29),
#: run offline on this order with the test key 0x...03: the inner 65-byte signature for
#: each exchange, and the order's contents hash. ERC-7739: the outer domain is the app's
#: (the exchange's) and the TypedDataSign struct carries the wallet's own domain fields
#: (https://eips.ethereum.org/EIPS/eip-7739).
OFFICIAL_1271 = {
    False: "0f0a25123b68904374da25d6785b36c3d43b396313484ceab5f3e8e4de7e0898"
           "4bff0653060a2c963d7037f5f026803467c91f85dcc62078d7e3846132f28faf1b",
    True: "0622e0bad0388072e13834d3550ccbd55d93257ba3a0e550b7641ad0162f690c"
          "419784b9885583cc128735915b0c0c77a8bf3609f765bed72632849c1dddcb851b",
}
OFFICIAL_CONTENTS = "6b72994dbc4787d8dfc02b99140b31ce8d07d8d91a304a029dafa27831748171"


@pytest.mark.parametrize("neg_risk", [False, True])
def test_a_deposit_wallet_signature_is_the_official_clients_byte_for_byte(neg_risk):
    signer = clob.Signer(Account.from_key("0x" + "03".rjust(64, "0")))
    wallet = "0x" + "22" * 20
    order = {**ONCHAIN_ORDER, "maker": wallet, "signer": wallet, "signatureType": 3}
    expected = ("0x" + OFFICIAL_1271[neg_risk] + clob.domain_separator(neg_risk).hex()
                + OFFICIAL_CONTENTS + clob.ORDER_TYPE.encode().hex()
                + len(clob.ORDER_TYPE).to_bytes(2, "big").hex())
    assert clob.order_signature(order, neg_risk, signer) == expected


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
    server.fake._markets["fake-1"]["fee_rate"] = Decimal("0.05")
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


def test_a_first_poll_long_after_an_execution_still_books_it():
    """Astra P0 and Codex on #177: the cursor started at the first poll less 600 s, so a
    world resumed more than ten minutes after its order executed never read the fill.
    It starts at its earliest order not wholly booked, less the overlap."""
    venue, server = live_venue()
    token, intent = _intent(venue, server, "c-1", price="0.45")
    venue.intent_of = {"c-1": intent}.get
    _place(venue, token, price="0.45")
    placed_s = int(intent["order_identity"]["order"]["timestamp"]) // 1000
    server.trades[0]["match_time"] = str(placed_s + 5)
    orders = {**_orders(intent, token)}
    orders[intent["order_hash"]]["timestamp"] = intent["order_identity"]["order"]["timestamp"]
    answer = venue.poll(now_ns=(placed_s + 1_000) * 10**9, cursor={}, orders=orders)
    assert [e["kind"] for e in answer["events"]] == ["fill"]
    # Wholly booked, the order no longer holds the cursor back.
    later = venue.poll(now_ns=(placed_s + 5_000) * 10**9, cursor=answer["cursor"],
                       orders=orders)
    assert later["cursor"]["after"] == placed_s + 5 - clob.TRADE_OVERLAP_S


def _facts(neg_risk=False):
    return {"tick_size": "0.01", "neg_risk": neg_risk,
            "fees": {"enabled": True, "rate": "0.05", "exponent": "1"}}


def _signed_intent(venue, server, *, neg_risk=False, price="0.30"):
    token = _token(server)
    identity = venue.order_identity(client_id="c-1", token_id=token, is_buy=True,
                                    size=Decimal(10), price=Decimal(price),
                                    market=_facts(neg_risk))
    intent = {"handle": "decision-1", "client_id": "c-1",
              "operation": "polymarket.place_limit",
              "args": {"token_id": token, "side": "buy", "size": "10", "price": price},
              "order_hash": identity["order_hash"], "order_identity": identity}
    venue.intent_of = {"c-1": intent}.get
    return token


def test_the_fake_verifies_a_deposit_wallet_order_and_refuses_a_wrong_owner():
    """Astra P2 on #177: the fake skipped type-3 verification. It now checks the ERC-7739
    wrapper independently (eth_account's own EIP-712 encoder), against the wallet's owner."""
    owner, wallet = make_signer(5), "0x" + "33" * 20
    venue, server = live_venue(signer=owner, funder=wallet, signature_type=3)
    token = _signed_intent(venue, server)
    assert _place(venue, token, price="0.30")["status"] == "resting"
    stranger, _ = live_venue(signer=make_signer(6), funder=wallet, signature_type=3)
    stranger.send = FakeClob(server.fake, owners={wallet: owner.address})
    token = _signed_intent(stranger, server)
    refused = _place(stranger, token, price="0.30")
    assert refused["status"] == "rejected" and refused["error"] == "invalid signature"


@pytest.mark.parametrize("signature_type", [0, 3])
def test_a_corrupted_signature_is_refused_by_the_venue(monkeypatch, signature_type):
    owner = make_signer(5)
    funder = owner.address if signature_type == 0 else "0x" + "33" * 20
    venue, server = live_venue(signer=owner, funder=funder, signature_type=signature_type)
    token = _signed_intent(venue, server)
    real = clob.order_signature

    def corrupted(order, neg_risk, signer):
        signature = real(order, neg_risk, signer)
        return signature[:10] + ("0" if signature[10] != "0" else "1") + signature[11:]

    monkeypatch.setattr(clob, "order_signature", corrupted)
    refused = _place(venue, token, price="0.30")
    assert refused["status"] == "rejected" and refused["error"] == "invalid signature"
    assert server.fake._all_orders == {}


def test_a_neg_risk_market_s_order_is_signed_for_the_neg_risk_exchange_and_no_other():
    venue, server = live_venue()
    server.neg_risk_markets = {"fake-1"}
    token = _signed_intent(venue, server, neg_risk=True)
    assert _place(venue, token, price="0.30")["status"] == "resting"
    wrong, server2 = live_venue()
    server2.neg_risk_markets = {"fake-1"}
    token = _signed_intent(wrong, server2, neg_risk=False)  # signed for the other exchange
    refused = _place(wrong, token, price="0.30")
    assert refused["status"] == "rejected" and refused["error"] == "invalid signature"


def test_a_matched_trade_moves_nothing_and_a_failed_one_leaves_nothing_behind():
    venue, server = live_venue(confirm=False)
    token = _signed_intent(venue, server, price="0.45")
    cash = server.fake._cash
    _place(venue, token, price="0.45")
    assert server.fake._cash == cash and server.fake._positions[token]["size"] == 0
    server.settle("FAILED")
    assert server.fake._cash == cash and server.fake._positions[token]["size"] == 0
    token = _signed_intent(venue, server, price="0.46")
    venue.intent_of("c-1")["client_id"] = "c-1"
    _place(venue, token, price="0.46")
    server.settle("CONFIRMED")
    assert server.fake._cash == cash - Decimal("4.1")
    assert server.fake._positions[token]["size"] == 10


def test_the_fake_refuses_a_corrupted_type_suffix_and_an_unauthorised_proxy_signer(
        monkeypatch):
    """Sol P2 on #177: the fake accepted a type-3 signature whose ERC-7739 type string
    was altered, and a type-1 order signed by an EOA that does not own the wallet."""
    owner, wallet = make_signer(5), "0x" + "33" * 20
    venue, server = live_venue(signer=owner, funder=wallet, signature_type=3)
    token = _signed_intent(venue, server)
    real = clob.order_signature

    def suffix_altered(order, neg_risk, signer):
        signature = bytearray(bytes.fromhex(real(order, neg_risk, signer)[2:]))
        signature[129] ^= 0x01  # the first byte of the appended type string
        return "0x" + signature.hex()

    monkeypatch.setattr(clob, "order_signature", suffix_altered)
    refused = _place(venue, token, price="0.30")
    assert refused["status"] == "rejected" and refused["error"] == "invalid signature"
    monkeypatch.setattr(clob, "order_signature", real)
    proxy, server2 = live_venue(signer=make_signer(6), funder=wallet, signature_type=1,
                                owners={wallet: owner.address})
    token = _signed_intent(proxy, server2)
    refused = _place(proxy, token, price="0.30")
    assert refused["status"] == "rejected" and refused["error"] == "invalid signature"
    authorised, server3 = live_venue(signer=owner, funder=wallet, signature_type=1)
    token = _signed_intent(authorised, server3)
    assert _place(authorised, token, price="0.30")["status"] == "resting"


def test_what_the_pot_holds_does_not_depend_on_the_order_buys_are_booked_in():
    """With BUY orders only, a token's holding and average cost are the same whatever
    the poll, page or instant each confirmed buy is read in: no chronology is needed."""
    venue, server = live_venue()
    token = _token(server)
    orders = {f"0x{n}": {"token_id": token, "side": "buy", "size": "10", "price": price}
              for n, price in enumerate(("0.41", "0.61", "0.55"))}
    rows = [{"id": f"t-{n}", "status": "CONFIRMED", "match_time": "100",
             "taker_order_id": f"0x{n}", "size": "10", "price": price, "fee_rate_bps": "0",
             "maker_orders": []} for n, price in enumerate(("0.41", "0.61", "0.55"))]
    venue._credentials()
    books = []
    for order in (rows, rows[::-1], [rows[1]], [rows[2], rows[0]]):
        server.trades = list(order)
        cursor = venue.poll(now_ns=10**11, cursor={"after": 0}, orders=orders)["cursor"]
        if len(order) < 3:  # the rest arrives in a later poll
            server.trades = [r for r in rows if r not in order]
            cursor = venue.poll(now_ns=10**11, cursor=cursor, orders=orders)["cursor"]
        books.append(cursor["book"][token])
    assert all(book == books[0] for book in books)
    assert Decimal(books[0][0]) == 30
    assert Decimal(books[0][1]) == (Decimal("0.41") + Decimal("0.61") + Decimal("0.55")) / 3
