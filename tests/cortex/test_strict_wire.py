"""A strict route's decoder gets the contract it can compile; the kernel still enforces all of it.

Chapter II §II.b: physics is enforced, not announced. A ``json_schema_strict`` route
hands the host's decoder ``strict_schema`` of the wire (``wire_schema``): the same
contract with every keyword strict decoders refuse removed, which only ever admits
more. The published OUTCOME SCHEMA is unchanged, and the kernel validates each reply
against the full contract exactly as before, so a reply only the strict wire admits
is still refused.
"""

from __future__ import annotations

import copy
import json
from decimal import Decimal

import pytest

from factorylab.cortex.assembly import (
    FIELD_NAME_PATTERN,
    Assembly,
    AssemblySpec,
    validate_schema,
    wire_schema,
)
from factorylab.cortex.request import Request
from factorylab.world.metering import Meter, MeteredModel
from factorylab.world.models import PriceTable, TokenPrice
from factorylab.world.openai_wire import STRICT_KEYWORDS, strict_schema
from factorylab.world.openrouter import OpenRouterProvider
from tests.cortex.test_cortex import TinyWallet
from tests.cortex.test_wire_schema import (
    CHILD,
    SYNTHETIC,
    VERDICT,
    _edition6_contracts,
    _replies,
)

#: Keywords a host refused with HTTP 400, or a strict decoder cannot compile.
REFUSED = frozenset({"propertyNames", "patternProperties", "dependentRequired",
                     "minProperties", "maxProperties", "pattern"})


def _keywords(schema, *, in_definition=False):
    """Every keyword at every schema position, with whether it sits in a definition."""
    if not isinstance(schema, dict):
        return
    for key, value in schema.items():
        yield key, in_definition
        if key in ("properties", "$defs", "dependentSchemas", "patternProperties") \
                and isinstance(value, dict):
            for sub in value.values():
                yield from _keywords(sub, in_definition=in_definition or key == "$defs")
        elif key in ("anyOf", "oneOf", "allOf") and isinstance(value, list):
            for sub in value:
                yield from _keywords(sub, in_definition=in_definition)
        elif key in ("items", "additionalProperties", "propertyNames", "not") \
                and isinstance(value, dict):
            yield from _keywords(value, in_definition=in_definition)


def admitted(reply, schema):
    try:
        validate_schema(reply, schema)
    except ValueError:
        return False
    return True


def test_the_strict_wire_names_no_keyword_outside_the_strict_set():
    """Every edition-6 seat's wire: the full one carries what hosts refused, the strict
    one none of it, and no definition in it is recursive."""
    declining, seats = 0, _edition6_contracts()
    for contract, emits in [*seats, *SYNTHETIC]:
        wire = wire_schema(contract, emits, max_children=3)
        strict = strict_schema(wire)
        assert REFUSED & {k for k, _ in _keywords(wire)}, emits
        found = list(_keywords(strict))
        assert {k for k, _ in found} <= STRICT_KEYWORDS, emits
        assert not REFUSED & {k for k, _ in found}
        assert not any(k == "$ref" for k, inside in found if inside), emits
        # What the kernel alone now enforces: a decline whose field name is no identifier
        # (every seat's contract is open, so its refusal form admits other fields).
        decline = {"status": "cannot", "bad key": 1}
        if (contract, emits) in seats:
            assert admitted(decline, strict) and not admitted(decline, wire), emits
            declining += 1
        # Field names are data, not keywords: they stay as they are.
        assert strict["anyOf"][0]["properties"].keys() == wire["anyOf"][0]["properties"].keys()
    assert declining >= len(seats)


@pytest.mark.parametrize("part", [0, 1])
def test_the_strict_wire_admits_every_reply_the_wire_admits(part):
    """The transform only widens the decoder's set: every sample reply the full wire
    admits, the strict wire admits. What it admits beyond the full wire the kernel
    still decides, since it never reads the route (see the invocations below). The
    contracts are checked in two alternating halves, each inside the check tier's time."""
    checked = 0
    for contract, emits in [*_edition6_contracts(), *SYNTHETIC][part::2]:
        wire = wire_schema(contract, emits, max_children=3)
        strict = strict_schema(wire)
        for reply in _replies(emits):
            on_wire, on_strict = admitted(reply, wire), admitted(reply, strict)
            assert on_strict or not on_wire, (emits, reply)
            checked += 1
    assert checked > 5_000, checked


PROPENSITY_WITHOUT_CHOICE = {**CHILD, "propensity": {"a": 1.0}}
EMPTY_PROPENSITY = {**CHILD, "propensity": {}, "chosen": "a"}
FINAL = {"verdict": 0.4, "payoff": 0.6, "rationale": "r",
         "forecasts": [{"predicate": "wallet_up", "params": {"horizon_events": 5}, "q": 0.3}]}
#: Replies the kernel refused before this route existed, that the strict wire admits.
STRICT_ONLY = [
    {**FINAL, "rationale ": {}},           # MiMo, live-20260928: a field name with a space
    {**FINAL, "forecasts**: []": 1},        # Granite, 2026-09-29 probe under strict decoding
    {"requests": [PROPENSITY_WITHOUT_CHOICE]},  # dependentRequired
    {"requests": [EMPTY_PROPENSITY]},           # minProperties
]


