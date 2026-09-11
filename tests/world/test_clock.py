import pytest

from factorylab.world.clock import ClockSource, DripSource, merge_sources
from factorylab.world.events import WorldEventKind


def test_clock_emits_exact_count_strictly_increasing() -> None:
    evs = list(ClockSource(start_ns=10, interval_ns=5, count=4).events())
    assert [e.ts_ns for e in evs] == [10, 15, 20, 25]
    assert all(e.kind is WorldEventKind.TICK for e in evs)
    assert [e.payload["index"] for e in evs] == [0, 1, 2, 3]


def test_clock_rejects_bad_params() -> None:
    with pytest.raises(ValueError):
        ClockSource(0, 0, 1)
    with pytest.raises(ValueError):
        ClockSource(0, 1, -1)


def test_drip_never_after_end() -> None:
    evs = list(DripSource(amount_micro_usd=10_000_000, period_ns=7, start_ns=0, end_ns=20).events())
    assert [e.ts_ns for e in evs] == [0, 7, 14]
    assert all(e.payload["amount_micro_usd"] == 10_000_000 for e in evs)


def test_merge_orders_by_time_and_breaks_ties_by_source_order() -> None:
    a = ClockSource(0, 10, 3).events()  # 0,10,20
    b = DripSource(1, 10, 5, 25).events()  # 5,15,25
    c = ClockSource(10, 100, 1).events()  # 10 (ties with a's 10)
    merged = list(merge_sources(a, b, c))
    assert [e.ts_ns for e in merged] == [0, 5, 10, 10, 15, 20, 25]
    tens = [e for e in merged if e.ts_ns == 10]
    assert tens[0].payload["index"] == 1 and tens[1].payload["index"] == 0


def test_merge_rejects_non_monotonic_source() -> None:
    from factorylab.world.events import WorldEvent

    def bad():
        yield WorldEvent(WorldEventKind.TICK, 5, "x")
        yield WorldEvent(WorldEventKind.TICK, 1, "x")

    with pytest.raises(ValueError):
        list(merge_sources(bad()))


@pytest.mark.parametrize("live", [False, True])
def test_interval_change_applies_to_next_tick_and_rejects_invalid(live):
    from factorylab.runtime.live import LiveClock

    now = [10]

    def sleep(seconds):
        now[0] += round(seconds * 1_000_000_000)

    clock = (LiveClock(5, 5, now_ns=lambda: now[0], sleep=sleep) if live
             else ClockSource(10, 5, 5))
    stream = clock.events()
    assert next(stream).ts_ns == 10
    assert next(stream).ts_ns == 15
    clock.set_interval(2)
    assert next(stream).ts_ns == 17
    for invalid in [0, -1, True, 1.5, "2"]:
        with pytest.raises(ValueError):
            clock.set_interval(invalid)
        assert clock.interval_ns == 2
    clock.set_interval(20)
    assert [e.ts_ns for e in stream] == [37, 57]


def test_clock_change_after_drip_recomputes_pending_tick():
    def run():
        clock = ClockSource(10, 10, 3)
        stream = clock.events(DripSource(1, 100, 15, 15).events())
        events = [next(stream), next(stream)]
        assert [e.ts_ns for e in events] == [10, 15]
        clock.set_interval(20)
        return events + list(stream)

    first = run()
    assert [e.ts_ns for e in first] == [10, 15, 30, 50]
    assert first == run()


@pytest.mark.parametrize("live", [False, True])
def test_injected_iterator_retains_clock_control(live):
    from factorylab.runtime.live import LiveClock

    now = [0]

    def sleep(seconds):
        now[0] += round(seconds * 1_000_000_000)

    clock = (LiveClock(10, 2, now_ns=lambda: now[0], sleep=sleep) if live
             else ClockSource(0, 10, 2))
    stream = clock.events()
    assert next(stream).ts_ns == 0
    stream.set_interval(20)
    assert stream.interval_ns == clock.interval_ns == 20
    assert next(stream).ts_ns == 20


@pytest.mark.parametrize("live", [False, True])
def test_restored_clock_keeps_amended_interval_and_remaining_ticks(live):
    import json

    from factorylab.runtime.live import LiveClock

    now = [10]

    def sleep(seconds):
        now[0] += round(seconds * 1_000_000_000)

    clock = (LiveClock(5, 4, now_ns=lambda: now[0], sleep=sleep) if live
             else ClockSource(10, 5, 4))
    stream = clock.events()
    assert next(stream).ts_ns == 10
    clock.set_interval(2)
    assert next(stream).ts_ns == 12
    saved = json.loads(json.dumps(clock.state()))
    assert saved["interval_ns"] == 2
    restored = (LiveClock.restore(saved, now_ns=lambda: now[0], sleep=sleep) if live
                else ClockSource.restore(saved))
    remaining = list(restored.events())
    assert [e.ts_ns for e in remaining] == [14, 16]
    assert [e.payload["index"] for e in remaining] == [2, 3]
    assert list(restored.events()) == []


def test_restored_clock_after_drip_keeps_time_floor_when_interval_shortens():
    clock = ClockSource(10, 10, 3)
    stream = clock.events(DripSource(1, 100, 15, 15).events())
    assert [next(stream).ts_ns, next(stream).ts_ns] == [10, 15]
    clock.set_interval(2)
    restored = ClockSource.restore(clock.state())
    assert list(restored.events()) == list(stream)
    assert restored.last_ns == 17
