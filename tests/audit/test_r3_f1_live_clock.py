"""Round three, group F1: the live tick is not the manifest tick.

T20 (seat 5 finding 6): at a declared 60 s interval the seats measured tick gaps
of 60 to 145 s, because one tick's work does not fit in 60 s at real model
latency. Anything converting events into real time must use the interval the
loop achieved. T50 (seat 6 finding 15): ``--duration 40m`` ran 67 minutes,
because a duration was converted into an event count.

Both use a fake clock: nothing here waits and nothing here touches a network.
"""

from factorylab.runtime.live import NS_PER_SECOND, LiveClock

SECOND = NS_PER_SECOND


class FakeWork:
    """A clock whose reader spends ``work`` nanoseconds between ticks."""

    def __init__(self, start_ns: int, work_ns: list[int]) -> None:
        self.now, self.work, self.slept = start_ns, list(work_ns), []

    def now_ns(self) -> int:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now += int(seconds * SECOND)

    def run(self, clock: LiveClock) -> list[int]:
        stamps = []
        for event in clock.events():
            stamps.append(event.ts_ns)
            self.now += self.work.pop(0) if self.work else 0
        return stamps


def test_an_overrunning_tick_is_measured_at_the_interval_it_achieved():
    """The declared interval is the manifest's; the measured one is the world's."""
    # 60 s declared; the seats' own gaps, as work that outlasts the interval.
    overruns = [60 * SECOND, 145 * SECOND, 120 * SECOND, 104 * SECOND, 118 * SECOND]
    fake = FakeWork(1_000 * SECOND, overruns)
    clock = LiveClock(60 * SECOND, 6, now_ns=fake.now_ns, sleep=fake.sleep)
    stamps = fake.run(clock)

    gaps = [b - a for a, b in zip(stamps, stamps[1:], strict=False)]
    assert gaps == overruns, gaps
    assert clock.interval_ns == 60 * SECOND
    measured = clock.measured_interval_ns()
    assert measured == sum(overruns) // len(overruns) == 109_400_000_000  # 109.4 s
    assert clock.intervals() == {"declared_ns": 60 * SECOND, "measured_ns": measured,
                                 "samples": 5}


def test_converting_events_to_time_with_the_declared_interval_understates_the_loop():
    """``CadenceGate.slowest_period_ns`` multiplies an event estimate by the tick.
    With the declared tick the published period is 1.8x short of the real one."""
    fake = FakeWork(0, [145 * SECOND] * 4)
    clock = LiveClock(60 * SECOND, 5, now_ns=fake.now_ns, sleep=fake.sleep)
    fake.run(clock)

    period_events = 200  # the seed consequence backstop
    declared = period_events * clock.interval_ns
    measured = period_events * clock.measured_interval_ns()
    assert measured > declared
    assert measured == period_events * 145 * SECOND


def test_an_unmeasured_clock_reports_the_declared_interval():
    """Before two ticks there is no evidence; absence of evidence is not speed."""
    clock = LiveClock(60 * SECOND, 4, now_ns=lambda: 0, sleep=lambda _s: None)
    assert clock.measured_interval_ns() == 60 * SECOND
    assert clock.intervals()["samples"] == 0


def test_a_duration_is_wall_clock_time_even_when_every_tick_overruns():
    """A 40 minute duration buys 40 ticks at 60 s, and the seats got 67 minutes of
    them. The clock stops at the first tick at or after the deadline instead."""
    start = 5_000 * SECOND
    duration_ns = 40 * 60 * SECOND
    fake = FakeWork(start, [100 * SECOND] * 60)
    clock = LiveClock(60 * SECOND, 40, now_ns=fake.now_ns, sleep=fake.sleep,
                      deadline_ns=start + duration_ns)
    stamps = fake.run(clock)

    assert stamps[-1] - start < duration_ns
    assert stamps[0] == start
    # 40 ticks were budgeted at the declared interval; 100 s ticks fit 24 in 40 min.
    assert len(stamps) == 24


def test_a_duration_never_buys_more_ticks_than_its_event_budget():
    """The budget stays a ceiling: a loop faster than its interval does not get extra ticks."""
    fake = FakeWork(0, [])
    clock = LiveClock(60 * SECOND, 10, now_ns=fake.now_ns, sleep=fake.sleep,
                      deadline_ns=10_000 * SECOND)
    assert len(fake.run(clock)) == 10


def test_a_clock_without_a_deadline_is_unchanged():
    fake = FakeWork(0, [])
    clock = LiveClock(60 * SECOND, 3, now_ns=fake.now_ns, sleep=fake.sleep)
    stamps = fake.run(clock)
    assert [s - stamps[0] for s in stamps] == [0, 60 * SECOND, 120 * SECOND]
    assert clock.state() == {"interval_ns": 60 * SECOND, "count": 3, "source": "wallclock",
                             "index": 3, "last_ns": stamps[-1], "gaps": [60 * SECOND] * 2}
    assert LiveClock.restore(clock.state(), now_ns=fake.now_ns,
                             sleep=fake.sleep).deadline_ns is None


def test_run_gives_a_live_world_a_deadline_and_a_simulated_world_none(monkeypatch, tmp_path):
    """The CLI wiring: only a live world needs a wall clock, because only a live
    world's tick can outlast its interval. Nothing is run and nothing is paid."""
    from factorylab.runtime.cli import main
    from factorylab.runtime.worlds import load_manifest

    # `run` loads every key file in the working directory into the environment.
    # A test must not leave the architect's credentials in this process.
    monkeypatch.chdir(tmp_path)
    for name in ("HL_PRIVATE_KEY", "RESERVE_PRIVATE_KEY", "OPENROUTER_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    seen = {}

    def capture(manifest, **kwargs):
        seen[manifest.name] = kwargs["clock_source"]
        return {"terminated": False}

    monkeypatch.setattr("factorylab.runtime.loop.run_world", capture)
    monkeypatch.setenv("OPENROUTER_API_KEY", "unused-by-this-stub")

    assert main(["run", "--world", "scripted", "--duration", "40m"]) == 0
    assert seen["scripted"] is None  # simulated time advances by the interval exactly

    assert main(["run", "--world", "testnet", "--duration", "40m"]) == 0
    clock = seen["testnet"]
    assert isinstance(clock, LiveClock)
    manifest = load_manifest("testnet")
    assert clock.count == 40 * 60 * SECOND // manifest.tick_interval_ns
    assert clock.deadline_ns is not None
    assert clock.deadline_ns - clock.now_ns() <= 40 * 60 * SECOND
