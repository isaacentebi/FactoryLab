"""Live diary facts survive the adapter, journal and tape (Chapter II §III.b)."""

import json
from decimal import Decimal
from types import SimpleNamespace

import pytest

from factorylab.kernel.ledger import Ledger
from factorylab.runtime.live import LiveVenue
from factorylab.runtime.resume import JournalProxy, RecoveryJournal
from factorylab.settlement.consequence import FillCursor
from factorylab.world.exchange import HyperliquidExchange, Order, OrderKind
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
