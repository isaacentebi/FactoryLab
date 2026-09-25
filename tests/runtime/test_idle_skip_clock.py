"""The idle-skipping clock and every wall-clock reader under it (critique C1, H3).

A replayed world may compress only the time it spends waiting: when it is not busy
(no model call, no kernel work) its clock jumps to the next tick instead of sleeping,
and busy time stays real. Every reader of the wall clock is keyed on whether the tick
clock is paced by the wall (``wall_paced``), never on whether the venue is live.
"""

from dataclasses import replace
from decimal import Decimal
from pathlib import Path

import pytest

from factorylab.runtime.live import IdleSkipClock, wall_paced
from factorylab.runtime.loop import Runtime
from factorylab.runtime.resume import ResumeError, _restored_tick_clock, restore_runtime
from factorylab.runtime.resume import runtime_state as checkpoint_of
from factorylab.runtime.worlds import TapeSpec, load_manifest
from factorylab.world.clock import ClockSource, ReplayClock
from factorylab.world.exchange import Order
from factorylab.world.scripted import ScriptedProvider
from factorylab.world.tape import Tape, TapeVenue

S = 10**9
TAPE = Path(__file__).parents[1] / "fixtures" / "tape" / "longrun1-2100.events.json"


class Monotonic:
    """A monotonic source the test moves: the time the process is busy."""

    def __init__(self):
        self.t = 5 * S

    def __call__(self):
        return self.t


def test_idle_is_skipped_never_slept_and_busy_time_fires_a_tick_late():
    busy = Monotonic()
    clock = IdleSkipClock(10 * S, 5, origin_ns=1_000 * S, monotonic=busy)
    stream = clock.events()
    assert next(stream).ts_ns == 1_000 * S
    busy.t += 3 * S  # the tick took three seconds of work: seven are idle
    assert next(stream).ts_ns == 1_010 * S
    busy.t += 14 * S  # this one took fourteen: the next tick is four seconds late
    assert next(stream).ts_ns == 1_024 * S
    clock.spend(12 * S)  # a modelled call of twelve seconds is busy time too
    assert next(stream).ts_ns == 1_036 * S
    assert list(clock.gaps) == [10 * S, 14 * S, 12 * S]
    assert clock.idle == [7 * S, 0, 0] and clock.skipped_ns == 7 * S
    assert clock.measured_interval_ns() == 12 * S  # the real lateness, reported
    assert wall_paced(clock)
    with pytest.raises(RuntimeError, match="never sleeps"):
        clock.sleep(1.0)
    with pytest.raises(ValueError):
        clock.spend(-1)


def test_the_stream_ends_at_the_deadline_whatever_the_interval():
    clock = IdleSkipClock(10 * S, 100, origin_ns=0, monotonic=Monotonic(), deadline_ns=25 * S)
    stream = clock.events()
    assert [next(stream).ts_ns for _ in range(2)] == [0, 10 * S]
    clock.set_interval(20 * S)  # a charter clock amendment
    assert list(stream) == []  # the next tick, at 30 s, is past the tape


def test_a_resumed_clock_continues_from_the_worlds_instant_with_what_it_skipped():
    busy = Monotonic()
    clock = IdleSkipClock(10 * S, 9, origin_ns=500 * S, monotonic=busy)
    stream = clock.events()
    next(stream), next(stream)
    clock.spend(3 * S)
    saved = clock.state()
    fresh = IdleSkipClock(10 * S, 9, origin_ns=500 * S, monotonic=Monotonic(),
                          deadline_ns=900 * S)
    resumed = _restored_tick_clock(fresh, saved, instant_ns=510 * S)
    assert isinstance(resumed, IdleSkipClock) and resumed.deadline_ns == 900 * S
    assert (resumed.skipped_ns, resumed.modelled_ns) == (clock.skipped_ns, 3 * S)
    assert resumed.now_ns() == 510 * S and resumed.index == 2
    assert next(resumed.events()).ts_ns == 520 * S


