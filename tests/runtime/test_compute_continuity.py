"""A funded Venice population survives loss of its OpenRouter endowment."""

import hashlib
import json
from dataclasses import replace

from factorylab.runtime.loop import Runtime
from factorylab.runtime.worlds import load_manifest
from factorylab.world.market import MultiProvider
from factorylab.world.openrouter import OpenRouterError
from factorylab.world.scripted import ScriptedProvider, _inputs_from_prompt
from tests.conftest import make_runtime

ROSTER = "worlds/compute-continuity-roster.toml"


def test_invocation_records_bounded_effective_prefix_hashes(monkeypatch):
    rt = make_runtime()
    captured = []
    complete = rt.provider.target.complete

    def capture(request):
        captured.append(request)
        return complete(request)

    monkeypatch.setattr(rt.provider.target, "complete", capture)
    requests = [
        rt._request(
            f"cache-witness-{index}", f"Respond to supplied tick {index}.",
            {"kind": "Tick", "payload": {"index": index}, "world": rt._world_block()},
            {}, 10**15, "policy",
        )
        for index in (1, 2)
    ]
    for req in requests:
        rt._invoke("seed-decider", req, "producer")
    invocations = [row for row in rt.ledger._recovery_items()
                   if row["kind"] == "invocation"
                   and str(row["handle"]).startswith("cache-witness-")]
    assert len(invocations) == 2
    stable = replace(
        requests[0], inputs={**requests[0].inputs, "you": "seed-decider"}
    ).stable_prefix()
    leading = [
        {"role": "system", "content": captured[0].system},
        {"role": "user", "content": stable},
    ]
    encoded = json.dumps(leading, sort_keys=True, separators=(",", ":"),
                         ensure_ascii=False).encode("utf-8")
    expected = {
        "stable_prefix_sha256": hashlib.sha256(stable.encode()).hexdigest(),
        "effective_leading_messages_sha256": hashlib.sha256(encoded).hexdigest(),
    }
    assert all(invocation["prompt_cache"] == expected for invocation in invocations)
    assert all(len(value) == 64 for value in expected.values())
    assert "WORLD CONTRACT" not in json.dumps(
        [invocation["prompt_cache"] for invocation in invocations]
    )


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
                   ledger_path=str(path) if path else None, router_gamma=.1, provider=provider)


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
