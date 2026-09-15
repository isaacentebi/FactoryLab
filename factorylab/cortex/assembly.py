"""Assemblies: ephemeral executors built from registered parts.

An ``AssemblySpec`` names a model capability, a system prompt, tool ids, and a
memory policy. Invoking it is stateless: one request in, one metered model
call, one ``Return`` out. Nothing persists on the assembly between calls; if
a memory policy is set, the memory lives in the registry-controlled store the
runtime passes in, keyed by handle scope.

The system prompt is world-supplied. It must not describe kernel rules (the
kernel enforces them); it describes only how to answer a request. The system
*message* is that prompt and nothing else. Everything the kernel discloses about
the world — the charter text, the tool, observation, work and metric catalogues —
is population-authored the moment a member registers into it, so it travels in
the user message, where it is material to read rather than an instruction that
outranks the assembly's own. An assembly that writes a tool description is
writing to its peers' inputs, never to their system role.
"""

from __future__ import annotations

import json
import math
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from decimal import Decimal
from typing import Any

from factorylab.cortex.registration import output_contracts, reward_contracts, seed_emits
from factorylab.cortex.request import ChildRequest, Request, Return
from factorylab.cortex.sandbox import MAX_PROGRAM_TIMEOUT_S
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
        """Render the exact prompt this assembly will be billed for.

        The executor's own id is stamped here, where the prompt is rendered, and
        nowhere else: an identity added after a caller has priced the request
        would make every ceiling derived from that request — the metered
        ceiling, the router's affordability check — smaller than the prompt
        actually sent. Stamping it here also means a parent cannot forge its
        child's identity: whatever ``inputs`` carried, the assembly overwrites it
        with its own.

        Guarantees the system message is exactly ``spec.system_prompt``: no
        population-authored text ever reaches the system role. The registered
        catalogues carry prose their authors chose — a tool's description, an
        observation's, a predicate's, a metric card's, the charter's norms — and
        in the system role, shared by every later call, that prose would be a
        standing instruction one member wrote for the rest of the population.
        The prompt's stable prefix (``Request.stable_prefix``) therefore heads
        this user message, which is where the world block belongs and where it
        reads as material.

        The cache hit that placement keeps is the per-assembly one, which is
        where the volume is: this assembly's system text is a constant, so its
        own consecutive calls open with the identical ``system`` message
        followed by the identical stable block, which is all DeepSeek's and
        OpenAI's automatic prompt caching asks for — they key on an identical
        leading token sequence and need no ``cache_control`` marker, so none is
        sent. Handle-scoped memory, when a world registers it, is the one thing
        that precedes the block and costs that assembly the hit.
        """
        req = replace(req, inputs={**req.inputs, "you": self.spec.id})
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
            json_object=True,
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
        return _children(req, parsed, self.child_factory)


def _children(req: Request, parsed: dict[str, Any],
              child_factory: Callable[[Request, dict[str, Any], int], Request] | None,
              ) -> tuple[ChildRequest | Request, ...]:
    raw = parsed.get("requests")
    if not isinstance(raw, list):
        return ()
    if child_factory is not None:
        return tuple(child_factory(req, item, i) for i, item in enumerate(raw))
    return tuple(ChildRequest(item["target"], item["description"], item["inputs"],
                              item["outcome_schema"]) for item in raw)


# --- programs as seats (contract C8) -----------------------------------------

PROGRAM_MODEL_ID = "program"
MAX_PROGRAM_CODE_CHARS = 16_000
MAX_PROGRAM_STATE_BYTES = 65_536
PROGRAM_STATE_POLICIES = ("none", "private")


@dataclass(frozen=True)
class ProgramAssemblySpec(AssemblySpec):
    """A seat whose executor is population Python in the jail rather than a model.

    ``code`` reads one JSON object from stdin — ``prompt`` (the rendered request,
    exactly what a model would read), ``description``, ``inputs``,
    ``outcome_schema`` and ``state`` — and prints the same Return JSON a model
    would. With ``state_policy = "private"`` the object it prints under
    ``state`` is archived as an artifact owned by this seat and handed back on
    its next call; the archive is versioned by content hash, one artifact per
    call that changes it. ``reward_shapes`` carries admitted shapes for custom
    emitted kinds, as ``WorkAssemblySpec`` does for model seats.
    """

    code: str = ""
    timeout_s: int = 10
    state_policy: str = "none"
    reward_shapes: dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        super().__post_init__()
        if self.model_id != PROGRAM_MODEL_ID:
            raise ValueError("a program seat's model_id is program")
        if not isinstance(self.code, str) or not self.code.strip():
            raise ValueError("a program seat needs code")
        if len(self.code) > MAX_PROGRAM_CODE_CHARS:
            raise ValueError(f"code exceeds {MAX_PROGRAM_CODE_CHARS} chars")
        if type(self.timeout_s) is not int or not 1 <= self.timeout_s <= MAX_PROGRAM_TIMEOUT_S:
            raise ValueError(f"timeout_s must be an int in [1, {MAX_PROGRAM_TIMEOUT_S}]")
        if self.state_policy not in PROGRAM_STATE_POLICIES:
            raise ValueError("state_policy must be none or private")
        object.__setattr__(self, "reward_shapes", reward_contracts(self.emits, self.reward_shapes))


