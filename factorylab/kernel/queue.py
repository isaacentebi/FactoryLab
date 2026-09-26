"""Addressable sampling evidence and lossless delayed settlement routes."""

import math
import random
from collections.abc import Callable
from dataclasses import dataclass, replace
from enum import StrEnum
from time import time_ns

from factorylab.kernel.ledger import Ledger
from factorylab.kernel.money import Money, require_money

#: An action a seat can take is an effect in the world (essay II.II.b; ruling R5): a
#: tool of a kind it called. A free-text label a return declares is not one: a fresh
#: string would make any action look new, so labels never name an action here.
ACTION_TOOL = "tool"


def action_key(*, tool: str, kind: str) -> str:
    """The name of one action, ``tool:<kind>:<tool>``: a tool of a published kind.

    Essay II.II.b: learning death is prevented by a niche "usable only in the
    context of unhistoried actions (decisions that arrive carrying no propensity
    record and no reward trail)". Guarantees one key per (tool, kind), whoever
    calls it.
    """
    if (not isinstance(tool, str) or not tool.strip()
            or not isinstance(kind, str) or not kind.strip()):
        raise ValueError("a tool action names a nonempty tool and kind")
    return f"{ACTION_TOOL}:{kind}:{tool}"


def _is_action_key(key: object) -> bool:
    return (isinstance(key, str) and key.startswith(f"{ACTION_TOOL}:")
            and key.count(":") >= 2 and all(key.split(":", 2)))


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
class Tombstone:
    """What a released decision's handle still answers: it settled and was released.

    Essay II.I.b: the return channel keeps "a managed queue of outstanding
    decisions awaiting their reward"; a decision no score is owed to any more is
    not outstanding, and "a verdict is consumed ... and then discarded" (II.IV.c).
    What stays is the handle, the lineage key of the seat that authored it, its
    final state and when that state was retained: never its propensity, its
    returns or anything a learner could read.
    """

    handle: str
    author: str | None
    status: SettleStatus
    settled_ns: int | None


class Released(KeyError):
    """A handle that named a decision which settled and was released.

    A ``KeyError``, because the decision's bulk is gone and every reader that
    tolerates an unknown handle tolerates this one; ``tombstone`` says what the
    handle was (None once the tombstone itself was compacted into counts).
    """

    def __init__(self, handle: str, tombstone: Tombstone | None) -> None:
        super().__init__(handle)
        self.handle = handle
        self.tombstone = tombstone


