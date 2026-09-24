"""The Polymarket read client against recorded public responses, and the simulated venue.

The fixtures under ``fixtures/polymarket/`` are real answers from the public,
unauthenticated Gamma and CLOB endpoints, recorded 2026-09-22 (the search answer
is cut to one event and two markets; nothing else was edited).
"""

import json
import threading
from decimal import Decimal
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest

from factorylab.world.polymarket import (
    TICK_SIZES,
    FakePolymarket,
    LiveOrderAdapter,
    PolymarketReader,
    PolymarketRefused,
    PolymarketUnavailable,
    http_get_json,
    parse_book,
    parse_market,
    payout,
)

FIXTURES = Path(__file__).parent / "fixtures" / "polymarket"
FED_YES = "111061902544814266207267295505639408607400625795891618462682726460921782993748"


def fixture(name):
    # The client parses numbers as Decimal; so does this, so a float never appears.
    return json.loads((FIXTURES / name).read_text(), parse_float=Decimal)


class Recorded:
    """A transport that answers from the recordings and remembers what was asked."""

    ROUTES = {"/public-search": "gamma_search.json", "/markets/2589812": "gamma_market.json",
              "/markets": "gamma_markets.json", "/book": "clob_book.json",
              "/midpoint": "clob_midpoint.json"}

    def __init__(self):
        self.urls = []

    def __call__(self, url):
        self.urls.append(url)
        return fixture(self.ROUTES[urlsplit(url).path])


def test_search_flattens_events_into_bounded_parsed_markets():
    get = Recorded()
    markets = PolymarketReader(get=get).search_markets("fed rates", 5)
    query = parse_qs(urlsplit(get.urls[0]).query)
    assert urlsplit(get.urls[0]).netloc == "gamma-api.polymarket.com"
    assert query["q"] == ["fed rates"] and query["limit_per_type"] == ["5"]
    assert [m["market_id"] for m in markets] == ["2589810", "2589811"]
    first = markets[0]
    # Gamma encodes outcomes, prices and token ids as JSON text; they pair up here.
    assert [o["outcome"] for o in first["outcomes"]] == ["Yes", "No"]
    assert all(o["token_id"].isdigit() and len(o["token_id"]) > 60 for o in first["outcomes"])
    assert Decimal(first["tick_size"]) in TICK_SIZES and first["min_order_size"] == "5"
    assert first["neg_risk"] is True and first["accepting_orders"] is True


def test_market_detail_carries_contract_fields_fees_and_bounded_rules_text():
    market = PolymarketReader(get=Recorded()).market("2589812")
    assert market["market_id"] == "2589812"
    assert market["condition_id"].startswith("0xdf9bf27e")
    assert market["end_date"] == "2026-10-29T03:59:00Z"
    assert market["outcomes"][0] == {"outcome": "Yes", "outcome_index": 0,
                                     "token_id": FED_YES, "price": "0.455"}
    assert market["best_bid"] == "0.45" and market["best_ask"] == "0.46"
    # The fee is the market's feeSchedule (CLOB V2), not the legacy base fee.
    assert market["fees"] == {"enabled": True, "rate": "0.05", "exponent": "1",
                              "taker_only": True}
    assert market["resolution_source"] is None  # "" in the answer: nothing to publish
    assert 0 < len(market["description"]) <= 2000
    assert all(not isinstance(v, float) for v in market.values())


def test_fee_free_market_and_resolved_market_parse_honestly():
    free = parse_market(fixture("gamma_markets.json")[0])
    assert free["market_id"] == "665374"
    assert free["fees"]["enabled"] is False and free["fees"]["rate"] == "0"
    resolved = parse_market(fixture("gamma_market_closed.json")[0])
    assert resolved["closed"] is True
    assert resolved["uma_resolution_status"] == "resolved"
    assert [(o["outcome"], o["price"]) for o in resolved["outcomes"]] == [
        ("Over", "1"), ("Under", "0")]


CLOSED_OVER = "55277242749159958514265240140241311155293095895205911209777418570254035077633"


def test_a_resolved_markets_token_is_found_among_the_closed_and_redeems_at_its_price():
    """Gamma's /markets lists open markets unless asked for closed ones, and its open
    listing lags a resolution (read 2026-09-23), so the closed listing is asked first."""
    urls = []

    def get(url):
        urls.append(url)
        query = parse_qs(urlsplit(url).query)
        return fixture("gamma_market_closed.json") if query.get("closed") == ["true"] else []

    market = PolymarketReader(get=get).market_of_token(CLOSED_OVER)
    assert market["market_id"] == "4283025" and market["closed"] is True
    assert [parse_qs(urlsplit(u).query).get("closed") for u in urls] == [["true"]]
    assert payout(market, CLOSED_OVER) == Decimal(1)
    assert payout(market, market["outcomes"][1]["token_id"]) == Decimal(0)
    assert payout(market, "1") is None
    assert PolymarketReader(get=lambda url: []).market_of_token(CLOSED_OVER) is None
    # A token the closed listing does not hold is looked up among the open markets.
    urls.clear()
    reader = PolymarketReader(get=lambda url: urls.append(url) or (
        [] if "closed=true" in url else fixture("gamma_markets.json")))
    listed = parse_market(fixture("gamma_markets.json")[0])["outcomes"][0]["token_id"]
    assert reader.market_of_token(listed)["market_id"] == "665374"
    assert [parse_qs(urlsplit(u).query).get("closed") for u in urls] == [["true"], None]


