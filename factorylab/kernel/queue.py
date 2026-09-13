"""Addressable sampling evidence and lossless delayed settlement routes."""

import math
import random
from collections.abc import Callable
from dataclasses import dataclass, replace
from enum import StrEnum
from time import time_ns

from factorylab.kernel.ledger import Ledger
from factorylab.kernel.money import Money, require_money


class SettleStatus(StrEnum):
    PENDING = "pending"
    SETTLED = "settled"
    CENSORED = "censored"
    TIMED_OUT = "timed_out"
    INAPPLICABLE = "inapplicable"
    HISTORICAL = "historical"


@dataclass(frozen=True)
class PropensityRecord:
    """A validated record identifies the exact ordered distribution and reproducible sample.

    ``source`` is ``sampled`` for a distribution the kernel drew from itself, which
    must replay exactly from its seed. It is ``declared`` for the deciding agent's
    own accounting of the field it drew from: nothing in the kernel
    sampled it, so the seed cannot reproduce it; the chosen action must simply
    carry positive mass in the distribution the agent disclosed.
    """

    action_ids: tuple[str, ...]
    probs: tuple[float, ...]
    chosen: str
    rng_seed: int
    learner_id: str
    learner_state_hash: str
    source: str = "sampled"

    def __post_init__(self) -> None:
        object.__setattr__(self, "action_ids", tuple(self.action_ids))
        object.__setattr__(self, "probs", tuple(self.probs))
        self.validate()

    def validate(self) -> None:
        """Reject malformed support, impossible choices and samples inconsistent with the seed."""
        if self.source not in ("sampled", "declared"):
            raise ValueError("propensity source is sampled or declared")
        if not self.action_ids or len(self.action_ids) != len(self.probs):
            raise ValueError("action support and probabilities must have matching nonzero lengths")
        if any(not isinstance(action, str) or not action for action in self.action_ids):
            raise ValueError("actions must have persistent nonempty ids")
        if len(set(self.action_ids)) != len(self.action_ids):
            raise ValueError("action ids must be unique")
        if any(
            type(prob) not in (float, int) or not math.isfinite(prob) or prob < 0 or prob > 1
            for prob in self.probs
        ):
            raise ValueError("probabilities must be finite and in [0, 1]")
        if not math.isclose(math.fsum(self.probs), 1.0, rel_tol=0, abs_tol=1e-12):
            raise ValueError("probabilities must sum to one")
        if type(self.rng_seed) is not int:
            raise TypeError("sampling seed must be an integer")
        if (
            not isinstance(self.learner_id, str)
            or not self.learner_id
            or not isinstance(self.learner_state_hash, str)
            or not self.learner_state_hash
        ):
            raise ValueError("learner identity and state hash are required")
        if self.source == "declared":
            if self.chosen not in self.action_ids:
                raise ValueError("chosen action is outside the declared action set")
            if self.probs[self.action_ids.index(self.chosen)] <= 0:
                raise ValueError("a declared propensity gives the chosen action positive mass")
            return
        executed = random.Random(self.rng_seed).choices(self.action_ids, weights=self.probs, k=1)[0]
        if self.chosen != executed:
            raise ValueError("chosen action does not match the logged seeded distribution")


@dataclass(frozen=True)
class LearningReturn:
    """Feedback contains only the six allowed address, score and definition fields."""

    handle: str
    channel: str
    score: float
    definition_version: str
    status: SettleStatus
    sampling_ref: str | None

    def __post_init__(self) -> None:
        if any(
            not isinstance(value, str) or not value
            for value in (self.handle, self.channel, self.definition_version)
        ):
            raise ValueError("return handle, channel and definition version are required")
        if type(self.score) not in (int, float) or not math.isfinite(self.score):
            raise ValueError("score must be finite")
        if self.sampling_ref is not None and not isinstance(self.sampling_ref, str):
            raise TypeError("sampling_ref must be a string or None")
        object.__setattr__(self, "status", SettleStatus(self.status))


@dataclass(frozen=True)
class Decision:
    """Original actor, sampling evidence and channel survive retirement and timeout."""

    handle: str
    actor: str
    event_id: str
    propensity: PropensityRecord
    channel: str
    deadline_ns: int
    parent_handle: str | None
    cost_ceiling: Money
    opened_ns: int
    status: SettleStatus = SettleStatus.PENDING


