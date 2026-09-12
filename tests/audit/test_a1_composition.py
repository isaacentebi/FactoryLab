"""A1: contracts, retirement and recursive composition over an entirely fake world."""

import json
from dataclasses import replace

import pytest

from factorylab.cortex.assembly import SEED_SYSTEM_PROMPT
from factorylab.cortex.registration import (
    AssemblyProposal,
    RetireProposal,
    measured_role,
    parse_proposals,
)
from factorylab.cortex.request import ChildRequest, Return
from factorylab.kernel.events import Event, EventKind
from factorylab.kernel.queue import PropensityRecord, SettleStatus
from factorylab.learners.router import Sample
from factorylab.runtime.loop import Runtime
from factorylab.runtime.resume import restore_runtime, resume_runtime, runtime_state
from factorylab.runtime.shared import CH_CONFORMITY, CH_VERDICT
from factorylab.runtime.worlds import load_manifest
from factorylab.world.models import FakeModel, ModelResponse
from factorylab.world.scripted import _description_from_prompt, _inputs_from_prompt
from tests.runtime.test_fa_defects import make_runtime as _runtime
from tests.runtime.test_fc_children import parent_request
from tests.runtime.test_fidelity import decision


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


def subject(rt, aid='seed-decider'):
    handle = decision(rt, aid)
    rt._start_return(handle)
    rt.consequences.finish(handle, 0)
    from factorylab.runtime.feedback import PendingJudgement

    rt.pending[handle] = PendingJudgement(handle, CH_VERDICT, rt.n)
    rt._emit(EventKind.PRODUCER_RETURN, {
        'about_handle': handle, 'description': 'research', 'inputs': {'question': 'signal'},
        'outputs': {'action': 'hold'}, 'cost': 0, 'status': 'ok'})
    return rt.internal[-1]


def test_a1_parent_helper_grandchild_execute_with_tools(monkeypatch):
    rt = make_runtime()
    req = parent_request(rt)
    assembly(rt, req.handle, 'helper')
    assembly(rt, req.handle, 'grandchild')
    calls = []

    def complete(request):
        prompt = request.messages[-1]['content']
        description, inputs = _description_from_prompt(prompt), _inputs_from_prompt(prompt)
        calls.append(description)
        if 'tool_results' in inputs:
            body = {'action': 'hold', 'answer': 42}
        elif description == 'grandchild':
            body = {'tool_calls': [{'tool': 'catalogue.search',
                                   'args': {'substring': 'fake', 'limit': 1}}]}
        else:
            target = 'grandchild' if description == 'helper' else 'helper'
            body = {'requests': [{'target': target, 'description': target, 'inputs': {},
                                  'outcome_schema': {'type': 'object',
                                                     'required': ['answer']}}]}
        return ModelResponse(request.model_id, json.dumps(body), 1, 1, 'end_turn')

    monkeypatch.setattr(rt.provider.target, 'complete', complete)
    ret = rt._invoke('seed-decider', req, 'producer')
    assert ret.status == 'ok' and ret.outputs['answer'] == 42
    assert calls == ['parent task', 'helper', 'grandchild', 'grandchild', 'helper', 'parent task']
    children = items(rt, 'request.child')
    assert [row['target'] for row in children] == ['helper', 'grandchild']
    assert rt.queue.get(children[1]['handle']).parent_handle == children[0]['handle']
    assert all(row['cost_ceiling'] <= req.cost_ceiling for row in children)
    assert items(rt, 'tool.call')[0]['handle'] == children[1]['handle']
    assert items(rt, 'tool.call')[0]['ok']
    assert ret.cost == sum(row['amount'] for row in items(rt, 'wallet.commit'))
    assert rt.wallet.check_conservation() and rt.ledger.verify()


