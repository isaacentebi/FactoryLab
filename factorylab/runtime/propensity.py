"""The deciding agent's own propensity.

Essay II.I.b: a reward that is only a score builds ordinary no-regret learners,
because nothing in it lets a learner reconstruct a road not taken. The remedy is
the propensity score — "an agent's own accounting of the statistical field it
drew from when making its decision, which it discloses at decision time alongside
the result it produces" — and it is the one thing the essay directs forward
within a request while the rest of an agent's local state stays private.

The router's propensity over *which assembly to wake* is a different
distribution, and it stays exactly as it was. This module is about the decision
the woken assembly actually makes: order or hold, which coin, how big, which
verdict.

Three facts make a declared propensity usable rather than decorative:

* the kernel names the action that was taken, so the declaration is about
  something it can check (``action_label``);
* the declaration is recorded as a second ``PropensityRecord`` on the same
  handle, so the reward that arrives rounds later still finds it;
* absent or malformed, it is recorded as degenerate — the chosen action at 1.0 —
  so every decision has an accounting, and a silent agent simply declares that it
  considered nothing else.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from decimal import Decimal
from types import MappingProxyType
from typing import Any

from factorylab.cortex.assembly import declines
from factorylab.cortex.request import validate_propensity
from factorylab.kernel.queue import PropensityRecord

MALFORMED = "malformed"
#: A seat answered ``{"status": "cannot", "reason": ...}``: it declined paid work
#: it was commissioned for. That is a decision with a name, not a parse failure.
DECLINED = "declined"
HOLD = "hold"
#: The action vocabulary of edition 3, C2. A seat's alternatives are not "hold or
#: trade": deciding to look, to build, to legislate or to sleep are the choices
#: this experiment wants to select over, and naming every one of them ``hold``
#: erases exactly that variation. The kernel classifies each answer into one of
#: these six and ledgers it beside the finer label, so a declaration may be made
#: over the verbs or over the exact labels.
ACTION_CLASSES = ("hold", "investigate", "build", "govern", "defer", "order")
#: Registration kinds that are a move in the factory's politics rather than a build.
GOVERNING_KINDS = frozenset({"amendment", "challenge", "retire"})
# The venue and treasury tools whose execution is an action in its own right.
EFFECT_TOOLS: Mapping[str, str] = MappingProxyType({
    "venue.place_market": "order", "venue.place_limit": "order", "venue.close": "close",
    "venue.cancel": "cancel", "venue.set_leverage": "leverage", "treasury.transfer": "transfer",
    # The vault surface's writes ([venue] vault_tools), named "vault:<operation>".
    "venue.vault_create": "vault", "venue.vault_deposit": "vault",
    "venue.vault_withdraw": "vault",
    # Polymarket's writes ([polymarket] on the simulated venue), named "polymarket:...".
    # An outcome token's id is not in the name: it is up to 78 digits, one per outcome
    # of every market, and a label is at most 64 characters.
    "polymarket.place_limit": "polymarket", "polymarket.cancel": "polymarket",
})
# How big the order was, in the base units the return declared, as a closed
# vocabulary of five bands. Sizing is a decision — half a position and a tenth of
# one are not the same choice — so the label has to be able to say which was
# taken, and a band says it in a name a learner can hold one arm for.
SIZE_BANDS: tuple[tuple[Decimal, str], ...] = (
    (Decimal("0.01"), "xs"),
    (Decimal("0.1"), "s"),
    (Decimal("1"), "m"),
    (Decimal("10"), "l"),
)
SIZE_BAND_MAX = "xl"


def size_band(size: Any) -> str | None:
    """Bucket a declared order size into the published band vocabulary.

    Returns ``None`` when the size is missing or is not a positive number, which
    is the same size the venue refuses: an order the kernel cannot place did not
    decide a trade.
    """
    try:
        value = Decimal(str(size).strip())
    except (ArithmeticError, AttributeError, TypeError, ValueError):
        return None
    if not value.is_finite() or value <= 0:
        return None
    for edge, name in SIZE_BANDS:
        if value < edge:
            return name
    return SIZE_BAND_MAX


def _order_label(args: dict[str, Any]) -> str:
    side = str(args.get("side", "buy")).strip().lower()
    coin = str(args.get("coin", "")).strip().upper()
    band = size_band(args.get("size"))
    if side not in ("buy", "sell") or not coin or band is None:
        return MALFORMED
    return f"{side}:{coin}:{band}"


def effect_label(tool: str, args: Any) -> str | None:
    """Name an executed venue or treasury tool call as an action, or None for any other tool.

    Guarantees that a trade made through a tool has the same name as the same
    trade made in the final answer, so a return cannot trade under one label and
    declare under another.
    """
    kind = EFFECT_TOOLS.get(tool)
    if kind is None:
        return None
    args = args if isinstance(args, dict) else {}
    if kind == "order":
        return _order_label(args)
    if kind == "transfer":
        return f"transfer:{str(args.get('direction', '')).strip().lower()}"[:64]
    if kind == "vault":
        return f"vault:{tool.removeprefix('venue.vault_')}"
    if kind == "polymarket":
        if tool == "polymarket.cancel":
            return "polymarket:cancel"
        side = str(args.get("side", "")).strip().lower()
        band = size_band(args.get("size"))
        if side not in ("buy", "sell") or band is None:
            return MALFORMED
        return f"polymarket:{side}:{band}"
    return f"{kind}:{str(args.get('coin', '')).strip().upper()}"[:64]


def canonical_label(label: str) -> str:
    """Canonicalize only the published vocabulary; custom action ids remain exact."""
    parts = label.split(":")
    # ``side:coin:band``; a HIP-3 coin carries its dex (``buy:xyz:TSLA:m``), so the coin
    # is every part between the side and the band.
    if len(parts) >= 3 and parts[0].lower() in ("buy", "sell") and parts[-1].lower() in (
            "xs", "s", "m", "l", "xl"):
        return f"{parts[0].lower()}:{':'.join(parts[1:-1]).upper()}:{parts[-1].lower()}"
    if len(parts) == 2 and parts[0] in ("verdict", "conformity"):
        try:
            value = float(parts[1])
            if math.isfinite(value) and 0 <= value <= 1:
                return f"{parts[0]}:{round(value, 1):.1f}"
        except ValueError:
            pass
    return label


def action_label(role: str, outputs: dict[str, Any], status: str,
                 effects: tuple[str, ...] = ()) -> str:
    """Name the action a return took, in the public vocabulary of its role.

    Producers and antagonists decide to hold, or to trade a side of a coin at a
    size; judges decide a number, bucketed to one decimal. Each part is bucketed
    so that a decision has a name a learner can hold an arm for — including the
    sizing, which two otherwise identical orders differ only in. A return that
    did not parse, or that ordered a size the venue could not place, decided
    nothing nameable.

    ``effects`` are the actions the return already executed before its final
    answer — venue and treasury tools, requested children — in execution order.
    They come first in the label, so a return that traded through a tool and then
    answered ``hold`` is named by its trade, not by its last word.
    """
    if not isinstance(outputs, dict):
        return MALFORMED
    if status == "refused" and declines(outputs):
        # Declining paid work is a decision, not a failure to parse (R3-F). A seat
        # that answers ``cannot`` on a judge or meta commission has said something
        # nameable, and a learner that cannot hold an arm for declining cannot
        # learn that declining was right.
        return DECLINED
    if status != "ok":
        return MALFORMED
    if role in ("evaluator", "meta", "adversary"):
        key = "conformity" if role == "meta" else "verdict"
        value = outputs.get(key)
        if type(value) not in (int, float) or isinstance(value, bool):
            return MALFORMED
        return f"{key}:{min(1.0, max(0.0, round(float(value), 1))):.1f}"
    parts = list(effects)
    action = str(outputs.get("action", "")).strip().lower()
    if action == "order":
        # After a venue write the answer's "order" reports that trade and is never
        # executed (a decision acts once), so it adds no second name to the label.
        if not parts:
            parts.append(_order_label(outputs))
    elif action.startswith(("buy:", "sell:")):
        # A propensity label is not an executable order or an observed trade.
        parts.append(MALFORMED)
    elif action not in ("", "noop", HOLD):
        parts.append(action)
    if MALFORMED in parts:
        return MALFORMED
    if not parts:
        return HOLD
    return "+".join(parts)[:64]


def action_class(label: str, outputs: Any, *, tool_calls: int = 0) -> str:
    """Name which of the six actions an answer actually took (edition 3, C2).

    The finer label stays what it was — a learner still holds an arm for
    ``buy:BTC:xs`` — and this is the coarse verb beside it, so a seat may
    declare its propensity over the verbs without having to guess the exact
    order it would end up placing. A judgement (``verdict:``, ``conformity:``)
    is that seat's product, not a producer's choice among these six, and keeps
    its own label as its class.
    """
    if label in (MALFORMED, DECLINED):
        return label
    if label.startswith(("verdict:", "conformity:")):
        return label
    outputs = outputs if isinstance(outputs, dict) else {}
    parts = label.split("+")
    if any(p.startswith(("buy:", "sell:", "close:", "cancel:", "leverage:", "transfer:",
                         "vault:", "polymarket:"))
           for p in parts):
        return "order"
    register = outputs.get("register")
    kinds = {str(item.get("kind")) for item in register
             if isinstance(item, dict)} if isinstance(register, list) else set()
    if kinds & GOVERNING_KINDS:
        return "govern"
    if kinds:
        return "build"
    if tool_calls or any(p.startswith("request:") for p in parts):
        return "investigate"
    if outputs.get("defer") or str(outputs.get("action", "")).strip().lower() == "defer":
        return "defer"
    return HOLD


def size_band_vocabulary() -> str:
    """The bands, spelled out, so a declaration can name the sizes it weighed."""
    edges = ", ".join(f'"{name}" (< {edge} base units)' for edge, name in SIZE_BANDS)
    return f'{edges}, "{SIZE_BAND_MAX}" ({SIZE_BANDS[-1][0]} base units or more)'


#: How every role's decline is labelled, published with each vocabulary.
DECLINED_LABEL_TEXT = (f'a decline (status "cannot") is labelled "{DECLINED}"; '
                       f'a return the kernel could not use, "{MALFORMED}"')


def action_vocabulary() -> dict[str, str]:
    """The public shape of an action label, stated once for every role.

    Guarantees every role's entry names every label ``action_label`` gives that role,
    ``declined`` and ``malformed`` included, so a seat can declare its propensity over
    the actions the kernel will name its answer by.
    """
    judged = {
        "evaluator": '"verdict:<q>" with q the verdict rounded to one decimal, e.g. '
                     '"verdict:0.8"',
        "meta": '"conformity:<c>" with c the conformity rounded to one decimal, e.g. '
                '"conformity:0.6"',
        "adversary": '"verdict:<q>" with q the counter-verdict rounded to one decimal, '
                     'e.g. "verdict:0.4"',
    }
    return {
        **{role: f"{text}; {DECLINED_LABEL_TEXT}"
           for role, text in _producer_vocabulary().items()},
        **{role: f"{text}; {DECLINED_LABEL_TEXT}" for role, text in judged.items()},
    }


def propensity_field(role: str) -> dict[str, Any]:
    """The ``propensity`` field an outcome schema publishes for ``role``.

    Chapter II §I.b (the schematics are public, the structures of requests and
    rewards among them): the field states, where it is defined, the labels the
    kernel names this role's answer by, generated from ``action_vocabulary``, the
    single source. It states no reason to declare one.
    """
    return {"type": "object",
            "description": "your own distribution over your own actions, {action_id: "
                           "probability} summing to one; the kernel labels this "
                           f"answer's action {action_vocabulary()[role]}"}


def _producer_vocabulary() -> dict[str, str]:
    """The producing roles' labels, before the decline and malformed labels."""
    return {
        "producer": 'hold, investigate (tool calls and no order), build (a '
        "registration), govern (a proposal or a challenge), defer (sleep through "
        "routine ticks), order — the six actions a propensity may be declared over; "
        'the kernel classifies your answer into one of them and ledgers it beside the '
        "finer label below. The finer label is hold, or "
        '"<side>:<COIN>:<size band>" for an order, e.g. '
        '"buy:BTC:xs". Labels belong in propensity, not the action field: execute with '
        '"action": "order" and explicit coin, side and numeric size. A decision acts '
        "once: after a venue tool wrote in this decision, \"action\": \"order\" with no "
        "coin, side or size reports that trade, and an answer never places a second "
        'order. The size band '
        'buckets the size you declared, in base units: '
        f'{size_band_vocabulary()}; an order that named no placeable size is '
        '"malformed". What a return executes before its final answer is '
        "part of its action: a venue.place_market or venue.place_limit tool call is "
        'named like an order, venue.close "close:<COIN>", venue.cancel "cancel:<COIN>", '
        'venue.set_leverage "leverage:<COIN>", treasury.transfer "transfer:<direction>", '
        'polymarket.place_limit "polymarket:<side>:<size band>" (size in outcome tokens), '
        'polymarket.cancel "polymarket:cancel" '
        'and a requested child "request:<assembly id>"; several are joined with "+" in '
        'execution order, and a trade through a tool followed by "hold" is named by '
        "the trade",
        "antagonist": "the same labels as a producer",
    }


