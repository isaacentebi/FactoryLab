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


def _outcome_id(item: Any) -> Any:
    """The exact id of one inbox item: the inbox's own, or the handle it is about.

    R3-F stamps ``outcome_id`` on every delivered item. Where it is present it is
    what ``outcome.get`` and ``ack_through`` address, and it is used verbatim.
    Where it is not, the decision handle is the address those two tools already
    accept. Nothing is minted here: a renderer that invented an id would hand a
    seat an address the kernel would refuse.
    """
    if not isinstance(item, dict):
        return UNAVAILABLE
    return item.get("outcome_id") or item.get("handle") or UNAVAILABLE

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
    "trading_markets", "venue", "venue_listing", "work", "compute_supply",
})

# R3-E. The prefix is no longer derived from the world block at render time: it is
# the WORLD CONTRACT wrapper with the charter's own norms and a compact base
# capability index, serialised once per runtime by
# ``SchematicsMixin._stable_prefix_text`` and carried here as its exact bytes. Two
# requests cannot differ by a character because they render the same string object,
# and a restored runtime recomputes the same string from the same restored state.
# GPT-6's third reading, §8: "Byte stability should be enforced by serializing the
# fixed prefix once and reusing its exact bytes; it does not require copying every
# institutional description into that prefix."
PREFIX_WORLD_KEY = "stable_prefix"
# The world keys the prefix's capability index is built from. They stay in the world
# block, which more than the prompt reads, and are rendered only through the index:
# ``tools`` and ``proposal_shapes`` published a second, longer copy of exactly what
# the index names, which is the largest thing a compaction can remove without
# removing a fact. The argument names and proposal skeletons they carried are a
# ``catalogue.search`` away, retrieved when a seat means to use one.
PREFIX_INDEX_KEYS = frozenset({"tools", "proposal_shapes", "addressing"})

# R4-B. The institutional world: every world key that is constant for the life of
# a runtime — it carries no seat's own facts and no value the runtime moves between
# two requests of one tick — rendered once in the prefix, after the WORLD CONTRACT
# and the capability index, by ``SchematicsMixin._institutional_block``.
#
# R3-E read §8's "it does not require copying every institutional description into
# that prefix" as an instruction to move the institutional description *out* of the
# cached bytes. It shrank the prefix from 52 KB to 10 KB and put the whole world
# block in ``INPUTS``, where nothing is cached: the sentence permits a small prefix,
# it does not ask for an expensive one. Bytes that never change are cheapest where
# a provider can cache them, so they ride here and are suppressed from ``INPUTS``
# below. Nothing is said twice and nothing is said differently; only the place
# changed.
#
# What stays out, and why:
#   ``charter``/``charter_edition`` are constant, but WORLD UPDATE already renders
#   them and a fact is rendered once;
#   ``venue`` is the venue's own instrument record, re-read once a tick, so it is a
#   reading of an outside system rather than a constant of this runtime;
#   everything with an account, a pot, a price, a position, a timestamp, a count or
#   a queue in it moves by construction and is named nowhere here.
#
# A registry key (``catalogue``, ``models``, ``connectors``, ``observations``,
# ``assemblies``, ``contracts``, ``routers``, ``event_kinds``, ``event_schemas``,
# ``sellers``, ``trading_markets``, ``work``) changes only when the population
# ratifies a registration, which happens at a tick boundary and never between two
# requests of one tick. It is the same bargain ``tools`` already took: one cache
# miss on the call after a registration, cached bytes for every call before and
# after it.
PREFIX_CONSTANT_KEYS = frozenset({
    "a_return_may_include", "accounting_facts", "action_labels", "assemblies", "catalogue",
    "clock", "committee", "composition", "compute_supply", "connectors", "contracts",
    "event_kinds", "event_schemas", "mechanics", "meta_input", "models",
    "observation_facts", "observations", "population_tools", "prices",
    "reserved_return_fields", "routers", "scoring", "sellers", "trading_markets",
    "venue_listing", "work",
})

# Every world key the prefix renders, and therefore every world key ``INPUTS`` must
# not render again: the capability index's sources and the institutional block's.
PREFIX_SOURCE_KEYS = PREFIX_INDEX_KEYS | PREFIX_CONSTANT_KEYS

# The moving world block (§8's WORLD UPDATE). ``world_update`` is the rendered
# block; the keys beside it are the sources it is built from, and they are
# suppressed from ``INPUTS`` so each fact is rendered exactly once. They remain in
# the world block itself, which is the runtime's own disclosure surface and is read
# by more than the prompt.
UPDATE_WORLD_KEY = "world_update"
UPDATE_SOURCE_KEYS = frozenset({
    "charter", "charter_edition", "card_prices", "continuity", "governance",
    "pathologies", "recent_mids",
})

