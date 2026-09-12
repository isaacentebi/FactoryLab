"""B19: an unlimited key does not make real provider credits unknowable."""

from types import SimpleNamespace

import pytest

from factorylab.runtime.live import Reconciler
from factorylab.world.openrouter import OpenRouterProvider
from factorylab.world.treasury import provider_pots


def test_unlimited_openrouter_key_uses_account_credits():
    calls = []

    def transport(method, path, body):
        calls.append(path)
        assert method == "GET" and path == "/credits" and body is None
        return {"data": {"total_credits": "12.345678", "total_usage": "2.000001"}}

    seed, sellers = provider_pots(OpenRouterProvider(transport=transport))
    assert seed == 10_345_677 and sellers == {} and calls == ["/credits"]
    pots = {"venue": 2_000_000, "reserve": 3_000_000, "seed": seed,
            "sellers": sellers, "pending": False}
    result = Reconciler.snapshot(15_345_677, None, SimpleNamespace(), pots_view=pots)
    assert result["pots_micro"] == 15_345_677 and result["within_tolerance"]


@pytest.mark.parametrize("limit,expected", [("5", 3_750_000), (None, None)])
def test_scoped_credits_fallback_uses_limit_and_usage(limit, expected):
    calls = []

    def transport(method, path, body):
        calls.append(path)
        if path == "/credits":
            raise PermissionError("scoped key")
        return {"data": {"limit": limit, "usage": "1.25", "limit_remaining": None}}

    seed, _ = provider_pots(OpenRouterProvider(transport=transport))
    assert seed == expected and calls == ["/credits", "/key"]
