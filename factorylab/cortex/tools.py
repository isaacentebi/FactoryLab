"""Population tool results are bounded JSON objects or deterministic errors."""

from __future__ import annotations

import json
import signal
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
    provenance: str  # the handle of the decision that registered the tool
    # What the tool promises to return (essay II.I: a contract says "what it promises
    # to return"; primitive audit F9). None promises only a JSON object. A checkpoint
    # written before this field restores None.
    returns_schema: dict | None = None


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
            if tool.returns_schema is not None:
                # A result that breaks the tool's published promise never reaches the
                # caller as if it kept it: the caller composed against the contract.
                broken = _check_object(tool.returns_schema, output, "result",
                                       additional_default=True)
                if broken is not None:
                    return {"error": f"result breaks returns_schema: {broken}"}
            return output
        except NoJail:
            return {"error": "no jail on this host"}
        except Exception:
            # Launch, decoding and malformed tool failures stay within the
            # result protocol without exposing host exception details.
            return {"error": "tool execution failed"}


_OBSERVATION_HARNESS = """

import json as _json, math as _math, sys as _sys
_facts = _json.loads(_sys.stdin.read())
_value = observe(_facts)
if _value is None:
    print(_json.dumps({"value": None}))
else:
    _value = float(_value)
    if not _math.isfinite(_value):
        raise ValueError("observation must be finite")
    print(_json.dumps({"value": _value}))
"""


class ObservationRunner:
    """One registered observation, measured under the tool jail's own limits.

    The population supplies a module defining ``observe(facts)``; the facts are
    the public per-window facts as JSON on stdin. The result is ``(value, error)``:
    a finite float, or ``None`` with the reason it could not be measured. Nothing
    here raises, and no host detail reaches the reason.
    """

    def __init__(self, *, timeout_s: int | None = None, cpu_s: int | None = None) -> None:
        from factorylab.runtime.observations import OBSERVATION_CPU_S, OBSERVATION_TIMEOUT_S

        self.timeout_s = OBSERVATION_TIMEOUT_S if timeout_s is None else timeout_s
        self.cpu_s = OBSERVATION_CPU_S if cpu_s is None else cpu_s
        self.available = jail_available()

    def run(self, code: str, facts: dict) -> tuple[float | None, str | None]:
        """Return the observed value, or None and the reason there is none."""
        try:
            if not self.available:
                return None, "no jail on this host"
            try:
                stdin = json.dumps(facts, allow_nan=False)
            except (TypeError, ValueError, RecursionError):
                return None, "window facts are not JSON serializable"
            result = run_python(
                code + _OBSERVATION_HARNESS,
                stdin=stdin,
                timeout_s=self.timeout_s,
                cpu_s=self.cpu_s,
                max_output_bytes=2000,
            )
            # Jail wrappers may encode a signal as 128 + signal instead of -signal.
            if result.timed_out or result.returncode in (
                -signal.SIGKILL, -signal.SIGXCPU, 128 + signal.SIGKILL, 128 + signal.SIGXCPU,
            ):
                return None, "timeout"
            if result.returncode != 0:
                return None, f"exit {result.returncode}: {result.stderr.strip()[-200:]}"
            try:
                output = json.loads(result.stdout, parse_constant=_reject_constant)
            except (ValueError, RecursionError):
                return None, "observation printed something other than its value"
            if not isinstance(output, dict) or "value" not in output:
                return None, "observation printed something other than its value"
            value = output["value"]
            if value is None:
                return None, "unsupported: observe returned None"
            return float(value), None
        except NoJail:
            return None, "no jail on this host"
        except Exception:
            return None, "observation execution failed"


def _reject_constant(value: str) -> None:
    raise ValueError("non-JSON numeric constant")


_TYPES = {
    "string": (str,),
    "number": (int, float),
    "integer": (int,),
    "boolean": (bool,),
    "array": (list,),
    "object": (dict,),
}


def object_schema_error(schema: object) -> str | None:
    """Why ``schema`` is not a top-level object schema this runner enforces, or None.

    Guarantees the accepted subset is exactly what ``_check_object`` checks: type
    object, properties naming one supported type each, a list of required names
    and a boolean additionalProperties. Nested constraints are outside it.
    """
    if (
        not isinstance(schema, dict)
        or schema.get("type") != "object"
        or not isinstance(schema.get("properties"), dict)
    ):
        return "expected object with properties"
    for name, prop in schema["properties"].items():
        if (
            not isinstance(name, str)
            or not isinstance(prop, dict)
            or not isinstance(prop.get("type"), str)
            or prop["type"] not in _TYPES
        ):
            return "properties must name supported types"
    required = schema.get("required", [])
    if not isinstance(required, list) or any(not isinstance(name, str) for name in required):
        return "required must be a list of strings"
    if type(schema.get("additionalProperties", False)) is not bool:
        return "additionalProperties must be a boolean"
    return None


