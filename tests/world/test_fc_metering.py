import pytest

from factorylab.cortex.assembly import Assembly, AssemblySpec
from factorylab.cortex.request import Request
from factorylab.kernel.ledger import Ledger
from factorylab.kernel.wallet import Wallet
from factorylab.world.metering import BillingUncertain, Meter, MeteredModel
from factorylab.world.models import ModelResponse, PriceTable, TokenPrice


@pytest.mark.parametrize('phase', ['execute', 'cost'])
def test_unknown_bill_is_accounted_without_leaking_a_hold(phase):
    wallet = Wallet(100, Ledger())

    def bad(*_):
        raise ValueError('invalid vendor usage')

    with pytest.raises(BillingUncertain):
        Meter(wallet).run(handle='caller', reason='model:vendor', ceiling=20,
                          execute=bad if phase == 'execute' else lambda: 'billed',
                          cost_of=bad if phase == 'cost' else lambda _: 5)
    assert wallet.balance == wallet.available == 80
    assert wallet.state()['reservations'] == []
    assert wallet.check_conservation()
    uncertain = [i for i in wallet.ledger._recovery_items() if i['kind'] == 'metering.uncertain']
    assert len(uncertain) == 1 and uncertain[0]['handle'] == 'caller'
    saved = wallet.state()
    restored = Wallet(100, Ledger())
    restored._restore_state(saved)
    assert restored.state() == saved


@pytest.mark.parametrize('usage', [-1, '5', None, {}, [], True, 1.5, float('nan'), float('inf')])
def test_invalid_usage_at_invocation_never_leaks_reservation(usage):
    wallet = Wallet(10000, Ledger())
    prices = PriceTable({'vendor': TokenPrice(1, 1)})

    class Provider:
        def complete(self, req):
            return ModelResponse('vendor', '{"ok":true}', usage, 1, 'stop')

    assembly = Assembly(AssemblySpec('assembly', 1, 'vendor', max_tokens=16),
                        MeteredModel(Provider(), prices, Meter(wallet)))
    req = Request('caller', 'test', {}, {}, {}, 100, 10000, None, 'JSON', 'test', 'caller')
    ret = assembly.invoke(req)
    assert ret.status == 'failed'
    assert wallet.state()['reservations'] == []
    assert wallet.available == wallet.balance < 10000
    assert ret.cost == 10000 - wallet.balance