def _ordinal(handle: str) -> int | None:
    """The ledger ordinal a ``decision-<n>`` handle carries, or None for any other string."""
    prefix, _, digits = handle.rpartition("-")
    if prefix != "decision" or not digits.isdigit():
        return None
    return int(digits)


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
    """A decision stays addressable, with every return it received, while a score is owed.

    Essay II.I.b: the return channel "must assign each sampled action a persistent
    handle that a later score can be attached to ... a managed queue of outstanding
    decisions awaiting their reward". A decision whose every score has arrived and
    been read is released (``release``): its handle keeps answering, as a
    tombstone and then as a compacted range, and its bulk is dropped.
    """

    def __init__(self, ledger: Ledger, *, clock_ns: Callable[[], int] = time_ns) -> None:
        self.__ledger = ledger
        self.__clock = clock_ns
        self.__decisions: dict[str, Decision] = {}
        # The handles whose decision is PENDING, in opening order: ``outstanding``
        # reads these instead of walking every decision the world ever opened.
        self.__pending: dict[str, None] = {}
        self.__retired: set[str] = set()
        self.__successors: dict[str, tuple[str, dict[str, str]]] = {}
        self.__deliveries: dict[str, list[LearningReturn]] = {}
        # actor -> how many of its earliest deliveries were released (wave 17): its
        # consumer read them, and only the count survives (``release_delivered``).
        self.__released: dict[str, int] = {}
        self.__returns: dict[str, list[LearningReturn]] = {}
        self.__settled_contracts: set[str] = set()
        # The deciding agent's own distribution over its own actions, recorded
        # as a second propensity on the handle the router already opened.
        self.__declared: dict[str, list[PropensityRecord]] = {}
        # The actions each decision took (``action_key``), and per contract the
        # actions that are historied: taken by a decision that carries a propensity
        # record or a delivered return.
        self.__actions: dict[str, frozenset[str]] = {}
        self.__settled_actions: dict[str, set[str]] = {}
        # When each retained decision's final outcome was retained (the tombstone's
        # settle time); a decision still owed a score has none.
        self.__closed_ns: dict[str, int] = {}
        # Released decisions (wave 17b): handle -> (author, final status, settle ns),
        # then, past the caller's horizon, only counts per final status and the ranges
        # of ledger ordinals they covered (``compact_released``).
        self.__tombstones: dict[str, tuple[str | None, str, int | None]] = {}
        self.__compacted: dict[str, int] = {}
        self.__compacted_ranges: list[list[int]] = []
        # Handles whose outcome became final since the caller last drained them
        # (``drain_finalized``): what a running tally over final outcomes reads.
        self.__finalized: list[str] = []
        # Derived, never checkpointed: each retained decision's retained children, and
        # how many of its deliveries an actor's consumer has not released yet.
        self.__children: dict[str, int] = {}
        self.__held: dict[str, int] = {}

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
        self.__pending[handle] = None
        self.__returns[handle] = []
        if parent_handle is not None:
            self.__children[parent_handle] = self.__children.get(parent_handle, 0) + 1
        return handle

    def _decision(self, handle: str) -> Decision:
        """The retained decision, or ``Released`` for one released, or ``KeyError``."""
        try:
            return self.__decisions[handle]
        except KeyError:
            if isinstance(handle, str) and self.is_released(handle):
                raise Released(handle, self.tombstone(handle)) from None
            raise

    def get(self, handle: str) -> Decision:
        """Return immutable original addressing and current completion status.

        A released handle raises ``Released`` (a ``KeyError``) naming its tombstone.
        """
        return self._decision(handle)

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
        decision = self._decision(handle)
        if decision.status not in (SettleStatus.PENDING, SettleStatus.TIMED_OUT):
            raise ValueError("decision already has a final outcome")
        self.__ledger.append({
            "kind": "decision.propensity", "ts": self.__clock(), "handle": handle,
            "propensity": propensity, "index": len(self.__declared.get(handle, ())) + 1,
        })
        self.__declared.setdefault(handle, []).append(propensity)
        self._history(handle)

    def record_actions(self, handle: str, keys) -> None:
        """Log the actions an open decision took; they are historied once it has a trail.

        Essay II.II.b: an unhistoried action is one "carrying no propensity record and
        no reward trail". Guarantees that an action key becomes historied for the
        decision's contract (``has_action_history``) as soon as that decision carries
        a declared propensity record or any delivered return (settled, censored,
        inapplicable or a timeout), in whichever order they arrive. Actions can only
        be added while the decision is open, and only as ``action_key`` names; the
        record is evidence and never changes addressing.
        """
        decision = self._decision(handle)
        keys = frozenset(keys)
        if not keys or not all(_is_action_key(key) for key in keys):
            raise ValueError("actions are nonempty action_key names")
        if decision.status not in (SettleStatus.PENDING, SettleStatus.TIMED_OUT):
            raise ValueError("decision already has a final outcome")
        added = keys - self.__actions.get(handle, frozenset())
        if not added:
            return
        self.__ledger.append({"kind": "decision.actions", "ts": self.__clock(),
                              "handle": handle, "actions": sorted(added)})
        self.__actions[handle] = self.__actions.get(handle, frozenset()) | added
        self._history(handle)

    def _history(self, handle: str) -> None:
        """A decision with a propensity record or a delivered return historicizes its actions."""
        taken = self.__actions.get(handle)
        if taken and (self.__declared.get(handle) or self.__returns.get(handle)):
            chosen = self.__decisions[handle].propensity.chosen
            self.__settled_actions.setdefault(chosen, set()).update(taken)

    def has_action_history(self, contract_id: str, key: str) -> bool:
        """Whether a decision of ``contract_id`` with a propensity record or a delivered
        return took the action ``key``.

        An action without it is *unhistoried* for that contract (essay II.II.b).
        """
        return key in self.__settled_actions.get(contract_id, ())

    def propensities(self, handle: str) -> tuple[PropensityRecord, ...]:
        """Return every propensity on a handle: the router's first, then the agents'."""
        return (self._decision(handle).propensity, *self.__declared.get(handle, ()))

    def declared_propensity(self, handle: str) -> PropensityRecord | None:
        """Return the deciding agent's own latest propensity, or None if it declared none."""
        records = self.__declared.get(handle)
        if records is None:
            self._decision(handle)  # a released handle is refused, never answered None
        return records[-1] if records else None

    def outstanding(self, actor: str | None = None) -> list[Decision]:
        """Return pending decisions in opening order, optionally filtered by original actor."""
        decisions = self.__decisions
        return [
            decision
            for decision in map(decisions.__getitem__, self.__pending)
            if actor is None or decision.actor == actor
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
        decision = self._decision(handle)
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
        self.__pending.pop(handle, None)  # no outcome status is PENDING
        self.__returns[handle].append(retained)
        self.__closed_ns[handle] = now
        self.__finalized.append(handle)
        # Essay II.II.b defines unhistoried actions as "decisions that arrive carrying
        # no propensity record and no reward trail": a settled delivery of any status
        # (a score, or a decline, abstention or censored decision credited at its
        # published price) is a reward trail (wave 16, ruling R10-b). A timeout is not
        # a settlement and leaves none.
        self.__settled_contracts.add(decision.propensity.chosen)
        self._history(handle)
        # The outcome is final: every later settle, timeout, propensity or action on
        # this handle is refused before it reads the actions, and ``_history`` has just
        # folded them into the contract's settled actions. Nothing reads them again.
        self.__actions.pop(handle, None)
        if actor is not None:
            delivered = replace(original, channel=mapped_channel)
            self.__deliveries.setdefault(actor, []).append(delivered)
            self.__held[handle] = self.__held.get(handle, 0) + 1

    def expire(self, now_ns: int) -> list[str]:
        """Emit score-zero timeout penalties once, preserving all late settlement rights."""
        if type(now_ns) is not int or now_ns < 0:
            raise ValueError("now_ns must be nonnegative integer nanoseconds")
        return self.time_out(
            [d.handle for d in self.outstanding() if d.deadline_ns <= now_ns], now_ns)

    def time_out(self, handles: list[str], now_ns: int) -> list[str]:
        """Time out exactly the named pending decisions, once each, at ``now_ns``.

        The caller owns the clock a cutoff is counted in (the runtime counts world
        ticks, essay II.IV.b-c); the queue owns the penalty. Guarantees the same
        score-zero timeout return ``expire`` emits, at most one per decision, a
        late settlement's right preserved, and a handle that is not pending
        (already timed out or settled) left untouched. An unknown handle raises.
        """
        if type(now_ns) is not int or now_ns < 0:
            raise ValueError("now_ns must be nonnegative integer nanoseconds")
        expired = []
        for handle in handles:
            decision = self._decision(handle)
            if decision.status is not SettleStatus.PENDING:
                continue
            penalty = LearningReturn(
                decision.handle, "timeout", 0.0, "timeout-v1", SettleStatus.TIMED_OUT, None
            )
            actor, _ = self._route(decision.actor, decision.channel)
            self.__ledger.append(
                {"kind": "decision.timeout", "ts": now_ns, "return": penalty, "recipient": actor}
            )
            self.__decisions[decision.handle] = replace(decision, status=SettleStatus.TIMED_OUT)
            self.__pending.pop(decision.handle, None)
            self.__returns[decision.handle].append(penalty)
            self._history(decision.handle)
            if actor is not None:
                self.__deliveries.setdefault(actor, []).append(penalty)
                self.__held[decision.handle] = self.__held.get(decision.handle, 0) + 1
            expired.append(decision.handle)
        return expired

    def returns_for(self, actor: str) -> tuple[LearningReturn, ...]:
        """Return the thin feedback addressed to this actor and not released, in delivery order.

        An actor none of whose deliveries was released gets every one it was sent.
        """
        return tuple(self.__deliveries.get(actor, ()))

    def delivered_count(self, actor: str) -> int:
        """How many returns were ever delivered to ``actor``, released ones included."""
        return self.__released.get(actor, 0) + len(self.__deliveries.get(actor, ()))

    def returns_since(self, actor: str, start: int) -> tuple[tuple[LearningReturn, ...], int]:
        """``(the deliveries to actor from position start on, delivered_count(actor))``.

        Guarantees a reader never silently skips a return: ``start`` before the
        released prefix raises, since those returns are gone.
        """
        released = self.__released.get(actor, 0)
        if type(start) is not int or start < released:
            raise ValueError("returns before the released prefix were released")
        retained = self.__deliveries.get(actor, ())
        return tuple(retained[start - released:]), released + len(retained)

    def release_delivered(self, actor: str, through: int) -> int:
        """Forget the first ``through`` deliveries to ``actor``; return how many are gone.

        The caller is the actor's one consumer and has read them (wave 17: retained
        state no reader can reach is not kept; essay II.II.b, the disk and memory are
        a hard cast). Guarantees: nothing is released past what was delivered (that
        raises), a release never moves back, ``delivered_count`` is unchanged, and
        every decision, its outcomes and its history stay addressable: only the
        actor's copy of the thin feedback it already read is dropped.
        """
        if type(through) is not int or through < 0:
            raise ValueError("a release names a nonnegative delivery count")
        if through > self.delivered_count(actor):
            raise ValueError("cannot release a return that was never delivered")
        released = self.__released.get(actor, 0)
        if through <= released:
            return released
        gone = self.__deliveries[actor][:through - released]
        del self.__deliveries[actor][:through - released]
        for delivery in gone:
            left = self.__held.get(delivery.handle, 0) - 1
            if left > 0:
                self.__held[delivery.handle] = left
            else:
                self.__held.pop(delivery.handle, None)
        self.__released[actor] = through
        return through

    def delivery_actors(self) -> list[str]:
        """Every actor still holding a delivery it has not released, in first-delivery order."""
        return [actor for actor, held in self.__deliveries.items() if held]

    def history(self, handle: str) -> tuple[LearningReturn, ...]:
        """Retain thin original-channel returns, including timeouts and undeliverable history.

        A released handle raises ``Released``: its returns were consumed and discarded.
        """
        returns = self.__returns.get(handle)
        if returns is None:
            self._decision(handle)
            raise KeyError(handle)
        return tuple(returns)

    def has_history(self, contract_id: str) -> bool:
        """Whether any decision of the contract was settled, whatever its status, even
        after retirement: a reward trail (ruling R10-b). A timeout alone leaves none."""
        return contract_id in self.__settled_contracts

    # -- release (wave 17b) --------------------------------------------------------------

    def owed(self, handle: str) -> str | None:
        """Why the kernel still owes ``handle`` something, or None when it owes nothing.

        Essay II.I.b: a decision is "outstanding ... awaiting [its] reward" while a
        score can still be attached to its handle. The kernel's own debts, each
        named: the decision is still pending; it timed out and keeps its late
        settlement's right; a retained decision names it as its parent; or an actor's
        consumer has not yet read a return it was delivered. The caller owns every
        other debt (judges, horizons, lots) and releases only when it owes none either.
        """
        decision = self._decision(handle)
        if decision.status is SettleStatus.PENDING:
            return "pending"
        if decision.status is SettleStatus.TIMED_OUT:
            return "timed out: a late settlement keeps its right"
        if self.__children.get(handle):
            return "a retained child names it as its parent"
        if self.__held.get(handle):
            return "a delivered return was not read by its consumer"
        return None

    def release(self, handle: str, *, author: str | None) -> Tombstone:
        """Drop a fully settled decision's bulk and keep its tombstone.

        Guarantees a decision is released only when the kernel owes it nothing
        (``owed`` is None; otherwise ``ValueError`` names the debt and nothing
        changes), at most once (a second release, like an unknown handle, raises
        ``KeyError``), and that afterwards its handle answers ``Released`` with the
        tombstone (``author``, the caller's lineage key of the seat that authored
        it; its final status; when that status was retained). Its propensity, its
        declared propensities and its returns are gone; the per-contract history
        (``has_history``, ``has_action_history``) and every delivery count are
        unchanged. Nothing is appended to the diary: every fact the decision carried
        is already there (its opening, its propensities, each settlement), and the
        release is a deterministic function of the state a replay rebuilds, as
        ``release_delivered`` is; the checkpoint holds the tombstone.
        """
        if author is not None and (not isinstance(author, str) or not author):
            raise ValueError("an author is a nonempty lineage key or None")
        decision = self.__decisions[handle]
        debt = self.owed(handle)
        if debt is not None:
            raise ValueError(f"a score is still owed to {handle}: {debt}")
        settled_ns = self.__closed_ns.get(handle)
        del self.__decisions[handle]
        self.__returns.pop(handle, None)
        self.__declared.pop(handle, None)
        self.__actions.pop(handle, None)
        self.__closed_ns.pop(handle, None)
        if decision.parent_handle is not None:
            left = self.__children.get(decision.parent_handle, 0) - 1
            if left > 0:
                self.__children[decision.parent_handle] = left
            else:
                self.__children.pop(decision.parent_handle, None)
        self.__tombstones[handle] = (author, str(decision.status), settled_ns)
        return Tombstone(handle, author, decision.status, settled_ns)

    def is_released(self, handle: str) -> bool:
        """Whether ``handle`` named a decision this queue released, tombstoned or compacted."""
        if handle in self.__tombstones:
            return True
        ordinal = _ordinal(handle) if isinstance(handle, str) else None
        if ordinal is None or handle in self.__decisions:
            return False
        from bisect import bisect_right

        ranges = self.__compacted_ranges
        at = bisect_right(ranges, [ordinal, float("inf")]) - 1
        return at >= 0 and ranges[at][0] <= ordinal <= ranges[at][1]

    def tombstone(self, handle: str) -> Tombstone | None:
        """The tombstone a released handle still carries, or None (never released, or
        compacted into counts)."""
        kept = self.__tombstones.get(handle)
        if kept is None:
            return None
        author, status, settled_ns = kept
        return Tombstone(handle, author, SettleStatus(status), settled_ns)

    def tombstones(self) -> list[Tombstone]:
        """Every tombstone still held, in release order."""
        return [self.tombstone(handle) for handle in self.__tombstones]

    def compact_released(self, before_ns: int) -> int:
        """Fold every tombstone that settled before ``before_ns`` into counts; return how many.

        Guarantees the tombstones stay bounded by the caller's horizon: past it a
        released decision is one count under its final status and one ledger
        ordinal in a range, so ``is_released`` still answers True for it while
        ``tombstone`` answers None. A tombstone with no settle time (released from
        a checkpoint older than settle times) is folded at the first compaction.
        Nothing retained is touched.
        """
        if type(before_ns) is not int:
            raise ValueError("before_ns must be integer nanoseconds")
        due = [handle for handle, (_author, _status, settled) in self.__tombstones.items()
               if settled is None or settled < before_ns]
        ordinals = []
        for handle in due:
            _author, status, _settled = self.__tombstones.pop(handle)
            self.__compacted[status] = self.__compacted.get(status, 0) + 1
            ordinal = _ordinal(handle)
            if ordinal is not None:
                ordinals.append(ordinal)
        if ordinals:
            merged: list[list[int]] = []
            for lo, hi in sorted([*self.__compacted_ranges, *([o, o] for o in ordinals)]):
                if merged and lo <= merged[-1][1] + 1:
                    merged[-1][1] = max(merged[-1][1], hi)
                else:
                    merged.append([lo, hi])
            self.__compacted_ranges = merged
        return len(due)

    def released_counts(self) -> dict[str, int]:
        """How many released decisions ended in each final status, compacted or not."""
        counts = dict(self.__compacted)
        for _author, status, _settled in self.__tombstones.values():
            counts[status] = counts.get(status, 0) + 1
        return counts

    def drain_finalized(self) -> list[str]:
        """Every handle whose outcome became final since the last drain, in order, once.

        Guarantees each settlement is handed out exactly once, whichever path made
        it, so a tally kept over final outcomes is updated at settlement rather than
        by scanning every decision the world ever opened.
        """
        drained, self.__finalized = self.__finalized, []
        return drained

    def retained(self) -> list[str]:
        """Every retained handle, in opening order."""
        return list(self.__decisions)

    def state(self) -> dict:
        """Retain all decisions, outcomes, retirement routes and unreleased feedback in order."""
        return {
            "decisions": dict(self.__decisions), "retired": set(self.__retired),
            "successors": {k: (v[0], dict(v[1])) for k, v in self.__successors.items()},
            "deliveries": {k: list(v) for k, v in self.__deliveries.items()},
            "released": dict(self.__released),
            "returns": {k: list(v) for k, v in self.__returns.items()},
            "settled_contracts": set(self.__settled_contracts),
            "declared": {k: list(v) for k, v in self.__declared.items()},
            "actions": dict(self.__actions),
            "settled_actions": {k: set(v) for k, v in self.__settled_actions.items()},
            "closed_ns": dict(self.__closed_ns),
            "tombstones": dict(self.__tombstones),
            "compacted": dict(self.__compacted),
            "compacted_ranges": [list(r) for r in self.__compacted_ranges],
            "finalized": list(self.__finalized),
        }

    def _restore_state(self, state: dict) -> None:
        """Authenticated checkpoint feedback retains its original handles and delivery order."""
        if self.__ledger.final:
            raise RuntimeError("world is final")
        for name in ("decisions", "retired", "successors", "deliveries", "returns",
                     "settled_contracts"):
            setattr(self, f"_DecisionQueue__{name}", state[name])
        self.__declared = state.get("declared", {})
        # Older checkpoints predate releases: every delivery is still held.
        self.__released = dict(state.get("released", {}))
        # Older checkpoints predate action history: no action has a trail yet.
        self.__actions = dict(state.get("actions", {}))
        self.__settled_actions = {k: set(v) for k, v in state.get("settled_actions", {}).items()}
        # Older checkpoints predate decision release (wave 17b): nothing was released,
        # and no final decision recorded when it settled.
        self.__closed_ns = dict(state.get("closed_ns", {}))
        self.__tombstones = {h: tuple(t) for h, t in state.get("tombstones", {}).items()}
        self.__compacted = dict(state.get("compacted", {}))
        self.__compacted_ranges = [list(r) for r in state.get("compacted_ranges", [])]
        self.__finalized = list(state.get("finalized", []))
        self.__pending = {handle: None for handle, decision in self.__decisions.items()
                          if decision.status == SettleStatus.PENDING}
        self.__children = {}
        for decision in self.__decisions.values():
            if decision.parent_handle is not None:
                parent = decision.parent_handle
                self.__children[parent] = self.__children.get(parent, 0) + 1
        self.__held = {}
        for deliveries in self.__deliveries.values():
            for delivery in deliveries:
                self.__held[delivery.handle] = self.__held.get(delivery.handle, 0) + 1
