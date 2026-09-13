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
from dataclasses import dataclass, field, replace
from decimal import Decimal
from typing import Any

from factorylab.cortex.registration import output_contracts, seed_emits
from factorylab.cortex.request import ChildRequest, Request, Return
from factorylab.kernel.ledger import utf8_text
from factorylab.world.metering import BillingUncertain, Infeasible, MeteredModel
from factorylab.world.models import ModelRequest

SEED_SYSTEM_PROMPT = (
    "You receive one request. Reply with a single JSON object that satisfies the "
    "outcome schema. If the request cannot be completed, reply with a JSON object "
    'containing "status": "cannot" and "reason". '
    'A return may also carry "register" proposals and "requests" for work from other '
    "assemblies. Both are bounded, and whatever is not admitted comes back with a public "
    "reason. The world input is what you know about this world."
)


@dataclass(frozen=True)
class AssemblySpec:
    id: str
    version: int
    model_id: str
    system_prompt: str = SEED_SYSTEM_PROMPT
    # Not read at runtime — an assembly's tools come from the registry — but every
    # snapshot serialises this dataclass field by field, so it is part of the
    # recorded evidence and of the resume format. Removing it changes the diary.
    tool_ids: tuple[str, ...] = ()
    memory_policy: str = "none"  # "none" | "handle-scoped"
    max_tokens: int = 2048
    effort: str = "medium"
    accepts: frozenset[str] = frozenset({"Tick", "MarketMid", "Funding", "Fill", "Verdict"})
    role: str = "producer"  # descriptive label; dispatch depends only on accepts/emits
    emits: tuple[str, ...] | None = None
    schemas: dict[str, dict] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.memory_policy not in ("none", "handle-scoped"):
            raise ValueError("unknown memory policy")
        if not isinstance(self.role, str) or not self.role.strip():
            raise ValueError("assembly role must be a nonempty label")
        emits, schemas = output_contracts(
            self.emits if self.emits is not None else seed_emits(self.role), self.schemas)
        object.__setattr__(self, "emits", emits)
        object.__setattr__(self, "schemas", schemas)
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
        provider = _provider_report(resp, mreq.max_tokens)
        if resp.refused:
            return Return(
                req.handle, {"reason": "refused"}, cost, "refused", served_by=resp.model_id,
                provider=provider,
            )
        # Reply bytes outside UTF-8 (an emoji cut by max_tokens) are seen exactly as the
        # journal can store them, so a live call and its replay parse the same text.
        resp = replace(resp, text=utf8_text(resp.text))
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
                provider=provider,
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
            tuple(c for c in raw_calls if isinstance(c, dict) and isinstance(c.get("tool"), str))
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
            provider=provider,
        )

    def _children(self, req: Request, parsed: dict[str, Any]) -> tuple[ChildRequest | Request, ...]:
        raw = parsed.get("requests")
        if not isinstance(raw, list):
            return ()
        if self.child_factory is not None:
            return tuple(self.child_factory(req, item, i) for i, item in enumerate(raw))
        return tuple(ChildRequest(item["target"], item["description"], item["inputs"],
                                  item["outcome_schema"]) for item in raw)


def _provider_report(resp: Any, max_tokens: int) -> dict[str, Any]:
    """Return the provider's own account of the completion; unreported fields are None."""
    raw = resp.raw if isinstance(raw := getattr(resp, "raw", None), dict) else {}
    reasoning = raw.get("reasoning_tokens")
    return {
        "finish_reason": resp.stop_reason,
        "input_tokens": resp.input_tokens if type(resp.input_tokens) is int else None,
        "output_tokens": resp.output_tokens if type(resp.output_tokens) is int else None,
        "reasoning_tokens": reasoning if type(reasoning) is int else None,
        "max_tokens": max_tokens,
    }


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
                _utf8_json(obj)
            except (ValueError, RecursionError):
                return None
            return obj
    return None


def _utf8_json(value: Any) -> None:
    """Reject strings the JSON escape syntax admits but UTF-8 cannot carry (lone surrogates)."""
    if isinstance(value, str):
        value.encode("utf-8")
    elif isinstance(value, dict):
        for key, item in value.items():
            _utf8_json(key)
            _utf8_json(item)
    elif isinstance(value, list):
        for item in value:
            _utf8_json(item)


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


