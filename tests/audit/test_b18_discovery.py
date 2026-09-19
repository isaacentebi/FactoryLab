"""B18: discovery has bounded pages and one cached index per runtime window."""

from urllib.parse import parse_qs, urlsplit

from factorylab.world.market import discover
from factorylab.world.x402 import HTTPResponse


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
