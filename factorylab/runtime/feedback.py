"""Runtime feedback method group."""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from statistics import fmean
from typing import Any

from factorylab.kernel.events import Event, EventKind
from factorylab.kernel.queue import LearningReturn, SettleStatus
from factorylab.kernel.wallet import Infeasible
from factorylab.learners.base import BanditFeedback
from factorylab.runtime.cascade import CascadeGate, event_tier, release_window_ns
from factorylab.runtime.grounded import (
    DEFAULT_TAKER_FEE_BPS,
    OPPORTUNITY_DEFINITION,
    declined_trade,
    latest_mids,
    opportunity_cost,
)
from factorylab.runtime.pricing import UNRESOLVED_PRICED
from factorylab.runtime.routing import _KeyedLearner
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
    _to_plain,
)
from factorylab.runtime.summary import _as_unit
from factorylab.settlement import (
    Forecast,
    WindowFacts,
    open_forecast_decision,
)
from factorylab.settlement.settle import PredicateForecast
from factorylab.settlement.vocabulary import (
    DECLINED_DEFINITION,
    RETURN_PAID_OFF,
)


def _priced(neutral: float | None, lr: LearningReturn | None) -> float | None:
    """The neutral credit of an unscored decision, less the price its settlement carries.

    Only a censored settlement under ``UNRESOLVED_PRICED`` carries one (its score is
    the penalty for a commitment its owner left avoidably unresolved); every other
    unscored decision keeps its neutral credit unchanged. The result stays in [0, 1],
    and no neutral estimate (nothing observed yet) stays None.
    """
    if (neutral is None or lr is None or lr.status is not SettleStatus.CENSORED
            or lr.definition_version != UNRESOLVED_PRICED):
        return neutral
    return min(1.0, max(0.0, neutral - float(lr.score)))


#: A judgement the kernel could not use: malformed, refused by the model, or
#: addressed to nothing this judgement may be about. The call is charged, and the
#: decision settles censored: a form check is never a grade (evaluations S2, P9).
CENSORED_JUDGEMENT = "judgement-censored-v1"
#: A decision whose tier grade and world outcome both failed to arrive.
EVALUATION_UNSCORED = "evaluation-unscored-v1"
#: The base rates a verdict is scored against, per kind of measured outcome, and
#: the base rate a meta's conformity is scored against (the evaluator
#: consequence scores it predicted).
VERDICT_BASE = "verdict:"
EVALUATION_BASE = "evaluation_consequence"
_CARDS_FOR_CHANNEL = {CH_VERDICT: "producer", CH_CONFORMITY: "evaluator",
                      CH_EXPOSURE: "antagonist"}


def consequence_score(brier: float, baseline_brier: float) -> float:
    """The world's grade of one prediction, centred on its base rate, in [0, 1].

    Guarantees ``0.5 + 0.5 * (brier - baseline_brier)``, where both Brier scores
    are higher-is-better (``settle.normative_brier``) and so in [0, 1]: the result
    is in [0, 1] with no clipping. It is an affine transform of the Brier score with
    a constant the prediction cannot move, so it is a proper scoring rule: the
    expected score is maximised by reporting one's true probability, whatever the
    base rate (a clip would reward shading a report toward it). A prediction that
    only repeats the base rate earns 0.5, one the world proved righter earns more,
    and one it proved wronger less (ruling R1: a judge the world proved wrong earns
    less).
    """
    return 0.5 + 0.5 * (brier - baseline_brier)


def evaluation_reward(grade: float | None, consequence: float | None) -> float | None:
    """An evaluator decision's reward: the equal mean of the signals that arrived.

    Guarantees a value in [0, 1] when either signal exists, that signal alone when
    only one does, and None (the decision settles censored) when neither does.

    Why the two combine this way. Essay II.III.b gives an evaluator two rewards and
    ranks neither. It is "graded from above, tier upon tier ... on how compliant
    its scoring was with the factory's input document", and "this cannot be the
    only reward mechanism": "a second reward signal is required", one that "scores
    realized consequence: a judgment of whether a given verdict predicted real
    downstream outcomes". The first alone lets "evaluator agents and productive
    agents ... collaborate in the direction of overfitting"; the second alone is
    "not only sparse but shrinking over time", and a bare hold has no world
    outcome at all. Each covers the other's blind spot, so neither may dominate:

    * Linear, not a product or a minimum. Robust contracts are linear (II.I.a,
      Carroll): a product would zero the compliance grade whenever the world
      scored zero, which is a threshold on the judge by another name.
    * Equal weights, fixed here rather than by the charter. The second signal
      "sits outside the factory's input entirely, fixed at the architect's
      Stackelberg move", so no card may weight it (ruling R1), and an architect
      who does not hold the payoff table has no ground for any other split.
    * One scale. A tier grade is a probability whose uninformed value is 0.5,
      and ``consequence_score`` is centred so the base rate's forecaster also
      earns 0.5. Neither channel can dominate the mean through its units.
    * Nothing imputed. "Missing facts never become performance": a verdict no
      tier read settles on the world's grade alone, and one the world never
      resolved settles on its compliance grade alone.
    """
    signals = [s for s in (grade, consequence) if s is not None]
    if not signals:
        return None
    return min(1.0, max(0.0, fmean(signals)))


@dataclass
class PendingJudgement:
    handle: str  # a decision awaiting verdicts (producer) or its two signals (evaluator)
    channel: str
    opened_at_event: int
    tier: int = 1
    # The world tick this judgement opened at. Its horizons (the verdict timeout,
    # the consequence backstop) count world ticks consumed, never internal events:
    # a busy tick is many events and still one tick (defect 1). A judgement
    # recorded without one is aged from the world's first tick, so it can never
    # wait forever.
    opened_at_tick: int | None = None
    # An evaluator decision only (ruling R1). ``q`` is its verdict or conformity,
    # which is also its prediction about ``about``: the return a judge judged, or
    # the evaluator decision a meta graded.
    about: str | None = None
    q: float | None = None
    evaluator_id: str | None = None
    # The first signal: the grades the tier above gave it while the grade window
    # was open, and the first grader's handle.
    grades: list = field(default_factory=list)
    graded_by: str | None = None
    grade_closed: bool = False
    # The second signal: the world's score of ``q`` (``consequence_score``), or
    # None once it is known the world will not produce one.
    consequence: float | None = None
    consequence_closed: bool = False

    @property
    def evaluation(self) -> bool:
        """Whether this is an evaluator decision waiting on its two signals."""
        return self.q is not None

    @property
    def grade(self) -> float | None:
        """The mean of the tier grades that arrived, or None."""
        return fmean(self.grades) if self.grades else None


