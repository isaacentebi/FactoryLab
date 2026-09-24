"""The ledgered prompt bytes are the opening prompt's, byte for byte (PR #143 review).

``ComputeMixin._invoke`` changes its request after the first call: the cover caps its
ceiling, a working state written in a tool round replaces ``your_state``, the niche
widens and restores the ceiling. Counting the sections at ledger time counted a
request nobody was sent. These drive a real invocation and compare the ledger row, the
window counters and the return sample with the request the assembly was handed first.
"""

import json
from dataclasses import replace

import pytest

from factorylab.kernel.queue import PropensityRecord
from factorylab.runtime.loop import Runtime
from factorylab.runtime.shared import CH_VERDICT
from factorylab.runtime.worlds import load_manifest
from factorylab.world.exchange import FakeExchange
from factorylab.world.models import ModelResponse
from factorylab.world.scripted import ScriptedProvider

SEAT = "seed-decider"


def runtime():
    manifest = load_manifest("scripted")
    return Runtime(manifest, events=0, seed=1, initial_balance_micro=None, ledger_path=None,
                   router_gamma=0.1, provider=ScriptedProvider(),
                   exchange=FakeExchange(coins=manifest.exchange.coins))


def request(rt, ceiling):
    handle = rt.queue.open(
        actor=SEAT, event_id="sections",
        propensity=PropensityRecord((SEAT,), (1.0,), SEAT, 0, SEAT, "test"),
        channel=CH_VERDICT, deadline_ns=10**15, parent_handle=None, cost_ceiling=ceiling,
    )
    rt.consequences.start(handle, 0)
    return replace(rt._request(handle, "Choose what to do.",
                               {"kind": "Tick", "payload": {}, "world": rt._world_block()},
                               {}, 10**15, CH_VERDICT), cost_ceiling=ceiling)


def drive(rt, monkeypatch, replies):
    """Answer each call with the next reply; keep each request the assembly was handed."""
    handed, sent = [], []
    assembly = rt.assemblies[SEAT]
    invoke = assembly.invoke

    def capture(req):
        handed.append(req)
        return invoke(req)

    def complete(model_request):
        sent.append(model_request.messages[-1]["content"])
        return ModelResponse(model_request.model_id,
                             json.dumps(replies[min(len(sent), len(replies)) - 1]), 1, 1, "stop")

    monkeypatch.setattr(assembly, "invoke", capture)
    monkeypatch.setattr(rt.provider.target, "complete", complete)
    return handed, sent


@pytest.mark.parametrize(("ceiling", "replies"), [
    # A working state written in a tool round: the continuation renders the new head.
    (1_000_000, [{"working_state": {"note": "x" * 3_000},
                  "tool_calls": [{"tool": "outcome.list", "args": {"after": 0, "limit": 8}}]},
                 {"action": "hold", "rationale": "done"}]),
    # One call whose ceiling the wallet caps below the seat's cover before it is
    # rendered: after the call the request is re-capped by the cover alone.
    (10**12, [{"action": "hold", "rationale": "done"}]),
])
def test_the_ledgered_sections_are_the_first_requests_the_assembly_was_handed(
        monkeypatch, ceiling, replies):
    rt = runtime()
    req = request(rt, ceiling)
    if len(replies) == 1:
        available = rt.wallet.available_for
        monkeypatch.setattr(rt.wallet, "available_for",
                            lambda handle, reason: min(available(handle, reason), 777_777))
    handed, sent = drive(rt, monkeypatch, replies)
    ret = rt._invoke(SEAT, req, "producer")
    assert len(handed) == len(replies)
    opening = replace(handed[0], inputs={**handed[0].inputs, "you": SEAT}).section_bytes()
    # The prompt the provider received is the one counted, to the byte.
    assert opening["total"] == len(sent[0].encode("utf-8"))
    row = next(r for r in rt.ledger._recovery_items()
               if r["kind"] == "invocation" and r["handle"] == req.handle)
    assert row["sections"] == opening
    assert ret.prompt_sections == opening
    assert (rt.window.prompt_bytes, rt.window.you_bytes, rt.window.inputs_bytes) == (
        opening["total"], opening["you"], opening["inputs"])
    sample = rt.card_samples.returns[-1]
    assert (sample["prompt_bytes"], sample["you_bytes"], sample["inputs_bytes"]) == (
        opening["total"], opening["you"], opening["inputs"])
    if len(handed) > 1:
        # The scenario did mutate the request: the state written between calls is in
        # the continuation and not in the opening prompt that was counted.
        assert "x" * 3_000 in sent[1] and "x" * 3_000 not in sent[0]
    else:
        # The request the caller built states a ceiling the call was never shown.
        assert handed[0].cost_ceiling < req.cost_ceiling
        assert opening != replace(req, inputs={**req.inputs, "you": SEAT}).section_bytes()
