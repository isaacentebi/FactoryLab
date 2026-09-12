"""Assemblies: ephemeral executors built from registered parts.

An ``AssemblySpec`` names a model capability, a system prompt, tool ids, and a
memory policy. Invoking it is stateless: one request in, one metered model
call, one ``Return`` out. Nothing persists on the assembly between calls; if
a memory policy is set, the memory lives in the registry-controlled store the
runtime passes in, keyed by handle scope.

The system prompt is world-supplied. It must not describe kernel rules (the
kernel enforces them); it describes only how to answer a request.
"""

from __future__ import annotations

import json
import math
from collections.abc import Callable
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from factorylab.cortex.request import ChildRequest, Request, Return
from factorylab.world.metering import BillingUncertain, Infeasible, MeteredModel
from factorylab.world.models import ModelRequest

SEED_SYSTEM_PROMPT = (
    "You receive one request. Reply with a single JSON object that satisfies the "
    "outcome schema. If the request cannot be completed, reply with a JSON object "
    'containing "status": "cannot" and "reason". Optionally include a "requests" '
    "array (up to two), each with target (an assembly id or \"self\"), description, inputs "
    "and outcome_schema. Their outputs arrive as tool_results in a second call. "
    "Child invocations answer once; they cannot request further invocations."
)


@dataclass(frozen=True)
class AssemblySpec:
    id: str
    version: int
    model_id: str
    system_prompt: str = SEED_SYSTEM_PROMPT
    tool_ids: tuple[str, ...] = ()
    memory_policy: str = "none"  # "none" | "handle-scoped"
    max_tokens: int = 2048
    effort: str = "medium"
    accepts: frozenset[str] = frozenset({"Tick", "MarketMid", "Funding", "Fill", "Verdict"})
    role: str = "producer"  # "producer" | "evaluator" | "meta"

    def __post_init__(self) -> None:
        if self.memory_policy not in ("none", "handle-scoped"):
            raise ValueError("unknown memory policy")
        if self.role not in ("producer", "evaluator", "meta", "antagonist"):
            raise ValueError("unknown assembly role")
        if self.max_tokens <= 0:
            raise ValueError("max_tokens must be positive")


@dataclass
class Assembly:
    """Executes requests through a metered model.

    Guarantees: the model is invoked at most once per request; the returned
    ``cost`` is exactly what the wallet was charged; a model refusal, a
    malformed reply, or an infeasible reservation each produce a ``Return``
    with the matching status rather than an exception, so the router always
    gets an addressable outcome for the handle it opened.
    """

    spec: AssemblySpec
    model: MeteredModel
    memory: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    child_factory: Callable[[Request, dict[str, Any], int], Request] | None = None
    validator: Callable[[dict[str, Any], Request], None] | None = None

    def build_model_request(self, req: Request) -> ModelRequest:
        messages: list[dict[str, Any]] = []
        if self.spec.memory_policy == "handle-scoped" and req.parent_handle:
            messages.extend(self.memory.get(req.parent_handle, []))
        messages.append({"role": "user", "content": req.prompt_text()})
        return ModelRequest(
            model_id=self.spec.model_id,
            system=self.spec.system_prompt,
            messages=tuple(messages),
            max_tokens=self.spec.max_tokens,
            effort=self.spec.effort,
        )

    def invoke(self, req: Request) -> Return:
        try:
            mreq = self.build_model_request(req)
            ceiling = self.model.ceiling(mreq)
            if ceiling > req.cost_ceiling:
                return Return(
                    req.handle, {"reason": "ceiling exceeds request cost_ceiling"}, 0, "failed"
                )
            metered = self.model.complete(mreq, handle=req.handle)
        except Infeasible as exc:
            return Return(req.handle, {"reason": f"infeasible: {exc}"}, 0, "failed")
        except BillingUncertain as exc:
            return Return(req.handle, {"reason": str(exc)}, exc.cost, "failed")
        except Exception as exc:
            return Return(req.handle, {"reason": type(exc).__name__}, 0, "failed")
        resp = metered.result
        cost = metered.cost
        if (not isinstance(resp.text, str) or not isinstance(resp.model_id, str)
                or not isinstance(resp.stop_reason, str) or type(resp.refused) is not bool):
            return Return(req.handle, {"reason": "invalid response metadata"}, cost, "malformed")
        if resp.refused:
            return Return(
                req.handle, {"reason": "refused"}, cost, "refused", served_by=resp.model_id
            )
        parsed = _parse_json_object(resp.text)
        if parsed is not None:
            try:
                _validate_return(parsed, req.outcome_schema)
                if self.validator is not None:
                    self.validator(parsed, req)
            except (ValueError, TypeError, ArithmeticError, RecursionError):
                parsed = None
        if parsed is None:
            return Return(
                req.handle,
                {"raw": resp.text},
                cost,
                "malformed",
                served_by=resp.model_id,
                stop_reason=resp.stop_reason,
            )
        if self.spec.memory_policy == "handle-scoped":
            scope = req.parent_handle or req.handle
            self.memory.setdefault(scope, []).extend(
                [
                    {"role": "user", "content": req.prompt_text()},
                    {"role": "assistant", "content": resp.text},
                ]
            )
        children = self._children(req, parsed)
        raw_calls = parsed.get("tool_calls")
        tool_calls = (
            tuple(c for c in raw_calls if isinstance(c, dict) and isinstance(c.get("tool"), str))[
                :4
            ]
            if isinstance(raw_calls, list)
            else ()
        )
        outputs = {k: v for k, v in parsed.items() if k not in ("requests", "tool_calls")}
        return Return(
            req.handle,
            outputs,
            cost,
            "ok",
            children=children,
            served_by=resp.model_id,
            stop_reason=resp.stop_reason,
            tool_calls=tool_calls,
        )

    def _children(self, req: Request, parsed: dict[str, Any]) -> tuple[ChildRequest | Request, ...]:
        raw = parsed.get("requests")
        if not isinstance(raw, list):
            return ()
        if self.child_factory is not None:
            return tuple(self.child_factory(req, item, i) for i, item in enumerate(raw))
        return tuple(ChildRequest(item["target"], item["description"], item["inputs"],
                                  item["outcome_schema"]) for item in raw)


