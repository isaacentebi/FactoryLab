import json
from dataclasses import FrozenInstanceError, replace

import pytest

from factorylab.cortex.registration import Rejected, ToolProposal, parse_proposals
from factorylab.cortex.sandbox import SandboxResult, jail_available
from factorylab.cortex.tools import PopulationTool, ToolRunner, as_spec


@pytest.fixture(autouse=True)
def available_for_protocol_tests(monkeypatch):
    # Shape/result protocol tests are independent of installed host packages.
    monkeypatch.setattr("factorylab.cortex.registration.jail_available", lambda: True)
    monkeypatch.setattr("factorylab.cortex.tools.jail_available", lambda: True)


@pytest.fixture
def usable_jail():
    if not jail_available():
        pytest.skip("host cannot launch an OS jail; refusal has separate regression coverage")


def _tool(code='print("{}")', schema=None, timeout_s=1):
    return PopulationTool(
        "test-tool", "A test tool", schema if schema is not None else {
            "type": "object", "properties": {}
        }, code, timeout_s, "decision-1"
    )


def _proposal(**changes):
    return {
        "kind": "tool",
        "id": "test-tool",
        "description": "A test tool",
        "args_schema": {"type": "object", "properties": {}},
        "code": 'print("{}")',
        "timeout_s": 1,
        **changes,
    }


def _parse(items, **kwargs):
    return parse_proposals(
        {"register": items}, event_kinds=frozenset(), known_models=frozenset(),
        known_assemblies=frozenset(), **kwargs
    )


def test_tool_proposal_accepts_empty_properties_and_boundaries():
    item = _proposal(id="a" * 48, description="d" * 500, code="#" * 8000, timeout_s=5)
    accepted, rejected = _parse([item])
    assert rejected == []
    assert accepted == [ToolProposal(**{k: v for k, v in item.items() if k != "kind"})]
    assert _parse([_proposal(code="")])[1] == []


@pytest.mark.parametrize(("field", "value", "reason"), [
    ("id", "a", "id must be a slug of 2-48 chars"),
    ("id", "a" * 49, "id must be a slug of 2-48 chars"),
    ("id", "Bad Id", "id must be a slug of 2-48 chars"),
    ("id", "ab\n", "id must be a slug of 2-48 chars"),
    ("id", [], "id must be a slug of 2-48 chars"),
    ("description", " ", "description is required"),
    ("description", None, "description is required"),
    ("description", "d" * 501, "description exceeds 500 chars"),
    ("code", None, "code must be a string"),
    ("code", "#" * 8001, "code exceeds 8000 chars"),
    ("timeout_s", 0, "timeout_s must be an int in [1, 5]"),
    ("timeout_s", 6, "timeout_s must be an int in [1, 5]"),
    ("timeout_s", True, "timeout_s must be an int in [1, 5]"),
    ("timeout_s", 1.0, "timeout_s must be an int in [1, 5]"),
    ("timeout_s", None, "timeout_s must be an int in [1, 5]"),
])
def test_invalid_tool_fields_are_rejected(field, value, reason):
    assert _parse([_proposal(**{field: value})]) == ([], [Rejected(0, reason)])


@pytest.mark.parametrize("schema", [
    None, [], {}, {"type": "array", "properties": {}},
    {"type": "object"}, {"type": "object", "properties": []},
])
def test_proposal_requires_an_object_schema_with_properties(schema):
    assert _parse([_proposal(args_schema=schema)]) == (
        [], [Rejected(0, "args_schema must have type object and a properties dict")]
    )


@pytest.mark.parametrize("code", [
    "import urllib.request", "import socket", "import http.client", "import requests",
    "from subprocess import run", "import os; os.system('true')",
    "# import urllib is forbidden even in a comment",
])
def test_code_is_not_filtered_by_substrings(code):
    accepted, rejected = _parse([_proposal(code=code)])
    assert not rejected and accepted[0].code == code


def test_known_tool_id_and_proposal_cap_are_enforced():
    assert _parse([_proposal()], known_tools=frozenset({"test-tool"})) == (
        [], [Rejected(0, "id already registered")]
    )
    accepted, rejected = _parse([_proposal(id=f"tool-{i}") for i in range(4)])
    assert len(accepted) == 3
    assert rejected == [Rejected(3, "proposal cap reached for this return")]


