"""A recorded market replayed as the world's venue (factorylab/world/tape.py).

The fixtures are small slices of paid diaries, cut with their items unchanged:
``longrun1-2100`` is 54 ticks of ``work/capital-loop/longrun1-open`` around the
21:00 UTC funding boundary (with the run's own funding payment on it and four of its
recorded order books); ``live4-head`` is the first 12 ticks of ``work/merged/live-4``,
whose recorded instrument listing states the account's fee rates.
"""

from dataclasses import replace
from decimal import Decimal
from pathlib import Path

import pytest

from factorylab.runtime.bootstrap import TapeMismatch, check_tape
from factorylab.runtime.worlds import TapeSpec, load_manifest
from factorylab.world.clock import ClockSource
from factorylab.world.exchange import NS_PER_HOUR, Position
from factorylab.world.tape import TAPE_FORMAT, Tape, TapeVenue, cut

FIXTURES = Path(__file__).parents[1] / "fixtures" / "tape"
LONGRUN = FIXTURES / "longrun1-2100.events.json"
LIVE4 = FIXTURES / "live4-head.events.json"
BOUNDARY = 1790283600 * 10**9  # 21:00:00 UTC, an hour boundary inside the slice


@pytest.fixture(scope="module")
def tape():
    return Tape.load(LONGRUN)


def _venue(tape, **kw):
    return TapeVenue(tape, coins=("BTC", "ETH"), spot_pairs=("PURR/USDC",),
                     start_cash_usd=Decimal(120), **kw)


def test_a_tape_is_cut_from_what_the_diary_recorded_and_nothing_else(tape):
    data = tape.data
    assert data["format"] == TAPE_FORMAT and data["venue"] == "hyperliquid-testnet"
    assert len(data["ticks"]) == 54 and data["declared_tick_ns"] == 10**10
    assert tape.markets == ("BTC", "ETH", "PURR/USDC", "HYPE/USDC")
    assert all(len(rows) == 54 for rows in data["mids"].values())
    # The diary's one funding payment (the run's own short, settled at 21:00) moved money
    # for that run's account: it is not market data, so only the 2 x 54 rate rows remain.
    assert sum(len(rows) for rows in data["funding"].values()) == 108
    assert not any(ts == 1790283600018000000 for rows in data["funding"].values()
                   for ts, *_ in rows)
    assert sum(len(rows) for rows in data["books"].values()) == 4
    assert {row["coin"] for row in data["instruments"]["perp"]} == {"BTC", "ETH"}


def test_the_tape_identity_is_its_content_whatever_file_carried_it(tape, tmp_path):
    compact = tmp_path / "tape.json"
    tape.write(compact)
    again = Tape.load(compact)
    assert again.sha256 == tape.sha256 == Tape.from_data(cut(LONGRUN)).sha256
    other = Tape.load(LIVE4)
    assert other.sha256 != tape.sha256


def test_reads_answer_the_latest_row_at_or_before_and_never_loop(tape):
    stamps = [ts for ts, _ in tape.data["mids"]["BTC"]]
    assert tape.mid_at("BTC", stamps[0] - 1) is None  # nothing before the recording
    assert tape.mid_at("BTC", stamps[3]) == (stamps[3], Decimal(tape.data["mids"]["BTC"][3][1]))
    assert tape.mid_at("BTC", stamps[4] - 1)[0] == stamps[3]  # held, never interpolated
    last = tape.mid_at("BTC", stamps[-1])
    # Past the end the last row holds: the tape never wraps round to its start.
    assert tape.mid_at("BTC", stamps[-1] + 10 * NS_PER_HOUR) == last
    assert tape.mid_at("BTC", stamps[-1] + NS_PER_HOUR) != tape.mid_at("BTC", stamps[0])
    assert tape.mid_at("NOT-RECORDED", stamps[5]) is None


def with_read_fees(tape, taker="0.00045", maker="0.00015"):
    """``tape`` as if its venue had stated the account's rates at its first instant (a
    diary written since live-4 reads them with the instrument listing)."""
    fees = {market: {"venue_read": {"taker": [[tape.start_ns, taker, ["test read"]]],
                                    "maker": [[tape.start_ns, maker, ["test read"]]]}}
            for market in tape.markets}
    return Tape.from_data(dict(tape.data, fees=fees))