def test_the_wall_is_journaled_whenever_the_tick_clock_is_paced_by_it():
    """Critique H3: keyed on ``self.live``, a replayed world re-executed a real clock."""
    simulated = _runtime(ClockSource(S, S, 3))
    paced = _runtime(IdleSkipClock(S, 3, origin_ns=S, monotonic=Monotonic()))
    assert simulated.wall.deterministic is True
    assert paced.wall.deterministic is False


def _runtime(clock, **kw):
    from factorylab.world.exchange import FakeExchange

    return Runtime(load_manifest("scripted"), events=0, seed=1, initial_balance_micro=None,
                   ledger_path=None, router_gamma=.1, provider=ScriptedProvider(),
                   exchange=kw.pop("exchange", FakeExchange()), clock_source=clock, **kw)


def _tape_runtime(busy):
    tape = Tape.load(TAPE)
    base = load_manifest("scripted")
    spec = TapeSpec.of(tape,
                    allow_unknown_cutoff=True)
    manifest = replace(base, exchange=replace(base.exchange, tape=spec, spot_pairs=()))
    venue = TapeVenue(tape, coins=manifest.exchange.coins, start_cash_usd=Decimal(1000))
    clock = IdleSkipClock(10 * S, 50, origin_ns=tape.start_ns, monotonic=busy,
                          deadline_ns=tape.end_ns + 1)
    rt = Runtime(manifest, events=50, seed=1, initial_balance_micro=None, ledger_path=None,
                 router_gamma=.1, provider=ScriptedProvider(), exchange=venue,
                 clock_source=clock)
    return rt, tape


def test_the_safety_pass_settles_what_the_tape_filled_while_a_model_thought():
    """Critique H3: the pass ran only for a live venue, so on a paced replay a fill the
    market made during a long call waited for the next tick. It now runs on the wall's
    instant and settles what the recording filled by then."""
    busy = Monotonic()
    rt, tape = _tape_runtime(busy)
    event = tape.ticks[3]
    rt.exchange.advance(event)
    rt.clock.now_ns = rt._safety_ns = event
    rt.tick_clock.origin_ns, rt.tick_clock._anchor = event, None
    assert rt.tick_clock.now_ns() == event  # the wall reads the event's instant now
    result = rt.exchange.place(Order("BTC", True, Decimal("0.001")))
    assert result.status == "resting"
    busy.t += 30 * S  # a long model call: the recording moved on
    rt._safety_pass()
    [fill] = [i for i in rt.ledger._recovery_items() if i["kind"] == "fill.counted"]
    [safety] = [i for i in rt.ledger._recovery_items() if i["kind"] == "safety.pass"]
    assert fill["order_id"] == result.order_id and safety["fills"] >= 1
    assert fill["ts"] > event and rt.clock.now_ns == event + 30 * S


def test_an_unpaced_tape_world_needs_no_pass_and_a_restore_keeps_the_clocks_kind():
    rt = _runtime(ClockSource(S, S, 3))
    rt._safety_pass()  # a simulated clock does not move inside an event: nothing runs
    assert not [i for i in rt.ledger._recovery_items() if i["kind"] == "safety.pass"]
    busy = Monotonic()
    taped, _tape = _tape_runtime(busy)
    state = checkpoint_of(taped)
    assert "skipped_ns" in state["tick_clock"]
    twin, _ = _tape_runtime(Monotonic())
    restore_runtime(twin, state)
    assert isinstance(twin.tick_clock, IdleSkipClock)
    other, _ = _tape_runtime(Monotonic())
    other.tick_clock = ClockSource(S, S, 3)
    with pytest.raises(ResumeError) as refused:
        restore_runtime(other, state)
    assert refused.value.code == "tick_clock_mismatch"