@dataclass(frozen=True)
class _ProgramPrice:
    """What routing asks a seat's model: the ceiling of one call. A program's is flat."""

    micro_per_call: int

    def ceiling(self, req: Any) -> int:
        return self.micro_per_call


@dataclass
class ProgramAssembly:
    """Executes requests by running the seat's program in the jail.

    Guarantees, matching ``Assembly``: the program runs at most once per
    request; ``cost`` is exactly what the wallet was charged, which is the flat
    ``price`` reserved and committed through the meter under the reason
    ``model:program``, so every call is a wallet transaction and the novelty
    reserve treats it as this seat's own compute; every failure — no jail, a
    non-zero exit, a wall timeout, a reply that is not the Return JSON the
    validator accepts — is a ``Return`` with status ``malformed`` (or ``failed``
    when nothing ran), never an exception. Private state is loaded from and
    saved to the artifact archive around the call, and the hash of the state
    the call left behind is ledgered (``program.call``) before the Return
    leaves, so the diary names the machinery's memory as well as its answer.
    """

    spec: ProgramAssemblySpec
    runner: Any  # ProgramRunner or its journal proxy: run(code, stdin=, timeout_s=) -> dict
    meter: Any  # Meter over the world wallet
    price: int
    artifacts: Any | None = None  # ArtifactStore; required for state_policy "private"
    memory: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    child_factory: Callable[[Request, dict[str, Any], int], Request] | None = None
    validator: Callable[[dict[str, Any], Request], None] | None = None
    record: Callable[[dict[str, Any]], Any] | None = None
    state_sha: str | None = None
    model: _ProgramPrice = field(init=False)

    def __post_init__(self) -> None:
        if type(self.price) is not int or self.price < 0:
            raise ValueError("program price must be a non-negative integer micro-USD")
        self.model = _ProgramPrice(self.price)

    def build_stdin(self, req: Request, state: Any) -> str:
        """Render what the program reads: the request as a model would see it, plus state."""
        req = replace(req, inputs={**req.inputs, "you": self.spec.id})
        return json.dumps({
            "prompt": req.prompt_text(),
            "description": req.description,
            "inputs": req.inputs,
            "outcome_schema": req.outcome_schema,
            "state": state,
        }, sort_keys=True, ensure_ascii=False)

    def _load_state(self) -> tuple[Any, str | None]:
        """The state the last successful call left, or None and why it could not be read."""
        if self.spec.state_policy != "private" or self.state_sha is None:
            return None, None
        if self.artifacts is None:
            return None, "no artifact archive"
        try:
            return json.loads(self.artifacts.get(self.state_sha).decode("utf-8")), None
        except Exception as exc:
            return None, type(exc).__name__

    def invoke(self, req: Request) -> Return:
        if self.price > req.cost_ceiling:
            return Return(req.handle, {"reason": "ceiling exceeds request cost_ceiling"}, 0,
                          "failed")
        state, state_error = self._load_state()
        if state_error is not None:
            # The seat has state and it cannot be read: the call does not run with
            # different memory (an empty one) and report ok. Nothing is billed; the
            # failure names why, and the last good state hash is kept for the
            # operator who restores the bytes (second reading, P1-02).
            if self.record is not None:
                self.record({
                    "kind": "program.call", "assembly_id": self.spec.id, "handle": req.handle,
                    "status": "failed", "cost": 0, "state_in": self.state_sha,
                    "state_out": self.state_sha, "state_error": state_error,
                })
            return Return(req.handle, {"reason": f"state unavailable: {state_error}"}, 0,
                          "failed")
        try:
            stdin = self.build_stdin(req, state)
        except (TypeError, ValueError) as exc:
            return Return(req.handle, {"reason": type(exc).__name__}, 0, "failed")
        code, timeout_s = self.spec.code, self.spec.timeout_s

        def execute() -> dict:
            return self.runner.run(code, stdin=stdin, timeout_s=timeout_s)

        try:
            metered = self.meter.run(
                handle=req.handle, reason=f"model:{PROGRAM_MODEL_ID}", ceiling=self.price,
                execute=execute, cost_of=lambda _r: self.price,
            )
        except Infeasible as exc:
            return Return(req.handle, {"reason": f"infeasible: {exc}"}, 0, "failed")
        except BillingUncertain as exc:
            return Return(req.handle, {"reason": str(exc)}, exc.cost, "failed")
        except Exception as exc:
            return Return(req.handle, {"reason": type(exc).__name__}, 0, "failed")
        cost = metered.cost
        result = metered.result if isinstance(metered.result, dict) else {}
        state_in = self.state_sha
        ret = self._interpret(req, result, cost, state_error)
        if self.record is not None:
            self.record({
                "kind": "program.call", "assembly_id": self.spec.id, "handle": req.handle,
                "status": ret.status, "cost": cost, "state_in": state_in,
                "state_out": self.state_sha,
            })
        return ret

    def _interpret(self, req: Request, result: dict, cost: int,
                   state_error: str | None) -> Return:
        provider = {"finish_reason": "stop", "input_tokens": None, "output_tokens": None,
                    "reasoning_tokens": None, "cached_tokens": None,
                    "max_tokens": self.spec.max_tokens, "state_sha": self.state_sha}
        if state_error is not None:
            provider["state_error"] = state_error

        def malformed(outputs: dict, finish: str) -> Return:
            return Return(req.handle, outputs, cost, "malformed", served_by=PROGRAM_MODEL_ID,
                          stop_reason=finish, provider={**provider, "finish_reason": finish})

        if "error" in result:
            return malformed({"reason": str(result["error"])[:200]}, "error")
        if result.get("timed_out") is True:
            return malformed({"reason": "timeout"}, "timeout")
        if result.get("returncode") != 0:
            return malformed({"reason": f"exit {result.get('returncode')}",
                              "stderr": str(result.get("stderr", ""))[:500]}, "error")
        text = utf8_text(result.get("stdout", "")) if isinstance(result.get("stdout"), str) else ""
        parsed = _parse_json_object(text)
        new_state: Any = None
        if parsed is not None:
            # The state is the program's, not the return's: it never reaches the
            # outcome schema, the judges or the ledger's outputs field.
            new_state = parsed.pop("state", None)
            if self.spec.state_policy != "private" and new_state is not None:
                parsed = None
            else:
                try:
                    _validate_return(parsed, req.outcome_schema)
                    if self.validator is not None:
                        self.validator(parsed, req)
                except (ValueError, TypeError, ArithmeticError, RecursionError):
                    parsed = None
        if parsed is None:
            return malformed({"raw": text[:4000]}, "stop")
        if new_state is not None:
            try:
                encoded = json.dumps(new_state, sort_keys=True, allow_nan=False,
                                     ensure_ascii=False).encode("utf-8")
            except (TypeError, ValueError):
                return malformed({"reason": "state is not JSON"}, "stop")
            if len(encoded) > MAX_PROGRAM_STATE_BYTES:
                return malformed({"reason": f"state exceeds {MAX_PROGRAM_STATE_BYTES} bytes"},
                                 "stop")
            if self.artifacts is None:
                return malformed({"reason": "no artifact archive"}, "stop")
            self.state_sha = self.artifacts.put(encoded, owner=self.spec.id,
                                                kind="program.state")
            provider["state_sha"] = self.state_sha
        children = _children(req, parsed, self.child_factory)
        raw_calls = parsed.get("tool_calls")
        tool_calls = (
            tuple(c for c in raw_calls if isinstance(c, dict) and isinstance(c.get("tool"), str))
            if isinstance(raw_calls, list)
            else ()
        )
        outputs = {k: v for k, v in parsed.items() if k not in ("requests", "tool_calls")}
        return Return(
            req.handle, outputs, cost, "ok", children=children, served_by=PROGRAM_MODEL_ID,
            stop_reason="stop", tool_calls=tool_calls, provider=provider,
        )


