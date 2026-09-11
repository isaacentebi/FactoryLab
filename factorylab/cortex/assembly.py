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
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from factorylab.cortex.request import Request, Return
from factorylab.world.metering import Infeasible, MeteredModel
from factorylab.world.models import ModelRequest

SEED_SYSTEM_PROMPT = (
    "You receive one request. Reply with a single JSON object that satisfies the "
    "outcome schema. If the request cannot be completed, reply with a JSON object "
    'containing "status": "cannot" and "reason". Optionally include a "requests" '
    "array of further requests, each with description, inputs and outcome_schema."
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

    def __post_init__(self) -> None:
        if self.memory_policy not in ("none", "handle-scoped"):
            raise ValueError("unknown memory policy")
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
        mreq = self.build_model_request(req)
        ceiling = self.model.ceiling(mreq)
        if ceiling > req.cost_ceiling:
            return Return(
                req.handle, {"reason": "ceiling exceeds request cost_ceiling"}, 0, "failed"
            )
        try:
            metered = self.model.complete(mreq, handle=req.handle)
        except Infeasible as exc:
            return Return(req.handle, {"reason": f"infeasible: {exc}"}, 0, "failed")
        except Exception as exc:  # provider error after reservation release
            return Return(req.handle, {"reason": f"{type(exc).__name__}: {exc}"}, 0, "failed")
        resp = metered.result
        cost = metered.cost
        if resp.refused:
            return Return(
                req.handle, {"reason": "refused"}, cost, "refused", served_by=resp.model_id
            )
        parsed = _parse_json_object(resp.text)
        if parsed is None:
            return Return(
                req.handle, {"raw": resp.text}, cost, "malformed", served_by=resp.model_id
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
        outputs = {k: v for k, v in parsed.items() if k != "requests"}
        return Return(req.handle, outputs, cost, "ok", children=children, served_by=resp.model_id)

    def _children(self, req: Request, parsed: dict[str, Any]) -> tuple[Request, ...]:
        raw = parsed.get("requests")
        if not isinstance(raw, list) or self.child_factory is None:
            return ()
        out: list[Request] = []
        for i, item in enumerate(raw):
            if isinstance(item, dict) and "description" in item:
                out.append(self.child_factory(req, item, i))
        return tuple(out)


def _parse_json_object(text: str) -> dict[str, Any] | None:
    """Return the first JSON object in ``text`` or None. Tolerates code fences."""
    s = text.strip()
    if s.startswith("```"):
        s = s.strip("`")
        if s.startswith("json"):
            s = s[4:]
    start = s.find("{")
    if start < 0:
        return None
    depth = 0
    for i in range(start, len(s)):
        if s[i] == "{":
            depth += 1
        elif s[i] == "}":
            depth -= 1
            if depth == 0:
                try:
                    obj = json.loads(s[start : i + 1])
                except json.JSONDecodeError:
                    return None
                return obj if isinstance(obj, dict) else None
    return None
