"""What crosses a contract boundary when one participant addresses another.

A judge prices an act. To price an addressed message it needs to know that the
message happened, who it went to and what it cost; it does not need the text, and
a sender who knows the text will be read writes for the judge instead of for the
recipient. So the projection keeps the fact and drops the body, wherever in a
model-authored return the body was written.

The manifest modes are parsed here too: the feedback line a producer settles on
is a factor a run declares, and an unknown value is refused rather than assumed.
"""

from __future__ import annotations

import json
import tomllib
from pathlib import Path

import pytest

from factorylab.cortex.request import (
    ADDRESS_TOOL,
    public_child_inputs,
    public_return,
    public_tool_calls,
)
from factorylab.runtime.worlds import load_manifest, manifest_from_dict

SECRET = "I will take the other side of your carry trade at 40 bps."


def call(**changes):
    """One record of an addressed message, as a return may carry it."""
    return {"tool": ADDRESS_TOOL, "args": {"to": "seat-b", "text": SECRET}, **changes}


def rendered(value) -> str:
    return json.dumps(value, sort_keys=True, default=str)


def test_the_body_does_not_cross_and_the_message_still_did():
    projected = public_return({"action": "message", "tool_calls": [call()]})
    assert SECRET not in rendered(projected)
    sent = projected["tool_calls"][0]
    # The act stays legible: which capability, to whom, and how much was said.
    assert sent["tool"] == ADDRESS_TOOL
    assert sent["args"]["to"] == "seat-b"
    assert sent["args"]["body"]["fields"] == ["text"]
    assert sent["args"]["body"]["bytes"] > len(SECRET)
    assert "text" not in sent["args"]


@pytest.mark.parametrize("field", ["text", "body", "message", "content", "payload"])
def test_a_body_under_any_name_is_still_a_body(field):
    projected = public_return({"tool_calls": [call(args={"to": "b", field: SECRET})]})
    assert SECRET not in rendered(projected)


def test_a_body_is_redacted_wherever_a_model_chose_to_put_it():
    everywhere = {
        "outcome": {"steps": [{"result": {"calls": [call()]}}]},
        "inline": {"name": ADDRESS_TOOL, "to": "seat-c", "text": SECRET},
        "named_arguments": {"tool_id": ADDRESS_TOOL, "arguments": {"to": "d", "text": SECRET}},
    }
    assert SECRET not in rendered(public_return(everywhere))


def test_a_root_address_call_is_redacted_without_exposing_continuity():
    continuity = {"working_state": {"private": SECRET},
                  "ack_through": "outcome:1", "raw": SECRET}
    root = public_return({**call(), **continuity})
    nested = public_return({"call": call(), **continuity})

    for projected in (root, nested):
        assert SECRET not in rendered(projected)
        assert not ({"working_state", "ack_through", "raw"} & projected.keys())
    assert root["tool"] == ADDRESS_TOOL
    assert root["args"]["to"] == "seat-b"
    assert root["args"]["body"]["fields"] == ["text"]


@pytest.mark.parametrize("field", ["args", "arguments", "inputs"])
@pytest.mark.parametrize("project", [public_return, public_child_inputs])
def test_outer_address_preserves_nested_redaction_and_public_metadata(field, project):
    inner = {"tool": ADDRESS_TOOL, "args": {"to": "seat-c", "text": SECRET}}
    outer = {
        "tool": ADDRESS_TOOL,
        "text": "inline private body",
        field: {"to": "seat-b", "text": "outer private body",
                "nested": [inner], "cost_micro": 1},
    }
    original = rendered(outer)
    projected = project(outer)
    visible = rendered(projected)
    assert SECRET not in visible
    assert "inline private body" not in visible
    assert "outer private body" not in visible
    assert projected[field]["nested"][0]["args"]["to"] == "seat-c"
    assert projected[field]["nested"][0]["args"]["body"]["fields"] == ["text"]
    assert projected[field]["to"] == "seat-b"
    assert projected[field]["cost_micro"] == 1
    assert rendered(outer) == original
    assert public_tool_calls([outer]) == [public_return(outer)]