def test_only_a_final_redemption_is_a_payout():
    closed = parse_market(fixture("gamma_market_closed.json")[0])
    assert payout(parse_market(fixture("gamma_market.json")), FED_YES) is None  # open

    def variant(prices, status="resolved"):
        return {**closed, "uma_resolution_status": status, "outcomes": [
            {**o, "price": p} for o, p in zip(closed["outcomes"], prices, strict=True)]}

    assert payout(variant(("0.5", "0.5")), CLOSED_OVER) == Decimal("0.5")
    # In its challenge window or in dispute, a closed market has not paid anything yet.
    assert payout(variant(("1", "0"), status="proposed"), CLOSED_OVER) is None
    assert payout(variant(("1", "0"), status=None), CLOSED_OVER) == Decimal(1)
    # Closed without a redemption vector (an old market reads ["0", "0"]).
    for prices in (("0", "0"), ("0.9", "0.1"), ("1", "1"), (None, "1")):
        assert payout(variant(prices), CLOSED_OVER) is None
    assert payout({**closed, "closed": False}, CLOSED_OVER) is None


def test_the_simulated_venue_publishes_its_resolution_as_a_payout():
    fake = FakePolymarket(resolutions={"fake-1": (5, 1)})
    yes, no = (o["token_id"] for o in fake.market("fake-1")["outcomes"])
    assert payout(fake.market_of_token(yes), yes) is None
    fake.advance(1)
    fake.advance(10)
    assert (payout(fake.market_of_token(yes), yes), payout(fake.market_of_token(no), no)) == (
        Decimal(0), Decimal(1))


def test_a_market_whose_outcomes_and_tokens_do_not_pair_is_dropped():
    raw = dict(fixture("gamma_market.json"))
    raw["clobTokenIds"] = json.dumps([FED_YES])
    assert parse_market(raw) is None
    assert parse_market("not a market") is None


def test_book_is_best_first_on_both_sides_whatever_order_the_clob_sent():
    raw = fixture("clob_book.json")
    # The CLOB lists bids ascending and asks descending: the best of each is last.
    assert raw["bids"][-1]["price"] == "0.45" and raw["asks"][-1]["price"] == "0.46"
    book = parse_book(raw, 3)
    assert [lvl["price"] for lvl in book["bids"]] == ["0.45", "0.44", "0.43"]
    assert [lvl["price"] for lvl in book["asks"]] == ["0.46", "0.47", "0.48"]
    assert book["midpoint"] == "0.455"
    assert book["token_id"] == FED_YES and book["tick_size"] == "0.01"
    assert book["min_order_size"] == "5" and book["neg_risk"] is True


def test_reader_book_and_midpoint_use_the_public_clob():
    get = Recorded()
    reader = PolymarketReader(get=get)
    assert reader.order_book(FED_YES, 2)["bids"][0]["price"] == "0.45"
    assert reader.midpoint(FED_YES) == "0.455"
    assert all(urlsplit(u).netloc == "clob.polymarket.com" for u in get.urls)
    assert parse_qs(urlsplit(get.urls[0]).query) == {"token_id": [FED_YES]}


def test_book_that_is_not_an_object_is_unavailable_not_empty():
    with pytest.raises(PolymarketUnavailable):
        parse_book(["not", "a", "book"], 5)


class _Server(BaseHTTPRequestHandler):
    status = 500
    body = b'{"error": "secret-remote-diagnostic"}'

    def do_GET(self):  # noqa: N802 - the stdlib's name
        self.send_response(self.status)
        self.end_headers()
        self.wfile.write(self.body)

    def log_message(self, *args):
        pass


def _serve(status, body):
    handler = type("Handler", (_Server,), {"status": status, "body": body})
    server = HTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f"http://127.0.0.1:{server.server_address[1]}/book"


def test_http_errors_name_a_status_and_never_the_response_body():
    server, url = _serve(500, b'{"error": "secret-remote-diagnostic"}')
    try:
        with pytest.raises(PolymarketUnavailable) as caught:
            http_get_json(url, timeout_s=5)
    finally:
        server.shutdown()
    assert str(caught.value) == "HTTP 500"
    assert "secret" not in repr(caught.value) and caught.value.__cause__ is None


def test_a_body_that_is_not_json_is_refused_without_echoing_it():
    server, url = _serve(200, b"<html>secret-page</html>")
    try:
        with pytest.raises(PolymarketUnavailable) as caught:
            http_get_json(url, timeout_s=5)
    finally:
        server.shutdown()
    assert "secret" not in str(caught.value)