def test_a1_registered_producer_accepting_producer_return_is_dispatched_as_producer(monkeypatch):
    rt = make_runtime()
    origin = parent_request(rt).handle
    assembly(rt, origin, 'combined', accepts=('ProducerReturn', 'MetaVerdict'))
    event = subject(rt)
    assert 'combined' in rt._universe_for('ProducerReturn', event)
    state = rt.routers['ProducerReturn'][0]
    monkeypatch.setattr(state.learner, 'distribution', lambda feasible: {
        aid: float(aid == 'combined') for aid in feasible})
    monkeypatch.setattr(rt.provider.target, 'complete', lambda request: ModelResponse(
        request.model_id, '{"action":"hold","answer":"research"}', 1, 1, 'end_turn'))
    rt._route_with(state, event)
    invocation = items(rt, 'invocation')[-1]
    assert invocation['assembly_id'] == 'combined' and invocation['status'] == 'ok'
    handle = invocation['handle']
    assert rt.queue.get(handle).channel == CH_VERDICT
    assert rt.internal[-1].kind is EventKind.PRODUCER_RETURN
    assert rt.internal[-1].payload['about_handle'] == handle
    assert rt.queue.get(event.payload['about_handle']).status is SettleStatus.PENDING


def test_a1_retire_seed_retains_pending_feedback_and_accepts_a_new_version():
    rt = make_runtime()
    for aid in rt.assemblies:
        for _ in range(rt.m.committee.min_settled):
            decision(rt, aid, settled=True)
    old = rt.routers['ProducerReturn'][0]
    target = 'eval-a'
    handle = rt.queue.open(actor=old.learner.id, event_id='pending-seed',
                           propensity=PropensityRecord((target,), (1.,), target, 0,
                                                       old.learner.id, 'frozen'),
                           channel=CH_CONFORMITY, deadline_ns=10**18,
                           parent_handle=None, cost_ceiling=100)
    rt.handle_to_assembly[handle] = target
    rt._start_return(handle)
    rt.consequences.finish(handle, 2)
    proposer = decision(rt, 'seed-decider')
    rt._apply_registrations(proposer, Return(proposer, {
        'register': [{'kind': 'retire', 'assembly_id': target}]}, 0, 'ok'))
    row = next(iter(rt.retirement_proposals.values()))
    assert row['status'] == 'passed'
    assert all(seat.assembly_id != 'seed-decider' for seat in row['committee'].seats)
    assert target not in rt.retired_assemblies  # governance cadence still applies
    rt.n = rt.m.timing.min_ratio * rt.ev.consequence_backstop_events + 1
    rt.cadence.advance(rt.n)
    rt.clock.now_ns = rt.n * rt.tick_clock.interval_ns
    rt.stats.reserve_windows += 1
    rt._activate_charter_if_due()
    assert target in rt.retired_assemblies
    assert all(target not in router.universe for router in rt._all_router_states())
    assert old.learner.id in rt.retired_routers
    fresh = rt.routers['ProducerReturn'][0]
    before_old, before_fresh = old.learner.state(), fresh.learner.state()
    rt.queue.settle(handle, channel=CH_CONFORMITY, score=.8, status=SettleStatus.SETTLED,
                    definition_version='conformity-v1', sampling_ref=None)
    rt._deliver_returns()
    assert rt.queue.history(handle)[0].score == .8
    assert old.learner.state() != before_old and fresh.learner.state() == before_fresh
    assert rt.consequences.table.account(handle).cost_micro == 2
    accepted, refused = parse_proposals({'register': [{
        'kind': 'assembly', 'id': target, 'role': 'producer', 'model_id': 'fake-haiku',
        'system_prompt': 'Reply with JSON.', 'accepts': ['Tick'], 'emits': ['ProducerReturn']}]},
        event_kinds=rt._event_kinds(), known_models=frozenset(rt.prices.prices),
        known_assemblies=frozenset(rt.assemblies),
        retired_assemblies=frozenset(rt.retired_assemblies))
    assert not refused
    rt._manage_reserve_window()
    rt._register(proposer, accepted[0])
    assert rt.assemblies[target].spec.version == 2
    assert rt.registry.get(target, 1).output_schema['emits'] == ('Verdict',)
    assert rt.registry.get(target, 2).output_schema['emits'] == ('ProducerReturn',)
    assert rt.queue.history(handle)[0].score == .8
    assert target in rt._universe_for('Tick')


@pytest.mark.parametrize('emits,channel', [('ProducerReturn', CH_VERDICT),
                                         ('Verdict', CH_CONFORMITY)])