def test_a_replay_clock_checkpoints_its_gaps_and_restores_as_itself():
    """The bug: a ReplayClock was checkpointed as a bare ClockSource and restored as one,
    so a resumed ``--gaps-from`` world ticked at the declared interval, its gaps lost."""
    clock = ReplayClock(S, S, 6, [4 * S, 7 * S])
    stream = clock.events()
    stamps = [next(stream).ts_ns for _ in range(3)]
    assert stamps == [S, 5 * S, 12 * S]
    restored = ReplayClock.restore(clock.state())
    assert isinstance(restored, ReplayClock)
    assert restored.recorded == [4 * S, 7 * S] and list(restored.gaps) == [4 * S, 7 * S]
    assert restored.measured_interval_ns() == clock.measured_interval_ns()
    assert next(restored.events()).ts_ns == 16 * S  # the next recorded gap, not 1 s
    assert isinstance(_restored_tick_clock(clock, clock.state(), instant_ns=0), ReplayClock)
    rt = _runtime(ReplayClock(S, S, 3, [4 * S]))
    state = checkpoint_of(rt)
    bare = _runtime(ClockSource(S, S, 3))
    with pytest.raises(ResumeError) as refused:
        restore_runtime(bare, state)
    assert refused.value.code == "tick_clock_mismatch"


def test_a_clock_adopts_a_recorded_reading_and_its_totals():
    busy = Monotonic()
    clock = IdleSkipClock(10 * S, 5, origin_ns=100 * S, monotonic=busy)
    clock.adopt({"now_ns": 500 * S, "skipped_ns": 70 * S, "modelled_ns": 30 * S})
    assert clock.now_ns() == 500 * S and clock.pace_record() == {
        "now_ns": 500 * S, "skipped_ns": 70 * S, "modelled_ns": 30 * S}
    busy.t += 2 * S  # real busy time counts again from the adopted instant
    assert clock.now_ns() == 502 * S


def test_an_order_that_can_no_longer_arrive_is_cancelled_when_the_world_ends():
    """Codex review of #151 (6a1ac93): a market order sent after the tape's last recorded
    mid could never arrive, and the wind-down's cancel of an immediate-or-cancel order is
    refused, so the sealed world kept it forever as pending exposure. Closing the
    recorded market cancels it, settled like any venue cancel."""
    rt, tape = _tape_runtime(Monotonic())
    rt.m = replace(rt.m, kill=replace(rt.m.kill, wind_down=True))
    rt.exchange.advance(tape.end_ns)  # the last recorded tick
    rt.clock.now_ns = tape.end_ns
    sent = rt.exchange.place(Order("BTC", True, Decimal("0.001"), client_id="last"))
    assert sent.status == "resting" and rt.exchange.open_orders()[0]["in_flight"] is True
    report = rt.kill("explicit_kill:budget")
    assert rt.exchange.lookup("last").status == "cancelled"
    assert rt.exchange.open_orders() == []
    assert report["residual"]["resting"] == [] and report["exposure_state"] == "flat"
    items = rt.ledger._recovery_items()
    [cancel] = [i for i in items if i["kind"] == "consequence.cancel"
                and i["order_id"] == sent.order_id]
    kill = next(i["seq"] for i in items if i["kind"] == "kill.production")
    assert cancel["seq"] < kill  # settled before the production mark
    after = rt.exchange.target.place(Order("BTC", True, Decimal("0.001"), client_id="late"))
    assert after.status == "rejected" and after.error == "the recorded market has ended"


