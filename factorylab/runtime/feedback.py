"""Runtime feedback method group."""

from __future__ import annotations

import random
from dataclasses import dataclass
from typing import Any

from factorylab.kernel.events import Event, EventKind
from factorylab.kernel.queue import LearningReturn, SettleStatus
from factorylab.learners.base import BanditFeedback
from factorylab.runtime.cascade import CascadeGate, event_tier, release_threshold
from factorylab.runtime.routing import _KeyedLearner
from factorylab.runtime.shared import (
    CH_CONFORMITY,
    CH_CONSEQUENCE,
    CH_EXPOSURE,
    CH_FAST,
    DEF_CONFORMITY,
    DEF_EXPOSURE,
    DEF_META_CONSEQUENCE,
    _to_plain,
)
from factorylab.runtime.summary import _as_unit
from factorylab.settlement import (
    SEED_VOCABULARY,
    Forecast,
    WindowFacts,
    brier,
    open_forecast_decision,
)
from factorylab.settlement.vocabulary import RETURN_PAID_OFF


@dataclass
class PendingJudgement:
    handle: str  # decision awaiting a verdict (producer) or conformity (evaluator/meta)
    channel: str
    opened_at_event: int
    tier: int = 1


class FeedbackMixin:
    """Preserve runtime state and behavior for feedback operations."""

    def _cascade_arrival(self, ev: Event) -> Event | None:
        """Ledger every arrival and release before changing buffers or routing upward."""
        tier = event_tier(ev)
        gate = self.cascade.get(tier)
        rng = random.Random()
        rng.setstate(self.rng.getstate())
        if gate is None:
            gate = CascadeGate(
                release_threshold(
                    self.m.timing.min_ratio,
                    self.m.timing.jitter_fraction,
                    rng.random(),
                )
            )
        next_gate, released = gate.add(ev)
        self.ledger.append(
            {
                "kind": "cascade.arrival",
                "tier": tier,
                "event_id": ev.id,
                "threshold": gate.threshold,
                "ts": self.clock.now_ns,
            }
        )
        if released is not None:
            self.ledger.append(
                {
                    "kind": "cascade.release",
                    "tier": tier,
                    "event_id": ev.id,
                    "window": _to_plain(released.payload["window"]),
                    "ts": self.clock.now_ns,
                }
            )
        self.rng.setstate(rng.getstate())
        if next_gate is None:
            self.cascade.pop(tier, None)
        else:
            self.cascade[tier] = next_gate
        if released is not None:
            # The meta judges the window as a distribution (essay II.IV.c); its score
            # settles every handle in the window, so nobody gains by not being sampled.
            handles = list(released.payload["window"]["handles"])
            self.cascade_windows[handles[-1]] = handles[:-1]
        return released

    def _open_forecasts(
        self, evaluator_handle: str, evaluator_id: str, about: str, raw: Any
    ) -> None:
        if not isinstance(raw, list):
            return
        known = {p.id: p for p in SEED_VOCABULARY}
        for item in raw[: self.ev.max_forecasts_per_verdict]:
            if not isinstance(item, dict):
                continue
            pid = item.get("predicate")
            q = _as_unit(item.get("q"))
            params = item.get("params") if isinstance(item.get("params"), dict) else {}
            if not isinstance(pid, str) or pid not in known or q is None:
                continue
            horizon = params.get(known[pid].horizon_param, self.ev.forecast_horizon_events)
            if type(horizon) is not int or not 1 <= horizon <= 200:
                continue
            params = dict(params, **{known[pid].horizon_param: horizon})
            try:
                from factorylab.settlement.vocabulary import _validate_params

                _validate_params(pid, params)
                fh = open_forecast_decision(
                    self.queue,
                    evaluator_id=evaluator_id,
                    event_id=f"forecast-{evaluator_handle}",
                    q=q,
                    deadline_ns=self.clock.now_ns + (horizon + 2) * self.tick_clock.interval_ns * 4,
                    parent_handle=evaluator_handle,
                    now_event=self.n,
                    horizon=horizon,
                )
                self.book.seal(
                    Forecast(fh, evaluator_id, about, pid, params, q, self.n, self.n + horizon, "")
                )
            except (ValueError, KeyError):
                continue
            self.stats.forecasts_sealed += 1

    def _deliver_meta_verdict(self, ev: Event) -> None:
        """Only the first timely higher-tier judgement settles its original handle."""
        payload = ev.payload
        tier, about = payload["tier"], payload["about"]
        self.stats.meta_verdicts[tier] = self.stats.meta_verdicts.get(tier, 0) + 1
        pend = self.pending.get(about)
        if (
            pend is None
            or pend.channel != CH_CONFORMITY
            or tier <= pend.tier
            or self.n - pend.opened_at_event > self.ev.verdict_timeout_events
            or self.queue.get(about).status is not SettleStatus.PENDING
        ):
            return
        self._settle_priced(
            about,
            channel=CH_CONFORMITY,
            score=payload["score"],
            definition_version=DEF_CONFORMITY,
            sampling_ref=payload["by"],
            cards="meta" if pend.tier > 1 else "evaluator",
        )
        del self.pending[about]
        self.stats.conformities += 1
        for sibling in self.cascade_windows.pop(about, []):
            sib = self.pending.get(sibling)
            if (
                sib is None
                or self.n - sib.opened_at_event > self.ev.verdict_timeout_events
                or self.queue.get(sibling).status is not SettleStatus.PENDING
            ):
                continue
            # A sibling was never read by the meta: it settles at a declared share of the
            # representative's score, so attribution stays with the verdict that was judged.
            self.ledger.append({"kind": "cascade.sibling", "handle": sibling,
                                "representative": about, "share": self.ev.sibling_share,
                                "ts": self.clock.now_ns})
            self._settle_priced(
                sibling,
                channel=CH_CONFORMITY,
                score=payload["score"] * self.ev.sibling_share,
                definition_version=DEF_CONFORMITY,
                sampling_ref=payload["by"],
                cards="meta" if sib.tier > 1 else "evaluator",
            )
            del self.pending[sibling]
            self.stats.conformities += 1
        self.stats.max_settlement_latency_events = max(
            self.stats.max_settlement_latency_events, self.n - pend.opened_at_event
        )
        owner = self.handle_to_assembly.get(about)
        if owner is not None:
            for entry in self.memory.get(owner, ()):
                if entry["handle"] == about:
                    entry["verdict"] = payload["score"]

    def _facts_for(self, f: Forecast) -> WindowFacts | None:
        if f.made_at_event >= len(self.balance_at):
            return None
        start = f.made_at_event
        window_balances = self.balance_at[start : self.n + 1]
        return WindowFacts(
            balance_at_forecast=self.balance_at[start],
            balance_at_settlement=self.wallet.balance,
            min_balance_in_window=min(window_balances) if window_balances else self.wallet.balance,
            events=tuple(self.events_log[start + 1 : self.n + 1]),
        )

    def _standing_for(self, evaluator_id: str) -> dict[str, Any] | None:
        """A judge's own consequence standing: skill against the prevalence baseline, sample
        size, selection weight. Its own running score, private to it (v0.4 §1.6)."""
        st = self.standing.snapshot().get(evaluator_id)
        if not st or not st.get("n"):
            return None
        return {
            "skill_vs_baseline": round(float(st["skill"]), 4),
            "settled_forecasts": st["n"],
            "selection_weight": round(float(st["weight"]), 4),
        }

    def _deliver_consequence_to_memory(self, s: Any) -> None:
        """The reward line must reach the primitive that acted, not only its router (essay
        II.I.b: memory across rounds, reward attributable to the decision). A producer learns
        whether its return paid off; a judge learns whether the return it blessed paid off and
        how its verdict scored. Private local state, never public. Run 7 showed judges blessing
        inaction at 1.0 while their standing fell, because nothing ever told them."""
        if s.predicate_id != "return_paid_off":
            return
        producer = self.handle_to_assembly.get(s.about_handle)
        if producer is not None:
            for entry in self.memory.get(producer, ()):
                if entry["handle"] == s.about_handle:
                    entry["paid_off"] = s.y
        event_id = self.queue.get(s.handle).event_id
        prefix = "verdict-" if event_id.startswith("verdict-") else "self-"
        if not event_id.startswith(prefix):
            return
        forecaster_handle = event_id[len(prefix) :]
        forecaster = self.handle_to_assembly.get(forecaster_handle)
        if forecaster is None:
            return
        for entry in self.memory.get(forecaster, ()):
            if entry["handle"] == forecaster_handle:
                if prefix == "verdict-":
                    entry["judged_return_paid_off"] = s.y
                entry["your_payoff_brier"] = round(float(s.brier), 4)
                entry["baseline_brier"] = (
                    round(float(s.baseline_brier), 4) if s.baseline_brier is not None else None
                )

    def _settle_exposures(self, settled: list[Any]) -> None:
        """An antagonist wins only for a real, attributable failure of the judge.

        Exposure settles 1 when the evaluated verdict's mandatory payoff forecast
        on the antagonist's return scored worse than the prevalence baseline and
        the antagonist's own payoff forecast on that return beat it (essay
        II.III.b: the failures have to be real). Optional forecasts never count.
        Without both facts by the time nothing about the return is pending, it
        settles 0.
        """
        for s in settled:
            if (s.predicate_id != RETURN_PAID_OFF.id or s.about_handle not in self.pending_exposure
                    or s.brier is None or s.baseline_brier is None):
                continue
            evidence = self.exposure_evidence.setdefault(
                s.about_handle, {"judge_failed": False, "self_beat": False}
            )
            if s.evaluator_id == self.handle_to_assembly.get(s.about_handle):
                evidence["self_beat"] |= s.brier > s.baseline_brier
            else:
                evidence["judge_failed"] |= s.brier < s.baseline_brier
            if evidence["judge_failed"] and evidence["self_beat"]:
                self._settle_exposure(s.about_handle, 1.0)
        waiting = {f.about_handle for f in self.book.pending()}
        stale = [
            h
            for h, o in self.pending_exposure.items()
            if self.n - o > self.ev.verdict_timeout_events and h not in waiting
        ]
        for h in stale:
            self._settle_exposure(h, 0.0)

    def _settle_exposure(self, handle: str, score: float) -> None:
        evidence = self.exposure_evidence.pop(
            handle, {"judge_failed": False, "self_beat": False}
        )
        if self.queue.get(handle).status not in (SettleStatus.PENDING, SettleStatus.TIMED_OUT):
            self.pending_exposure.pop(handle, None)
            return
        self.ledger.append({"kind": "exposure.settled", "handle": handle, "score": score,
                            **evidence, "ts": self.clock.now_ns})
        # Exposure is priced like every other channel: the antagonist's cards apply.
        self._settle_priced(
            handle,
            channel=CH_EXPOSURE,
            score=score,
            definition_version=DEF_EXPOSURE,
            sampling_ref=None,
            cards="antagonist",
        )
        self.pending_exposure.pop(handle, None)
        self.stats.exposures_settled += 1
        self.window.exposures_settled += 1
        if score > 0:
            self.stats.exposures_won += 1
            self.window.exposures_won += 1

    def _count_consequence(self, assembly_id: str | None) -> None:
        """One settled consequence delivered to an assembly ends one of its novelty trials;
        a trial beyond the base allowance spends the window's learning-death grant."""
        if assembly_id is None:
            return
        delivered = self.stats.consequences_by_assembly.get(assembly_id, 0)
        if delivered >= self.m.novelty.trials and self._novelty_grant_open(assembly_id):
            self.novelty_grant["consumed"].append(assembly_id)
            self.ledger.append({"kind": "novelty.grant_consumed", "assembly": assembly_id,
                                "window": self.stats.reserve_windows, "ts": self.clock.now_ns})
        self.stats.consequences_by_assembly[assembly_id] = delivered + 1

    def _settle_meta_consequence(
        self, meta_handle: str, conformity: float, y: int, forecast_handle: str
    ) -> None:
        """A top meta's conformity is a probability that the verdict was right; it is graded
        by Brier against whether the verdict's payoff forecast beat the baseline."""
        if self.queue.get(meta_handle).status not in (
            SettleStatus.PENDING, SettleStatus.TIMED_OUT,
        ):
            return
        score = brier(conformity, y)
        self.ledger.append({"kind": "meta.consequence", "handle": meta_handle,
                            "conformity": conformity, "y": y, "score": score,
                            "forecast_handle": forecast_handle, "ts": self.clock.now_ns})
        self._settle_priced(
            meta_handle,
            channel=CH_FAST,
            score=score,
            definition_version=DEF_META_CONSEQUENCE,
            sampling_ref=forecast_handle,
            cards="meta",
        )
        self.stats.fast_settlements += 1
        self._count_consequence(self.handle_to_assembly.get(meta_handle))

    def _settle_due_forecasts(self) -> None:
        for payoff in self.consequences.resolve(self.n):
            try:
                top_level = self.queue.get(payoff.handle).parent_handle is None
            except KeyError:
                top_level = True
            if top_level:  # continuations and children are not trials (A13)
                self._count_consequence(self.handle_to_assembly.get(payoff.handle))
        pending = {f.handle: f for f in self.book.pending()}
        settled = self.settler.settle_due(self.n, self._facts_for)
        settled.extend(self.settler.settle_consequences(self.consequences.payoff))
        self._settle_exposures(settled)
        backstop = self.ev.consequence_backstop_events
        for judge_handle in [h for h, (_y, at, _f) in self.verdict_outcomes.items()
                             if self.n - at > backstop]:
            del self.verdict_outcomes[judge_handle]
        for s in settled:
            forecast = pending[s.handle]
            if s.predicate_id == RETURN_PAID_OFF.id and s.brier is not None:
                event_id = self.queue.get(s.handle).event_id
                if event_id.startswith("verdict-"):
                    self._count_consequence(s.evaluator_id)
                    judge_handle = event_id[len("verdict-"):]
                    y = int(s.brier >= s.baseline_brier)
                    for meta_handle, conformity in self.pending_meta.pop(judge_handle, []):
                        self._settle_meta_consequence(meta_handle, conformity, y, s.handle)
                    self.verdict_outcomes[judge_handle] = (y, self.n, s.handle)
            self.cadence.record(
                handle=s.handle,
                predicate_id=s.predicate_id,
                opened_event=forecast.made_at_event,
                settled_event=self.n,
                opened_ns=self.queue.get(s.handle).opened_ns,
                settled_ns=self.clock.now_ns,
                status=str(s.status),
            )
            self.stats.forecasts_settled += 1
            self.window.outcomes += 1
            self.window.censored += int(s.status is SettleStatus.CENSORED)
            if s.predicate_id == "return_paid_off" and s.status is SettleStatus.SETTLED:
                self.window.consequences_settled += 1
                self.window.consequences_paid_off += int(s.y == 1)
            self._emit(
                EventKind.FORECAST_SETTLED,
                {
                    "handle": s.handle,
                    "evaluator_id": s.evaluator_id,
                    "predicate": s.predicate_id,
                    "y": s.y,
                    "brier": s.brier,
                    "status": str(s.status),
                    "marked": s.marked,
                },
            )
            if s.brier is None:
                continue
            self._deliver_consequence_to_memory(s)
            self.last_closure_ns = max(self.clock.now_ns, self.last_closure_ns + 1)
            self.timing.record_closure("leaf", self.last_closure_ns)
            self.buffer.add(
                LearningReturn(
                    s.handle, CH_CONSEQUENCE, float(s.brier), "brier-v1", SettleStatus.SETTLED, None
                ),
                self.clock.now_ns,
            )
            released = self.buffer.release()
            if released is not None:
                self.stats.upward_releases += 1
                self.ledger.append(
                    {"kind": "upward.release", "summary": released, "ts": self.clock.now_ns}
                )

    def _sampling_actuator(self) -> None:
        """The live sampling-rate actuator (essay II.IV.b: increase the sampling rate).

        When the evaluator role's verdict mean rises while its payoff skill falls
        over the last ``immune.k`` closed windows (the offline overfitting
        divergence), the consequence mix in evaluator selection rises by
        ``evaluation.sampling_step`` for the next window, capped at
        ``evaluation.sampling_cap``; without divergence it steps back toward the
        manifest's ``consequence_share``. Every change is a ledger item.
        """
        from factorylab.versioning.versions import slope

        values = self.stats.last_window_values
        self.sampling_history.append({
            "window": self.stats.reserve_windows - 1,
            "verdict": values.get("verdict_mean"),
            "consequence": values.get("forecast_skill"),
        })
        k = self.m.immune.k
        del self.sampling_history[:-k]
        base, step, cap = self.ev.consequence_share, self.ev.sampling_step, self.ev.sampling_cap
        before = self.consequence_mix
        verdict_slope = outcome_slope = None
        if len(self.sampling_history) == k:
            verdict_slope = slope([w["verdict"] for w in self.sampling_history])
            outcome_slope = slope([w["consequence"] for w in self.sampling_history])
        diverging = (verdict_slope is not None and outcome_slope is not None
                     and verdict_slope > 0 and outcome_slope < 0)
        after = min(cap, before + step) if diverging else max(base, before - step)
        after = round(after, 12)
        if after == before:
            return
        self.ledger.append({
            "kind": "sampling.raise" if after > before else "sampling.lower",
            "window": self.stats.reserve_windows - 1, "verdict_slope": verdict_slope,
            "outcome_slope": outcome_slope, "mix_before": before, "mix_after": after,
            "ts": self.clock.now_ns,
        })
        self.consequence_mix = after

    def _censor_stale_judgements(self) -> None:
        stale = [
            p
            for p in self.pending.values()
            if self.n - p.opened_at_event > self.ev.verdict_timeout_events
        ]
        for p in stale:
            if self.queue.get(p.handle).status is SettleStatus.PENDING:
                self.queue.settle(
                    p.handle,
                    channel=p.channel,
                    score=0.0,
                    status=SettleStatus.CENSORED,
                    definition_version="censored-v1",
                    sampling_ref=None,
                )
                self.stats.censored += 1
                self.window.outcomes += 1
                self.window.censored += 1
            del self.pending[p.handle]

    def _close_assembly_rounds(self) -> None:
        """Close every assembly round whose decision now has an outcome (A10).

        The evidence is the same thin score the router receives: the first
        settlement or timeout on the handle. A round is closed once, whatever
        opened the decision — a router, a parent's child request, a continuation —
        so nothing is left open for a decision that will never be scored again.
        """
        for handle in list(self.assembly_rounds):
            decision = self.queue.get(handle)
            if decision.status is SettleStatus.PENDING:
                continue
            scored = next((lr for lr in self.queue.history(handle)
                           if lr.status in (SettleStatus.SETTLED, SettleStatus.TIMED_OUT)), None)
            if scored is None:
                self._close_assembly_round(handle, None)  # censored: no evidence
                continue
            reward = (min(1.0, max(0.0, float(scored.score)))
                      if scored.status is SettleStatus.SETTLED else 0.0)
            self._close_assembly_round(handle, reward)

    def _close_assembly_round(self, handle: str, reward: float | None) -> None:
        """Train an assembly's own learner from the reward that settled its decision (A10).

        The reward is the same thin score the router receives; what differs is the
        distribution it is attributed to. The router's record prices the choice of
        who acted; this one prices what the actor chose to do, over the action set
        the actor declared. A censored decision closes its round without evidence.
        """
        assembly_id = self.assembly_rounds.pop(handle, None)
        if assembly_id is None:
            return
        learner = self.assembly_learners.get(assembly_id)
        if learner is None:
            return
        declared = self.queue.declared_propensity(handle)
        if reward is None or declared is None:
            try:
                learner.discard_for(handle)
            except KeyError:
                pass
            return
        index = declared.action_ids.index(declared.chosen)
        try:
            learner.update_for(
                handle, BanditFeedback(declared.chosen, reward, declared.probs[index])
            )
        except (KeyError, ValueError, RuntimeError, TypeError, AssertionError) as exc:
            self.ledger.append({"kind": "propensity.unlearned", "handle": handle,
                                "assembly_id": assembly_id, "reason": str(exc)[:200],
                                "ts": self.clock.now_ns})
            return
        self.ledger.append({"kind": "propensity.learned", "handle": handle,
                            "assembly_id": assembly_id, "action": declared.chosen,
                            "propensity": declared.probs[index], "reward": reward,
                            "ts": self.clock.now_ns})

    def _deliver_returns(self) -> None:
        self._close_assembly_rounds()
        for state in self._all_router_states() + list(self.retired_routers.values()):
            lid = state.learner.id
            returns = self.queue.returns_for(lid)
            for lr in returns[self.delivered_seen.get(lid, 0) :]:
                if lr.status not in (SettleStatus.SETTLED, SettleStatus.TIMED_OUT):
                    if isinstance(state.learner, _KeyedLearner):
                        key = self.snapshot_keys.pop(lr.handle, None)
                        if key is not None:
                            state.learner.inner.discard_for(key)
                    continue  # censored or inapplicable: no evidence, no update
                decision = self.queue.get(lr.handle)
                prop = decision.propensity
                idx = prop.action_ids.index(prop.chosen)
                reward = (
                    min(1.0, max(0.0, float(lr.score)))
                    if lr.status is SettleStatus.SETTLED
                    else 0.0
                )
                fb = BanditFeedback(prop.chosen, reward, prop.probs[idx])
                learner = state.learner
                if isinstance(learner, _KeyedLearner):
                    key = self.snapshot_keys.pop(lr.handle, None)
                    if key is not None:
                        learner.inner.update_for(key, fb)
                elif set(prop.action_ids) <= set(state.universe):
                    learner.update(fb)
            self.delivered_seen[lid] = len(returns)
            if lid in self.retired_routers and not self.queue.outstanding(lid):
                self.queue.retire_actor(lid)
                self.ledger.append({"kind": "router.drained", "learner_id": lid})
                del self.retired_routers[lid]
