"""The contract a provider's decoder receives (Chapter II §II.b: physics is enforced).

``wire_schema`` transports the kernel's own acceptance set: every reply shape the
kernel accepts is admitted, and a reply that leaves out what the contract requires,
without continuing or declining, is not.
"""

import copy
import json

import pytest

from factorylab.cortex.assembly import (
    Assembly,
    AssemblySpec,
    _validate_return,
    answer_kind,
    reserved_return_fields,
    validate_schema,
    wire_schema,
)
from factorylab.cortex.request import Request
from factorylab.world.metering import Meter, MeteredModel
from factorylab.world.models import FakeModel, PriceTable, TokenPrice

UNIT = {"type": "number", "minimum": 0, "maximum": 1}
ENVELOPE = {k: v for k, v in reserved_return_fields(max_children=3, max_tool_calls=4).items()
            if k in ("requests", "tool_calls", "status", "reason")}


def _shape(kind: str, fields: dict, required: list[str], polymorphic: bool) -> dict:
    return {"type": "object", "properties": {
        **ENVELOPE, **fields, "emits": {"enum": [kind]}, "about_handle": {"type": "string"}},
        "required": [*required, *(["emits"] if polymorphic else [])]}


FORECASTS = {"type": "array", "maxItems": 2, "items": {
    "type": "object", "properties": {
        "predicate": {"enum": ["wallet_up", "fill_within"]},
        "params": {"type": "object",
                   "properties": {"horizon_events": {"type": "integer", "minimum": 1}}},
        "q": UNIT},
    "required": ["predicate", "params", "q"]}}
VERDICT = _shape("Verdict", {"verdict": UNIT, "payoff": UNIT, "rationale": {"type": "string"},
                             "forecasts": FORECASTS},
                 ["verdict", "payoff", "rationale", "forecasts"], False)
POLY = {"anyOf": [
    _shape("ProducerReturn", {"action": {"type": "string"}}, ["action"], True),
    _shape("Note", {"body": {"type": "string"}}, ["body"], True)]}
FINAL = {"verdict": 0.4, "payoff": 0.6, "rationale": "r",
         "forecasts": [{"predicate": "wallet_up", "params": {"horizon_events": 5}, "q": 0.3}]}
CALL = {"tool": "world.read", "args": {"section": "prices", "any": {"nested": [1]}}}
CHILD = {"target": "judge", "description": "d", "inputs": {"x": 1},
         "outcome_schema": {"type": "object"}}

ACCEPTED = [
    (VERDICT, "Verdict", FINAL),
    (VERDICT, "Verdict", {**FINAL, "working_state": {"lens": "x"}, "ack_through": "h-3",
                          "propensity": {"a": 1.0}}),
    (VERDICT, "Verdict", {"tool_calls": [CALL]}),
    (VERDICT, "Verdict", {"tool_calls": [CALL], "verdict": 0.2}),
    (VERDICT, "Verdict", {"requests": [CHILD]}),
    (VERDICT, "Verdict", {"status": "cannot", "reason": "out of scope"}),
    (POLY, "ProducerReturn", {"emits": "ProducerReturn", "action": "hold"}),
    (POLY, "Note", {"emits": "Note", "body": "b"}),
    (POLY, None, {"tool_calls": [CALL]}),
    (POLY, None, {"status": "cannot", "reason": "no"}),
]

REJECTED = [
    (VERDICT, "Verdict", {k: v for k, v in FINAL.items() if k != "payoff"}),
    (VERDICT, "Verdict", {k: v for k, v in FINAL.items() if k != "verdict"}),
    (VERDICT, "Verdict", {**FINAL, "verdict": 1.5}),
    (VERDICT, "Verdict", {"tool_calls": [], "verdict": 0.2}),
    (VERDICT, "Verdict", {"status": "cannot"}),
    (VERDICT, "Verdict", {"status": "pending", "reason": "later"}),
    (POLY, None, {"emits": "ProducerReturn"}),
    (POLY, None, {"action": "hold"}),
]


def _emits(contract):
    return ("Verdict",) if contract is VERDICT else ("ProducerReturn", "Note")


@pytest.mark.parametrize("contract, kind, reply", ACCEPTED)
def test_every_reply_the_kernel_accepts_is_admitted_on_the_wire(contract, kind, reply):
    _validate_return(reply, contract, kind)
    validate_schema(reply, wire_schema(contract, _emits(contract)))


