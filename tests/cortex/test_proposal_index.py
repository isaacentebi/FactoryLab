"""The capability index and the register enum name the same proposal kinds.

The prompt's BASE CAPABILITIES index lists what a seat may propose. A kind it
lists that a return's register enum refuses voids the whole return, order and
all; a kind the enum accepts that the index never shows is unreachable. Both
come from ``PROPOSAL_SHAPES`` now, and ``program`` registers as the assembly it
abbreviates.
"""

import json

from factorylab.cortex.assembly import Assembly, AssemblySpec
from factorylab.cortex.registration import parse_proposals
from factorylab.cortex.request import Request
from factorylab.kernel.ledger import Ledger
from factorylab.kernel.wallet import Wallet
from factorylab.world.metering import Meter, MeteredModel
from factorylab.world.models import FakeModel, PriceTable, TokenPrice
from tests.conftest import make_runtime

ORDER = {"action": "order", "coin": "BTC", "side": "buy", "size": "0.001"}


def invoke(body, schema):
    wallet = Wallet(100000, Ledger())
    provider = FakeModel(default=json.dumps(body), fixed_input_tokens=1, fixed_output_tokens=1)
    assembly = Assembly(AssemblySpec("a", 1, "v", max_tokens=16),
                        MeteredModel(provider, PriceTable({"v": TokenPrice(1, 1)}), Meter(wallet)))
    req = Request("h", "test", {}, {}, schema, 100, 100000, None, "JSON", "test", "h")
    return assembly.invoke(req)


def register(item):
    return parse_proposals({"register": [item]}, event_kinds=frozenset({"Tick"}),
                           known_models=frozenset(), known_assemblies=frozenset(),
                           tool_jail=True)


def indexed_kinds(rt):
    return {row["kind"] for row in rt._capability_index()["proposals"]}


def test_every_proposal_kind_the_index_names_is_one_a_return_may_register():
    rt = make_runtime()
    enum = {form["properties"]["kind"]["enum"][0]
            for form in rt._register_schema()["items"]["anyOf"]}
    assert indexed_kinds(rt) == enum
    assert {"program", "predicate"} <= enum


def test_every_indexed_proposal_shape_survives_return_validation():
    rt = make_runtime()
    schema = {"type": "object", "properties": {"register": rt._register_schema()}}
    shapes = rt._proposal_shape_search("")
    assert set(shapes) == indexed_kinds(rt)
    for kind, shape in sorted(shapes.items()):
        ret = invoke({**ORDER, "register": [shape]}, schema)
        assert ret.status == "ok", (kind, ret.outputs)
        assert ret.outputs["register"] == [shape]
        assert {k: ret.outputs[k] for k in ORDER} == ORDER
        if kind == "amendment":
            continue  # the charter book admits amendments; registration never sees one
        # Registration answers for it on its own terms, never as an unknown kind.
        _, rejected = register(shape)
        assert not any(r.reason == "unknown proposal kind" for r in rejected), kind


def test_a_program_proposal_keeps_the_order_beside_it_and_registers_a_program_seat():
    rt = make_runtime()
    schema = {"type": "object", "properties": {"register": rt._register_schema()}}
    program = {"kind": "program", "id": "btc-watch", "accepts": ["Tick"],
               "emits": ["ProducerReturn"], "code": "print('{}')", "timeout_s": 5,
               "trigger": {"kind": "price_cross", "coin": "btc", "level": "60000"}}
    ret = invoke({**ORDER, "register": [program]}, schema)
    assert ret.status == "ok" and {k: ret.outputs[k] for k in ORDER} == ORDER
    accepted, rejected = register(ret.outputs["register"][0])
    assert not rejected
    (seat,) = accepted
    assert seat.model_id == "program" and seat.code == "print('{}')"
    assert seat.trigger == {"kind": "price_cross", "coin": "BTC", "level": "60000"}
    # The alias is the assembly it abbreviates, field for field.
    (same,) = register({**program, "kind": "assembly", "model_id": "program"})[0]
    assert same == seat


def test_a_program_proposal_naming_another_model_is_refused_with_a_reason():
    accepted, rejected = register({"kind": "program", "id": "x-seat", "model_id": "vendor/m",
                                   "accepts": ["Tick"], "code": "print('{}')"})
    assert not accepted
    assert rejected[0].reason.startswith("a program proposal's model_id is program")


def test_the_public_program_shape_publishes_its_trigger():
    shape = make_runtime()._proposal_shape_search("program")["program"]
    assert "trigger" in shape
    for kind in ("price_cross", "funding_sign", "equity_below", "equity_above"):
        assert kind in shape["trigger"]
