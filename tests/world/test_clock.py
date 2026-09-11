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
