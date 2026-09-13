"""B18: discovery has bounded pages and one cached index per runtime window."""

from urllib.parse import parse_qs, urlsplit

from factorylab.world.market import X402Provider, discover
from factorylab.world.x402 import HTTPResponse
from tests.conftest import make_runtime


def test_unique_endless_index_has_a_page_budget():
    calls = []

    def endless(method, url, payload, headers):
        offset = int(parse_qs(urlsplit(url).query)["offset"][0])
        calls.append(offset)
        return HTTPResponse(200, {"items": [
            {"resource": f"https://seller-{offset + n}.example", "description": "inference"}
            for n in range(100)
        ], "pagination": {"offset": offset, "limit": 100}})

    assert discover(query="absent", transport=endless) == []
    assert calls == [0, 100, 200, 300, 400]


def test_queries_share_the_index_until_the_next_window():
    calls = []

    def index(method, url, payload, headers):
        calls.append(url)
        return HTTPResponse(200, {"items": [
            {"resource": "https://a.example", "description": "inference"},
            {"resource": "https://b.example", "description": "search"},
        ]})

    rt = make_runtime()
    try:
        rt.market.target = X402Provider(transport=index)
        rt._manage_reserve_window()
        assert len(rt._discover_market(query="inference")) == 1
        assert len(rt._discover_market(query="search")) == 1
        assert len(calls) == 1
        rt.clock.now_ns += rt.m.novelty.window_ns
        rt._manage_reserve_window()
        assert len(rt._discover_market()) == 2 and len(calls) == 2
    finally:
        rt._ledger_lock.close()
