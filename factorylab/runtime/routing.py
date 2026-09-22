"""Runtime routing method group."""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from types import MappingProxyType
from typing import Any

from factorylab.cortex.assembly import PROGRAM_MODEL_ID
from factorylab.cortex.registration import reward_contracts
from factorylab.kernel.events import Event, EventKind
from factorylab.kernel.queue import PropensityRecord, SettleStatus
from factorylab.kernel.registry import Contract
from factorylab.learners.base import NEUTRAL_REWARD, ObservedRewards
from factorylab.learners.exp3 import EXP3
from factorylab.learners.router import Router, Sample
from factorylab.runtime.shared import (
    CH_CONFORMITY,
    CH_CONSEQUENCE,
    CH_EXPOSURE,
    CH_FAST,
    CH_VERDICT,
    DEF_EVALUATION,
    DEF_EXPOSURE,
    DEF_VERDICT,
    NOOP,
    assembly_rewards,
    return_channel,
)
from factorylab.runtime.subscriptions import is_routine
from factorylab.world.models import ModelRequest


@dataclass(frozen=True)
class PopulationEvent(Event):
    """A population event retains the kernel envelope and cannot impersonate a built-in kind."""

    def __post_init__(self) -> None:
        from factorylab.cortex.registration import event_name

        kind = event_name(self.kind)
        if kind in {str(k) for k in EventKind}:
            raise ValueError("use the kernel event type for a built-in kind")
        # Reuse the kernel's envelope validation and immutable payload construction.
        envelope = Event(self.id, EventKind.REGISTERED, self.ts_ns, self.payload, self.source)
        object.__setattr__(self, "kind", kind)
        object.__setattr__(self, "payload", envelope.payload)


class ContractQueue:
    """A variant contract binds once; the underlying kernel decision never changes channels.

    The kernel routes a polymorphic decision on ``emits``. This adapter resolves
    that declared sum type to the selected public channel, retaining its binding
    in the ledger and checkpoint. Sampling, settlement, money and retirement
    remain owned by the kernel queue; no private queue state is modified.
    """

    def __init__(self, queue, runtime) -> None:
        self.queue = queue
        self.runtime = runtime

    def __getattr__(self, name):
        return getattr(self.queue, name)

    def open(self, *, return_channels=None, **kwargs):
        """Every possible variant is declared before the first metered call."""
        if return_channels:
            return_channels = dict(return_channels)
            if len(set(return_channels.values())) > 1:
                kwargs["channel"] = "emits"
        handle = self.queue.open(**kwargs)
        if return_channels:
            self.runtime.ledger.append({"kind": "decision.contract", "handle": handle,
                                        "return_channels": return_channels})
            self.runtime.return_bindings[handle] = {
                "channels": return_channels, "selected": None}
        return handle

    def bind(self, handle: str, kind: str) -> str | None:
        """A return selects one predeclared channel, irrevocably and before its effects."""
        binding = self.runtime.return_bindings.get(handle)
        if binding is None:
            # A single-channel contract has nothing to select, and a handle the kernel
            # queue never opened has no channel to report.
            try:
                return self.queue.get(handle).channel
            except KeyError:
                return None
        if kind not in binding["channels"]:
            raise ValueError("return emits an undeclared kind")
        if binding["selected"] not in (None, kind):
            raise ValueError("return kind cannot change after tools or children run")
        if binding["selected"] is None:
            if self.queue.get(handle).status is not SettleStatus.PENDING:
                raise ValueError("a finished decision cannot select a return kind")
            self.runtime.ledger.append({"kind": "decision.emits", "handle": handle,
                                        "emits": kind, "channel": binding["channels"][kind]})
            binding["selected"] = kind
        return binding["channels"][kind]

    def _channel(self, handle, channel):
        binding = self.runtime.return_bindings.get(handle)
        if channel == "emits" and binding and binding["selected"] is not None:
            return binding["channels"][binding["selected"]]
        return channel

    def get(self, handle):
        decision = self.queue.get(handle)
        return replace(decision, channel=self._channel(handle, decision.channel))

    def outstanding(self, actor=None):
        return [self.get(d.handle) for d in self.queue.outstanding(actor)]

    def settle(self, handle, *, channel, **kwargs):
        """Only the selected variant may settle; the original kernel routing channel is retained."""
        if channel != self.get(handle).channel:
            raise ValueError("settlement must address the selected return channel")
        return self.queue.settle(handle, channel=self.queue.get(handle).channel, **kwargs)

    def _mapped(self, handle, ret):
        """Return the same feedback under its selected channel, retaining every other field.

        Only a polymorphic ``emits`` return can name a different channel, and a
        return the selection never touches is handed back unchanged: an immutable
        record equals its own copy, so rebuilding one would only cost the reader.
        """
        if ret.channel != "emits":
            return ret
        channel = self._channel(handle, ret.channel)
        return ret if channel == ret.channel else replace(ret, channel=channel)

    def history(self, handle):
        return tuple(self._mapped(handle, r) for r in self.queue.history(handle))

    def returns_for(self, actor):
        return tuple(self._mapped(r.handle, r) for r in self.queue.returns_for(actor))

    def returns_since(self, actor, start):
        """``(returns_for(actor)[start:], len(returns_for(actor)))``, mapping only the tail."""
        raw = self.queue.returns_for(actor)
        return tuple(self._mapped(r.handle, r) for r in raw[start:]), len(raw)


