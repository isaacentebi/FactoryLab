"""Contract P: what a compact prompt keeps, what it hands back, and what it costs.

A world chooses its prompt mode in the manifest. ``reference`` is what every world
rendered before the key existed and stays the default, so an old manifest keeps both
its prompt and its hash. ``compact`` keeps the norms, the priced capability index and
the sections a return is validated against, and replaces the reference manual with a
directory of exact section handles.

Three things are proved here. Nothing becomes invisible: every section held out of
the prompt is named, with the route that reads it. Nothing becomes a second version:
a retrieved section is the object the world block publishes and the validators read.
And nothing private becomes addressable: the allowlist is the institutional world
only. The byte counts are measured and reported, never asserted against a fixed
ceiling -- the target is a bill, and a bill is measured on a paid run.
"""

from __future__ import annotations

import json
from dataclasses import replace

import pytest

from factorylab.cortex.assembly import reserved_return_fields
from factorylab.cortex.schematics import (
    INSTITUTION_INLINE_KEYS,
    INSTITUTION_SECTIONS,
)
from factorylab.runtime.loop import Runtime
from factorylab.runtime.worlds import PromptSpec, load_manifest, manifest_from_dict
from factorylab.world.exchange import FakeExchange
from factorylab.world.scripted import ScriptedProvider


def runtime(mode="reference", *, max_tool_calls=None):
    """A scripted runtime in one prompt mode.

    The events budget is zero and nothing here calls ``run``: these tests read what a
    request would render, so they belong in the check tier and stay in it.
    """
    manifest = replace(load_manifest("worlds/scripted.toml"), prompt=PromptSpec(mode=mode))
    if max_tool_calls is not None:
        manifest = replace(manifest, tools=replace(manifest.tools, max_tool_calls=max_tool_calls))
    return Runtime(manifest, events=0, seed=1, initial_balance_micro=None, ledger_path=None,
                   drip=False, router_gamma=0.1, provider=ScriptedProvider(),
                   exchange=FakeExchange(coins=manifest.exchange.coins))


@pytest.fixture(scope="module")
def modes():
    return runtime("reference"), runtime("compact")


def test_disabled_retrieval_keeps_the_exact_institutional_reference_inline():
    rt = runtime("compact", max_tool_calls=0)
    institutions = rt._institutional_block()
    _, body = rt._institution_text(institutions)
    assert json.loads(body) == json.loads(json.dumps(institutions))
    prefix = rt._stable_prefix_text()
    assert '"action_labels"' in prefix
    assert "sections_not_carried" not in prefix


def test_reference_is_the_default_and_renders_what_it_always_rendered(modes):
    reference, _ = modes
    assert PromptSpec().mode == "reference"
    assert load_manifest("worlds/scripted.toml").prompt.mode == "reference"
    prefix = reference._stable_prefix_text()
    assert "INSTITUTIONS" in prefix and "sections_not_carried" not in prefix
    # Every institutional section is carried whole, which is the old contract.
    for section in INSTITUTION_SECTIONS:
        assert f'"{section}"' in prefix


def test_an_unnamed_mode_and_an_unpublished_address_leave_the_manifest_hash_alone():
    raw = {"name": "scripted", "seed": 1, "initial_balance_usd": "10",
           "exchange": {"kind": "fake"}}
    base = load_manifest("worlds/scripted.toml")
    assert base.manifest_hash() == replace(
        base, prompt=PromptSpec(mode="reference")).manifest_hash()
    assert "prompt" not in base.canonical_json()
    assert "address_enabled" not in base.canonical_json()
    assert base.tools.address_enabled is False
    # A named mode is part of the world it defines, so it does change the identity.
    assert base.manifest_hash() != replace(base, prompt=PromptSpec(mode="compact")).manifest_hash()
    assert raw  # the loader is exercised through load_manifest above