def _completion(text):
    return {"id": "gen-1", "model": "test/flash",
            "choices": [{"message": {"role": "assistant", "content": text},
                         "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "cost": Decimal("0.00001")}}


class _Transport:
    def __init__(self, text):
        self.text, self.calls = text, []

    def __call__(self, method, path, payload):
        self.calls.append(payload)
        return _completion(self.text)


def _invoke(text, *, strict=True):
    """One seat on a json_schema_strict route (or the default one) answering ``text``."""
    prices = PriceTable()
    prices.register("test/flash", TokenPrice(1, 1))
    transport = _Transport(text)
    provider = OpenRouterProvider(transport=transport,
                                  strict_models=["test/flash"] if strict else ())
    assembly = Assembly(AssemblySpec(id="judge", version=1, model_id="test/flash",
                                     role="evaluator", emits=("Verdict",)),
                        MeteredModel(provider, prices, Meter(TinyWallet(10**9))),
                        max_children=3)
    req = Request(handle="h1", description="d", inputs={}, capability_versions={},
                  outcome_schema=VERDICT, deadline_ns=10**12, cost_ceiling=10**9,
                  parent_handle=None, completion_criterion="c", scoring_channel="fast",
                  resource_liability="self")
    return assembly.build_model_request(req), assembly.invoke(req), transport.calls[0]


@pytest.mark.parametrize("reply", STRICT_ONLY)
def test_a_strict_route_sends_the_strict_wire_and_the_kernel_still_refuses_what_it_refused(
        reply):
    mreq, ret, payload = _invoke(json.dumps(reply))
    assert payload["response_format"] == {"type": "json_schema", "json_schema": {
        "name": "outcome", "strict": True, "schema": strict_schema(mreq.response_schema)}}
    assert payload["provider"] == {"require_parameters": True}
    # The decoder could have produced it; the full contract refuses it.
    assert admitted(reply, strict_schema(mreq.response_schema))
    assert not admitted(reply, mreq.response_schema)
    assert ret.status == "malformed"
    assert ret.outputs["raw"] == json.dumps(reply)


def test_a_strict_route_changes_no_byte_the_seat_reads_and_accepts_what_it_accepted():
    printed = json.dumps(VERDICT, sort_keys=True, separators=(",", ":"))
    strict_req, strict_ret, strict_payload = _invoke(json.dumps(FINAL))
    plain_req, plain_ret, plain_payload = _invoke(json.dumps(FINAL), strict=False)
    assert strict_ret.status == plain_ret.status == "ok"
    assert strict_ret.outputs == plain_ret.outputs
    # The same request, the same messages on the wire, the same published schema.
    assert strict_req == plain_req
    assert strict_payload["messages"] == plain_payload["messages"]
    assert f"OUTCOME SCHEMA\n{printed}" in strict_payload["messages"][-1]["content"]
    assert plain_payload["response_format"] == {"type": "json_object"}
    assert strict_req.response_schema == wire_schema(VERDICT, ("Verdict",), max_children=3)


def test_strict_schema_removes_keywords_and_nothing_else():
    schema = {
        "type": "object", "propertyNames": {"pattern": FIELD_NAME_PATTERN},
        "minProperties": 1, "maxProperties": 4, "dependentRequired": {"a": ["b"]},
        "required": ["pattern"], "additionalProperties": True,
        "properties": {
            # Field names that are also keyword names stay: they are data.
            "pattern": {"type": "string", "pattern": "^x", "minLength": 1},
            "enum": {"type": "array", "items": [{"type": "string"}], "maxItems": 2},
            "a": {"enum": ["propertyNames", 1]},
            "b": {"$ref": "#/$defs/node"},
        },
        "$defs": {"node": {"type": "object", "title": "t",
                           "properties": {"next": {"$ref": "#/$defs/node"}}}},
        "anyOf": [{"required": ["a"]}, {"not": {"required": ["b"]}}],
    }
    before = copy.deepcopy(schema)
    strict = strict_schema(schema)
    assert schema == before
    assert strict == {
        "type": "object", "required": ["pattern"], "additionalProperties": True,
        "properties": {
            "pattern": {"type": "string"},
            "enum": {"type": "array", "maxItems": 2},
            "a": {"enum": ["propertyNames", 1]},
            "b": {"$ref": "#/$defs/node"},
        },
        # The recursive reference inside the definition admits anything.
        "$defs": {"node": {"type": "object", "properties": {"next": {}}}},
        "anyOf": [{"required": ["a"]}, {}],
    }
    other = strict_schema(schema)
    other["properties"]["a"]["enum"].append("x")
    other["$defs"]["node"]["properties"]["next"]["type"] = "string"
    assert schema == before and strict_schema(schema) == strict
    assert strict_schema(True) is True and strict_schema(None) is None
    # Closed around its patterns: without them it opens rather than closing further.
    patterned = {"type": "object", "patternProperties": {"^x_": {"type": "number"}},
                 "additionalProperties": False}
    assert strict_schema(patterned) == {"type": "object"}
    validate_schema({"x_a": 1}, strict_schema(patterned))
    # Removing assertions only ever widens: each value fails the schema on one removed
    # keyword (a field name, a dependent field, the recursion's depth, tuple items).
    for value in ({"pattern": "x", "a": 1, "b": {"next": {}}, "bad key": 0},
                  {"pattern": "x", "a": 1},
                  {"pattern": "x", "a": 1, "b": {"next": {"next": 5}}},
                  {"pattern": "x", "a": 1, "b": {}, "enum": ["x"]}):
        with pytest.raises(ValueError):
            validate_schema(value, schema)
        validate_schema(value, strict)