def test_fees_are_recorded_rates_usable_from_their_instant_and_none_refuses(tape):
    """Codex review of #151 (7b8de4f) and the ruling on it: a tape never exposes a fee
    rate its recording does not contain, and a recorded one only from the instant it
    was recorded. live-4's venue stated the account's rates with its first tick;
    this longrun1 slice holds no fill and no venue read of them, so it refuses."""
    from factorylab.world.exchange import Order, OrderKind
    from factorylab.world.tape import NO_MAKER_RATE, NO_TAKER_RATE

    live4 = Tape.load(LIVE4)
    first = live4.start_ns
    assert live4.fees("BTC", first) == (Decimal("0.00045"), Decimal("0.00015"))
    assert live4.fees("PURR/USDC", first) == (Decimal("0.0007"), Decimal("0.0004"))
    assert live4.fees("BTC", first - 1) == (None, None)
    assert live4.fee_at("BTC", "taker", first) == (
        Decimal("0.00045"), "venue_read", first, ["exchange.instruments call 208"])
    assert tape.data["fees"] == {}
    venue = _venue(tape)
    venue.advance(tape.ticks[0])
    assert venue.place(Order("BTC", True, Decimal("0.001"))).error == NO_TAKER_RATE
    assert venue.place(Order("BTC", True, Decimal("0.001"), OrderKind.LIMIT,
                             Decimal("80000"))).error == NO_MAKER_RATE
    row = next(r for r in venue.instruments()["perp"] if r["coin"] == "BTC")
    assert row["taker_fee_rate"] is None and row["maker_fee_rate"] is None
    assert row["market_orders_refused"] == NO_TAKER_RATE
    assert row["limit_orders_refused"] == NO_MAKER_RATE and row["refused"] is None


def test_spreads_come_from_recorded_books_and_say_so(tape):
    assert tape.spread_bps("BTC")[1] == "recorded"
    assert Decimal(0) < tape.spread_bps("BTC")[0] < Decimal(2)
    assert tape.spread_bps("PURR/USDC")[1] == "recorded_other_markets"
    # No recorded book anywhere: no spread is stated, never the fake's own 2 bps.
    assert Tape.load(LIVE4).spread_bps("BTC") == (None, "none")
    assert "spread_bps" in tape.identity() and Tape.load(LIVE4).identity()["spread_bps"] == {}


def test_the_venue_is_the_fake_named_for_its_tape_and_opens_at_its_start(tape):
    venue = _venue(tape)
    assert venue.name == f"tape:{tape.sha256[:8]}"
    assert venue.opens_ns == tape.start_ns and venue.closes_ns == tape.end_ns
    assert venue.mids()["BTC"] == tape.mid_at("BTC", tape.start_ns)[1]
    assert venue.mids()["PURR/USDC"] == tape.mid_at("PURR/USDC", tape.start_ns)[1]
    # A checkpoint carries the venue's state, never a copy of the recording.
    assert "_tape" not in vars(venue) and venue.tape is tape
    with pytest.raises(ValueError, match="recorded no mids"):
        TapeVenue(tape, coins=("SOL",), start_cash_usd=Decimal(120))


def test_the_venue_samples_the_tape_at_the_worlds_ticks_and_refuses_to_rewind(tape):
    venue = _venue(tape)
    clock = ClockSource(tape.start_ns, 10**10, 40)
    seen = []
    for i, tick in enumerate(clock.events()):
        if i == 3:
            clock.set_interval(3 * 10**10)  # a charter clock amendment: the world's tick
        events = venue.advance(tick.ts_ns)
        mids = {e.payload["coin"]: e.payload["mid"] for e in events if e.kind == "MarketMid"}
        assert mids["BTC"] == str(tape.mid_at("BTC", tick.ts_ns)[1])
        seen.append(tick.ts_ns)
    gaps = [b - a for a, b in zip(seen, seen[1:], strict=False)]
    assert gaps[:3] == [10**10] * 3 and set(gaps[3:]) == {3 * 10**10}
    with pytest.raises(ValueError, match="backwards"):
        venue.advance(seen[-1] - 1)