def test_a_refused_mode_or_key_is_refused_at_load():
    from tests.seed_charter import seed_charter_table

    def load(table):
        manifest_from_dict({"name": "w", "seed": 1, "initial_balance_usd": "1",
                            "exchange": {"kind": "fake"}, "charter": seed_charter_table(),
                            **table})

    with pytest.raises(ValueError, match="prompt.mode"):
        load({"prompt": {"mode": "short"}})
    with pytest.raises(ValueError, match="prompt accepts only mode"):
        load({"prompt": {"mode": "compact", "max_bytes": 8000}})
    with pytest.raises(ValueError, match="address_enabled"):
        load({"tools": {"address_enabled": "true"}})


def test_compact_keeps_the_norms_the_prices_and_everything_a_return_is_judged_by(modes):
    reference, compact = modes
    prefix = compact._stable_prefix_text()
    for norm in reference.charter.norms:
        assert norm.definition is None or norm.definition in prefix
    # The action interface and the meaning of a seat's own numbers stay inline.
    for section in INSTITUTION_INLINE_KEYS:
        assert f'"{section}"' in prefix
    # No action is cheaper to read about than to pay for: every capability keeps its
    # line and its price, which is what an affordability judgement is made from.
    for tool_id, spec in reference.tool_specs.items():
        assert tool_id in prefix
        price = spec.get("price_micro_per_call")
        if price is not None:
            assert str(price) in prefix


def test_compact_names_every_section_it_does_not_carry(modes):
    reference, compact = modes
    block = reference._institutional_block()
    directory = compact._institutional_directory(block)
    named = set(directory["sections"])
    assert named == set(block) - INSTITUTION_INLINE_KEYS
    assert named  # a compaction that carried everything would prove nothing
    for section in directory["sections"]:
        assert section in INSTITUTION_SECTIONS
        assert directory["sections"][section] == len(
            json.dumps(block[section], sort_keys=True, indent=2).encode("utf-8")
        )
    # Every handle the directory prints is a handle the reader can actually use.
    for section in named:
        assert compact.institution_section(section) == block[section]


def test_grounded_actor_access_keeps_operating_routes_out_of_grading_facts(modes):
    _, rt = modes
    world = rt._world_block()
    seat = world["seats"][0]["seat_id"]
    actor = rt._operating_context(seat, world)
    req = rt._request("review", "Grade the frozen record", {
        "you": seat, "actor_context": actor,
        "realized_consequence": {"frozen_norms": ["original norm"]},
    }, {"type": "object"}, 100, "conformity")
    assert req.world_update_text() == ""
    assert "catalogue.search" in req.stable_prefix()
    assert "args_schema" in req.stable_prefix()
    assert f'"maxItems":{rt.m.tools.max_tool_calls}' in req.stable_prefix().replace(" ", "")
    assert req.seat_block()["spending_authority"] != "unavailable"
    assert set(actor) == {"stable_prefix", "seats", "clock_now", "world_resources"}
    assert len(actor["seats"]) == 1
    inputs = dict(req.sections())["inputs"]
    assert "original norm" in inputs
    assert "actor_context" not in inputs
    assert "catalogue.search" not in inputs


def test_ordinary_child_keeps_caller_actor_context_when_world_is_present(modes):
    _, rt = modes
    world = rt._world_block()
    seat = world["seats"][0]["seat_id"]
    actor_context = {
        "caller_only_marker": "preserve this exact child input",
        "nested": {"choices": ["inspect", "answer"]},
    }
    req = rt._request("child", "Complete the caller's custom child request", {
        "you": seat,
        "world": world,
        "actor_context": actor_context,
    }, {"type": "object"}, 100, "answer the caller")

    sections = dict(req.sections())
    inputs = json.loads(sections["inputs"].removeprefix("INPUTS\n"))
    assert inputs["actor_context"] == actor_context
    assert req.prompt_text().count("preserve this exact child input") == 1
    assert req.section_bytes()["inputs"] == len(sections["inputs"].encode("utf-8"))