class DecisionQueue:
    """Every accepted decision remains addressable and every final return remains retained."""

    def __init__(self, ledger: Ledger, *, clock_ns: Callable[[], int] = time_ns) -> None:
        self.__ledger = ledger
        self.__clock = clock_ns
        self.__decisions: dict[str, Decision] = {}
        self.__retired: set[str] = set()
        self.__successors: dict[str, tuple[str, dict[str, str]]] = {}
        self.__deliveries: dict[str, list[LearningReturn]] = {}
        self.__returns: dict[str, list[LearningReturn]] = {}
        self.__settled_contracts: set[str] = set()
        # The deciding agent's own distribution over its own actions, recorded
        # as a second propensity on the handle the router already opened.
        self.__declared: dict[str, list[PropensityRecord]] = {}

    def open(
        self,
        *,
        actor: str,
        event_id: str,
        propensity: PropensityRecord,
        channel: str,
        deadline_ns: int,
        parent_handle: str | None,
        cost_ceiling: Money,
    ) -> str:
        """Persist sampling evidence before issuing a unique handle for a declared score channel."""
        if not isinstance(propensity, PropensityRecord):
            raise TypeError("a logged propensity record is required")
        propensity.validate()
        if actor != propensity.learner_id or actor in self.__retired:
            raise ValueError("actor must match an active sampling learner")
        if any(not isinstance(value, str) or not value for value in (actor, event_id, channel)):
            raise ValueError("actor, event_id and channel are required")
        if channel == "timeout":
            raise ValueError("timeout is reserved for operational penalties")
        require_money(cost_ceiling, nonnegative=True)
        now = self.__clock()
        if type(deadline_ns) is not int or deadline_ns < now:
            raise ValueError("deadline must be integer nanoseconds at or after opening")
        if parent_handle is not None and parent_handle not in self.__decisions:
            raise ValueError("parent handle must already exist")
        # The ledger owns the ordinal across queues; resume annotations cannot change it.
        seq = self.__ledger.append({"kind": "decision.handle", "ts": now})
        handle = self.__ledger.decision_id(seq)
        decision = Decision(
            handle,
            actor,
            event_id,
            propensity,
            channel,
            deadline_ns,
            parent_handle,
            cost_ceiling,
            now,
        )
        self.__ledger.append({"kind": "decision.open", "ts": now, **vars(decision)})
        self.__decisions[handle] = decision
        self.__returns[handle] = []
        return handle

    def get(self, handle: str) -> Decision:
        """Return immutable original addressing and current completion status."""
        return self.__decisions[handle]

    def record_propensity(self, handle: str, propensity: PropensityRecord) -> None:
        """Log a deciding agent's own propensity as a second record on an open handle.

        The router's record stays exactly as it was: this one is about
        the action the woken assembly took, over the action set it declared. It is
        evidence, not addressing, so it never changes the decision's channel,
        actor or status, and it can only be added while the decision is open.
        """
        if not isinstance(propensity, PropensityRecord):
            raise TypeError("a logged propensity record is required")
        propensity.validate()
        decision = self.__decisions[handle]
        if decision.status not in (SettleStatus.PENDING, SettleStatus.TIMED_OUT):
            raise ValueError("decision already has a final outcome")
        self.__ledger.append({
            "kind": "decision.propensity", "ts": self.__clock(), "handle": handle,
            "propensity": propensity, "index": len(self.__declared.get(handle, ())) + 1,
        })
        self.__declared.setdefault(handle, []).append(propensity)

    def propensities(self, handle: str) -> tuple[PropensityRecord, ...]:
        """Return every propensity on a handle: the router's first, then the agents'."""
        return (self.__decisions[handle].propensity, *self.__declared.get(handle, ()))

    def declared_propensity(self, handle: str) -> PropensityRecord | None:
        """Return the deciding agent's own latest propensity, or None if it declared none."""
        records = self.__declared.get(handle)
        return records[-1] if records else None

    def outstanding(self, actor: str | None = None) -> list[Decision]:
        """Return pending decisions in opening order, optionally filtered by original actor."""
        return [
            decision
            for decision in self.__decisions.values()
            if decision.status == SettleStatus.PENDING
            and (actor is None or decision.actor == actor)
        ]

    def retire_actor(self, actor: str) -> None:
        """Retire future sampling rights without deleting liabilities or historical returns."""
        if not isinstance(actor, str) or not actor:
            raise ValueError("actor id is required")
        if actor in self.__retired:
            return
        self.__ledger.append({"kind": "actor.retire", "actor": actor, "ts": self.__clock()})
        self.__retired.add(actor)

    def register_successor(self, retired_actor: str, successor_actor: str, compat: dict) -> None:
        """Bind one immutable channel map, rejecting self-successions and cycles."""
        if any(
            not isinstance(actor, str) or not actor for actor in (retired_actor, successor_actor)
        ):
            raise ValueError("both actor ids are required")
        if not isinstance(compat, dict) or any(
            not isinstance(old, str) or not old or not isinstance(new, str) or not new
            for old, new in compat.items()
        ):
            raise ValueError("compatibility is a mapping of original channel to successor channel")
        if retired_actor in self.__successors:
            raise ValueError("successor mapping is immutable")
        cursor = successor_actor
        while True:
            if cursor == retired_actor:
                raise ValueError("successor cycle")
            if cursor not in self.__successors:
                break
            cursor = self.__successors[cursor][0]
        self.__ledger.append(
            {
                "kind": "actor.successor",
                "actor": retired_actor,
                "successor": successor_actor,
                "compat": compat,
                "ts": self.__clock(),
            }
        )
        self.__successors[retired_actor] = (successor_actor, dict(compat))
        self.__retired.add(retired_actor)

    def _route(self, actor: str, channel: str) -> tuple[str | None, str]:
        while actor in self.__retired:
            if actor not in self.__successors:
                return None, channel
            successor, compat = self.__successors[actor]
            if channel not in compat:
                return None, channel
            actor, channel = successor, compat[channel]
        return actor, channel

    def settle(
        self,
        handle: str,
        *,
        channel: str,
        score: float,
        status: SettleStatus,
        definition_version: str,
        sampling_ref: str | None,
    ) -> None:
        """Retain each outcome on its original handle and route only compatible feedback."""
        decision = self.__decisions[handle]
        status = SettleStatus(status)
        if status not in (SettleStatus.SETTLED, SettleStatus.CENSORED, SettleStatus.INAPPLICABLE):
            raise ValueError("only outcome settlement statuses may be submitted")
        if channel != decision.channel:
            raise ValueError("settlement must address the original declared channel")
        if decision.status not in (SettleStatus.PENDING, SettleStatus.TIMED_OUT):
            raise ValueError("decision already has a final outcome")
        original = LearningReturn(handle, channel, score, definition_version, status, sampling_ref)
        actor, mapped_channel = self._route(decision.actor, channel)
        retained = (
            original if actor is not None else replace(original, status=SettleStatus.HISTORICAL)
        )
        now = self.__clock()
        if now < decision.opened_ns:
            raise ValueError("settlement precedes opening")
        self.__ledger.append(
            {
                "kind": "decision.settle",
                "ts": now,
                "return": retained,
                "original_status": status,
                "recipient": actor,
                "mapped_channel": mapped_channel,
                "latency_ns": now - decision.opened_ns,
            }
        )
        self.__decisions[handle] = replace(decision, status=retained.status)
        self.__returns[handle].append(retained)
        if status == SettleStatus.SETTLED:
            self.__settled_contracts.add(decision.propensity.chosen)
        if actor is not None:
            delivered = replace(original, channel=mapped_channel)
            self.__deliveries.setdefault(actor, []).append(delivered)

    def expire(self, now_ns: int) -> list[str]:
        """Emit score-zero timeout penalties once, preserving all late settlement rights."""
        if type(now_ns) is not int or now_ns < 0:
            raise ValueError("now_ns must be nonnegative integer nanoseconds")
        expired = []
        for decision in self.outstanding():
            if decision.deadline_ns > now_ns:
                continue
            penalty = LearningReturn(
                decision.handle, "timeout", 0.0, "timeout-v1", SettleStatus.TIMED_OUT, None
            )
            actor, _ = self._route(decision.actor, decision.channel)
            self.__ledger.append(
                {"kind": "decision.timeout", "ts": now_ns, "return": penalty, "recipient": actor}
            )
            self.__decisions[decision.handle] = replace(decision, status=SettleStatus.TIMED_OUT)
            self.__returns[decision.handle].append(penalty)
            if actor is not None:
                self.__deliveries.setdefault(actor, []).append(penalty)
            expired.append(decision.handle)
        return expired

    def returns_for(self, actor: str) -> tuple[LearningReturn, ...]:
        """Return only thin feedback addressed to this actor, in delivery order."""
        return tuple(self.__deliveries.get(actor, ()))

    def history(self, handle: str) -> tuple[LearningReturn, ...]:
        """Retain thin original-channel returns, including timeouts and undeliverable history."""
        return tuple(self.__returns[handle])

    def has_history(self, contract_id: str) -> bool:
        """Count settled performance even after retirement; exclude missingness and timeouts."""
        return contract_id in self.__settled_contracts

    def state(self) -> dict:
        """Retain all decisions, outcomes, retirement routes and delivered feedback in order."""
        return {
            "decisions": dict(self.__decisions), "retired": set(self.__retired),
            "successors": {k: (v[0], dict(v[1])) for k, v in self.__successors.items()},
            "deliveries": {k: list(v) for k, v in self.__deliveries.items()},
            "returns": {k: list(v) for k, v in self.__returns.items()},
            "settled_contracts": set(self.__settled_contracts),
            "declared": {k: list(v) for k, v in self.__declared.items()},
        }

    def _restore_state(self, state: dict) -> None:
        """Authenticated checkpoint feedback retains its original handles and delivery order."""
        if self.__ledger.final:
            raise RuntimeError("world is final")
        for name in ("decisions", "retired", "successors", "deliveries", "returns",
                     "settled_contracts"):
            setattr(self, f"_DecisionQueue__{name}", state[name])
        self.__declared = state.get("declared", {})
