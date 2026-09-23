"""The harness replays a real diary's delivered tick gaps (time audit T3).

The virtual clock at exactly the declared tick hid every timing failure the wall
clock produced; ``--gaps-from`` ticks at the gaps a diary actually delivered.
"""

import json

import pytest

from scripts import fastloop


def _tick(ts):
    return {"kind": "event", "event": {"kind": "Tick", "ts_ns": ts}}


def test_delivered_gaps_are_read_from_a_diarys_ticks(tmp_path):
    diary = tmp_path / "events.json"
    diary.write_text(json.dumps([_tick(100), {"kind": "event", "event": {"kind": "Fill"}},
                                 _tick(130), _tick(190), {"kind": "decision.open"}]))
    assert fastloop.delivered_gaps(diary) == [30, 60]
    diary.write_text(json.dumps([_tick(100)]))
    with pytest.raises(ValueError, match="fewer than two ticks"):
        fastloop.delivered_gaps(diary)


def test_the_replay_clock_ticks_at_the_recorded_gaps_and_reports_them_measured():
    clock = fastloop.ReplayClock(10, 10, 5, [30, 60])
    stamps = [event.ts_ns for event in clock.events()]
    assert stamps == [10, 40, 100, 130, 190]  # the recorded gaps, cycled
    assert clock.interval_ns == 10  # the declared tick is unchanged
    assert clock.measured_interval_ns() == (30 + 60 + 30 + 60) // 4
    assert clock.intervals()["samples"] == 4


def test_the_scorecard_reads_the_clock_from_the_diary():
    events = [
        _tick(0), _tick(20_000_000_000), _tick(50_000_000_000),
        {"kind": "decision.open", "handle": "d-1", "channel": "verdict"},
        {"kind": "decision.timeout", "return": {"handle": "d-1"}},
        {"kind": "decision.settle", "return": {"handle": "d-1"}},
        {"kind": "clock.loop", "loop": "price", "period_ticks": 3.5, "inner_ticks": 1},
        {"kind": "clock.loop", "loop": "price", "period_ticks": 9.0, "inner_ticks": 3},
        {"kind": "price.skipped", "reason": "ratio"},
        {"kind": "immune.window", "acts": False}, {"kind": "immune.window", "acts": True},
        {"kind": "governance.nonviable"},
    ]
    card = fastloop.clock(events)
    assert card["tick_gap_s"] == {"p50": 30.0, "mean": 25.0, "p90": 30.0, "max": 30.0}
    assert card["loops"]["price"] == {"fires": 2, "period_ticks_mean": 6.25,
                                      "inner_ticks_mean": 2, "ratio_min": 3.0}
    assert card["timeouts"] == {"verdict": 1} and card["cutoff_then_scored"] == 1
    assert card["price_skipped"] == {"ratio": 1}
    assert card["immune"] == {"diagnosed": 2, "acted": 1, "gain_steps": 0}
    assert card["governance"]["nonviable"] == 1
