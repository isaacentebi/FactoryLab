"""A grounded finding cites references its commission supplied, and nothing else.

The paid consequence probe produced the failure these tests hold: a judge with a
real execution receipt beside the claim and the contract cited the receipt and
the two section names, and the whole finding was refused for the invented
strings. The request now states which strings are references.
"""

import json
from dataclasses import replace

import pytest

from factorylab.cortex.assembly import validate_schema
from factorylab.runtime.grounded import parse_finding
from factorylab.runtime.worlds import PromptSpec, load_manifest
from tests.runtime.test_grounded_feedback import _fix_payoff, _GroundedProvider
from tests.runtime.test_loop import (
    _consequence_judge,
    _consequence_produce,
    _consequence_runtime,
)

#: The six names the settling code freezes and this request passes through.
SNAPSHOT_FIELDS = ("as_of_tick", "event_cursor", "receipt_cursor", "observation_due_tick",
                   "assessment_timeout_tick", "scope")
INVENTED = "producer_claim"


class _Capturing(_GroundedProvider):
    """Keep the request and the inputs the final grounded evaluator was sent."""

    def _evaluate(self, req, inputs):
        if inputs.get("realized_consequence") is not None:
            self.__dict__.setdefault("sent", []).append(
                "\n".join(str(message.get("content", "")) for message in req.messages))
            self.__dict__["grounded_input"] = inputs["realized_consequence"]
        return super()._evaluate(req, inputs)


def _runtime(mode="reference", provider=None):
    manifest = load_manifest("scripted")
    manifest = replace(
        manifest,
        evaluation=replace(manifest.evaluation, producer_feedback="realized",
                           grounded_horizon_ticks=2, verdict_timeout_events=3),
        prompt=PromptSpec(mode=mode),
    )
    return _consequence_runtime(manifest=manifest, provider=provider)


def _commission(rt, *, amend=None):
    """Open, judge and mature one grounded contract, and return its final commission."""
    producer, event = _consequence_produce(rt, "seed-decider")
    _consequence_judge(rt, event, "eval-a")
    _fix_payoff(rt, producer, net=8_000, cost=5_000)
    rt.ticks_consumed = rt.grounded_pending[producer].due_tick
    rt._settle_due_grounded()
    commission = next(ev for ev in reversed(rt.internal)
                      if ev.payload.get("grounded_consequence"))
    if amend is not None:
        commission = replace(commission, payload=amend(dict(commission.payload)))
    return producer, commission


def _sent(provider):
    return provider.__dict__["sent"][-1]


def _evidence_contract(provider):
    """The evidence field of the answer schema the evaluator was actually sent."""
    text = _sent(provider)
    head = text.index("OUTCOME SCHEMA") + len("OUTCOME SCHEMA")
    schema, _ = json.JSONDecoder().raw_decode(text[head:].lstrip())
    return schema["properties"]["realized_consequence"]["properties"]["evidence"]


def _outcome_schema(provider):
    """Return the effective schema rendered in the evaluator's actual request."""
    text = _sent(provider)
    head = text.index("OUTCOME SCHEMA") + len("OUTCOME SCHEMA")
    schema, _ = json.JSONDecoder().raw_decode(text[head:].lstrip())
    return schema


def _ask(mode="reference", **commission):
    provider = _Capturing()
    rt = _runtime(mode, provider)
    _producer, event = _commission(rt, **commission)
    refs = [row["ref"] for row in event.payload["evidence"]]
    _consequence_judge(rt, event, "eval-b")
    return rt, provider, refs, event


def test_grounded_judge_cannot_acknowledge_an_inbox_omitted_from_its_request():
    provider = _Capturing()
    rt = _runtime(provider=provider)
    _producer, event = _commission(rt)
    rt.outcomes.append("eval-b", handle="unseen", outcome={"kind": "message"})
    ident = f"outcome:{rt.outcomes.items['eval-b'][-1]['seq']}"
    _consequence_judge(rt, event, "eval-b")
    rt.outcomes.ack_through("eval-b", ident)
    assert rt.outcomes.cursors.get("eval-b", 0) == 0


@pytest.mark.parametrize("mode", ["reference", "compact"])
def test_the_evidence_field_names_exactly_the_refs_this_commission_supplied(mode):
    _rt, provider, refs, _event = _ask(mode)
    contract = _evidence_contract(provider)

    assert refs and contract["items"]["enum"] == refs
    # Which strings are references is all this narrows: no length is imposed on
    # an answer that has something to cite.
    assert "maxItems" not in contract
    # The description says where a claim or a norm belongs, so the judge has
    # somewhere to put what it was citing the section names for.
    assert "reason" in contract["description"]
    # A finding citing the supplied receipt satisfies the stated contract; the
    # answer the paid probe sent does not.
    validate_schema(refs, contract)
    with pytest.raises(ValueError):
        validate_schema([refs[0], INVENTED], contract)


def test_a_repeated_known_reference_is_as_valid_as_it_was_before():
    _rt, provider, refs, _event = _ask()
    contract = _evidence_contract(provider)
    repeated = [refs[0], refs[0]]

    # Settlement reads a citation list as a set and drops the repeat, so a
    # contract that refused this would refuse an answer the world accepts.
    validate_schema(repeated, contract)
    status, score, cited, _reason = parse_finding(
        {"status": "supported", "score": 0.9, "evidence": repeated,
         "reason": "the receipt establishes the claimed effect"},
        set(refs), set(refs))
    assert (status, score, cited) == ("supported", 0.9, (refs[0],))


