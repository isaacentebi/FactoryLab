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
from dataclasses import dataclass, field
from typing import Any

Money = int

# A10: the kernel bounds only the size of a declared action set, never its contents.
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

    def prompt_text(self) -> str:
        """Render the request as the executor sees it: description, inputs, schema, criterion.

        Guarantees the rendering is a pure function of the request's fields and
        contains no handle, parent, or channel information the executor does
        not need to do the work.
        """
        blocks = [
            f"REQUEST\n{self.description}",
            f"INPUTS\n{json.dumps(self.inputs, sort_keys=True, indent=2)}",
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
        return "\n\n".join(blocks)


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
    # output_tokens, reasoning_tokens (None when unreported) and the max_tokens sent.
    provider: dict[str, Any] = field(default_factory=dict)
