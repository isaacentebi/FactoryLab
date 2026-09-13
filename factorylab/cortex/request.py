"""Request and Return: the rich request line and the thin return line.

A ``Request`` is everything an executor needs and nothing about who asked.
Author identity never appears here; lineage is the sealed ledger's business.
A ``Return`` carries the outputs, the committed cost, a status, and any child
requests the assembly wants routed. Learning feedback is *not* on the Return;
it travels on the reward channel as a thin scalar addressed to the handle.

One thing does travel forward on the request, and only one: the propensity of
the agent whose decision this request is about (essay II.I.b — the propensity
score is "directed forward within a request and stored within the reward queue",
so that its recipient can "reconstruct the potential value of an alternative
choice"). It is still author-neutral: a distribution over actions, never a name.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field, replace
from typing import Any

Money = int

# The world facts that hold still between calls: everything fixed within a charter
# edition and a registration state. They are rendered first, in one contiguous
# block, so consecutive calls to any assembly of a world begin with byte-identical
# text and a provider's automatic prefix cache (DeepSeek and OpenAI cache on an
# identical prefix, with no cache_control marker) can hit. Everything not named
# here moves — the account, the mids, the pots, the note counts, the pathologies,
# the reserve, the controller's prices, the governance queue, the measured tick —
# and is rendered after the block, inside ``INPUTS`` with the request itself.
# A key absent from this set is treated as moving, which costs cache, never
# correctness: adding a world key can only shrink the stable prefix.
STABLE_WORLD_KEYS = frozenset({
    "a_return_may_include", "action_labels", "addressing", "assemblies", "catalogue",
    "charter", "charter_edition", "clock", "committee", "composition", "connectors",
    "contracts", "event_kinds", "event_schemas", "mechanics", "meta_input", "models",
    "observation_facts", "observations", "population_tools", "prices", "proposal_shapes",
    "reserved_return_fields", "routers", "scoring", "sellers", "tools", "trading_markets",
    "venue", "venue_listing", "work",
})

WORLD_HEADER = (
    "WORLD\nWhat holds for every call in this world while its charter edition and its "
    "registrations stand. The facts that move — the account, the mids, the prices on "
    "the cards, the reserve — arrive below with the work.\n"
)

# The kernel bounds only the size of a declared action set, never its contents.
MAX_DECLARED_ACTIONS = 32
MAX_ACTION_ID_CHARS = 64
PROPENSITY_TOLERANCE = 1e-6


def _as_probability(prob: Any) -> float:
    """Return a probability as a float, or say it is not a finite number.

    Guarantees no numeric input escapes as an exception other than ``ValueError``:
    an integer too large for a float is not representable, and a declaration that
    carries one is malformed rather than fatal.
    """
    if type(prob) not in (int, float) or isinstance(prob, bool):
        raise ValueError("propensity probabilities must be finite numbers")
    try:
        value = float(prob)
    except (OverflowError, ValueError):
        raise ValueError("propensity probabilities must be finite numbers") from None
    if not math.isfinite(value):
        raise ValueError("propensity probabilities must be finite numbers")
    return value


def validate_propensity(propensity: Any) -> dict[str, float]:
    """Return a declared distribution over an agent's own actions, or say why it is not one.

    The action ids are the agent's: the kernel checks only that they are short,
    unique, bounded in number, and that the probabilities are a distribution. The
    tolerance is loose enough for a model that writes three decimal places.
    """
    if not isinstance(propensity, dict) or not propensity:
        raise ValueError("propensity must be a non-empty object of action to probability")
    if len(propensity) > MAX_DECLARED_ACTIONS:
        raise ValueError(f"propensity declares more than {MAX_DECLARED_ACTIONS} actions")
    result: dict[str, float] = {}
    for action, prob in propensity.items():
        if not isinstance(action, str) or not action.strip():
            raise ValueError("propensity action ids must be non-empty strings")
        if len(action) > MAX_ACTION_ID_CHARS:
            raise ValueError(f"propensity action ids exceed {MAX_ACTION_ID_CHARS} chars")
        value = _as_probability(prob)
        if not 0 <= value <= 1:
            raise ValueError("propensity probabilities must lie in [0, 1]")
        if action.strip() in result:
            raise ValueError("propensity action ids must be unique")
        result[action.strip()] = value
    if abs(math.fsum(result.values()) - 1.0) > PROPENSITY_TOLERANCE:
        raise ValueError("propensity probabilities must sum to one")
    return result


@dataclass(frozen=True)
class Request:
    """Spec 4.10. ``inputs`` must be JSON-serialisable; ``capability_versions`` pins
    every capability the executor may use to an exact registry version."""

    handle: str
    description: str
    inputs: dict[str, Any]
    capability_versions: dict[str, int]
    outcome_schema: dict[str, Any]
    deadline_ns: int
    cost_ceiling: Money
    parent_handle: str | None
    completion_criterion: str
    scoring_channel: str
    resource_liability: str
    # The deciding agent's own distribution over its own actions, and the action it
    # took: the essay's one exception to privacy, carried on the request itself.
    # Whatever rebuilds this request keeps the pair — see ``continuation``.
    propensity: dict[str, float] | None = None
    propensity_chosen: str | None = None

    def __post_init__(self) -> None:
        if not self.handle:
            raise ValueError("request needs a handle")
        if self.cost_ceiling < 0:
            raise ValueError("cost_ceiling must be non-negative")
        for forbidden in ("author", "author_id", "requester", "lineage"):
            if forbidden in self.inputs:
                raise ValueError(f"request inputs must not carry {forbidden!r}")
        json.dumps(self.inputs)  # raises if not serialisable
        if self.propensity is not None:
            validate_propensity(self.propensity)
            if self.propensity_chosen is not None and (
                self.propensity_chosen not in self.propensity
            ):
                raise ValueError("the chosen action must belong to the declared action set")

    def continuation(self, *, inputs: dict[str, Any], cost_ceiling: Money) -> Request:
        """The same request again, after its tool calls and children answered.

        Guarantees the continuation is this request with two fields changed and
        nothing else lost: the second call is the billed one that produces the
        final answer, so anything the first call was shown — the PROPENSITY block
        above all — it is shown too. Rebuilding the request field by field is how
        that silently stops being true.
        """
        return replace(self, inputs=inputs, cost_ceiling=cost_ceiling)

    def _world_split(self) -> tuple[dict[str, Any], dict[str, Any]]:
        """Separate the world facts that hold still from the ones that move."""
        world = self.inputs.get("world")
        if not isinstance(world, dict):
            return {}, {}
        stable = {k: v for k, v in world.items() if k in STABLE_WORLD_KEYS}
        return stable, {k: v for k, v in world.items() if k not in STABLE_WORLD_KEYS}

    def stable_prefix(self) -> str:
        """The leading text every request in this world renders identically.

        Guarantees it is a pure function of the world facts named in
        ``STABLE_WORLD_KEYS`` — no description, no identity, no event, no
        account — so two requests to two different assemblies about two
        different events begin with the same bytes, and a provider's automatic
        prefix cache scores a hit on the second of them. It is the empty string
        when the request carries no world block.
        """
        stable, _ = self._world_split()
        if not stable:
            return ""
        return f"{WORLD_HEADER}{json.dumps(stable, sort_keys=True, indent=2)}\n\n"

    def prompt_text(self) -> str:
        """Render the request as the executor sees it: world, description, inputs, schema.

        Guarantees the rendering is a pure function of the request's fields and
        contains no handle, parent, or channel information the executor does
        not need to do the work. The stable world block comes first and nothing
        that moves between calls precedes it.
        """
        stable, moving = self._world_split()
        inputs = {**self.inputs, "world": moving} if stable else self.inputs
        blocks = [
            f"REQUEST\n{self.description}",
            f"INPUTS\n{json.dumps(inputs, sort_keys=True, indent=2)}",
        ]
        if self.propensity is not None:
            blocks.append(
                "PROPENSITY\nThe distribution the deciding agent says it drew from, and the "
                f"action it took ({self.propensity_chosen}). The roads it did not take are "
                "here so you can price them.\n"
                f"{json.dumps(self.propensity, sort_keys=True, indent=2)}"
            )
        blocks.extend([
            f"OUTCOME SCHEMA\n{json.dumps(self.outcome_schema, sort_keys=True, indent=2)}",
            f"COMPLETION CRITERION\n{self.completion_criterion}",
        ])
        return self.stable_prefix() + "\n\n".join(blocks)


@dataclass(frozen=True)
class ChildRequest:
    """A neutral composition contract names a target capability and its complete task."""

    target: str
    description: str
    inputs: dict[str, Any]
    outcome_schema: dict[str, Any]


@dataclass(frozen=True)
class Return:
    """Spec 4.10 plus ``children``: requests the assembly wants routed next."""

    handle: str
    outputs: dict[str, Any]
    cost: Money
    status: str  # "ok" | "malformed" | "refused" | "failed"
    children: tuple[ChildRequest | Request, ...] = field(default_factory=tuple)
    served_by: str | None = None
    stop_reason: str | None = None
    tool_calls: tuple[dict[str, Any], ...] = ()
    # What the provider reported about the completion: finish_reason, input_tokens,
    # output_tokens, reasoning_tokens, cached_tokens (None when unreported) and the
    # max_tokens sent.
    provider: dict[str, Any] = field(default_factory=dict)
