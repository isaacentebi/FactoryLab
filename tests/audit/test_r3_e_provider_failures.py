"""T46: provider failures retain identity and release only known-unbilled holds."""

import socket
from urllib.error import URLError

import pytest

from factorylab.cortex.assembly import Assembly, AssemblySpec
from factorylab.cortex.request import Request
from factorylab.kernel.ledger import Ledger
from factorylab.kernel.wallet import Wallet
from factorylab.runtime.resume import JournalProxy, RecoveryJournal
from factorylab.world.metering import Meter, MeteredModel, UnbilledFailure
from factorylab.world.models import ModelRequest, PriceTable, TokenPrice
from factorylab.world.openrouter import OpenRouterProvider
from factorylab.world.venice import VeniceProvider


@pytest.mark.parametrize("provider_type", [OpenRouterProvider, VeniceProvider])
@pytest.mark.parametrize("failure", ["connection", "dns", "timeout", "401", "429", "500", "decode"])
def test_provider_failure_billing_and_identity_survive_replay(provider_type, failure):
    calls = []

    def transport(*args):
        calls.append(args)
        if failure == "connection":
            raise ConnectionError("secret transport details")
        if failure == "dns":
            raise URLError(socket.gaierror("secret transport details"))
        if failure == "timeout":
            raise TimeoutError("secret transport details")
        if failure in ("401", "429", "500"):
            from urllib.error import HTTPError

            raise HTTPError("https://provider.invalid", int(failure), "secret", {}, None)
        raise ValueError("secret decoding details")

    provider = provider_type(transport=transport)
    model_id = "venice:vendor" if provider_type is VeniceProvider else "vendor"
    error_name = "VeniceError" if provider_type is VeniceProvider else "OpenRouterError"
    ledger = Ledger(clock_ns=lambda: 0)
    journal = RecoveryJournal(ledger, lambda: 0)
    journal.active = True
    req = Request("caller", "test", {}, {}, {}, 100, 10000, None, "JSON", "test", "caller")

    def invoke():
        wallet = Wallet(10000, Ledger())
        model = MeteredModel(JournalProxy(provider, journal, "provider"),
                             PriceTable({model_id: TokenPrice(1, 1)}), Meter(wallet))
        assembly = Assembly(AssemblySpec("assembly", 1, model_id, max_tokens=16), model)
        ret = assembly.invoke(req)
        assert ret.status == "failed"
        assert error_name in ret.outputs["reason"]
        assert wallet.state()["reservations"] == []
        assert wallet.check_conservation()
        assert wallet.balance == wallet.available == 10000 - ret.cost
        assert (ret.cost > 0) if failure in ("decode", "500") else (ret.cost == 0)
        return ret

    first = invoke()
    items = ledger._recovery_items()
    assert items[-1]["error"] == error_name
    assert items[-1]["unbilled"] is (failure not in ("decode", "500"))
    assert items[-1]["status"] == (int(failure) if failure.isdigit() else None)
    assert "secret" not in str(items)
    journal.recovering = True
    journal.tail = items
    assert invoke() == first
    assert len(calls) == 1


@pytest.mark.parametrize("provider_type", [OpenRouterProvider, VeniceProvider])
def test_runtime_invocation_records_provider_error_without_a_debit(provider_type):
    from factorylab.runtime.loop import Runtime
    from factorylab.runtime.worlds import load_manifest

    def transport(*_):
        raise ConnectionError("secret transport details")

    runtime = Runtime(load_manifest("scripted"), events=3, seed=1,
                      initial_balance_micro=100_000_000, ledger_path=None, drip=False,
                      router_gamma=.1)
    provider = provider_type(transport=transport)
    # Keep the ordinary runtime routing, but use the real provider adapter with fake I/O.
    provider.balance_micro = lambda: None
    runtime.provider.target = provider
    runtime.provider.deterministic = False
    if provider_type is VeniceProvider:
        from dataclasses import replace

        for assembly in runtime.assemblies.values():
            old_id = assembly.spec.model_id
            model_id = "venice:" + old_id
            runtime.prices.register(model_id, runtime.prices.price(old_id))
            assembly.spec = replace(assembly.spec, model_id=model_id)
    runtime.run()
    invocations = [i for i in runtime.ledger._recovery_items() if i["kind"] == "invocation"]
    assert invocations
    error_name = "VeniceError" if provider_type is VeniceProvider else "OpenRouterError"
    assert all(error_name in i["outputs"] and i["cost"] == 0 for i in invocations)
    assert "secret" not in str(invocations)
    assert not runtime.wallet.state()["reservations"]


@pytest.mark.parametrize("provider_type", [OpenRouterProvider, VeniceProvider])
def test_missing_provider_credentials_release_the_hold(monkeypatch, provider_type):
    monkeypatch.delenv("R3_E_MISSING_KEY", raising=False)
    monkeypatch.delenv("RESERVE_PRIVATE_KEY", raising=False)
    provider = provider_type(key_env="R3_E_MISSING_KEY")
    model_id = "venice:vendor" if provider_type is VeniceProvider else "vendor"
    ledger = Ledger(clock_ns=lambda: 0)
    journal = RecoveryJournal(ledger, lambda: 0)
    journal.active = True
    wallet = Wallet(10000, Ledger())
    model = MeteredModel(JournalProxy(provider, journal, "provider"),
                         PriceTable({model_id: TokenPrice(1, 1)}), Meter(wallet))
    req = ModelRequest(model_id, "", (), 16)
    with pytest.raises(UnbilledFailure):
        model.complete(req, handle="caller")
    assert wallet.balance == wallet.available == 10000
    assert not wallet.state()["reservations"]
    assert ledger._recovery_items()[-1]["unbilled"] is True
