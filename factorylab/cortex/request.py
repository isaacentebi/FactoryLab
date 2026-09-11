"""Request and Return: the rich request line and the thin return line.

A ``Request`` is everything an executor needs and nothing about who asked.
Author identity never appears here; lineage is the sealed ledger's business.
A ``Return`` carries the outputs, the committed cost, a status, and any child
requests the assembly wants routed. Learning feedback is *not* on the Return;
it travels on the reward channel as a thin scalar addressed to the handle.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

Money = int


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

    def __post_init__(self) -> None:
        if not self.handle:
            raise ValueError("request needs a handle")
        if self.cost_ceiling < 0:
            raise ValueError("cost_ceiling must be non-negative")
        for forbidden in ("author", "author_id", "requester", "lineage"):
            if forbidden in self.inputs:
                raise ValueError(f"request inputs must not carry {forbidden!r}")
        json.dumps(self.inputs)  # raises if not serialisable

    def prompt_text(self) -> str:
        """Render the request as the executor sees it: description, inputs, schema, criterion.

        Guarantees the rendering is a pure function of the request's fields and
        contains no handle, parent, or channel information the executor does
        not need to do the work.
        """
        return "\n\n".join(
            [
                f"REQUEST\n{self.description}",
                f"INPUTS\n{json.dumps(self.inputs, sort_keys=True, indent=2)}",
                f"OUTCOME SCHEMA\n{json.dumps(self.outcome_schema, sort_keys=True, indent=2)}",
                f"COMPLETION CRITERION\n{self.completion_criterion}",
            ]
        )


@dataclass(frozen=True)
class Return:
    """Spec 4.10 plus ``children``: requests the assembly wants routed next."""

    handle: str
    outputs: dict[str, Any]
    cost: Money
    status: str  # "ok" | "malformed" | "refused" | "failed"
    children: tuple[Request, ...] = field(default_factory=tuple)
    served_by: str | None = None
