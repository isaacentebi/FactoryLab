"""Runtime routing method group."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from factorylab.kernel.events import Event, EventKind
from factorylab.kernel.queue import PropensityRecord
from factorylab.learners.exp3 import EXP3
from factorylab.learners.router import Router, Sample
from factorylab.runtime.immune import gamma
from factorylab.runtime.shared import CH_CONFORMITY, CH_EXPOSURE, CH_FAST, CH_VERDICT, NOOP
from factorylab.world.models import ModelRequest


@dataclass
class RouterState:
    kind: str
    universe: list[str]
    learner: Any
    router: Router
    epoch: int = 1
    seed_gamma: float = 0.1

    def state(self) -> dict:
        """Retain the exact learner, public universe order and comparator epoch."""
        return {
            "kind": self.kind,
            "universe": list(self.universe),
            "router": self.router.state(),
            "epoch": self.epoch,
            "seed_gamma": self.seed_gamma,
        }

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
                   state.get("seed_gamma", 0.1))


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
        kinds = {k for a in self.assemblies.values() for k in a.spec.accepts}
        return sorted(kinds)

    def _universe_for(self, kind: str, ev: Event | None = None) -> list[str]:
        judged = None
        if ev is not None and kind in ("Verdict", "MetaVerdict"):
            handle = ev.payload["by"] if kind == "MetaVerdict" else ev.payload["evaluator_handle"]
            judged = self.handle_to_assembly.get(handle)
        ids = sorted(
            a.spec.id
            for a in self.assemblies.values()
            if kind in a.spec.accepts and a.spec.id != judged
        )
        return ids + [NOOP]

    def _all_router_states(self) -> list[RouterState]:
        return [st for states in self.routers.values() for st in states]

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
        self.ledger.append({"kind": "router.created", "learner_id": lid, "event_kind": kind,
                            "replaces": [st.learner.id for st in existing] if replace else []})
        if replace:
            for retired in existing:
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
        ):
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
        """No settled record, or an unspent population assembly trial, admits protected compute."""
        if not self.queue.has_history(action_id):
            return True
        return (self.registry.get(action_id).provenance != "seed"
                and self.stats.invocations_by_assembly.get(action_id, 0)
                < self.m.novelty.trial_invocations)

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

    def _mix_with_standing(self, dist: dict[str, float]) -> dict[str, float]:
        s = self.ev.consequence_share
        evaluators = [a for a in dist if a != NOOP]
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
        if ev.kind is EventKind.META_VERDICT:
            self._deliver_meta_verdict(ev)
        if ev.kind in (EventKind.VERDICT, EventKind.META_VERDICT):
            ev = self._cascade_arrival(ev)
            if ev is None:
                return
        for state in list(self.routers.get(kind, [])):
            if self.wallet.dead:
                break
            self._route_with(state, ev)

    def _route_with(self, state: RouterState, ev: Event) -> None:
        kind = str(ev.kind)
        mix = self._mix_with_standing if kind == "ProducerReturn" else None
        key = f"{state.learner.id}:{self.n}"
        if isinstance(state.learner, _KeyedLearner):
            state.learner.current_key = key
        universe = self._universe_for(kind, ev)

        def feasible(action_id: str) -> tuple[bool, str]:
            if action_id not in universe:
                return False, "self-judgement"
            return self._is_feasible(action_id)

        sample = state.router.route(kind, feasible, self.rng, mix=mix)
        candidates = [a for a in universe if a != NOOP]
        excluded = dict(sample.excluded)
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
        role = self._role_for_kind(kind)
        channel = {"producer": CH_VERDICT, "evaluator": CH_CONFORMITY, "meta": CH_FAST}[role]
        if role == "meta" and any(
            "MetaVerdict" in a.spec.accepts for a in self.assemblies.values()
        ):
            channel = CH_CONFORMITY
        chosen_role = self.assemblies[sample.chosen].spec.role if sample.chosen != NOOP else None
        if chosen_role == "antagonist":
            channel = CH_EXPOSURE
        deadline = (
            self.clock.now_ns + (self.ev.verdict_timeout_events + 2) * self.tick_clock.interval_ns
        )
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
        )
        if isinstance(state.learner, _KeyedLearner):
            self.snapshot_keys[handle] = key
        self.stats.decisions += 1
        if self.stats.sample_propensity is None and sample.chosen != NOOP:
            self.stats.sample_propensity = {
                "handle": handle,
                "action_ids": list(sample.action_ids),
                "probs": list(sample.probs),
                "chosen": sample.chosen,
                "rng_seed": sample.rng_seed,
            }
        if role == "producer":
            self._producer_step(ev, handle, sample, deadline)
        elif role == "evaluator":
            self._evaluator_step(ev, handle, sample, deadline)
        else:
            self._meta_step(ev, handle, sample, deadline)

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

    @staticmethod
    def _role_for_kind(kind: str) -> str:
        if kind == "ProducerReturn":
            return "evaluator"
        if kind in ("Verdict", "MetaVerdict"):
            return "meta"
        return "producer"

    def _open_epoch(self, kind: str) -> None:
        universe = self._universe_for(kind)
        entry = {"kind": "epoch", "event_kind": kind, "universe": universe, "ts": self.clock.now_ns}
        states = self.routers.get(kind)
        if not states:
            self._build_router(kind, "exp3", self.router_gamma)
            self.ledger.append({**entry, "carried": False})
            self.stats.epochs += 1
            return
        for i, state in enumerate(list(states)):
            if universe == state.universe:
                continue
            if isinstance(state.learner, EXP3):
                new_learner = state.learner.expand(universe)
                state.learner = new_learner
                state.universe = universe
                state.router = Router(
                    new_learner, lambda _k, u=universe: [x for x in u if x != NOOP]
                )
                state.epoch += 1
                self.ledger.append({**entry, "carried": True, "router": state.learner.id})
            else:  # snapshot learners cannot expand; rebuild fresh over the new universe
                lid = self._fresh_router_id(state.learner.id)
                fresh = self._make_learner(
                    kind, "blum_mansour", gamma(state.learner), universe, lid
                )
                self.ledger.append({**entry, "carried": False, "router": lid})
                self._retain_router(state)
                self.delivered_seen[lid] = 0
                states[i] = RouterState(
                    kind,
                    universe,
                    fresh,
                    Router(fresh, lambda _k, u=universe: [x for x in u if x != NOOP]),
                    state.epoch + 1,
                    state.seed_gamma,
                )
            self.stats.epochs += 1
