
import pytest

from factorylab.cortex.request import Return
from factorylab.runtime.resume import restore_runtime, runtime_state
from tests.runtime.test_fa_defects import make_runtime


def test_lost_ack_is_intented_before_submission_and_fill_keeps_original_handle(monkeypatch):
    rt = make_runtime()
    rt.consequences.start('caller', 0)
    calls = []
    place = rt.exchange.target.place

    def accept_then_lose(order):
        assert any(i['kind'] == 'order.intent' and i['handle'] == 'caller'
                   for i in rt.ledger._recovery_items())
        calls.append(order)
        place(order)
        raise TimeoutError('lost acknowledgement')

    monkeypatch.setattr(rt.exchange.target, 'place', accept_then_lose)
    ret = Return('caller', {
        'action': 'order', 'coin': 'ETH', 'side': 'buy', 'size': '.001'}, 0, 'ok')
    rt._execute_outputs(ret)
    assert calls[0].client_id == 'caller'
    assert rt.consequences.table.lots[0].handle == 'caller'
    rt._execute_outputs(ret)
    assert len(calls) == 1 and rt.stats.orders_rejected == 0


def test_unknown_ack_defers_fill_attribution_until_recovery(monkeypatch):
    rt = make_runtime()
    rt.consequences.start('caller', 0)
    place, lookup = rt.exchange.target.place, rt.exchange.target.lookup

    def lost(order):
        place(order)
        raise TimeoutError('lost ack')

    monkeypatch.setattr(rt.exchange.target, 'place', lost)
    monkeypatch.setattr(rt.exchange.target, 'lookup', lambda *a, **k: (_ for _ in ()).throw(
        TimeoutError('lookup unavailable')))
    rt._execute_outputs(Return('caller', {
        'action': 'order', 'coin': 'ETH', 'size': '.001'}, 0, 'ok'))
    rt.consequences.finish('caller', 0)
    assert rt.order_intents['caller']['result']['status'] == 'uncertain'
    assert rt.stats.orders_rejected == 0
    rt.consequences.resolve(1000)
    assert rt.consequences.payoff('caller') is None
    assert any(i['kind'] == 'order.uncertain' for i in rt.ledger._recovery_items())
    monkeypatch.undo()
    rt.exchange.target.__dict__.pop("place", None)
    rt.exchange.target.__dict__.pop("lookup", None)
    restored = make_runtime()
    restore_runtime(restored, runtime_state(rt))
    restored._reconcile_orders()
    assert restored.order_intents['caller']['result']['status'] == 'filled'
    assert restored.consequences.table.lots[0].handle == 'caller'
    assert len(restored.exchange.fills(0)) == 1
    assert restored.wallet.check_conservation()
    assert lookup('caller').order_id == restored.consequences.table.orders[0].order_id


@pytest.mark.parametrize('tool', ['venue.place_market', 'venue.place_limit', 'venue.close',
                                 'venue.cancel'])
def test_venue_tool_writes_have_handle_intents(tool):
    rt = make_runtime()
    rt.consequences.start('caller', 0)
    if tool in ('venue.close', 'venue.cancel'):
        rt._run_tool('seed-decider', 'caller', {'tool': 'venue.place_limit', 'args': {
            'coin': 'ETH', 'side': 'buy', 'size': '.001', 'price': '3000' if tool.endswith('close')
            else '1'}}, slot='setup')
    args = {'coin': 'ETH'}
    if tool in ('venue.place_market', 'venue.place_limit'):
        args.update(side='buy', size='.001')
    if tool == 'venue.place_limit':
        args['price'] = '1'
    if tool == 'venue.cancel':
        args['order_id'] = rt.exchange.open_orders()[0]['order_id']
    result, cost = rt._run_tool('seed-decider', 'caller', {'tool': tool, 'args': args})
    assert result['status'] in ('filled', 'resting', 'cancelled') and cost == 0
    assert any(i['kind'] == 'order.intent' and i['handle'] == 'caller'
               and i['operation'] == tool for i in rt.ledger._recovery_items())