def test_both_prompt_modes_state_the_same_evidence_contract():
    _rt, full, refs, _event = _ask("reference")
    _rt2, compact, compact_refs, _other = _ask("compact")

    assert refs == compact_refs
    assert _evidence_contract(full) == _evidence_contract(compact)
    # A grounded commission carries its own frozen record instead of the world
    # block the two modes differ over, so this is one request either way: the
    # contract cannot drift between a full and a compact world.
    assert _sent(full) == _sent(compact)
    assert '"enum"' in _sent(compact)


def test_final_grounded_request_requires_the_finding_and_omits_dead_forecasts():
    _rt, provider, _refs, _event = _ask()
    schema = _outcome_schema(provider)

    assert "realized_consequence" in schema["required"]
    assert {"payoff", "forecasts"}.isdisjoint(schema["properties"])
    # The verdict remains: the final interpretation still enters the ordinary
    # recursive meta-evaluation path.
    assert "verdict" in schema["required"]


def test_zero_financial_result_alone_is_not_stated_as_a_contrary_ground():
    def zero_cost_only(payload):
        changed = dict(payload)
        contract = dict(changed["contract"])
        contract["producer_outputs"] = {
            "action": "defer", "defer": 2,
            "rationale": "wait for enough evidence before deciding",
        }
        changed["contract"] = contract
        evidence = []
        for row in changed["evidence"]:
            row = dict(row)
            if row.get("kind") == "EconomicOutcome":
                row["payload"] = {
                    **row["payload"], "earned_micro": 0, "net_micro": 0,
                    "cost_micro": 2_433,
                }
            evidence.append(row)
        changed["evidence"] = evidence
        return changed

    _rt, provider, _refs, _event = _ask(amend=zero_cost_only)
    grounded = provider.__dict__["grounded_input"]
    prompt = _sent(provider)

    assert grounded["producer_claim"]["action"] == "defer"
    assert "contradicts an applicable frozen norm or criterion" in prompt
    assert "does not require producer_claim to repeat that norm" in prompt
    assert "Zero earnings, zero net, or compute cost alone is not contrary" in prompt
    assert "When the evidence merely fails to show usefulness, answer unknown" in prompt
    assert "Do not return payoff or forecasts" in prompt


def test_a_commission_with_no_supplied_evidence_admits_the_empty_list_alone():
    _rt, provider, refs, _event = _ask(amend=lambda payload: {**payload, "evidence": []})
    contract = _evidence_contract(provider)

    # Nothing to cite is stated as a length of zero over ordinary strings, not
    # as a field with an empty set of allowed values.
    assert refs == [] and contract["maxItems"] == 0
    assert contract["items"] == {"type": "string"}
    validate_schema([], contract)
    with pytest.raises(ValueError):
        validate_schema(["execution:exec-anything"], contract)


def test_the_parser_still_refuses_an_invented_reference_the_contract_excludes():
    _rt, provider, refs, _event = _ask()
    contract = _evidence_contract(provider)
    finding = {"status": "supported", "score": 0.9, "evidence": [refs[0], INVENTED],
               "reason": "the receipt establishes the claimed execution"}

    # The failure this came from: one real reference, one section name, and the
    # whole finding refused. Nothing repairs the citation after the answer.
    with pytest.raises(ValueError):
        parse_finding(finding, set(refs), set(refs))
    with pytest.raises(ValueError):
        validate_schema(finding["evidence"], contract)
    # The same finding, citing only what the contract names, settles as before.
    status, score, cited, _reason = parse_finding(
        {**finding, "evidence": [refs[0]]}, set(refs), set(refs))
    assert (status, score, cited) == ("supported", 0.9, (refs[0],))


def test_the_observation_contract_carries_the_frozen_snapshot_not_the_current_clock():
    provider = _Capturing()
    rt = _runtime("compact", provider)
    _producer, event = _commission(rt)
    frozen = dict(event.payload["evidence_snapshot"])
    # Time moves between the reading and the assessment; the reading does not.
    rt.ticks_consumed += 9
    rt.clock.now_ns += 10 * 10**9
    _consequence_judge(rt, event, "eval-b")

    grounded = provider.__dict__["grounded_input"]
    # The settling code's own six fields, copied, with nothing added or renamed.
    assert set(grounded["evidence_snapshot"]) == set(SNAPSHOT_FIELDS)
    assert grounded["evidence_snapshot"] == frozen
    assert grounded["evidence_snapshot"]["as_of_tick"] != rt.ticks_consumed
    # The tick names are explained where they are used, without a second copy
    # of the values they explain.
    ticks = grounded["observation_contract_ticks"]
    assert "due_tick is when this observation first matures" in ticks
    assert "close_tick" in ticks and "not a deadline" in ticks
    assert "evidence_snapshot" in _sent(provider)
    assert str(frozen["scope"])[:40] in _sent(provider)


def test_a_commission_without_that_metadata_reports_it_unknown():
    _rt, provider, _refs, _event = _ask(
        "compact",
        amend=lambda payload: {key: value for key, value in payload.items()
                               if key != "evidence_snapshot"},
    )
    grounded = provider.__dict__["grounded_input"]

    # Absent metadata is null, never the runtime's current position dressed up
    # as the time the evidence was read.
    assert grounded["evidence_snapshot"] == dict.fromkeys(SNAPSHOT_FIELDS)