def test_echo_and_sum_execute_with_json_stdin(usable_jail):
    echo = _tool(
        "import json, sys; print(json.dumps(json.load(sys.stdin)))",
        {"type": "object", "properties": {}, "additionalProperties": True},
    )
    args = {"text": "héllo", "nested": {"ok": True}, "values": [1, 2, 3]}
    assert ToolRunner().run(echo, args) == args
    summer = _tool(
        "import json, sys; print(json.dumps({'sum': sum(json.load(sys.stdin)['values'])}))",
        {"type": "object", "properties": {"values": {"type": "array"}},
         "required": ["values"]},
    )
    assert ToolRunner().run(summer, {"values": [1, 2, 3]}) == {"sum": 6}


def test_timeout_returns_an_error(usable_jail):
    assert ToolRunner().run(_tool("import time; time.sleep(10)"), {}) == {"error": "timeout"}


@pytest.mark.parametrize("output", ["hello", "[]", "1", "null", "", '{"n": NaN}'])
def test_non_object_json_and_non_json_outputs_fail(output, usable_jail):
    assert ToolRunner().run(_tool(f"print({output!r})"), {}) == {
        "error": "output is not a JSON object"
    }


def test_20kb_output_is_rejected(usable_jail):
    tool = _tool("import json; print(json.dumps({'text': 'x' * 20_000}))")
    assert ToolRunner().run(tool, {}) == {"error": "output too large"}


def test_output_cap_counts_utf8_bytes_including_whitespace(usable_jail):
    output = '{"text":"é"}'
    cap = len(output.encode("utf-8"))
    tool = _tool(f"print({output!r}, end='')")
    assert ToolRunner(max_output_bytes=cap).run(tool, {}) == {"text": "é"}
    assert ToolRunner(max_output_bytes=cap - 1).run(tool, {}) == {"error": "output too large"}
    padded = _tool("print('{}' + ' ' * 100, end='')")
    assert ToolRunner(max_output_bytes=2).run(padded, {}) == {"error": "output too large"}


def test_custom_cap_above_sandbox_default_detects_truncation(usable_jail):
    tool = _tool("print('{}' + ' ' * 70_000, end='')")
    assert ToolRunner(max_output_bytes=65_536).run(tool, {}) == {"error": "output too large"}


def test_nonzero_exit_returns_first_500_stderr_characters(usable_jail):
    tool = _tool("import sys; sys.stderr.write('é' * 600); sys.exit(7)")
    assert ToolRunner(max_output_bytes=2).run(tool, {}) == {
        "error": "exit 7", "stderr": "é" * 500
    }


@pytest.mark.parametrize(("kind", "valid", "invalid"), [
    ("string", "x", 1), ("number", 1.5, True), ("number", 1, "1"),
    ("integer", 1, True), ("integer", 1, 1.0), ("boolean", True, 1),
    ("array", [], ()), ("object", {}, []),
])
def test_schema_property_types_are_validated_before_execution(monkeypatch, kind, valid, invalid):
    calls = []

    def run(code, **kwargs):
        calls.append(kwargs)
        return SandboxResult(kwargs["stdin"], "", 0, False, False)

    monkeypatch.setattr("factorylab.cortex.tools.run_python", run)
    tool = _tool(schema={"type": "object", "properties": {"value": {"type": kind}}})
    assert ToolRunner().run(tool, {"value": valid}) == {"value": valid}
    assert len(calls) == 1
    assert ToolRunner().run(tool, {"value": invalid}) == {
        "error": f"invalid args: property value must be {kind}"
    }
    assert len(calls) == 1


@pytest.mark.parametrize(("schema", "args", "error"), [
    ({"required": ["value"]}, {}, "invalid args: missing required property value"),
    ({}, {"extra": 1}, "invalid args: additional property extra"),
    ({"additionalProperties": False}, {"extra": 1}, "invalid args: additional property extra"),
    ({}, [], "invalid args: expected an object with string keys"),
    ({}, {1: "x"}, "invalid args: expected an object with string keys"),
    ({"type": "array"}, {}, "invalid args_schema: expected object with properties"),
    ({"properties": []}, {}, "invalid args_schema: expected object with properties"),
    ({"properties": {"x": {"type": "null"}}}, {},
     "invalid args_schema: properties must name supported types"),
    ({"properties": {"x": {"type": []}}}, {},
     "invalid args_schema: properties must name supported types"),
    ({"required": "value"}, {}, "invalid args_schema: required must be a list of strings"),
    ({"required": [1]}, {}, "invalid args_schema: required must be a list of strings"),
    ({"additionalProperties": {}}, {},
     "invalid args_schema: additionalProperties must be a boolean"),
])
def test_invalid_schema_or_args_never_execute(monkeypatch, schema, args, error):
    def unexpected(*args, **kwargs):
        pytest.fail("invalid arguments reached the sandbox")

    monkeypatch.setattr("factorylab.cortex.tools.run_python", unexpected)
    tool = _tool(schema={"type": "object", "properties": {}, **schema})
    assert ToolRunner().run(tool, args) == {"error": error}


