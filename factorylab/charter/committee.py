"""Stratified sortition gives each round fresh pseudonyms and reports its coverage."""

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
    """A frozen round has distinct assemblies and distinct aliases."""

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
    """Fresh identities cannot affect the draw; only completed decisions qualify a seat.

    ``settled`` counts decisions whose outcome was observed; the caller never
    counts a censored (unknown) outcome as a completed decision.
    """
    if type(min_settled) is not int or min_settled < 1:
        raise ValueError("committee.min_settled must be a positive integer")
    return {assembly: role for assembly, role in roles.items()
            if settled.get(assembly, 0) >= min_settled}


def role_strata(eligible: dict[str, str]) -> list[str]:
    """Every role present: the seed roles in ``ROLES`` order, then declared roles sorted."""
    from factorylab.cortex.registration import ROLES

    present = set(eligible.values())
    return [role for role in ROLES if role in present] + sorted(present - set(ROLES))


def learner_strata(learners: dict[str, frozenset[str]] | None, candidates) -> list[str]:
    """The learner types present among the candidates, sorted."""
    if not learners:
        return []
    return sorted({kind for assembly_id in candidates for kind in learners.get(assembly_id, ())})


def draw(eligible: dict[str, str], rng: random.Random, size: int = 5, *,
         learners: dict[str, frozenset[str]] | None = None) -> tuple[Seat, ...]:
    """Sample without replacement, stratified over roles and then over learner types.

    Essay II.IV.a: "a sample of the factory's population is seated (no-regret
    learners, no-swap-regret learners, productive agents, evaluators,
    antagonists)". Every role present among the candidates, a role the
    population declared included, gets a seat before any seat is filled
    uniformly; while a role is covered, a candidate whose learner type is not
    yet seated is preferred, and every learner type present is covered next.
    When the seats cannot cover every role, the covered roles are a uniform
    sample of those present. Coverage reaches as far as the population and the
    seat count allow; ``coverage`` reports how far that was.

    Sorted candidate ids make seeded draws independent of mapping insertion
    order. Independently shuffled aliases hide selection order; seats are
    returned in alias order.
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
    count = min(size, len(candidates))
    if count == len(candidates):
        selected = candidates
    else:
        roles = role_strata(eligible)
        if len(roles) > count:
            chosen = set(rng.sample(roles, count))
            roles = [role for role in roles if role in chosen]
        types = learners or {}
        covered: set[str] = set()
        selected = []
        for role in roles:
            group = [a for a in candidates if eligible[a] == role]
            fresh = [a for a in group if set(types.get(a, ())) - covered]
            choice = rng.choice(fresh or group)
            selected.append(choice)
            covered.update(types.get(choice, ()))
        for kind in learner_strata(learners, candidates):
            group = [a for a in candidates if a not in selected and kind in types.get(a, ())]
            if kind in covered or len(selected) >= count or not group:
                continue
            choice = rng.choice(group)
            selected.append(choice)
            covered.update(types.get(choice, ()))
        remaining = [assembly_id for assembly_id in candidates if assembly_id not in selected]
        selected += rng.sample(remaining, count - len(selected))
    aliases = list(range(1, count + 1))
    rng.shuffle(aliases)
    return tuple(
        Seat(f"seat-{alias}", assembly_id, eligible[assembly_id])
        for alias, assembly_id in sorted(zip(aliases, selected, strict=True))
    )


def coverage(eligible: dict[str, str], seats, learners=None) -> dict:
    """The strata the eligible population offered and the strata the seats cover."""
    seated = [seat.assembly_id for seat in seats]
    types = learners or {}
    return {
        "roles": {"present": role_strata(eligible),
                  "covered": role_strata({a: eligible[a] for a in seated if a in eligible})},
        "learners": {"present": learner_strata(learners, eligible),
                     "covered": sorted({kind for a in seated for kind in types.get(a, ())})},
        "eligible": len(eligible),
        "seats": len(seated),
    }


@dataclass(frozen=True)
class StandingCommittee:
    """One committee per governance boundary; its seats vote on every motion on its agenda.

    ``agenda`` names the motions this committee votes on. ``deferred`` names the
    pending motions too few seats could vote on once each motion's proposer is
    excluded; they wait for the next boundary's committee.
    """

    boundary: int
    round: int
    seats: tuple[Seat, ...]
    agenda: tuple[str, ...] = ()
    deferred: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if type(self.boundary) is not int or self.boundary < 0:
            raise ValueError("boundary must be a nonnegative integer")
        if type(self.round) is not int or self.round < 1:
            raise ValueError("round must be a positive integer")
        for name in ("seats", "agenda", "deferred"):
            if not isinstance(getattr(self, name), (tuple, list)):
                raise ValueError(f"{name} must be a sequence")
            object.__setattr__(self, name, tuple(getattr(self, name)))
        for seat in self.seats:
            if not isinstance(seat, Seat) or any(
                not isinstance(value, str) or not value.strip() for value in seat
            ):
                raise ValueError("each seat needs an alias, assembly id and role")
        if len({seat.alias for seat in self.seats}) != len(self.seats):
            raise ValueError("seat aliases must be unique")
        if len({seat.assembly_id for seat in self.seats}) != len(self.seats):
            raise ValueError("seat assemblies must be unique")