@pytest.mark.parametrize("contract, kind, reply", REJECTED)
def test_a_reply_that_omits_what_the_contract_requires_is_refused_on_the_wire(
        contract, kind, reply):
    with pytest.raises(ValueError):
        _validate_return(reply, contract, kind)
    with pytest.raises(ValueError):
        validate_schema(reply, wire_schema(contract, _emits(contract)))


def test_every_object_the_contract_leaves_open_is_stated_open():
    """A decoder whose default closes objects cannot forbid working_state or tool args."""
    def objects(node):
        if isinstance(node, dict):
            if node.get("type") == "object" or "properties" in node:
                yield node
            for value in node.values():
                yield from objects(value)
        elif isinstance(node, list):
            for value in node:
                yield from objects(value)

    wire = wire_schema(VERDICT, ("Verdict",))
    assert wire["type"] == "object"
    found = list(objects(wire))
    assert found and all(o.get("additionalProperties") is True for o in found)
    closed = {**VERDICT, "additionalProperties": False}
    assert all(form["additionalProperties"] is False
               for form in wire_schema(closed)["anyOf"])


def test_the_contract_is_never_mutated():
    before = copy.deepcopy(POLY)
    wire = wire_schema(POLY)
    assert POLY == before
    wire["anyOf"][0]["properties"]["action"]["type"] = "integer"
    assert POLY == before


def test_a_contract_that_forbids_continuing_or_declining_is_carried_as_it_is():
    fields = {**ENVELOPE, "tool_calls": {**ENVELOPE["tool_calls"], "maxItems": 0},
              "status": {"enum": ["done"]}, "action": {"type": "string"}}
    contract = {"type": "object", "properties": fields, "required": ["action"]}
    for reply in ({"tool_calls": [CALL]}, {"status": "cannot", "reason": "no"}):
        with pytest.raises(ValueError):
            _validate_return(reply, contract, None)
        with pytest.raises(ValueError):
            validate_schema(reply, wire_schema(contract))
    validate_schema({"requests": [CHILD]}, wire_schema(contract))


def test_a_contract_silent_on_the_envelope_still_carries_its_shape():
    """A continuation list the contract does not name keeps the envelope's item shape."""
    contract = {"type": "object", "properties": {"action": {"type": "string"}},
                "required": ["action"]}
    wire = wire_schema(contract)
    for reply in ({"tool_calls": [0]}, {"tool_calls": [{"tool": "world.read"}]},
                  {"requests": ["judge"]}, {"action": "hold", "tool_calls": [0]}):
        with pytest.raises(ValueError):
            validate_schema(reply, wire)
    for reply in ({"tool_calls": [CALL]}, {"requests": [CHILD]}, {"action": "hold"},
                  {"action": "hold", "tool_calls": [CALL]}):
        _validate_return(reply, contract, None)
        validate_schema(reply, wire)


def test_a_value_that_is_not_a_schema_carries_no_wire_schema():
    assert wire_schema(None) is None and wire_schema([]) is None


def test_the_model_request_carries_the_contract_and_the_prompt_still_prints_it():
    contract = {"type": "object", "properties": {"action": {"type": "string"}},
                "required": ["action"]}
    prices = PriceTable()
    prices.register("m", TokenPrice(1, 1))
    assembly = Assembly(AssemblySpec(id="seat", version=1, model_id="m"),
                        MeteredModel(FakeModel(), prices, Meter(None)))
    req = Request(handle="h1", description="d", inputs={}, capability_versions={},
                  outcome_schema=contract, deadline_ns=10**12, cost_ceiling=10**6,
                  parent_handle=None, completion_criterion="c", scoring_channel="fast",
                  resource_liability="self")
    mreq = assembly.build_model_request(req)
    assert mreq.json_object is True
    assert mreq.response_schema == wire_schema(contract, assembly.spec.emits)
    # Schematics are public (§I.b): the prompt's outcome_schema section is unchanged.
    printed = json.dumps(contract, sort_keys=True, separators=(",", ":"))
    assert f"OUTCOME SCHEMA\n{printed}" in mreq.messages[-1]["content"]


CLOSED = {"type": "object", "properties": {"action": {"type": "string"}},
          "required": ["action"], "additionalProperties": False}


