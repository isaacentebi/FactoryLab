"""Availability filtering preserves input order and caller-supplied reasons."""

from collections.abc import Callable, Sequence
from dataclasses import dataclass


@dataclass(frozen=True)
class FeasibilityResult:
    """Feasible IDs and excluded (ID, reason) pairs retain their relative order."""

    feasible: list[str]
    excluded: list[tuple[str, str]]


def filter(
    actions: Sequence[str], is_feasible: Callable[[str], tuple[bool, str]]
) -> FeasibilityResult:
    """Partition actions in input order, retaining each exclusion's exact string reason."""
    feasible = []
    excluded = []
    for action in actions:
        allowed, reason = is_feasible(action)
        if not isinstance(reason, str):
            raise TypeError("feasibility reasons must be strings")
        if allowed:
            feasible.append(action)
        else:
            excluded.append((action, reason))
    return FeasibilityResult(feasible, excluded)
