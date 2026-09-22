"""A1: contracts, retirement and recursive composition over an entirely fake world."""

import json
from dataclasses import replace

import pytest

from factorylab.cortex.registration import (
    AssemblyProposal,
)
from factorylab.cortex.request import ChildRequest
from factorylab.kernel.events import Event, EventKind
from factorylab.kernel.queue import PropensityRecord
from factorylab.learners.router import Sample
from factorylab.runtime.loop import Runtime
from factorylab.runtime.resume import resume_runtime
from factorylab.runtime.shared import CH_VERDICT
from factorylab.runtime.worlds import load_manifest
from factorylab.world.models import FakeModel, ModelResponse
from tests.conftest import make_runtime as _runtime
from tests.runtime.test_child_requests import parent_request


def make_runtime():
    rt = _runtime()
    rt._manage_reserve_window()
    return rt


def items(rt, kind):
    return [row for row in rt.ledger._recovery_items() if row['kind'] == kind]


def assembly(rt, handle, aid, *, accepts=('Tick',), emits=('ProducerReturn',), schemas=None):
    rt._register(handle, AssemblyProposal(aid, 'producer', 'fake-haiku',
                                         'Reply with JSON.', accepts, 128, 'low',
                                         emits, schemas or {}))


def routed(rt, aid, event):
    channels = rt._return_channels(aid, event)
    actor = 'test:' + aid
    prop = PropensityRecord((aid,), (1.,), aid, 0, actor, 'fixed')
    handle = rt.queue.open(actor=actor, event_id=event.id, propensity=prop,
                           channel=next(iter(channels.values())), deadline_ns=10**18,
                           parent_handle=None, cost_ceiling=rt.wallet.available,
                           return_channels=channels)
    sample = Sample(prop.action_ids, prop.probs, prop.chosen, prop.rng_seed,
                    prop.learner_id, prop.learner_state_hash, ())
    rt._assembly_step(event, handle, sample, 10**18)
    return handle


def test_a1_all_ancestors_are_excluded_from_judging_a_deep_return():
    rt = make_runtime()
    parent = None
    handles = []
    for aid in ('seed-decider', 'eval-a', 'meta-a'):
        actor = 'parent:' + aid
        handle = rt.queue.open(actor=actor, event_id='chain',
                               propensity=PropensityRecord((aid,), (1.,), aid, 0, actor, 'fixed'),
                               channel=CH_VERDICT, deadline_ns=10**18,
                               parent_handle=parent, cost_ceiling=100)
        rt.handle_to_assembly[handle] = aid
        handles.append(handle)
        parent = handle
    event = Event('return', EventKind.PRODUCER_RETURN, 0, {'about_handle': handles[-1]}, 'runtime')
    assert not {'seed-decider', 'eval-a', 'meta-a'} & set(rt._universe_for('ProducerReturn', event))
    assert rt._subject_authors('ProducerReturn', event) == {'seed-decider', 'eval-a', 'meta-a'}


def test_a1_custom_child_schema_cannot_be_weakened_to_execute_effects(monkeypatch):
    rt = make_runtime()
    req = parent_request(rt)
    # C10: a child request is its parent's subcontracting and spends the parent's
    # entitlement. The parent decision is seed-decider's, as a routed one would be:
    # the trial moves from seed-decider to the child, and the child's call, whose
    # ceiling exceeds the 0.10 USD trial, is covered by seed-decider's share.
    rt.handle_to_assembly[req.handle] = 'seed-decider'
    assembly(rt, req.handle, 'custom-child', emits=('Finding',), schemas={'Finding': {
        'type': 'object', 'properties': {'answer': {'type': 'integer'}}, 'required': ['answer']}})
    monkeypatch.setattr(rt.provider.target, 'complete', lambda request: ModelResponse(
        request.model_id, json.dumps({'answer': 'wrong type', 'tool_calls': [{
            'tool': 'venue.place_market', 'args': {'coin': 'BTC', 'side': 'buy', 'size': '0.1'}}]}),
        1, 1, 'end_turn'))
    # A request names the kind (primitive audit F5); custom-child alone emits Finding.
    result, cost = rt._invoke_child('seed-decider', req, ChildRequest(
        'Finding', 'custom task', {}, {'type': 'object'}), req.cost_ceiling)
    assert result['result']['status'] == 'malformed' and cost > 0
    assert not items(rt, 'tool.call') and not items(rt, 'consequence.order')
    assert rt.internal[-1].kind == 'Finding' and rt.internal[-1].payload['status'] == 'malformed'


@pytest.mark.slow
@pytest.mark.parametrize('cut', ['decision.contract', 'decision.emits'])
def test_a1_polymorphic_decision_replays_after_its_durable_binding_before_mutation(tmp_path, cut):
    base = load_manifest('scripted')
    seed = next(a for a in base.assemblies if a.id == 'seed-decider')
    manifest = replace(base, assemblies=(replace(
        seed, accepts=('Tick',), emits=('ProducerReturn', 'Verdict')),))

    def provider():
        return FakeModel(default='{"emits":"ProducerReturn","action":"hold"}',
                         fixed_input_tokens=1, fixed_output_tokens=1)

    def runtime(path=None):
        return Runtime(manifest, events=2, seed=1, initial_balance_micro=None,
                       ledger_path=path, router_gamma=.1, provider=provider())

    class ProcessDeath(BaseException):
        pass

    path = str(tmp_path / 'binding.jsonl')
    rt = runtime(path)
    append = rt.ledger.append

    def interrupt(entry):
        seq = append(entry)
        if entry['kind'] == cut:
            raise ProcessDeath
        return seq

    rt.ledger.append = interrupt
    with pytest.raises(ProcessDeath):
        rt.run()
    resumed = resume_runtime(manifest, path, provider=provider())
    observed = resumed.run()
    observed['stats']['resumes'] = 0
    assert observed == runtime().run()
    bound = items(resumed, 'decision.emits')
    assert len({row['handle'] for row in bound}) == len(bound)
    assert all(resumed.queue.get(row['handle']).channel == CH_VERDICT for row in bound)
