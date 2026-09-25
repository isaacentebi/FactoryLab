"""Composition through contracts (essay II.I, II.I.b; Chapter II rulings §2, R11, W4).

Seats compose one another's work only through published contracts: an assembly's
self-description, a tool's promised return, and a request addressed to a *kind* of
work rather than to a peer's id. What composition earns flows back through the one
reward channel there is. Nothing here is a channel between seats.

"Respond in kind" (essay II.I.b; information audit M3) is not built as a return
path. A child already answers with its outputs, and a child may itself issue
requests (to a kind, with a forwarded propensity), which are self-describing and
author-neutral. A follow-on request addressed *back to its parent* would name a
peer, which is the id addressing F5 removes and a third channel R11 forbids, so a
child that wants more work done asks for a kind like anyone else.
"""

from __future__ import annotations

import math
from statistics import fmean
from typing import Any

from factorylab.cortex.assembly import with_counterfactual
from factorylab.cortex.registration import ToolProposal
from factorylab.cortex.request import ChildRequest, Request, public_return
from factorylab.kernel.queue import PropensityRecord, SettleStatus
from factorylab.learners.router import Sample
from factorylab.runtime.clockwork import deadline_ticks
from factorylab.runtime.feedback import composed_reward
from factorylab.runtime.routing import _KeyedLearner
from factorylab.runtime.shared import (
    CH_VERDICT,
    DEF_COMPOSED,
    DEF_VERDICT,
    NOOP,
    assembly_rewards,
    request_router_key,
)
from factorylab.settlement.vocabulary import COMMISSIONED_JUDGE_REFUSAL

#: Why no request may reach an adversarial contract: the adversarial minority is a
#: routing share the kernel caps (``_cap_adversarial``), never a hand to hire.
ADVERSARY_REFUSAL = ("an adversarial contract cannot be commissioned as a child: its "
                     "work reaches the world only through the capped adversarial share "
                     "of routing")

#: The learner-state tag of a requester's forwarded propensity on its child's handle.
REQUESTER_DECLARED = "requester-declared"


