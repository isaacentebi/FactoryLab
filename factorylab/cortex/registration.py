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
from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any

from factorylab.cortex.sandbox import jail_available

SLUG = re.compile(r"^[a-z][a-z0-9-]{1,47}$")
MAX_PROMPT_CHARS = 4000
MAX_PROPOSALS_PER_RETURN = 3
LEARNERS = ("exp3", "blum_mansour")
ROLES = ("producer", "evaluator", "meta", "antagonist")
# An assembly's declared action set is its own; the kernel bounds only its size.
MAX_DECLARED_ACTIONS = 32
MAX_ACTION_ID_CHARS = 64


def seed_emits(role: str) -> tuple[str, ...]:
    """Expand a legacy seed label into an ordinary, replaceable output contract."""
    return {"producer": ("ProducerReturn",), "evaluator": ("Verdict",),
            "meta": ("MetaVerdict",), "antagonist": ("Exposure",)}.get(
                role, ("ProducerReturn",))


CONTRACT_ROLES = MappingProxyType({
    "ProducerReturn": "producer", "Verdict": "evaluator",
    "MetaVerdict": "meta", "Exposure": "antagonist",
})
REWARD_SHAPES = ("judged", "forecast", "conformity", "exposure")
SEED_REWARD_SHAPES = MappingProxyType({
    "ProducerReturn": "judged", "Verdict": "forecast",
    "MetaVerdict": "conformity", "Exposure": "exposure",
})


def measured_role(emits: str | tuple[str, ...] | None) -> str:
    """Name the measurement scope of an emitted contract, never of a free-form label.

    A registration's ``role`` is a display name; what a return is measured
    against follows the kind it emits. Seed role names remain aliases for their
    seed kinds; population kinds retain their exact, case-sensitive names. A
    contract with several declared kinds uses the first until the return selects.
    """
    kinds = (emits,) if isinstance(emits, str) else tuple(emits or ())
    return CONTRACT_ROLES.get(kinds[0], kinds[0]) if kinds else "producer"


BUILTIN_RETURNS = frozenset({"ProducerReturn", "Verdict", "MetaVerdict", "Exposure"})
# A metric card's accountability scope is either a role alias, ``all``, or an
# emitted kind. These spellings name populations, so no emitted kind may take one
# in any case: a kind and the scope that measures it must never be the same name.
RESERVED_SCOPES = frozenset({*ROLES, "all"})


def event_name(value: Any) -> str:
    """An event kind is a nonempty name, independent of any role label."""
    if (not isinstance(value, str) or not value
            or any(c.isspace() or not c.isprintable() for c in value)):
        raise ValueError("event kind must be a nonempty name without whitespace")
    return value


