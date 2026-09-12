"""Population tool results are bounded JSON objects or deterministic errors."""

from __future__ import annotations

import json
from copy import deepcopy
from dataclasses import dataclass

from factorylab.cortex.sandbox import NoJail, jail_available, run_python


@dataclass(frozen=True)
class PopulationTool:
    id: str
    description: str
    args_schema: dict
    code: str
    timeout_s: int
    provenance: str


class ToolRunner:
    def __init__(self, *, max_output_bytes: int = 8192) -> None:
        """Guarantee a positive integer UTF-8 stdout budget for each result."""
        if type(max_output_bytes) is not int or max_output_bytes <= 0:
            raise ValueError("max_output_bytes must be a positive int")
        self.max_output_bytes = max_output_bytes
        self.available = jail_available()

    def run(self, tool: PopulationTool, args: dict) -> dict:
        """Return a JSON object or an error; tool failures never escape as exceptions.

        Validate top-level property types and required names before execution.
        Extra arguments require explicit additionalProperties=true. Nested
        constraints are outside this schema subset. Timeouts and unsuccessful
        exits take precedence over stdout size and JSON errors.
        """
        try:
            error = _validate_args(tool.args_schema, args)
            if error is not None:
                return {"error": error}
            try:
                stdin = json.dumps(args, allow_nan=False)
            except (TypeError, ValueError, RecursionError):
                return {"error": "invalid args: must be JSON serializable"}
            if not self.available:
                return {"error": "no jail on this host"}
            result = run_python(
                tool.code,
                stdin=stdin,
                timeout_s=tool.timeout_s,
                cpu_s=min(tool.timeout_s, 2),
                # One extra byte makes truncation detectable by the result cap.
                max_output_bytes=max(self.max_output_bytes + 1, 2000),
            )
            if result.timed_out:
                return {"error": "timeout"}
            if result.returncode != 0:
                return {"error": f"exit {result.returncode}", "stderr": result.stderr[:500]}
            if len(result.stdout.encode("utf-8")) > self.max_output_bytes:
                return {"error": "output too large"}
            try:
                output = json.loads(result.stdout, parse_constant=_reject_constant)
            except (ValueError, RecursionError):
                return {"error": "output is not a JSON object"}
            if not isinstance(output, dict):
                return {"error": "output is not a JSON object"}
            return output
        except NoJail:
            return {"error": "no jail on this host"}
        except Exception:
            # Launch, decoding and malformed tool failures stay within the
            # result protocol without exposing host exception details.
            return {"error": "tool execution failed"}


def _reject_constant(value: str) -> None:
    raise ValueError("non-JSON numeric constant")


def _validate_args(schema: dict, args: dict) -> str | None:
    if (
        not isinstance(schema, dict)
        or schema.get("type") != "object"
        or not isinstance(schema.get("properties"), dict)
    ):
        return "invalid args_schema: expected object with properties"
    properties = schema["properties"]
    types = {
        "string": (str,),
        "number": (int, float),
        "integer": (int,),
        "boolean": (bool,),
        "array": (list,),
        "object": (dict,),
    }
    for name, prop in properties.items():
        if (
            not isinstance(name, str)
            or not isinstance(prop, dict)
            or not isinstance(prop.get("type"), str)
            or prop["type"] not in types
        ):
            return "invalid args_schema: properties must name supported types"
    required = schema.get("required", [])
    if not isinstance(required, list) or any(not isinstance(name, str) for name in required):
        return "invalid args_schema: required must be a list of strings"
    additional = schema.get("additionalProperties", False)
    if type(additional) is not bool:
        return "invalid args_schema: additionalProperties must be a boolean"
    if not isinstance(args, dict) or any(not isinstance(name, str) for name in args):
        return "invalid args: expected an object with string keys"
    for name in required:
        if name not in args:
            return f"invalid args: missing required property {name}"
    for name, value in args.items():
        if name not in properties:
            if not additional:
                return f"invalid args: additional property {name}"
        elif type(value) not in types[properties[name]["type"]]:
            return f"invalid args: property {name} must be {properties[name]['type']}"
    return None


def as_spec(tool: PopulationTool, price_micro_per_call: int) -> dict:
    """Return public ToolSpec fields without source, provenance or schema aliases."""
    if type(price_micro_per_call) is not int or price_micro_per_call < 0:
        raise ValueError("price_micro_per_call must be a non-negative int")
    return {
        "id": tool.id,
        "description": tool.description,
        "args_schema": deepcopy(tool.args_schema),
        "price_micro_per_call": price_micro_per_call,
        "kind": "population",
    }
