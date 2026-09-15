from dataclasses import dataclass, field

import pytest

from factorylab.world.metering import Infeasible, Meter, MeteredModel, UnbilledFailure
from factorylab.world.models import (
    FakeModel,
    ModelRequest,
    ModelResponse,
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
        raise UnbilledFailure("vendor did not run or bill")

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


class _Provider:
    """A provider whose balance drops by what each call really cost, whether or not
    the call returned a bill: the settlement measures the drop."""

    def __init__(self, balance, script):
        self.balance, self.script, self.reads, self.calls = balance, list(script), 0, 0

    def complete(self, req):
        self.calls += 1
        true_cost, bill = self.script.pop(0)
        self.balance -= true_cost
        if bill == "drop":
            raise ConnectionError("the reply never arrived")
        return ModelResponse(req.model_id, "{}", 10, 5, "end_turn", cost_micro=bill)

    def balance_of(self, model_id):
        self.reads += 1
        if self.balance is None:
            raise OSError("balance endpoint unavailable")
        return self.balance


def _settled_model(provider, settlement, wallet):
    prices = PriceTable({"vendor": TokenPrice(1, 1)})
    return MeteredModel(provider, prices, Meter(wallet), settlement=settlement)


REQ = ModelRequest("vendor", "s", ({"role": "user", "content": "x" * 40},), max_tokens=100)


def test_uncertain_bill_is_charged_at_the_ceiling_then_settled_to_the_true_cost() -> None:
    from factorylab.kernel.ledger import Ledger
    from factorylab.kernel.wallet import Wallet
    from factorylab.world.metering import BillingUncertain, BillSettlement

    provider = _Provider(1_000_000, [(15, 15), (9, "drop"), (15, 15), (7, "drop")])
    notes = []
    settlement = BillSettlement(provider.balance_of, record=notes.append)
    wallet = Wallet(10_000, Ledger())
    model = _settled_model(provider, settlement, wallet)
    ceiling = model.ceiling(REQ)
    # The reference is taken lazily: a launch-time refresh reads the balance once.
    assert settlement.refresh("vendor") == 1_000_000 and provider.reads == 1
    assert model.complete(REQ, handle="d1").cost == 15
    assert settlement.reference == {"openrouter": {"balance": 1_000_000, "spent": 15}}
    assert provider.reads == 1  # a settled bill reads nothing
    with pytest.raises(BillingUncertain) as info:
        model.complete(REQ, handle="d2")
    # Booked at the ceiling, then settled to the drop in the provider's balance.
    assert info.value.cost == 9 and provider.reads == 2
    assert wallet.balance == 10_000 - 15 - 9 and wallet.uncertain_bills == {}
    kinds = [i["kind"] for i in wallet.ledger._recovery_items()]
    assert kinds[-3:] == ["metering.uncertain", "wallet.commit", "wallet.settle_uncertain"]
    settled = wallet.ledger._recovery_items()[-1]
    assert settled["amount"] == ceiling - 9 and settled["actual_micro"] == 9
    assert settled["provisional_micro"] == ceiling
    assert (settled["provider_balance_before"], settled["spent_since_before"],
            settled["provider_balance_after"]) == (1_000_000, 15, 1_000_000 - 24)
    assert notes[-1] == {**notes[-1], "kind": "metering.settlement", "status": "settled",
                         "actual_micro": 9, "released_micro": ceiling - 9}
    # The read after a settlement is the next reference; ordinary calls accrue on it.
    assert settlement.reference == {"openrouter": {"balance": 1_000_000 - 24, "spent": 0}}
    model.complete(REQ, handle="d3")
    with pytest.raises(BillingUncertain) as info:
        model.complete(REQ, handle="d4")
    assert info.value.cost == 7 and wallet.balance == 10_000 - 15 - 9 - 15 - 7
    assert wallet.check_conservation() and wallet.uncertain_bills == {}


def test_without_a_reference_the_first_uncertain_bill_keeps_its_ceiling() -> None:
    from factorylab.kernel.ledger import Ledger
    from factorylab.kernel.wallet import Wallet
    from factorylab.world.metering import BillingUncertain, BillSettlement

    provider = _Provider(1_000_000, [(9, "drop"), (7, "drop")])
    notes = []
    settlement = BillSettlement(provider.balance_of, record=notes.append)
    wallet = Wallet(10_000, Ledger())
    model = _settled_model(provider, settlement, wallet)
    ceiling = model.ceiling(REQ)
    with pytest.raises(BillingUncertain) as info:
        model.complete(REQ, handle="d1")
    assert info.value.cost == ceiling and wallet.balance == 10_000 - ceiling
    assert list(wallet.uncertain_bills) == ["wallet-0"]
    assert notes[-1]["status"] == "reference_taken"
    assert settlement.reference == {"openrouter": {"balance": 1_000_000 - 9, "spent": 0}}
    with pytest.raises(BillingUncertain) as info:
        model.complete(REQ, handle="d2")
    assert info.value.cost == 7 and wallet.balance == 10_000 - ceiling - 7
    assert list(wallet.uncertain_bills) == ["wallet-0"]  # the first stays at its ceiling


def test_a_failed_or_implausible_balance_read_keeps_the_ceiling_and_never_guesses() -> None:
    from factorylab.kernel.ledger import Ledger
    from factorylab.kernel.wallet import Wallet
    from factorylab.world.metering import BillingUncertain, BillSettlement

    provider = _Provider(1_000_000, [(9, "drop"), (7, "drop"), (0, "drop"), (0, "drop")])
    notes = []
    settlement = BillSettlement(provider.balance_of, record=notes.append)
    wallet = Wallet(100_000, Ledger())
    model = _settled_model(provider, settlement, wallet)
    ceiling = model.ceiling(REQ)
    settlement.refresh("vendor")
    provider.balance = None  # the read fails
    with pytest.raises(BillingUncertain) as info:
        model.complete(REQ, handle="d1")
    assert info.value.cost == ceiling and wallet.balance == 100_000 - ceiling
    assert notes[-1]["status"] == "balance_unavailable" and notes[-1]["error"] == "OSError"
    assert settlement.reference == {}  # a reference the failed call spent from is untrusted
    provider.balance = 1_000_000 - 9 - 7
    with pytest.raises(BillingUncertain) as info:
        model.complete(REQ, handle="d2")  # takes a fresh reference, keeps its ceiling
    assert info.value.cost == ceiling and notes[-1]["status"] == "reference_taken"
    provider.balance += 5  # a top-up between calls: the drop is negative
    with pytest.raises(BillingUncertain) as info:
        model.complete(REQ, handle="d3")
    assert info.value.cost == ceiling and notes[-1]["status"] == "outside_ceiling"
    assert notes[-1]["measured_micro"] == -5
    provider.balance -= ceiling + 1  # more than the ceiling: not this call's alone
    with pytest.raises(BillingUncertain) as info:
        model.complete(REQ, handle="d4")
    assert info.value.cost == ceiling and notes[-1]["status"] == "outside_ceiling"
    assert wallet.balance == 100_000 - 4 * ceiling and len(wallet.uncertain_bills) == 4
    assert wallet.check_conservation()
    # A provider with no bounded balance (None) settles nothing either.
    settlement = BillSettlement(lambda _m: None, record=notes.append)
    assert settlement.refresh("vendor") is None and settlement.reference == {}