def test_optional_properties_and_explicit_additional_properties(usable_jail):
    tool = _tool(schema={
        "type": "object", "properties": {"optional": {"type": "string"}},
        "additionalProperties": True,
    })
    assert ToolRunner().run(tool, {}) == {}
    assert ToolRunner().run(tool, {"extra": 1}) == {}


@pytest.mark.parametrize("value", [object(), float("nan"), float("inf")])
def test_non_json_args_fail_without_raising(value):
    tool = _tool(schema={"type": "object", "properties": {}, "additionalProperties": True})
    assert ToolRunner().run(tool, {"value": value}) == {
        "error": "invalid args: must be JSON serializable"
    }


def test_sandbox_failure_is_an_error_result(monkeypatch):
    def fail(*args, **kwargs):
        raise OSError("host details must not leak")

    monkeypatch.setattr("factorylab.cortex.tools.run_python", fail)
    assert ToolRunner().run(_tool(), {}) == {"error": "tool execution failed"}


@pytest.mark.parametrize("timeout_s", [1, 2, 5])
def test_runner_passes_wall_and_cpu_limits(monkeypatch, timeout_s):
    def run(code, **kwargs):
        assert code == 'print("{}")'
        assert json.loads(kwargs["stdin"]) == {}
        assert kwargs["timeout_s"] == timeout_s
        assert kwargs["cpu_s"] == min(timeout_s, 2)
        return SandboxResult("{}", "", 0, False, False)

    monkeypatch.setattr("factorylab.cortex.tools.run_python", run)
    assert ToolRunner().run(_tool(timeout_s=timeout_s), {}) == {}


def test_tool_environment_does_not_inherit_path_home_or_keys(monkeypatch, usable_jail):
    names = ("PATH", "HOME", "OPENROUTER_API_KEY", "HL_PRIVATE_KEY")
    for name in names:
        monkeypatch.setenv(name, "test-value-must-not-reach-tool")
    tool = _tool("import json, os; print(json.dumps({'keys': sorted(os.environ)}))")
    result = ToolRunner().run(tool, {})
    assert "keys" in result
    assert set(names[1:]).isdisjoint(result["keys"])
    # Python and macOS may synthesize locale variables even with env={}.
    assert set(result["keys"]) <= {"PATH", "LC_CTYPE", "__CF_USER_TEXT_ENCODING"}


def test_population_tool_is_frozen_and_spec_only_exposes_public_fields():
    tool = _tool()
    with pytest.raises(FrozenInstanceError):
        tool.code = "changed"
    spec = as_spec(tool, 50)
    assert spec == {
        "id": "test-tool", "description": "A test tool",
        "args_schema": {"type": "object", "properties": {}},
        "price_micro_per_call": 50, "kind": "population",
    }
    spec["args_schema"]["properties"]["new"] = {"type": "string"}
    assert tool.args_schema["properties"] == {}
    assert replace(tool, provenance="another-decision").provenance == "another-decision"


@pytest.mark.parametrize("price", [True, 1.5, -1])
def test_spec_rejects_non_integer_or_negative_money(price):
    with pytest.raises(ValueError, match="non-negative int"):
        as_spec(_tool(), price)


@pytest.mark.parametrize("cap", [True, 0, -1, 1.5])
def test_invalid_output_budget_is_a_configuration_error(cap):
    with pytest.raises(ValueError, match="positive int"):
        ToolRunner(max_output_bytes=cap)


def test_stderr_character_budget_survives_byte_bounded_capture(monkeypatch):
    def bounded_capture(code, **kwargs):
        stderr = ("é" * 600).encode()[:kwargs["max_output_bytes"]].decode()
        return SandboxResult("", stderr, 7, False, False)

    monkeypatch.setattr("factorylab.cortex.tools.run_python", bounded_capture)
    assert ToolRunner(max_output_bytes=2).run(_tool(), {}) == {
        "error": "exit 7", "stderr": "é" * 500,
    }