def _provider_report(resp: Any, max_tokens: int) -> dict[str, Any]:
    """Return the provider's own account of the completion; unreported fields are None."""
    raw = resp.raw if isinstance(raw := getattr(resp, "raw", None), dict) else {}
    reasoning = raw.get("reasoning_tokens")
    # Input the provider served from its own prompt cache. Absent where the
    # provider does not report it, which is not a miss and must not read as one.
    cached = raw.get("cached_tokens")
    return {
        "finish_reason": resp.stop_reason,
        "input_tokens": resp.input_tokens if type(resp.input_tokens) is int else None,
        "output_tokens": resp.output_tokens if type(resp.output_tokens) is int else None,
        "reasoning_tokens": reasoning if type(reasoning) is int else None,
        "cached_tokens": cached if type(cached) is int else None,
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
        "unit", "assembly_id", "state_policy")}
    # A tool's timeout stays within [1, 5] (checked where tools are parsed); a
    # program seat's may reach MAX_PROGRAM_TIMEOUT_S.
    fields.update({"gamma": {"type": "number", "minimum": 1e-300, "maximum": 1},
                   "max_tokens": {"type": "integer", "minimum": 16, "maximum": 4096},
                   "timeout_s": {"type": "integer", "minimum": 1,
                                 "maximum": MAX_PROGRAM_TIMEOUT_S},
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