def test_a1_return_selects_its_emitted_channel_and_binding_survives_resume(
    monkeypatch, emits, channel,
):
    rt = make_runtime()
    origin = parent_request(rt).handle
    assembly(rt, origin, 'combined', accepts=('ProducerReturn',),
             emits=('ProducerReturn', 'Verdict'))
    event = subject(rt)
    reply = ({'emits': emits, 'action': 'hold'} if emits == 'ProducerReturn' else
             {'emits': emits, 'verdict': .9, 'payoff': .1, 'rationale': 'quality', 'forecasts': []})
    monkeypatch.setattr(rt.provider.target, 'complete', lambda request: ModelResponse(
        request.model_id, json.dumps(reply), 1, 1, 'end_turn'))
    handle = routed(rt, 'combined', event)
    assert rt.queue.get(handle).channel == channel
    assert items(rt, 'decision.emits')[-1]['emits'] == emits
    monkeypatch.undo()
    rt.provider.target.__dict__.pop('complete', None)
    resumed = make_runtime()
    restore_runtime(resumed, runtime_state(rt))
    assert resumed.queue.get(handle).channel == channel
    other = 'ProducerReturn' if emits == 'Verdict' else 'Verdict'
    with pytest.raises(ValueError, match='cannot change'):
        resumed.queue.bind(handle, other)
    resumed.queue.settle(handle, channel=channel, score=.7, status=SettleStatus.SETTLED,
                         definition_version='test', sampling_ref=None)
    assert resumed.queue.history(handle)[0].channel == channel
    assert resumed.queue.returns_for('test:combined')[-1].score == .7


def test_a1_declared_event_schema_and_population_router_execute_and_resume(monkeypatch):
    rt = make_runtime()
    origin = parent_request(rt).handle
    schema = {'type': 'object', 'properties': {'answer': {'type': 'integer'}},
              'required': ['answer'], 'additionalProperties': False}
    assembly(rt, origin, 'researcher', emits=('Finding',), schemas={'Finding': schema})
    assembly(rt, origin, 'reader', accepts=('Finding',))
    rt._apply_registrations(origin, Return(origin, {'register': [{
        'kind': 'router', 'event_kind': 'Finding', 'learner': 'blum_mansour', 'gamma': .1}
    ]}, 0, 'ok'))
    assert rt.routers['Finding'][0].universe == ['reader', 'NOOP']
    monkeypatch.setattr(rt.provider.target, 'complete', lambda request: ModelResponse(
        request.model_id, '{"emits":"Finding","answer":42}', 1, 1, 'end_turn'))
    handle = routed(rt, 'researcher', Event('t', EventKind.TICK, 0, {'index': 0}, 'fake'))
    event = rt.internal[-1]
    assert str(event.kind) == 'Finding' and event.payload['outputs']['answer'] == 42
    assert rt.queue.get(handle).channel == CH_VERDICT
    assert rt._world_block()['event_schemas']['Finding'] == schema
    monkeypatch.undo()
    rt.provider.target.__dict__.pop('complete', None)
    resumed = make_runtime()
    restore_runtime(resumed, runtime_state(rt))
    assert str(resumed.internal[-1].kind) == 'Finding'
    assert resumed.event_schemas == rt.event_schemas
    with pytest.raises(ValueError, match='differently'):
        assembly(resumed, origin, 'bad-schema', emits=('Finding',),
                 schemas={'Finding': {'type': 'object'}})


def test_a1_depth_bound_refuses_only_the_excess_child_and_keeps_continuations(monkeypatch):
    rt = make_runtime()
    rt.m = replace(rt.m, tools=replace(rt.m.tools, max_depth=2))
    req = parent_request(rt)
    calls = []

    def complete(request):
        prompt = request.messages[-1]['content']
        inputs = _inputs_from_prompt(prompt)
        calls.append(inputs)
        body = ({'action': 'hold'} if 'tool_results' in inputs else
                {'requests': [{'target': 'self', 'description': 'recurse', 'inputs': {},
                               'outcome_schema': {'type': 'object'}}]})
        return ModelResponse(request.model_id, json.dumps(body), 1, 1, 'end_turn')

    monkeypatch.setattr(rt.provider.target, 'complete', complete)
    ret = rt._invoke('seed-decider', req, 'producer')
    assert ret.status == 'ok' and len(calls) == 6
    assert len(items(rt, 'request.child')) == 2
    refusal = items(rt, 'requests.refused')
    assert len(refusal) == 1 and refusal[0]['reason'] == 'tools.max_depth reached'
    assert refusal[0]['depth'] == 2
    assert ret.cost <= req.cost_ceiling


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


