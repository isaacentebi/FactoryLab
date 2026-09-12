"""Population registration proposals.

An assembly's return may carry a ``register`` list. This module turns that
raw JSON into typed proposals and rejects malformed ones with a deterministic
reason. It decides nothing about money or physics: the runtime pays the
novelty trial, the registry enforces versions and provenance. Rejections here
are about shape, not merit.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Any

from factorylab.cortex.sandbox import jail_available

SLUG = re.compile(r"^[a-z][a-z0-9-]{1,47}$")
MAX_PROMPT_CHARS = 4000
MAX_PROPOSALS_PER_RETURN = 3
LEARNERS = ("exp3", "blum_mansour")
ROLES = ("producer", "evaluator", "meta", "antagonist")
# A10: an assembly's declared action set is its own; the kernel bounds only its size.
MAX_DECLARED_ACTIONS = 32
MAX_ACTION_ID_CHARS = 64


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
    add: bool = False  # True: add another router for the kind instead of replacing


@dataclass(frozen=True)
class ToolProposal:
    id: str
    description: str
    args_schema: dict
    code: str
    timeout_s: int


@dataclass(frozen=True)
class ObservationProposal:
    """Spec A11: a measurement the population writes, priced like any other card input."""

    id: str
    description: str
    unit: str
    unit_range: tuple[float, float]
    code: str


@dataclass(frozen=True)
class LearnerProposal:
    """Spec A10: a learner over an assembly's own declared action set."""

    assembly_id: str
    learner: str
    actions: tuple[str, ...]
    gamma: float