def _parse_json_object(text: str) -> dict[str, Any] | None:
    """Return the first JSON object in ``text`` or None. Tolerates code fences."""
    if not isinstance(text, str):
        return None
    decoder = json.JSONDecoder()
    for start, char in enumerate(text):
        if char != "{":
            continue
        try:
            obj, _ = decoder.raw_decode(text, start)
        except (ValueError, RecursionError):
            continue
        if isinstance(obj, dict):
            try:
                _finite_json(obj)
            except (ValueError, RecursionError):
                return None
            return obj
    return None


def _finite_json(value: Any) -> None:
    """Reject non-finite JSON numbers at every depth, including unused extension fields."""
    if type(value) is float and not math.isfinite(value):
        raise ValueError("nonfinite model number")
    if isinstance(value, dict):
        for item in value.values():
            _finite_json(item)
    elif isinstance(value, list):
        for item in value:
            _finite_json(item)


def _validate_schema(value: Any, schema: dict, *, partial: bool = False) -> None:
    """Enforce the supported JSON-schema types, required fields, enums and numeric bounds."""
    if not isinstance(schema, dict):
        raise ValueError("schema must be an object")
    if "anyOf" in schema:
        for alternative in schema["anyOf"]:
            try:
                _validate_schema(value, alternative, partial=partial)
                break
            except (ValueError, TypeError):
                pass
        else:
            raise ValueError("no matching alternative")
    kind = schema.get("type")
    types = {"object": dict, "array": list, "string": str, "boolean": bool,
             "integer": int, "number": (int, float), "null": type(None)}
    if kind is not None and (kind not in types or not isinstance(value, types[kind])
                             or kind in ("number", "integer") and isinstance(value, bool)):
        raise ValueError("wrong field type")
    if "enum" in schema and not any(type(value) is type(v) and value == v
                                    for v in schema["enum"]):
        raise ValueError("field is outside enum")
    if type(value) in (int, float):
        for key, invalid in (("minimum", lambda b: value < b),
                             ("maximum", lambda b: value > b),
                             ("exclusiveMinimum", lambda b: value <= b),
                             ("exclusiveMaximum", lambda b: value >= b)):
            if key in schema and invalid(schema[key]):
                raise ValueError("number out of range")
    if isinstance(value, dict):
        if not partial and any(k not in value for k in schema.get("required", [])):
            raise ValueError("required field absent")
        properties = schema.get("properties", {})
        for key, item in value.items():
            if key in properties:
                _validate_schema(item, properties[key])
            elif schema.get("additionalProperties") is False:
                raise ValueError("unexpected field")
            elif isinstance(schema.get("additionalProperties"), dict):
                _validate_schema(item, schema["additionalProperties"])
    if isinstance(value, list):
        if (len(value) < schema.get("minItems", 0)
                or len(value) > schema.get("maxItems", len(value))):
            raise ValueError("array length out of range")
        for item in value:
            _validate_schema(item, schema.get("items", {}))