def test_funding_is_charged_once_per_hour_boundary_never_per_recorded_row(tape):
    """C4: the tape carries a predicted rate every tick; charging each row would charge
    funding about 360 times an hour. The venue charges the position held at the hour
    boundary once, at the last recorded rate and mid at or before it."""
    venue = _venue(tape)
    venue._positions["BTC"] = Position("BTC", Decimal("-0.001"), Decimal("84000"))
    cash = venue._cash
    funding = []
    for ts in tape.ticks:
        funding += [e for e in venue.advance(ts) if e.kind == "Funding"]
    btc = [e for e in funding if e.payload["coin"] == "BTC"]
    assert len(btc) == 1 and len({e.payload["coin"] for e in funding}) == 2
    _, rate, _ = tape.funding_at("BTC", BOUNDARY)
    mark = tape.mid_at("BTC", BOUNDARY)[1]
    paid = Decimal("-0.001") * mark * rate
    assert Decimal(btc[0].payload["paid_usd"]) == paid and venue._cash == cash - paid
    # The run that recorded the tape held this very short and was paid -0.036131 at
    # this boundary; the replay's computed payment matches it to within the mark.
    assert abs(paid - Decimal("-0.036131")) < Decimal("0.0005")
    [payment] = [p for p in venue.funding_payments(0) if p.coin == "BTC"]
    assert payment.ts_ns == BOUNDARY and payment.rate == rate


def test_a_long_gap_settles_every_boundary_it_crossed_once(tape):
    stamps = tape.ticks
    data = dict(tape.data, ticks=[stamps[0], stamps[0] + 3 * NS_PER_HOUR])
    venue = TapeVenue(Tape.from_data(data), coins=("BTC",), start_cash_usd=Decimal(120))
    venue._positions["BTC"] = Position("BTC", Decimal("0.001"), Decimal("84000"))
    events = venue.advance(stamps[0] + 3 * NS_PER_HOUR)
    assert len([e for e in events if e.kind == "Funding" and e.payload["coin"] == "BTC"]) == 3


def test_the_funding_read_is_the_latest_recorded_rate_at_or_before_now(tape):
    venue = _venue(tape)
    venue.advance(tape.ticks[5])
    [btc] = [f for f in venue.funding() if f.coin == "BTC"]
    assert (btc.ts_ns, btc.rate, btc.premium) == tape.funding_at("BTC", tape.ticks[5])


def test_the_manifest_fixes_the_tape_and_only_the_fake_venue_replays_one(tape):
    from factorylab.runtime.worlds import WebSpec

    base = load_manifest("scripted")
    # A tape world lists no web route (tests/runtime/test_look_ahead.py).
    base = replace(base, web=WebSpec(), models=tuple(replace(m, web=()) for m in base.models))
    spec = TapeSpec.of(tape,
                    allow_unknown_cutoff=True)
    taped = replace(base, exchange=replace(base.exchange, tape=spec, spot_pairs=()))
    taped.validate()
    assert tape.sha256 in taped.canonical_json()
    other = replace(spec, sha256=Tape.load(LIVE4).sha256)
    assert replace(base, exchange=replace(base.exchange, tape=other)).manifest_hash() != (
        taped.manifest_hash())
    for exchange, why in (
            (replace(taped.exchange, kind="hyperliquid"), "only by the fake venue"),
            (replace(taped.exchange, coins=("SOL",)), "recorded no mids"),
            (replace(taped.exchange, tape=replace(spec, sha256="xyz")), "SHA-256"),
            (replace(taped.exchange, tape=replace(spec, end_ns=spec.start_ns)), "span")):
        with pytest.raises(ValueError, match=why):
            replace(taped, exchange=exchange).validate()


def test_the_runtime_refuses_a_venue_whose_tape_the_manifest_does_not_fix(tape):
    base = load_manifest("scripted")
    spec = TapeSpec.of(tape, allow_unknown_cutoff=True)
    taped = replace(base, exchange=replace(base.exchange, tape=spec, spot_pairs=()))
    check_tape(taped, _venue(tape))
    check_tape(base, object())
    # Codex review of #151: the digest alone let a manifest state a false span, markets
    # or spreads about the tape it names; a later start_ns would feed the look-ahead
    # guard a false date. Every field must be the tape's own.
    for field, value in (("start_ns", tape.start_ns + 86_400 * 10**9),
                         ("end_ns", tape.end_ns + 1), ("markets", ("BTC", "ETH")),
                         ("spread_bps", (("BTC", "0.01"),))):
        lying = replace(taped, exchange=replace(taped.exchange,
                                                tape=replace(spec, **{field: value})))
        with pytest.raises(TapeMismatch, match=field):
            check_tape(lying, _venue(tape))
    with pytest.raises(TapeMismatch, match="tape_mismatch"):
        check_tape(taped, TapeVenue(Tape.load(LIVE4), coins=("BTC",),
                                    start_cash_usd=Decimal(120)))
    with pytest.raises(TapeMismatch, match="does not name"):
        check_tape(base, _venue(tape))
    with pytest.raises(TapeMismatch):
        check_tape(taped, None)