def test_a1_public_seed_contracts_and_manifest_bounds_are_truthful():
    rt = make_runtime()
    world = rt._world_block()
    assert world['mechanics']['tools'] == {
        'max_depth': 4, 'max_children': 3, 'max_tool_calls': 4, 'continuations_per_request': 1}
    assert world['reserved_return_fields']['requests']['maxItems'] == 3
    assert world['proposal_shapes']['retire']['kind'] == 'retire'
    # The prompt composes; the mechanics it used to announce are published as data only.
    assert 'cannot request further' not in SEED_SYSTEM_PROMPT
    for announced in ('depth', 'fan-out', 'children', 'continuation', 'cost', 'ceiling',
                      'router', 'Routers', 'retirement', 'sortition', 'tool', 'accepts',
                      'emits', 'ProducerReturn', 'Verdict', 'MetaVerdict', 'Exposure',
                      'charter', 'mechanics', 'world.mechanics'):
        assert announced not in SEED_SYSTEM_PROMPT
    assert 'outcome schema' in SEED_SYSTEM_PROMPT and '"register"' in SEED_SYSTEM_PROMPT
    assert '"requests"' in SEED_SYSTEM_PROMPT and 'public' in SEED_SYSTEM_PROMPT
    for aid, kind in [('seed-decider', 'ProducerReturn'), ('eval-a', 'Verdict'),
                      ('meta-a', 'MetaVerdict'), ('antagonist-a', 'Exposure')]:
        assert tuple(rt.registry.get(aid).output_schema['emits']) == (kind,)
    with pytest.raises(ValueError, match='positive|integer'):
        replace(rt.m, tools=replace(rt.m.tools, max_depth=True)).validate()
    with pytest.raises(ValueError):
        rt._register(parent_request(rt).handle, RetireProposal('unknown'))


def test_a1_a_free_form_role_label_never_moves_a_returns_measurement(monkeypatch):
    """A registration's role is a display name; cards measure the contract it emits."""
    from factorylab.charter.charter import MetricCard
    from factorylab.charter.measurement import _groups
    from factorylab.charter.windows import MetricWindow

    rt = make_runtime()
    origin = parent_request(rt).handle
    rt._register(origin, AssemblyProposal('desk-researcher', 'researcher', 'fake-haiku',
                                          'Reply with JSON.', ('Tick',), 128, 'low',
                                          ('ProducerReturn',), {}))
    rt._register(origin, AssemblyProposal('desk-critic', 'researcher', 'fake-haiku',
                                          'Reply with JSON.', ('ProducerReturn',), 128, 'low',
                                          ('Verdict',), {}))
    assert [rt.assemblies[a].spec.role for a in ('desk-researcher', 'desk-critic')] == [
        'researcher', 'researcher']  # the label survives as a display name
    monkeypatch.setattr(rt.provider.target, 'complete', lambda request: ModelResponse(
        request.model_id, '{"action":"hold"}', 1, 1, 'end_turn'))
    handle = routed(rt, 'desk-researcher', Event('t', EventKind.TICK, 0, {'index': 0}, 'fake'))
    row = next(r for r in rt.card_samples.returns if r['handle'] == handle)
    assert row['role'] == 'producer' and rt.queue.get(handle).channel == CH_VERDICT
    card = MetricCard('cost-producer', 'thrift', 'producer cost', 'micro-USD',
                      MetricWindow('returns', 1, 'role'), 'below 1000', 'cost_per_return',
                      'producer')
    assert handle in [r['handle'] for r in _groups(card, rt.card_samples.returns)['producer']]
    # The evaluator-scoped card and the evaluator forecast-skill set follow emits too.
    assert measured_role(rt.assemblies['desk-critic'].spec.emits) == 'evaluator'
    assert 'desk-critic' in {a.spec.id for a in rt.assemblies.values()
                             if measured_role(a.spec.emits) == 'evaluator'}


