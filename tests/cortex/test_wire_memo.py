"""The rendered contract an assembly keeps is the contract ``wire_schema`` renders.

``Assembly.build_model_request`` keeps each contract's wire by content. A kept wire
must be indistinguishable from a fresh render in every byte a provider or the
recovery journal reads, and a caller's copy must be its own.
"""

from factorylab.cortex.assembly import (
    Assembly,
    AssemblySpec,
    judging_contract,
    reserved_return_fields,
    wire_schema,
)
from factorylab.cortex.request import Request
from factorylab.kernel.ledger import canonical
from factorylab.runtime.resume import encode
from factorylab.world.metering import Meter, MeteredModel
from factorylab.world.models import FakeModel, PriceTable, TokenPrice

CONTRACTS = [
    {"type": "object", "properties": {"action": {"type": "string"}}, "required": ["action"]},
    {"type": "object", "properties": {**reserved_return_fields(max_children=3),
                                      "q": {"type": "number", "minimum": 0, "maximum": 1.0}},
     "required": ["q"]},
    judging_contract("Verdict", propensity={"type": "object"},
                     register={"type": "array", "items": {"type": "object"}},
                     forecasts={"type": "array"}),
    # Equal under ``==`` to the contract above it, but not the same JSON: never conflated.
    {"type": "object", "properties": {"q": {"type": "number", "maximum": 1}}},
    {"type": "object", "properties": {"q": {"type": "number", "maximum": True}}},
]


def _assembly(emits=("ProducerReturn",)) -> Assembly:
    prices = PriceTable()
    prices.register("m", TokenPrice(1, 1))
    return Assembly(AssemblySpec(id="seat", version=1, model_id="m", emits=list(emits)),
                    MeteredModel(FakeModel(), prices, Meter(None)), max_children=3)


def _request(contract, channel="fast") -> Request:
    return Request(handle="h1", description="d", inputs={}, capability_versions={},
                   outcome_schema=contract, deadline_ns=10**12, cost_ceiling=10**6,
                   parent_handle=None, completion_criterion="c", scoring_channel=channel,
                   resource_liability="self")


def _bytes(wire):
    return repr(wire), canonical(encode(wire))


def test_a_kept_wire_is_byte_for_byte_a_fresh_render():
    assembly = _assembly()
    for channel in ("fast", "policy"):
        for contract in CONTRACTS:
            fresh = wire_schema(contract, assembly.spec.emits, policy=channel == "policy",
                                max_children=3)
            for _ in range(3):  # a miss, then hits
                wire = assembly.build_model_request(_request(contract, channel)).response_schema
                assert _bytes(wire) == _bytes(fresh)


def test_a_callers_copy_is_its_own():
    assembly = _assembly()
    contract = CONTRACTS[1]
    first = assembly.build_model_request(_request(contract)).response_schema
    expected = _bytes(first)
    first["anyOf"].clear()
    first["planted"] = 1
    second = assembly.build_model_request(_request(contract)).response_schema
    assert _bytes(second) == expected
    second["anyOf"][0]["properties"].clear()
    assert _bytes(assembly.build_model_request(_request(contract)).response_schema) == expected


def test_a_contract_the_key_cannot_state_is_rendered_every_time():
    assembly = _assembly()
    # A tuple would come back from JSON as a list: such a contract is never kept.
    contract = {"type": "object", "properties": {"side": {"enum": ("buy", "sell")}}}
    wire = assembly.build_model_request(_request(contract)).response_schema
    assert _bytes(wire) == _bytes(wire_schema(contract, assembly.spec.emits, max_children=3))
    assert assembly.__dict__.get("_wire_memo") == {}


def test_a_changed_contract_is_rendered_anew():
    assembly = _assembly()
    contract = {"type": "object", "properties": {"action": {"type": "string"}}}
    assembly.build_model_request(_request(contract))
    contract["properties"]["size"] = {"type": "number"}
    contract["required"] = ["size"]
    wire = assembly.build_model_request(_request(contract)).response_schema
    assert _bytes(wire) == _bytes(wire_schema(contract, assembly.spec.emits, max_children=3))
