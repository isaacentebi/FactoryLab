from dataclasses import fields

import pytest

from factorylab.kernel.queue import LearningReturn
from factorylab.kernel.timing import TimingRegistry, UpwardBuffer


def hierarchy():
    registry = TimingRegistry()
    registry.register_loop("fast", [])
    registry.register_loop("slow", [])
    registry.register_loop("governor", ["fast", "slow"])
    return registry


def feedback(handle="h", score=1.0, status="settled"):
    return LearningReturn(handle, "outcome", score, "v1", status, None)


def test_period_estimate_and_invalid_closures():
    registry = hierarchy()
    assert registry.estimated_period("fast") is None
    registry.record_closure("fast", 10)
    assert registry.estimated_period("fast") is None
    registry.record_closure("fast", 21)
    registry.record_closure("fast", 40)
    assert registry.estimated_period("fast") == 15
    for time in (40, 39, -1, 1.5, True):
        with pytest.raises(ValueError):
            registry.record_closure("fast", time)
    with pytest.raises(ValueError):
        registry.register_loop("governor", [])
    with pytest.raises(ValueError):
        registry.register_loop("cycle", ["cycle"])
    with pytest.raises(ValueError):
        registry.register_loop("unknown", ["missing"])


def test_invariant_7_early_reports_and_faster_loop_cannot_bypass_slowest_loop():
    registry = hierarchy()
    buffer = UpwardBuffer(registry, "governor", jitter=0)
    buffer.add(feedback("h1", 1.0), 10)
    buffer.add(feedback("h2", 3.0), 20)
    buffer.add(feedback("missing", 1000.0, "censored"), 30)
    for step in range(1, 10):
        registry.record_closure("fast", step)
    for step in (1, 2):
        registry.record_closure("slow", step)
        assert buffer.release() is None
    registry.record_closure("slow", 3)
    summary = buffer.release()
    assert {field.name for field in fields(summary)} == {
        "count",
        "mean",
        "variance",
        "missing",
        "oldest_ts",
        "newest_ts",
    }
    assert (summary.count, summary.mean, summary.variance, summary.missing) == (3, 2.0, 1.0, 1)
    assert (summary.oldest_ts, summary.newest_ts) == (10, 30)
    assert buffer.release() is None
    buffer.add(feedback(), 40)
    # Surplus fast closures from the prior window cannot pay for this window.
    for step in (4, 5, 6):
        registry.record_closure("slow", step)
    assert buffer.release() is None
    for step in (10, 11, 12):
        registry.record_closure("fast", step)
    assert buffer.release().count == 1


def test_seeded_jitter_never_violates_minimum_and_polling_cannot_shorten_it():
    def releases(seed, polls):
        registry = hierarchy()
        buffer = UpwardBuffer(registry, "governor", seed=seed, jitter=3)
        buffer.add(feedback(), 0)
        result, last = [], 0
        for step in range(1, 81):
            registry.record_closure("fast", step)
            registry.record_closure("slow", step)
            summary = None
            for _ in range(polls):
                summary = buffer.release()
                if summary is not None:
                    break
            if summary is not None:
                assert step - last >= 3
                result.append(step)
                last = step
                buffer.add(feedback(str(step)), step)
        return result

    assert releases(11, 1) == releases(11, 20)
    assert releases(11, 1) != releases(12, 1)


def test_preexisting_closures_do_not_satisfy_new_buffer_and_missing_has_no_fake_score():
    registry = hierarchy()
    for loop in ("fast", "slow"):
        for step in (1, 2, 3):
            registry.record_closure(loop, step)
    buffer = UpwardBuffer(registry, "governor", jitter=0)
    buffer.add(feedback(status="timed_out", score=0.0), 50)
    assert buffer.release() is None
    for loop in ("fast", "slow"):
        for step in (4, 5, 6):
            registry.record_closure(loop, step)
    result = buffer.release()
    assert result.count == result.missing == 1
    assert result.mean is result.variance is None


def test_ratio_floor_and_thin_input_enforced():
    registry = hierarchy()
    with pytest.raises(ValueError):
        UpwardBuffer(registry, "governor", min_ratio=0)
    with pytest.raises(ValueError):
        UpwardBuffer(registry, "fast")
    buffer = UpwardBuffer(registry, "governor")
    with pytest.raises(TypeError):
        buffer.add({"handle": "h", "private": "not thin"}, 1)


def test_configured_ratio_is_respected():
    registry = hierarchy()
    buffer = UpwardBuffer(registry, "governor", min_ratio=5, jitter=0)
    buffer.add(feedback(), 1)
    for step in range(1, 6):
        registry.record_closure("fast", step)
        registry.record_closure("slow", step)
        summary = buffer.release()
        assert (summary is not None) == (step == 5)
