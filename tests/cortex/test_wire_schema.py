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


@pytest.mark.parametrize("contract, kind, reply", ACCEPTED)
def test_every_reply_the_kernel_accepts_is_admitted_on_the_wire(contract, kind, reply):
    _validate_return(reply, contract, kind)
    validate_schema(reply, wire_schema(contract))


@pytest.mark.parametrize("contract, kind, reply", REJECTED)
def test_a_reply_that_omits_what_the_contract_requires_is_refused_on_the_wire(
        contract, kind, reply):
    with pytest.raises(ValueError):
        _validate_return(reply, contract, kind)
    with pytest.raises(ValueError):
        validate_schema(reply, wire_schema(contract))


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

    wire = wire_schema(VERDICT)
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
    assert mreq.response_schema == wire_schema(contract)
    # Schematics are public (§I.b): the prompt's outcome_schema section is unchanged.
    printed = json.dumps(contract, sort_keys=True, separators=(",", ":"))
    assert f"OUTCOME SCHEMA\n{printed}" in mreq.messages[-1]["content"]
