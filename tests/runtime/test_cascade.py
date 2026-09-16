"""Cascade windows, restated for R3-D: separation is time and completed evidence.

Every test here used to count arrivals, because the gate did. GPT-6 Pro's third
reading calls that a launch blocker (§3: "three messages arriving together
satisfy the separation") and §6.C replaces it with a duration. The properties
being pinned are the same three: a window releases exactly one representative
carrying its window's evidence, the jitter is deterministic, bounded and never
redrawn mid-window, and tiers never mix. What changed is the unit.
"""

import random
from math import ceil, floor

import pytest

from factorylab.kernel.events import Event, EventKind
from factorylab.runtime.cascade import (
    CascadeGate,
    event_tier,
    release_threshold,
    release_window_ns,
)

# One observation window, in nanoseconds: the unit a tier's duration is counted in.
WINDOW = 10


def arrival(index, *, tier=1, score=0.5, ts_ns=None):
    payload = (
        {"evaluator_handle": f"handle-{index}", "verdict": score, "rationale": "reason"}
        if tier == 1
        else {"by": f"handle-{index}", "about": "lower", "tier": tier, "score": score}
    )
    return Event(
        f"event-{index}",
        EventKind.VERDICT if tier == 1 else EventKind.META_VERDICT,
        index if ts_ns is None else ts_ns,
        payload,
        "runtime",
    )


@pytest.mark.parametrize("tier", [1, 2, 3, 20])
def test_latest_representative_contains_exact_window_without_mutating_inputs(tier):
    window_ns = release_window_ns(3, 0, 0.0, WINDOW)
    gate = CascadeGate(window_ns, opened_ns=0)
    # Three arrivals spread across the window's duration; the third closes it.
    events = [arrival(i, tier=tier, score=s, ts_ns=i * (window_ns // 2))
              for i, s in enumerate([0.1, 0.9, 0.2])]
    for event in events[:2]:
        before = gate
        gate, released = gate.add(event)
        assert released is None
        assert len(gate.arrivals) == len(before.arrivals) + 1
    next_gate, released = gate.add(events[-1])
    assert next_gate is None
    assert len(gate.arrivals) == 2
    assert event_tier(released) == tier
    assert released.id == events[-1].id
    assert released.ts_ns == events[-1].ts_ns
    assert released.source == events[-1].source
    assert dict(released.payload) == {
        **events[-1].payload,
        "window": {
            "count": 3,
            "arrivals": 3,
            "window_ns": window_ns,
            "elapsed_ns": window_ns,
            "mean": pytest.approx(0.4),
            "min": 0.1,
            "max": 0.9,
            "handles": ("handle-0", "handle-1", "handle-2"),
        },
    }
    assert all("window" not in e.payload for e in events)


@pytest.mark.parametrize("ratio,fraction", [(3, 0), (3, 0.2), (5, 0.5), (10, 1)])
def test_jitter_is_deterministic_bounded_and_never_redrawn_midwindow(ratio, fraction):
    """The same property as before, over durations: a window's length is drawn once."""
    def run(seed):
        rng = random.Random(seed)
        gate = None
        sizes = []
        opened_at = 0
        handles = []
        for i in range(1000):
            if gate is None:
                gate = CascadeGate(release_window_ns(ratio, fraction, rng.random(), WINDOW),
                                   opened_ns=i * WINDOW)
                opened_at = i
            window_ns = gate.window_ns
            gate, released = gate.add(arrival(i, ts_ns=i * WINDOW))
            if released is None:
                assert (i - opened_at) * WINDOW < window_ns
                assert gate.window_ns == window_ns
            else:
                assert (i - opened_at) * WINDOW == window_ns
                sizes.append(window_ns // WINDOW)
                handles.extend(released.payload["window"]["handles"])
        assert handles == [f"handle-{i}" for i in range(len(handles))]
        return sizes

    sizes = run(7)
    assert sizes == run(7)
    assert all(
        ratio - floor(ratio * fraction) <= n <= ratio + ceil(ratio * fraction) for n in sizes
    )
    assert min(sizes) >= ratio
    if fraction:
        assert len(set(sizes)) > 1
        assert sizes != run(8)
    else:
        assert set(sizes) == {ratio}


def test_distinct_tiers_cannot_share_a_gate():
    gate, _ = CascadeGate(WINDOW, opened_ns=0).add(arrival(0))
    with pytest.raises(ValueError, match="mix tiers"):
        gate.add(arrival(1, tier=2))
    with pytest.raises(ValueError, match="mix tiers"):
        CascadeGate(WINDOW, 0, (arrival(0), arrival(1, tier=2)))
    with pytest.raises(ValueError, match="Verdict"):
        CascadeGate(WINDOW, opened_ns=0).add(Event("tick", EventKind.TICK, 0, {}, "world"))


@pytest.mark.parametrize(
    "ratio,fraction,draw",
    [
        (1, 0.2, 0.5),
        (2, 0.2, 0.5),
        (3.0, 0.2, 0.5),
        (3, -0.1, 0.5),
        (3, float("nan"), 0.5),
        (3, float("inf"), 0.5),
        (3, 0.2, -0.1),
        (3, 0.2, 1.0),
        (3, 0.2, float("nan")),
    ],
)
def test_invalid_cadence_is_rejected(ratio, fraction, draw):
    with pytest.raises(ValueError):
        release_threshold(ratio, fraction, draw)
    with pytest.raises(ValueError):
        release_window_ns(ratio, fraction, draw, WINDOW)


@pytest.mark.parametrize("window_ns", [0, -1, 3.0])
def test_invalid_gate_state_is_rejected(window_ns):
    with pytest.raises(ValueError):
        CascadeGate(window_ns)
    with pytest.raises(ValueError):
        release_window_ns(3, 0.2, 0.5, 0)
    with pytest.raises(ValueError):
        CascadeGate(WINDOW, -1)