def test_a1_custom_child_schema_cannot_be_weakened_to_execute_effects(monkeypatch):
    rt = make_runtime()
    req = parent_request(rt)
    assembly(rt, req.handle, 'custom-child', emits=('Finding',), schemas={'Finding': {
        'type': 'object', 'properties': {'answer': {'type': 'integer'}}, 'required': ['answer']}})
    monkeypatch.setattr(rt.provider.target, 'complete', lambda request: ModelResponse(
        request.model_id, json.dumps({'answer': 'wrong type', 'tool_calls': [{
            'tool': 'venue.place_market', 'args': {'coin': 'BTC', 'side': 'buy', 'size': '0.1'}}]}),
        1, 1, 'end_turn'))
    result, cost = rt._invoke_child('seed-decider', req, ChildRequest(
        'custom-child', 'custom task', {}, {'type': 'object'}), req.cost_ceiling)
    assert result['result']['status'] == 'malformed' and cost > 0
    assert not items(rt, 'tool.call') and not items(rt, 'consequence.order')
    assert rt.internal[-1].kind == 'Finding' and rt.internal[-1].payload['status'] == 'malformed'


def test_a1_continuation_cannot_change_emits_after_reading_tool_results(monkeypatch):
    rt = make_runtime()
    origin = parent_request(rt).handle
    assembly(rt, origin, 'combined', emits=('ProducerReturn', 'Verdict'))
    calls = []

    def complete(request):
        calls.append(request)
        reply = ({'emits': 'ProducerReturn', 'tool_calls': [{
            'tool': 'catalogue.search', 'args': {'substring': 'fake', 'limit': 1}}]}
            if len(calls) == 1 else {'emits': 'Verdict', 'verdict': .9, 'payoff': .1,
                                   'rationale': 'changed', 'forecasts': []})
        return ModelResponse(request.model_id, json.dumps(reply), 1, 1, 'end_turn')

    monkeypatch.setattr(rt.provider.target, 'complete', complete)
    handle = routed(rt, 'combined', Event('tick', EventKind.TICK, 0, {'index': 0}, 'fake'))
    assert len(calls) == 2 and len(items(rt, 'tool.call')) == 1
    assert rt.queue.get(handle).channel == CH_VERDICT
    assert rt.return_kinds[handle] == 'ProducerReturn'
    assert items(rt, 'invocation')[-1]['status'] == 'malformed'


def test_a1_judge_cost_account_does_not_create_an_extra_novelty_trial(monkeypatch):
    rt = make_runtime()
    event = subject(rt)
    monkeypatch.setattr(rt.provider.target, 'complete', lambda request: ModelResponse(
        request.model_id, '{"verdict":0.8,"payoff":0.1,"rationale":"quality","forecasts":[]}',
        1, 1, 'end_turn'))
    handle = routed(rt, 'eval-a', event)
    assert rt.consequences.table.account(handle).cost_micro > 0
    rt._settle_due_forecasts()
    assert rt.consequences.table.account(handle).payoff is not None
    assert rt.stats.consequences_by_assembly['eval-a'] == 1


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
                       ledger_path=path, drip=True, router_gamma=.1, provider=provider())

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


@pytest.mark.slow
def test_a1_scripted_world_exercises_each_composition_freedom_once():
    base = load_manifest('scripted')
    manifest = replace(base, evaluation=replace(base.evaluation, consequence_backstop_events=20))
    rt = Runtime(manifest, events=260, seed=1, initial_balance_micro=None,
                 ledger_path=None, drip=True, router_gamma=.1)
    summary = rt.run()
    children = items(rt, 'request.child')
    assert [row['target'] for row in children] == ['composition-helper', 'funding-watcher']
    helper, grandchild = (row['handle'] for row in children)
    assert rt.queue.get(grandchild).parent_handle == helper
    assert any(row['handle'] == grandchild and row['tool'] == 'catalogue.search' and row['ok']
               for row in items(rt, 'tool.call'))
    assert rt.return_events[helper].kind == 'Finding'
    assert 'Finding' in rt.routers
    assert rt.stats.invocations_by_assembly['return-observer'] > 0
    assert 'ProducerReturn' in rt.assemblies['return-observer'].spec.accepts
    assert rt.assemblies['return-observer'].spec.emits == ('ProducerReturn',)
    assert [row['assembly_id'] for row in items(rt, 'assembly.retired')] == ['eval-a']
    assert all('eval-a' not in router.universe for router in rt._all_router_states())
    assert summary['wallet_conservation'] and summary['ledger_verify']
    restored = Runtime(manifest, events=260, seed=1, initial_balance_micro=None,
                       ledger_path=None, drip=True, router_gamma=.1)
    restore_runtime(restored, runtime_state(rt))
    assert runtime_state(restored) == runtime_state(rt)
