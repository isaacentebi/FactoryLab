from dataclasses import replace

import pytest

from factorylab.cortex.registration import RouterProposal
from factorylab.kernel.queue import SettleStatus
from factorylab.learners.base import BanditFeedback
from factorylab.learners.blum_mansour import BlumMansour
from factorylab.learners.delayed import SnapshotLearner
from factorylab.learners.exp3 import EXP3
from factorylab.runtime.loop import _KeyedLearner
from factorylab.runtime.resume import restore_runtime, runtime_state
from tests.runtime.test_fa_defects import make_runtime


def test_standing_mix_uses_executed_propensity_and_preserves_unbiased_row_estimator():
    inner = BlumMansour(lambda a: EXP3(a, .2), ('a', 'b'))
    learner = SnapshotLearner(inner)
    p = learner.distribution_for('old', ('a', 'b'))
    learner.record_executed('old', {'a': .99, 'b': .01})
    saved = learner.state()
    learner = SnapshotLearner.restore(saved)
    # Low executed propensity makes the original SR_MAB gain exceed one.
    learner.update_for('old', BanditFeedback('b', 1., .01))
    for a, base in zip(inner.actions, learner.inner._bases, strict=True):
        weights = base.state()['log_weights']
        assert weights['b'] - weights['a'] == pytest.approx(.2 / 2 * p[a] / .01)
    assert not learner.state()['snapshots']


def test_producer_return_router_proposal_then_delayed_epoch_settlement():
    rt = make_runtime()
    rt._manage_reserve_window()
    rt._register('population', RouterProposal('ProducerReturn', 'blum_mansour', .2))
    old = rt.routers['ProducerReturn'][0]
    assert isinstance(old.learner, _KeyedLearner)
    old.learner.current_key = 'old'
    sample = old.router.route('ProducerReturn', lambda _: (True, ''), rt.rng,
                              mix=rt._mix_with_standing)
    handle = rt.queue.open(actor=old.learner.id, event_id='p', propensity=rt._propensity(sample),
                           channel='test', deadline_ns=100, parent_handle=None, cost_ceiling=0)
    rt.snapshot_keys[handle] = 'old'
    spec = next(a.spec for a in rt.assemblies.values() if a.spec.role == 'evaluator')
    rt._instantiate(replace(spec, id='new-judge'))
    rt._open_epoch('ProducerReturn')
    assert rt.routers['ProducerReturn'][0].learner.id != old.learner.id
    assert old.learner.id in rt.retired_routers
    restored = make_runtime()
    restore_runtime(restored, runtime_state(rt))
    retired = restored.retired_routers[old.learner.id]
    before = retired.learner.inner.inner.state()
    restored.queue.settle(handle, channel='test', score=.9, status=SettleStatus.SETTLED,
                          definition_version='1', sampling_ref=None)
    restored._deliver_returns()
    assert not retired.learner.inner.state()['snapshots']
    assert retired.learner.inner.inner.state() != before
    assert old.learner.id not in restored.retired_routers
    assert handle not in restored.snapshot_keys
