"""Primitive audit F6 and F9: a contract carries its own description and its promise.

Essay II.I: "the contract has to carry enough self-description that a primitive can
be picked up against a constraint that did not exist when the contract was written",
and a contract says "what it promises to return". An assembly publishes an agent
card (id, accepts, emits, description); a population tool publishes the schema its
results are held to.
"""

import json

import pytest

from factorylab.cortex.assembly import SEED_KIND_LINES, contract_line
from factorylab.cortex.registration import (
    AssemblyProposal,
    Rejected,
    ToolProposal,
    parse_proposals,
)
from factorylab.cortex.tools import PopulationTool, ToolRunner, as_spec
from factorylab.runtime.resume import restore_runtime, runtime_state
from tests.conftest import make_runtime as _runtime
from tests.runtime.test_child_requests import parent_request


def make_runtime():
    rt = _runtime()
    rt._manage_reserve_window()  # an open novelty window admits registrations
    return rt


CARD = "Returns a forecast of the next funding print for one coin."


def _register_seat(rt, aid, *, description="", emits=("ProducerReturn",)):
    req = parent_request(rt)
    rt.handle_to_assembly[req.handle] = "seed-decider"
    rt._register(req.handle, AssemblyProposal(
        aid, "producer", "fake-haiku", "Reply with JSON.", ("Tick",), 128, "low", emits,
        description=description))
    return req.handle


def _parse(item, **known):
    return parse_proposals({"register": [item]}, event_kinds=frozenset({"Tick"}),
                           known_models=frozenset({"fake-haiku"}),
                           known_assemblies=frozenset(), known_tools=frozenset(),
                           tool_jail=True, **known)


def _assembly_item(**extra):
    return {"kind": "assembly", "id": "funding-card", "model_id": "fake-haiku",
            "system_prompt": "Reply with JSON.", "accepts": ["Tick"], **extra}


def test_a_registered_description_is_published_in_the_catalogue_and_search():
    rt = make_runtime()
    _register_seat(rt, "funding-card", description=CARD)
    row = next(r for r in rt._world_block()["catalogue"] if r["id"] == "funding-card")
    assert row["description"] == CARD
    announced = next(e for e in rt.internal if e.payload.get("id") == "funding-card")
    assert announced.payload["description"] == CARD
    found = rt._run_tool("seed-decider", "h", {"tool": "catalogue.search",
                                                "args": {"substring": "funding print"}})[0]
    assert [a["id"] for a in found["assemblies"]] == ["funding-card"]
    assert found["assemblies"][0] == {"id": "funding-card", "version": 1, "accepts": ["Tick"],
                                      "emits": ["ProducerReturn"], "description": CARD}


def test_a_description_survives_a_checkpoint():
    rt = make_runtime()
    _register_seat(rt, "funding-card", description=CARD)
    restored = make_runtime()
    restore_runtime(restored, runtime_state(rt))
    assert restored.assemblies["funding-card"].spec.description == CARD


def test_seeds_publish_their_contracts_line_and_nothing_of_their_lens():
    rt = make_runtime()
    rows = {r["id"]: r for r in rt._world_block()["catalogue"]}
    for aid, assembly in rt.assemblies.items():
        spec = assembly.spec
        assert rows[aid]["description"] == contract_line(spec.accepts, spec.emits)
        assert spec.system_prompt not in rows[aid]["description"]
        for kind in spec.emits:
            assert SEED_KIND_LINES[kind] in rows[aid]["description"]


def test_a_description_is_bounded_and_must_be_text():
    accepted, rejected = _parse(_assembly_item(description=CARD))
    assert accepted[0].description == CARD and not rejected
    for bad in ("x" * 501, 7):
        accepted, rejected = _parse(_assembly_item(description=bad))
        assert not accepted and isinstance(rejected[0], Rejected)


def test_a_tool_returns_schema_is_parsed_and_a_malformed_one_is_refused():
    item = {"kind": "tool", "id": "doubler", "description": "doubles x",
            "args_schema": {"type": "object", "properties": {"x": {"type": "number"}}},
            "returns_schema": {"type": "object", "properties": {"y": {"type": "number"}},
                               "required": ["y"]},
            "code": "", "timeout_s": 1}
    accepted, _ = _parse(item)
    assert accepted[0].returns_schema == item["returns_schema"]
    for bad in ({"type": "array"}, {"type": "object", "properties": {"y": {"type": "date"}}},
                {"type": "object", "properties": {}, "required": "y"}):
        accepted, rejected = _parse({**item, "returns_schema": bad})
        assert not accepted and "returns_schema" in rejected[0].reason


@pytest.fixture
def usable_jail():
    runner = ToolRunner()
    if not runner.available:
        pytest.skip("host cannot launch an OS jail")
    return runner


def _tool(code, returns):
    return PopulationTool("doubler", "doubles x",
                          {"type": "object", "properties": {"x": {"type": "number"}}},
                          code, 2, "h", returns)


def test_a_result_that_breaks_the_promise_never_reaches_the_caller(usable_jail):
    promise = {"type": "object", "properties": {"y": {"type": "number"}}, "required": ["y"]}
    kept = _tool("import json,sys\nprint(json.dumps({'y': json.load(sys.stdin)['x'] * 2}))",
                 promise)
    assert usable_jail.run(kept, {"x": 2}) == {"y": 4}
    broken = _tool("print('{\"y\": \"four\"}')", promise)
    assert usable_jail.run(broken, {"x": 2})["error"].startswith(
        "result breaks returns_schema")
    missing = _tool("print('{}')", promise)
    assert "missing required property y" in usable_jail.run(missing, {"x": 2})["error"]
    unpromised = _tool("print('{\"anything\": 1}')", None)
    assert usable_jail.run(unpromised, {"x": 2}) == {"anything": 1}


def test_a_registered_returns_schema_is_published_with_the_tool():
    rt = make_runtime()
    rt.tool_jail_available = True
    promise = {"type": "object", "properties": {"y": {"type": "number"}}, "required": ["y"]}
    rt._register("author", ToolProposal("doubler", "doubles x",
                                        {"type": "object", "properties": {}}, "", 1, promise))
    assert rt.population_tools["doubler"].returns_schema == promise
    assert rt.tool_specs["doubler"]["returns_schema"] == promise
    found = rt._tool_schema_search("doubler", 5)
    assert found[0]["returns_schema"] == promise
    # The durable contract and the public announcement carry the promise too.
    output = rt.registry.get("tool:doubler").output_schema  # frozen: tuples, mappings
    assert json.loads(json.dumps(output, default=dict)) == promise
    announced = rt.internal[-1].payload["returns_schema"]
    assert json.loads(json.dumps(announced, default=dict)) == promise
    assert "returns_schema" not in as_spec(_tool("", None), 0)
