from dataclasses import replace

import pytest

from factorylab.cortex.registration import RouterProposal
from factorylab.kernel.queue import SettleStatus
from factorylab.learners.base import BanditFeedback
from factorylab.learners.blum_mansour import BlumMansour
from factorylab.learners.delayed import SnapshotLearner
from factorylab.runtime.resume import restore_runtime, runtime_state
from factorylab.runtime.routing import _KeyedLearner
from tests.conftest import make_runtime


def test_executed_propensity_trains_the_core_through_its_fixed_coverage_bound():
    """Learners design §2.3: a draw transform (the standing mix, the adversarial cap)
    executes pi, never the learner's own p; the row update uses the truthful pi, scaled
    by the phase-wide coverage bound kappa so Lemma 10's gain stays at most one, and a
    pi below p / kappa is refused rather than trained."""
    tight = SnapshotLearner(BlumMansour(('a', 'b'), coverage=2.0))
    tight.distribution_for('old', ('a', 'b'), ordinal=0)
    tight.record_executed('old', {'a': .99, 'b': .01})
    saved = tight.state()
    with pytest.raises(ValueError, match="coverage"):
        tight.update_for('old', BanditFeedback('b', 1., .01))
    assert tight.state() == saved  # refused: nothing trained, the round kept
    wide = SnapshotLearner(BlumMansour(('a', 'b'), coverage=100.0))
    p = wide.distribution_for('old', ('a', 'b'), ordinal=0)
    wide.record_executed('old', {'a': .99, 'b': .01})
    gamma = wide.exploration('old')
    wide = SnapshotLearner.restore(wide.state())
    assert wide.update_for('old', BanditFeedback('b', 1., .01))
    for a, row in zip(('a', 'b'), wide.inner.state()['rows'], strict=True):
        assert row['b'] - row['a'] == pytest.approx(gamma / 2 * p[a] / (100.0 * .01))
    assert not wide.state()['snapshots']


def test_producer_return_router_proposal_then_delayed_epoch_settlement():
    rt = make_runtime()
    rt._manage_reserve_window()
    rt._register('population', RouterProposal('ProducerReturn', 'blum_mansour', .2))
    old = rt.routers['ProducerReturn'][0]
    assert isinstance(old.learner, _KeyedLearner)
    old.learner.current_key, old.learner.current_ordinal = 'old', rt.n
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
    live = restored.routers['ProducerReturn'][0]
    before = retired.learner.inner.inner.state()
    live_before = live.learner.inner.inner.state()
    restored.queue.settle(handle, channel='test', score=.9, status=SettleStatus.SETTLED,
                          definition_version='1', sampling_ref=None)
    restored._deliver_returns()
    assert not retired.learner.inner.state()['snapshots']
    # A replaced router never samples again: its settled round trains nothing, neither
    # itself nor its successor (learners design §2.5), and is on the record.
    assert retired.learner.inner.inner.state() == before
    assert live.learner.inner.inner.state() == live_before
    assert any(i['kind'] == 'learner.orphaned' and i['handle'] == handle
               for i in restored.ledger._recovery_items())
    assert old.learner.id not in restored.retired_routers
    assert handle not in restored.snapshot_keys