def validate_schema(value: Any, schema: dict, *, partial: bool = False) -> None:
    """Enforce the supported JSON-schema types, required fields, enums and numeric bounds."""
    if not isinstance(schema, dict):
        raise ValueError("schema must be an object")
    if "anyOf" in schema:
        for alternative in schema["anyOf"]:
            try:
                validate_schema(value, alternative, partial=partial)
                break
            except (ValueError, TypeError):
                pass
        else:
            raise ValueError("no matching alternative")
    kind = schema.get("type")
    types = {"object": dict, "array": list, "string": str, "boolean": bool,
             "integer": int, "number": (int, float), "null": type(None)}
    kinds = kind if isinstance(kind, list) else [kind]
    if kind is not None and not any(
        isinstance(k, str) and k in types and isinstance(value, types[k])
        and not (k in ("number", "integer") and isinstance(value, bool)) for k in kinds
    ):
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
                validate_schema(item, properties[key])
            elif schema.get("additionalProperties") is False:
                raise ValueError("unexpected field")
            elif isinstance(schema.get("additionalProperties"), dict):
                validate_schema(item, schema["additionalProperties"])
    if isinstance(value, list):
        if (len(value) < schema.get("minItems", 0)
                or len(value) > schema.get("maxItems", len(value))):
            raise ValueError("array length out of range")
        for item in value:
            validate_schema(item, schema.get("items", {}))


def reserved_return_fields(*, max_children: int | None = None,
                           max_tool_calls: int | None = None) -> dict:
    """Publish the same reserved names and types enforced on every return."""
    properties = {k: {"type": "string"} for k in
                  ("action", "rationale", "reason", "status", "coin", "side", "emits",
                   "about_handle")}
    properties.update({k: {"type": "number", "minimum": 0, "maximum": 1}
                       for k in ("verdict", "payoff", "conformity")})
    properties.update({
        "vote": {"type": "boolean"},
        # The deciding agent's own distribution over its own actions.
        "propensity": {"type": "object"},
        "register": {"type": "array"},
        "tool_calls": {"type": "array", "items": {
            "type": "object", "properties": {"tool": {"type": "string"},
                                                "args": {"type": "object"}},
            "required": ["tool", "args"]}},
        "requests": {"type": "array", "items": {
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
    if max_children is not None:
        properties["requests"]["maxItems"] = max_children
    if max_tool_calls is not None:
        properties["tool_calls"]["maxItems"] = max_tool_calls
    return properties


def _validate_return(parsed: dict, schema: dict) -> None:
    """Validate reply effects; each registration is admitted independently by the runtime."""
    properties = reserved_return_fields()
    validate_schema(parsed, {"type": "object", "properties": properties})
    if parsed.get("action") == "order":
        validate_schema(parsed, {"properties": {
            "side": {"enum": ["buy", "sell"]}}, "required": ["coin", "size"]})
        positive_wire_decimal(parsed["size"])
    # Tool/child requests may precede the final answer, but fields already supplied are typed.
    continuation = bool(parsed.get("tool_calls") or parsed.get("requests"))
    cannot = parsed.get("status") == "cannot" and isinstance(parsed.get("reason"), str)
    validate_schema(parsed, schema, partial=continuation or cannot)
    for child in parsed.get("requests", []):
        if not child["target"] or not child["description"].strip():
            raise ValueError("child needs target and description")
        if any(k in child["inputs"] for k in ("author", "author_id", "requester", "lineage")):
            raise ValueError("child inputs contain author metadata")
        _schema_definition(child["outcome_schema"])


def validate_proposal(proposal: dict) -> None:
    """Reject malformed proposal fields before any registration effect."""
    fields = {k: {"type": "string"} for k in (
        "kind", "id", "model_id", "openrouter_id", "role", "system_prompt", "effort",
        "event_kind", "learner", "description", "code", "tick_interval",
        "unit", "assembly_id")}
    fields.update({"gamma": {"type": "number", "minimum": 1e-300, "maximum": 1},
                   "max_tokens": {"type": "integer", "minimum": 16, "maximum": 4096},
                   "timeout_s": {"type": "integer", "minimum": 1, "maximum": 5},
                   "accepts": {"type": "array", "items": {"type": "string"}},
                   "emits": {"type": "array", "items": {"type": "string"}},
                   "schemas": {"type": "object"},
                   "range": {"type": "array", "items": {"type": "number"}},
                   "actions": {"type": "array", "items": {"type": "string"}},
                   "args_schema": {"type": "object"}})
    validate_schema(proposal, {"type": "object", "properties": fields, "required": ["kind"]})
    if proposal["kind"] == "router" and "add" in proposal:
        add = proposal["add"]
        if not isinstance(add, bool) and not (isinstance(add, str)
                                              and add.lower() in ("true", "false")):
            raise ValueError("add must be a boolean")
    if proposal["kind"] == "amendment":
        from factorylab.charter.amendment import effect_schema
        from factorylab.charter.windows import window_schema

        validate_schema(proposal, {"properties": {
            "predicted_effect": effect_schema(),
            "add": {"type": "array", "items": {"type": "object"}},
            "replace": {"type": "array", "items": {"type": "object"}},
            "remove": {"type": "array", "items": {"type": "string"}}}})
        for card in proposal.get("add", []) + proposal.get("replace", []):
            validate_schema(card, {"properties": {
                **{k: {"type": "string"} for k in (
                    "id", "norm", "description", "units", "acceptable_region",
                    "observation", "answers_for")},
                "window": window_schema(),
                "lambda": {"type": "number", "minimum": 0}}})


def positive_wire_decimal(value: Any) -> None:
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
