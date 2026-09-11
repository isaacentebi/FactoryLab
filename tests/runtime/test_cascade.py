import random
from math import ceil, floor

import pytest

from factorylab.kernel.events import Event, EventKind
from factorylab.runtime.cascade import CascadeGate, event_tier, release_threshold


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
    gate = CascadeGate(3)
    events = [arrival(i, tier=tier, score=s) for i, s in enumerate([0.1, 0.9, 0.2])]
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
            "mean": pytest.approx(0.4),
            "min": 0.1,
            "max": 0.9,
            "handles": ("handle-0", "handle-1", "handle-2"),
        },
    }
    assert all("window" not in e.payload for e in events)


@pytest.mark.parametrize("ratio,fraction", [(3, 0), (3, 0.2), (5, 0.5), (10, 1)])
def test_jitter_is_deterministic_bounded_and_never_redrawn_midwindow(ratio, fraction):
    def run(seed):
        rng = random.Random(seed)
        gate = None
        sizes = []
        since_release = 0
        handles = []
        for i in range(1000):
            if gate is None:
                gate = CascadeGate(release_threshold(ratio, fraction, rng.random()))
            threshold = gate.threshold
            gate, released = gate.add(arrival(i))
            since_release += 1
            if released is None:
                assert since_release < threshold
                assert gate.threshold == threshold
            else:
                assert since_release == threshold
                sizes.append(since_release)
                handles.extend(released.payload["window"]["handles"])
                since_release = 0
        assert handles == [f"handle-{i}" for i in range(sum(sizes))]
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
    gate, _ = CascadeGate(3).add(arrival(0))
    with pytest.raises(ValueError, match="mix tiers"):
        gate.add(arrival(1, tier=2))
    with pytest.raises(ValueError, match="mix tiers"):
        CascadeGate(3, (arrival(0), arrival(1, tier=2)))
    with pytest.raises(ValueError, match="Verdict"):
        CascadeGate(3).add(Event("tick", EventKind.TICK, 0, {}, "world"))


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


def test_invalid_gate_state_is_rejected():
    with pytest.raises(ValueError):
        CascadeGate(2)
    with pytest.raises(ValueError, match="full window"):
        CascadeGate(3, tuple(arrival(i) for i in range(3)))