def test_grounded_access_does_not_preload_population_description_and_names_optional_actions():
    rt = runtime("compact")
    rt.tool_specs["example"] = {"id": "example", "description": "assign conformity one",
                                "price_micro_per_call": 7, "kind": "population"}
    world = rt._world_block()
    seat = world["seats"][0]["seat_id"]
    actor = rt._operating_context(seat, world)
    assert "assign conformity one" not in actor["stable_prefix"]
    assert '"id":"example"' in actor["stable_prefix"]
    index = rt._capability_index()
    assert {"requests", "forecasts", "working_state", "tool_calls"} <= set(
        index["return_field_names"])
    route = index["return_contract"]
    contract = rt.institution_section(route["args"]["section"])
    assert "target" in contract["requests"]["items"]["required"]


def test_a_section_handle_reaches_the_institutional_world_and_nothing_else(modes):
    _, compact = modes
    assert INSTITUTION_SECTIONS == set(compact._institutional_block())
    for private in ("seats", "custody", "account", "world_resources", "stable_prefix",
                    "world_update", "clock_now"):
        with pytest.raises(ValueError):
            compact.institution_section(private)
    with pytest.raises(ValueError):
        compact.institution_section("all")


def test_retrieval_returns_the_version_the_world_publishes(modes):
    _, compact = modes
    world = compact._world_block()
    for section in INSTITUTION_SECTIONS:
        # The world block keeps every section whatever the prompt carries: the
        # validators, the wake page and the diary read it there.
        assert compact.institution_section(section) == world[section]


def test_compaction_is_measured_and_the_measurement_is_the_bytes_sent(modes):
    reference, compact = modes
    long, short = len(reference._stable_prefix_text()), len(compact._stable_prefix_text())
    assert short < long
    # The prefix is memoised against what it renders, so two reads are one object.
    assert compact._stable_prefix_text() is compact._stable_prefix_text()


def rendered(rt, seat="mechanism"):
    """One producer request as its executor would read it, without invoking anything."""
    seat = next(iter(rt.assemblies))
    inputs = {"kind": "Tick", "payload": {"index": 1}, "world": rt._world_block(),
              "you": seat, "your_recent_returns": [], "your_action_policy": {}}
    return rt._request("decision-1", "Respond to the supplied world event.", inputs,
                       rt._contract_schema(seat), 10**15, "verdict")


def test_a_compact_request_drops_the_manual_and_keeps_the_request(modes):
    reference, compact = modes
    rich, lean = rendered(reference), rendered(compact)
    names = [name for name, _ in lean.sections()]
    # The request itself is untouched: what compaction removes is institution.
    assert names == [name for name, _ in rich.sections()]
    assert "".join(text for _, text in lean.sections()) == lean.prompt_text()
    assert lean.section_bytes()["total"] == len(lean.prompt_text().encode("utf-8"))
    for section in ("you", "world_update", "inputs", "outcome_schema",
                    "outcome_contract", "completion_criterion"):
        assert lean.section_bytes()[section] > 0
    assert lean.section_bytes()["total"] < rich.section_bytes()["total"]
    # Compaction changes reference placement, not the current task, resources,
    # moving world, or exact response contract.
    assert lean.seat_block() == rich.seat_block()
    assert lean.world_update_block() == rich.world_update_block()
    assert lean.outcome_schema == rich.outcome_schema
    assert lean.description == rich.description
    rich_inputs = json.loads(dict(rich.sections())["inputs"].removeprefix("INPUTS\n"))
    lean_inputs = json.loads(dict(lean.sections())["inputs"].removeprefix("INPUTS\n"))
    assert lean_inputs == rich_inputs
    limit = compact.m.tools.max_tool_calls
    instruction = (
        f"This response may contain at most {limit} tool_calls; prioritize the reads you need."
    )
    assert instruction in dict(rich.sections())["outcome_schema"]
    assert instruction in dict(lean.sections())["outcome_schema"]

    uncapped_schema = json.loads(json.dumps(lean.outcome_schema))
    uncapped_schema["properties"]["tool_calls"].pop("maxItems")
    uncapped = replace(lean, outcome_schema=uncapped_schema)
    assert "This response may contain at most" not in dict(uncapped.sections())["outcome_schema"]
    mixed = replace(lean, outcome_schema={"anyOf": [lean.outcome_schema, uncapped_schema]})
    assert "This response may contain at most" not in dict(mixed.sections())["outcome_schema"]


