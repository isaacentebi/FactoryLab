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
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

Money = int


def public_return(outputs: Any) -> dict[str, Any]:
    """Project a return across a contract boundary, excluding continuity internals."""
    if not isinstance(outputs, dict):
        return {"invalid_return": True}
    return {k: v for k, v in outputs.items()
            if k not in {"working_state", "ack_through", "raw"}}


def _utc(ns: Any) -> str | None:
    """A whole-second UTC stamp for integer nanoseconds, or None."""
    if type(ns) is not int or ns < 0:
        return None
    try:
        return datetime.fromtimestamp(ns // 1_000_000_000, UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    except (OverflowError, OSError, ValueError):
        return None


def _usd(micro: Any) -> str | None:
    """Exact USD text for integer micro-USD, or None for an amount nobody observed."""
    if type(micro) is not int:
        return None
    sign = "-" if micro < 0 else ""
    return f"{sign}{Decimal(abs(micro)).scaleb(-6):f}"

# The world facts that hold still between calls: everything fixed within a charter
# edition and a registration state. They are rendered first, in one contiguous
# block at the head of the first user message — never in the system message, which
# carries the assembly's own prompt and nothing the population wrote — so
# consecutive calls to one assembly begin with byte-identical text and a provider's
# automatic prefix cache (DeepSeek and OpenAI cache on an identical prefix, with no
# cache_control marker) can hit. Everything not named here moves — the account,
# the mids, the pots, the note counts, the pathologies, the reserve, the card prices,
# the scoring values the runtime's own adaptation changes, the governance queue,
# the measured tick — and is rendered after the block, inside ``INPUTS`` with the
# request itself. A key absent from this set is treated as moving, which costs
# cache, never correctness: adding a world key can only shrink the stable prefix.
# A key named here that carries a value the runtime changes between calls is the
# one real error, and it costs the whole prefix, so a block listed here renders
# committed parameters and names the moving value rather than inlining it.
STABLE_WORLD_KEYS = frozenset({
    "a_return_may_include", "accounting_facts", "action_labels", "addressing", "assemblies",
    "catalogue", "charter", "charter_edition", "clock", "committee", "composition",
    "connectors", "contracts", "event_kinds", "event_schemas", "mechanics", "meta_input",
    "models", "observation_facts", "observations", "population_tools", "prices",
    "proposal_shapes", "reserved_return_fields", "routers", "scoring", "sellers", "tools",
    "trading_markets", "venue", "venue_listing", "work",
})

# The world keys that are about the acting seat rather than about the world, and
# are lifted out of ``INPUTS`` into the block at the head of the changing part
# (edition 3, C4). ``seats`` carries one row per live seat because one world
# block serves every request built in a tick; only the acting seat's row is ever
# rendered, so no seat reads another's account.
SEAT_WORLD_KEYS = ("seats", "world_resources", "continuity")

# The coalesced world update (edition 3, C2) arrives on the event payload. It is
# what happened while this seat was not awake, which is continuity, so it is
# rendered in the ``YOU`` block beside the ages of the prices it folds — and taken
# out of the payload there, so the fold is rendered exactly once.
COALESCED_UPDATE = "since_you_last_woke"

# The request inputs C1 supplies for continuity: the seat's own working-state head
# and its unread outcomes. They are rendered inside the ``YOU`` block's
# ``continuity`` under exactly these names, and taken out of ``INPUTS`` there, so a
# working state of up to 64 KiB is carried once and appears where a seat looks for
# what it remembers.
SEAT_INPUT_KEYS = ("your_state", "unread_outcomes")

YOU_HEADER = (
    "YOU\nRendered from the kernel's own state, never by a model, and reconciled with the "
    "ledger: the entitlement is yours net of your holds, the resources are the factory's, "
    "and world.accounting_facts says what they mean. Nothing here is a goal.\n"
)

WORLD_HEADER = (
    "WORLD\nWhat holds for every call in this world while its charter edition and its "
    "registrations stand. The facts that move — the account, the mids, the prices on "
    "the cards, the reserve — arrive below with the work.\n"
)

# Continuity (edition 3, C1). Two fields any answer may carry, published with the
# rest of the reserved return names so a seat can see that it owns them:
# ``working_state`` replaces the seat's own head — the next request of that seat
# renders it verbatim under ``your_state`` — and ``ack_through`` advances that
# seat's outcome-inbox cursor past the named handle. Both are the seat's own
# business: neither is scored, and refusing either changes nothing else about
# the return. The hard size bound lives with the store (``runtime/continuity.py``),
# because it is a property of what the archive will keep, not of the wire type.
CONTINUITY_RETURN_FIELDS: dict[str, dict[str, Any]] = {
    "working_state": {"type": "object"},
    "ack_through": {"type": "string"},
}

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
        """Return the world's stable facts and its moving ones, in that order.

        Guarantees the two mappings and ``SEAT_WORLD_KEYS`` partition the
        request's world block exactly once: every key of it is rendered in the
        stable block, inside ``INPUTS``, or in the ``YOU`` block, and in no two
        of them, so rendering them apart publishes the whole world and publishes
        nothing twice. A request whose ``world`` is absent or is not a mapping
        yields two empty mappings.
        """
        world = self.inputs.get("world")
        if not isinstance(world, dict):
            return {}, {}
        stable = {k: v for k, v in world.items() if k in STABLE_WORLD_KEYS}
        return stable, {k: v for k, v in world.items()
                        if k not in STABLE_WORLD_KEYS and k not in SEAT_WORLD_KEYS}

    def seat_block(self) -> dict[str, Any]:
        """The acting seat's own account of itself, in GPT-6 §9's five sections.

        Guarantees it is built only from this request's fields and the world block
        it carries — no model wrote any of it — and that it is scoped: the world's
        ``seats`` rows hold one per live seat and exactly one, the acting
        seat's, is read. A request without a world block, or one whose seat the
        world does not know, still renders ``self``: the handle, the deadline, the
        ceiling and the budget that pays are facts about this request, and they
        were the fields the executor was never shown.

        The coalesced world update (edition 3, C2) is continuity too: it is
        everything that happened while this seat was not awake. It is lifted out
        of the event payload and rendered here, beside the ages of the prices it
        folds, and ``sections`` removes it from the payload so it is rendered
        once.
        """
        world = self.inputs.get("world")
        world = world if isinstance(world, dict) else {}
        seat = self.inputs.get("you")
        entry = next((row for row in (world.get("seats") or ())
                      if isinstance(row, dict) and row.get("seat_id") == seat), None)
        entry = entry if isinstance(entry, dict) else {}
        continuity = dict(world.get("continuity") or {})
        block: dict[str, Any] = {
            "self": {
                "seat_id": seat,
                "lineage_id": entry.get("lineage_id"),
                "capability_version": entry.get("capability_version"),
                "request_handle": self.handle,
                "request_deadline_utc": _utc(self.deadline_ns),
                "request_cost_ceiling_usd": _usd(self.cost_ceiling),
                "liable_budget": self.resource_liability,
            },
            "your_resources": entry.get("your_resources"),
            "world_resources": world.get("world_resources"),
            "continuity": {
                # C1 supplies these two on the request; until it lands they are
                # rendered as absent rather than invented.
                "your_state": self.inputs.get("your_state"),
                "unread_outcomes": self.inputs.get("unread_outcomes")
                                   or {"count": 0, "items": []},
                "open_commitments": entry.get("open_commitments"),
                "shared_directory_changes": continuity.get("shared_directory_changes"),
                "since_you_last_woke": self._coalesced_update(),
                "market_data_as_of": continuity.get("market_data_as_of"),
            },
        }
        return block

    def _coalesced_update(self) -> Any:
        """The fold of everything that happened while this seat slept, or None."""
        payload = self.inputs.get("payload")
        if not isinstance(payload, dict):
            return None
        return payload.get(COALESCED_UPDATE)

    def seat_text(self) -> str:
        """The rendered ``YOU`` block; empty only when there is no request to describe."""
        return f"{YOU_HEADER}{json.dumps(self.seat_block(), sort_keys=True, indent=2)}\n\n"

    def stable_prefix(self) -> str:
        """The leading text every request in this world renders identically.

        Guarantees it is a pure function of the world facts named in
        ``STABLE_WORLD_KEYS`` — no description, no identity, no event, no
        account — so two requests about two different events render the same
        bytes here, and a provider's automatic prefix cache scores a hit on the
        second call an assembly makes. It heads ``prompt_text``, which is the
        whole of the first user message, and it is never given to the system
        role: the block publishes catalogues the population writes, and the
        system role is where an instruction outranks the rest of the prompt. It
        is the empty string when the request carries no world block.
        """
        stable, _ = self._world_split()
        if not stable:
            return ""
        return f"{WORLD_HEADER}{json.dumps(stable, sort_keys=True, indent=2)}\n\n"

    def sections(self) -> tuple[tuple[str, str], ...]:
        """The prompt, in order, as (name, text) pairs: what is rendered and where.

        Guarantees the pairs concatenate to exactly ``prompt_text()``, so the
        bytes counted per section are the bytes actually sent and a measurement
        of the prompt's shape can never drift from the prompt. The order is the
        contract: the stable block first, so a provider's prefix cache can hit;
        then this seat's own account of itself; then the work.
        """
        stable, moving = self._world_split()
        inputs = ({**self.inputs, "world": moving}
                  if isinstance(self.inputs.get("world"), dict) else self.inputs)
        # The continuity inputs are rendered in ``YOU``, where continuity belongs.
        # Carrying them here too would put a second copy of a working state — up
        # to 64 KiB of it — in front of every decision, for no reader.
        inputs = {k: v for k, v in inputs.items() if k not in SEAT_INPUT_KEYS}
        payload = inputs.get("payload")
        if isinstance(payload, dict) and COALESCED_UPDATE in payload:
            inputs = {**inputs,
                      "payload": {k: v for k, v in payload.items() if k != COALESCED_UPDATE}}
        blocks = [
            ("request", f"REQUEST\n{self.description}"),
            ("inputs", f"INPUTS\n{json.dumps(inputs, sort_keys=True, indent=2)}"),
        ]
        if self.propensity is not None:
            blocks.append((
                "propensity",
                "PROPENSITY\nThe distribution the deciding agent says it drew from, and the "
                f"action it took ({self.propensity_chosen}). The roads it did not take are "
                "here so you can price them.\n"
                f"{json.dumps(self.propensity, sort_keys=True, indent=2)}",
            ))
        blocks.extend([
            ("outcome_schema",
             f"OUTCOME SCHEMA\n{json.dumps(self.outcome_schema, sort_keys=True, indent=2)}"),
            ("completion_criterion", f"COMPLETION CRITERION\n{self.completion_criterion}"),
        ])
        joined = [(name, text + ("\n\n" if index + 1 < len(blocks) else ""))
                  for index, (name, text) in enumerate(blocks)]
        return (("stable_prefix", self.stable_prefix()), ("you", self.seat_text()), *joined)

    def section_bytes(self) -> dict[str, int]:
        """UTF-8 bytes rendered per prompt section, plus the whole under ``total``.

        This is the measurement the compact catalogue is answerable to: moving a
        schema behind ``catalogue.search`` is a claim about bytes, and a claim
        about bytes belongs in the diary beside the bill it is supposed to move.
        """
        counts = {name: len(text.encode("utf-8")) for name, text in self.sections()}
        return {**counts, "total": sum(counts.values())}

    def prompt_text(self) -> str:
        """Render the request as the executor sees it: world, self, description, inputs, schema.

        Guarantees the rendering is a pure function of the request's fields and
        contains no parent or channel information the executor does not need to
        do the work; that it opens with ``stable_prefix()`` and that nothing
        which moves between calls precedes that block; that the seat's own
        account of itself (``YOU``) heads the changing part, once; and that the
        world it publishes is the whole world exactly once — the stable facts in
        the block, the seat-scoped ones in ``YOU``, the rest inside ``INPUTS``,
        by the partition ``_world_split`` and ``SEAT_WORLD_KEYS`` make.

        The handle, deadline, ceiling and liable budget now appear, in ``YOU``.
        They are facts about this decision that the decider was never shown, and
        an actor that cannot see its own deadline or its own ceiling cannot be
        answerable for missing either.
        """
        return "".join(text for _name, text in self.sections())


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