#: What a round that delivered nothing scores, per score definition a router can be
#: trained on: the reward an abstention (NOOP) is credited, so a seat is woken more
#: only by beating what doing nothing would have scored on the same scale. A NOOP
#: that a know-nothing seat outscores is a dead arm: the router pays to wake someone
#: every time, which is thrash's bill, "the entire cost of exploration" for nothing
#: delivered (essay II.II.a). Only settled scores need a value: censored,
#: inapplicable, unmeasured, declined, timed-out and uninformative rounds carry no
#: score and are imputed. A definition not listed is worth ``NEUTRAL_REWARD``.
ZERO_CONSEQUENCE: Mapping[str, float] = MappingProxyType({
    # Producer scores on the midpoint scale: the mean verdict of an uninformed judge.
    DEF_VERDICT: 0.5,
    # An evaluator decision's two signals are both centred at 0.5: an uninformed tier
    # grade, and a prediction no better than the base rate (``consequence_score``).
    DEF_EVALUATION: 0.5,
    # 1 when a ballot matched the promise the world kept: a coin-flip ballot expects 0.5.
    "policy-promise-brier-v2": 0.5,
    # Brier scores, 1 - (q - y)^2: the uninformed forecaster (q = 0.5) earns 0.75
    # whatever happens. The per-predicate prevalence baseline scores at least that,
    # but it prices a judge's standing question by question, not a router's round.
    "brier-v1": 0.75,
    "forecast-mean-v1": 0.75,  # the mean brier-v1 of a forecast return's predictions
    # 1 - the judges' consequence score on the antagonist's return: an antagonist
    # whose return they predicted exactly as well as the base rate earns 0.5.
    DEF_EXPOSURE: 0.5,
})


def zero_consequence(definition: str) -> float:
    """What a round settled under ``definition`` scores when it delivered nothing."""
    return ZERO_CONSEQUENCE.get(definition, NEUTRAL_REWARD)


def learning_death_floor(gamma: float) -> float:
    """The NOOP probability at or above which a draw woke its seats only by exploration.

    Learning death is the frontier that "is no longer being invoked" (essay II.II.a).
    A router whose every draw in a whole window gave NOOP at least ``1 - gamma`` left
    its seats at most the exploration mass: they sat at the gamma floor all window.
    """
    return 1.0 - gamma


@dataclass
class RouterState:
    kind: str
    universe: list[str]
    learner: Any
    router: Router
    epoch: int = 1
    seed_gamma: float = 0.1
    # The rewards this router's own draws observed, per arm: what a censored draw
    # is credited instead of a zero (defect 2).
    observed: ObservedRewards = field(default_factory=ObservedRewards)
    # The live router that replaced this one: a retired router's settled rounds
    # train its successor, so no reward is spent on a copy that never samples again.
    successor: str | None = None
    # [total ns, rounds]: how long this router's learned seat rounds took to be
    # learned, the delay an abstention's credit is deferred by.
    latency: list[int] = field(default_factory=lambda: [0, 0])
    # definition -> learned seat rounds settled under it: the scales this router's
    # rewards are on, and so what an abstention is worth to it (``neutral``).
    definitions: dict[str, int] = field(default_factory=dict)
    # This window's NOOP watch, {"window", "draws", "min_p"}; empty before a draw.
    # Observation only: nothing in routing or learning reads it.
    watch: dict = field(default_factory=dict)

    def neutral(self) -> float:
        """Guarantees the zero-consequence reward of the rounds this router learns from.

        It is the mean of ``zero_consequence`` over the definitions its learned seat
        rounds settled under, weighted by how many settled under each: a router whose
        seats are scored by Brier credits NOOP 0.75, one scored on producer outcomes
        0.5, and a mixed router what its own wakes would have scored had every woken
        seat delivered nothing. ``NEUTRAL_REWARD`` before any seat round is learned.
        Independent of insertion order, so a resumed router computes the same value.
        """
        total = sum(self.definitions.values())
        if not total:
            return NEUTRAL_REWARD
        return math.fsum(zero_consequence(d) * self.definitions[d]
                         for d in sorted(self.definitions)) / total

    def state(self) -> dict:
        """Retain the exact learner, public universe order, comparator epoch and successor."""
        saved = {
            "kind": self.kind,
            "universe": list(self.universe),
            "router": self.router.state(),
            "epoch": self.epoch,
            "seed_gamma": self.seed_gamma,
            "observed": self.observed.state(),
        }
        if self.successor is not None:
            saved["successor"] = self.successor
        if self.latency[1]:
            saved["latency"] = list(self.latency)
        if self.definitions:
            saved["definitions"] = dict(self.definitions)
        if self.watch:
            saved["watch"] = dict(self.watch)
        return saved

    @classmethod
    def restore(cls, state: dict) -> RouterState:
        """Restore the router and its learner as the same object against the saved menu."""
        from factorylab.learners.base import restore_learner

        saved = state["router"]["learner"]
        learner = (
            _KeyedLearner.restore(saved)
            if saved["algorithm"] == "KeyedLearner"
            else restore_learner(saved)
        )
        universe = list(state["universe"])
        router = Router(learner, lambda _k: [a for a in universe if a != NOOP])
        return cls(state["kind"], universe, learner, router, state["epoch"],
                   state.get("seed_gamma", 0.1), ObservedRewards(state.get("observed")),
                   state.get("successor"), list(state.get("latency", [0, 0])),
                   dict(state.get("definitions", {})), dict(state.get("watch", {})))