# The world keys that are about the acting seat rather than about the world, and
# are lifted out of ``INPUTS`` into the block at the head of the changing part
# (edition 3, C4). ``seats`` carries one row per live seat because one world
# block serves every request built in a tick; only the acting seat's row is ever
# rendered, so no seat reads another's account.
# ``account`` is the custody view's own source: ``custody`` renders the venue's
# equity, cash, margin, positions and spot balances by custody account, and the
# block that published them a second time under ``account`` published the same
# read twice in one prompt.
SEAT_WORLD_KEYS = ("seats", "world_resources", "clock_now", "custody", "account")

# The coalesced world update (edition 3, C2) arrives on the event payload. It is
# what changed while this seat was not awake, which is a fact about the world, so
# R3-E renders it in the ``WORLD UPDATE`` block under §8's own name for it,
# ``changes_since_last_successful_delivery`` — and takes it out of the payload
# there, so the fold is rendered exactly once.
COALESCED_UPDATE = "since_you_last_woke"

# The request inputs C1 supplies for continuity: the seat's own working-state head
# and its unread outcomes. They are rendered inside the ``YOU`` block as
# ``working_state`` and ``outcomes``, and taken out of ``INPUTS`` there, so a
# working state of up to 64 KiB is carried once and appears where a seat looks for
# what it remembers, and never in the moving block.
SEAT_INPUT_KEYS = ("your_state", "unread_outcomes")

# The request input R3-F supplies for the WORLD UPDATE block: the receipts newly
# addressed to this seat. It is rendered there and taken out of ``INPUTS``, so it
# appears exactly once. Absent, the slot says it is unavailable rather than
# claiming there were none.
RECEIPTS_INPUT_KEY = "execution_receipts"

YOU_HEADER = "YOU\n"

WORLD_UPDATE_HEADER = "WORLD UPDATE\n"

#: GPT-6's third reading, §8, verbatim: the outcome-schema text. It is rendered
#: once per request, immediately after the schema it is about.
OUTCOME_CONTRACT = """OUTCOME CONTRACT

Return the public result required by this request's schema. Optional private
continuity fields are working_state and ack_through.

For an execution claim, distinguish:
- intended: no operation has been submitted;
- submitted: an operation identifier exists, but settlement is not known;
- settled: an addressed receipt establishes the consequence;
- rejected: an addressed receipt establishes refusal;
- unknown: the necessary observation is unavailable.

Reference the exact operation or outcome identifier. A narrative assertion does
not establish execution or payment.

For a forecast, identify the claim, observation rule, horizon, probability and
the decision it concerns. Do not replace an unobserved outcome with false.

For a fidelity objection, supply:
{
  "value": "<one fixed norm>",
  "measurement": "<identified card or observation>",
  "evidence": "<specific evidence of a mismatch>",
  "uncertainty": <number from 0 to 1>
}
The objection is a contestable claim. The measurement it challenges cannot
establish its own fidelity.

For a pause, state the next relevant condition when you can identify one.
Do not invent a condition merely to justify a pause.

Use monetary quantities with an explicit asset, custody account and unit.
Keep resource facts separate from learning scores."""