def test_a_closed_contract_without_continuation_lists_gets_no_continuation_form():
    """A closed contract names every field a reply may carry: the kernel refuses a
    tool_calls it does not name, so the wire has no continuation and no such field."""
    wire = wire_schema(CLOSED, ("ProducerReturn",))
    assert all("tool_calls" not in form["properties"] and "requests" not in form["properties"]
               for form in wire["anyOf"])
    assert all(form["required"] == ["action"] for form in wire["anyOf"])  # no refusal either
    for reply in ({"tool_calls": [CALL]}, {"action": "hold", "tool_calls": [CALL]},
                  {"requests": [CHILD]}, {"status": "cannot", "reason": "no"}):
        with pytest.raises(ValueError):
            _validate_return(reply, CLOSED, "ProducerReturn")
        with pytest.raises(ValueError):
            validate_schema(reply, wire)
    _validate_return({"action": "hold"}, CLOSED, "ProducerReturn")
    validate_schema({"action": "hold"}, wire)
    named = {**CLOSED, "properties": {**CLOSED["properties"], **ENVELOPE}}
    validate_schema({"tool_calls": [CALL]}, wire_schema(named, ("ProducerReturn",)))
    _validate_return({"tool_calls": [CALL]}, named, "ProducerReturn")


def test_custom_items_meet_the_envelope_rather_than_replace_it():
    """A contract's own tool_calls items that omit args still require args on the wire,
    because the kernel checks the envelope beside the contract."""
    custom = {"type": "object", "properties": {
        "action": {"type": "string"},
        "tool_calls": {"type": "array", "maxItems": 2, "items": {
            "type": "object", "properties": {"tool": {"enum": ["world.read"]}},
            "required": ["tool"]}}},
        "required": ["action"]}
    wire = wire_schema(custom, ("ProducerReturn",))
    for reply in ({"tool_calls": [{"tool": "world.read"}]},
                  {"tool_calls": [{"tool": "other", "args": {}}]},
                  {"tool_calls": [CALL, CALL, CALL]}):
        with pytest.raises(ValueError):
            validate_schema(reply, wire)
        with pytest.raises(ValueError):
            _validate_return(reply, custom, "ProducerReturn")
    validate_schema({"tool_calls": [CALL]}, wire)
    _validate_return({"tool_calls": [CALL]}, custom, "ProducerReturn")
    items = wire["anyOf"][1]["properties"]["tool_calls"]["items"]
    assert items["required"] == ["tool", "args"] or set(items["required"]) == {"tool", "args"}
    assert items["properties"]["tool"] == {"type": "string", "enum": ["world.read"]}


def test_a_contract_that_contradicts_the_envelope_sends_no_form_rather_than_a_looser_one():
    contradicts = {"type": "object", "properties": {"status": {"type": "number"},
                                                    "action": {"type": "string"}},
                   "required": ["action", "status"]}
    assert wire_schema(contradicts, ("ProducerReturn",)) is None


# --- the property: wire-valid implies kernel-valid, over real and synthetic contracts


def _edition6_contracts():
    from dataclasses import replace

    from factorylab.runtime.loop import Runtime
    from factorylab.runtime.worlds import load_manifest
    from factorylab.world.exchange import FakeExchange
    from factorylab.world.scripted import ScriptedProvider

    manifest = load_manifest("edition6-testnet-rehearsal")
    # Resolved allowances stand in for the provider's own: no catalogue is read.
    manifest = replace(manifest, assemblies=tuple(
        replace(a, max_tokens=1024) for a in manifest.assemblies))
    rt = Runtime(manifest, events=0, seed=1, initial_balance_micro=None, ledger_path=None,
                 router_gamma=0.1, provider=ScriptedProvider(),
                 exchange=FakeExchange(coins=manifest.exchange.coins))
    return [(rt._contract_schema(seat), asm.spec.emits)
            for seat, asm in sorted(rt.assemblies.items())]


BASES = {"Verdict": FINAL, "MetaVerdict": {"conformity": 0.5, "rationale": "r"},
         "CounterVerdict": {"verdict": 0.5, "rationale": "r"},
         "ProducerReturn": {"action": "hold"}, "Exposure": {"action": "hold"},
         "Note": {"emits": "Note", "body": "b"}}
