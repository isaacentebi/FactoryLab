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
# The least mass the action taken may carry before it weights a reward. A declared
# probability is unverifiable, so it is clipped before it becomes an importance
# weight: one reward can move a learner by at most 1 / MIN_DECLARED_MASS times the
# on-policy step, whatever the return claimed.
MIN_DECLARED_MASS = 0.05
# The venue and treasury tools whose execution is an action in its own right.
EFFECT_TOOLS: Mapping[str, str] = MappingProxyType({
    "venue.place_market": "order", "venue.place_limit": "order", "venue.close": "close",
    "venue.cancel": "cancel", "venue.set_leverage": "leverage", "treasury.transfer": "transfer",
    # The vault surface's writes ([venue] vault_tools), named "vault:<operation>".
    "venue.vault_create": "vault", "venue.vault_deposit": "vault",
    "venue.vault_withdraw": "vault",
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
    return f"{kind}:{str(args.get('coin', '')).strip().upper()}"[:64]


def canonical_label(label: str) -> str:
    """Canonicalize only the published vocabulary; custom action ids remain exact."""
    parts = label.split(":")
    if len(parts) == 3 and parts[0].lower() in ("buy", "sell") and parts[2].lower() in (
            "xs", "s", "m", "l", "xl"):
        return f"{parts[0].lower()}:{parts[1].upper()}:{parts[2].lower()}"
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
    if status == "refused" and outputs.get("status") == "cannot":
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
                         "vault:"))
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


def action_vocabulary() -> dict[str, str]:
    """The public shape of an action label, stated once for every role."""
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
        f'{size_band_vocabulary()}; "malformed" when the return did not parse or '
        "named no placeable size. What a return executes before its final answer is "
        "part of its action: a venue.place_market or venue.place_limit tool call is "
        'named like an order, venue.close "close:<COIN>", venue.cancel "cancel:<COIN>", '
        'venue.set_leverage "leverage:<COIN>", treasury.transfer "transfer:<direction>" '
        'and a requested child "request:<assembly id>"; several are joined with "+" in '
        'execution order, and a trade through a tool followed by "hold" is named by '
        "the trade",
        "antagonist": "the same labels as a producer",
        "evaluator": '"verdict:<q>" with q the verdict rounded to one decimal, e.g. '
        '"verdict:0.8"',
        "meta": '"conformity:<c>", rounded the same way',
    }


def _floored(probs: tuple[float, ...], chosen: int) -> tuple[float, ...]:
    """Return the same distribution with the chosen action's mass at the floor or above.

    Guarantees ``probs[chosen] >= MIN_DECLARED_MASS`` in the recorded numbers, so
    one reward moves a learner by at most 1 / MIN_DECLARED_MASS times the on-policy
    step. Raising the declared mass before normalising does not guarantee it: the
    other actions still sum to nearly one, and dividing by the new total puts the
    action taken back under the floor. The rest are rescaled to make room instead.
    """
    if probs[chosen] >= MIN_DECLARED_MASS:
        return probs
    rest = math.fsum(p for i, p in enumerate(probs) if i != chosen)
    scale = (1.0 - MIN_DECLARED_MASS) / rest if rest > 0 else 0.0
    return tuple(MIN_DECLARED_MASS if i == chosen else p * scale for i, p in enumerate(probs))


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
    see why its disclosure was refused simply repeats it. A declaration that gives
    the action taken less than ``MIN_DECLARED_MASS`` is used with that mass
    raised to the floor and the rest rescaled around it, so the recorded mass on
    the action taken is at least the floor (a refused declaration is recorded
    degenerate, over the one action taken; a floored one keeps its support): the
    probability is the agent's unverifiable report, and the importance weight it
    becomes is bounded.
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
            if distribution[label] < MIN_DECLARED_MASS:
                reason = (f"the action taken ({label}) declared {distribution[label]:.3g} "
                          f"mass; floored to {MIN_DECLARED_MASS} before it weights a reward, "
                          "and the other actions rescaled so the floor survives normalising")
        except ValueError as exc:
            distribution, reason = None, str(exc)
    if distribution is None:
        distribution = {label: 1.0}
    actions = tuple(distribution)
    total = math.fsum(distribution.values())
    probs = _floored(tuple(distribution[a] / total for a in actions), actions.index(label))
    record = PropensityRecord(
        actions, probs, label, 0, learner_id, state_hash, source="declared",
    )
    return record, reason


def as_public(record: PropensityRecord) -> dict[str, Any]:
    """Render a propensity as it travels: a distribution and the action taken."""
    return {
        "over": dict(zip(record.action_ids, record.probs, strict=True)),
        "chosen": record.chosen,
        "source": record.source,
    }
