"""Decision six opens public knowledge without granting unregistered trades."""


import pytest

from factorylab.world.exchange import FakeExchange
from factorylab.world.venue_tools import VenueTools


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def deny(*args, **kwargs):
        pytest.fail("real network is forbidden")
    monkeypatch.setattr("socket.create_connection", deny)
    monkeypatch.setattr("socket.getaddrinfo", deny)


def test_public_reads_include_unseeded_coin_but_trades_refuse():
    ex = FakeExchange(coins=("BTC", "ETH"))
    venue = VenueTools(ex, coins=("BTC",))
    assert "error" not in venue.call("venue.order_book", {"coin": "ETH", "depth": 2})
    assert "error" not in venue.call("venue.funding_history", {"coin": "ETH", "n": 2})
    assert "ETH" in venue.call("venue.mids", {})["mids"]
    assert {r["coin"] for r in venue.call("venue.instruments", {})["perp"]} == {"BTC", "ETH"}
    assert "error" in venue.call("venue.place_market", {"coin": "ETH", "side": "buy", "size": "1"})