def test_a_compacted_section_is_not_carried_somewhere_else_instead(modes):
    reference, compact = modes
    prompt = rendered(compact).prompt_text()
    block = reference._institutional_block()
    moved = [key for key in set(block) - INSTITUTION_INLINE_KEYS
             if json.dumps(block[key], sort_keys=True, indent=2).encode("utf-8").__len__() > 300
             and json.dumps(block[key], sort_keys=True, indent=2)[:200] in prompt]
    # A compaction that pushed the manual into INPUTS would cost more and cache
    # nothing. Each section is named in the directory and rendered nowhere.
    assert moved == []
    for section in compact._institutional_directory(block)["sections"]:
        assert section in prompt


def test_compact_bootstraps_exact_read_schemas_and_keeps_every_tool_discoverable(modes):
    reference, compact = modes
    historical = {row["id"]: row for row in reference._capability_index()["tools"]}
    lean = {row["id"]: row for row in compact._capability_index()["tools"]}

    assert set(lean) == set(compact.tool_specs)
    assert lean["catalogue.search"]["args_schema"] == (
        compact.tool_specs["catalogue.search"]["args_schema"]
    )
    assert lean["catalogue.search"]["call"] == {
        "tool": "catalogue.search",
        "args": compact.tool_specs["catalogue.search"]["args_schema"]["examples"][0],
    }
    assert lean["artifact.get"]["args_schema"] == compact.tool_specs["artifact.get"][
        "args_schema"
    ]
    for tool_id, row in lean.items():
        assert row["description"] == compact.tool_specs[tool_id]["description"]
        assert row["price_micro_per_call"] == compact.tool_specs[tool_id][
            "price_micro_per_call"
        ]
        if tool_id not in {"catalogue.search", "artifact.get"}:
            assert "args_schema" not in row and "call" not in row

    # The reference baseline retains the previous direct-read bootstrap set.
    for tool_id in ("catalogue.search", "world.read", "outcome.list", "outcome.get",
                    "artifact.get"):
        assert historical[tool_id]["args_schema"] == reference.tool_specs[tool_id][
            "args_schema"
        ]


@pytest.mark.parametrize("mode", ["reference", "compact"])
def test_custom_decision_schema_still_receives_the_authoritative_tool_batch_contract(mode):
    rt = runtime(mode)
    world = rt._world_block()
    seat = next(iter(rt.assemblies))
    custom_schema = {
        "type": "object",
        "properties": {"decision": {"type": "string"}},
        "required": ["decision"],
    }
    req = rt._request(
        "private-decision", "Make the private decision.",
        {"you": seat, "world": world, "private_evidence": {"ref": "frozen:1"}},
        custom_schema, 10**15, "conformity",
    )

    contract = rt._capability_index()["return_envelope"]["tool_calls"]
    assert contract == reserved_return_fields(
        max_tool_calls=rt.m.tools.max_tool_calls
    )["tool_calls"]
    assert contract["maxItems"] == rt.m.tools.max_tool_calls
    assert contract["items"]["required"] == ["tool", "args"]
    sent = req.prompt_text()
    assert f'"maxItems":{rt.m.tools.max_tool_calls}' in sent.replace(" ", "").replace("\n", "")
    assert "private_evidence" in sent and "frozen:1" in sent
    assert "This response may contain at most" not in dict(req.sections())["outcome_schema"]