def _validate_return(parsed: dict, schema: dict) -> None:
    """Validate the entire reply before any memory, child, tool, forecast or proposal effect."""
    properties = {k: {"type": "string"} for k in
                  ("action", "rationale", "reason", "status", "coin", "side")}
    properties.update({k: {"type": "number", "minimum": 0, "maximum": 1}
                       for k in ("verdict", "conformity")})
    properties.update({
        "vote": {"type": "boolean"},
        "register": {"type": "array", "items": {"type": "object"}},
        "tool_calls": {"type": "array", "maxItems": 4, "items": {
            "type": "object", "properties": {"tool": {"type": "string"},
                                                "args": {"type": "object"}},
            "required": ["tool", "args"]}},
        "requests": {"type": "array", "maxItems": 2, "items": {
            "type": "object", "properties": {
                "target": {"type": "string"}, "description": {"type": "string"},
                "inputs": {"type": "object"}, "outcome_schema": {"type": "object"}},
            "required": ["target", "description", "inputs", "outcome_schema"]}},
        "forecasts": {"type": "array", "items": {"type": "object", "properties": {
            "predicate": {"type": "string"},
            "q": {"type": "number", "minimum": 0, "maximum": 1},
            "params": {"type": "object", "properties": {
                "horizon_events": {"type": "integer", "minimum": 1, "maximum": 200}}}},
            "required": ["predicate", "q", "params"]}},
    })
    _validate_schema(parsed, {"type": "object", "properties": properties})
    if parsed.get("action") == "order":
        _validate_schema(parsed, {"properties": {
            "side": {"enum": ["buy", "sell"]}}, "required": ["coin", "size"]})
        _positive_wire_decimal(parsed["size"])
    # Tool/child requests may precede the final answer, but fields already supplied are typed.
    continuation = bool(parsed.get("tool_calls") or parsed.get("requests"))
    cannot = parsed.get("status") == "cannot" and isinstance(parsed.get("reason"), str)
    _validate_schema(parsed, schema, partial=continuation or cannot)
    for proposal in parsed.get("register", []):
        fields = {k: {"type": "string"} for k in (
            "kind", "id", "model_id", "openrouter_id", "role", "system_prompt", "effort",
            "event_kind", "learner", "description", "code", "predicted_effect", "tick_interval")}
        fields.update({"gamma": {"type": "number", "minimum": 1e-300, "maximum": 1},
                       "max_tokens": {"type": "integer", "minimum": 16, "maximum": 4096},
                       "timeout_s": {"type": "integer", "minimum": 1, "maximum": 5},
                       "accepts": {"type": "array", "items": {"type": "string"}},
                       "args_schema": {"type": "object"}})
        _validate_schema(proposal, {"properties": fields, "required": ["kind"]})
        if proposal["kind"] == "router" and "add" in proposal:
            add = proposal["add"]
            if not isinstance(add, bool) and not (isinstance(add, str)
                                                  and add.lower() in ("true", "false")):
                raise ValueError("add must be a boolean")
        if proposal["kind"] == "amendment":
            _validate_schema(proposal, {"properties": {
                "add": {"type": "array", "items": {"type": "object"}},
                "replace": {"type": "array", "items": {"type": "object"}},
                "remove": {"type": "array", "items": {"type": "string"}}}})
            for card in proposal.get("add", []) + proposal.get("replace", []):
                _validate_schema(card, {"properties": {
                    **{k: {"type": "string"} for k in (
                        "id", "norm", "description", "units", "window", "acceptable_region",
                        "observation", "answers_for")},
                    "lambda": {"type": "number", "minimum": 0}}})
    for child in parsed.get("requests", []):
        if not child["target"] or not child["description"].strip():
            raise ValueError("child needs target and description")
        if any(k in child["inputs"] for k in ("author", "author_id", "requester", "lineage")):
            raise ValueError("child inputs contain author metadata")
        _schema_definition(child["outcome_schema"])


def _positive_wire_decimal(value: Any) -> None:
    """Model amounts must be positive, finite, and representable by the venue wire format."""
    if type(value) not in (str, int, float):
        raise ValueError("invalid decimal amount")
    number = Decimal(str(value))
    if (not number.is_finite() or number <= 0 or not math.isfinite(float(number))
            or float(number) == 0):
        raise ValueError("decimal amount out of range")


def _schema_definition(schema: Any) -> None:
    """Composition schemas are type-checked and cannot silently request unsupported constraints."""
    if not isinstance(schema, dict):
        raise ValueError("schema must be an object")
    allowed = {"type", "properties", "required", "enum", "minimum", "maximum", "minItems",
               "maxItems", "additionalProperties", "items", "description", "title", "default",
               "anyOf", "exclusiveMinimum", "exclusiveMaximum"}
    if schema.keys() - allowed:
        raise ValueError("unsupported child outcome schema keyword")
    if "type" in schema and schema["type"] not in (
        "object", "array", "string", "boolean", "integer", "number", "null"
    ):
        raise ValueError("unsupported schema type")
    for key in ("minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum"):
        if key in schema and type(schema[key]) not in (int, float):
            raise ValueError("schema bound must be a number")
    for key in ("minItems", "maxItems"):
        if key in schema and (type(schema[key]) is not int or schema[key] < 0):
            raise ValueError("schema item bound must be a nonnegative integer")
    if "required" in schema and (not isinstance(schema["required"], list)
                                 or any(not isinstance(k, str) for k in schema["required"])):
        raise ValueError("schema required must contain strings")
    if "enum" in schema and not isinstance(schema["enum"], list):
        raise ValueError("schema enum must be an array")
    if "properties" in schema:
        if not isinstance(schema["properties"], dict):
            raise ValueError("schema properties must be an object")
        for prop in schema["properties"].values():
            _schema_definition(prop)
    if "items" in schema:
        _schema_definition(schema["items"])
    if "additionalProperties" in schema and type(schema["additionalProperties"]) is not bool:
        _schema_definition(schema["additionalProperties"])
    if "anyOf" in schema:
        if not isinstance(schema["anyOf"], list) or not schema["anyOf"]:
            raise ValueError("anyOf must contain schemas")
        for alternative in schema["anyOf"]:
            _schema_definition(alternative)