#: What a slot says when the source it would be rendered from is missing. It is a
#: string and never a number, so no reader can mistake an absent fact for a zero.
UNAVAILABLE = "unavailable"

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

    def _world(self) -> dict[str, Any]:
        """This request's world block, or an empty mapping when it carries none."""
        world = self.inputs.get("world")
        return world if isinstance(world, dict) else {}

    def _world_split(self) -> tuple[dict[str, Any], dict[str, Any]]:
        """Return the world's stable facts and its moving ones, in that order.

        Guarantees the two mappings, ``SEAT_WORLD_KEYS``, the prefix key and the
        WORLD UPDATE keys partition the request's world block exactly once: every
        key of it is rendered in the prefix, in the ``YOU`` block, in the
        ``WORLD UPDATE`` block, or inside ``INPUTS``, and in no two of them, so
        rendering them apart publishes the whole world and publishes nothing
        twice. A request whose ``world`` is absent or is not a mapping yields two
        empty mappings.

        ``stable`` is kept for the measurement and for readers that ask which
        world facts hold still; what heads the prompt is ``stable_prefix()``,
        which is the serialised prefix the runtime hands over, not this mapping.
        """
        world = self._world()
        if not world:
            return {}, {}
        inputs_world = self._inputs_world()
        return ({k: v for k, v in inputs_world.items() if k in STABLE_WORLD_KEYS},
                {k: v for k, v in inputs_world.items() if k not in STABLE_WORLD_KEYS})

    def _inputs_world(self) -> dict[str, Any]:
        """The world facts ``INPUTS`` carries: everything no earlier block rendered."""
        elsewhere = (frozenset(SEAT_WORLD_KEYS) | {PREFIX_WORLD_KEY, UPDATE_WORLD_KEY}
                     | UPDATE_SOURCE_KEYS | PREFIX_SOURCE_KEYS)
        return {k: v for k, v in self._world().items() if k not in elsewhere}

    def seat_block(self) -> dict[str, Any]:
        """The acting seat's own account of itself, in §8's ``YOU`` template.

        Guarantees it is built only from this request's fields and the world block
        it carries — every slot is kernel-serialised and no model wrote any of
        it — and that it is scoped: the world's ``seats`` rows hold one per live
        seat and exactly one, the acting seat's, is read.

        Guarantees a slot whose source is missing renders the string
        ``unavailable`` and never a number. A request without a world block, or
        one whose seat the world does not know, still renders every slot and
        still renders ``request``: the handle, the deadline, the ceiling and the
        budget that pays are facts about this decision, and they were the fields
        the executor was never shown. A seat told its venue account is empty when
        the venue would not answer has been taught something false about its own
        world, and the whole of R3 is that this stops happening.
        """
        world = self._world()
        seat = self.inputs.get("you")
        entry = next((row for row in (world.get("seats") or ())
                      if isinstance(row, dict) and row.get("seat_id") == seat), None)
        entry = entry if isinstance(entry, dict) else {}
        custody = world.get("custody") if isinstance(world.get("custody"), dict) else {}
        resources = world.get("world_resources")
        resources = resources if isinstance(resources, dict) else {}
        outcomes = self.inputs.get("unread_outcomes")
        outcomes = outcomes if isinstance(outcomes, dict) else {}
        items = list(outcomes.get("items") or ())
        count = outcomes.get("count")
        return {
            "seat": seat,
            "lineage": entry.get("lineage_id", UNAVAILABLE),
            # Not in §8's template, and kept: the handle, the deadline, the ceiling
            # and the budget that pays are facts about this decision that the
            # decider was never shown before edition 3's C4, and an actor that
            # cannot see its own deadline cannot be answerable for missing it.
            "request": {
                "handle": self.handle,
                "deadline_utc": _utc(self.deadline_ns) or UNAVAILABLE,
                "cost_ceiling_usd": _usd(self.cost_ceiling) or UNAVAILABLE,
                "liable_budget": self.resource_liability,
                "capability_version": entry.get("capability_version", UNAVAILABLE),
            },
            "clock": self._clock(world),
            "working_state": self.inputs.get("your_state", UNAVAILABLE),
            "spending_authority": entry.get("spending_authority", UNAVAILABLE),
            "provider_inventory": self._provider_inventory(resources, entry),
            "venue_accounts": custody.get("venue_accounts", UNAVAILABLE),
            "pending_conversions": custody.get("pending_conversions", UNAVAILABLE),
            "runway": (entry.get("your_resources") or {}).get(
                "runway_at_observed_burn", UNAVAILABLE),
            "subscription": entry.get("subscription", UNAVAILABLE),
            "open_commitments": entry.get("open_commitments", UNAVAILABLE),
            "outcomes": {
                "unread_count": count if type(count) is int else UNAVAILABLE,
                # Oldest first, with the exact id of each item: ``outcome.get`` and
                # ``ack_through`` both take one of these. The id is the inbox's own
                # ``outcome_id`` where it has one and the decision handle otherwise;
                # nothing here is minted by this renderer.
                "items": [{**item, "outcome_id": _outcome_id(item)} for item in items],
                "more": (count - len(items)) if type(count) is int else UNAVAILABLE,
            },
            "directory": entry.get("directory", UNAVAILABLE),
        }

    @staticmethod
    def _provider_inventory(resources: dict[str, Any],
                            entry: dict[str, Any]) -> dict[str, Any] | str:
        """Every rail's balance with its freshness, and which rail pays for this seat.

        Two rails cannot refill each other, so an inventory that does not say
        which one this seat's model is bought on is an inventory a seat cannot
        act on: the credit that matters to it is the credit on its own route.
        Both facts are the kernel's; neither is invented here.
        """
        inventory = resources.get("provider_inventory")
        if not isinstance(inventory, dict):
            return UNAVAILABLE
        own = entry.get("your_resources") or {}
        return {**inventory,
                "your_route": own.get("this_route", UNAVAILABLE),
                "available_on_your_route_usd": own.get(
                    "provider_credit_available_for_this_route_usd") or UNAVAILABLE}

    def _clock(self, world: dict[str, Any]) -> dict[str, Any]:
        """The clock slot: the instant, the tick, how long a tick is, and the last delivery."""
        now = world.get("clock_now")
        now = now if isinstance(now, dict) else {}
        entry = next((row for row in (world.get("seats") or ())
                      if isinstance(row, dict)
                      and row.get("seat_id") == self.inputs.get("you")), None)
        delivery = (entry or {}).get("last_successful_delivery", UNAVAILABLE)
        return {
            "now_utc": now.get("now_utc", UNAVAILABLE),
            "tick_index": now.get("tick_index", UNAVAILABLE),
            "tick_interval_seconds": now.get("tick_interval_seconds", UNAVAILABLE),
            "last_successful_delivery": delivery,
        }

    def world_update_block(self) -> dict[str, Any]:
        """§8's WORLD UPDATE: what moved, joined to what moved for *this* seat.

        Guarantees no private state is here. The two slots that are about this
        request rather than about the world — the fold since this seat's last
        successful delivery, and the receipts newly addressed to it — are the
        seat's own continuity, and they are rendered in this block exactly once
        and nowhere else. Everything else is the world's, and is the same text
        for every seat woken in this tick.
        """
        update = self._world().get(UPDATE_WORLD_KEY)
        update = dict(update) if isinstance(update, dict) else {}
        receipts = self.inputs.get(RECEIPTS_INPUT_KEY)
        return {
            "observation_window": update.get("observation_window", UNAVAILABLE),
            "changes_since_last_successful_delivery": (
                self._coalesced_update() if self._coalesced_update() is not None
                else {"status": UNAVAILABLE,
                      "reason": "this request carries no coalesced world update"}),
            "execution_receipts": (
                receipts if receipts is not None
                else {"status": UNAVAILABLE,
                      "reason": "no addressed receipts are carried on this request"}),
            "charter": update.get("charter", UNAVAILABLE),
            "catalogue": update.get("catalogue", UNAVAILABLE),
            "public_observations": update.get("public_observations", UNAVAILABLE),
            "unavailable_observations": update.get("unavailable_observations", UNAVAILABLE),
        }

    def world_update_text(self) -> str:
        """The rendered WORLD UPDATE block; empty when the request carries no world."""
        if not self._world():
            return ""
        return (f"{WORLD_UPDATE_HEADER}"
                f"{json.dumps(self.world_update_block(), sort_keys=True, indent=2)}\n\n")

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

        R3-E: the bytes are the runtime's, not this renderer's. The world block
        carries the prefix the runtime serialised once —  the WORLD CONTRACT
        wrapper with the charter's own five norms, and the compact base
        capability index — and this returns exactly those bytes. Two requests in
        one world therefore open with the same string object, so a provider's
        automatic prefix cache scores a hit on the second call an assembly makes,
        and there is no second serialisation that could drift from the first.

        It heads ``prompt_text``, which is the whole of the first user message,
        and it is never given to the system role: the capability index publishes
        one-line descriptions the population wrote, and the system role is where
        an instruction outranks the rest of the prompt. It is the empty string
        when the request carries no world block.
        """
        prefix = self._world().get(PREFIX_WORLD_KEY)
        return prefix if isinstance(prefix, str) else ""

    def sections(self) -> tuple[tuple[str, str], ...]:
        """The prompt, in order, as (name, text) pairs: what is rendered and where.

        Guarantees the pairs concatenate to exactly ``prompt_text()``, so the
        bytes counted per section are the bytes actually sent and a measurement
        of the prompt's shape can never drift from the prompt. The order is the
        contract: the stable block first, so a provider's prefix cache can hit;
        then this seat's own account of itself; then the work.
        """
        inputs = ({**self.inputs, "world": self._inputs_world()}
                  if isinstance(self.inputs.get("world"), dict) else self.inputs)
        # The continuity inputs are rendered in ``YOU``, where continuity belongs,
        # and the addressed receipts in ``WORLD UPDATE``. Carrying them here too
        # would put a second copy of a working state — up to 64 KiB of it — in
        # front of every decision, for no reader.
        inputs = {k: v for k, v in inputs.items()
                  if k not in SEAT_INPUT_KEYS and k != RECEIPTS_INPUT_KEY}
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
            # §8's outcome-schema text, once per request and immediately after the
            # schema it is about: what an execution claim must distinguish, what a
            # forecast and an objection must carry, and that money names its asset,
            # its custody account and its unit.
            ("outcome_contract", OUTCOME_CONTRACT),
            ("completion_criterion", f"COMPLETION CRITERION\n{self.completion_criterion}"),
        ])
        joined = [(name, text + ("\n\n" if index + 1 < len(blocks) else ""))
                  for index, (name, text) in enumerate(blocks)]
        return (("stable_prefix", self.stable_prefix()), ("you", self.seat_text()),
                ("world_update", self.world_update_text()), *joined)

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
    # Optional sections of the reply that did not validate and were dropped while
    # the answer stood: ``{"section", "reason"[, "index"]}`` each, in section order.
    dropped: tuple[dict[str, Any], ...] = ()