def test_json_numbers_arrive_as_decimals_never_floats():
    server, url = _serve(200, b'{"mid": 0.455}')
    try:
        assert http_get_json(url, timeout_s=5) == {"mid": Decimal("0.455")}
    finally:
        server.shutdown()


# --- the simulated venue -----------------------------------------------------------------

def venue(**kwargs):
    return FakePolymarket(start_usdc=Decimal(100), **kwargs)


def yes(fake, market="fake-1"):
    return fake.market(market)["outcomes"][0]["token_id"]


def test_fake_client_ids_are_idempotent_and_never_trade_twice():
    fake = venue()
    token = yes(fake)
    first = fake.place(client_id="d:tool:0", token_id=token, is_buy=True,
                       size=Decimal(10), price=Decimal("0.45"))
    again = fake.place(client_id="d:tool:0", token_id=token, is_buy=True,
                       size=Decimal(10), price=Decimal("0.45"))
    assert first == again and first["status"] == "filled"
    assert fake.account()["positions"][0]["size"] == "10"
    assert fake.lookup("d:tool:0") == first
    assert fake.lookup("never-sent")["status"] == "rejected"


def test_fake_crossing_order_takes_the_ask_and_pays_the_taker_fee():
    fake = venue()
    token = yes(fake, "fake-2")  # mid 0.70, fee rate 0.05
    result = fake.place(client_id="c", token_id=token, is_buy=True, size=Decimal(10),
                        price=Decimal("0.80"))
    assert result["avg_px"] == "0.71"
    fee = Decimal(10) * Decimal("0.05") * Decimal("0.71") * Decimal("0.29")
    [fill] = fake.drain_events()
    assert Decimal(fill["fee_usd"]) == fee.quantize(Decimal("0.00001"))
    assert Decimal(fake.account()["usdc"]) == Decimal(100) - Decimal("7.1") - Decimal(
        fill["fee_usd"])


def test_fake_resting_orders_hold_collateral_and_fill_as_maker_without_fee():
    fake = venue(seed=3)
    token = yes(fake, "fake-2")
    fake.place(client_id="rest", token_id=token, is_buy=True, size=Decimal(10),
               price=Decimal("0.60"))
    account = fake.account()
    assert Decimal(account["usdc_available"]) == Decimal(94)  # 0.60 x 10 held
    refused = fake.place(client_id="too-much", token_id=token, is_buy=True,
                         size=Decimal(200), price=Decimal("0.60"))
    assert refused["status"] == "rejected"
    fake._markets["fake-2"]["mid"] = Decimal("0.58")  # the market walks down through it
    fills = [e for e in fake.advance(1) if e["kind"] == "fill"]
    assert fills and fills[0]["fee_usd"] == "0" and fills[0]["px"] == "0.60"


def test_fake_refuses_off_tick_prices_small_orders_and_selling_what_it_lacks():
    fake = venue()
    token = yes(fake)
    for price, size, is_buy in ((Decimal("0.455"), Decimal(10), True),
                                (Decimal("0.40"), Decimal(1), True),
                                (Decimal("0.40"), Decimal(10), False)):
        answer = fake.place(client_id=f"{price}{size}{is_buy}", token_id=token,
                            is_buy=is_buy, size=size, price=price)
        assert answer["status"] == "rejected"


def test_fake_resolution_cancels_resting_orders_and_redeems_the_pot():
    fake = venue(resolutions={"fake-1": (5, 1)})
    yes_token, no_token = (o["token_id"] for o in fake.market("fake-1")["outcomes"])
    fake.place(client_id="a", token_id=yes_token, is_buy=True, size=Decimal(10),
               price=Decimal("0.50"))
    fake.place(client_id="b", token_id=no_token, is_buy=True, size=Decimal(5),
               price=Decimal("0.10"))  # rests below the book
    fake.drain_events()
    events = fake.advance(5)
    kinds = [e["kind"] for e in events]
    assert "cancelled" in kinds
    [resolution] = [e for e in events if e["kind"] == "resolution"]
    assert resolution["token_id"] == yes_token and resolution["payout"] == "0"
    assert Decimal(resolution["realized_usd"]) == Decimal("-4.1")
    assert fake.account()["positions"] == [] and fake.account()["open_orders"] == []
    assert fake.market("fake-1")["closed"] is True
    late = fake.place(client_id="late", token_id=yes_token, is_buy=True, size=Decimal(10),
                      price=Decimal("0.50"))
    assert late["status"] == "rejected"


def test_fake_is_deterministic_for_one_seed():
    def run():
        fake = venue(seed=7)
        return [fake.advance(n * 10**9) for n in range(1, 30)], fake.account()

    assert run() == run()


def test_live_order_adapter_refuses_every_write():
    adapter = LiveOrderAdapter()
    for call in (lambda: adapter.place(token_id="1"), lambda: adapter.cancel(order_id="1"),
                 lambda: adapter.lookup("x")):
        with pytest.raises(PolymarketRefused):
            call()