def test_the_wind_down_flattens_a_tape_world_at_its_last_recorded_book():
    """Codex review of #151 (e49955c): the venue was sealed before the kill's wind-down,
    so every close was refused and the world sealed with pending exposure. The terminal
    sequence is: accrued funding, orders that can never arrive cancelled, the wind-down's
    closes filled against the last recorded book (same depth rules, taker rate), their
    P&L booked, and only then the seal and Terminated."""
    from factorylab.world.exchange import Position

    tape = Tape.load(TAPE)
    base = load_manifest("scripted")
    manifest = replace(base, kill=replace(base.kill, wind_down=True), exchange=replace(
        base.exchange, tape=TapeSpec.of(tape, allow_unknown_cutoff=True),
        spot_pairs=("PURR/USDC",)))
    venue = TapeVenue(tape, coins=manifest.exchange.coins, spot_pairs=("PURR/USDC",),
                      start_cash_usd=Decimal(1000))
    rt = Runtime(manifest, events=0, seed=1, initial_balance_micro=None, ledger_path=None,
                 router_gamma=.1, provider=ScriptedProvider(), exchange=venue,
                 clock_source=IdleSkipClock(10 * S, 5, origin_ns=tape.start_ns,
                                            monotonic=Monotonic()))
    rt.exchange.advance(tape.end_ns)
    rt.clock.now_ns = tape.end_ns
    btc, purr = tape.mid_at("BTC", tape.end_ns)[1], tape.mid_at("PURR/USDC", tape.end_ns)[1]
    venue._positions["BTC"] = Position("BTC", Decimal("0.01"), Decimal(84000))
    venue._spot_positions["PURR/USDC"] = Position("PURR/USDC", Decimal(5), Decimal(4))
    report = rt.kill("explicit_kill:budget")
    assert report["exposure_state"] == "flat", report.get("residual")
    assert not venue.account().positions and not venue._spot_positions
    fills = {f.coin: f for f in venue._fills}
    for coin, mid in (("BTC", btc), ("PURR/USDC", purr)):
        half = mid * tape.spread_bps(coin)[0] / 20_000
        fill = fills[coin]  # sold at the last recorded bid: mid less half the spread
        assert fill.px == (mid - half).quantize(Decimal("1e-10")) and not fill.is_buy
    taker = Decimal("0.00045")
    assert fills["BTC"].fee == (Decimal("0.01") * fills["BTC"].px * taker).quantize(
        Decimal("0.000001"))
    items = rt.ledger._recovery_items()
    [counted] = [i for i in items if i["kind"] == "fill.counted" and i["coin"] == "BTC"]
    assert counted["realized_micro"] == round((fills["BTC"].px - 84000) * Decimal("0.01")
                                              * 1_000_000)
    booked = [i for i in items if i["kind"] == "venue.settled" and i["reason"] == "exchange_pnl"]
    assert booked, "the close's realized P&L is booked on the venue account"
    # The order of the terminal sequence: production dies, the closes fill and are
    # booked, and only then is the world Terminated.
    seqs = {i["kind"]: i["seq"] for i in items}
    assert seqs["kill.production"] < counted["seq"] < max(
        i["seq"] for i in items if (i.get("event") or {}).get("kind") == "Terminated")
    assert venue.place(Order("BTC", True, Decimal("0.001"))).error == (
        "the recorded market has ended")


def test_a_checkpoint_mid_tick_keeps_the_paced_reading_and_its_totals():
    """Codex review of #151 (e49955c): a checkpoint with no journal tail rebuilt the
    clock's origin from the world's instant (the tick), so a 5 s modelled call inside a
    10 s tick was undone: the resumed clock read 5 s earlier and the next tick skipped
    the whole interval. The checkpoint carries the recorded paced reading."""
    busy = Monotonic()
    clock = IdleSkipClock(10 * S, 5, origin_ns=1_000 * S, monotonic=busy)
    stream = clock.events()
    assert next(stream).ts_ns == 1_000 * S
    busy.t += S // 2  # the kernel's own work
    clock.spend(5 * S)  # a modelled call inside the tick
    record = clock.pace_record()  # what the event's runtime.event_done holds
    assert record["now_ns"] == 1_000 * S + 5 * S + S // 2
    saved = clock.state()
    assert saved["paced_now_ns"] == record["now_ns"]
    busy.t += 7 * S  # a fresh read after the event would differ: the state does not
    assert clock.state() == saved
    fresh = IdleSkipClock(10 * S, 5, origin_ns=1_000 * S, monotonic=Monotonic())
    resumed = _restored_tick_clock(fresh, saved, instant_ns=1_000 * S)  # the tick
    assert resumed.now_ns() == record["now_ns"]
    assert (resumed.skipped_ns, resumed.modelled_ns) == (0, 5 * S)
    assert next(resumed.events()).ts_ns == 1_010 * S  # the next tick, on time
    assert resumed.idle == [10 * S - record["now_ns"] + 1_000 * S]  # only the remainder
    assert resumed.skipped_ns == 4 * S + S // 2


