"""The deciding agent's own propensity (spec A10).

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

from decimal import Decimal
from typing import Any

from factorylab.cortex.request import validate_propensity
from factorylab.kernel.queue import PropensityRecord

MALFORMED = "malformed"
HOLD = "hold"
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


def action_label(role: str, outputs: dict[str, Any], status: str) -> str:
    """Name the action a return took, in the public vocabulary of its role.

    Producers and antagonists decide to hold, or to trade a side of a coin at a
    size; judges decide a number, bucketed to one decimal. Each part is bucketed
    so that a decision has a name a learner can hold an arm for — including the
    sizing, which two otherwise identical orders differ only in. A return that
    did not parse, or that ordered a size the venue could not place, decided
    nothing nameable.
    """
    if status != "ok" or not isinstance(outputs, dict):
        return MALFORMED
    if role in ("evaluator", "meta"):
        key = "verdict" if role == "evaluator" else "conformity"
        value = outputs.get(key)
        if type(value) not in (int, float) or isinstance(value, bool):
            return MALFORMED
        return f"{key}:{min(1.0, max(0.0, round(float(value), 1))):.1f}"
    action = str(outputs.get("action", "")).strip().lower()
    if action in ("", "noop", HOLD):
        return HOLD
    if action != "order":
        return action[:64]
    side = str(outputs.get("side", "")).strip().lower()
    coin = str(outputs.get("coin", "")).strip().upper()
    band = size_band(outputs.get("size"))
    if side not in ("buy", "sell") or not coin or band is None:
        return MALFORMED
    return f"{side}:{coin}:{band}"[:64]


def size_band_vocabulary() -> str:
    """The bands, spelled out, so a declaration can name the sizes it weighed."""
    edges = ", ".join(f'"{name}" (< {edge} base units)' for edge, name in SIZE_BANDS)
    return f'{edges}, "{SIZE_BAND_MAX}" ({SIZE_BANDS[-1][0]} base units or more)'


def action_vocabulary() -> dict[str, str]:
    """The public shape of an action label, stated once for every role."""
    return {
        "producer": 'hold, or "<side>:<COIN>:<size band>" for an order, e.g. '
        '"buy:BTC:xs"; the size band buckets the size you declared, in base units: '
        f'{size_band_vocabulary()}; "malformed" when the return did not parse or '
        "named no placeable size",
        "antagonist": "the same labels as a producer",
        "evaluator": '"verdict:<q>" with q the verdict rounded to one decimal, e.g. '
        '"verdict:0.8"',
        "meta": '"conformity:<c>", rounded the same way',
    }


def declared_record(
    label: str,
    declared: Any,
    *,
    learner_id: str,
    state_hash: str,
) -> tuple[PropensityRecord, str | None]:
    """Build the second propensity on a handle; degenerate when none was declared.

    Returns the record and, when a declaration was offered but could not be used,
    the reason — the population is told, because an agent that cannot see why its
    disclosure was refused simply repeats it.
    """
    reason: str | None = None
    distribution: dict[str, float] | None = None
    if declared is not None:
        try:
            distribution = validate_propensity(declared)
            if label not in distribution:
                raise ValueError(f"propensity must include the action taken ({label})")
            if distribution[label] <= 0:
                raise ValueError(f"the action taken ({label}) needs positive mass")
        except ValueError as exc:
            distribution, reason = None, str(exc)
    if distribution is None:
        distribution = {label: 1.0}
    actions = tuple(distribution)
    total = sum(distribution.values())
    probs = tuple(distribution[a] / total for a in actions)
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
