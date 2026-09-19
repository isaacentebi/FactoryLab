"""Old routers remain addressable until their grounded producer decisions settle."""

from types import SimpleNamespace

import pytest

from factorylab.cortex.registration import LearnerProposal
from factorylab.kernel.events import Event, EventKind
from factorylab.kernel.queue import SettleStatus
from factorylab.runtime.resume import restore_runtime, runtime_state
from factorylab.runtime.shared import CH_VERDICT
from tests.runtime.test_grounded_assembly_timeout import (
    _DeclaredProducer,
    _open_timed_out_grounded_round,
)
from tests.runtime.test_grounded_feedback import _judge, _runtime
from tests.runtime.test_learning_signal import _drawn, _router, _weights
from tests.runtime.test_loop import _consequence_produce


def _open_grounded_round_before_timeout():
    runtime = _runtime(provider=_DeclaredProducer())
    runtime._manage_reserve_window()
    registration_handle, _ = _consequence_produce(runtime)
    runtime._register(
        registration_handle,
        LearnerProposal("seed-decider", "exp3", ("hold", "buy:BTC"), 0.1),
    )
    router, _ = _router(runtime, "Tick")
    handle = _drawn(runtime, router, "seed-decider", channel=CH_VERDICT)
    runtime.n += 1
    runtime._producer_step(
        Event(
            f"tick-{runtime.n}", EventKind.TICK, runtime.clock.now_ns,
            {"index": 0}, "test",
        ),
        handle,
        SimpleNamespace(chosen="seed-decider"),
        runtime.queue.get(handle).deadline_ns,
    )
    assert handle in runtime.grounded_pending
    return runtime, router, handle


@pytest.mark.parametrize("rotate", ["replace", "shrink"])
def test_consumed_grounded_timeout_retains_old_router_through_restore(rotate, monkeypatch):
    runtime, old, _assembly, handle, old_before, _assembly_before = (
        _open_timed_out_grounded_round()
    )

    if rotate == "replace":
        runtime._build_router("Tick", "exp3", 0.1)
    else:
        smaller = [arm for arm in old.universe if arm != "antagonist-a"]
        monkeypatch.setattr(runtime, "_universe_for", lambda _kind: smaller)
        runtime._open_epoch("Tick")

    old_id = old.learner.id
    assert old_id in runtime.retired_routers
    assert not any(
        item["kind"] == "actor.retire" and item["actor"] == old_id
        for item in runtime.ledger._recovery_items()
    )

    restored = _runtime(provider=_DeclaredProducer())
    restore_runtime(restored, runtime_state(runtime))
    restored_old = restored.retired_routers[old_id]
    restored_fresh = restored.routers["Tick"][0]
    fresh_before = _weights(restored_fresh)

    _judge(
        restored,
        handle,
        {
            "status": "supported",
            "score": 0.8,
            "evidence": ["event:7"],
            "reason": "the supplied evidence decides the frozen criterion",
        },
    )
    restored._deliver_returns()

    assert _weights(restored_old) != old_before
    assert _weights(restored_fresh) == fresh_before
    assert old_id not in restored.retired_routers
    assert any(
        item["kind"] == "actor.retire" and item["actor"] == old_id
        for item in restored.ledger._recovery_items()
    )
    assert any(
        item["kind"] == "router.drained" and item["learner_id"] == old_id
        for item in restored.ledger._recovery_items()
    )


def test_timeout_delivery_keeps_replaced_router_until_restored_unknown_settlement():
    runtime, old, handle = _open_grounded_round_before_timeout()
    old_id = old.learner.id
    runtime._build_router("Tick", "exp3", 0.1)
    assert old_id in runtime.retired_routers

    runtime.queue.expire(10**16)
    runtime._deliver_returns()
    assert runtime.queue.get(handle).status is SettleStatus.TIMED_OUT
    assert old_id in runtime.retired_routers
    assert not any(
        item["kind"] == "actor.retire" and item["actor"] == old_id
        for item in runtime.ledger._recovery_items()
    )

    restored = _runtime(provider=_DeclaredProducer())
    restore_runtime(restored, runtime_state(runtime))
    restored._deliver_returns()
    assert old_id in restored.retired_routers
    restored._grounded_unknown(restored.grounded_pending[handle], "no deciding fact")
    restored._deliver_returns()

    assert restored.queue.history(handle)[-1].status is SettleStatus.CENSORED
    assert old_id not in restored.retired_routers
    assert any(
        item["kind"] == "router.drained" and item["learner_id"] == old_id
        for item in restored.ledger._recovery_items()
    )