def declared_record(
    label: str,
    declared: Any,
    *,
    learner_id: str,
    state_hash: str,
    taken_class: str | None = None,
) -> tuple[PropensityRecord, str | None]:
    """Build the second propensity on a handle; degenerate when none was declared.

    Returns the record and, when a declaration was offered but could not be used
    as offered, the reason — the population is told, because an agent that cannot
    see why its disclosure was refused simply repeats it. A usable declaration is
    recorded exactly as declared, normalised and never floored (essay II.I.b: "an
    agent's own accounting of the statistical field"; audit s06 #4): what bounds
    its importance weight is the estimator inside the learner that reads it
    (docs/architecture/learners-noregret.md §2.4), never a rewrite of the record. A
    refused declaration is recorded degenerate, over the one action taken.
    """
    reason: str | None = None
    distribution: dict[str, float] | None = None
    if declared is not None:
        try:
            raw = validate_propensity(declared)
            distribution = {}
            for action, mass in raw.items():
                action = canonical_label(action)
                distribution[action] = distribution.get(action, 0.0) + mass
            if label not in distribution and taken_class in distribution:
                # Edition 3, C2: a declaration over the six actions names the verb,
                # not the exact order it would have been. The action taken is then
                # that verb, and the weight it carries is the mass declared on it.
                label = taken_class
            if label not in distribution:
                raise ValueError(
                    f"propensity must include the action taken ({label})"
                    + (f" or its action class ({taken_class})"
                       if taken_class and taken_class != label else ""))
            if distribution[label] <= 0:
                raise ValueError(f"the action taken ({label}) needs positive mass")
        except ValueError as exc:
            distribution, reason = None, str(exc)
    if distribution is None:
        distribution = {label: 1.0}
    actions = tuple(distribution)
    total = math.fsum(distribution.values())
    probs = tuple(distribution[a] / total for a in actions)
    record = PropensityRecord(
        actions, probs, label, 0, learner_id, state_hash, source="declared",
    )
    return record, reason


