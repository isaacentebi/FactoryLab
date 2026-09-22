"""Composition through contracts (essay II.I, II.I.b; Chapter II rulings §2, R11, W4).

Seats compose one another's work only through published contracts: an assembly's
self-description, a tool's promised return, and a request addressed to a *kind* of
work rather than to a peer's id. What composition earns flows back through the one
reward channel there is. Nothing here is a channel between seats.
"""

from __future__ import annotations

import math
from dataclasses import replace
from typing import Any

from factorylab.cortex.registration import AssemblyProposal, ToolProposal
from factorylab.cortex.request import ChildRequest, Request, public_return
from factorylab.cortex.tools import as_spec
from factorylab.kernel.queue import PropensityRecord
from factorylab.learners.router import Sample
from factorylab.runtime.routing import _KeyedLearner
from factorylab.runtime.shared import NOOP, request_router_key
from factorylab.settlement.vocabulary import COMMISSIONED_JUDGE_REFUSAL

#: The learner-state tag of a requester's forwarded propensity on its child's handle.
REQUESTER_DECLARED = "requester-declared"


class CompositionMixin:
    """Contracts that carry their own description, and the composition built on them."""

    def _register(self, handle: str, prop: Any, *, predicted_effect: Any = None) -> None:
        """Register as governance does, then publish the contract's own promises.

        Guarantees an admitted assembly carries the description its proposal gave
        (primitive audit F6) and an admitted tool the ``returns_schema`` it promised
        (F9), both on the objects the catalogue publishes and the checkpoint keeps.
        A proposal governance refused raises before either is touched.
        """
        super()._register(handle, prop, predicted_effect=predicted_effect)
        if isinstance(prop, AssemblyProposal) and prop.description:
            assembly = self.assemblies[prop.id]
            assembly.spec = replace(assembly.spec, description=prop.description)
        elif isinstance(prop, ToolProposal) and prop.returns_schema is not None:
            tool = replace(self.population_tools[prop.id],
                           returns_schema=dict(prop.returns_schema))
            self.population_tools[prop.id] = tool
            self.tool_specs[prop.id] = as_spec(tool, self.m.tools.population_tool_micro_per_call)

    # --- requests addressed to kinds (primitive audit F5, information audit M1) -------

    def _refuse_request(self, parent: Request, item: ChildRequest, reason: str,
                        **extra: Any) -> tuple[dict, int]:
        """Refuse one request before any decision opens; its reason reaches its author."""
        self.ledger.append({"kind": "requests.refused", "handle": parent.handle,
                            "target": item.target, "reason": reason, **extra,
                            "ts": self.clock.now_ns})
        self._refusal_to_owner(parent.handle, "request_refused", reason)
        return {"tool": f"request:{item.target}", "args": item.inputs,
                "result": {"error": reason}}, 0

    def _draw_executor(self, requester: str, parent_handle: str,
                       kind: str) -> tuple[Sample, str | None] | str:
        """Draw who executes a request for ``kind``, or say why no one can.

        Guarantees the executor is sampled by the router for requests of ``kind``
        (``request_router_key``), over the live contracts that emit it, else accept
        it (``_request_universe``), with the propensity it drew at logged on the
        sample: composition is sampled and learned, never a name the requester
        holds (essay II.I: "without any single system or agent needing to hold the
        full topology"). The requester is never drawn for its own request (it has
        ``"self"``), and the draw is never NOOP: a request is work already paid
        for, and who does it is the only choice left to the router. A contract that
        retired is simply not on the menu, so retirement never fails a request
        another contract can serve.
        """
        key = request_router_key(kind)
        offered = [a for a in self._universe_for(key) if a not in (NOOP, requester)]
        if not offered:
            judging = any(kind in a.spec.emits for aid, a in self.assemblies.items()
                          if aid not in self.retired_assemblies and aid != requester)
            return (COMMISSIONED_JUDGE_REFUSAL if judging else
                    f"no live contract other than the requester emits or accepts {kind}")
        # A new menu (a registration, a retirement) is a new comparator epoch for
        # this router, exactly as for an event router (``_open_epoch``).
        self._open_epoch(key)
        state = self.routers[key][0]
        snapshot = None
        if isinstance(state.learner, _KeyedLearner):
            snapshot = f"{state.learner.id}:{self.n}:{parent_handle}:{self.stats.decisions}"
            state.learner.current_key = snapshot

        def feasible(action_id: str) -> tuple[bool, str]:
            if action_id in offered:
                return True, ""
            return False, "requester" if action_id == requester else "not offered"

        def work(dist: dict[str, float]) -> dict[str, float]:
            mass = math.fsum(p for a, p in dist.items() if a != NOOP)
            return {a: (0.0 if a == NOOP else p / mass) for a, p in dist.items()}

        sample = state.router.route(key, feasible, self.rng, mix=work)
        return sample, snapshot

    def _forwarded_propensity(self, handle: str, item: ChildRequest,
                              actor: str) -> PropensityRecord | None:
        """Record the requester's own propensity on its child's handle (information M1).

        Guarantees the record is the requester's declared distribution, normalised,
        with its ``chosen`` action, tagged ``requester-declared`` and never
        ``sampled``: no router drew it, so no router trains on it. It sits beside the
        router's draw (the handle's first record) and before the executor's own
        declaration, so every account of this decision travels with it.
        """
        if item.propensity is None or item.chosen is None:
            return None
        total = math.fsum(item.propensity.values())
        try:
            record = PropensityRecord(
                tuple(item.propensity), tuple(p / total for p in item.propensity.values()),
                item.chosen, 0, actor, REQUESTER_DECLARED, source="declared")
            self.queue.record_propensity(handle, record)
        except (ValueError, TypeError, ZeroDivisionError) as exc:
            self.ledger.append({"kind": "propensity.refused", "handle": handle,
                                "reason": f"forwarded propensity: {exc}"[:200],
                                "ts": self.clock.now_ns})
            return None
        return record

    def _invoke_child(self, action_id: str, parent: Request, item: ChildRequest,
                      ceiling: int) -> tuple[dict, int]:
        """Open one requested decision on a drawn executor and hand its return back.

        Guarantees, for a request whose ``target`` is a kind, that the executor was
        drawn by that kind's request router and the child's handle is addressed to
        that router with the propensity it sampled (the router learns from the
        child's settlement); for ``"self"``, that the requester's own contract runs
        it under its parent's router as before (``parent-selected``). Either way the
        child spends only its parent's remaining cap, depth is bounded, the
        requester's forwarded propensity is recorded on the handle and carried on
        the child's request, and the result names the kind asked for, never the
        executor (the author of a request and of its answer stays private, essay
        II.I.b).
        """
        depth, cursor = 0, parent.handle
        while self.queue.get(cursor).parent_handle is not None:
            depth += 1
            cursor = self.queue.get(cursor).parent_handle
        if depth >= self.m.tools.max_depth:
            return self._refuse_request(parent, item, "tools.max_depth reached", depth=depth)
        snapshot = None
        if item.target == "self":
            target = action_id
            if (reason := self._commissioned_judge_refusal(target)) is not None:
                return self._refuse_request(parent, item, reason)
            actor = self.queue.get(parent.handle).actor
            record = PropensityRecord((target,), (1.,), target, 0, actor, "parent-selected")
            sample = Sample((target,), (1.,), target, 0, actor, "parent-selected", ())
            deadline = parent.deadline_ns
        else:
            drawn = self._draw_executor(action_id, parent.handle, item.target)
            if isinstance(drawn, str):
                return self._refuse_request(parent, item, drawn)
            sample, snapshot = drawn
            target, actor, record = sample.chosen, sample.learner_id, self._propensity(sample)
            # A drawn child settles only after its requester (the collaboration
            # credit), so its router's cutoff reaches one verdict window past its
            # parent's: a credit that arrives in time trains the router once.
            deadline = parent.deadline_ns + (
                (self.ev.verdict_timeout_ticks + 2) * self.tick_clock.interval_ns)
        # A child spends its parent's money: whatever the parent's remaining request
        # ceiling says, the ceiling never exceeds what the parent's own decision may
        # spend now, so a fresh executor cannot be bought compute the parent lacks.
        ceiling = min(ceiling, max(0, self._compute_available(parent.handle)))
        channels = self._return_channels(target)
        channel = next(iter(channels.values()))
        handle = self.queue.open(
            actor=actor, event_id=f"child-{parent.handle}", propensity=record,
            channel=channel, deadline_ns=deadline, parent_handle=parent.handle,
            cost_ceiling=ceiling, return_channels=channels)
        if snapshot is not None:
            self.snapshot_keys[handle] = snapshot
        forwarded = self._forwarded_propensity(handle, item, actor)
        self.ledger.append({
            "kind": "request.child", "handle": handle, "requested": item.target,
            "target": target, "router": actor if item.target != "self" else None,
            "p": record.probs[record.action_ids.index(target)],
            "resource_liability": parent.handle, "cost_ceiling": ceiling,
            "description": item.description, "inputs": item.inputs,
            "outcome_schema": item.outcome_schema,
            "forwarded_propensity": (dict(zip(forwarded.action_ids, forwarded.probs,
                                              strict=True)) if forwarded else None),
            "forwarded_chosen": forwarded.chosen if forwarded else None})
        self.stats.decisions += 1
        self.consequences.start(handle, self.n)
        req = Request(handle, item.description, {**item.inputs, "world": self._world_block()},
                      {}, item.outcome_schema, parent.deadline_ns, ceiling, parent.handle,
                      "a JSON object satisfying the outcome schema", channel, parent.handle,
                      propensity=dict(item.propensity) if forwarded else None,
                      propensity_chosen=item.chosen if forwarded else None)
        ret = self._run_child(parent, item, handle, target, sample, req)
        return {"tool": f"request:{item.target}", "args": item.inputs,
                "result": {"outputs": public_return(ret.outputs), "status": ret.status,
                           "cost_micro": ret.cost}}, ret.cost