class _KeyedLearner:
    """Adapter so a SnapshotLearner can be driven through Router.route.

    The runtime sets ``current_key`` before routing; ``distribution`` records
    the snapshot under that key. Updates go through ``inner.update_for``.
    """

    def __init__(self, inner: Any) -> None:
        self.inner = inner
        self.id = inner.id
        self.current_key: str | None = None

    def distribution(self, feasible):
        if self.current_key is None:
            return self.inner.distribution(feasible)
        return self.inner.distribution_for(self.current_key, feasible)

    def update(self, feedback) -> None:
        raise TypeError("use inner.update_for(key, feedback)")

    def record_executed(self, distribution: dict[str, float]) -> None:
        """The current keyed round retains the policy the router actually sampled."""
        if self.current_key is not None:
            self.inner.record_executed(self.current_key, distribution)

    def state(self) -> dict:
        """Preserve the adapter's current decision key as well as all frozen learning rounds."""
        return {
            "algorithm": "KeyedLearner",
            "inner": self.inner.state(),
            "current_key": self.current_key,
        }

    @classmethod
    def restore(cls, state: dict) -> _KeyedLearner:
        """Rebind a complete snapshot learner without losing its in-flight routing key."""
        from factorylab.learners.delayed import SnapshotLearner

        learner = cls(SnapshotLearner.restore(state["inner"]))
        learner.current_key = state["current_key"]
        return learner


