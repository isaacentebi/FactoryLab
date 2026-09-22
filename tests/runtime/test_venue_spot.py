from factorylab.runtime.loop import Runtime
from factorylab.runtime.resume import decode, encode, runtime_state
from factorylab.runtime.worlds import load_manifest


def runtime():
    return Runtime(load_manifest('scripted'), events=40, seed=1,
                   initial_balance_micro=None, ledger_path=None, router_gamma=.1)


def test_spot_runtime_journal_settlement_and_snapshot():
    rt = runtime()
    rt.treasury.transfer('perps_to_spot', '20', handle='transfer', now_ns=1)
    rt.treasury.tick(2)
    rt.exchange.sync_cash(rt.treasury.venue_balance_usd)
    for handle, side in [('buy', 'buy'), ('sell', 'sell')]:
        rt.consequences.start(handle, rt.n)
        result = rt._venue_write(handle, 'venue.place_market', {
            'coin': 'BTC/USDC', 'market': 'spot', 'side': side, 'size': '.0001'}, slot='tool:0')
        assert result['status'] == 'filled'
        rt._settle_exchange_effects(rt.exchange.drain_events())
        rt.consequences.finish(handle, 1)
    assert not rt.consequences.table.lots
    assert rt.wallet.check_conservation()
    assert rt.window.notional_micro > 0
    state = runtime_state(rt)
    assert decode(state['runtime'])['spot_inventory'] == rt.spot_inventory
    assert decode(encode(rt.exchange.account())) == rt.exchange.account()
    assert rt._world_block()['venue']['spot'][0]['coin'] == 'BTC/USDC'


def test_scripted_provider_exercises_spot_and_transfer():
    rt = runtime()
    rt.run()
    spot = [i for i in rt.order_intents.values() if i['args'].get('market') == 'spot']
    assert {i['args']['side'] for i in spot if i['result']['status'] == 'filled'} == {'buy', 'sell'}
    assert rt.treasury.state['direction'] == 'perps_to_spot'
    assert rt.treasury.state['status'] == 'confirmed'


def test_pending_class_transfer_and_inventory_restore_without_duplicate_capital():
    from factorylab.runtime.resume import restore_runtime

    rt = runtime()
    rt.treasury.transfer('perps_to_spot', '20', handle='transfer', now_ns=1)
    saved = runtime_state(rt)
    restored = runtime()
    restore_runtime(restored, saved)
    assert restored.wallet.available == rt.wallet.available
    assert restored.exchange._spot_cash == 0
    restored.treasury.tick(2)
    assert restored.exchange._spot_cash == 20
    assert restored.treasury.tick(3) == []
    assert restored.wallet.balance == restored.wallet.available == rt.wallet.balance
    restored.consequences.start('buy', 0)
    result = restored._venue_write('buy', 'venue.place_market', {
        'coin': 'BTC/USDC', 'market': 'spot', 'side': 'buy', 'size': '.0001'}, slot='0')
    assert result['status'] == 'filled'
    restored._settle_exchange_effects(restored.exchange.drain_events())
    inventory_state = runtime_state(restored)
    again = runtime()
    restore_runtime(again, inventory_state)
    assert again.spot_inventory == restored.spot_inventory
    assert again.exchange.account() == restored.exchange.account()
    assert again.consequences.table == restored.consequences.table