#: The vault operations a ``vault:<operation>`` label names (``effect_label``).
VAULT_OPERATIONS = frozenset({"create", "deposit", "withdraw"})
#: The prefix of an opaque alias for an identifier outside the published vocabulary.
ALIAS = "other"
_BANDS = frozenset({name for _, name in SIZE_BANDS} | {SIZE_BAND_MAX})
#: The published domain of a judged label's operand: a score in [0, 1] at one decimal,
#: spelled as ``action_label`` spells it ("0.0" … "1.0"). Enumerated, so membership is
#: the whole check: no text, no non-finite and no out-of-range number is in it.
_ONE_DECIMAL = frozenset(f"{tenth / 10:.1f}" for tenth in range(11))


def _published_part(part: str, markets: frozenset[str]) -> bool:
    if part in (*ACTION_CLASSES, DECLINED, MALFORMED):
        return True
    head, _, rest = part.partition(":")
    if head in ("verdict", "conformity"):
        return rest in _ONE_DECIMAL
    if head in ("buy", "sell"):
        coin, _, band = rest.rpartition(":")
        return band in _BANDS and coin in markets
    if head in ("close", "cancel", "leverage"):
        return rest in markets
    if head == "transfer":
        from factorylab.world.treasury import TRANSFER_DIRECTIONS

        return rest in TRANSFER_DIRECTIONS
    if head == "vault":
        return rest in VAULT_OPERATIONS
    if head == "polymarket":
        side, _, band = rest.partition(":")
        return rest == "cancel" or (side in ("buy", "sell") and band in _BANDS)
    return False