def test_a_tape_venue_checkpoints_its_state_and_restores_over_the_same_tape(tape):
    """A checkpoint holds the venue's own state (a resting order's kind included) and
    restores it over the same tape: the recording itself is never in it."""
    import json

    from factorylab.runtime.resume import decode, encode
    from factorylab.world.exchange import Order, OrderKind

    tape = with_read_fees(tape)  # so an order may rest
    venue = _venue(tape)
    venue.advance(tape.ticks[2])
    placed = venue.place(Order("BTC", True, Decimal("0.001"), OrderKind.LIMIT,
                               Decimal("10000")))
    assert placed.status == "resting"
    venue.advance(tape.ticks[3])
    assert venue.open_orders()[0].get("in_flight") is None  # arrived: it rests
    state = json.loads(json.dumps(encode(vars(venue))))
    assert tape.sha256 not in json.dumps(state) and len(json.dumps(state)) < 20_000
    twin = _venue(tape)
    twin.__dict__.clear()
    twin.__dict__.update(decode(state))
    assert twin.open_orders() == venue.open_orders() and twin.mids() == venue.mids()
    assert twin.tape is tape and twin.name == venue.name


T, S = 1_790_000_000 * 10**9, 10**9


def _diary(path, *, fills=(), reads=(), orders=()):
    """A live venue's diary: BTC and ETH mids and a BTC book every 10 s tick from ``T``,
    a listing that states no fee rates, and the given fills, fee reads and orders (each
    at a tick index)."""
    import json

    items = [{"kind": "event", "event": {"kind": "Launch", "ts_ns": T, "payload": {
        "manifest": {"tick_interval_ns": 10 * S,
                     "exchange": {"kind": "hyperliquid", "coins": ["BTC", "ETH"]}}}}}]
    listing = {"perp": [{"coin": c, "lot_size": lot, "tick_size": "0.1",
                         "min_order_value_usd": "10", "fee_rates": "unavailable"}
                        for c, lot in (("BTC", "0.00001"), ("ETH", "0.0001"))], "spot": []}
    seq = 100
    for i in range(6):
        ts = T + i * 10 * S
        items.append({"kind": "event", "event": {"kind": "Tick", "ts_ns": ts, "payload": {}}})
        for coin, mid in (("BTC", "84757"), ("ETH", "2500")):
            items.append({"kind": "event", "event": {"kind": "MarketMid", "ts_ns": ts,
                                                     "source": "hyperliquid",
                                                     "payload": {"coin": coin, "mid": mid}}})
        book = {"coin": "BTC", "ts_ns": ts, "bids": [{"price": "84756", "size": "1"}],
                "asks": [{"price": "84758", "size": "1"}]}
        answers = [("exchange.order_book", book)]
        answers += [("exchange.instruments", listing)] if i == 0 else []
        answers += [("exchange.instruments", read) for at, read in reads if at == i]
        for name, result in answers:
            seq += 2
            items += [{"kind": "io.call", "seq": seq, "name": name, "ts": ts},
                      {"kind": "io.result", "seq": seq + 1, "call": seq, "ts": ts,
                       "result": result}]
        for at, client_id, operation, ack in orders:
            if at == i:
                items += [{"kind": "order.intent", "client_id": client_id,
                           "operation": operation, "ts": ts},
                          {"kind": "order.acknowledged", "client_id": client_id,
                           "result": ack, "ts": ts}]
        items += [{"kind": "event", "event": {"kind": "Fill", "ts_ns": ts,
                                              "source": "hyperliquid", "payload": payload}}
                  for at, payload in fills if at == i]
    path.write_text(json.dumps(items))


def _fill(order_id, size, px, fee):
    return {"coin": "BTC", "order_id": order_id, "size": size, "px": px, "fee_usd": fee,
            "is_buy": True, "liquidation": False, "market": "perp"}


