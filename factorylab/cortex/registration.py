"""Population registration proposals.

An assembly's return may carry a ``register`` list. This module turns that
raw JSON into typed proposals and rejects malformed ones with a deterministic
reason. It decides nothing about money or physics: the runtime pays the
novelty trial, the registry enforces versions and provenance. Rejections here
are about shape, not merit.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

SLUG = re.compile(r"^[a-z][a-z0-9-]{1,47}$")
MAX_PROMPT_CHARS = 4000
MAX_PROPOSALS_PER_RETURN = 3
LEARNERS = ("exp3", "blum_mansour")
ROLES = ("producer", "evaluator", "meta")


@dataclass(frozen=True)
class ModelProposal:
    openrouter_id: str


@dataclass(frozen=True)
class AssemblyProposal:
    id: str
    role: str
    model_id: str
    system_prompt: str
    accepts: tuple[str, ...]
    max_tokens: int
    effort: str


@dataclass(frozen=True)
class RouterProposal:
    event_kind: str
    learner: str
    gamma: float


Proposal = ModelProposal | AssemblyProposal | RouterProposal


@dataclass(frozen=True)
class Rejected:
    index: int
    reason: str


def parse_proposals(
    outputs: dict[str, Any],
    *,
    event_kinds: frozenset[str],
    known_models: frozenset[str],
    known_assemblies: frozenset[str],
) -> tuple[list[Proposal], list[Rejected]]:
    """Return well-formed proposals and the reasons the rest were refused.

    Guarantees: at most ``MAX_PROPOSALS_PER_RETURN`` proposals are accepted,
    in order; an assembly proposal never reuses an existing id or names an
    unknown model; a router proposal names a known event kind and learner;
    prompts are bounded; nothing here has side effects.
    """
    raw = outputs.get("register")
    if raw is None:
        return [], []
    if not isinstance(raw, list):
        return [], [Rejected(-1, "register must be a list")]
    accepted: list[Proposal] = []
    rejected: list[Rejected] = []
    for i, item in enumerate(raw):
        if len(accepted) >= MAX_PROPOSALS_PER_RETURN:
            rejected.append(Rejected(i, "proposal cap reached for this return"))
            continue
        if not isinstance(item, dict):
            rejected.append(Rejected(i, "proposal must be an object"))
            continue
        kind = item.get("kind")
        try:
            if kind == "model":
                accepted.append(_model(item))
            elif kind == "assembly":
                accepted.append(_assembly(item, event_kinds, known_models, known_assemblies))
            elif kind == "router":
                accepted.append(_router(item, event_kinds))
            else:
                raise ValueError("unknown proposal kind")
        except ValueError as exc:
            rejected.append(Rejected(i, str(exc)))
    return accepted, rejected


def _model(item: dict[str, Any]) -> ModelProposal:
    oid = item.get("openrouter_id")
    if not isinstance(oid, str) or "/" not in oid or len(oid) > 128 or " " in oid:
        raise ValueError("openrouter_id must look like vendor/model")
    return ModelProposal(oid)


def _assembly(
    item: dict[str, Any],
    event_kinds: frozenset[str],
    known_models: frozenset[str],
    known_assemblies: frozenset[str],
) -> AssemblyProposal:
    aid = item.get("id")
    if not isinstance(aid, str) or not SLUG.match(aid):
        raise ValueError("id must be a slug of 2-48 chars")
    if aid in known_assemblies or aid == "NOOP":
        raise ValueError("id already registered")
    role = item.get("role", "producer")
    if role not in ROLES:
        raise ValueError("role must be producer, evaluator or meta")
    model_id = item.get("model_id")
    if not isinstance(model_id, str) or model_id not in known_models:
        raise ValueError("model_id must name a registered model")
    prompt = item.get("system_prompt")
    if not isinstance(prompt, str) or not prompt.strip():
        raise ValueError("system_prompt is required")
    if len(prompt) > MAX_PROMPT_CHARS:
        raise ValueError(f"system_prompt exceeds {MAX_PROMPT_CHARS} chars")
    accepts = item.get("accepts")
    if not isinstance(accepts, list) or not accepts:
        raise ValueError("accepts must be a non-empty list of event kinds")
    if any(not isinstance(k, str) or k not in event_kinds for k in accepts):
        raise ValueError("accepts contains an unknown event kind")
    if role == "evaluator" and set(accepts) != {"ProducerReturn"}:
        raise ValueError("evaluators accept exactly ProducerReturn")
    if role == "meta" and set(accepts) != {"Verdict"}:
        raise ValueError("metas accept exactly Verdict")
    if role == "producer" and {"ProducerReturn", "Verdict"} & set(accepts):
        raise ValueError("producers do not accept evaluation events")
    max_tokens = item.get("max_tokens", 512)
    if type(max_tokens) is not int or not 16 <= max_tokens <= 4096:
        raise ValueError("max_tokens must be an int in [16, 4096]")
    effort = item.get("effort", "low")
    if effort not in ("low", "medium", "high"):
        raise ValueError("effort must be low, medium or high")
    return AssemblyProposal(
        aid, role, model_id, prompt, tuple(dict.fromkeys(accepts)), max_tokens, effort
    )


def _router(item: dict[str, Any], event_kinds: frozenset[str]) -> RouterProposal:
    kind = item.get("event_kind")
    if not isinstance(kind, str) or kind not in event_kinds:
        raise ValueError("event_kind must be a known event kind")
    learner = item.get("learner")
    if learner not in LEARNERS:
        raise ValueError("learner must be exp3 or blum_mansour")
    gamma = item.get("gamma", 0.1)
    if not isinstance(gamma, int | float) or isinstance(gamma, bool) or not 0 < gamma <= 1:
        raise ValueError("gamma must be in (0, 1]")
    return RouterProposal(kind, learner, float(gamma))
