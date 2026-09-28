"""T46: provider failures retain identity and release only known-unbilled holds."""

import io
import json
import socket
from urllib.error import HTTPError, URLError

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


@pytest.mark.parametrize("provider_type", [OpenRouterProvider, VeniceProvider])
@pytest.mark.parametrize("status", [400, 500])
def test_http_diagnostic_redaction_bounds_and_exact_replay(monkeypatch, provider_type, status):
    from factorylab.kernel.ledger import canonical

    key = "diagnostic-test-api-token"
    monkeypatch.setenv("DIAGNOSTIC_TEST_API_KEY", key)
    body = f"upstream-marker\n  rejected\t{key} " + "detail " * 120
    calls = []

    def transport(*args):
        calls.append(args)
        raise HTTPError("https://provider.invalid", status, "unused", {},
                        io.BytesIO(body.encode()))

    provider = provider_type(key_env="DIAGNOSTIC_TEST_API_KEY", transport=transport)
    model_id = "venice:vendor" if provider_type is VeniceProvider else "vendor"
    ledger = Ledger(clock_ns=lambda: 0)
    journal = RecoveryJournal(ledger, lambda: 0)
    journal.active = True
    req = Request("caller", "test", {}, {}, {}, 100, 20000, None, "JSON", "test", "caller")

    def invoke():
        model = MeteredModel(JournalProxy(provider, journal, "provider"),
                             PriceTable({model_id: TokenPrice(1, 1)}),
                             Meter(Wallet(20000, Ledger())))
        assembly = Assembly(AssemblySpec("assembly", 1, model_id, max_tokens=16), model)
        return assembly.invoke(req)

    first = invoke()
    items = ledger._recovery_items()
    expected = " ".join(body.replace(key, "[REDACTED]").split())[:500]
    assert items[-1]["http_status"] == status
    assert items[-1]["provider_message"] == expected
    assert first.provider == {"http_status": status, "provider_message": expected}
    assert len(expected) == 500
    assert key not in json.dumps(items)
    assert "upstream-marker" not in json.dumps(first.outputs)
    before = canonical(items)
    journal.recovering = True
    journal.tail = items
    assert invoke() == first
    assert canonical(ledger._recovery_items()) == before
    assert len(calls) == 1


@pytest.mark.parametrize("name", ["OpenRouterError", "VeniceError"])
def test_old_http_journal_does_not_invent_diagnostics(name):
    from factorylab.runtime.resume import _recorded_error
    from factorylab.world.metering import classify_provider_failure, provider_failure_diagnostic

    failure = _recorded_error(name, status=400, unbilled=True)
    assert provider_failure_diagnostic(failure) == {}
    assert provider_failure_diagnostic(classify_provider_failure(failure)) == {}


@pytest.mark.gate
@pytest.mark.parametrize("provider_type", [OpenRouterProvider, VeniceProvider])
@pytest.mark.parametrize("status", [400, 500])
def test_http_diagnostic_is_diary_only_across_later_invocations(monkeypatch, provider_type, status):
    from tests.conftest import make_runtime
    from tests.runtime.test_connectors import decision

    key = "diary-only-api-token"
    monkeypatch.setenv("DIAGNOSTIC_TEST_API_KEY", key)
    body = f"operator-only-marker\n rejected\t{key} " + "detail " * 120
    payloads = []

    def transport(method, path, payload):
        payloads.append(payload)
        raise HTTPError("https://provider.invalid", status, "unused", {},
                        io.BytesIO(body.encode()))

    def runtime():
        rt = make_runtime()
        rt._manage_reserve_window()
        provider = provider_type(key_env="DIAGNOSTIC_TEST_API_KEY", transport=transport)
        # The fixture's model id is valid for OpenRouter; Venice owns a namespaced id.
        if provider_type is VeniceProvider:
            from dataclasses import replace

            assembly = rt.assemblies["seed-decider"]
            model_id = "venice:vendor"
            assembly.spec = replace(assembly.spec, model_id=model_id)
            assembly.model.prices.prices[model_id] = TokenPrice(1, 1)
        from types import SimpleNamespace

        rt.provider = JournalProxy(SimpleNamespace(complete=provider.complete),
                                   rt.ledger, "provider")
        for assembly in rt.assemblies.values():
            if isinstance(assembly, Assembly):
                assembly.model.provider = rt.provider
        rt.ledger.active = True
        handles = [decision(rt) for _ in range(2)]
        return rt, handles

    def invoke_twice(rt, handles):
        for handle in handles:
            req = rt._request(handle, "Produce a return", {}, {}, 10**15, "verdict")
            ret = rt._invoke("seed-decider", req, "producer")
            assert ret.status == "failed"
            assert "provider_message" not in ret.provider
            assert "operator-only-marker" not in json.dumps(ret.outputs)

    rt, handles = runtime()
    boundary = len(rt.ledger._recovery_items())
    invoke_twice(rt, handles)
    rows = rt.ledger._recovery_items()
    failures = [row for row in rows if row["kind"] in ("io.result", "invocation")
                and row.get("http_status") == status]
    assert [row["kind"] for row in failures] == ["io.result", "invocation"] * 2
    expected = " ".join(body.replace(key, "[REDACTED]").split())[:500]
    assert all(row["provider_message"] == expected for row in failures)
    assert key not in json.dumps(rows)
    assert len(payloads) == 2
    assert "operator-only-marker" not in json.dumps(payloads)
    assert "provider_message" not in json.dumps(payloads)
    assert "operator-only-marker" not in json.dumps([
        row for row in rows if row["kind"] not in ("io.result", "invocation")])
    replay, replay_handles = runtime()
    replay.ledger.recovering = True
    replay.ledger.tail = rows[boundary:]
    invoke_twice(replay, replay_handles)
    # RecoveryJournal.append compares canonical bytes for every row, including diary.
    assert replay.ledger.peek() is None
    assert replay.ledger.failure is None
    assert len(payloads) == 2
