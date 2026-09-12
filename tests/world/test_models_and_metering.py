from dataclasses import dataclass, field

import pytest

from factorylab.world.metering import Infeasible, Meter, MeteredModel
from factorylab.world.models import (
    FakeModel,
    ModelRequest,
    PriceTable,
    TokenPrice,
    anthropic_first_party_prices,
)


@dataclass
class _Res:
    amount: int
    handle: str
    open: bool = True


@dataclass
class TinyWallet:
    """Minimal wallet honoring reserve/commit/release with a hard balance."""

    balance: int
    reserved: int = 0
    log: list[tuple[str, int]] = field(default_factory=list)

    def reserve(self, amount, handle, reason):
        if amount > self.balance - self.reserved:
            raise RuntimeError("infeasible")
        self.reserved += amount
        self.log.append(("reserve", amount))
        return _Res(amount, handle)

    def commit(self, r, actual):
        assert r.open and actual <= r.amount
        self.reserved -= r.amount
        self.balance -= actual
        r.open = False
        self.log.append(("commit", actual))

    def commit_reported(self, r, actual):
        assert r.open
        self.reserved -= r.amount
        self.balance -= actual
        r.open = False
        self.log.append(("commit", actual))

    def release(self, r):
        assert r.open
        self.reserved -= r.amount
        r.open = False
        self.log.append(("release", r.amount))


def test_token_price_exact_integer_math() -> None:
    p = TokenPrice.from_per_mtok("5", "25")
    assert p.cost(1_000_000, 1_000_000) == 30_000_000  # $30 exactly
    assert p.cost(123, 45) == 123 * 5 + 45 * 25
    with pytest.raises(ValueError):
        TokenPrice.from_per_mtok("0.80", "4")
    with pytest.raises(ValueError):
        p.cost(-1, 0)


def test_first_party_table_and_unpriced_lookup_fails() -> None:
    t = anthropic_first_party_prices()
    assert t.cost("claude-opus-5", 1000, 100) == 1000 * 5 + 100 * 25
    assert t.cost("claude-haiku-4-5", 1000, 100) == 1000 * 1 + 100 * 5
    with pytest.raises(KeyError):
        t.price("gpt-6-astra")


def test_fake_model_is_deterministic_and_scripted() -> None:
    m = FakeModel(script={"buy": "BUY BTC 0.001"}, fixed_input_tokens=10, fixed_output_tokens=4)
    req = ModelRequest("claude-opus-5", "sys", ({"role": "user", "content": "should we buy?"},))
    a, b = m.complete(req), m.complete(req)
    assert a == b and a.text == "BUY BTC 0.001" and (a.input_tokens, a.output_tokens) == (10, 4)


def test_metering_reserves_then_commits_actual_before_return() -> None:
    w = TinyWallet(balance=1_000_000)
    meter = Meter(w)
    seen: list[str] = []

    def execute():
        seen.append(f"balance_during={w.balance} reserved_during={w.reserved}")
        return "ok"

    out = meter.run(handle="h1", reason="test", ceiling=500, execute=execute, cost_of=lambda r: 120)
    assert out.result == "ok" and out.cost == 120 and out.reserved == 500 and out.overrun == 0
    assert seen == ["balance_during=1000000 reserved_during=500"]
    assert w.balance == 1_000_000 - 120 and w.reserved == 0
    assert w.log == [("reserve", 500), ("commit", 120)]


def test_metering_failure_releases_and_commits_nothing() -> None:
    w = TinyWallet(balance=1000)
    meter = Meter(w)
    failures: list[BaseException] = []

    def boom():
        raise RuntimeError("vendor down")

    with pytest.raises(RuntimeError):
        meter.run(
            handle="h", reason="t", ceiling=100, execute=boom, cost_of=lambda r: 0,
            on_failure=failures.append,
        )
    assert w.balance == 1000 and w.reserved == 0 and len(failures) == 1
    assert w.log == [("reserve", 100), ("release", 100)]


def test_metering_infeasible_when_wallet_cannot_cover_ceiling() -> None:
    w = TinyWallet(balance=50)
    ran = []
    with pytest.raises(Infeasible):
        Meter(w).run(
            handle="h", reason="t", ceiling=100, execute=lambda: ran.append(1), cost_of=lambda r: 1
        )
    assert ran == [] and w.log == []


def test_metering_overrun_debits_the_full_reported_cost() -> None:
    w = TinyWallet(balance=1000)
    out = Meter(w).run(
        handle="h", reason="t", ceiling=100, execute=lambda: "r", cost_of=lambda r: 130
    )
    assert out.cost == 130 and out.overrun == 30 and w.balance == 870


def test_metered_model_prices_the_serving_model() -> None:
    prices = PriceTable()
    prices.register("claude-opus-5", TokenPrice(5, 25))
    prices.register("claude-haiku-4-5", TokenPrice(1, 5))
    w = TinyWallet(balance=10_000_000)
    fake = FakeModel(fixed_input_tokens=100, fixed_output_tokens=10)
    mm = MeteredModel(fake, prices, Meter(w))
    req = ModelRequest(
        "claude-opus-5", "s", ({"role": "user", "content": "x" * 40},), max_tokens=100
    )
    out = mm.complete(req, handle="d1")
    assert out.cost == 100 * 5 + 10 * 25
    assert out.reserved == mm.ceiling(req) >= out.cost
    assert w.balance == 10_000_000 - out.cost