@pytest.mark.parametrize("project", [public_return, public_child_inputs])
def test_malformed_argument_list_does_not_restore_nested_private_body(project):
    projected = project({"tool": ADDRESS_TOOL, "args": [call()]})
    assert SECRET not in rendered(projected)
    assert projected["args"][0]["args"]["to"] == "seat-b"


@pytest.mark.parametrize(
    ("recipient_field", "body_field"),
    [
        ("recipient", "text"),
        ("to", "body"),
        ("recipient", "message"),
        ("to", "content"),
        ("recipient", "payload"),
    ],
)
@pytest.mark.parametrize("nested", [False, True])
def test_delegated_address_arguments_are_private_without_a_tool_marker(
        recipient_field, body_field, nested):
    addressed = {recipient_field: "seat-b", body_field: SECRET, "topic": "funding"}
    inputs = {"delegation": addressed} if nested else addressed

    projected = public_child_inputs(inputs)
    visible = projected["delegation"] if nested else projected

    assert SECRET not in rendered(projected)
    assert visible[recipient_field] == "seat-b"
    assert visible["topic"] == "funding"
    assert visible["body"]["fields"] == [body_field]


def test_child_projection_keeps_ordinary_text_and_handles_explicit_calls():
    ordinary = {"text": SECRET, "payload": {"question": "value"}, "topic": "analysis"}
    assert public_child_inputs(ordinary) == ordinary

    projected = public_child_inputs({**call(), "working_state": {"private": SECRET}})
    assert SECRET not in rendered(projected)
    assert projected["args"]["to"] == "seat-b"
    assert projected["args"]["body"]["fields"] == ["text"]
    assert "working_state" not in projected


def test_a_body_nested_past_the_projection_is_dropped_not_passed_through():
    deep = value = {}
    for _ in range(40):
        value["next"] = {}
        value = value["next"]
    value["call"] = call()
    assert SECRET not in rendered(public_return(deep))


def test_everything_that_is_not_a_body_is_left_alone():
    outputs = {"action": "hold", "rationale": "No new evidence.",
               "tool_calls": [{"tool": "venue.positions", "args": {"coin": "BTC"}}]}
    projected = public_return(outputs)
    assert projected["rationale"] == outputs["rationale"]
    assert projected["tool_calls"][0]["args"] == {"coin": "BTC"}
    # Continuity still does not cross, which is what this projection was for first.
    assert "working_state" not in public_return({**outputs, "working_state": {"lens": "x"}})


def test_the_executed_call_list_is_projected_the_same_way():
    calls = public_tool_calls([call(), {"tool": "venue.positions", "args": {}}])
    assert SECRET not in rendered(calls)
    assert [c["tool"] for c in calls] == [ADDRESS_TOOL, "venue.positions"]
    assert public_tool_calls(None) == []


def test_the_senders_own_record_is_not_what_this_touches():
    # A projection is made for a reader across a boundary. The caller's own state is
    # never an argument to it, and the object handed in is not modified.
    outputs = {"tool_calls": [call()]}
    public_return(outputs)
    assert outputs["tool_calls"][0]["args"]["text"] == SECRET


def test_the_producer_feedback_line_is_declared_and_checked():
    assert load_manifest("worlds/scripted.toml").evaluation.producer_feedback == "verdict"

    def load(evaluation):
        raw = tomllib.loads(Path("worlds/scripted.toml").read_text())
        raw["evaluation"] = {**raw.get("evaluation", {}), **evaluation}
        return manifest_from_dict(raw)

    assert load({"producer_feedback": "realized"}).evaluation.producer_feedback == "realized"
    with pytest.raises(ValueError, match="producer_feedback"):
        load({"producer_feedback": "consequence"})
    with pytest.raises(ValueError, match="producer_feedback"):
        load({"producer_feedback": True})
