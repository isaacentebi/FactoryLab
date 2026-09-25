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
    spec = TapeSpec(tape.sha256, tape.start_ns, tape.end_ns, tape.markets)
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
