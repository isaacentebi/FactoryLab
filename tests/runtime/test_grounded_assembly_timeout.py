"""Grounded producer rounds wait for their final consequence after wall timeout."""

from types import SimpleNamespace

import pytest

from factorylab.cortex.registration import LearnerProposal
from factorylab.kernel.events import Event, EventKind
from factorylab.kernel.queue import SettleStatus
from factorylab.runtime.resume import restore_runtime, runtime_state
from factorylab.runtime.shared import CH_VERDICT
from factorylab.world.scripted import ScriptedProvider
from tests.runtime.test_grounded_feedback import _judge, _runtime
from tests.runtime.test_learning_signal import _drawn, _router, _weights
from tests.runtime.test_loop import _consequence_produce


class _DeclaredProducer(ScriptedProvider):
    def _produce(self, desc, inputs):
        return {
            "action": "hold",
            "propensity": {"hold": 0.6, "buy:BTC": 0.4},
        }


def _open_timed_out_grounded_round():
    runtime = _runtime(provider=_DeclaredProducer())
    runtime._manage_reserve_window()
    registration_handle, _ = _consequence_produce(runtime)
    runtime._register(
        registration_handle,
        LearnerProposal(
            "seed-decider", "exp3", ("hold", "buy:BTC"), 0.1
        ),
    )

    router, _ = _router(runtime, "Tick")
    handle = _drawn(runtime, router, "seed-decider", channel=CH_VERDICT)
    runtime.n += 1
    runtime._producer_step(
        Event(
            f"tick-{runtime.n}",
            EventKind.TICK,
            runtime.clock.now_ns,
            {"index": 0},
            "test",
        ),
        handle,
        SimpleNamespace(chosen="seed-decider"),
        runtime.queue.get(handle).deadline_ns,
    )
    assembly = runtime.assembly_learners["seed-decider"]
    assert handle in runtime.grounded_pending
    assert handle in runtime.assembly_rounds
    assert handle in assembly.state()["snapshots"]

    router_before = _weights(router)
    assembly_before = assembly.inner.state()
    runtime.queue.expire(10**16)
    assert runtime.queue.get(handle).status is SettleStatus.TIMED_OUT
    runtime._deliver_returns()

    assert _weights(router) == router_before
    assert assembly.inner.state() == assembly_before
    assert handle in runtime.assembly_rounds
    assert handle in assembly.state()["snapshots"]
    return runtime, router, assembly, handle, router_before, assembly_before


@pytest.mark.parametrize(
    ("status", "score", "restore_after_timeout"),
    (("supported", 0.8, False), ("contrary", 0.0, True)),
)
def test_final_grounded_score_trains_both_learners_once_after_wall_timeout(
    status, score, restore_after_timeout
):
    runtime, router, assembly, handle, router_before, assembly_before = (
        _open_timed_out_grounded_round()
    )
    if restore_after_timeout:
        restored = _runtime(provider=_DeclaredProducer())
        restore_runtime(restored, runtime_state(runtime))
        runtime = restored
        router, _ = _router(runtime, "Tick")
        assembly = runtime.assembly_learners["seed-decider"]
        assert _weights(router) == router_before
        assert assembly.inner.state() == assembly_before

    _judge(
        runtime,
        handle,
        {
            "status": status,
            "score": score,
            "evidence": ["event:7"],
            "reason": "the supplied evidence decides the frozen criterion",
        },
    )
    runtime._deliver_returns()

    if score > 0:
        assert _weights(router) != router_before
        assert assembly.inner.state() != assembly_before
    else:
        assert _weights(router) == router_before
        assert assembly.inner.state() == assembly_before
    assert router.observed.state()["seed-decider"] == pytest.approx([score, 1])
    assert assembly.observed.state()["hold"] == pytest.approx([score, 1])
    assert handle not in runtime.assembly_rounds
    assert handle not in assembly.state()["snapshots"]
    learned = [
        item
        for item in runtime.ledger._recovery_items()
        if item["kind"] == "propensity.learned" and item["handle"] == handle
    ]
    assert len(learned) == 1
    assert learned[0]["reward"] == pytest.approx(score)

    router_after = _weights(router)
    assembly_after = assembly.inner.state()
    runtime._deliver_returns()
    assert _weights(router) == router_after
    assert assembly.inner.state() == assembly_after
    assert len(
        [
            item
            for item in runtime.ledger._recovery_items()
            if item["kind"] == "propensity.learned" and item["handle"] == handle
        ]
    ) == 1


def test_final_grounded_unknown_discards_both_rounds_after_wall_timeout():
    runtime, router, assembly, handle, router_before, assembly_before = (
        _open_timed_out_grounded_round()
    )
    restored = _runtime(provider=_DeclaredProducer())
    restore_runtime(restored, runtime_state(runtime))
    runtime = restored
    router, _ = _router(runtime, "Tick")
    assembly = runtime.assembly_learners["seed-decider"]

    _judge(
        runtime,
        handle,
        {
            "status": "unknown",
            "evidence": ["event:7"],
            "reason": "the supplied evidence cannot decide the frozen criterion",
        },
    )
    runtime._deliver_returns()

    assert _weights(router) == router_before
    assert assembly.inner.state() == assembly_before
    assert router.observed.state() == {}
    assert assembly.observed.state() == {}
    assert handle not in runtime.assembly_rounds
    assert handle not in assembly.state()["snapshots"]
    assert not any(
        item["kind"] == "propensity.learned" and item["handle"] == handle
        for item in runtime.ledger._recovery_items()
    )