def reward_contracts(
    emits: tuple[str, ...], declared: Any = None, *,
    registered: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """Each emitted kind has one of four reward shapes; seed meanings remain fixed."""
    if declared is None:
        declared = {}
    if not isinstance(declared, Mapping) or any(k not in emits for k in declared):
        raise ValueError("reward_shapes must map declared emits kinds to reward shapes")
    result = {}
    for kind in emits:
        existing = SEED_REWARD_SHAPES.get(kind, (registered or {}).get(kind))
        shape = declared.get(kind, existing or "judged")
        if not isinstance(shape, str) or shape not in REWARD_SHAPES:
            raise ValueError("reward shape must be judged, forecast, conformity or exposure")
        if kind in SEED_REWARD_SHAPES and shape != SEED_REWARD_SHAPES[kind]:
            raise ValueError("built-in reward shapes cannot be replaced")
        if existing is not None and shape != existing:
            raise ValueError(f"reward shape already declared differently: {kind}")
        result[kind] = shape
    return result


def output_contracts(emits: Any, schemas: Any) -> tuple[tuple[str, ...], dict[str, dict]]:
    """Custom return kinds require executable schemas; built-in meanings cannot be replaced."""
    from factorylab.cortex.assembly import _schema_definition
    from factorylab.kernel.events import EventKind

    if not isinstance(emits, (list, tuple)) or not emits:
        raise ValueError("emits must be a non-empty list of return kinds")
    kinds = tuple(dict.fromkeys(event_name(k) for k in emits))
    if not isinstance(schemas, dict) or any(k not in kinds for k in schemas):
        raise ValueError("schemas must map declared emits kinds to outcome schemas")
    custom = {}
    for kind in kinds:
        if kind.lower() in RESERVED_SCOPES:
            raise ValueError("a return kind cannot take a reserved measurement scope name")
        if kind in BUILTIN_RETURNS:
            if kind in schemas:
                raise ValueError("built-in return schemas cannot be replaced")
            continue
        if kind in {str(k) for k in EventKind}:
            raise ValueError("a population return cannot impersonate a world or kernel event")
        schema = schemas.get(kind)
        _schema_definition(schema)
        if schema.get("type") != "object":
            raise ValueError("a population return schema must have type object")
        custom[kind] = schema
    return kinds, custom


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
    emits: tuple[str, ...] = ()
    schemas: dict[str, dict] = field(default_factory=dict)
    reward_shapes: dict[str, str] = field(default_factory=dict)
    # A program seat (``model_id == "program"``): its jailed code, wall timeout and
    # whether it keeps private state between calls. Empty for a model seat.
    code: str = ""
    timeout_s: int = 10
    state_policy: str = "none"
    # A watcher (edition 3, C2): the predicate over world state the kernel settles
    # each tick without a model call. Empty for every seat that is not one.
    trigger: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        """``reward_shapes`` holds the resolved contract for the kinds this proposal emits.

        The field is resolved, not declared: construction fills an undeclared kind
        from its seed shape or ``judged``, so a proposal derived from another by
        ``replace`` arrives carrying the earlier proposal's kinds. Resolution is
        against this proposal's own emitted kinds; a declaration naming a kind the
        proposal does not emit is refused where the population declares it.
        """
        kinds = self.emits or seed_emits(self.role)
        declared = self.reward_shapes
        if isinstance(declared, Mapping):
            declared = {k: v for k, v in declared.items() if k in kinds}
        object.__setattr__(self, "reward_shapes", reward_contracts(kinds, declared))


@dataclass(frozen=True)
class RetireProposal:
    assembly_id: str


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
    """A measurement the population writes, priced like any other card input."""

    id: str
    description: str
    unit: str
    unit_range: tuple[float, float]
    code: str


@dataclass(frozen=True)
class PredicateProposal:
    """A population predicate defines a boolean resolution over public facts."""

    id: str
    description: str
    code: str


@dataclass(frozen=True)
class LearnerProposal:
    """A learner over an assembly's own declared action set."""

    assembly_id: str
    learner: str
    actions: tuple[str, ...]
    gamma: float


# --- connectors: admission policy lives in the runtime sortition path --------

@dataclass(frozen=True)
class ConnectorProposal:
    """A connector specifies only its identity, description and HTTPS origin."""

    id: str
    description: str
    origin: str
    preflight_path: str = "/"
    pay: str | None = None
    max_call_micro: int = 0


@dataclass(frozen=True)
class MarketProposal:
    """A market names exactly one venue-listed perpetual or USDC spot pair."""

    coin: str
    market: str = "perp"


CHALLENGE_RULES = ("at most", "at least", "above", "below")
MAX_CHALLENGE_TRIAL_WINDOWS = 50
MAX_EVIDENCE_CHARS = 4000


@dataclass(frozen=True)
class ChallengeProposal:
    """A challenge names a live card, gives evidence, and offers a replacement to trial.

    The replacement keeps the challenged card's id and norm: it states a new
    observation, rule, value and window (and optionally description, units and
    answers_for) for the same standard. The runtime measures both, frozen, for
    ``trial_windows`` reserve windows before the committee ballots on adoption.
    """

    card_id: str
    evidence: str
    replacement: dict[str, Any]
    trial_windows: int


def _challenge(item: dict[str, Any]) -> ChallengeProposal:
    required = {"kind", "card_id", "evidence", "replacement", "trial_windows"}
    if set(item) != required:
        raise ValueError("challenge needs exactly card_id, evidence, replacement, trial_windows")
    card_id = item["card_id"]
    if not isinstance(card_id, str) or not card_id.strip() or len(card_id) > 64:
        raise ValueError("card_id must name a current card")
    evidence = item["evidence"]
    if not isinstance(evidence, str) or not evidence.strip():
        raise ValueError("evidence must be a nonempty string")
    if len(evidence) > MAX_EVIDENCE_CHARS:
        raise ValueError(f"evidence must be at most {MAX_EVIDENCE_CHARS} chars")
    windows = item["trial_windows"]
    if type(windows) is not int or not 1 <= windows <= MAX_CHALLENGE_TRIAL_WINDOWS:
        raise ValueError(f"trial_windows must be an integer in [1, {MAX_CHALLENGE_TRIAL_WINDOWS}]")
    replacement = item["replacement"]
    if not isinstance(replacement, dict):
        raise ValueError("replacement must be an object")
    allowed = {"observation", "rule", "value", "window", "description", "units", "answers_for"}
    if not {"observation", "rule", "value", "window"} <= set(replacement) <= allowed:
        raise ValueError("replacement needs observation, rule, value and window; optional "
                         "description, units, answers_for")
    for key in ("observation", "description", "units", "answers_for"):
        text = replacement.get(key)
        if key in replacement and (not isinstance(text, str) or not text.strip()
                                   or len(text) > 512):
            raise ValueError(f"replacement.{key} must be a nonempty string")
    if replacement["rule"] not in CHALLENGE_RULES:
        raise ValueError(f"replacement.rule must be one of {', '.join(CHALLENGE_RULES)}")
    value = replacement["value"]
    if type(value) not in (int, float) or not math.isfinite(value):
        raise ValueError("replacement.value must be a finite number")
    window = replacement["window"]
    if not isinstance(window, dict) or not {"kind", "n"} <= set(window) <= {"kind", "n", "per"}:
        raise ValueError("replacement.window must be {kind, n, per}")
    return ChallengeProposal(card_id.strip(), evidence, dict(replacement), windows)
@dataclass(frozen=True)
class ServiceProposal:
    """A service sells one registered program's output over x402 at a fixed price."""

    program_id: str
    price_micro: int
    description: str


# A service price is bounded like every other per-request amount on the x402 rail.
MAX_SERVICE_PRICE_MICRO = 10_000_000


def _service(item: dict[str, Any], known_tools: frozenset[str]) -> ServiceProposal:
    if set(item) != {"kind", "program_id", "price_micro", "description"}:
        raise ValueError("service fields are kind, program_id, price_micro, description")
    program_id = item["program_id"]
    if not isinstance(program_id, str) or not SLUG.fullmatch(program_id):
        raise ValueError("program_id must be a slug of 2-48 chars")
    if program_id not in known_tools:
        raise ValueError("program_id must name a registered population tool")
    price = item["price_micro"]
    if type(price) is not int or not 1 <= price <= MAX_SERVICE_PRICE_MICRO:
        raise ValueError(f"price_micro must be an int in [1, {MAX_SERVICE_PRICE_MICRO}]")
    description = item["description"]
    if not isinstance(description, str) or not description.strip():
        raise ValueError("description is required")
    if len(description) > 500:
        raise ValueError("description exceeds 500 chars")
    return ServiceProposal(program_id, price, description.strip())


def _market(item: dict[str, Any]) -> MarketProposal:
    if set(item) not in ({"kind", "coin"}, {"kind", "pair"}):
        raise ValueError("market requires exactly one coin or pair")
    value = item.get("coin", item.get("pair"))
    if (not isinstance(value, str) or not value or len(value) > 128
            or any(c.isspace() or not c.isprintable() for c in value)):
        raise ValueError("market must name a coin or pair")
    if "pair" in item and (value.count("/") != 1 or not value.endswith("/USDC")):
        raise ValueError("spot pair must be BASE/USDC")
    if "coin" in item and "/" in value:
        raise ValueError("perpetual coin cannot be a pair")
    return MarketProposal(value, "spot" if "pair" in item else "perp")


def _connector(item: dict[str, Any]) -> ConnectorProposal:
    from factorylab.kernel.money import nonnegative_usd_micro
    from factorylab.world.connector import origin_host, validate_path

    required = {"kind", "id", "description", "origin"}
    optional = {"preflight_path", "pay", "max_call_usd"}
    if not required <= set(item) or set(item) - required - optional:
        raise ValueError("invalid connector fields")
    if not isinstance(item["id"], str) or not SLUG.fullmatch(item["id"]):
        raise ValueError("connector id must be a slug")
    if (not isinstance(item["description"], str) or not item["description"].strip()
            or len(item["description"]) > 500):
        raise ValueError("connector description must contain 1..500 characters")
    origin_host(item["origin"])
    path = item.get("preflight_path", "/")
    validate_path(path)
    pay, cap = item.get("pay"), item.get("max_call_usd")
    if pay is not None and pay != "x402":
        raise ValueError("connector pay must be x402")
    if (pay is None and "max_call_usd" in item
            or pay == "x402" and type(cap) not in (str, int)):
        raise ValueError("x402 requires max_call_usd as exact USD text or integer")
    micro = nonnegative_usd_micro(cap, rounding="exact") if pay else 0
    return ConnectorProposal(item["id"], item["description"], item["origin"], path, pay, micro)


Proposal = (
    ModelProposal | AssemblyProposal | RouterProposal | ToolProposal | RetireProposal
    | ObservationProposal | PredicateProposal | LearnerProposal | ConnectorProposal
    | MarketProposal | ChallengeProposal
    | MarketProposal | ServiceProposal
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
    retired_assemblies: frozenset[str] = frozenset(),
    seed_observations: frozenset[str] = frozenset(),
    known_reward_shapes: Mapping[str, str] | None = None,
) -> tuple[list[Proposal], list[Rejected]]:
    """Return well-formed proposals and the reasons the rest were refused.

    Guarantees: at most ``MAX_PROPOSALS_PER_RETURN`` proposals are accepted,
    in order; an assembly proposal reuses an id only after retirement and never
    names an unknown model; a router proposal names an event kind and learner;
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
                accepted.append(_assembly(item, event_kinds, known_models,
                                          known_assemblies - retired_assemblies,
                                          known_reward_shapes, jail=tool_jail))
            elif kind == "router":
                accepted.append(_router(item, event_kinds))
            elif kind == "tool":
                accepted.append(_tool(item, known_tools, jail=tool_jail))
            elif kind == "retire":
                aid = item.get("assembly_id")
                if not isinstance(aid, str) or aid not in known_assemblies:
                    raise ValueError("assembly_id must name a registered assembly")
                if aid in retired_assemblies:
                    raise ValueError("assembly is already retired")
                accepted.append(RetireProposal(aid))
            elif kind == "connector":
                accepted.append(_connector(item))
            elif kind == "market":
                accepted.append(_market(item))
            elif kind == "service":
                accepted.append(_service(item, known_tools))
            elif kind == "observation":
                accepted.append(_observation(item, seed_observations, jail=tool_jail))
            elif kind == "predicate":
                accepted.append(_predicate(item, jail=tool_jail))
            elif kind == "learner":
                accepted.append(_learner(item, known_assemblies))
            elif kind == "challenge":
                accepted.append(_challenge(item))
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
    known_reward_shapes: Mapping[str, str] | None = None,
    *,
    jail: bool | None = None,
) -> AssemblyProposal:
    aid = item.get("id")
    if not isinstance(aid, str) or not SLUG.match(aid):
        raise ValueError("id must be a slug of 2-48 chars")
    if aid in known_assemblies or aid == "NOOP":
        raise ValueError("id already registered")
    # A display name only: measurement and settlement both follow ``emits``.
    role = item.get("role", "producer")
    if not isinstance(role, str) or not SLUG.fullmatch(role):
        raise ValueError("role must be a descriptive slug")
    model_id = item.get("model_id")
    # ``program`` is not a registered model: the seat's executor is its own code.
    program = model_id == "program"
    if not isinstance(model_id, str) or (model_id not in known_models and not program):
        raise ValueError("model_id must name a registered model")
    code, timeout_s, state_policy = "", 10, "none"
    trigger: dict[str, Any] = {}
    if program:
        code = item.get("code")
        if not isinstance(code, str) or not code.strip():
            raise ValueError("a program seat needs code")
        if len(code) > 16_000:
            raise ValueError("code exceeds 16000 chars")
        timeout_s = item.get("timeout_s", 10)
        if type(timeout_s) is not int or not 1 <= timeout_s <= 10:
            raise ValueError("timeout_s must be an int in [1, 10]")
        state_policy = item.get("state_policy", "none")
        if state_policy not in ("none", "private"):
            raise ValueError("state_policy must be none or private")
        if "trigger" in item:
            from factorylab.runtime.subscriptions import validate_trigger

            # A watcher is a program seat with a predicate the kernel can settle from
            # world state; the trigger is part of the spec, registered by this route.
            trigger = validate_trigger(item["trigger"])
        if not (jail_available() if jail is None else jail):
            raise ValueError("no jail on this host")
    elif any(k in item for k in ("code", "timeout_s", "state_policy", "trigger")):
        raise ValueError(
            "code, timeout_s, state_policy and trigger belong to a program seat")
    # A program has no system role; the prompt field is kept only as its label.
    prompt = item.get("system_prompt", "program" if program else None)
    if not isinstance(prompt, str) or not prompt.strip():
        raise ValueError("system_prompt is required")
    if len(prompt) > MAX_PROMPT_CHARS:
        raise ValueError(f"system_prompt exceeds {MAX_PROMPT_CHARS} chars")
    accepts = item.get("accepts")
    if not isinstance(accepts, list) or not accepts:
        raise ValueError("accepts must be a non-empty list of event kinds")
    accepts = tuple(dict.fromkeys(event_name(k) for k in accepts))
    emits, schemas = output_contracts(item.get("emits", seed_emits(role)),
                                      item.get("schemas", {}))
    max_tokens = item.get("max_tokens", 512)
    if type(max_tokens) is not int or not 16 <= max_tokens <= 4096:
        raise ValueError("max_tokens must be an int in [16, 4096]")
    effort = item.get("effort", "low")
    if effort not in ("low", "medium", "high"):
        raise ValueError("effort must be low, medium or high")
    if "reward_shapes" in item and not isinstance(item["reward_shapes"], dict):
        raise ValueError("reward_shapes must map declared emits kinds to reward shapes")
    return AssemblyProposal(
        aid, role, model_id, prompt, accepts, max_tokens, effort, emits, schemas,
        reward_contracts(emits, item.get("reward_shapes", {}), registered=known_reward_shapes),
        code, timeout_s, state_policy, trigger,
    )


def _router(item: dict[str, Any], event_kinds: frozenset[str]) -> RouterProposal:
    kind = event_name(item.get("event_kind"))
    if kind not in event_kinds:
        raise ValueError("event_kind must name a world or population-declared event kind")
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


# --- propensity and measurement ----------------------------------------------
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
    """Shape-check a population measurement before the runtime preflights it.

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


def _predicate(item: dict[str, Any], *, jail: bool | None = None) -> PredicateProposal:
    """Malformed predicate definitions receive feedback before their jailed preflight."""
    from factorylab.settlement.vocabulary import validate_predicate_definition

    if set(item) != {"kind", "id", "description", "code"}:
        raise ValueError("predicate fields are kind, id, description, code")
    validate_predicate_definition(item["id"], item["description"], item["code"])
    if not (jail_available() if jail is None else jail):
        raise ValueError("no jail on this host")
    return PredicateProposal(item["id"], item["description"], item["code"])


def _learner(item: dict[str, Any], known_assemblies: frozenset[str]) -> LearnerProposal:
    """A learner over an assembly's own declared action set.

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
