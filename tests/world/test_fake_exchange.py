from decimal import Decimal

from factorylab.world.events import WorldEventKind
from factorylab.world.exchange import NS_PER_HOUR, FakeExchange, Order, OrderKind


def _ex(**kw) -> FakeExchange:
    return FakeExchange(seed=7, start_cash_usd=Decimal("100"), **kw)


def test_determinism_same_seed_same_path() -> None:
    a, b = _ex(), _ex()
    for t in range(1, 6):
        ea = a.advance(t * 1_000)
        eb = b.advance(t * 1_000)
        assert [e.payload for e in ea] == [e.payload for e in eb]
    assert a.mids() == b.mids()


def test_market_buy_pays_half_spread_and_fee_and_marks_equity() -> None:
    ex = _ex(price_path={"BTC": [Decimal("50000")] * 10, "ETH": [Decimal("2000")] * 10})
    ex.advance(1)
    r = ex.place(Order("BTC", True, Decimal("0.001")))
    assert r.status == "filled"
    # mid 50000, spread 2bps -> half = 5, fill at 50005
    assert r.avg_px == Decimal("50005")
    notional = Decimal("0.001") * Decimal("50005")
    fee = (notional * Decimal("3.5") / Decimal(10_000)).quantize(Decimal("0.000001"))
    acct = ex.account()
    assert acct.cash_usd == Decimal("100") - fee
    # unrealized: (mid - entry) * size = (50000-50005)*0.001 = -0.005
    assert acct.equity_usd == Decimal("100") - fee + Decimal("-0.005")
    fills = ex.drain_events()
    assert fills[0].kind is WorldEventKind.FILL
    assert fills[0].payload["px"] == "50005"


def test_close_realizes_pnl_and_flip_resets_entry() -> None:
    path = {"BTC": [Decimal("100"), Decimal("110"), Decimal("120")], "ETH": [Decimal("1")] * 3}
    ex = _ex(price_path=path, spread_bps=Decimal("0"), fee_bps=Decimal("0"))
    ex.advance(1)
    ex.place(Order("BTC", True, Decimal("1")))
    ex.advance(2)  # mid 110
    ex.place(Order("BTC", False, Decimal("1")))  # close
    assert ex.account().cash_usd == Decimal("110")
    assert ex.account().positions == ()
    ex.place(Order("BTC", False, Decimal("2")))  # short 2 at 110
    ex.advance(3)  # mid 120
    ex.place(Order("BTC", True, Decimal("3")))  # flip to long 1 at 120
    pos = ex.account().positions[0]
    assert pos.size == Decimal("1") and pos.entry_px == Decimal("120")
    # short 2 from 110 to 120 lost 20
    assert ex.account().cash_usd == Decimal("90")


def test_margin_rejection_emits_event_and_changes_nothing() -> None:
    ex = _ex(price_path={"BTC": [Decimal("50000")] * 3, "ETH": [Decimal("1")] * 3})
    ex.advance(1)
    before = ex.account()
    r = ex.place(Order("BTC", True, Decimal("1")))  # 50k notional on $100 at 3x
    assert r.status == "rejected" and r.error == "insufficient margin"
    assert ex.account() == before
    evs = ex.drain_events()
    assert evs[0].kind is WorldEventKind.ORDER_REJECTED


def test_limit_order_rests_then_fills_when_crossed() -> None:
    path = {"BTC": [Decimal("100"), Decimal("100"), Decimal("90")], "ETH": [Decimal("1")] * 3}
    ex = _ex(price_path=path, fee_bps=Decimal("0"))
    ex.advance(1)
    r = ex.place(Order("BTC", True, Decimal("1"), OrderKind.LIMIT, Decimal("95")))
    assert r.status == "resting"
    evs = ex.advance(2)
    assert not [e for e in evs if e.kind is WorldEventKind.FILL]
    evs = ex.advance(3)
    fills = [e for e in evs if e.kind is WorldEventKind.FILL]
    assert len(fills) == 1 and fills[0].payload["px"] == "95"


def test_funding_charges_longs_hourly() -> None:
    path = {"BTC": [Decimal("100")] * 5, "ETH": [Decimal("1")] * 5}
    ex = _ex(price_path=path, fee_bps=Decimal("0"), spread_bps=Decimal("0"))
    ex.advance(1)
    ex.place(Order("BTC", True, Decimal("1")))
    cash = ex.account().cash_usd
    evs = ex.advance(NS_PER_HOUR + 1)
    fund = [e for e in evs if e.kind is WorldEventKind.FUNDING and e.payload["coin"] == "BTC"]
    assert len(fund) == 1
    assert Decimal(fund[0].payload["paid_usd"]) == Decimal("100") * Decimal("0.0001")
    assert ex.account().cash_usd == cash - Decimal("0.01")
    # no second funding within the same hour
    evs = ex.advance(NS_PER_HOUR + 2)
    assert not [e for e in evs if e.kind is WorldEventKind.FUNDING]


def test_time_cannot_go_backwards() -> None:
    import pytest

    ex = _ex()
    ex.advance(10)
    with pytest.raises(ValueError):
        ex.advance(5)
