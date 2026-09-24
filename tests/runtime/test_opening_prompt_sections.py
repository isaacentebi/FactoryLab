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


def test_a_request_that_cannot_be_rendered_fails_as_the_assembly_fails_it(monkeypatch):
    # PR #143 review: the byte count rendered the request outside the invocation's
    # failure boundary, so a request no rendering could serialise raised out of
    # _invoke_compute and killed the runtime. (Before wave 7 the ledger row's own
    # section count raised the same way.) The call must fail as the assembly fails
    # it, and the prompt must be unmeasured, never counted as zero bytes.
    from factorylab.charter.charter import MetricCard
    from factorylab.charter.measurement import fresh_sample
    from factorylab.charter.windows import MetricWindow
    from factorylab.runtime.observations import observation_for

    rt = runtime()
    called = []
    monkeypatch.setattr(rt.provider.target, "complete", lambda request: called.append(1))
    req = request(rt, 1_000_000)
    # Mixed key types serialise unsorted, so the request accepts them, but not with
    # sorted keys, which is how every prompt section is rendered.
    req = replace(req, inputs={**req.inputs, "payload": {"a": 1, 2: 3}})
    ret = rt._invoke(SEAT, req, "producer")
    assert (ret.status, ret.outputs, ret.cost) == ("failed", {"reason": "TypeError"}, 0)
    assert ret.prompt_sections is None and not called
    row = next(r for r in rt.ledger._recovery_items()
               if r["kind"] == "invocation" and r["handle"] == req.handle)
    assert row["status"] == "failed" and row["sections"] is None
    # An invocation, failed, but no prompt: counted as the one and not the other.
    assert (rt.window.invocations, rt.window.prompts, rt.window.prompt_bytes) == (1, 0, 0)
    sample = rt.card_samples.returns[-1]
    assert sample["invoked"] and sample["prompt_bytes"] is None
    card = MetricCard("card", "n", "d", "u", MetricWindow("returns", 1, None),
                      "at most 50000", "prompt_bytes", "all")
    assert not fresh_sample(card, rt.card_samples, rt.window)
    assert observation_for("prompt_bytes").measure(rt.window) is None


def _reader(rt, ceiling):
    """A decision routed on a published return authored by another assembly."""
    req = request(rt, ceiling)
    rt.handle_to_assembly["authored-h"] = "author-seat"
    rt.decision_subjects[req.handle] = "authored-h"
    return req


def test_a_model_request_refused_over_its_ceiling_is_read_by_nobody(monkeypatch):
    # PR #143 review: a request the assembly refuses before calling the provider was
    # recorded as a downstream reading of the return it was commissioned on.
    rt = runtime()
    called = []
    monkeypatch.setattr(rt.provider.target, "complete", lambda request: called.append(1))
    req = _reader(rt, 1)
    ret = rt._invoke(SEAT, req, "evaluator")
    assert (ret.status, ret.outputs) == ("failed",
                                         {"reason": "ceiling exceeds request cost_ceiling"})
    assert rt.card_samples.readings == [] and rt.window.downstream_read_bytes == 0
    assert not called and not ret.delivered
    # Still an invocation, its prompt rendered and measured, its return's readings
    # metered: only the reading it never received is absent.
    assert ret.prompt_sections is not None
    assert (rt.window.invocations, rt.window.prompts, rt.window.read_measured) == (1, 1, 1)


def test_a_delivered_request_is_a_downstream_reading(monkeypatch):
    rt = runtime()
    req = _reader(rt, 1_000_000)
    drive(rt, monkeypatch, [{"action": "hold", "rationale": "done"}])
    ret = rt._invoke(SEAT, req, "evaluator")
    assert ret.status == "ok" and ret.delivered
    assert rt.card_samples.readings == [{
        "handle": "authored-h", "assembly": "author-seat", "role": "producer",
        "window": rt.window.index, "read_bytes": ret.prompt_sections["inputs"],
        "reading": True}]
    assert rt.window.downstream_read_bytes == ret.prompt_sections["inputs"]


def test_a_program_refused_over_its_price_is_read_by_nobody():
    # The program's flat price above the capped ceiling returns before stdin is built.
    from tests.cortex.test_cortex import TinyWallet
    from tests.cortex.test_programs import PRICE, program
    from tests.cortex.test_programs import req as program_request

    asm, wallet, _ = program()
    refused = asm.invoke(program_request(ceiling=PRICE - 1))
    assert refused.status == "failed" and not refused.delivered and wallet.log == []
    # Its reservation refused: nothing was run either.
    unaffordable, _, _ = program(TinyWallet(balance=PRICE - 1))
    assert not unaffordable.invoke(program_request()).delivered
    # And the runtime files no reading for a return that says it was not delivered.
    rt = runtime()
    req = _reader(rt, 1_000_000)
    rt._record_reading(req.handle, replace(refused, handle=req.handle,
                                            prompt_sections={"inputs": 900, "total": 1_000}))
    assert rt.card_samples.readings == [] and rt.window.downstream_read_bytes == 0


def test_a_program_the_jail_ran_was_delivered():
    from tests.cortex.test_jail import require_jail
    from tests.cortex.test_programs import program
    from tests.cortex.test_programs import req as program_request

    require_jail()
    asm, _, _ = program()
    ret = asm.invoke(program_request())
    assert ret.status == "ok" and ret.delivered


def test_a_ballot_no_assembly_answered_is_no_invocation_in_its_scope_facts(monkeypatch):
    # PR #143 review: the window never counts an assembly-unavailable ballot among its
    # invocations; the scope facts published to population code must not either, or
    # the scope's prompt bytes per invocation are diluted by a prompt nobody rendered.
    from types import SimpleNamespace

    from factorylab.charter.amendment import PredictedEffect
    from factorylab.charter.measurement import scope_facts

    rt = runtime()
    monkeypatch.setattr(rt.charter_book, "vote", lambda *_: None)
    monkeypatch.setattr(rt.charter_book, "abstain", lambda *_: None)
    monkeypatch.setattr(rt.charter_book, "tally", lambda *_: "failed")
    amendment = SimpleNamespace(id="gone-seat", proposed_prices=(), add=(), replace=(),
                                remove=(), tick_interval=None,
                                predicted_effect=PredictedEffect("cost_per_return",
                                                                 "decrease", 1))
    committee = SimpleNamespace(seats=[("seat1", SEAT), ("seat2", "retired-assembly")])
    rt._hold_vote(amendment, committee)
    assert rt.window.invocations == 1
    rows = rt.card_samples.returns
    rendered = [row for row in rows if row["prompt_bytes"] is not None]
    assert len(rows) == 2 and len(rendered) == 1
    facts = scope_facts([{"index": rt.window.index}], rows, [])
    assert facts["invocations"] == rt.window.invocations == 1
    assert facts["prompt_bytes"] / facts["invocations"] == rendered[0]["prompt_bytes"]
    assert [row.get("invoked") for row in rows if row["prompt_bytes"] is None] == [False]


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
