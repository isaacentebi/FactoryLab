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


def test_core_router_proposal_then_delayed_settlement_across_a_coverage_phase():
    """A population core router for Tick (a per-tick kind; learners design §2.2) is built
    once the kind's gate allows a replacement; a round it drew is still pending when a
    forecast-shaped seat raises the core's coverage bound and, past the gate, opens a
    phase. Through a resume, the round settles for the kernel and trains nothing."""
    rt = make_runtime()
    rt._manage_reserve_window()
    gate = rt.m.timing.min_ratio * max(rt.clockwork.measured('router:Tick'),
                                       rt._delivery_bound())
    rt.ticks_consumed = gate
    rt._register('population', RouterProposal("Tick", "blum_mansour"))
    old = rt.routers['Tick'][0]
    assert isinstance(old.learner, _KeyedLearner) and old.learner.inner.core
    assert rt.clockwork.opened('epoch:Tick') == gate  # the replacement opened a phase
    # Drawn shortly before the next phase may open, so it is still in flight then.
    rt.ticks_consumed = 2 * gate - 10
    old.learner.current_key, old.learner.current_ordinal = 'old', rt.n
    sample = old.router.route('Tick', lambda _: (True, ''), rt.rng,
                              mix=rt._cap_adversarial)
    handle = rt.queue.open(actor=old.learner.id, event_id='p', propensity=rt._propensity(sample),
                           channel='test', horizon_ticks=50, parent_handle=None,
                           cost_ceiling=0)
    rt.snapshot_keys[handle] = 'old'
    spec = next(a.spec for a in rt.assemblies.values() if a.spec.role == 'evaluator')
    rt._instantiate(replace(spec, id='new-judge', accepts=frozenset({'Tick'})))
    rt._open_epoch('Tick')
    assert rt.routers['Tick'][0] is old  # the raised bound waits for the gate
    rt.ticks_consumed += 10
    rt._open_pending_epochs()
    assert rt.routers['Tick'][0].learner.id != old.learner.id
    assert old.learner.id in rt.retired_routers
    restored = make_runtime()
    restore_runtime(restored, runtime_state(rt))
    retired = restored.retired_routers[old.learner.id]
    live = restored.routers['Tick'][0]
    before = retired.learner.inner.inner.state()
    live_before = live.learner.inner.inner.state()
    noop = sample.chosen == 'NOOP'
    restored.queue.settle(handle, channel='test', score=0. if noop else .9,
                          status=SettleStatus.INAPPLICABLE if noop else SettleStatus.SETTLED,
                          definition_version='1', sampling_ref=None)
    restored._deliver_returns()
    restored.ticks_consumed += 60  # an abstention's credit is due at its cutoff
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
