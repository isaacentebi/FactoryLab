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


def test_fees_are_the_recorded_rates_else_the_published_schedule(tape):
    assert Tape.load(LIVE4).fees("perp") == (Decimal("0.00045"), Decimal("0.00015"), "recorded")
    assert tape.fees("perp") == (Decimal("0.00045"), Decimal("0.00015"), "published")
    # The published taker rate is what longrun1's recorded fill was charged.
    assert (Decimal("0.00045") * Decimal("0.001") * Decimal("84757.0")).quantize(
        Decimal("0.00001")) == Decimal("0.03814")


def test_spreads_come_from_recorded_books_and_say_so(tape):
    assert tape.spread_bps("BTC")[1] == "recorded"
    assert Decimal(0) < tape.spread_bps("BTC")[0] < Decimal(2)
    assert tape.spread_bps("PURR/USDC")[1] == "recorded_other_markets"
    assert Tape.load(LIVE4).spread_bps("BTC") == (Decimal(2), "assumed")


def test_the_venue_is_the_fake_named_for_its_tape_and_opens_at_its_start(tape):
    venue = _venue(tape)
    assert venue.name == f"tape:{tape.sha256[:8]}"
    assert venue.opens_ns == tape.start_ns and venue.closes_ns == tape.end_ns
    assert venue.mids()["BTC"] == tape.mid_at("BTC", tape.start_ns)[1]
    assert venue.mids()["PURR/USDC"] == tape.mid_at("PURR/USDC", tape.start_ns)[1]
    # A checkpoint carries the venue's state, never a copy of the recording.
    assert "_tape" not in vars(venue) and venue.tape is tape
    with pytest.raises(ValueError, match="recorded no mids"):
        TapeVenue(tape, coins=("SOL",))


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
    venue = TapeVenue(Tape.from_data(data), coins=("BTC",))
    venue._positions["BTC"] = Position("BTC", Decimal("0.001"), Decimal("84000"))
    events = venue.advance(stamps[0] + 3 * NS_PER_HOUR)
    assert len([e for e in events if e.kind == "Funding" and e.payload["coin"] == "BTC"]) == 3


def test_the_funding_read_is_the_latest_recorded_rate_at_or_before_now(tape):
    venue = _venue(tape)
    venue.advance(tape.ticks[5])
    [btc] = [f for f in venue.funding() if f.coin == "BTC"]
    assert (btc.ts_ns, btc.rate, btc.premium) == tape.funding_at("BTC", tape.ticks[5])


def test_the_manifest_fixes_the_tape_and_only_the_fake_venue_replays_one(tape):
    base = load_manifest("scripted")
    spec = TapeSpec(tape.sha256, tape.start_ns, tape.end_ns, tape.markets)
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
    spec = TapeSpec(tape.sha256, tape.start_ns, tape.end_ns, tape.markets)
    taped = replace(base, exchange=replace(base.exchange, tape=spec, spot_pairs=()))
    check_tape(taped, _venue(tape))
    check_tape(base, object())
    with pytest.raises(TapeMismatch, match="tape_mismatch"):
        check_tape(taped, TapeVenue(Tape.load(LIVE4), coins=("BTC",)))
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

    venue = _venue(tape)
    venue.advance(tape.ticks[2])
    venue.place(Order("BTC", True, Decimal("0.001"), OrderKind.LIMIT, Decimal("1000")))
    state = json.loads(json.dumps(encode(vars(venue))))
    assert tape.sha256 not in json.dumps(state) and len(json.dumps(state)) < 20_000
    twin = _venue(tape)
    twin.__dict__.clear()
    twin.__dict__.update(decode(state))
    assert twin.open_orders() == venue.open_orders() and twin.mids() == venue.mids()
    assert twin.tape is tape and twin.name == venue.name
