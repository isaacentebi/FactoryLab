"""A funded Venice population survives loss of its OpenRouter endowment."""

from dataclasses import replace

from factorylab.runtime.loop import Runtime
from factorylab.runtime.worlds import load_manifest
from factorylab.world.market import MultiProvider
from factorylab.world.openrouter import OpenRouterError
from factorylab.world.scripted import ScriptedProvider, _inputs_from_prompt

ROSTER = "worlds/compute-continuity-roster.toml"


class CreditProvider:
    """Expose a controlled provider-credit loss without spending real account funds."""

    def __init__(self, name, calls, *, available=True, exhaust_after=None, race=False):
        self.name, self.calls = name, calls
        self.available, self.exhaust_after, self.race = available, exhaust_after, race
        self.completed = 0
        self.scripted = ScriptedProvider(register_at_calls=(), tool_at_calls=(),
                                         treasury_at_call=-1, router_add_at_call=-1)

    def balance_micro(self):
        return 1_000_000 if self.available else 0

    def complete(self, request):
        assert self.available, "an exhausted provider was dispatched"
        if self.race:
            self.available = False
            self.calls.append((self.name, "402", None))
            raise OpenRouterError(402, "credit exhausted")
        inputs = _inputs_from_prompt(str(request.messages[0]["content"]))
        self.calls.append((self.name, request.model_id, inputs.get("you")))
        result = replace(self.scripted.complete(request), cost_micro=1)
        self.completed += 1
        if self.completed == self.exhaust_after:
            self.available = False
        return result


def runtime(openrouter, venice, *, path=None, roster=ROSTER, events=12):
    m = load_manifest(roster)
    m = replace(m, exchange=replace(m.exchange, kind="fake"),
                treasury=replace(m.treasury, insolvency_events=3))
    provider = MultiProvider(openrouter=openrouter, venice=venice)
    return Runtime(m, events=events, seed=4, initial_balance_micro=None,
                   ledger_path=str(path) if path else None, drip=False,
                   router_gamma=.1, provider=provider)


def test_unseeded_venice_cannot_rescue_an_empty_openrouter_population():
    calls = []
    rt = runtime(CreditProvider("openrouter", calls, available=False),
                 CreditProvider("venice", calls), roster="worlds/testnet-10m-roster.toml")
    result = rt.run()
    assert result["terminated"]
    assert calls == []


def test_both_providers_empty_ends_without_dispatch_or_billing():
    calls = []
    rt = runtime(CreditProvider("openrouter", calls, available=False),
                 CreditProvider("venice", calls, available=False))
    result = rt.run()
    assert result["terminated"] and result["seal_key_released"]
    assert result["ledger_verify"] and result["wallet_conservation"]
    assert calls == []
