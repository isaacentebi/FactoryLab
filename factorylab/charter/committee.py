"""Sortition preserves role coverage and gives each round fresh pseudonyms."""

import random
from dataclasses import dataclass
from typing import NamedTuple


class Seat(NamedTuple):
    """An immutable seat binds one round's alias to one assembly and role."""

    alias: str
    assembly_id: str
    role: str


@dataclass(frozen=True)
class Committee:
    """A frozen round has at most five distinct assemblies and distinct aliases."""

    amendment_id: str
    round: int
    seats: tuple[Seat, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.amendment_id, str) or not self.amendment_id.strip():
            raise ValueError("amendment_id is required")
        if type(self.round) is not int or self.round < 1:
            raise ValueError("round must be a positive integer")
        if not isinstance(self.seats, (tuple, list)):
            raise ValueError("seats must be a sequence of Seat records")
        object.__setattr__(self, "seats", tuple(self.seats))
        if len(self.seats) > 5:
            raise ValueError("a committee has at most five seats")
        for seat in self.seats:
            if not isinstance(seat, Seat) or any(
                not isinstance(value, str) or not value.strip() for value in seat
            ):
                raise ValueError("each seat needs an alias, assembly id and role")
        if len({seat.alias for seat in self.seats}) != len(self.seats):
            raise ValueError("seat aliases must be unique")
        if len({seat.assembly_id for seat in self.seats}) != len(self.seats):
            raise ValueError("seat assemblies must be unique")


@dataclass(frozen=True)
class Ballot:
    """A frozen ballot carries only an alias; None records an abstention."""

    alias: str
    vote: bool | None
    reason: str

    def __post_init__(self) -> None:
        if not isinstance(self.alias, str) or not self.alias.strip():
            raise ValueError("ballot alias is required")
        if self.vote is not None and type(self.vote) is not bool:
            raise ValueError("a ballot must be a boolean vote or an abstention")
        if not isinstance(self.reason, str):
            raise ValueError("ballot reason must be a string")


def experienced(roles: dict[str, str], settled: dict[str, int], min_settled: int) -> dict[str, str]:
    """Fresh identities cannot affect the draw; only completed decisions qualify a seat."""
    if type(min_settled) is not int or min_settled < 1:
        raise ValueError("committee.min_settled must be a positive integer")
    return {assembly: role for assembly, role in roles.items()
            if settled.get(assembly, 0) >= min_settled}


def draw(eligible: dict[str, str], rng: random.Random, size: int = 5) -> tuple[Seat, ...]:
    """Sample without replacement, covering each available core role before filling uniformly.

    Sorted candidate ids make seeded draws independent of mapping insertion
    order. Independently shuffled aliases hide selection order; seats are
    returned in alias order. A requested size too small for coverage is invalid.
    """
    if type(size) is not int or size < 1:
        raise ValueError("size must be a positive integer")
    if not isinstance(eligible, dict) or any(
        not isinstance(value, str) or not value.strip()
        for pair in eligible.items()
        for value in pair
    ):
        raise ValueError("eligible must map assembly ids to non-empty roles")
    candidates = sorted(eligible)
    groups = [
        [assembly_id for assembly_id in candidates if eligible[assembly_id] == role]
        for role in ("producer", "evaluator", "meta")
    ]
    count = min(size, len(candidates))
    if count < sum(bool(group) for group in groups):
        raise ValueError("size cannot cover the available roles")
    if count == len(candidates):
        selected = candidates
    else:
        selected = [rng.choice(group) for group in groups if group]
        remaining = [assembly_id for assembly_id in candidates if assembly_id not in selected]
        selected += rng.sample(remaining, count - len(selected))
    aliases = list(range(1, count + 1))
    rng.shuffle(aliases)
    return tuple(
        Seat(f"seat-{alias}", assembly_id, eligible[assembly_id])
        for alias, assembly_id in sorted(zip(aliases, selected, strict=True))
    )
