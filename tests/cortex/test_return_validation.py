import json

import pytest

from factorylab.cortex.assembly import OPTIONAL_SECTIONS, Assembly, AssemblySpec
from factorylab.cortex.request import Request
from factorylab.kernel.ledger import Ledger
from factorylab.kernel.wallet import Wallet
from factorylab.world.metering import Meter, MeteredModel
from factorylab.world.models import FakeModel, PriceTable, TokenPrice


def invoke(body, schema=None, emits=('ProducerReturn',)):
    wallet = Wallet(100000, Ledger())
    provider = FakeModel(default=body, fixed_input_tokens=1, fixed_output_tokens=1)
    assembly = Assembly(AssemblySpec('a', 1, 'v', memory_policy='handle-scoped', max_tokens=16,
                                     emits=emits),
                        MeteredModel(provider, PriceTable({'v': TokenPrice(1, 1)}), Meter(wallet)))
    req = Request('h', 'test', {}, {}, schema or {}, 100, 100000, None, 'JSON', 'test', 'h')
    ret = assembly.invoke(req)
    return ret, assembly, wallet


# Primitive audit F7: a seed kind's own fields are typed on that kind's returns.
_OWNER = {'verdict': ('Verdict',), 'conformity': ('MetaVerdict',)}


@pytest.mark.parametrize('field,bad', [
    ('action', []), ('verdict', '0.5'), ('verdict', True), ('verdict', 2),
    ('conformity', -1), ('rationale', {}), ('register', {}),
    ('tool_calls', [{'tool': [], 'args': {}}]),
    ('tool_calls', [{'tool': 'venue.order', 'args': []}]), ('forecasts', {}),
    ('forecasts', [{'predicate': [], 'q': .5, 'params': {}}]),
    ('forecasts', [{'predicate': 'wallet_up', 'q': '0.5', 'params': {}}]),
    ('forecasts', [{'predicate': 'wallet_up', 'q': .5, 'params': {'horizon_events': 201}}]),
    ('requests', [{'target': [], 'description': 'd', 'inputs': {}, 'outcome_schema': {}}]),
])
def test_wrong_structured_type_fails_before_memory_or_effects(field, bad):
    ret, assembly, wallet = invoke(json.dumps({field: bad, 'tool_calls': []}
                                            if field != 'tool_calls' else {field: bad}),
                                   emits=_OWNER.get(field, ('ProducerReturn',)))
    assert not ret.children and not ret.tool_calls
    assert wallet.state()['reservations'] == [] and ret.cost == 2
    if field in OPTIONAL_SECTIONS and field != 'tool_calls':
        # An optional section beside the answer is dropped with its reason, and
        # nothing in it reaches an effect; the rest of the return stands.
        assert ret.status == 'ok' and field not in ret.outputs
        assert [d['section'] for d in ret.dropped] == [field]
        return
    # The answer itself, or a reply with nothing valid left in it, is malformed.
    assert ret.status == 'malformed' and not assembly.memory


@pytest.mark.parametrize('number', ['1e309', 'NaN', '-Infinity'])
@pytest.mark.parametrize('template', [
    '{{"verdict":{n}}}',
    '{{"forecasts":[{{"predicate":"wallet_up","params":{{}},"q":{n}}}]}}',
    '{{"register":[{{"kind":"router","gamma":{n}}}]}}',
    '{{"tool_calls":[{{"tool":"x","args":{{"nested":[{n}]}}}}]}}',
])
def test_nonfinite_nested_numbers_are_malformed(number, template):
    ret, assembly, _ = invoke(template.format(n=number))
    assert ret.status == 'malformed' and not assembly.memory
    assert not ret.children and not ret.tool_calls


def test_declared_nested_schema_and_required_fields_are_checked():
    schema = {'type': 'object', 'properties': {'result': {'type': 'array',
              'items': {'type': 'integer', 'minimum': 0}}}, 'required': ['result']}
    assert invoke('{"result":[1,"2"]}', schema)[0].status == 'malformed'
    assert invoke('{}', schema)[0].status == 'malformed'
    assert invoke('{"result":[0,2]}', schema)[0].status == 'ok'


def test_a_field_another_kind_owns_has_no_meaning_in_this_one():
    """Primitive audit F7: the universal envelope carries no venue and no seed role.

    A population kind may give ``action``, ``verdict`` or ``vote`` its own meaning;
    the kind that owns one of those fields still types it strictly.
    """
    own = {'Finding': {'type': 'object', 'properties': {'action': {'type': 'array'}}}}
    custom = Assembly(AssemblySpec('a', 1, 'v', max_tokens=16, emits=('Finding',),
                                   schemas=own),
                      MeteredModel(FakeModel(default=json.dumps(
                          {'action': ['order'], 'verdict': 7, 'vote': 'maybe', 'coin': 1}),
                          fixed_input_tokens=1, fixed_output_tokens=1),
                          PriceTable({'v': TokenPrice(1, 1)}), Meter(Wallet(100000, Ledger()))))
    req = Request('h', 'test', {}, {}, {}, 100, 100000, None, 'JSON', 'test', 'h')
    assert custom.invoke(req).status == 'ok'
    assert invoke('{"verdict": 7, "vote": "maybe"}')[0].status == 'ok'
    assert invoke('{"verdict": 7}', emits=('Verdict',))[0].status == 'malformed'
    assert invoke('{"action": 3}', emits=('Verdict',))[0].status == 'ok'
    assert invoke('{"action": 3}')[0].status == 'malformed'


def test_valid_brace_strings_remain_valid():
    assert invoke('{"verdict":0.5,"rationale":"a}b"}')[0].status == 'ok'


@pytest.mark.parametrize('field', ['action', 'verdict', 'conformity', 'rationale', 'vote',
                                   'register', 'requests', 'tool_calls', 'forecasts'])
def test_boundary_type_fuzz_is_total(field):
    # Deterministic wrong-shape corpus includes all JSON container/scalar kinds.
    for value in [None, True, 7, 0.25, 'text', [], {}, [None], {'x': [False]}]:
        ret, _, wallet = invoke(json.dumps({field: value}))
        assert ret.status in ('ok', 'malformed')
        assert wallet.available == wallet.balance and ret.cost == 2


@pytest.mark.parametrize('size', ['not-a-number', '1e-999', '1e999', 'NaN', 'Infinity'])
def test_order_amount_must_fit_the_wire_format_before_any_effect(size):
    ret, assembly, wallet = invoke(json.dumps({'action': 'order', 'coin': 'BTC', 'size': size}))
    assert ret.status == 'malformed' and not assembly.memory
    assert wallet.state()['reservations'] == []


@pytest.mark.parametrize('proposal', [False, {'kind': 'router', 'gamma': 2},
                                      {'kind': 'assembly', 'max_tokens': '512'}])
def test_proposal_shape_errors_reach_individual_runtime_admission(proposal):
    ret, _, wallet = invoke(json.dumps({'action': 'hold', 'register': [proposal]}))
    assert ret.status == 'ok' and ret.outputs['register'] == [proposal]
    assert wallet.state()['reservations'] == [] and ret.cost == 2
