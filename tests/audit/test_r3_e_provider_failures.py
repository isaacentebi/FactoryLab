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

# A failure only releases the hold when the request provably never reached the provider.
UNBILLED = ("dns", "refused", "401", "429")


@pytest.mark.parametrize("provider_type", [OpenRouterProvider, VeniceProvider])
@pytest.mark.parametrize(
    "failure", ["connection", "dns", "refused", "timeout", "401", "429", "500", "decode"]
)
def test_provider_failure_billing_and_identity_survive_replay(provider_type, failure):
    calls = []

    def transport(*args):
        calls.append(args)
        if failure == "connection":
            # A drop after the POST was written: the provider may have generated and billed.
            raise ConnectionError("secret transport details")
        if failure == "dns":
            raise URLError(socket.gaierror("secret transport details"))
        if failure == "refused":
            raise URLError(ConnectionRefusedError("secret transport details"))
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
    req = Request("caller", "test", {}, {}, {}, 100, 20000, None, "JSON", "test", "caller")

    def invoke():
        wallet = Wallet(20000, Ledger())
        model = MeteredModel(JournalProxy(provider, journal, "provider"),
                             PriceTable({model_id: TokenPrice(1, 1)}), Meter(wallet))
        assembly = Assembly(AssemblySpec("assembly", 1, model_id, max_tokens=16), model)
        ret = assembly.invoke(req)
        assert ret.status == "failed"
        assert error_name in ret.outputs["reason"]
        assert wallet.state()["reservations"] == []
        assert wallet.check_conservation()
        assert wallet.balance == wallet.available == 20000 - ret.cost
        assert (ret.cost == 0) if failure in UNBILLED else (ret.cost > 0)
        return ret

    first = invoke()
    items = ledger._recovery_items()
    assert items[-1]["error"] == error_name
    assert items[-1]["unbilled"] is (failure in UNBILLED)
    assert items[-1]["status"] == (int(failure) if failure.isdigit() else None)
    assert "secret" not in str(items)
    journal.recovering = True
    journal.tail = items
    assert invoke() == first
    assert len(calls) == 1


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