class CompositionMixin:
    """Contracts that carry their own description, and the composition built on them."""

    def _register(self, handle: str, prop: Any, *, predicted_effect: Any = None) -> None:
        """Register as governance does; a tool's registering decision is then held.

        Guarantees that when a tool is admitted from a producer decision that will
        settle on its judges' verdicts, that decision is held for the tool-use
        window (``_hold_for_tool_use``), so the uses the tool finds across lineages
        reach it through its own one settlement. A refused proposal raises before
        anything is held.
        """
        super()._register(handle, prop, predicted_effect=predicted_effect)
        if isinstance(prop, ToolProposal):
            self._hold_for_tool_use(handle, prop.id)

    def _hold_for_tool_use(self, handle: str, tool_id: str) -> None:
        """Open the bounded window in which other lineages' use of a tool credits its builder.

        Guarantees a hold only on a pending decision on the verdict channel (the
        decision that registered the tool, before its judges have settled it). The
        window closes after ``verdict_timeout_ticks + consequence_backstop_ticks``
        world ticks, or on the last event before the decision's tick cutoff,
        whichever comes first: past that cutoff the kernel has already credited
        its router neutrally, and a later settlement could train nothing (§I.b:
        reward must find "the exact decision (and the exact propensity) that
        produced it"). Any other registering decision (a judge's, a ballot's) is
        not held, and its tool's uses reach the builder's inbox only.
        """
        try:
            decision = self.queue.get(handle)
        except KeyError:
            return
        if decision.status is not SettleStatus.PENDING or decision.channel != CH_VERDICT:
            return
        hold = self.tool_holds.setdefault(handle, {
            "until": self.ticks_consumed + self.ev.verdict_timeout_ticks
            + self.ev.consequence_backstop_ticks, "tools": [], "scores": []})
        hold["tools"].append(tool_id)
        self.ledger.append({"kind": "credit.tool_hold", "handle": handle, "tool": tool_id,
                            "until_tick": hold["until"], "ts": self.clock.now_ns})

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
            emitters = [a.spec for aid, a in self.assemblies.items()
                        if aid not in self.retired_assemblies and aid != requester
                        and kind in a.spec.emits]
            if any("exposure" in assembly_rewards(s).values() for s in emitters):
                return ADVERSARY_REFUSAL
            return (COMMISSIONED_JUDGE_REFUSAL if emitters else
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
            # Defence in depth: an adversary is never on the menu, and were one
            # there, its mass would still be capped as on every other draw.
            return self._cap_adversarial(
                {a: (0.0 if a == NOOP else p / mass) for a, p in dist.items()})

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
            if "exposure" in assembly_rewards(self.assemblies[target].spec).values():
                return self._refuse_request(parent, item, ADVERSARY_REFUSAL)
            actor = self.queue.get(parent.handle).actor
            record = PropensityRecord((target,), (1.,), target, 0, actor, "parent-selected")
            sample = Sample((target,), (1.,), target, 0, actor, "parent-selected", ())
            cutoff = self.queue.deadline_tick(parent.handle)
        else:
            drawn = self._draw_executor(action_id, parent.handle, item.target)
            if isinstance(drawn, str):
                return self._refuse_request(parent, item, drawn)
            sample, snapshot = drawn
            target, actor, record = sample.chosen, sample.learner_id, self._propensity(sample)
            # A drawn child settles only after its requester (the collaboration
            # credit), so its router's cutoff reaches one verdict horizon, with its
            # ratio slack, past its parent's: a credit that arrives in time trains the
            # router once. Counted in world ticks (time audit T3).
            cutoff = self.queue.deadline_tick(parent.handle)
            if cutoff is not None:
                cutoff += deadline_ticks(self.ev.verdict_timeout_ticks, self.m.timing.min_ratio)
        # A child spends its parent's money: whatever the parent's remaining request
        # ceiling says, the ceiling never exceeds what the parent's own decision may
        # spend now, so a fresh executor cannot be bought compute the parent lacks.
        ceiling = min(ceiling, max(0, self._compute_available(parent.handle)))
        channels = self._return_channels(target)
        channel = next(iter(channels.values()))
        # A parent opened before the tick record keeps its wall deadline for its child.
        cutoff_kw = ({"deadline_tick": cutoff} if cutoff is not None
                     else {"deadline_ns": parent.deadline_ns})
        handle = self.queue.open(
            actor=actor, event_id=f"child-{parent.handle}", propensity=record,
            channel=channel, parent_handle=parent.handle,
            cost_ceiling=ceiling, return_channels=channels, **cutoff_kw)
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
        # The executor's contract, not only the requester's schema, binds its answer:
        # a producing kind's carries the declined trade (II.III.b), so the request
        # publishes the field even where the requester's schema is closed.
        spec = self.assemblies[target].spec
        schema = (with_counterfactual(item.outcome_schema)
                  if any(self._return_shape(spec, k) in self.PRODUCING_SHAPES
                         for k in spec.emits) else item.outcome_schema)
        req = Request(handle, item.description, {**item.inputs, "world": self._world_block()},
                      {}, schema, parent.deadline_ns, ceiling, parent.handle,
                      "a JSON object satisfying the outcome schema", channel, parent.handle,
                      propensity=dict(item.propensity) if forwarded else None,
                      propensity_chosen=item.chosen if forwarded else None)
        ret = self._run_child(parent, item, handle, target, sample, req)
        if item.target != "self":
            self._compose(parent.handle, handle, action_id, target, ret)
        return {"tool": f"request:{item.target}", "args": item.inputs,
                "result": {"outputs": public_return(ret.outputs), "status": ret.status,
                           "cost_micro": ret.cost}}, ret.cost


    # --- the collaboration credit (rulings §2, W4; ``feedback.composed_reward``) ------

    def _chain_lineages(self, handle: str) -> set[str]:
        """The lineages of the seats that own ``handle`` and every request ancestor of it.

        Guarantees the whole parent chain is walked, so self-dealing through an
        intermediary (A requests B, B requests A's lineage) is seen as self-dealing.
        """
        lineages: set[str] = set()
        cursor: str | None = handle
        while cursor is not None:
            owner = self.handle_to_assembly.get(cursor)
            if owner is not None:
                lineages.add(self.budget.lineage(owner))
            try:
                cursor = self.queue.get(cursor).parent_handle
            except KeyError:
                break
        return lineages

    def _compose(self, requester_handle: str, handle: str, requester: str, executor: str,
                 ret: Any) -> None:
        """Hold a consumed child's settlement for its requester's, or say why it earns none.

        Guarantees a child is credited only when a request router drew it, its return
        reached its requester ok (consumed), it settles on its judges' verdicts (a
        producer-shaped kind), and its executor's lineage is none of the lineages of
        its requester's whole request chain: a lineage requesting its own work,
        directly or through an intermediary, earns nothing extra
        (``composed_reward`` gives the reasons for each exclusion).
        """
        pend = self.pending.get(handle)
        if (ret.status != "ok" or pend is None or pend.evaluation
                or pend.channel != CH_VERDICT):
            return
        if self.budget.lineage(executor) in self._chain_lineages(requester_handle):
            self.ledger.append({"kind": "credit.withheld", "handle": handle,
                                "requester_handle": requester_handle,
                                "reason": "the executor shares a lineage with the request "
                                          "chain",
                                "ts": self.clock.now_ns})
            return
        pend.requester = requester_handle
        self.ledger.append({"kind": "credit.composed", "handle": handle,
                            "requester_handle": requester_handle, "ts": self.clock.now_ns})

    def _run_tool(self, action_id: str, handle: str, call: dict[str, Any], *,
                  slot: str = "tool:0") -> tuple[dict, int]:
        """Run a tool as the kernel does, and note a population tool used across lineages.

        Guarantees nothing about the call changes. A population tool that answered
        without an error is counted against the calling decision only when its
        builder's lineage is none of the lineages of the calling decision's whole
        request chain; that decision's settlement later credits the builder
        (``_credit_requested``). A lineage calling its own tool, directly or through
        an intermediary it requested, earns nothing extra.
        """
        result, cost = super()._run_tool(action_id, handle, call, slot=slot)
        tool_id = str(call.get("tool"))
        builder = self.tool_owner.get(tool_id)
        if (tool_id not in self.population_tools or builder is None
                or not isinstance(result, dict) or "error" in result):
            return result, cost
        chain = self._chain_lineages(handle) | {self.budget.lineage(action_id)}
        across = self.budget.lineage(builder) not in chain
        self.ledger.append({"kind": "tool.population_call", "tool": tool_id,
                            "handle": handle, "across_lineage": across,
                            "ts": self.clock.now_ns})
        if across:
            uses = self.tool_uses.setdefault(handle, {})
            uses[tool_id] = uses.get(tool_id, 0) + 1
        return result, cost

    def _settled(self, handle: str, settlement: dict[str, Any]) -> None:
        """The settlement hook: every settled decision credits what it composed, once.

        Guarantees one call per kernel settlement, from whatever path settled it (a
        verdict, a composed or evaluator reward, a policy ballot, a censoring), with
        the raw score of a priced settlement (``raw_scores``) and no score for any
        settlement that is not ``SETTLED``.
        """
        status = SettleStatus(settlement["status"])
        score = (self.raw_scores.get(handle, settlement["score"])
                 if status is SettleStatus.SETTLED else None)
        self._credit_requested(handle, score, settlement.get("definition_version"))

    def _credit_requested(self, handle: str, score: float | None,
                          definition: str | None = None) -> None:
        """Carry one settled decision's score to what it composed, once.

        Guarantees a held child's credit flows only from a requester that settled
        on a producer's verdict (``verdict-v1``) or as a composed return
        (``composed-v1``), at its raw (pre-penalty) ``score``: an antagonist's
        exposure score, a judge's evaluation reward or a forecast's Brier score is
        not a measure of whether composed work paid, so from those each held
        child's credit closes empty and it keeps its own verdict. A population tool
        is credited from any scored settlement of the calling decision on the
        builder's own scale (a zero consequence of 0.5: a verdict, a composed or
        evaluator reward, an exposure, a ballot's promise score), so a committee
        seat's tool use counts when its ballot is scored; a Brier forecast, whose
        uninformed score is 0.75, is on another scale and credits nothing. Each use
        adds the score, once, to the hold of the decision that registered the tool
        while that hold is open (``_hold_for_tool_use``); a tool whose registering
        decision is not held credits its builder's inbox only. Nothing is counted
        twice: a child's credit closes once, and a decision's tool uses are consumed
        by its one settlement.
        """
        from factorylab.runtime.routing import ZERO_CONSEQUENCE

        child_score = score if definition in (DEF_VERDICT, DEF_COMPOSED) else None
        for pend in self.pending.values():
            if pend.requester == handle and not pend.credit_closed:
                pend.credit, pend.credit_closed = child_score, True
        uses = self.tool_uses.pop(handle, None)
        if not uses or score is None or ZERO_CONSEQUENCE.get(definition or "") != 0.5:
            return
        for tool_id, calls in sorted(uses.items()):
            tool = self.population_tools.get(tool_id)
            builder = self.tool_owner.get(tool_id)
            if tool is None or builder is None:
                continue
            hold = self.tool_holds.get(tool.provenance)
            if hold is not None:
                hold["scores"].append(score)
                applied = "hold"
            elif self.queue.get(tool.provenance).status is SettleStatus.PENDING:
                applied = "unheld"  # a judge's or a ballot's registration
            else:
                applied = "late"
            seq = self.ledger.append({
                "kind": "credit.tool", "tool": tool_id, "builder": builder,
                "registered_by": tool.provenance, "caller_handle": handle, "calls": calls,
                "credit": score, "applied": applied, "ts": self.clock.now_ns})
            if applied == "unheld":
                self.outcomes.append(builder, handle=tool.provenance, evidence=seq, outcome={
                    "kind": "tool_use_credit", "tool": tool_id, "calls": calls,
                    "credit": round(score, 6)})

    def _settle_composed(self) -> None:
        """Settle every held decision whose signals are in (``composed_reward``).

        Guarantees a held decision settles once, on the equal mean of the signals
        that arrived: its judges' mean verdict, its requester's credit (a requested
        child) and the mean score of the other-lineage decisions that used a tool
        it registered (a builder). A requester's credit closes when the requester
        closed or past the consequence backstop; a tool hold closes at the end of
        its window (``_hold_for_tool_use``); every signal closes on the last event
        before the decision's own tick cutoff, so the settlement always reaches
        the router that drew it. Its verdict is waited for no longer than an
        ordinary return's (``verdict_timeout_ticks``). With no signal it settles
        censored, as an unjudged return does. A decision's population-tool uses are
        dropped once it closed without a priced score.
        """
        timeout = self.ev.verdict_timeout_ticks
        backstop = self.ev.consequence_backstop_ticks
        open_states = (SettleStatus.PENDING, SettleStatus.TIMED_OUT)
        for handle in [h for h in self.tool_uses
                       if self.queue.get(h).status not in open_states]:
            del self.tool_uses[handle]
        for handle in [h for h in self.tool_holds
                       if h not in self.pending
                       and self.queue.get(h).status is not SettleStatus.PENDING]:
            del self.tool_holds[handle]  # settled by another path; its window is void
        held = sorted((p for p in self.pending.values() if self._held(p)),
                      key=lambda p: p.handle)
        for pend in held:
            age = self._tick_age(pend)
            # The last event before the decision's tick cutoff (time audit T3).
            cutoff = self.queue.deadline_tick(pend.handle)
            due = (self.ticks_consumed + 1 >= cutoff if cutoff is not None
                   else self.clock.now_ns + self.tick_clock.interval_ns
                   >= self.queue.get(pend.handle).deadline_ns)
            if pend.requester is not None and not pend.credit_closed and (
                    due or self.queue.get(pend.requester).status not in open_states
                    or age > timeout + backstop):
                pend.credit_closed = True
            hold = self.tool_holds.get(pend.handle)
            tool_open = hold is not None and not due and self.ticks_consumed < hold["until"]
            if ((pend.requester is not None and not pend.credit_closed) or tool_open
                    or (not pend.verdicts and age <= timeout and not due)):
                continue
            del self.pending[pend.handle]
            self.tool_holds.pop(pend.handle, None)
            if self.queue.get(pend.handle).status not in open_states:
                continue
            verdict = fmean(v for _judge, v in pend.verdicts) if pend.verdicts else None
            use = fmean(hold["scores"]) if hold and hold["scores"] else None
            reward = composed_reward(verdict, pend.credit, use)
            credited = pend.credit is not None or use is not None
            self.ledger.append({"kind": "composed.settled", "handle": pend.handle,
                                "requester_handle": pend.requester, "verdict": verdict,
                                "credit": pend.credit, "tool_use_credit": use,
                                "tool_uses": len(hold["scores"]) if hold else 0,
                                "reward": reward, "ts": self.clock.now_ns})
            if reward is None:
                self.queue.settle(pend.handle, channel=pend.channel, score=0.0,
                                  status=SettleStatus.CENSORED,
                                  definition_version="censored-v1", sampling_ref=None)
                self.stats.censored += 1
                self.window.outcomes += 1
                self.window.censored += 1
                continue
            self._settle_priced(
                pend.handle, channel=pend.channel, score=reward,
                definition_version=DEF_COMPOSED if credited else DEF_VERDICT,
                sampling_ref=pend.verdicts[0][0] if pend.verdicts else None, cards="producer")
            if pend.verdicts:
                self.stats.verdicts += 1
            owner = self.handle_to_assembly.get(pend.handle)
            if credited and owner in self.assemblies:
                # The seat's own inbox, under its own handle: the credits its decision
                # carried and what it settled on. No requester or caller is named.
                self.outcomes.append(owner, handle=pend.handle, outcome={
                    "kind": "composed_settled",
                    "requester_score": None if pend.credit is None else round(pend.credit, 6),
                    "tool_use_score": None if use is None else round(use, 6),
                    "verdict": None if verdict is None else round(verdict, 6),
                    "reward": round(reward, 6)})