def test_a_tape_with_only_crossed_fills_takes_at_their_rate_from_their_instant_only(tmp_path):
    """The ruling on Codex's #151 finding: the rate a recorded fill states is this
    account's, usable from the instant the diary recorded it, on the side the fill was
    on. A market order's fills took liquidity, so the tape has a taker rate and no maker
    rate: a market order is taken at the recorded rate and a resting limit is refused,
    and before the fill was recorded a market order is refused too."""
    from factorylab.world.exchange import Order, OrderKind
    from factorylab.world.tape import NO_MAKER_RATE, NO_TAKER_RATE

    _diary(tmp_path / "events.json",
           orders=[(2, "d-1", "venue.place_market",
                    {"order_id": "77", "status": "filled", "filled_size": "0.00112"})],
           # Two fills of one order. The second's fee, rounded by the venue to its last
           # place, states 0.00044990...; within that place the rate is 0.00045.
           fills=[(2, _fill("77", "0.001", "84757", "0.03814")),
                  (2, _fill("77", "0.00012", "84700", "0.004573"))])
    tape = Tape.load(tmp_path / "events.json")
    fill_at = T + 20 * S
    # Provenance: the fills the rate came from, by order id, instant and position.
    assert tape.data["fees"] == {"BTC": {"fills": {"taker": [
        [fill_at, "0.00045", [f"fill 77@{fill_at}#1", f"fill 77@{fill_at}#2"]]]}}}
    venue = TapeVenue(tape, coins=("BTC", "ETH"), start_cash_usd=Decimal(1000))
    venue.advance(T + 10 * S)  # the fill is recorded at 20 s: its rate is not usable yet
    assert venue.place(Order("BTC", True, Decimal("0.001"))).error == NO_TAKER_RATE
    venue.advance(fill_at)
    assert venue.place(Order("BTC", True, Decimal("0.001"), client_id="m")).status == "resting"
    limit = Order("BTC", True, Decimal("0.001"), OrderKind.LIMIT, Decimal("84000"))
    assert venue.place(limit).error == NO_MAKER_RATE
    [fill] = [e.payload for e in venue.advance(T + 30 * S) if e.kind == "Fill"]
    assert Decimal(fill["fee_usd"]) == (Decimal("84.758") * Decimal("0.00045")).quantize(
        Decimal("0.000001"))
    btc, eth = venue.instruments()["perp"]
    assert (btc["taker_fee_rate"], btc["taker_fee_source"], btc["taker_fee_since_ns"]) == (
        "0.00045", "fills", fill_at)
    assert btc["maker_fee_rate"] is None and btc["limit_orders_refused"] == NO_MAKER_RATE
    assert btc["market_orders_refused"] is None
    # ETH recorded no fill: BTC's rate, pooled across the venue's perps, and said so.
    assert (eth["taker_fee_rate"], eth["taker_fee_source"]) == ("0.00045", "fills_pooled")


def test_a_resting_limits_later_fills_state_the_maker_rate_and_a_venue_read_comes_first(
        tmp_path):
    """A limit the venue acknowledged resting with nothing filled, then hit, provided
    liquidity: its fill states the maker rate. The venue's own statement of the rates,
    once recorded, is the primary source; before it the fills stand."""
    read = {"perp": [{"coin": "BTC", "taker_fee_rate": "0.0004", "maker_fee_rate": "0.0001",
                      "fee_basis": "fraction of notional, the venue's userFees for this "
                                   "account"}]}
    _diary(tmp_path / "events.json",
           orders=[(1, "d-2", "venue.place_limit",
                    {"order_id": "88", "status": "resting", "filled_size": "0"})],
           fills=[(2, _fill("88", "0.001", "84757", "0.012713"))], reads=[(4, read)])
    tape = Tape.load(tmp_path / "events.json")
    assert tape.fee_at("BTC", "maker", T + 30 * S)[:3] == (Decimal("0.00015"), "fills",
                                                            T + 20 * S)
    assert tape.fee_at("BTC", "taker", T + 30 * S) is None
    assert tape.fees("BTC", T + 40 * S) == (Decimal("0.0004"), Decimal("0.0001"))
    rate, source, since, [provenance] = tape.fee_at("BTC", "maker", T + 40 * S)
    assert (source, since) == ("venue_read", T + 40 * S)
    assert provenance.startswith("exchange.instruments call ")