class FeedbackMixin:
    """Preserve runtime state and behavior for feedback operations."""

    def _cascade_evidence_complete(self, ev: Event) -> bool:
        """Whether one arrival's evidence has finished: the return it judged has an outcome.

        A verdict whose subject is still pending is an opinion about an open
        question. It belongs to the window — it is named in the report — but it
        is not evidence yet, and the upward report is made of evidence (§6.C). A
        subject this runtime cannot address at all (a judgement of an event rather
        than a decision) is not held open by a fact that will never arrive.
        """
        about = ev.payload.get("about_handle") or ev.payload.get("about")
        try:
            return self.queue.get(about).status is not SettleStatus.PENDING
        except (KeyError, TypeError):
            return True

    def _cascade_arrival(self, ev: Event) -> Event | None:
        """Ledger every arrival and release before changing buffers or routing upward.

        Separation is time and completed evidence (§6.C). The window's duration
        is drawn once, from the runtime's own reproducible stream, with the
        jitter the manifest already precommits; arrivals never shorten it.
        """
        tier = event_tier(ev)
        gate = self.cascade.get(tier)
        rng = random.Random()
        rng.setstate(self.rng.getstate())
        if gate is None:
            gate = CascadeGate(
                release_window_ns(
                    self.m.timing.min_ratio,
                    self.m.timing.jitter_fraction,
                    rng.random(),
                    self.tick_clock.interval_ns,
                ),
                opened_ns=ev.ts_ns,
            )
        next_gate, released = gate.add(ev, complete=self._cascade_evidence_complete)
        self.ledger.append(
            {
                "kind": "cascade.arrival",
                "tier": tier,
                "event_id": ev.id,
                "window_ns": gate.window_ns,
                "opened_ns": gate.opened_ns,
                "elapsed_ns": gate.elapsed(ev.ts_ns),
                "ts": self.clock.now_ns,
            }
        )
        if released is not None:
            self.ledger.append(
                {
                    "kind": "cascade.release",
                    "tier": tier,
                    # The representative is the window's latest completed evidence,
                    # which is not always the arrival that closed the window.
                    "event_id": released.id,
                    "arrival_event_id": ev.id,
                    "window": _to_plain(released.payload["window"]),
                    "ts": self.clock.now_ns,
                }
            )
        self.rng.setstate(rng.getstate())
        if next_gate is None:
            self.cascade.pop(tier, None)
        else:
            self.cascade[tier] = next_gate
        # The meta reads the window as a distribution (essay II.IV.c) and grades the
        # representative it was released. The window's other verdicts were not read
        # and borrow no grade (evaluations U2): each settles on its own signals.
        return released

    def _open_forecasts(
        self, evaluator_handle: str, evaluator_id: str, about: str, raw: Any
    ) -> list[str]:
        if not isinstance(raw, list):
            return []
        opened = []
        known = {p.id: p for p in self.predicates.all()}
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

                _validate_params(pid, params, predicate=known[pid])
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
                definition = known[pid]
                forecast_type = PredicateForecast if definition.code is not None else Forecast
                population = {}
                if definition.code is not None:
                    from factorylab.runtime.observations import window_cursor

                    # Seal how much of the open window had already happened, so the
                    # claim is resolved over what follows it and not over its past.
                    population = {"predicate": definition,
                                  "window_cursor": window_cursor(self.window)}
                self.book.seal(forecast_type(
                    fh, evaluator_id, about, pid, params, q, self.n, self.n + horizon, "",
                    **population))
            except (ValueError, KeyError):
                continue
            self.stats.forecasts_sealed += 1
            opened.append(fh)
        return opened

    def _settle_forecast_returns(self) -> None:
        """Guarantees a forecast-shaped invocation settles once, on all its resolved predictions.

        It earns their mean, or settles censored (priced when it left one of them
        avoidably unresolved). A decision that timed out first settles too: its
        deadline is wall time while its forecasts' horizons count events, so an
        outage or a stalled loop can expire it before they come due, and the queue
        keeps a late settlement's right for exactly that. Its learners were credited
        once, neutrally, at the cutoff and are not trained again; the late
        settlement is what puts the charter's price on the decision's record.
        """
        from factorylab.cortex.registration import measured_role

        for handle, entry in list(self.forecast_returns.items()):
            forecasts = entry["handles"]
            if any(self.queue.get(f).status is SettleStatus.PENDING for f in forecasts):
                continue
            if self.queue.get(handle).status in (SettleStatus.PENDING, SettleStatus.TIMED_OUT):
                results = [self.queue.history(f)[-1] for f in forecasts]
                unresolved = tuple(entry.get("unresolved", ()))
                if unresolved:
                    # The seat left a commitment it accepted avoidably unresolved: the
                    # decision still has no observed score, but it is not a free
                    # censored neutral either. The charter prices it for its owner.
                    self._settle_priced(
                        handle, channel=CH_CONSEQUENCE, score=0.0,
                        definition_version="forecast-mean-v1", sampling_ref=None,
                        cards=measured_role(self.return_kinds[handle]),
                        unresolved=unresolved)
                elif not results or any(r.status is not SettleStatus.SETTLED for r in results):
                    self.queue.settle(handle, channel=CH_CONSEQUENCE, score=0.0,
                                      status=SettleStatus.CENSORED,
                                      definition_version="forecast-mean-v1", sampling_ref=None)
                else:
                    self._settle_priced(
                        handle, channel=CH_CONSEQUENCE, score=fmean(r.score for r in results),
                        definition_version="forecast-mean-v1", sampling_ref=None,
                        cards=measured_role(self.return_kinds[handle]))
            evidence = entry["results"]
            scored = bool(forecasts) and all(f in evidence for f in forecasts)
            # What the world made of this return's predictions is the fact a meta
            # that graded it predicted: centred on the predictions' own base rates.
            self._close_consequence(handle, consequence_score(
                fmean(evidence[f][0] for f in forecasts),
                fmean(evidence[f][1] for f in forecasts)) if scored else None)
            del self.forecast_returns[handle]

    def _deliver_meta_verdict(self, ev: Event) -> None:
        """A higher tier's grade reaches the evaluator decision it read, while it may.

        The grade is the first of that decision's two signals (``evaluation_reward``);
        grades that arrive while its grade window is open are averaged, and one that
        arrives later, from a tier that is not above it, or about anything that is not
        an evaluator decision waiting on its signals, grades nothing.
        """
        payload = ev.payload
        tier = payload["tier"]
        self.stats.meta_verdicts[tier] = self.stats.meta_verdicts.get(tier, 0) + 1
        self._grade_evaluation(payload["about"], payload["score"], by=payload["by"], tier=tier)

    def _grade_evaluation(self, about: str, score: float, *, by: str, tier: int) -> bool:
        """Record one tier grade on an evaluator decision; True if it counted.

        Essay II.III.b: evaluators are "graded from above, tier upon tier ... on how
        compliant [their] scoring was with the factory's input document". A grade
        counts only for an evaluator decision still waiting on it, from a tier above
        it, inside its grade window.
        """
        rec = self.pending.get(about)
        if (rec is None or not rec.evaluation or rec.grade_closed or tier <= rec.tier
                or self._tick_age(rec) > self.ev.verdict_timeout_ticks):
            return False
        rec.grades.append(float(score))
        rec.graded_by = rec.graded_by or by
        self.stats.conformities += 1
        self.ledger.append({"kind": "evaluator.meta_grade", "handle": about, "by": by,
                            "tier": tier, "grade": float(score), "ts": self.clock.now_ns})
        self._deliver_verdict_to_inbox(about, score, judge_handle=by)
        return True

    def _settle_declined(self, handle: str, channel: str, reason: str) -> bool:
        """Close one declined commission with no score, no price and no standing.

        A seat may decline paid judging work (§6.B): the call it made is its only
        cost. Declining is the seat's own choice, never a list the kernel keeps of
        what may be judged (evaluations S1). Nothing enters a standing, nothing
        enters a base rate, no card is blamed, and no money moves.
        """
        definition = DECLINED_DEFINITION
        try:
            status = self.queue.get(handle).status
        except KeyError:
            return False
        if status not in (SettleStatus.PENDING, SettleStatus.TIMED_OUT):
            return False
        self.ledger.append({"kind": "evaluation.declined", "handle": handle,
                            "channel": channel, "definition": definition,
                            "reason": reason, "ts": self.clock.now_ns})
        self.queue.settle(handle, channel=channel, score=0.0,
                          status=SettleStatus.INAPPLICABLE,
                          definition_version=definition, sampling_ref=None)
        owner = self.handle_to_assembly.get(handle) or self.outcomes.seat_of(handle)
        if owner is not None:
            self.outcomes.append(owner, handle=handle, evidence=handle,
                                 outcome={"status": "declined", "reason": reason})
        return True

    def _facts_for(self, f: Forecast) -> WindowFacts | None:
        if f.made_at_event >= len(self.balance_at):
            return None
        start = f.made_at_event
        window_balances = self.balance_at[start : self.n + 1]
        public = {}
        if isinstance(f, PredicateForecast):
            from factorylab.runtime.observations import window_facts_since

            # The same evidence surface observations read, restricted to what the
            # window accumulated after the claim was sealed: a fill that had already
            # happened resolves nothing. No private handles or attribution enter the
            # resolver. A bounded series that has discarded part of the sealed
            # interval supplies nothing at all, so the claim closes unscored rather
            # than resolving false against evidence the window no longer holds.
            since = window_facts_since(self.window, f.window_cursor)
            if since is None:
                self.ledger.append({"kind": "forecast.evidence_discarded", "handle": f.handle,
                                    "predicate": f.predicate_id, "window": self.window.index,
                                    "ts": self.clock.now_ns})
            public = {"public_window": since}
        return WindowFacts(
            balance_at_forecast=self.balance_at[start],
            balance_at_settlement=self.wallet.balance,
            min_balance_in_window=min(window_balances) if window_balances else self.wallet.balance,
            events=tuple(self.events_log[start + 1 : self.n + 1]),
            **public,
        )

    def _deliver_verdict_to_inbox(self, about: str, score: Any, *, judge_handle: str) -> None:
        """A verdict on a seat's return reaches that seat, whenever it lands (C1).

        The three-entry deque used to carry this and could only carry it while the
        return was still one of the last three. An inbox item is addressed to the
        handle, so a verdict that arrives after twenty other returns still finds
        the reasoning it is about.
        """
        owner = self.handle_to_assembly.get(about) or self.outcomes.seat_of(about)
        if owner is None:
            return self._undeliverable("verdict", about, "no seat owns that decision")
        self.outcomes.append(owner, handle=about, evidence=judge_handle,
                             outcome={"verdict": (round(float(score), 4)
                                                  if score is not None else None)})

    def _undeliverable(self, what: str, handle: str | None, why: str) -> None:
        """Record a consequence that reached nobody, rather than dropping it (R3-F)."""
        self.ledger.append({"kind": "outcome.undeliverable", "consequence": what,
                            "handle": handle, "reason": why, "ts": self.clock.now_ns})

    def _deliver_program_result_to_inbox(self, seat: str, handle: str, ret: Any) -> None:
        """A program seat's result is the lineage that registered it (R3-F).

        A program has no model to read its own inbox, so the seat that put it in
        the world is the one that must learn what it produced and what it cost.
        A program that is its own lineage root keeps the item itself, which is the
        same rule with nobody above it.
        """
        owner = self.budget.lineage(seat)
        if owner not in self.assemblies:
            owner = seat if seat in self.assemblies else None
        if owner is None:
            return self._undeliverable("program_result", handle, "no live owner")
        outputs = ret.outputs if isinstance(ret.outputs, dict) else {}
        self.outcomes.append(
            owner, handle=handle, evidence=f"program:{handle}",
            outcome={"kind": "program_result", "program": seat, "status": ret.status,
                     "cost_micro": ret.cost,
                     "emitted": self.return_kinds.get(handle),
                     "reason": str(outputs.get("reason"))[:200] if "reason" in outputs else None})

    def _address_fill_to_inbox(self, payload: dict[str, Any]) -> None:
        """A fill is the ordering seat's news, addressed to it with an id (R3-F).

        The order's own decision handle is on the lot table, so the fill reaches
        the seat that placed it rather than whoever the router happened to wake.
        A fill nobody with an open account ordered — the venue's own, a fill
        arriving before its intent is acknowledged — is a failed delivery and is
        ledgered as one by the inbox rather than addressed to a stranger.
        """
        order_id = str(payload.get("order_id", ""))
        orders = getattr(getattr(self.consequences, "table", None), "orders", ())
        handle = next((o.handle for o in orders if o.order_id == order_id), None)
        if handle is None:
            return self._undeliverable("fill", None, f"no decision owns order {order_id}")
        owner = self.handle_to_assembly.get(handle) or self.outcomes.seat_of(handle)
        if owner is None:
            return self._undeliverable("fill", handle, "no seat owns that decision")
        self.outcomes.append(
            owner, handle=handle, evidence=f"fill:{order_id}",
            outcome={"kind": "fill", "status": "settled", "order_id": order_id,
                     "coin": payload.get("coin"), "market": payload.get("market", "perp"),
                     "is_buy": payload.get("is_buy"), "size": str(payload.get("size")),
                     "px": str(payload.get("px")), "fee_usd": str(payload.get("fee_usd")),
                     "realized_usd": str(payload.get("realized_usd")),
                     "liquidation": bool(payload.get("liquidation", False))})

    def _deliver_consequence_to_inbox(self, s: Any) -> None:
        """A settled forecast reaches the seat that made it (R3-F)."""
        self._deliver_forecast_to_inbox(s)

    def _deliver_forecast_to_inbox(self, s: Any) -> None:
        """A settled non-payoff forecast reaches the seat that made it (R3-F).

        The claim rode on a decision — the forecast's parent handle — so the item
        is addressed there, beside whatever else that decision came to. Nothing is
        scored here; this is the delivery of a fact the seat had no other way to
        read (§3: "non-payoff forecasts miss the inbox").
        """
        try:
            parent = self.queue.get(s.handle).parent_handle
        except KeyError:
            parent = None
        handle = parent or s.handle
        owner = self.handle_to_assembly.get(handle) or self.outcomes.seat_of(handle)
        if owner is None:
            return self._undeliverable("forecast_settled", handle, "no seat owns that decision")
        self.outcomes.append(
            owner, handle=handle, evidence=s.handle,
            outcome={"kind": "forecast_settled", "predicate": s.predicate_id,
                     "resolved": s.y, "your_brier": round(float(s.brier), 4),
                     "baseline_brier": (round(float(s.baseline_brier), 4)
                                        if s.baseline_brier is not None else None),
                     "status": str(s.status)})

    def _count_consequence(self, assembly_id: str | None) -> None:
        """One observed consequence delivered to an assembly ends one of its novelty trials;
        a trial beyond the base allowance spends the window's learning-death grant.

        Callers count resolved evidence only: a censored payoff or an unmeasured
        commission told nobody anything about the seat, so it spends no trial.
        """
        if assembly_id is None:
            return
        delivered = self.stats.consequences_by_assembly.get(assembly_id, 0)
        if delivered >= self.m.novelty.trials and self._novelty_grant_open(assembly_id):
            self.novelty_grant["consumed"].append(assembly_id)
            self.ledger.append({"kind": "novelty.grant_consumed", "assembly": assembly_id,
                                "window": self.stats.reserve_windows, "ts": self.clock.now_ns})
        self.stats.consequences_by_assembly[assembly_id] = delivered + 1

    def _credit_consequence(self, payoff: Any) -> None:
        """A settled return's net proceeds are its owner's, in both directions (C10).

        They are venue money: the P&L settled on the venue account, which never
        passes through the compute wallet (C5). So they are the owner's claim on
        venue custody (``BudgetBook.claim_venue``), not compute entitlement drawn
        from, or paid into, the pool that funds every seat's thinking (defect 6).
        A marked outcome is an estimate at the backstop, not settled money, and
        moves nothing here: what its lots realise later is booked to the owner as
        a late consequence (``_settle_late``).
        """
        if payoff.censored is not None:
            return self._address_unknown_outcome(payoff)
        owner = self.handle_to_assembly.get(payoff.handle) or self.outcomes.seat_of(payoff.handle)
        if owner is None:
            self._undeliverable("return_paid_off", payoff.handle, "no seat owns that decision")
        if owner is not None:
            # The seat that decided it is told what it came to, marked or settled,
            # with the money attached (C1). A marked outcome says so, so an estimate
            # at the backstop is never read as realised cash.
            #
            # And it is told in parts, not as one number. GPT-6 Pro's third
            # reading §2: "the request line and the reward line stop learning
            # from one number". A useful consequence record reads like "incurred
            # 920 microUSD of provider cost, received 1,200 microUSDC of funding
            # at Hyperliquid, position still open, committed hypothesis not
            # settled" -- four facts in different units and different custodies,
            # which a single net destroys. ``net_micro`` stays because it is
            # itself a fact: the movement in this seat's venue claim.
            venue_delta = self.venue_deltas.pop(payoff.handle, {})
            self.outcomes.append(
                owner, handle=payoff.handle, delta_micro=payoff.net_micro,
                evidence=payoff.handle,
                outcome={"return_paid_off": payoff.y, "net_micro": payoff.net_micro,
                         "provider_cost_micro": payoff.cost_micro,
                         "venue_delta_micro": venue_delta,
                         "position_open": self._position_open(payoff),
                         "commitment_settled": not payoff.marked,
                         "cost_micro": payoff.cost_micro, "earned_micro": payoff.earned_micro,
                         "marked": payoff.marked, "liquidated": payoff.liquidated})
        if payoff.marked or owner is None or owner not in self.assemblies:
            return
        self._book_consequence(owner, payoff.net_micro, "return_paid_off", payoff.handle)

    def _address_unknown_outcome(self, payoff: Any) -> None:
        """Tell the owner that its return's consequence is unknown, and why (R4-C).

        The OUTCOME CONTRACT has three answers, and this is the third: the
        necessary observation is unavailable. The venue would not say whether one
        order filled, so no payoff is claimed in either direction and nothing is
        scored. Only that order's portion is unknown (defect 10): what the
        return's observed orders realised is booked to the owner exactly as a
        settled outcome's would be, and a marked one books nothing until it is
        real. The venue's last answer rides with the item so the seat reads the
        fact rather than a silence, and if the missing fill is observed later its
        money reaches the same seat through ``_settle_late``.
        """
        owner = self.handle_to_assembly.get(payoff.handle) or self.outcomes.seat_of(payoff.handle)
        if owner is None:
            return self._undeliverable("return_paid_off", payoff.handle,
                                       "no seat owns that decision")
        self.outcomes.append(
            owner, handle=payoff.handle, delta_micro=0 if payoff.marked else payoff.net_micro,
            evidence=payoff.handle,
            outcome={"outcome": "unknown", "reason": payoff.censored,
                     "return_paid_off": None,
                     "known_net_micro": payoff.net_micro,
                     "provider_cost_micro": payoff.cost_micro,
                     "cost_micro": payoff.cost_micro,
                     "earned_micro": payoff.earned_micro,
                     "venue_answer": self._venue_last_answer(payoff.handle),
                     "venue_delta_micro": self.venue_deltas.pop(payoff.handle, {}),
                     "position_open": True, "commitment_settled": False,
                     "marked": payoff.marked, "liquidated": payoff.liquidated})
        if not payoff.marked and owner in self.assemblies:
            self._book_consequence(owner, payoff.net_micro, "return_known_portion",
                                   payoff.handle)

    def _venue_last_answer(self, handle: str) -> dict | None:
        """The last thing the venue said about this return's unresolved intent."""
        for client_id, intent in getattr(self, "order_intents", {}).items():
            if intent.get("handle") == handle and intent.get("unresolved"):
                return {"client_id": client_id, "coin": intent["args"].get("coin"),
                        "operation": intent["operation"],
                        "polls": int(intent.get("polls", 0)),
                        "result": dict(intent["result"])}
        return None

    def _position_open(self, payoff: Any) -> bool:
        """Whether this decision still has exposure at the venue when its outcome is fixed.

        The consequence book knows how many lots the return opened and how many
        of them are closed; a marked outcome is one whose position outlived its
        evaluation horizon. Either way the seat is told plainly, rather than
        being left to infer an open position from a number that looks final.
        """
        if payoff.marked:
            return True
        try:
            account = self.consequences.table.account(payoff.handle)
        except (AttributeError, KeyError):
            return False
        return account.opened_lots > account.closed_lots

    def _book_consequence(self, owner: str, micro: int, reason: str,
                          handle: str | None = None) -> None:
        """Book a return's realised venue P&L to its owner as a claim on its custody.

        A Polymarket share is a claim on the polymarket pot and never on the venue:
        financing converts only venue claims, so a profit made on Polygon cannot be
        withdrawn from Hyperliquid money (runtime/polymarket.py).
        """
        if getattr(self, "polymarket", None) is not None and handle is not None:
            from factorylab.runtime.polymarket import claim_share

            micro -= claim_share(self, owner, handle, micro, reason)
        if micro:
            self.budget.claim_venue(owner, micro, reason)

    def _settle_late(self) -> None:
        """Money realised after an outcome was fixed still belongs to the return's owner.

        A position marked at the backstop and closed later is settled by the venue
        on its own account; the opener's venue claim moves by the realised result,
        ledgered as a late consequence. The learning score of the marked outcome
        stays as it was; only the claim moves.
        """
        for handle, micro in self.consequences.settle_late(self.n).items():
            owner = self.handle_to_assembly.get(handle) or self.outcomes.seat_of(handle)
            if owner is None or owner not in self.assemblies:
                self._undeliverable("late_realization", handle, "no live seat owns that decision")
                continue
            self._book_consequence(owner, micro, "late_consequence", handle)
            # A realisation after the outcome was fixed is still this seat's news (C1).
            self.outcomes.append(owner, handle=handle, delta_micro=micro, evidence=handle,
                                 outcome={"late_realization_micro": micro})

    def _book_income(self, item: dict) -> None:
        """Verified x402 income is new money: it arrives in ``base_reserve`` and is the
        owning seat's (C11, C10).

        A paid call settles USDC to the reserve address on Base, so the money
        itself lands in the ``base_reserve`` custody account -- never in
        OpenRouter or Venice credit, which the x402 route does not touch. The
        compute wallet rises by the same amount as *authority*: new assets back
        new spending permission, and the seat that owns the service's program is
        credited from that new money rather than from the pool, so an empty pool
        still pays the seller.

        What reaches here has already been verified: the treasury books a spool
        receipt as a claim and only a confirmed chain read promotes it to income
        (``Treasury.verify_receipt``). A service whose program has no live owner
        (retired, or seeded without one) leaves the receipt in the pool. The
        receipt is also the economic consequence of the return that registered
        the service, while that outcome is open.

        Income from outside can arrive elsewhere: a vault leader's commission is paid
        by the venue into the leader's own perps account. Such an item names its
        ``custody`` and the ``seat`` it belongs to; the asset is already where the
        venue put it, so no pot is told it arrived, and the wallet's authority rises
        exactly as it does for a paid call. It is income, never financing (that is
        principal converted) and never venue P&L (that is the factory's own trading).
        """
        program = item.get("program") or item.get("service")
        service = item.get("service")
        custody = item.get("custody") or "base_reserve"
        owner = item["seat"] if "seat" in item else self.tool_owner.get(program)
        micro = item.get("micro")
        if type(micro) is not int or micro <= 0:
            return
        try:
            self.wallet.settle(micro, f"income:{service}:{item.get('tx')}", "income")
        except Infeasible:
            # A dead wallet books nothing; the receipt stays counted by the treasury.
            self.ledger.append({"kind": "income.unbooked", "service": service, "micro": micro,
                                "tx": item.get("tx"), "reason": "wallet is dead"})
            return
        # The asset itself is USDC at the reserve address. A scripted rail has no
        # chain to read it back from, so it is credited to its pot here; a live
        # rail reads the reserve's own balance and must not be told twice.
        receive = getattr(getattr(self.treasury, "rail", None), "receive_income", None)
        if receive is not None and custody == "base_reserve":
            receive(micro)
        self.ledger.append({"kind": "income.custody", "service": service, "micro": micro,
                            "tx": item.get("tx"), "custody": custody,
                            "asset": item.get("asset") or "USDC",
                            "chain": item.get("chain") or "base"})
        if owner in self.assemblies:
            self.budget.earn(owner, micro, f"income.earned:{service}")
        self.consequences.income(service, micro, self.n)

    def _collect_income(self) -> None:
        """Book the seller's spooled receipts and credit each to its owning seat.

        ``Treasury.tick`` collects the same spool and would book nothing new after
        this: the consumed offset is part of the treasury snapshot.
        """
        for item in self.treasury.collect_income():
            self._book_income(item)
        # A spool row is a claim; only a confirmed chain read makes it income.
        for item in self.treasury.verify_receipts():
            self._book_income(item)
        # A vault leader's commission, read from the venue's own ledger (II.IV).
        collect = getattr(self, "_collect_vault_income", None)
        if collect is not None:
            collect()
        self._classify_financing()

    def _classify_financing(self) -> None:
        """Give converted capital to whoever earned it: the seat's own venue profit first.

        Guarantees each confirmed conversion is classified once. Up to the
        converting seat's positive venue claim becomes that seat's entitlement and
        leaves its claim (its own profit, now thinking money); the rest came from
        shared principal and stays in the unallocated pool the wallet already holds.
        """
        collect = getattr(self.treasury, "collect_financing", None)
        for item in (collect() if collect else []):
            handle = item.get("handle")
            seat = (self.handle_to_assembly.get(handle) or self.outcomes.seat_of(handle)
                    if handle else None)
            own = 0
            if seat is not None:
                own = max(0, min(int(item["micro"]),
                                 self.budget.venue_claims().get(seat, 0)))
                if own:
                    own = self.budget.credit(seat, own, "financing:own venue profit")
                    self.budget.claim_venue(seat, -own, "converted to compute")
            self.ledger.append({"kind": "financing.classified",
                                "transfer_id": item.get("transfer_id"), "seat": seat,
                                "micro": item["micro"], "to_seat_micro": own,
                                "to_pool_micro": int(item["micro"]) - own,
                                "ts": self.clock.now_ns})

    def _settle_due_forecasts(self) -> None:
        self._settle_late()
        for payoff in self.consequences.resolve(self.n):
            self._credit_consequence(payoff)
            try:
                top_level = self.queue.get(payoff.handle).parent_handle is None
            except KeyError:
                top_level = True
            # Continuations and children are not trials, and neither is an outcome
            # nobody observed: an unknown consequence ends no novelty trial.
            if top_level and payoff.censored is None:
                self._count_consequence(self.handle_to_assembly.get(payoff.handle))
            if payoff.censored is None and self._acted(payoff.handle):
                # The world's paid-off rate is read from every return that acted on
                # it, whoever forecast it (the seed observation consequence_paid_off_rate).
                self.window.consequences_settled += 1
                self.window.consequences_paid_off += int(payoff.y == 1)
        settled = self.settler.settle_due(self.n, self._facts_for)
        for result in settled:
            parent = self.queue.get(result.handle).parent_handle
            if parent in self.forecast_returns and result.brier is not None:
                self.forecast_returns[parent]["results"][result.handle] = (
                    result.brier, result.baseline_brier)
            elif (parent in self.forecast_returns and result.status is SettleStatus.CENSORED
                  and result.excluded is None):
                # An accepted commitment that came due unresolved without a documented
                # exclusion: exactly what ``avoidably_unresolved_share`` counts against
                # its owner, so its owner's decision is priced for it.
                self.forecast_returns[parent].setdefault("unresolved", []).append(
                    result.handle)
        self._settle_forecast_returns()
        self._settle_evaluations()
        for s in settled:
            # The cadence's clock is world ticks consumed (defect 1).
            self.cadence.record(
                handle=s.handle,
                predicate_id=s.predicate_id,
                opened_event=self.cadence.opened_at(s.handle, self.ticks_consumed),
                settled_event=self.ticks_consumed,
                opened_ns=self.queue.get(s.handle).opened_ns,
                settled_ns=self.clock.now_ns,
                status=str(s.status),
            )
            self.stats.forecasts_settled += 1
            self.window.outcomes += 1
            self.window.censored += int(s.status is SettleStatus.CENSORED)
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
            self._deliver_consequence_to_inbox(s)

    # -- the reward chain (ruling R1; essay II.III.b) -------------------------------

    def _settle_arrived_verdicts(self) -> None:
        """Every judged return that received verdicts this event settles on their mean.

        Guarantees a producer decision's reward is its judges' verdict (essay
        II.III.b: productive agents "learn from evaluator agents through the coupling
        of scoring and propensity"): the mean of every verdict that arrived while it
        was waiting, less its card penalty, settled once. One judge reads a return
        today; a return read by several settles on all of them, and none of them
        alone. Verdicts are collected while an event is routed and settled when its
        routing is done, so every judge a draw woke on the return is counted.
        """
        arrived, self.arrived_verdicts = self.arrived_verdicts, {}
        for about, verdicts in arrived.items():
            pend = self.pending.get(about)
            try:
                decision = self.queue.get(about)
            except KeyError:
                continue
            if (pend is None or pend.evaluation or decision.status is not SettleStatus.PENDING
                    or decision.channel != CH_VERDICT):
                continue
            score = fmean(v for _judge, v in verdicts)
            if len(verdicts) > 1:
                self.ledger.append({"kind": "verdict.mean", "handle": about,
                                    "judges": [j for j, _v in verdicts],
                                    "verdicts": [v for _j, v in verdicts], "score": score,
                                    "ts": self.clock.now_ns})
            self._settle_priced(about, channel=decision.channel, score=score,
                                definition_version=DEF_VERDICT, sampling_ref=verdicts[0][0],
                                cards="producer")
            self.stats.verdicts += 1
            self.stats.max_settlement_latency_events = max(
                self.stats.max_settlement_latency_events, self.n - pend.opened_at_event)
            del self.pending[about]

    def _open_evaluation(self, handle: str, *, about: str, q: float, evaluator_id: str,
                         tier: int) -> PendingJudgement:
        """Open one evaluator decision's wait on its two signals (ruling R1).

        A decision on the fast channel has no tier above it, so its grade window is
        closed from the start and it settles on the world's grade alone. A meta whose
        judged decision already has its consequence is scored at once.
        """
        channel = self.queue.get(handle).channel
        rec = PendingJudgement(handle, channel, self.n, tier, opened_at_tick=self.ticks_consumed,
                               about=about, q=float(q), evaluator_id=evaluator_id,
                               grade_closed=channel == CH_FAST)
        self.pending[handle] = rec
        if tier > 1 and about in self.consequence_scores:
            self._score_meta(rec, self.consequence_scores[about][0])
        return rec

    def _acted(self, handle: str) -> bool:
        """Whether a decision executed anything at the venue or earned anything.

        Guarantees a durable venue intent, a lot opened or closed, or a paid service
        receipt: the operations whose outcome ``return_paid_off`` measures.
        """
        try:
            account = self.consequences.table.account(handle)
        except KeyError:
            return False
        if account.opened_lots or account.closes or account.earnings:
            return True
        try:
            return bool(self.executed_operations(handle))
        except (AttributeError, KeyError):
            return False

    def _world_outcome(self, about: str) -> tuple[str, float | None, str | None]:
        """The measured outcome of a judged return: ``(state, y, kind)``.

        ``state`` is ``open`` while the world has not answered, ``none`` when it
        never will, and ``measured`` with ``y`` in [0, 1] and the kind of measurement
        (ruling R1):

        * a return that executed venue operations is measured by ``return_paid_off``,
          realized or marked P&L net of its compute and tool cost, as 0 or 1, once the
          consequence book fixes it (at its backstop at the latest);
        * a return that executed nothing and named the trade it declined is
          measured by that trade's opportunity price at the consequence backstop
          (ruling R2), with the mids frozen when the return was made;
        * anything else (a bare hold) has no world outcome, and only the tier above
          grades a verdict about it.

        A measurement is taken once and kept, so every verdict about one return is
        scored against the same fact.
        """
        known = self.world_outcomes.get(about)
        if known is not None:
            return known["state"], known["y"], known["kind"]
        try:
            account = self.consequences.table.account(about)
        except KeyError:
            return "none", None, None
        if account.voided:
            return "none", None, None
        if self._acted(about):
            payoff = account.payoff
            if payoff is None:
                return "open", None, None
            if payoff.censored is not None:
                return self._keep_outcome(about, "none", None, None)
            return self._keep_outcome(about, "measured", float(payoff.y), RETURN_PAID_OFF.id)
        frozen = self.reference_mids.get(about)
        if frozen is None:
            return self._keep_outcome(about, "none", None, None)
        opened = (account.opened_at_tick if account.opened_at_tick is not None
                  else frozen["tick"])
        if self.ticks_consumed < opened + self.ev.consequence_backstop_ticks:
            return "open", None, None
        fee = getattr(self.exchange, "fee_bps", None)
        try:
            fee_bps = Decimal(str(fee)) if fee is not None else DEFAULT_TAKER_FEE_BPS
        except (InvalidOperation, ValueError):
            fee_bps = DEFAULT_TAKER_FEE_BPS
        priced = opportunity_cost(tuple(tuple(m) for m in frozen["mids"]), latest_mids(self),
                                  2 * fee_bps, frozen["declined"])
        self.reference_mids.pop(about, None)
        if priced is None:
            return self._keep_outcome(about, "none", None, None)
        self.ledger.append({"kind": "consequence.opportunity", "handle": about,
                            "horizon_ticks": self.ev.consequence_backstop_ticks,
                            **priced, "ts": self.clock.now_ns})
        owner = self.handle_to_assembly.get(about) or self.outcomes.seat_of(about)
        if owner is not None:
            # A fact about the producer's own decision, told to it; it is not its reward
            # (ruling R2: it never replaces the verdict as a producer's score).
            self.outcomes.append(owner, handle=about, evidence=f"opportunity:{about}",
                                 outcome={"kind": "opportunity_cost", "score": priced["score"],
                                          "declined": priced["declined"],
                                          "declined_net_bps": priced.get("declined_net_bps"),
                                          "moves": priced["moves"],
                                          "round_trip_fee_bps": priced["round_trip_fee_bps"]})
        return self._keep_outcome(about, "measured", float(priced["score"]),
                                  OPPORTUNITY_DEFINITION)

    def _keep_outcome(self, about: str, state: str, y: float | None,
                      kind: str | None) -> tuple[str, float | None, str | None]:
        """Keep a final measurement so every verdict about the return reads the same one."""
        self.world_outcomes[about] = {"state": state, "y": y, "kind": kind,
                                      "tick": self.ticks_consumed}
        return state, y, kind

    def _freeze_declined_trade(self, handle: str, outputs: Any) -> None:
        """Freeze the mids a declined trade is priced from, when the return names one.

        Guarantees the benchmark is fixed ex ante, from the mids the world had
        already broadcast when the return was made (ruling R2).
        """
        declined = declined_trade(outputs if isinstance(outputs, dict) else {})
        mids = latest_mids(self)
        if declined is None or not mids:
            return
        self.reference_mids[handle] = {"declined": declined, "mids": [list(m) for m in mids],
                                       "tick": self.ticks_consumed}

    def _score_verdict(self, rec: PendingJudgement, y: float, kind: str) -> None:
        """Score one judge's verdict against its return's measured outcome (ruling R1).

        The verdict is a prediction: it is scored by Brier (higher is better,
        ``settle.normative_brier``) against y, beside the base rate of that kind of
        outcome before this return's own entered it. The consequence score is the
        judge's own reward on its second signal, it trains the judge's standing,
        and the judge is told, privately.
        """
        result = self.settler.settle_verdict(
            evaluator_id=rec.evaluator_id, about_handle=rec.about, q=rec.q, outcome=y,
            key=f"{VERDICT_BASE}{kind}")
        score = consequence_score(result.brier, result.baseline_brier)
        seq = self.ledger.append({
            "kind": "verdict.consequence", "handle": rec.handle, "about_handle": rec.about,
            "evaluator_id": rec.evaluator_id, "q": rec.q, "y": y, "outcome": kind,
            "brier": result.brier, "baseline_brier": result.baseline_brier,
            "score": score, "ts": self.clock.now_ns,
        })
        self.outcomes.append(rec.evaluator_id, handle=rec.handle, evidence=seq,
                             outcome={"judged_outcome": kind, "judged_y": round(y, 4),
                                      "your_verdict_brier": round(result.brier, 4),
                                      "baseline_brier": round(result.baseline_brier, 4),
                                      "consequence_score": round(score, 4)})
        self._count_consequence(rec.evaluator_id)
        if rec.about in self.pending_exposure:
            self.exposure_scores.setdefault(rec.about, []).append(score)
        self._close_consequence(rec.handle, score, rec)

    def _score_meta(self, rec: PendingJudgement, judged: float | None) -> None:
        """Score a meta's grade against the consequence score of the decision it graded.

        Essay II.III.b: the metas are graded by the world too. A meta's conformity is
        a prediction of the graded decision's consequence score (``consequence_score``
        of a judge's verdict, or of a lower meta's grade), scored by Brier beside the
        base rate of those scores. When the graded decision has no world outcome,
        neither has the meta's grade.
        """
        if rec.consequence_closed:
            return
        if judged is None:
            self._close_consequence(rec.handle, None, rec)
            return
        result = self.settler.settle_verdict(
            evaluator_id=rec.evaluator_id, about_handle=rec.about, q=rec.q, outcome=judged,
            key=EVALUATION_BASE)
        score = consequence_score(result.brier, result.baseline_brier)
        self.ledger.append({"kind": "meta.consequence", "handle": rec.handle,
                            "about_handle": rec.about, "conformity": rec.q,
                            "judged_consequence": judged, "brier": result.brier,
                            "baseline_brier": result.baseline_brier, "score": score,
                            "ts": self.clock.now_ns})
        self._count_consequence(rec.evaluator_id)
        self._close_consequence(rec.handle, score, rec)

    def _close_consequence(self, handle: str, score: float | None,
                           rec: PendingJudgement | None = None) -> None:
        """Close one decision's consequence and pass it up to every meta that graded it.

        Guarantees the score is kept for a meta that grades the decision later, and
        that every meta already waiting on it is scored now, recursively: the tiers
        are graded by the world as far up as they go.
        """
        if rec is not None:
            rec.consequence, rec.consequence_closed = score, True
        self.consequence_scores[handle] = (score, self.ticks_consumed)
        for meta in sorted((r for r in self.pending.values()
                            if r.evaluation and r.tier > 1 and r.about == handle
                            and not r.consequence_closed), key=lambda r: r.handle):
            self._score_meta(meta, score)

    def _settle_evaluations(self) -> None:
        """Advance every evaluator decision's two signals, then settle what is complete.

        A judge's consequence closes when its return's outcome is measured or known
        to be absent; a meta's when the decision it graded closes. Either closes
        empty past the consequence backstop. The grade window closes after
        ``verdict_timeout_ticks``. A decision with both closed settles on
        ``evaluation_reward``, less its card penalty, or censored with neither.
        """
        backstop = self.ev.consequence_backstop_ticks
        timeout = self.ev.verdict_timeout_ticks
        records = sorted((r for r in self.pending.values() if r.evaluation),
                         key=lambda r: (r.tier, r.handle))
        for rec in records:
            if rec.consequence_closed:
                continue
            if rec.tier == 1:
                state, y, kind = self._world_outcome(rec.about)
                if state == "measured":
                    self._score_verdict(rec, y, kind)
                    continue
                if state == "none":
                    self._close_consequence(rec.handle, None, rec)
                    continue
            if self._tick_age(rec) > backstop + timeout:
                self._close_consequence(rec.handle, None, rec)
        self._settle_exposures()
        for rec in records:
            if not rec.grade_closed and self._tick_age(rec) > timeout:
                rec.grade_closed = True
            if not (rec.grade_closed and rec.consequence_closed):
                continue
            del self.pending[rec.handle]
            self._settle_evaluation(rec)
        # Past a backstop and a verdict window nothing opens on these any more: a judge
        # reads a return within its verdict window, and a declined trade is priced at
        # its backstop.
        horizon = self.ticks_consumed - backstop - timeout
        for kept in (self.consequence_scores, self.world_outcomes, self.reference_mids):
            for handle in [h for h, v in kept.items()
                           if (v[1] if isinstance(v, tuple) else v["tick"]) < horizon]:
                del kept[handle]

    def _settle_evaluation(self, rec: PendingJudgement) -> None:
        """Settle one evaluator decision on both its signals (``evaluation_reward``)."""
        reward = evaluation_reward(rec.grade, rec.consequence)
        cards = "meta" if rec.tier > 1 else "evaluator"
        self.ledger.append({"kind": "evaluator.settled", "handle": rec.handle, "tier": rec.tier,
                            "grade": rec.grade, "graded_by": rec.graded_by,
                            "consequence": rec.consequence, "reward": reward,
                            "ts": self.clock.now_ns})
        if self.queue.get(rec.handle).status not in (SettleStatus.PENDING,
                                                     SettleStatus.TIMED_OUT):
            return
        if reward is None:
            self.queue.settle(rec.handle, channel=rec.channel, score=0.0,
                              status=SettleStatus.CENSORED,
                              definition_version=EVALUATION_UNSCORED, sampling_ref=None)
            self.stats.censored += 1
            self.window.outcomes += 1
            self.window.censored += 1
            return
        self._settle_priced(rec.handle, channel=rec.channel, score=reward,
                            definition_version=DEF_EVALUATION, sampling_ref=rec.graded_by,
                            cards=cards)
        if rec.channel == CH_FAST:
            self.stats.fast_settlements += 1

    def _settle_exposures(self) -> None:
        """An antagonist earns by how wrong the judges' verdicts on its return were.

        Guarantees a continuous reward, only where the world measured the return:
        the mean over the judges scored on it of ``1 - consequence score``, so an
        antagonist whose return the judges predicted no better than the base rate
        earns 0.5 and one that fooled them earns more (II.III.b: the adversarial
        layer farms realized consequence; evaluations S4 removed the fixed
        endorsement threshold). A return no judge was scored on settles censored
        once no judge is still waiting on it and its verdict window has passed.
        """
        timeout = self.ev.verdict_timeout_ticks
        for handle, opened in list(self.pending_exposure.items()):
            waiting = any(r.evaluation and r.tier == 1 and r.about == handle
                          and not r.consequence_closed for r in self.pending.values())
            scores = self.exposure_scores.get(handle, [])
            if waiting or (not scores and self.ticks_consumed - opened <= timeout):
                continue
            del self.pending_exposure[handle]
            self.exposure_scores.pop(handle, None)
            if self.queue.get(handle).status not in (SettleStatus.PENDING,
                                                     SettleStatus.TIMED_OUT):
                continue
            if not scores:
                self.ledger.append({"kind": "exposure.settled", "handle": handle,
                                    "score": None, "ts": self.clock.now_ns})
                self.queue.settle(handle, channel=CH_EXPOSURE, score=0.0,
                                  status=SettleStatus.CENSORED,
                                  definition_version=DEF_EXPOSURE, sampling_ref=None)
                self.stats.censored += 1
                self.window.outcomes += 1
                self.window.censored += 1
                continue
            score = fmean(1.0 - s for s in scores)
            self.ledger.append({"kind": "exposure.settled", "handle": handle, "score": score,
                                "judge_consequences": list(scores), "ts": self.clock.now_ns})
            self._settle_priced(handle, channel=CH_EXPOSURE, score=score,
                                definition_version=DEF_EXPOSURE, sampling_ref=None,
                                cards="antagonist")
            self.stats.exposures_settled += 1
            self.window.exposures_settled += 1
            # Won: the judges predicted the return worse than its base rate did.
            if score > 0.5:
                self.stats.exposures_won += 1
                self.window.exposures_won += 1

    def _censor_judgement(self, handle: str, reason: str) -> None:
        """A judgement the kernel could not use is charged its call and settles censored.

        Guarantees no score: a malformed, refused or unaddressable judgement is a
        form failure, not a grade (evaluations S2, P9). Its cost was metered when the
        call was made; the charter's ``well_formed_rate`` is where the population
        prices form.
        """
        self.ledger.append({"kind": "evaluation.censored", "handle": handle,
                            "reason": reason[:300], "ts": self.clock.now_ns})
        if self.queue.get(handle).status is not SettleStatus.PENDING:
            return
        self.queue.settle(handle, channel=self.queue.get(handle).channel, score=0.0,
                          status=SettleStatus.CENSORED, definition_version=CENSORED_JUDGEMENT,
                          sampling_ref=None)
        self.stats.censored += 1
        self.window.outcomes += 1
        self.window.censored += 1

    def _tick_age(self, judgement: PendingJudgement) -> int:
        """World ticks consumed since this judgement opened."""
        return self.ticks_consumed - (judgement.opened_at_tick or 0)

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
        """A decision nobody judged within the verdict timeout settles censored.

        Evaluator decisions are not here: they close on their own two signals
        (``_settle_evaluations``).
        """
        stale = [
            p
            for p in self.pending.values()
            if not p.evaluation and self._tick_age(p) > self.ev.verdict_timeout_ticks
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
        """Close every assembly round whose decision now has an outcome.

        The evidence is the decision's first outcome, by the same rule the router
        follows (``_deliver_returns``): a score before its cutoff, or else nothing
        observed. Every round closes at most once.
        """
        for handle in list(self.assembly_rounds):
            decision = self.queue.get(handle)
            if decision.status is SettleStatus.PENDING:
                continue
            first = next(iter(self.queue.history(handle)), None)
            reward = (min(1.0, max(0.0, float(first.score)))
                      if first is not None and first.status is SettleStatus.SETTLED
                      else None)
            self._close_assembly_round(handle, reward, priced=first)

    def _close_assembly_round(self, handle: str, reward: float | None,
                              priced: LearningReturn | None = None) -> None:
        """Train an assembly's own learner from the reward that settled its decision.

        The reward is the same thin score the router receives; what differs is the
        distribution it is attributed to. The router's record prices the choice of
        who acted; this one prices what the actor chose to do, over the action set
        the actor declared. A decision with no observed score (censored,
        inapplicable, or past its cutoff) is credited the learner's neutral
        estimate for the action (its own observed mean, else zero consequence), as
        the router's are.
        """
        assembly_id = self.assembly_rounds.pop(handle, None)
        if assembly_id is None:
            return
        learner = self.assembly_learners.get(assembly_id)
        if learner is None:
            return
        declared = self.queue.declared_propensity(handle)
        imputed = reward is None
        if declared is not None and reward is None:
            reward = _priced(learner.observed.neutral(declared.chosen), priced)
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
        if not imputed:
            learner.observed.record(declared.chosen, reward)
        self.ledger.append({"kind": "propensity.learned", "handle": handle,
                            "assembly_id": assembly_id, "action": declared.chosen,
                            "propensity": declared.probs[index], "reward": reward,
                            "imputed": imputed, "ts": self.clock.now_ns})

    @staticmethod
    def _router_sampled(decision: Any) -> bool:
        """Whether the router that holds this decision drew it (defect 3).

        A child request is opened under its parent's router so its score has an
        addressable home, but the parent chose the target: its propensity of 1.0
        is the parent's choice, not a probability the router sampled from, and a
        router trained on it would learn from a round it never played.
        """
        prop = decision.propensity
        return (decision.parent_handle is None and prop.source == "sampled"
                and prop.learner_state_hash != "parent-selected")

    def _learn_router_return(self, state: Any, lr: LearningReturn) -> None:
        """Train a router once per decision it drew, on the evidence that decision has.

        The rule (defects 2 and 4): a decision's cutoff is its kernel deadline. Its
        first outcome is its one update. A score that settled it before the cutoff
        is observed and trains the router at that score. A decision that closed
        without an observed score (censored, inapplicable) or reached its cutoff
        unscored (timed out) is not a zero: it is credited the router's neutral
        estimate for the arm drawn (``ObservedRewards.neutral``: that arm's own
        observed mean, else the router's zero consequence). A score that arrives after the
        cutoff still settles the decision for the kernel -- its money, its
        standing, its history -- but trains no learner a second time.

        An abstention (NOOP) is credited the router's zero-consequence reward
        (``RouterState.neutral``), whatever its settlement: waking nobody is worth
        what a woken seat that delivered nothing scores on the scale the router's
        seat rounds are settled on (0.5 for producer outcomes, 0.75 for Brier), so
        it is never worth the average the seats earned (the free-average defect),
        and a seat is woken more often only by scoring above it. The credit is deferred to the delay
        the router's seat rounds take to be learned (``_defer_abstention``): an
        abstention settles at once, and crediting it at once would put it a whole
        feedback delay ahead of every seat it competes with. A router that has
        been replaced trains its live successor on these rounds instead of itself
        (``_apply_router_round``), so no settled reward is spent on a copy that
        never samples again.
        """
        decision = self.queue.get(lr.handle)
        prop = decision.propensity
        keyed = isinstance(state.learner, _KeyedLearner)
        key = self.snapshot_keys.pop(lr.handle, None) if keyed else None
        if not self._router_sampled(decision):
            if key is not None:
                state.learner.inner.discard_for(key)
            return
        if (
            lr.status is not SettleStatus.TIMED_OUT
            and any(r.status is SettleStatus.TIMED_OUT
                    for r in self.queue.history(lr.handle))
        ):
            return  # learned once already, neutrally, at its cutoff
        if prop.chosen == NOOP:
            if self._abstention_owed_or_credited(lr):
                if key is not None:
                    state.learner.inner.discard_for(key)
                return
            if not keyed or key is not None:
                self._defer_abstention(state, key, decision)
            return
        target = self._successor_state(state)
        settled = lr.status is SettleStatus.SETTLED
        if settled:
            reward = min(1.0, max(0.0, float(lr.score)))
        else:
            # The seat's own baseline on the live successor, less any charter price
            # its settlement carries (routers-learn + charter-price-bites).
            reward = _priced(target.observed.neutral(prop.chosen, target.neutral()), lr)
        if reward is None:
            if key is not None:
                state.learner.inner.discard_for(key)
            return
        if keyed and key is None:
            return  # its frozen round is already spent: nothing trains, nothing is booked
        fb = BanditFeedback(prop.chosen, reward, prop.probs[prop.action_ids.index(prop.chosen)])
        if target is not state:
            p, executed = state.learner.inner.take_for(key) if keyed else (None, None)
            learned = self._apply_router_round(state, lr.handle, p, executed, fb)
        elif keyed:
            state.learner.inner.update_for(key, fb)
            learned = True
        elif set(prop.action_ids) <= set(state.universe):
            state.learner.update(fb)
            learned = True
        else:
            self.ledger.append({"kind": "propensity.unlearned", "handle": lr.handle,
                                "learner_id": state.learner.id,
                                "reason": "the drawn arm is outside this router's universe",
                                "ts": self.clock.now_ns})
            learned = False
        if not learned:
            return
        # Booked only for a round that trained the router: the seat's own baseline, the
        # delay abstentions wait for and the scales they are priced on all describe
        # rounds the router learned from, never one that trained nothing.
        target.latency[0] += max(0, self.clock.now_ns - decision.opened_ns)
        target.latency[1] += 1
        if settled:
            target.observed.record(prop.chosen, reward)
            target.definitions[lr.definition_version] = (
                target.definitions.get(lr.definition_version, 0) + 1)

    def _abstention_owed_or_credited(self, lr: LearningReturn) -> bool:
        """Whether this abstention is already owed, or was credited on an earlier return.

        Guarantees a NOOP is credited once, on the first return its handle received:
        the queue admits one final outcome after at most one timeout, so any later
        return repeats a round already credited. A keyed router spends its snapshot
        key on the first; this is the same guard for a plain EXP3 router.
        """
        if lr.handle in self.noop_credits:
            return True
        first = self.queue.history(lr.handle)[0]
        return ((first.status, first.definition_version, first.score)
                != (lr.status, lr.definition_version, lr.score))

    def _defer_abstention(self, state: Any, key: str | None, decision: Any) -> None:
        """Owe ``state`` one abstention credit, due when a seat round would be learned.

        Guarantees the credit is applied exactly once, at the drawn NOOP's logged
        propensity, no earlier than its open time plus the mean delay the router's
        learned seat rounds took (its kernel deadline while there is none), and that
        a swap router's frozen round is detached now and survives a checkpoint in
        ``noop_credits``.
        """
        p, executed = state.learner.inner.take_for(key) if key is not None else (None, None)
        total, count = self._successor_state(state).latency
        due = decision.opened_ns + total // count if count else decision.deadline_ns
        self.noop_credits[decision.handle] = {"router": state.learner.id, "due_ns": due,
                                              "p": p, "executed": executed}
        self._credit_abstentions()

    def _credit_abstentions(self) -> None:
        """Apply every owed abstention credit that is due, in the order it was owed."""
        now = self.clock.now_ns
        routers = {st.learner.id: st
                   for st in self._all_router_states() + list(self.retired_routers.values())}
        for handle, credit in list(self.noop_credits.items()):
            if credit["due_ns"] > now:
                continue
            del self.noop_credits[handle]
            drawer = routers.get(credit["router"])
            if drawer is None:
                # A drained router is kept while it is owed, so this should not happen;
                # if it does, the dropped credit is on the record, not silent.
                self.ledger.append({"kind": "propensity.unlearned", "handle": handle,
                                    "learner_id": credit["router"],
                                    "reason": "the router owed this abstention is gone",
                                    "ts": now})
                continue
            prop = self.queue.get(handle).propensity
            # Priced when due, on the scales of every seat round learned by then, less
            # the charter prices a woken decision bears in the window it was drawn in.
            neutral = self._successor_state(drawer).neutral()
            reward, penalty = self._priced_abstention(handle, neutral)
            self.ledger.append({"kind": "router.abstention_priced", "handle": handle,
                                "router": credit["router"], "neutral": neutral,
                                "penalty": penalty, "reward": reward, "ts": now})
            fb = BanditFeedback(NOOP, reward, prop.probs[prop.action_ids.index(NOOP)])
            self._apply_router_round(drawer, handle, credit["p"], credit["executed"], fb)

    def _priced_abstention(self, handle: str, neutral: float) -> tuple[float, float]:
        """An abstention's credit: the router's zero-consequence reward less its price.

        Ruling R9 (versioning P4, primitive F1): the arm that wakes nobody bears the
        same charter prices a woken decision bears in the window it was drawn in,
        its card penalty computed exactly as a woken decision's is, with that
        decision's own share (a card on cost shares nothing to a decision that spent
        nothing), on the cards of each role it would have filled, weighted by the
        odds the draw gave that role's seats (``_abstention_roles``): the price a
        woken decision of this draw bears in expectation. Waking nobody can
        therefore never beat waking a seat merely because penalties touched only the
        decisions that acted, and a stable failure's ratcheted prices reach it too.
        An abstention drawn before its window recorded it is credited unpriced.
        Returns (reward, penalty).
        """
        origin = self.price_origins.get(handle, {}).get("origin")
        window = self.price_windows.get(origin)
        sample = window.decisions.get(handle) if window is not None else None
        if sample is None:
            return neutral, 0.0
        roles = sample.get("menu_roles") or {sample["role"]: 1.0}
        penalty = sum(weight * self._penalty_for(role, handle)
                      for role, weight in sorted(roles.items()))
        return min(1.0, max(0.0, neutral - penalty)), penalty

    def _router_owed_abstention(self, learner_id: str) -> bool:
        """Keep a router addressable until every abstention it drew has been credited."""
        return any(c["router"] == learner_id for c in self.noop_credits.values())

    def _apply_router_round(self, drawer: Any, handle: str, p: dict | None,
                            executed: dict | None, fb: BanditFeedback) -> bool:
        """Train the live router that owns ``drawer``'s rounds once on this round.

        Guarantees the update uses the drawn arm's logged propensity: a swap router
        credits each row its share of the policy that owned the round (the drawing
        swap router's frozen p, else the logged draw). A drawn arm the learning
        router no longer holds trains nothing and is ledgered unlearned; a round a
        replaced router drew is ledgered ``router.carried``. Returns whether it trained.

        A round drawn over a larger universe than the learning router's (N_old >
        N_new) is stepped at the drawer's size, gamma/N_old rather than gamma/N_new
        (the reward is scaled by N_new/N_old, which scales a swap router's every row
        alike): its estimator is bounded by N_old/gamma, so at gamma/N_new one round
        could move a log weight by N_old/N_new > 1 and swamp every round before it
        (thrash, essay II.II.a). The rescale is ledgered ``router.step_rescaled``.
        """
        target = self._successor_state(drawer)
        prop = self.queue.get(handle).propensity
        logged = dict(zip(prop.action_ids, prop.probs, strict=True))
        drawn, learning = len(drawer.universe), len(target.universe)
        scored = fb.reward
        if learning < drawn:
            fb = BanditFeedback(fb.action, scored * learning / drawn, fb.propensity)
        reason = None
        if fb.action not in target.universe:
            reason = "the drawn arm is outside the learning router's universe"
        else:
            try:
                if isinstance(target.learner, _KeyedLearner):
                    target.learner.inner.update_carried(p or logged, executed or logged, fb)
                else:
                    target.learner.update(fb)
            except (KeyError, ValueError, TypeError) as exc:
                reason = str(exc)[:200]
        if reason is not None:
            self.ledger.append({"kind": "propensity.unlearned", "handle": handle,
                                "learner_id": target.learner.id, "reason": reason,
                                "ts": self.clock.now_ns})
            return False
        if target is not drawer:
            self.ledger.append({"kind": "router.carried", "handle": handle,
                                "from": drawer.learner.id, "to": target.learner.id,
                                "action": fb.action, "reward": scored,
                                "ts": self.clock.now_ns})
        if learning < drawn:
            self.ledger.append({"kind": "router.step_rescaled", "handle": handle,
                                "learner_id": target.learner.id,
                                "drawn_universe": drawn, "learning_universe": learning,
                                "reward": scored, "stepped_as": fb.reward,
                                "ts": self.clock.now_ns})
        return True

    def _deliver_returns(self) -> None:
        self._close_assembly_rounds()
        for state in self._all_router_states() + list(self.retired_routers.values()):
            lid = state.learner.id
            seen = self.delivered_seen.get(lid, 0)
            if hasattr(self.queue, "returns_since"):
                # Only the undelivered tail is mapped; the delivered prefix is never read.
                fresh, total = self.queue.returns_since(lid, seen)
            else:
                returns = self.queue.returns_for(lid)
                fresh, total = returns[seen:], len(returns)
            for lr in fresh:
                self._learn_router_return(state, lr)
            self.delivered_seen[lid] = total
        self._credit_abstentions()
        for state in self._all_router_states():
            # A router that stopped drawing still has its last window's watch closed.
            self._close_abstention_watch(state)
        for state in list(self.retired_routers.values()):
            lid = state.learner.id
            if (
                not self.queue.outstanding(lid)
                and not self._router_owed_abstention(lid)
            ):
                self.queue.retire_actor(lid)
                self.ledger.append({"kind": "router.drained", "learner_id": lid})
                del self.retired_routers[lid]