MUTATIONS = [
    {"tool_calls": [CALL]}, {"tool_calls": [0]}, {"tool_calls": []},
    {"tool_calls": [{"tool": "t"}]}, {"tool_calls": "x"}, {"tool_calls": [CALL] * 5},
    {"requests": [CHILD]}, {"requests": ["j"]}, {"requests": [{"target": "j"}]},
    {"status": "cannot", "reason": "no"}, {"status": "cannot"}, {"status": "done"},
    {"status": 5}, {"reason": 5}, {"emits": "Verdict"}, {"emits": "ProducerReturn"},
    {"emits": "Other"}, {"emits": 5}, {"about_handle": "h"}, {"about_handle": 3},
    {"working_state": {"a": 1}}, {"working_state": "x"}, {"ack_through": "h"},
    {"ack_through": 3}, {"propensity": {"a": 1.0}}, {"propensity": 5},
    {"register": [{"kind": "tool"}]}, {"register": [{"kind": "nope"}]}, {"register": "x"},
    {"coin": 5}, {"side": 3}, {"rationale": 4}, {"zzz": 1}, {"verdict": 1.5},
    {"verdict": "x"}, {"payoff": -1}, {"conformity": 2}, {"action": 3}, {"body": 5},
    {"forecasts": [{"predicate": "nope", "params": {}, "q": 0.1}]},
    {"forecasts": [{"predicate": "wallet_up", "params": {"horizon_events": 5}, "q": 2}]},
    {"forecasts": []},
]
DROPS = [None, "verdict", "payoff", "rationale", "forecasts", "conformity", "action",
         "emits", "body"]


def _replies(kinds):
    bases = [{}] + [BASES[k] for k in kinds if k in BASES]
    for base in bases:
        for drop in DROPS:
            trimmed = {k: v for k, v in base.items() if k != drop}
            yield trimmed
            for i, first in enumerate(MUTATIONS):
                yield {**trimmed, **first}
                for second in MUTATIONS[i + 1::11]:
                    yield {**trimmed, **first, **second}


def _kernel(reply, contract, emits):
    """The kernel's verdict and whether it read the reply exactly as written."""
    from factorylab.cortex.assembly import answer_kind, validate_return_sections

    try:
        parsed, dropped = validate_return_sections(
            reply, contract, None, None, kind=answer_kind(emits, reply))
    except ValueError:
        return False, False
    return True, not dropped and parsed == reply


SYNTHETIC = [
    # Requires nothing: a reply of only invalid optional sections is refused.
    ({"type": "object"}, ("ProducerReturn",)),
    (VERDICT, ("Verdict",)),
    (POLY, ("ProducerReturn", "Note")),
    (CLOSED, ("ProducerReturn",)),
    ({**CLOSED, "properties": {**CLOSED["properties"], **ENVELOPE}}, ("ProducerReturn",)),
    ({"type": "object", "properties": {"body": {"type": "string"}}, "required": ["body"]},
     ("ProducerReturn", "Verdict")),
    ({"type": "object", "properties": {"action": {"type": "string"}, "tool_calls": {
        "type": "array", "items": {"type": "object", "properties": {
            "tool": {"enum": ["world.read"]}}, "required": ["tool"]}}},
      "required": ["action"]}, ("ProducerReturn",)),
]


def test_wire_valid_replies_are_kernel_valid_and_plain_kernel_valid_ones_are_wire_valid():
    """Soundness, and completeness up to the habits the kernel forgives.

    Every sample reply the wire admits passes the kernel. Every one the kernel reads
    exactly as written (no null dropped, no reason read as rationale, no invalid
    optional section dropped) is admitted by the wire. The samples stay clear of
    what the wire cannot state: an answer order, a child's semantic checks, and the
    runtime's validator (see ``wire_schema``).
    """
    checked = admitted = 0
    for contract, emits in [*_edition6_contracts(), *SYNTHETIC]:
        wire = wire_schema(contract, emits)
        assert wire is not None
        for reply in _replies(emits):
            try:
                validate_schema(reply, wire)
                on_wire = True
            except ValueError:
                on_wire = False
            accepted, plain = _kernel(reply, contract, emits)
            if len(emits) > 1 and answer_kind(emits, reply) is None:
                # A seat emitting several kinds, answering a shape that pins none of
                # them without naming one: the wire holds it to all of their fields,
                # the kernel to none. Stricter, never looser.
                plain = False
            assert accepted or not on_wire, (emits, reply)
            assert on_wire or not plain, (emits, reply)
            checked += 1
            admitted += on_wire
    assert checked > 10_000 and admitted > 1_000, (checked, admitted)