def _ending_runtime(busy):
    """A tape world at its last world tick, 5 s before the recording's end, holding a
    long BTC position and a market buy in flight that only the tail row can meet."""
    from factorylab.world.exchange import Position

    rt, tape = _tape_runtime(busy)
    last_tick = tape.end_ns - 5 * S  # the tape's span is not a multiple of the tick
    assert tape.mid_at("BTC", last_tick)[0] < tape.end_ns  # the tail row is later
    rt.exchange.advance(last_tick)
    rt.clock.now_ns = rt._safety_ns = last_tick
    rt.tick_clock.origin_ns, rt.tick_clock._anchor = last_tick, None
    assert rt.tick_clock.now_ns() == last_tick  # busy time counts from here
    venue = rt.exchange.target
    venue._positions["BTC"] = Position("BTC", Decimal("0.01"), Decimal(84000))
    sent = rt.exchange.place(Order("BTC", True, Decimal("0.001"), client_id="tail"))
    assert sent.status == "resting"
    return rt, tape, venue, sent


def test_a_world_the_tape_ended_closes_through_the_tapes_end():
    """Codex review of #151 (51614c7): when the tape's span is not a multiple of the
    tick, the world's last tick falls short of the recording's end, and the recording
    closed there: the tail row's fill was cancelled and funding stopped short. When the
    tape ended the run, the venue is advanced and settled exactly through closes_ns."""
    rt, tape, venue, sent = _ending_runtime(Monotonic())
    rt._finish_budget()  # the clock ran out before its tick budget: the tape ended it
    [fill] = [f for f in venue._fills if f.order_id == sent.order_id]
    assert fill.ts_ns == tape.end_ns and venue.lookup("tail").status == "filled"
    items = rt.ledger._recovery_items()
    assert [i for i in items if i["kind"] == "fill.counted"
            and i["order_id"] == sent.order_id and i["ts"] == tape.end_ns]
    [partial] = [p for p in venue.funding_payments(0) if ":partial:" in p.id
                 and p.coin == "BTC"]
    assert partial.ts_ns == tape.end_ns  # funding runs through the recording's end
    assert venue._now_ns == tape.end_ns and rt.clock.now_ns == tape.end_ns
    # An explicit, earlier budget closes at the world's own instant, as before.
    early, _tape, early_venue, early_sent = _ending_runtime(Monotonic())
    early.tick_clock.index = early.tick_clock.count
    early._finish_budget()
    assert early_venue.lookup("tail").status == "cancelled"
    assert early_venue._now_ns == tape.end_ns - 5 * S


def test_a_call_past_the_tapes_end_invents_nothing_and_ends_the_world():
    """Codex review of #151 (51614c7): a model call that ran past closes_ns, then a
    safety pass, advanced the venue into time the tape never recorded, crossing
    post-tape funding boundaries at the last mark. The venue's time stops at closes_ns,
    the pass latches the end, later calls and venue writes are refused, and the world
    is terminated, closed through the tape's end."""
    from factorylab.world.metering import UnbilledFailure
    from factorylab.world.tape import MARKET_ENDED, TAPE_ENDED

    busy = Monotonic()
    rt, tape, venue, sent = _ending_runtime(busy)
    busy.t += 3 * 3600 * S  # a call that ran three hours past the recording's end
    with pytest.raises(UnbilledFailure, match=TAPE_ENDED):
        rt._safety_pass()
    assert venue._now_ns == tape.end_ns and rt.clock.now_ns == tape.end_ns
    with pytest.raises(UnbilledFailure):
        rt._safety_pass()  # every later call of the event is refused
    refused = rt._venue_write("h-late", "venue.place_market",
                              {"coin": "BTC", "side": "buy", "size": "0.001"}, slot="output")
    assert refused["status"] == "rejected" and refused["error"] == MARKET_ENDED
    assert rt._check_termination() is True
    assert rt.termination.final
    after = [p for p in venue.funding_payments(0) if p.ts_ns > tape.end_ns]
    assert after == [] and all(f.ts_ns <= tape.end_ns for f in venue._fills)
    assert venue.lookup("tail").status == "filled"  # the tail row still met it
    boundaries = {p.ts_ns for p in venue.funding_payments(0)}
    assert all(ts <= tape.end_ns for ts in boundaries)
