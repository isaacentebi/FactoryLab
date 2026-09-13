"""Delivered tick gaps govern both the published period and activation."""

from factorylab.runtime.live import LiveClock
from factorylab.runtime.resume import encode
from factorylab.world.events import WorldEvent, WorldEventKind
from tests.audit.test_r3_f1_live_clock import SECOND, FakeWork
from tests.conftest import make_runtime


def test_delivered_gaps_price_the_public_cadence_and_gate():
    fake = FakeWork(0, [n * SECOND for n in (60, 145, 120, 104, 118)])
    clock = LiveClock(60 * SECOND, 6, now_ns=fake.now_ns, sleep=fake.sleep)
    rt = make_runtime(clock_source=clock)
    fake.run(clock)
    period = rt.cadence.slowest_period_events() * 109_400_000_000
    block = rt._world_block()
    assert block["tick_intervals"] == clock.intervals()
    assert block["governance"]["slowest_period"] == f"{period // SECOND}s"
    assert rt.cadence.slowest_period_ns(clock) == period
    rt.cadence.advance(rt.cadence.earliest_event())
    assert not rt.cadence.ready(now_ns=period * 3 - 1, tick_interval_ns=clock, window=1)
    assert rt.cadence.ready(now_ns=period * 3, tick_interval_ns=clock, window=1)


def test_simulated_clock_uses_declared_interval():
    rt = make_runtime()
    assert rt.cadence.slowest_period_ns(rt.tick_clock) == (
        rt.cadence.slowest_period_events() * rt.tick_clock.interval_ns
    )


def test_resume_preserves_measured_cadence_evidence():
    fake = FakeWork(0, [n * SECOND for n in (60, 145, 120, 104, 118)])
    clock = LiveClock(60 * SECOND, 6, now_ns=fake.now_ns, sleep=fake.sleep)
    fake.run(clock)
    restored = LiveClock.restore(clock.state(), now_ns=fake.now_ns, sleep=fake.sleep)
    assert restored.intervals() == clock.intervals()


def test_amendment_waits_past_declared_time_for_measured_time(monkeypatch):
    fake = FakeWork(0, [n * SECOND for n in (60, 145, 120, 104, 118)])
    clock = LiveClock(60 * SECOND, 6, now_ns=fake.now_ns, sleep=fake.sleep)
    rt = make_runtime(clock_source=clock)
    fake.run(clock)
    rt.cadence.advance(rt.cadence.earliest_event())
    rt.clock.now_ns = rt.cadence.earliest_ns(clock.interval_ns)
    calls = []
    monkeypatch.setattr(rt.charter_book, "activate_due", lambda now: calls.append(now))
    assert rt._next_charter_activation() is None
    assert calls == []
    rt.clock.now_ns = rt.cadence.earliest_ns(clock)
    assert rt._next_charter_activation() is None
    assert calls == [rt.clock.now_ns]


def test_replayed_ticks_rebuild_the_same_delivered_gap_sample(monkeypatch):
    clock = LiveClock(60 * SECOND, 6)
    rt = make_runtime(clock_source=clock)
    stamp = 0
    for index, gap in enumerate((0, 60, 145, 120, 104, 118)):
        stamp += gap * SECOND
        event = WorldEvent(WorldEventKind.TICK, stamp, "wallclock", {"index": index})
        saved = {"kind": "runtime.input", "world": encode(event),
                 "ts": rt.clock.now_ns, "seq": index}
        monkeypatch.setattr(rt.ledger, "peek", lambda saved=saved: saved)
        rt._next_event(iter(()))
    assert clock.intervals() == {
        "declared_ns": 60 * SECOND, "measured_ns": 109_400_000_000, "samples": 5}


def test_live_runtime_does_not_count_a_delivered_gap_twice():
    fake = FakeWork(0, [n * SECOND for n in (60, 145, 120, 104, 118)])
    clock = LiveClock(60 * SECOND, 6, now_ns=fake.now_ns, sleep=fake.sleep)
    rt = make_runtime(clock_source=clock)
    stream = clock.events()
    for index in range(6):
        rt._next_event(stream)
        if index < 5:
            fake.now += fake.work[index]
    assert clock.intervals() == {
        "declared_ns": 60 * SECOND, "measured_ns": 109_400_000_000, "samples": 5}
