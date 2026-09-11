"""Every sample carries the exact feasible distribution and a replayable draw seed.

The supplied random.Random generates one 128-bit seed per route. A fresh local
Random(seed) draws from the logged probabilities, so replay needs no hidden RNG
state. Learners must include NOOP in their fixed universe. Duplicate event IDs
are collapsed at their first occurrence, with exactly one NOOP placed last.
"""

import hashlib
from collections.abc import Callable
from dataclasses import dataclass
from random import Random

from .base import Learner, _probabilities
from .feasibility import filter


@dataclass(frozen=True)
class Sample:
    """The chosen action is sampled from probs aligned with feasible action_ids."""

    action_ids: tuple[str, ...]
    probs: tuple[float, ...]
    chosen: str
    rng_seed: int
    learner_id: str
    learner_state_hash: str
    excluded: tuple[tuple[str, str], ...]


class Router:
    """Event actions are filtered before querying and sampling the learner."""

    def __init__(
        self, learner: Learner, action_ids_for_event: Callable[[str], list[str]]
    ) -> None:
        """Retain the learner and event lookup without introducing kernel dependencies."""
        self.learner = learner
        self.action_ids_for_event = action_ids_for_event

    def route(
        self, event_kind: str, is_feasible: Callable[[str], tuple[bool, str]], rng: Random
    ) -> Sample:
        """Return a replayable draw and exact exclusions, with NOOP unconditionally feasible."""
        actions = list(dict.fromkeys(
            a for a in self.action_ids_for_event(event_kind) if a != "NOOP"
        ))
        actions.append("NOOP")
        result = filter(actions, lambda a: (True, "") if a == "NOOP" else is_feasible(a))
        distribution = self.learner.distribution(result.feasible)
        _probabilities(distribution, result.feasible)
        action_ids = tuple(result.feasible)
        probs = tuple(distribution[a] for a in action_ids)
        state_hash = hashlib.sha256(self.learner.state()).hexdigest()
        seed = rng.getrandbits(128)
        chosen = Random(seed).choices(action_ids, weights=probs, k=1)[0]
        return Sample(
            action_ids, probs, chosen, seed, self.learner.id, state_hash, tuple(result.excluded)
        )
