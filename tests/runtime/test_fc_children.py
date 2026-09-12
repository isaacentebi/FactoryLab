import json
from dataclasses import replace

from factorylab.kernel.queue import PropensityRecord, SettleStatus
from factorylab.runtime.loop import CH_VERDICT
from factorylab.runtime.resume import restore_runtime, runtime_state
from factorylab.world.models import ModelResponse
from tests.runtime.test_fa_defects import make_runtime


def parent_request(rt, ceiling=1000000):
    lid = 'parent-router'
    h = rt.queue.open(actor=lid, event_id='parent',
                      propensity=PropensityRecord(('seed-decider',), (1.,), 'seed-decider',
                                                  0, lid, 'direct'),
                      channel=CH_VERDICT, deadline_ns=100, parent_handle=None,
                      cost_ceiling=ceiling)
    rt.consequences.start(h, 0)
    return replace(rt._request(h, 'parent task', {}, {'type': 'object'}, 100, CH_VERDICT),
                   cost_ceiling=ceiling)


def test_child_target_is_called_once_judged_and_returned_to_parent(monkeypatch):
    rt = make_runtime()
    spec = rt.assemblies['seed-decider'].spec
    rt._instantiate(replace(spec, id='helper'))
    req = parent_request(rt)
    calls = []

    def provider(request):
        text = request.messages[-1]['content']
        calls.append(text)
        if 'REQUEST\nchild task' in text:
            body = {'answer': 42, 'requests': [{'target': 'self', 'description': 'grandchild',
                                              'inputs': {}, 'outcome_schema': {}}]}
        elif 'tool_results' in text:
            assert '42' in text and 'assembly:helper' in text
            body = {'action': 'hold', 'answer': 'used child'}
        else:
            body = {'requests': [{'target': 'helper', 'description': 'child task',
                                  'inputs': {'question': 'value'}, 'outcome_schema': {
                                      'type': 'object',
                                      'properties': {'answer': {'type': 'integer'}},
                                      'required': ['answer']}}]}
        return ModelResponse(request.model_id, json.dumps(body), 1, 1, 'stop')

    monkeypatch.setattr(rt.provider.target, 'complete', provider)
    ret = rt._invoke('seed-decider', req, 'producer')
    assert ret.status == 'ok' and ret.outputs['answer'] == 'used child'
    assert len(calls) == 3  # parent, one child, parent continuation
    items = rt.ledger._recovery_items()
    children = [i for i in items if i['kind'] == 'decision.open'
                and i['parent_handle'] == req.handle]
    assert len(children) == 1
    child = children[0]
    assert child['propensity']['chosen'] == 'helper'
    evidence = next(i for i in items if i['kind'] == 'request.child')
    assert evidence['resource_liability'] == req.handle
    assert child['cost_ceiling'] <= req.cost_ceiling
    assert any(i['kind'] == 'requests.refused' and i['handle'] == child['handle'] for i in items)
    event = next(ev for ev in rt.internal if ev.payload.get('about_handle') == child['handle'])
    assert str(event.kind) == 'ProducerReturn' and event.payload['outputs']['answer'] == 42
    restored = make_runtime()
    monkeypatch.undo()
    rt.provider.target.__dict__.pop("complete", None)
    restore_runtime(restored, runtime_state(rt))
    restored.queue.settle(child['handle'], channel=CH_VERDICT, score=.7,
                          status=SettleStatus.SETTLED, definition_version='test', sampling_ref=None)
    assert restored.queue.history(child['handle'])[0].score == .7
    assert ret.cost == sum(i['amount'] for i in items if i['kind'] == 'wallet.commit')


def test_self_request_and_unavailable_target_are_addressable(monkeypatch):
    rt = make_runtime()
    req = parent_request(rt)
    calls = []

    def provider(request):
        text = request.messages[-1]['content']
        calls.append(text)
        body = {'action': 'hold'}
        if len(calls) == 1:
            body['requests'] = [{'target': target, 'description': target + ' task',
                                  'inputs': {}, 'outcome_schema': {}}
                                 for target in ('self', 'missing')]
        return ModelResponse(request.model_id, json.dumps(body), 1, 1, 'stop')

    monkeypatch.setattr(rt.provider.target, 'complete', provider)
    ret = rt._invoke('seed-decider', req, 'producer')
    assert ret.status == 'ok' and len(calls) == 3
    child_items = [i for i in rt.ledger._recovery_items() if i['kind'] == 'request.child']
    assert len(child_items) == 2
    assert any(e.payload.get('status') == 'failed' for e in rt.internal)


def test_three_children_are_malformed_before_any_child_effect(monkeypatch):
    rt = make_runtime()
    req = parent_request(rt)
    body = {'requests': [{'target': 'self', 'description': 'child', 'inputs': {},
                           'outcome_schema': {}}] * 3}
    monkeypatch.setattr(rt.provider.target, 'complete', lambda r: ModelResponse(
        r.model_id, json.dumps(body), 1, 1, 'stop'))
    assert rt._invoke('seed-decider', req, 'producer').status == 'malformed'
    assert not any(i['kind'] == 'request.child' for i in rt.ledger._recovery_items())


def test_bad_second_child_schema_prevents_first_child_dispatch(monkeypatch):
    rt = make_runtime()
    req = parent_request(rt)
    body = {'requests': [{'target': 'self', 'description': 'child', 'inputs': {},
                           'outcome_schema': schema} for schema in ({}, {'required': [False]})]}
    monkeypatch.setattr(rt.provider.target, 'complete', lambda r: ModelResponse(
        r.model_id, json.dumps(body), 1, 1, 'stop'))
    assert rt._invoke('seed-decider', req, 'producer').status == 'malformed'
    assert not any(i['kind'] == 'request.child' for i in rt.ledger._recovery_items())


def test_bad_second_tool_argument_prevents_first_venue_effect(monkeypatch):
    rt = make_runtime()
    req = parent_request(rt)
    body = {'tool_calls': [
        {'tool': 'venue.place_market', 'args': {'coin': 'ETH', 'side': 'buy', 'size': '.001'}},
        {'tool': 'venue.candles', 'args': {'coin': 'BTC', 'n': 'bad', 'interval': '1m'}},
    ]}
    monkeypatch.setattr(rt.provider.target, 'complete', lambda r: ModelResponse(
        r.model_id, json.dumps(body), 1, 1, 'stop'))
    assert rt._invoke('seed-decider', req, 'producer').status == 'malformed'
    assert not rt.exchange.fills(0)
    assert not any(i['kind'] == 'order.intent' for i in rt.ledger._recovery_items())
