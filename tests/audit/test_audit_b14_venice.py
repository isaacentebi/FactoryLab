"""Fix pass 1, B14: a completion the provider bills but cannot deliver is visible in the
diary, and the Venice request carries the documented switch that withholds reasoning.

Offline: a scripted provider stands in for Venice; the wire shape is checked against a
fake transport. No credit is spent.
"""

import json
from dataclasses import replace

from factorylab.kernel.ledger import Ledger
from factorylab.runtime.loop import ScriptedProvider, run_world
from factorylab.runtime.worlds import load_manifest
from factorylab.world.models import ModelRequest
from factorylab.world.venice import VeniceProvider


def _items(path, manifest):
    return Ledger.reopen(path, manifest=json.loads(manifest.canonical_json()))._recovery_items()


class HiddenReasoningProvider(ScriptedProvider):
    """The 12th producer reply is what the recorded Venice probe returned on an 8,662-token
    prompt: finish ``length``, ``reasoning_tokens == max_tokens``, no visible content."""

    def complete(self, req):
        response = super().complete(req)
        if self._producer_calls == 12 and not getattr(self, "_done", False):
            self._done = True
            return replace(
                response, text="", stop_reason="length", output_tokens=req.max_tokens,
                raw={"reasoning_tokens": req.max_tokens, "request_id": "chatcmpl-fault"},
            )
        return response


def test_invocation_records_reasoning_tokens_and_the_provider_fault(tmp_path):
    m = load_manifest("scripted")
    path = str(tmp_path / "w.jsonl")
    summary = run_world(m, events=40, seed=1, ledger_path=path,
                        provider=HiddenReasoningProvider())
    assert summary["stats"]["events"] >= 40
    items = _items(path, m)
    faults = [i for i in items if i["kind"] == "provider.fault"]
    assert len(faults) == 1
    fault = faults[0]
    assert fault["reasoning_tokens"] == fault["max_tokens"] > 0
    assert "hidden reasoning" in fault["reason"]
    invocation = next(i for i in items if i["kind"] == "invocation"
                      and i["handle"] == fault["handle"])
    assert invocation["status"] == "malformed" and invocation["finish_reason"] == "length"
    assert invocation["usage"]["reasoning_tokens"] == invocation["usage"]["max_tokens"]
    assert invocation["usage"]["output_tokens"] == invocation["usage"]["max_tokens"]
    # The bill stands: the vendor charged for those tokens and the wallet mirrors it.
    assert invocation["cost"] == fault["cost"] > 0
    # Every other invocation carries the provider's report too, with nothing invented.
    healthy = next(i for i in items if i["kind"] == "invocation" and i["status"] == "ok")
    assert healthy["finish_reason"] == "end_turn"
    assert healthy["usage"]["reasoning_tokens"] is None
    assert healthy["usage"]["input_tokens"] > 0 and healthy["usage"]["max_tokens"] > 0


def test_venice_disables_thinking_with_the_venice_level_toggle():
    """``reasoning.enabled: false`` is Venice's own switch (it withholds reasoning parameters
    from the upstream model). ``reasoning_effort: none`` is forwarded as an effort, may map
    to the model's minimum effort, and makes ``reasoning.enabled`` ignored, so it must not
    be sent alongside the toggle; the recorded probe sent it and got 800 hidden tokens."""
    calls = []

    def transport(method, path, payload):
        calls.append((method, path, payload))
        return {
            "id": "venice-test", "model": "test-flash",
            "choices": [{"message": {"content": "OK"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 8662, "completion_tokens": 3,
                      "completion_tokens_details": {"reasoning_tokens": 0}},
            "cost": {"usd": "0.0001"},
        }

    provider = VeniceProvider(
        transport=transport, reasoning_config={"venice:test-flash": {"enabled": False}},
    )
    req = ModelRequest("venice:test-flash", "System", ({"role": "user", "content": "x"},), 800)
    response = provider.complete(req)
    payload = calls[0][2]
    assert payload["reasoning"] == {"enabled": False}
    assert "reasoning_effort" not in payload
    assert payload["venice_parameters"]["disable_thinking"] is True
    assert payload["max_tokens"] == 800
    assert response.raw["thinking"] == "disabled" and response.raw["reasoning_tokens"] == 0
    # The population's ``@none`` suffix reaches the same switch.
    provider.complete(replace(req, model_id="venice:test-flash@none"))
    assert calls[1][2]["reasoning"] == {"enabled": False}
    assert "reasoning_effort" not in calls[1][2]