Proposal = (
    ModelProposal | AssemblyProposal | RouterProposal | ToolProposal
    | ObservationProposal | LearnerProposal
)


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
    known_tools: frozenset[str] = frozenset(),
    tool_jail: bool | None = None,
    seed_observations: frozenset[str] = frozenset(),
) -> tuple[list[Proposal], list[Rejected]]:
    """Return well-formed proposals and the reasons the rest were refused.

    Guarantees: at most ``MAX_PROPOSALS_PER_RETURN`` proposals are accepted,
    in order; an assembly proposal never reuses an existing id or names an
    unknown model; a router proposal names a known event kind and learner;
    prompts are bounded; tools have fresh ids, bounded source and timeouts;
    nothing here has side effects.
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
            elif kind == "tool":
                accepted.append(_tool(item, known_tools, jail=tool_jail))
            elif kind == "observation":
                accepted.append(_observation(item, seed_observations, jail=tool_jail))
            elif kind == "learner":
                accepted.append(_learner(item, known_assemblies))
            else:
                raise ValueError("unknown proposal kind")
        except ValueError as exc:
            rejected.append(Rejected(i, str(exc)))
    return accepted, rejected


def _model(item: dict[str, Any]) -> ModelProposal:
    oid = item.get("openrouter_id")
    if not isinstance(oid, str) or "/" not in oid or len(oid) > 128 or " " in oid:
        raise ValueError("openrouter_id must look like vendor/model")
    if oid.count("@") > 1:
        raise ValueError("at most one @reasoning-level suffix")
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
        raise ValueError("role must be producer, evaluator, meta or antagonist")
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
    if role == "meta" and accepts not in (["Verdict"], ["MetaVerdict"]):
        raise ValueError("metas accept exactly one of Verdict or MetaVerdict")
    if role in ("producer", "antagonist") and {
        "ProducerReturn", "Verdict", "MetaVerdict"
    } & set(accepts):
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
    add = item.get("add", False)
    if isinstance(add, str) and add.lower() in ("true", "false"):
        add = add.lower() == "true"
    if not isinstance(add, bool):
        raise ValueError("add must be a boolean")
    return RouterProposal(kind, learner, float(gamma), add)


def _tool(
    item: dict[str, Any], known_tools: frozenset[str], *, jail: bool | None = None,
) -> ToolProposal:
    tid = item.get("id")
    if not isinstance(tid, str) or not SLUG.fullmatch(tid):
        raise ValueError("id must be a slug of 2-48 chars")
    if tid in known_tools:
        raise ValueError("id already registered")
    description = item.get("description")
    if not isinstance(description, str) or not description.strip():
        raise ValueError("description is required")
    if len(description) > 500:
        raise ValueError("description exceeds 500 chars")
    schema = item.get("args_schema")
    if (
        not isinstance(schema, dict)
        or schema.get("type") != "object"
        or not isinstance(schema.get("properties"), dict)
    ):
        raise ValueError("args_schema must have type object and a properties dict")
    code = item.get("code")
    if not isinstance(code, str):
        raise ValueError("code must be a string")
    if len(code) > 8000:
        raise ValueError("code exceeds 8000 chars")
    timeout_s = item.get("timeout_s")
    if type(timeout_s) is not int or not 1 <= timeout_s <= 5:
        raise ValueError("timeout_s must be an int in [1, 5]")
    if not (jail_available() if jail is None else jail):
        raise ValueError("no jail on this host")
    return ToolProposal(tid, description, schema, code, timeout_s)


# --- spec A10/A11: propensity and measurement (workstream W5) -----------------
# Kept in its own section: the two kinds below are independent of the assembly,
# model, router and tool kinds above.


def _finite_bound(value: Any) -> float:
    """Return a declared range bound as a float, or say it is not a finite number.

    Guarantees the conversion raises nothing but ``ValueError``: an integer too
    large for a float is not representable, and a proposal carrying one is
    rejected with a reason rather than ending the return that carried it.
    """
    if type(value) not in (int, float) or isinstance(value, bool):
        raise ValueError("range bounds must be finite numbers")
    try:
        bound = float(value)
    except (OverflowError, ValueError):
        raise ValueError("range bounds must be finite numbers") from None
    if not math.isfinite(bound):
        raise ValueError("range bounds must be finite numbers")
    return bound


def _observation(
    item: dict[str, Any], seed_observations: frozenset[str], *, jail: bool | None = None,
) -> ObservationProposal:
    """A11: shape-check a population measurement before the runtime preflights it.

    Merit is not decided here: whether the code actually measures the last closed
    window is settled by running it in the jail at registration.
    """
    from factorylab.runtime.observations import (
        MAX_OBSERVATION_CODE_CHARS,
        MAX_OBSERVATION_DESCRIPTION_CHARS,
        normalise,
    )

    oid = item.get("id")
    if not isinstance(oid, str) or not SLUG.fullmatch(normalise(oid)):
        raise ValueError("id must be a slug of 2-48 chars")
    oid = normalise(oid)
    if oid in seed_observations:
        raise ValueError("seed observation ids cannot be redefined")
    description = item.get("description")
    if not isinstance(description, str) or not description.strip():
        raise ValueError("description is required")
    if len(description) > MAX_OBSERVATION_DESCRIPTION_CHARS:
        raise ValueError(f"description exceeds {MAX_OBSERVATION_DESCRIPTION_CHARS} chars")
    unit = item.get("unit")
    if not isinstance(unit, str) or not unit.strip() or len(unit) > 64:
        raise ValueError("unit is required and is at most 64 chars")
    unit_range = item.get("range")
    if not isinstance(unit_range, list | tuple) or len(unit_range) != 2:
        raise ValueError("range must be [lo, hi]")
    lo, hi = (_finite_bound(v) for v in unit_range)
    if not lo < hi:
        raise ValueError("range must have lo below hi")
    code = item.get("code")
    if not isinstance(code, str):
        raise ValueError("code must be a string")
    if len(code) > MAX_OBSERVATION_CODE_CHARS:
        raise ValueError(f"code exceeds {MAX_OBSERVATION_CODE_CHARS} chars")
    if "observe" not in code:
        raise ValueError("code must define observe(facts)")
    if not (jail_available() if jail is None else jail):
        raise ValueError("no jail on this host")
    return ObservationProposal(oid, description, unit.strip(), (lo, hi), code)


def _learner(item: dict[str, Any], known_assemblies: frozenset[str]) -> LearnerProposal:
    """A10: a learner over an assembly's own declared action set.

    The action set is declared here because a Blum--Mansour construction needs one
    copy per action before the first round; the assembly's returns then declare a
    propensity over it, and the reward on the same handle trains it off-policy.
    """
    aid = item.get("assembly_id")
    if not isinstance(aid, str) or aid not in known_assemblies:
        raise ValueError("assembly_id must name a registered assembly")
    learner = item.get("learner")
    if learner not in LEARNERS:
        raise ValueError("learner must be exp3 or blum_mansour")
    actions = item.get("actions")
    if not isinstance(actions, list) or not 2 <= len(actions) <= MAX_DECLARED_ACTIONS:
        raise ValueError(f"actions must be 2 to {MAX_DECLARED_ACTIONS} action ids")
    if any(not isinstance(a, str) or not a.strip() or len(a) > MAX_ACTION_ID_CHARS
           for a in actions):
        raise ValueError(f"action ids are nonempty strings of at most {MAX_ACTION_ID_CHARS} chars")
    ordered = tuple(dict.fromkeys(a.strip() for a in actions))
    if len(ordered) != len(actions):
        raise ValueError("action ids must be unique")
    gamma = item.get("gamma", 0.1)
    if not isinstance(gamma, int | float) or isinstance(gamma, bool) or not 0 < gamma <= 1:
        raise ValueError("gamma must be in (0, 1]")
    return LearnerProposal(aid, learner, ordered, float(gamma))
