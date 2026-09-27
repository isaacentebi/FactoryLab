"""Live diary facts survive the adapter, journal and tape (Chapter II §III.b)."""

import json
from decimal import Decimal
from types import SimpleNamespace

import pytest

from factorylab.kernel.ledger import Ledger
from factorylab.runtime.live import LiveVenue
from factorylab.runtime.resume import JournalProxy, RecoveryJournal
from factorylab.settlement.consequence import FillCursor
from factorylab.world.exchange import NS_PER_HOUR, HyperliquidExchange, Order, OrderKind, Position
from factorylab.world.tape import Tape, TapeVenue, cut

S = 10**9
T = 1_790_000_000 * S


def _adapter(now, rows, fees):
    venue = object.__new__(HyperliquidExchange)
    venue.name = "hyperliquid-synthetic"
    venue.coins, venue.spot_pairs = ("BTC",), ()
    venue._sz_decimals = {"BTC": 3}
    venue._spot_names, venue._spot_tokens = {}, {}
    venue._address = "synthetic-account"
    venue._guarded = lambda name, call: call()
    venue._info = SimpleNamespace(
        user_fees=lambda address: fees,
        user_fills_by_time=lambda address, start: rows,
        all_mids=lambda: {"BTC": str((100, 100, 104, 101)[(now[0] - T) // S])},
        l2_snapshot=lambda coin: {
            "coin": coin, "time": now[0] // 1_000_000,
            "levels": [[{"px": str((99, 99, 103, 100)[(now[0] - T) // S]), "sz": "10"}],
                       [{"px": str((101, 101, 105, 102)[(now[0] - T) // S]), "sz": "10"}]]},
    )
    venue.funding = lambda: []
    venue.funding_payments = lambda since: []
    venue.settled_funding = False
    return venue


def _record(tmp_path, rows, *, venue_fees):
    now = [T]
    ledger = Ledger(clock_ns=lambda: now[0])
    journal = RecoveryJournal(ledger, lambda: now[0])
    journal.active = True
    fees = {"userCrossRate": "0.00045", "userAddRate": "0.00015"} if venue_fees else {}
    adapter = _adapter(now, rows, fees)
    exchange = JournalProxy(adapter, journal, "exchange")
    ledger.append({"kind": "event", "event": {"kind": "Launch", "ts_ns": T,
                   "payload": {"manifest": {"tick_interval_ns": S,
                               "exchange": {"kind": "hyperliquid", "coins": ["BTC"]}}}}})
    exchange.refresh_fee_rates()
    exchange.instruments()
    # Old order-level evidence conflicts with the individual maker fill; the venue's
    # execution flag wins, and an unknown flag must not become an inferred taker.
    ledger.append({"kind": "order.intent", "client_id": "order", "operation": "venue.place_market"})
    ledger.append({"kind": "order.acknowledged", "client_id": "order",
                   "result": {"order_id": "7", "status": "filled", "filled_size": "20"}})
    cursor = FillCursor(ledger, start_ns=T)
    for stamp, payload in cursor.poll(exchange, strict=True, now_ns=T):
        ledger.append({"kind": "event", "event": {"kind": "Fill", "ts_ns": stamp,
                       "source": adapter.name, "payload": payload}})
    live = LiveVenue(exchange, ledger=ledger, markets=lambda: ("BTC",))
    for tick in range(4):
        now[0] = T + tick * S
        ledger.append({"kind": "event", "event": {"kind": "Tick", "ts_ns": now[0]}})
        for event in live.on_tick(now[0]):
            ledger.append({"kind": "event", "event": event})
    path = tmp_path / "events.json"
    path.write_text(json.dumps(list(ledger.items())))
    return Tape.from_data(cut(path)), list(ledger.items())


def _row(crossed, tid):
    return {"oid": 7, "tid": tid, "coin": "BTC", "side": "B", "sz": "10",
            "px": "100", "fee": "0.450000" if crossed is True else "0.150000",
            "closedPnl": "0", "time": T // 1_000_000, "crossed": crossed}


@pytest.mark.parametrize("venue_fees", [True, False], ids=["userFees", "crossed-fills"])
def test_live_diary_cuts_both_fee_sides_and_trades_roundtrip(tmp_path, venue_fees):
    # Same order, two execution sides, no order hints: only crossed can classify these.
    tape, diary = _record(tmp_path, [_row(True, 1), _row(False, 2)], venue_fees=venue_fees)
    assert [row[0] for row in tape.data["books"]["BTC"]] == [T + n * S for n in range(4)]
    assert tape.fees("BTC", T) == (Decimal("0.00045"), Decimal("0.00015"))
    assert tape.fees("BTC", T - 1) == (None, None)
    source = "venue_read" if venue_fees else "fills"
    assert tape.fee_at("BTC", "maker", T)[1] == source
    if venue_fees:
        call = next(row["seq"] for row in diary
                    if row.get("name") == "exchange.refresh_fee_rates")
        assert tape.fee_at("BTC", "maker", T)[3] == [f"exchange.refresh_fee_rates call {call}"]
    venue = TapeVenue(tape, coins=("BTC",), start_cash_usd=Decimal("1000"))
    assert venue.place(Order("BTC", False, Decimal(1), OrderKind.LIMIT,
                             Decimal(103))).status == "resting"
    venue.advance(T + S)
    venue.advance(T + 2 * S)
    assert venue.place(Order("BTC", True, Decimal(1), OrderKind.MARKET)).status == "resting"
    venue.advance(T + 3 * S)
    fills = venue.fills(T)
    assert [(fill.px, fill.fee) for fill in fills] == [
        (Decimal(103), Decimal("0.015450")), (Decimal(102), Decimal("0.045900"))]
    assert sum((fill.realized - fill.fee for fill in fills), Decimal(0)) == Decimal("0.938650")
    assert venue.account().equity_usd == Decimal("1000.938650")


@pytest.mark.parametrize("flag", [None, "false", 0, "absent"])
def test_live_unknown_crossed_never_invents_a_fee_side(tmp_path, flag):
    row = _row(flag, 1)
    if flag == "absent":
        row.pop("crossed")
    tape, diary = _record(tmp_path, [row], venue_fees=False)
    payload = next(item["event"]["payload"] for item in diary
                   if item.get("kind") == "event" and item["event"]["kind"] == "Fill")
    assert payload["crossed"] is None
    assert tape.fees("BTC", T) == (None, None)


def _settled_tape(tmp_path, *, oracle="120.123456789", repeats=False):
    h = NS_PER_HOUR
    items = [{"kind": "event", "event": {"kind": "Launch", "ts_ns": h - 10,
              "payload": {"manifest": {"exchange": {"kind": "hyperliquid",
                                                       "coins": ["BTC"]}}}}}]
    for ts in (h - 10, h, h + 5, h + 10, h + 20):
        items.extend([{"kind": "event", "event": {"kind": "Tick", "ts_ns": ts}},
                      {"kind": "event", "event": {"kind": "MarketMid", "ts_ns": ts,
                       "payload": {"coin": "BTC", "mid": "100"}}}])
    payload = {"coin": "BTC", "rate": "0.01", "premium": "0.002", "paid_usd": "0",
               "funding_ns": h, "settled": True, "mark": oracle,
               "oracle_observed_at_ns": h + 1}
    for ts in ((h + 10, h + 20) if repeats else (h + 10,)):
        items.append({"kind": "event", "event": {"kind": "Funding", "ts_ns": ts,
                                                  "payload": payload}})
    path = tmp_path / "settled.json"
    path.write_text(json.dumps(items))
    return Tape.from_data(cut(path))


def test_cut_preserves_settled_evidence_on_the_same_nonzero_payment(tmp_path):
    tape = _settled_tape(tmp_path)
    path = tmp_path / "settled.json"
    items = json.loads(path.read_text())
    items[-1]["event"]["payload"]["paid_usd"] = "999"
    path.write_text(json.dumps(items))
    recut = Tape.from_data(cut(path))
    assert recut.data["settled_funding"] == tape.data["settled_funding"]
    venue = TapeVenue(recut, coins=("BTC",), start_cash_usd=Decimal(1000))
    venue._positions["BTC"] = Position("BTC", Decimal(2), Decimal(100))
    venue.advance(NS_PER_HOUR + 10)
    assert venue._cash == Decimal("997.59753086422")


def test_settled_tape_does_not_publish_a_delayed_rate_at_its_boundary(tmp_path):
    tape = _settled_tape(tmp_path)
    h = NS_PER_HOUR
    assert tape.funding_at("BTC", h) is None
    assert tape.funding_at("BTC", h + 9) is None
    assert tape.funding_at("BTC", h + 10) == (h + 10, Decimal("0.01"), Decimal("0.002"))
    [row] = tape.data["settled_funding"]["BTC"]
    assert row == {"published_at_ns": h + 10, "funding_ns": h, "settled": True,
                   "rate": "0.01", "premium": "0.002", "mark": "120.123456789",
                   "oracle_observed_at_ns": h + 1}


def test_settled_tape_charges_boundary_position_at_retained_oracle_on_publication(tmp_path):
    tape = _settled_tape(tmp_path, repeats=True)
    h = NS_PER_HOUR
    venue = TapeVenue(tape, coins=("BTC",), start_cash_usd=Decimal(1000))
    venue._positions["BTC"] = Position("BTC", Decimal(2), Decimal(100))
    assert not [e for e in venue.advance(h) if e.kind == "Funding"]
    venue._positions.clear()  # Closing after H cannot erase the amount owed at H.
    assert not [e for e in venue.advance(h + 5) if e.kind == "Funding"]
    [event] = [e for e in venue.advance(h + 10) if e.kind == "Funding"]
    assert event.ts_ns == h + 10
    assert event.payload["funding_ns"] == h
    assert event.payload["settled"] is True
    assert event.payload["oracle_observed_at_ns"] == h + 1
    assert event.payload["mark"] == "120.123456789"
    assert venue.funding()[0].mark == Decimal("120.123456789")
    assert Decimal(event.payload["paid_usd"]) == Decimal("2.40246913578")
    assert venue._cash == Decimal("997.59753086422")
    assert not [e for e in venue.advance(h + 20) if e.kind == "Funding"]


def test_settled_tape_never_substitutes_mid_for_missing_oracle(tmp_path):
    tape = _settled_tape(tmp_path, oracle=None)
    venue = TapeVenue(tape, coins=("BTC",), start_cash_usd=Decimal(1000))
    venue._positions["BTC"] = Position("BTC", Decimal(2), Decimal(100))
    venue.advance(NS_PER_HOUR)
    [event] = [e for e in venue.advance(NS_PER_HOUR + 10) if e.kind == "Funding"]
    assert event.payload["mark"] is None
    assert event.payload["settled"] is True
    assert venue.settled_funding is True
    assert venue._cash == Decimal(1000)
    assert venue.funding_payments(0) == []


@pytest.mark.parametrize("answer", [{}, {"userCrossRate": "bad"},
                                    {"userCrossRate": "NaN"},
                                    {"userSpotCrossRate": "0.02"},
                                    {"status": "unavailable", "answer": {}, "markets": []}])
def test_unusable_fee_refresh_cannot_mask_cached_listing(tmp_path, answer):
    from tests.world.test_tape import _diary

    path = tmp_path / "fees.json"
    _diary(path, reads=[(0, {"perp": [{"coin": "BTC", "taker_fee_rate": "0.0004",
                                     "maker_fee_rate": "0.0001", "fee_basis": "userFees"}]})])
    items = json.loads(path.read_text())
    items.extend([{"kind": "io.call", "seq": 900, "name": "exchange.refresh_fee_rates"},
                  {"kind": "io.result", "call": 900, "ts": T, "result": answer}])
    path.write_text(json.dumps(items))
    tape = Tape.from_data(cut(path))
    assert tape.fees("BTC", T) == (Decimal("0.0004"), Decimal("0.0001"))


def test_raw_fee_suppression_is_per_side_and_not_retroactive(tmp_path):
    from tests.world.test_tape import _diary

    path = tmp_path / "fees.json"
    cached = {"perp": [{"coin": "BTC", "taker_fee_rate": "0.0004",
                         "maker_fee_rate": "0.0001", "fee_basis": "userFees"}]}
    _diary(path, reads=[(0, cached), (4, cached)])
    items = json.loads(path.read_text())
    items.extend([{"kind": "io.call", "seq": 900, "name": "exchange.refresh_fee_rates"},
                  {"kind": "io.result", "call": 900, "ts": T + 20 * S,
                   "result": {"userCrossRate": "0.0005"}}])
    path.write_text(json.dumps(items))
    tape = Tape.from_data(cut(path))
    assert tape.fees("BTC", T) == (Decimal("0.0004"), Decimal("0.0001"))
    assert tape.fees("BTC", T + 40 * S) == (Decimal("0.0005"), Decimal("0.0001"))


def test_settled_tape_emits_zero_cash_corrections_and_prelaunch_evidence(tmp_path):
    tape = _settled_tape(tmp_path, repeats=True)
    rows = tape.data["settled_funding"]["BTC"]
    rows[1]["rate"] = "0.02"
    venue = TapeVenue(tape, coins=("BTC",), start_cash_usd=Decimal(1000))
    first = [e for e in venue.advance(NS_PER_HOUR + 10) if e.kind == "Funding"]
    second = [e for e in venue.advance(NS_PER_HOUR + 20) if e.kind == "Funding"]
    assert first[0].payload["rate"] == "0.01"
    assert second[0].payload["rate"] == "0.02"
    assert venue._cash == Decimal(1000)
    tape.data["ticks"] = [NS_PER_HOUR + 5, NS_PER_HOUR + 20]
    venue = TapeVenue(tape, coins=("BTC",), start_cash_usd=Decimal(1000))
    assert len([e for e in venue.advance(NS_PER_HOUR + 20) if e.kind == "Funding"]) == 2
    assert venue.funding_payments(0) == []


def test_settled_history_uses_effective_order_and_upserts_corrections(tmp_path):
    tape = _settled_tape(tmp_path)
    h = NS_PER_HOUR
    tape.data["ticks"] = [h - 10, 2 * h + 30]
    template = tape.data["settled_funding"]["BTC"][0]
    tape.data["settled_funding"]["BTC"] = [
        dict(template, funding_ns=2 * h, published_at_ns=2 * h + 10),
        dict(template, funding_ns=h, published_at_ns=2 * h + 20),
        dict(template, funding_ns=h, published_at_ns=2 * h + 30, rate="0.02"),
    ]
    venue = TapeVenue(tape, coins=("BTC",), start_cash_usd=Decimal(1000))
    venue.advance(2 * h + 20)
    assert [r.ts_ns for r in venue.funding_history("BTC", 1)] == [2 * h]
    venue.advance(2 * h + 30)
    rows = venue.funding_history("BTC", 10)
    assert [(r.ts_ns, r.rate) for r in rows] == [(h, Decimal("0.02")),
                                               (2 * h, Decimal("0.01"))]


def test_settled_tape_rejects_mixed_legacy_market_semantics(tmp_path):
    tape = _settled_tape(tmp_path)
    tape.data["mids"]["ETH"] = tape.data["mids"]["BTC"]
    tape.data["funding"]["ETH"] = [[NS_PER_HOUR, "0.01", None]]
    with pytest.raises(ValueError, match="mixed legacy and settled"):
        TapeVenue(tape, coins=("BTC", "ETH"), start_cash_usd=Decimal(1000))


def test_fee_refresh_journals_failure_separately_from_cached_rates(tmp_path):
    now = [T]
    ledger = Ledger(clock_ns=lambda: now[0])
    journal = RecoveryJournal(ledger, lambda: now[0])
    journal.active = True
    adapter = _adapter(now, [], {"userCrossRate": "0.0004", "userAddRate": "0.0001"})
    exchange = JournalProxy(adapter, journal, "exchange")
    good = exchange.refresh_fee_rates()
    assert good["status"] == "ok" and good["markets"] == ["perp"]
    adapter._info.user_fees = lambda address: {}
    bad = exchange.refresh_fee_rates()
    assert bad == {"status": "unavailable", "answer": {}, "markets": []}
    assert adapter.instruments()["perp"][0]["taker_fee_rate"] == "0.0004"
    adapter._info.user_fees = lambda address: {"userCrossRate": "NaN", "userAddRate": "0.1"}
    assert exchange.refresh_fee_rates()["status"] == "unavailable"
    assert adapter.instruments()["perp"][0]["taker_fee_rate"] == "0.0004"