def published_label(label: str, markets: frozenset[str]) -> bool:
    """Whether ``label`` is an action identifier of the published vocabulary.

    Guarantees True only for a label ``action_vocabulary`` publishes whose every
    operand lies in its public domain: a coin in ``markets`` (upper case, as labels
    spell it), a transfer direction, a vault operation, a size band, a one-decimal
    verdict. Free text cannot pass as an operand, so nothing an author wrote survives.
    """
    return all(_published_part(part, markets) for part in label.split("+"))


def neutral_projection(over: Mapping[str, float], chosen: str | None,
                       markets: frozenset[str]) -> tuple[dict[str, float], str | None]:
    """A declared distribution as it may travel to another seat: author-neutral.

    Chapter II §I.b: the request is "neutral with respect to its author"; AGENTS rule 5.
    Guarantees the same masses in the same order; every identifier either a
    ``published_label`` or an opaque alias ``other:<n>``, numbered in declaration
    order and fresh for this projection; and ``chosen`` mapped by the same map. The
    original identifiers stay on the sealed record, for attribution and learning.
    """
    names: dict[str, str] = {}
    aliases = 0
    for action in over:
        if published_label(action, markets):
            names[action] = action
        else:
            aliases += 1
            names[action] = f"{ALIAS}:{aliases}"
    if chosen is not None and chosen not in names:
        chosen = chosen if published_label(chosen, markets) else None
    return ({names[a]: p for a, p in over.items()},
            None if chosen is None else names.get(chosen, chosen))


def as_public(record: PropensityRecord, markets: frozenset[str]) -> dict[str, Any]:
    """Render a propensity as it travels: a distribution and the action taken.

    Guarantees the identifiers are ``neutral_projection``'s, so no text the deciding
    seat wrote reaches the seat that reads it.
    """
    over, chosen = neutral_projection(
        dict(zip(record.action_ids, record.probs, strict=True)), record.chosen, markets)
    return {"over": over, "chosen": chosen, "source": record.source}
