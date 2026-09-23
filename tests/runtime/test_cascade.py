"""Cascade windows: separation is time and completed evidence, counted in world ticks.

Every test here once counted arrivals, because the gate did; then durations in wall
nanoseconds at the declared tick. Chapter II §IV.c asks for "a minimum cascade
control ratio (e.g., 3:1+) before returning verdicts into the next evaluatory
tier", jittered, and time audit T10 asks that the ratio be taken against the
measured period of the loop the window gates, in the one clock domain (T3). The
properties pinned are the same three: a window releases exactly one
representative carrying its window's evidence, the jitter is deterministic,
bounded, continuous and never redrawn mid-window, and tiers never mix.
"""

from math import ceil

import pytest

from factorylab.kernel.events import Event, EventKind
from factorylab.runtime.cascade import CascadeGate, event_tier, release_window
from factorylab.runtime.clockwork import jitter_draw


def arrival(index, *, tier=1, score=0.5):
    payload = (
        {"evaluator_handle": f"handle-{index}", "verdict": score, "rationale": "reason"}
        if tier == 1
        else {"by": f"handle-{index}", "about": "lower", "tier": tier, "score": score}
    )
    return Event(
        f"event-{index}",
        EventKind.VERDICT if tier == 1 else EventKind.META_VERDICT,
        index,
        payload,
        "runtime",
    )


@pytest.mark.parametrize("tier", [1, 2, 3, 20])
def test_latest_representative_contains_exact_window_without_mutating_inputs(tier):
    window = release_window(3, 0, 0.0, 2)  # three times a two-tick inner loop
    assert window == 6
    gate = CascadeGate(window, opened=0)
    # Three arrivals spread across the window's ticks; the third closes it.
    events = [arrival(i, tier=tier, score=s) for i, s in enumerate([0.1, 0.9, 0.2])]
    for tick, event in zip((0, 3), events[:2], strict=True):
        before = gate
        gate, released = gate.add(event, now=tick)
        assert released is None
        assert len(gate.arrivals) == len(before.arrivals) + 1
    next_gate, released = gate.add(events[-1], now=6)
    assert next_gate is None
    assert len(gate.arrivals) == 2
    assert event_tier(released) == tier
    assert released.id == events[-1].id
    assert released.source == events[-1].source
    assert dict(released.payload) == {
        **events[-1].payload,
        "window": {
            "count": 3,
            "arrivals": 3,
            "window_ticks": 6,
            "elapsed_ticks": 6,
            "mean": pytest.approx(0.4),
            "min": 0.1,
            "max": 0.9,
            "handles": ("handle-0", "handle-1", "handle-2"),
        },
    }
    assert all("window" not in e.payload for e in events)


@pytest.mark.parametrize("ratio,fraction,inner", [(3, 0, 1), (3, 0.2, 1), (5, 0.5, 2),
                                                  (10, 1, 3)])
def test_jitter_is_deterministic_bounded_continuous_and_never_redrawn_midwindow(
        ratio, fraction, inner):
    """A window's length is drawn once, from the tier's own stream, and only lengthens."""
    def run(seed):
        gate = None
        windows = []
        opened_at = 0
        handles = []
        for tick in range(2000):
            if gate is None:
                drawn = release_window(ratio, fraction,
                                       jitter_draw(seed, "cascade:1", len(windows) + 1), inner)
                gate = CascadeGate(drawn, opened=tick)
                opened_at = tick
            window = gate.window
            gate, released = gate.add(arrival(tick), now=tick)
            if released is None:
                assert tick - opened_at < window
                assert gate.window == window
            else:
                assert tick - opened_at == ceil(window)
                windows.append(window)
                handles.extend(released.payload["window"]["handles"])
        assert handles == [f"handle-{i}" for i in range(len(handles))]
        return windows

    windows = run(7)
    assert windows == run(7)
    assert all(ratio * inner <= w <= ratio * inner * (1 + fraction) for w in windows)
    if fraction:
        assert len(set(windows)) > 5  # continuous, not a two-valued step
        assert windows != run(8)
    else:
        assert set(windows) == {ratio * inner}


def test_distinct_tiers_cannot_share_a_gate():
    gate, _ = CascadeGate(3, opened=0).add(arrival(0), now=0)
    with pytest.raises(ValueError, match="mix tiers"):
        gate.add(arrival(1, tier=2), now=1)
    with pytest.raises(ValueError, match="mix tiers"):
        CascadeGate(3, 0, (arrival(0), arrival(1, tier=2)))
    with pytest.raises(ValueError, match="Verdict"):
        CascadeGate(3, opened=0).add(Event("tick", EventKind.TICK, 0, {}, "world"), now=0)


@pytest.mark.parametrize(
    "ratio,fraction,draw,inner",
    [
        (1, 0.2, 0.5, 1),
        (2, 0.2, 0.5, 1),
        (3.0, 0.2, 0.5, 1),
        (3, -0.1, 0.5, 1),
        (3, float("nan"), 0.5, 1),
        (3, float("inf"), 0.5, 1),
        (3, 0.2, -0.1, 1),
        (3, 0.2, 1.0, 1),
        (3, 0.2, float("nan"), 1),
        (3, 0.2, 0.5, 0),
        (3, 0.2, 0.5, 1.5),
    ],
)
def test_invalid_cadence_is_rejected(ratio, fraction, draw, inner):
    with pytest.raises(ValueError):
        release_window(ratio, fraction, draw, inner)


@pytest.mark.parametrize("window", [0, -1, float("nan"), "3"])
def test_invalid_gate_state_is_rejected(window):
    with pytest.raises(ValueError):
        CascadeGate(window)
    with pytest.raises(ValueError):
        CascadeGate(3, -1)