def _check_object(schema: dict, value: object, what: str, *,
                  additional_default: bool) -> str | None:
    """Why ``value`` does not satisfy the object schema, or None when it does."""
    error = object_schema_error(schema)
    if error is not None:
        return f"invalid {what} schema: {error}"
    properties = schema["properties"]
    additional = schema.get("additionalProperties", additional_default)
    if not isinstance(value, dict) or any(not isinstance(name, str) for name in value):
        return f"invalid {what}: expected an object with string keys"
    for name in schema.get("required", []):
        if name not in value:
            return f"invalid {what}: missing required property {name}"
    for name, item in value.items():
        if name not in properties:
            if not additional:
                return f"invalid {what}: additional property {name}"
        elif type(item) not in _TYPES[properties[name]["type"]]:
            return f"invalid {what}: property {name} must be {properties[name]['type']}"
    return None


def _validate_args(schema: dict, args: dict) -> str | None:
    error = object_schema_error(schema)
    if error is not None:
        return f"invalid args_schema: {error}"
    return _check_object(schema, args, "args", additional_default=False)


def connector_spec() -> dict:
    """The fetch primitive publishes only an id and a path; the call itself is free.

    A GET of a public origin pays no one, so it carries no price (the wallet moves
    only when money moves). A paid source's price is the seller's own, debited when
    it is bought.
    """
    return {
        "id": "connector.fetch", "description": "GET a registered connector path as text",
        "kind": "connector", "price_micro_per_call": 0,
        "args_schema": {"type": "object", "properties": {
            "id": {"type": "string"}, "path": {"type": "string"}},
            "required": ["id", "path"], "additionalProperties": False},
    }


def web_search_spec(max_call_usd: str) -> dict:
    """The search primitive publishes a query, a result count and its ceiling.

    Its cost is what the route's provider bills for the one model call it makes,
    and nothing on top of it: no one else is paid.
    """
    return {
        "id": "web.search",
        "description": (
            "Search the web through this world's search-capable model route. Returns a "
            "bounded list of results, each {title, url, snippet, published?}, with the "
            "cost of the search and when it was run. Costs the metered cost of that one "
            f"model call, and never more than ${max_call_usd}."
        ),
        "kind": "web", "price_micro_per_call": 0,
        "args_schema": {"type": "object", "properties": {
            "query": {"type": "string"}, "max_results": {"type": "integer"}},
            "required": ["query"], "additionalProperties": False,
            # Every published tool carries examples its own schema accepts (B1). They
            # are placeholders on purpose: an example topic is a suggested plan
            # (smuggling audit D5).
            "examples": [{"query": "<query>"},
                         {"query": "<query>", "max_results": 3}]},
    }


def calc_spec(price_micro_per_call: int) -> dict:
    """The deterministic arithmetic primitive, at the price a world commits to it.

    GPT-6's third reading, §7. The published contract lives beside the
    arithmetic in ``factorylab.cortex.calc`` so the two cannot drift; this only
    stamps the price, which every world in this repository commits at zero.
    """
    from factorylab.cortex.calc import CALC_SPEC

    if type(price_micro_per_call) is not int or price_micro_per_call < 0:
        raise ValueError("price_micro_per_call must be a non-negative int")
    return {**deepcopy(CALC_SPEC), "price_micro_per_call": price_micro_per_call}


def as_spec(tool: PopulationTool, price_micro_per_call: int) -> dict:
    """Return public ToolSpec fields without source, provenance or schema aliases."""
    if type(price_micro_per_call) is not int or price_micro_per_call < 0:
        raise ValueError("price_micro_per_call must be a non-negative int")
    return {
        "id": tool.id,
        "description": tool.description,
        "args_schema": deepcopy(tool.args_schema),
        **({"returns_schema": deepcopy(tool.returns_schema)}
           if tool.returns_schema is not None else {}),
        "price_micro_per_call": price_micro_per_call,
        "kind": "population",
    }