class RoutingMixin:
    """Preserve runtime state and behavior for routing operations."""

    def _routable_kinds(self) -> list[str]:
        kinds = {k for aid, a in self.assemblies.items()
                 if aid not in self.retired_assemblies for k in a.spec.accepts}
        return sorted(kinds)

    def _event_kinds(self) -> frozenset[str]:
        """World kinds and published contracts remain discoverable after a retirement."""
        return frozenset({str(k) for k in EventKind} | {"Exposure"}
                         | set(self.event_schemas)
                         | {k for a in self.assemblies.values() for k in a.spec.accepts})

    def _ancestry(self, handle: str | None) -> set[str]:
        """Request parents and evaluated subjects retain the complete causal ancestry."""
        seen = set()
        remaining = [handle] if handle else []
        while remaining:
            current = remaining.pop()
            if current in seen:
                continue
            seen.add(current)
            try:
                parent = self.queue.get(current).parent_handle
            except KeyError:
                parent = None
            for ancestor in (parent, self.decision_subjects.get(current)):
                if ancestor is not None:
                    remaining.append(ancestor)
        return seen

    def _subject_authors(self, kind: str, ev: Event | None) -> set[str]:
        """Assemblies that authored an event's subject, or the parent of a child's return.

        Nothing judges its own output (essay II.III): the author of a return, a
        verdict or a meta verdict never sits on the router that judges it, and a
        parent never judges the child it requested.
        """
        if ev is None:
            return set()
        subject = self._event_subject(ev)
        return {self.handle_to_assembly[h] for h in self._ancestry(subject)
                if h in self.handle_to_assembly}

    @staticmethod
    def _event_subject(ev: Event) -> str | None:
        key = {"Verdict": "evaluator_handle", "MetaVerdict": "by"}.get(
            str(ev.kind), "about_handle")
        return ev.payload.get(key)

    def _higher_tier_universe(self, chosen: str) -> list[str]:
        """The assemblies that could judge the meta verdict ``chosen`` is about to emit.

        Nothing judges its own output , so the tier above this decision always
        excludes the meta making it: a recursive meta that is the only assembly
        accepting ``MetaVerdict`` is terminal on the tier it judges, and its
        conformity is graded against the consequence rather than waiting for
        a verdict that no one can give.
        """
        kinds = (self.assemblies[chosen].spec.emits if chosen in self.assemblies
                 else ("MetaVerdict",))
        return sorted(
            a.spec.id
            for a in self.assemblies.values()
            if set(kinds) & set(a.spec.accepts)
            and a.spec.id != chosen
            and a.spec.id not in self.retired_assemblies
            and set(assembly_rewards(a.spec).values()) & {"forecast", "conformity"}
        )

    def _universe_for(self, kind: str, ev: Event | None = None) -> list[str]:
        excluded = self._subject_authors(kind, ev)
        ids = sorted(
            a.spec.id
            for a in self.assemblies.values()
            if kind in a.spec.accepts
            and (a.spec.id not in excluded
                 or not set(assembly_rewards(a.spec).values()) <= {"forecast", "conformity"})
            and a.spec.id not in self.retired_assemblies
        )
        return ids + [NOOP]

    def _all_router_states(self) -> list[RouterState]:
        return [st for states in self.routers.values() for st in states]

    def _seed_learner_kind(self, kind: str) -> str:
        """The algorithm a router the runtime seeds itself for ``kind`` runs.

        Guarantees a no-swap-regret (Blum-Mansour) router exactly for the event kinds
        the manifest names in ``[evaluation] no_swap_regret_kinds`` (the retentive
        core, essay II.a) and mean-based EXP3 for every other kind (the frontier).
        """
        return ("blum_mansour" if kind in self.m.evaluation.no_swap_regret_kinds
                else "exp3")

    def _hand_over(self, retired: RouterState, successor: str) -> None:
        """Point ``retired`` and every router that handed over to it at ``successor``."""
        old = retired.learner.id
        retired.successor = successor
        for state in self.retired_routers.values():
            if state.successor == old:
                state.successor = successor

    def _successor_state(self, state: RouterState) -> RouterState:
        """The live router that learns ``state``'s settled rounds; ``state`` if none is."""
        if state.successor is None:
            return state
        return next((st for st in self._all_router_states()
                     if st.learner.id == state.successor), state)

    def _make_learner(
        self, kind: str, learner_kind: str, gamma: float, universe: list[str], lid: str
    ):
        if learner_kind == "blum_mansour":
            from factorylab.learners.blum_mansour import BlumMansour
            from factorylab.learners.delayed import SnapshotLearner

            inner = BlumMansour(lambda acts: EXP3(acts, gamma), universe, id=lid)
            return _KeyedLearner(SnapshotLearner(inner, id=lid))
        return EXP3(universe, gamma, id=lid)

    def _build_router(
        self, kind: str, learner_kind: str, gamma: float, *, replace: bool = True
    ) -> RouterState:
        """Create a router for ``kind``. ``replace`` swaps the whole set; else one is added."""
        universe = self._universe_for(kind)
        existing = self.routers.get(kind, [])
        index = 0 if replace else len(existing)
        if not replace and len(existing) >= self.m.tools.max_routers_per_kind:
            raise ValueError("router cap reached for this event kind")
        lid = f"router:{kind}" if index == 0 else f"router:{kind}#{index}"
        lid = self._fresh_router_id(lid)
        learner = self._make_learner(kind, learner_kind, gamma, universe, lid)
        router = Router(learner, lambda _k, u=universe: [x for x in u if x != NOOP])
        state = RouterState(kind, universe, learner, router, seed_gamma=gamma)
        created = {"kind": "router.created", "learner_id": lid, "event_kind": kind,
                   "replaces": [st.learner.id for st in existing] if replace else []}
        if learner_kind != "exp3":
            created["learner"] = learner_kind
        self.ledger.append(created)
        if replace:
            for retired in existing:
                self._hand_over(retired, lid)
                self._retain_router(retired)
            self.routers[kind] = [state]
        else:
            self.routers.setdefault(kind, []).append(state)
        if not hasattr(self, "delivered_seen"):
            self.delivered_seen = {}
        self.delivered_seen.setdefault(learner.id, 0)
        return state

    def _retain_router(self, state: RouterState) -> None:
        """Stop sampling an old router while its original decisions can still train it."""
        lid = state.learner.id
        if self.queue.outstanding(lid) or (
            len(self.queue.returns_for(lid)) > self.delivered_seen.get(lid, 0)
        ) or self._router_owed_abstention(lid):
            self.ledger.append({"kind": "router.retained", "learner_id": lid})
            self.retired_routers[lid] = state
        else:
            self.queue.retire_actor(lid)

    def _fresh_router_id(self, base: str) -> str:
        """Fresh learners never receive an active or retired learner's delayed returns."""
        used = set(getattr(self, "delivered_seen", {}))
        used.update(st.learner.id for st in self._all_router_states())
        lid, generation = base, 0
        while lid in used:
            generation += 1
            lid = f"{base}@{generation}"
        return lid

    def _unhistoried(self, action_id: str) -> bool:
        """No settled record, or an unfinished population trial, admits protected compute.

        A population assembly's trial ends when ``novelty.trials`` settled
        consequences have been delivered to it (continuations and children do not
        count) or ``novelty.max_lifetime_windows`` have passed since its
        registration, whichever comes first: the lifetime ends the trial even when
        no consequence ever arrived, so silence is not an unbounded entitlement
        (essay II.IV.b: the compensation period must be shorter than the lifetime).
        The window after a learning-death flag grants one more trial. A seed
        assembly has no registration window; it is protected until its first
        settled record.
        """
        try:
            population = self.registry.get(action_id).provenance != "seed"
        except KeyError:  # no contract: nothing the population registered, so no lifetime
            population = False
        if not population:
            return not self.queue.has_history(action_id)
        born = self.stats.registered_window.get(action_id, self.stats.reserve_windows)
        if self.stats.reserve_windows - born >= self.m.novelty.max_lifetime_windows:
            return False
        if not self.queue.has_history(action_id):
            return True
        delivered = self.stats.consequences_by_assembly.get(action_id, 0)
        return delivered < self.m.novelty.trials or self._novelty_grant_open(action_id)

    def _novelty_grant_open(self, assembly_id: str) -> bool:
        """A learning-death grant is one extra trial per assembly, live only in the window
        it was issued for and spent by that assembly's first delivered trial beyond the
        base allowance; an unspent grant expires at the next boundary."""
        grant = self.novelty_grant
        return (grant["window"] == self.stats.reserve_windows
                and assembly_id not in grant["consumed"])

    def _register_with_trial(self, contract: Contract, handle: str, amount: int,
                             *, refuse: str = ""):
        """A refused registration returns its trial to the window; only a registered
        contract consumes the novelty share. ``refuse`` states a refusal the registry
        cannot see, so it is still paid for and refunded like any other."""
        receipt = self.reserve.reserve_for(contract, amount)
        try:
            if refuse:
                raise ValueError(refuse)
            self.registry.register(contract, by_handle=handle, reservation=receipt)
        except Exception:
            self.reserve.release(receipt)
            raise
        return receipt

    def _registration_has_history(self, contract_id: str) -> bool:
        """A retired assembly may issue its next contract version without erasing learner history.

        This callback governs registration receipts only. Compute eligibility still
        reads the durable queue history and the assembly's original trial lifetime.
        """
        return (contract_id not in getattr(self, "retired_assemblies", ())
                and self.queue.has_history(contract_id))

    def _novelty_compute(self, handle: str, reason: str) -> bool:
        """Only an assembly's own model calls can use its novelty entitlement."""
        if not reason.startswith("model:"):
            return False
        try:
            action = self.queue.get(handle).propensity.chosen
        except KeyError:
            return False
        return (action in self.assemblies and self._unhistoried(action)
                and reason == f"model:{self.assemblies[action].spec.model_id}")

    def _compute_available(self, handle: str) -> int:
        """The request ceiling includes protection only for its sampled unhistoried assembly."""
        try:
            action = self.queue.get(handle).propensity.chosen
        except KeyError:
            return self.wallet.available
        if action not in self.assemblies:
            return self.wallet.available
        model = self.assemblies[action].spec.model_id
        return self.wallet.available_for(handle, f"model:{model}")

    def _seat_need(self, action_id: str) -> int:
        """The ceiling one call of this seat needs now: its last rendered ceiling plus the
        input price of every character the world block has grown by since (at the
        meter's own slack). Before its first call, the ceiling of its last hold.
        A flat-fee seat (a program) needs its fee: the world block's growth costs
        it nothing, so there is no growth term and no token price to look up."""
        record = self.seat_ceilings.get(action_id)
        if record is None:
            return self.budget.last_hold(action_id)
        if not record["world_chars"]:
            # Priced without a world block (a ballot, a bare request): the world's
            # size is not what that call grew with, and a routed call that turns
            # out dearer is bridged rather than failed.
            return record["ceiling"]
        asm = self.assemblies[action_id]
        if asm.spec.model_id == PROGRAM_MODEL_ID:
            return record["ceiling"]
        growth = max(0, self._current_world_chars() - record["world_chars"])
        if not growth:
            return record["ceiling"]
        price = self.prices.price(asm.spec.model_id)
        slack = getattr(asm.model, "input_slack", 1.5)
        return record["ceiling"] + price.cost(int(growth * slack), 0) - price.cost(0, 0)

    def _current_world_chars(self) -> int:
        """The world block's rendered size for this event, measured once per event."""
        cached = getattr(self, "_world_chars_cache", None)
        if cached is None or cached[0] != self.n:
            cached = (self.n, self._world_chars(self._world_block()))
            self._world_chars_cache = cached
        return cached[1]

    def _is_feasible(self, action_id: str) -> tuple[bool, str]:
        asm = self.assemblies[action_id]
        probe = ModelRequest(
            asm.spec.model_id,
            asm.spec.system_prompt,
            ({"role": "user", "content": ""},),
            asm.spec.max_tokens,
        )
        is_market = asm.spec.model_id.startswith("x402:")
        ceiling = asm.model.ceiling(probe) * (1 if is_market else 2)
        available = (self.wallet.unhistoried_available if self._unhistoried(action_id)
                     else self.wallet.available)
        if ceiling > available:
            return False, f"compute: ceiling {ceiling} exceeds wallet {available}"
        # An exhausted entitlement is the seat's own state, not the factory's: it is
        # infeasible for this request until credited, and never counts as insolvency.
        # The empty probe above only bounds a call from below; the seat's last real
        # ceiling, repriced for the world block's growth since, is what one of its
        # calls needs now. An unhistoried seat's trial is funded by the unallocated
        # pool as well, exactly as the wallet's own check lets a protected call use
        # everything the novelty share does not withhold.
        entitlement = self.budget.entitlement(action_id)
        cover = self.budget.cover(action_id, self._protected_share(action_id))
        need = max(ceiling, self._seat_need(action_id))
        if need > cover:
            return False, f"entitlement: ceiling {need} exceeds seat entitlement {entitlement}"
        try:
            if is_market:
                return self.market.affordable(asm.spec.model_id, ceiling)
            if hasattr(self.provider, "affordable"):
                return self.provider.affordable(asm.spec.model_id, ceiling)
            if hasattr(self.provider, "balance_micro"):
                balance = self.provider.balance_micro()
                if balance is not None and balance < ceiling:
                    return False, f"compute: provider balance {balance} below ceiling {ceiling}"
        except Exception:
            return False, "provider: balance unavailable"
        return True, ""

    def _cap_adversarial(self, dist: dict[str, float]) -> dict[str, float]:
        """Antagonist mass is renormalised to at most ``evaluation.adversarial_share``.

        The essay's adversarial minority (II.III.b) is a constraint on routing,
        not a prize exposure wins can grow.
        """
        share = self.ev.adversarial_share
        adversaries = [a for a in dist if a in self.assemblies
                       and "exposure" in assembly_rewards(self.assemblies[a].spec).values()]
        rest = [a for a in dist if a not in adversaries]
        mass = sum(dist[a] for a in adversaries)
        rest_mass = sum(dist[a] for a in rest)
        if mass <= share or not rest or rest_mass <= 0:
            return dist
        return {a: (dist[a] * share / mass if a in adversaries
                    else dist[a] * (1 - share) / rest_mass) for a in dist}

    def _mix_with_standing(self, dist: dict[str, float]) -> dict[str, float]:
        s = self.consequence_mix
        evaluators = [a for a in dist if a in self.assemblies
                      and "forecast" in assembly_rewards(self.assemblies[a].spec).values()]
        if s <= 0 or not evaluators:
            return dist
        weights = {a: self.standing.weight(a) for a in evaluators}
        total = sum(weights.values())
        if total <= 0:
            return dist
        mixed = {a: (1 - s) * p + s * (weights.get(a, 0.0) / total) for a, p in dist.items()}
        norm = sum(mixed.values())
        return {a: p / norm for a, p in mixed.items()}

    def _route(self, ev: Event) -> None:
        kind = str(ev.kind)
        conformity = (ev.kind is EventKind.META_VERDICT
                      or self._kind_rewards().get(kind) == "conformity")
        if conformity:
            self._deliver_meta_verdict(ev)
        # Admission is by declared reward shape, not by seed kind name: a judgement
        # buys no faster path to the tier above it by being registered under a new
        # name. Tier one is the seed Verdict; every conformity-shaped kind, seed or
        # population, is buffered with the others at the tier it judges.
        if ev.kind is EventKind.VERDICT or conformity:
            ev = self._cascade_arrival(ev)
            if ev is None:
                return
        states = list(self.routers.get(kind, []))
        for state in states:
            if self.wallet.dead:
                break
            self._route_with(state, ev)

    def _addressed_seat(self, ev: Event) -> str | None:
        """The one seat an event is addressed to, or None when the draw is open.

        Edition 3, C2: a watcher's firing is its owner's news. It is not work
        the router hands to whoever accepts the kind — it answers a predicate
        that seat registered and paid for.
        """
        if str(ev.kind) != str(EventKind.WATCHER_FIRED):
            return None
        owner = ev.payload.get("owner")
        return owner if isinstance(owner, str) else None

    def _asleep(self, action_id: str, ev: Event) -> str:
        """Why this seat is not in the draw for this event, or "" when it is awake.

        Edition 3, C2: a seat owns its subscription and its sleep. A deferred or
        unsubscribed seat is absent from the draw — not woken, not paid, and not
        recorded as having abstained.
        """
        book = getattr(self, "subscription_book", None)
        if book is None:
            return ""
        addressed = self._addressed_seat(ev)
        if addressed is not None and action_id != addressed:
            return "asleep: this event is addressed to another seat"
        return book.absent(action_id, str(ev.kind), now=self.tick_index,
                           coins=book.fold_coins(action_id))

    def _quiet_tick(self, ev: Event, candidates: list[str],
                    excluded: dict[str, str]) -> bool:
        """True when this draw reached nobody because the seats were asleep.

        A tick that routes to nobody is not an abstention; it is nothing. It is
        ledgered once, with how many seats were absent and why, and no decision
        is opened. Unaffordability is left exactly as it was: a draw where any
        seat was excluded for compute keeps the old path, because that exclusion
        is what the insolvency streak is made of.
        """
        if not candidates or any(a not in excluded for a in candidates):
            return False
        reasons = {a: excluded[a] for a in candidates}
        if any(r.startswith("compute:") for r in reasons.values()):
            return False
        if not any(r.startswith("asleep:") for r in reasons.values()):
            return False
        self.ledger.append({"kind": "tick.quiet", "n": self.n, "event_id": ev.id,
                            "event_kind": str(ev.kind), "absent": len(reasons),
                            "why": dict(sorted(reasons.items())), "ts": self.clock.now_ns})
        return True

    def _route_with(self, state: RouterState, ev: Event) -> None:
        kind = str(ev.kind)
        def mix(dist):
            return self._cap_adversarial(self._mix_with_standing(dist))

        key = f"{state.learner.id}:{self.n}"
        if isinstance(state.learner, _KeyedLearner):
            state.learner.current_key = key
        universe = self._universe_for(kind, ev)

        def feasible(action_id: str) -> tuple[bool, str]:
            if action_id not in universe:
                return False, "self-judgement"
            asleep = self._asleep(action_id, ev)
            if asleep:
                return False, asleep
            return self._is_feasible(action_id)

        sample = state.router.route(kind, feasible, self.rng, mix=mix)
        candidates = [a for a in universe if a != NOOP]
        excluded = dict(sample.excluded)
        if self._quiet_tick(ev, candidates, excluded):
            if isinstance(state.learner, _KeyedLearner):
                # A draw that opened no decision is no round: the snapshot the
                # distribution froze for it would otherwise wait forever.
                state.learner.inner.discard_for(key)
            return
        unaffordable = bool(candidates) and all(
            excluded.get(a, "").startswith("compute:") for a in candidates
        )
        self._record_market(
            {
                "kind": "compute.route",
                "event_id": ev.id,
                "router": state.learner.id,
                "unaffordable": unaffordable,
            }
        )
        self._compute_routed = True
        self._compute_unaffordable |= unaffordable
        self.stats.exclusions += len(sample.excluded)
        for assembly_id, reason in sample.excluded:
            if reason == "self-judgement":
                self.ledger.append({"kind": "route.excluded", "event_id": ev.id,
                                    "router": state.learner.id, "assembly_id": assembly_id,
                                    "reason": reason, "ts": self.clock.now_ns})
        channels = self._return_channels(sample.chosen, ev)
        channel = next(iter(channels.values()), CH_VERDICT)
        deadline = (
            self.clock.now_ns + (self.ev.verdict_timeout_ticks + 2) * self.tick_clock.interval_ns
        )
        if set(channels.values()) & {CH_FAST, CH_CONFORMITY, CH_EXPOSURE, CH_CONSEQUENCE}:
            # An evaluator decision is graded against its judged decision's measured
            # outcome and an exposure against its judges' (ruling R1), so each lives as
            # long as the return's backstop, like a forecast.
            deadline = self.clock.now_ns + (
                (self.ev.consequence_backstop_ticks + 2) * self.tick_clock.interval_ns * 4
            )
        if CH_CONSEQUENCE in channels.values():
            # A population forecast may select any of the admitted 1..200 event
            # horizons; its invocation must not expire before its predictions.
            deadline = max(deadline, self.clock.now_ns + 202 * self.tick_clock.interval_ns * 4)
        handle = self.queue.open(
            actor=sample.learner_id,
            event_id=ev.id,
            propensity=self._propensity(sample),
            channel=channel,
            deadline_ns=deadline,
            parent_handle=None,
            cost_ceiling=(self.wallet.unhistoried_available
                          if sample.chosen != NOOP and self._unhistoried(sample.chosen)
                          else max(0, self.wallet.available)),
            return_channels=channels,
        )
        if isinstance(state.learner, _KeyedLearner):
            self.snapshot_keys[handle] = key
        self._watch_abstention(state, sample)
        self.stats.decisions += 1
        if self.stats.sample_propensity is None and sample.chosen != NOOP:
            self.stats.sample_propensity = {
                "handle": handle,
                "action_ids": list(sample.action_ids),
                "probs": list(sample.probs),
                "chosen": sample.chosen,
                "rng_seed": sample.rng_seed,
            }
        book = getattr(self, "subscription_book", None)
        if book is not None and sample.chosen != NOOP and is_routine(kind):
            # A routine paid wake is what a cadence floor counts the ticks between,
            # and it ends whatever sleep the seat had bought itself.
            book.woke(sample.chosen, now=self.tick_index)
        self._assembly_step(ev, handle, sample, deadline)

    def _watch_abstention(self, state: RouterState, sample: Sample) -> None:
        """Watch this draw's NOOP probability for the window's learning-death entry.

        Guarantees observation only: the draw is made and nothing here is read by
        routing, learning or the manifest. A draw without NOOP on its menu is not
        watched; a draw in a new window closes the last window's watch first.
        """
        if NOOP not in sample.action_ids:
            return
        p = sample.probs[list(sample.action_ids).index(NOOP)]
        self._close_abstention_watch(state)
        if not state.watch:
            state.watch = {"window": self.window.index, "draws": 1, "min_p": p}
            return
        state.watch["draws"] += 1
        state.watch["min_p"] = min(state.watch["min_p"], p)

    def _close_abstention_watch(self, state: RouterState) -> None:
        """Close a watch whose window has ended, ledgering it if NOOP held that window.

        Guarantees one ``router.learning_death`` entry per router and window in which
        every draw gave NOOP at least ``learning_death_floor(seed_gamma)``: its seats
        were woken only by exploration all window, the frontier "no longer being
        invoked" (essay II.II.a). It is evidence for a reader; nothing reads it back,
        and it is apart from the immune organ's ``pathology.learning_death`` flag.
        """
        watch = state.watch
        if not watch or watch["window"] == self.window.index:
            return
        state.watch = {}
        floor = learning_death_floor(state.seed_gamma)
        if watch["min_p"] >= floor:
            self.ledger.append({"kind": "router.learning_death",
                                "learner_id": state.learner.id, "event_kind": state.kind,
                                "window": watch["window"], "draws": watch["draws"],
                                "min_p_noop": watch["min_p"], "floor": floor,
                                "neutral": state.neutral(), "ts": self.clock.now_ns})

    @staticmethod
    def _propensity(sample: Sample) -> PropensityRecord:
        return PropensityRecord(
            sample.action_ids,
            sample.probs,
            sample.chosen,
            sample.rng_seed,
            sample.learner_id,
            sample.learner_state_hash,
        )

    def _return_channels(self, action_id: str, ev: Event | None = None) -> dict[str, str]:
        """Each output kind declares its reward contract, independent of the accepted event."""
        excluded = self._subject_authors(str(ev.kind), ev) if ev else set()
        higher = set(self._higher_tier_universe(action_id)) - excluded
        if action_id == NOOP:
            # Abstention shares a homogeneous menu's contract, including the four
            # shipped seeds. A mixed menu has no selected output and is inapplicable.
            kinds = {kind for a in self._universe_for(str(ev.kind), ev) if a != NOOP
                     for kind in self.assemblies[a].spec.emits}
            kinds = kinds if len(kinds) == 1 else {"ProducerReturn"}
        else:
            kinds = self.assemblies[action_id].spec.emits
        shapes = {}
        actions = (self._universe_for(str(ev.kind), ev) if action_id == NOOP
                   else (action_id,))
        for action in actions:
            if action == NOOP:
                continue
            spec = self.assemblies[action].spec
            declared = assembly_rewards(spec)
            for kind, shape in declared.items():
                if kind in shapes and shapes[kind] != shape:
                    raise ValueError(f"reward shape already declared differently: {kind}")
                shapes[kind] = shape
        defaults = reward_contracts(tuple(kinds))
        return {kind: return_channel(kind, shapes.get(kind, defaults[kind]), higher=bool(higher))
                for kind in kinds}

    def _open_epoch(self, kind: str) -> None:
        universe = self._universe_for(kind)
        entry = {"kind": "epoch", "event_kind": kind, "universe": universe, "ts": self.clock.now_ns}
        states = self.routers.get(kind)
        if not states:
            self._build_router(kind, self._seed_learner_kind(kind), self.router_gamma)
            self.ledger.append({**entry, "carried": False})
            self.stats.epochs += 1
            return
        for i, state in enumerate(list(states)):
            if universe == state.universe:
                continue
            if isinstance(state.learner, EXP3) and set(state.universe) <= set(universe):
                new_learner = state.learner.expand(universe)
                state.learner = new_learner
                state.universe = universe
                state.router = Router(
                    new_learner, lambda _k, u=universe: [x for x in u if x != NOOP]
                )
                state.epoch += 1
                self.ledger.append({**entry, "carried": True, "router": state.learner.id})
            else:
                # A shrinking universe (or any swap router's) gets a new identity that
                # keeps the weights learned so far; the old identity stays addressable
                # for its in-flight decisions, whose settled rounds train the new one.
                lid = self._fresh_router_id(state.learner.id)
                if isinstance(state.learner, EXP3):
                    saved = state.learner.state()
                    # A universe may lose one action and gain another in the same
                    # epoch (a seat's accepts change while another registers). The
                    # survivors keep their weights and an action this learner never
                    # held starts at their mean, exactly as ``EXP3.expand`` admits a
                    # new one, rather than raising on a weight that was never there.
                    retained = {a: w for a, w in saved["log_weights"].items() if a in universe}
                    mean = sum(retained.values()) / len(retained) if retained else 0.0
                    saved.update(id=lid, actions=list(universe), log_weights={
                        a: retained.get(a, mean) for a in universe})
                    fresh = EXP3.restore(saved)
                else:
                    from factorylab.learners.delayed import SnapshotLearner

                    swap = state.learner.inner.inner.reshaped(universe, id=lid)
                    fresh = _KeyedLearner(SnapshotLearner(swap, id=lid))
                self.ledger.append({**entry, "carried": False, "router": lid})
                self._hand_over(state, lid)
                self._retain_router(state)
                self.delivered_seen[lid] = 0
                states[i] = RouterState(
                    kind,
                    universe,
                    fresh,
                    Router(fresh, lambda _k, u=universe: [x for x in u if x != NOOP]),
                    state.epoch + 1,
                    state.seed_gamma,
                    # The new identity learns on the same arms' evidence it inherits.
                    ObservedRewards(state.observed.state()),
                    latency=list(state.latency),
                    definitions=dict(state.definitions),
                    watch=dict(state.watch),
                )
            self.stats.epochs += 1

    def _retire_assembly(self, assembly_id: str, proposal_id: str) -> None:
        """Retirement changes sampling membership, retaining accounts, memory and old routers."""
        if assembly_id in self.retired_assemblies:
            return
        self.ledger.append({"kind": "assembly.retired", "assembly_id": assembly_id,
                            "proposal_id": proposal_id,
                            "version": self.assemblies[assembly_id].spec.version})
        self.retired_assemblies.add(assembly_id)
        book = getattr(self, "subscription_book", None)
        if book is not None:
            # A retired watcher stops being evaluated, and stops being charged for it.
            book.forget(assembly_id)
        self.budget.retire(assembly_id, f"retire:{proposal_id}")
        for kind in sorted(self.routers):
            self._open_epoch(kind)
