"""Runtime feedback method group."""

from __future__ import annotations

import random
from dataclasses import asdict, dataclass
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
    GROUNDED_DEFINITION,
    INACTION_ACTIONS,
    OPPORTUNITY_DEFINITION,
    UNKNOWN_REASON,
    GroundedContract,
    declined_trade,
    latest_mids,
    observed_evidence_refs,
    opportunity_cost,
    parse_finding,
    public_evidence,
)
from factorylab.runtime.pricing import UNRESOLVED_PRICED
from factorylab.runtime.routing import _KeyedLearner
from factorylab.runtime.shared import (
    CH_CONFORMITY,
    CH_CONSEQUENCE,
    CH_EXPOSURE,
    CH_FAST,
    CH_VERDICT,
    DEF_CONFORMITY,
    DEF_EXPOSURE,
    DEF_META_CONSEQUENCE,
    NOOP,
    _to_plain,
)
from factorylab.runtime.summary import _as_unit
from factorylab.settlement import (
    Forecast,
    WindowFacts,
    brier,
    open_forecast_decision,
)
from factorylab.settlement.fidelity import challenge_proposal, choose_adjudicator
from factorylab.settlement.settle import PredicateForecast
from factorylab.settlement.vocabulary import (
    RETURN_PAID_OFF,
    UNMEASURED_DEFINITION,
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


# A verdict is a prediction that the judged return will not be blamed by the charter. Its
# commitment waits in ``pending`` under the judge's payoff-forecast handle until the
# return's window closes; a subject marker under the judged return's handle keeps that
# window's attribution evidence alive until every verdict on the return has settled.
NORM_COMMITMENT = "verdict.norm"
NORM_SUBJECT = "verdict.subject"
# A verdict at or above this endorses the return; on a return the window blamed, it
# exposes the judge to the antagonist that made the return.
VERDICT_ENDORSEMENT = 0.8
# A judgement whose fact the world never produced. Nothing scored, nothing
# priced, nothing moved: the commission was answered and the answer is that
# there was nothing here to measure (GPT-6 third reading §6.B).
UNMEASURED_REASON_UNREAD = "the judged return's window closed unread"
# What a return has to have committed to before a judgement of it can be
# measured against anything: a stated claim, a counterfactual, an observation
# rule, a resource decision, or an accepted promise (§6.B, and the reviewer's
# "a hold should be evaluated only against something it commits to"). An action
# that is none of these is a return that did nothing and said nothing about
# doing nothing; "looks prudent" is not a fact.
COMMITMENT_FIELDS = (
    "forecasts", "payoff", "claim", "claims", "counterfactual", "observation_rule",
    "promise", "commitment", "commitments", "hypothesis", "prediction", "expect",
    "register", "requests", "tool_calls", "subscribe", "defer",
)
#: Actions that are not themselves resource decisions.
QUIET_ACTIONS = ("", "hold", "noop", "none", "wait", "pause")
UNMEASURED_REASON_UNCOMMITTED = (
    "the judged return committed to nothing this evaluation could measure: no claim, "
    "counterfactual, observation rule, resource decision or accepted promise"
)
_CARDS_FOR_CHANNEL = {CH_VERDICT: "producer", CH_CONFORMITY: "evaluator",
                      CH_EXPOSURE: "antagonist"}


@dataclass
class PendingJudgement:
    handle: str  # decision awaiting a verdict (producer) or conformity (evaluator/meta)
    channel: str
    opened_at_event: int
    tier: int = 1
    # NORM_COMMITMENT only: one verdict awaiting the charter's blame on the return it judged.
    about: str | None = None  # the judged return
    judge: str | None = None  # the evaluator's own decision, whose verdict this is
    evaluator_id: str | None = None
    q: float | None = None  # the verdict
    cards: str | None = None  # the judged return's card label
    window: int | None = None  # the price window the judged return worked in
    payoff_beat: int | None = None  # its payoff forecast beat the baseline, once settled
    # Whether this verdict sealed a payoff forecast at all. ``payoff`` is an
    # optional field in edition 3's third round, so a verdict that made no such
    # claim is not waiting for one (§7).
    awaits_payoff: bool = True
    # The verdict is closed out once, informatively or not. ``verdict_beat`` stays None
    # when the window never judged the return: there is no fact, so there is no score.
    verdict_closed: bool = False
    verdict_beat: int | None = None  # the verdict beat the baseline, once scored
    graded: bool = False  # the metas that conformed to it have their outcome
    # No fact the charter produced can decide this verdict: it closes unmeasured,
    # and every meta that conformed to it closes unmeasured with it, rather than
    # timing out at zero for a fact the runtime owed them and never delivered.
    unmeasured: bool = False
    # The world tick this judgement opened at. Its horizons (the verdict timeout,
    # the consequence backstop) count world ticks consumed, never internal events:
    # a busy tick is many events and still one tick (defect 1). A judgement
    # recorded without one is aged from the world's first tick, so it can never
    # wait forever.
    opened_at_tick: int | None = None


class FeedbackMixin:
    """Preserve runtime state and behavior for feedback operations."""

    @property
    def grounded_pending(self) -> dict[str, GroundedContract]:
        """Producer contracts still waiting for independently interpreted consequences."""
        if not hasattr(self, "_grounded_pending"):
            self._grounded_pending: dict[str, GroundedContract] = {}
        return self._grounded_pending

    @property
    def grounded_closed(self) -> set[str]:
        """Producer handles whose grounded settlement is permanently final."""
        if not hasattr(self, "_grounded_closed"):
            self._grounded_closed: set[str] = set()
        return self._grounded_closed

    @property
    def meta_waiting_since(self) -> dict[str, int]:
        """When each judge's metas began waiting on a fact about it.

        Counted in world ticks consumed, like the backstop it is compared with.
        Checkpointed with the rest of the runtime, so a restored runtime carries
        each wait over exactly rather than starting it again.
        """
        if not hasattr(self, "_meta_waiting_since"):
            self._meta_waiting_since: dict[str, int] = {}
        return self._meta_waiting_since

    @property
    def open_adjudications(self) -> dict[str, str]:
        """Seat -> the id of the adjudication queued for it, while it is unanswered."""
        if not hasattr(self, "_open_adjudications"):
            self._open_adjudications: dict[str, str] = {}
        return self._open_adjudications

    def _judging_seats(self) -> list[str]:
        """Live seats that judge: the other judge and the meta seats, in a stable order.

        The antagonist's evidence route is what may supply a counter-case; who
        decides the claim is a judge that has no stake in it.
        """
        from factorylab.runtime.shared import assembly_rewards

        seats = []
        for assembly_id, assembly in sorted(self.assemblies.items()):
            if assembly_id in self.retired_assemblies:
                continue
            shapes = set(assembly_rewards(assembly.spec).values())
            kinds = set(assembly.spec.emits)
            if kinds & {"Verdict", "MetaVerdict"} or shapes & {"conformity", "forecast"}:
                seats.append(assembly_id)
        return seats

    def _measurement_owners(self, measurement: str) -> set[str]:
        """Seats the challenged measurement answers for: its standing rides on it.

        A card that answers for a role is the price of every seat measured in
        that role. Those seats own the measurement in the only sense that
        matters here — an adjudication that went their way would raise or lower
        their own score — so they do not decide whether it is faithful.
        """
        from factorylab.cortex.registration import measured_role

        cards = tuple(getattr(self.charter, "cards", ()) or ())
        card = next((c for c in cards
                     if c.id == measurement or c.observation == measurement), None)
        if card is None or getattr(card, "answers_for", "all") in (None, "all"):
            return set()
        owners = set()
        for assembly_id, assembly in self.assemblies.items():
            kinds = assembly.spec.emits or ()
            if any(measured_role(kind) == card.answers_for or kind == card.answers_for
                   for kind in kinds):
                owners.add(assembly_id)
        return owners

    def _queue_adjudication(self, commitment: PendingJudgement) -> None:
        """Queue one accepted objection for an adjudicator with no stake in it (§7).

        The challenged proxy cannot certify its own fidelity, so the objection is
        not scored where it was made. It is an open claim until someone who did
        not write the verdict and does not own the measurement answers it. If
        nobody independent exists, it stays open: an interested finding is worse
        than none.
        """
        adjudication = self.settler.adjudication_for(commitment.judge)
        if adjudication is None or adjudication.adjudicator is not None:
            return
        if adjudication.id in self.open_adjudications.values():
            return
        owners = self._measurement_owners(adjudication.measurement)
        candidates = [s for s in self._judging_seats()
                      if s != commitment.evaluator_id and s not in owners]
        adjudicator, excluded = choose_adjudicator(
            candidates, author=commitment.evaluator_id,
            measurement_owner=next(iter(sorted(owners)), None))
        self.ledger.append({
            "kind": "fidelity.adjudication_queued", "adjudication": adjudication.id,
            "objector": adjudication.objector, "objection_handle": adjudication.objection_handle,
            "about_handle": adjudication.about_handle, "measurement": adjudication.measurement,
            "adjudicator": adjudicator, "excluded": sorted({*excluded, *owners}),
            "ts": self.clock.now_ns,
        })
        if adjudicator is not None:
            self.open_adjudications[adjudicator] = adjudication.id

    def _adjudication_for(self, seat: str) -> Any:
        """The open adjudication queued for this seat, if it is holding one."""
        identity = self.open_adjudications.get(seat)
        receipts = self.settler.receipts()
        if identity is None or receipts is None:
            return None
        return receipts.get(identity)

    def _resolve_adjudication(self, seat: str, handle: str, raw: Any) -> None:
        """Record this seat's independent finding on the objection it was given.

        What the finding produces is a learning receipt for the objector, scored
        on the uncertainty the objector itself stated, and — when the objection
        is upheld — a challenge proposal for the card, opened through the
        population's own registration route. Nothing here reprices anything: the
        committee does that, or nobody does.
        """
        adjudication = self._adjudication_for(seat)
        if adjudication is None or not isinstance(raw, dict):
            return
        upheld = raw.get("upheld")
        reason = raw.get("reason")
        if not isinstance(upheld, bool) or not isinstance(reason, str) or not reason.strip():
            self.ledger.append({"kind": "fidelity.finding_refused", "handle": handle,
                                "adjudication": adjudication.id, "adjudicator": seat,
                                "reason": "a finding states upheld and why",
                                "ts": self.clock.now_ns})
            return
        resolved, receipt = self.settler.resolve_adjudication(
            adjudication, adjudicator=seat, upheld=upheld, finding=reason.strip()[:2000])
        self.open_adjudications.pop(seat, None)
        self.ledger.append({
            "kind": "fidelity.adjudicated", "adjudication": resolved.id, "adjudicator": seat,
            "handle": handle, "upheld": upheld, "objector": resolved.objector,
            "measurement": resolved.measurement, "learning_receipt": receipt,
            "ts": self.clock.now_ns,
        })
        # The objector is told how its own stated uncertainty scored, addressed to
        # the decision that carried the objection (C1).
        self.outcomes.append(
            resolved.objector, handle=resolved.objection_handle, evidence=resolved.id,
            outcome={"fidelity_objection_upheld": upheld,
                     "your_objection_brier": (round(brier(resolved.confidence, int(upheld)), 4)),
                     "adjudicated_by": seat, "learning_receipt": receipt})
        if upheld:
            self._open_fidelity_challenge(handle, resolved)

    def _open_fidelity_challenge(self, handle: str, adjudication: Any) -> None:
        """An upheld objection becomes a challenge the population votes on, or nothing.

        The card keeps its price until the committee moves it. A challenge is
        refused like any other proposal — a card that no longer exists, a
        replacement the runtime cannot measure — and the refusal is public.
        """
        from factorylab.cortex.request import Return

        cards = tuple(getattr(self.charter, "cards", ()) or ())
        card = next((c for c in cards if c.id == adjudication.measurement), None)
        if card is None:
            self.ledger.append({"kind": "fidelity.challenge_skipped",
                                "adjudication": adjudication.id,
                                "measurement": adjudication.measurement,
                                "reason": "the objection names an observation, not a live card",
                                "ts": self.clock.now_ns})
            return
        proposal = challenge_proposal(adjudication, replacement=None)
        self.ledger.append({"kind": "fidelity.challenge_opened", "handle": handle,
                            "adjudication": adjudication.id, "card_id": card.id,
                            "ts": self.clock.now_ns})
        self._apply_registrations(handle, Return(handle, {"register": [proposal]}, 0, "ok"))

    def _cascade_evidence_complete(self, ev: Event) -> bool:
        """Whether one arrival's evidence has finished: the return it judged has an outcome.

        A verdict whose subject is still pending is an opinion about an open
        question. It belongs to the window — it is named in the report, and the
        sibling share still reaches it — but it is not evidence yet, and the
        upward report is made of evidence (§6.C). A subject this runtime cannot
        address at all (a judgement of an event rather than a decision) is not
        held open by a fact that will never arrive.
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
        if released is not None:
            # The meta judges the window as a distribution (essay II.IV.c); its score
            # settles every handle in the window, so nobody gains by not being sampled.
            handles = list(released.payload["window"]["handles"])
            self.cascade_windows[handles[-1]] = handles[:-1]
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
        """A forecast-shaped invocation earns the mean of all its resolved predictions once."""
        from factorylab.cortex.registration import measured_role

        for handle, entry in list(self.forecast_returns.items()):
            forecasts = entry["handles"]
            if any(self.queue.get(f).status is SettleStatus.PENDING for f in forecasts):
                continue
            if self.queue.get(handle).status is SettleStatus.PENDING:
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
                    if all(f in evidence for f in forecasts):
                        y = int(fmean(evidence[f][0] for f in forecasts)
                                >= fmean(evidence[f][1] for f in forecasts))
                        for meta_handle, conformity in self.pending_meta.pop(handle, []):
                            self._settle_meta_consequence(meta_handle, conformity, y, forecasts[0])
                        self.verdict_outcomes[handle] = (y, self.ticks_consumed, forecasts[0])
            del self.forecast_returns[handle]

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
            or self._tick_age(pend) > self.ev.verdict_timeout_ticks
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
        judge = self.handle_to_assembly.get(about)
        for sibling in self.cascade_windows.pop(about, []):
            sib = self.pending.get(sibling)
            if (
                sib is None
                or self._tick_age(sib) > self.ev.verdict_timeout_ticks
                or self.queue.get(sibling).status is not SettleStatus.PENDING
            ):
                continue
            author = self.handle_to_assembly.get(sibling)
            if judge is not None and author is not None and author != judge:
                # The meta read one judge's verdict. Another judge's verdict in the same
                # window was never read and borrows no grade (architect review #4): it
                # stays pending and times out censored, unscored rather than misscored.
                self.ledger.append({"kind": "cascade.sibling_unread", "handle": sibling,
                                    "representative": about, "ts": self.clock.now_ns})
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
        self._deliver_verdict_to_inbox(about, payload["score"], judge_handle=payload["by"])

    def _judged_commitment(self, about: str | None, payload: Any) -> str | None:
        """Name what the judged return committed to, or None if it committed to nothing.

        A hold is judged against something it committed to — a claim, a
        counterfactual, an observation rule, a resource decision, an accepted
        promise — and never against how prudent it looked (§6.B). Unfamiliar work
        keeps its exploratory allowance: while a seat is still inside the novelty
        share the population granted it, its returns stay evaluable whatever they
        say, so a new seat is not made invisible by its first quiet answers.
        """
        outputs = payload if isinstance(payload, dict) else {}
        outputs = outputs.get("outputs", outputs)
        if not isinstance(outputs, dict):
            outputs = {}
        action = str(outputs.get("action", "")).strip().lower()
        if action not in QUIET_ACTIONS:
            return f"action:{action}"
        for name in COMMITMENT_FIELDS:
            if outputs.get(name):
                return name
        receipts = self.settler.receipts()
        if receipts is not None and any(c.handle == about
                                        for c in receipts.all("commitment")):
            return "accepted promise"
        seat = self.handle_to_assembly.get(about) if about else None
        if seat is not None and self._unhistoried(seat):
            return "exploratory allowance"
        return None

    def _settle_unmeasured(self, handle: str, channel: str, reason: str, *,
                           definition: str | None = None) -> bool:
        """Close one commission with no score, no price and no standing.

        ``unmeasured`` is the answer an evaluation is allowed to reach (§6.B).
        It is not a low score, and it is not a censored decision that somebody
        failed to answer: the work was done and the finding is that there was
        nothing here this evidence could measure. Nothing enters a standing,
        nothing enters a base rate, no card is blamed, and no money moves.
        """
        definition = definition or UNMEASURED_DEFINITION
        try:
            status = self.queue.get(handle).status
        except KeyError:
            return False
        if status not in (SettleStatus.PENDING, SettleStatus.TIMED_OUT):
            return False
        self.ledger.append({"kind": "evaluation.unmeasured", "handle": handle,
                            "channel": channel, "definition": definition,
                            "reason": reason, "ts": self.clock.now_ns})
        self.queue.settle(handle, channel=channel, score=0.0,
                          status=SettleStatus.INAPPLICABLE,
                          definition_version=definition, sampling_ref=None)
        owner = self.handle_to_assembly.get(handle) or self.outcomes.seat_of(handle)
        if owner is not None:
            self.outcomes.append(owner, handle=handle, evidence=handle,
                                 outcome={"status": "unmeasured", "reason": reason})
        return True

    def _settle_meta_unmeasured(self, meta_handle: str, reason: str) -> None:
        """A meta whose judge never produced a fact closes unmeasured, never at zero.

        PR #97 found the leak: a top meta that conformed to a verdict whose
        normative window closed unread waits in ``pending_meta`` for a payoff
        fact that will never settle, and times out at score zero for a fact the
        runtime owed it and never delivered. The commission is answered
        ``unmeasured`` instead.
        """
        # An unmeasured commission carries no fact about the meta, so it ends none
        # of its novelty trials: only resolved evidence is counted.
        channel = self.queue.get(meta_handle).channel
        self._settle_unmeasured(meta_handle, channel, reason)

    def _drain_pending_meta(self, judge_handle: str, reason: str) -> None:
        """Every meta waiting on this judge closes unmeasured, and the queue is emptied."""
        for meta_handle, _conformity in self.pending_meta.pop(judge_handle, []):
            self._settle_meta_unmeasured(meta_handle, reason)

    def _expire_pending_meta(self) -> None:
        """No meta waits forever for a judge that will never produce a fact.

        A judge's own decision is final when its window has closed and its
        payoff commitment, if it made one, has settled. Past the consequence
        backstop, a meta still waiting on it is waiting on nothing: it closes
        unmeasured while it can still be closed at all.
        """
        backstop = self.ev.consequence_backstop_ticks
        for judge_handle in list(self.pending_meta):
            waiting = [c for c in self.pending.values()
                       if c.channel == NORM_COMMITMENT and c.judge == judge_handle
                       and not c.verdict_closed]
            if waiting:
                continue
            opened = self.meta_waiting_since.get(judge_handle)
            if opened is None:
                self.meta_waiting_since[judge_handle] = self.ticks_consumed
                continue
            if self.ticks_consumed - opened > backstop:
                self._drain_pending_meta(
                    judge_handle, "the judged verdict produced no fact within its backstop")
                self.meta_waiting_since.pop(judge_handle, None)

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

    def _standing_for(self, evaluator_id: str) -> dict[str, Any] | None:
        """A judge's own consequence standing: skill against the prevalence baseline, sample
        size, selection weight. Its own running score, private to it."""
        st = self.standing.snapshot().get(evaluator_id)
        if not st or not (st.get("n") or st.get("verdict_n")):
            return None
        return {
            "skill_vs_baseline": round(float(st["skill"]), 4),
            "payoff_skill": round(float(st["payoff_skill"]), 4),
            "verdict_skill": round(float(st["verdict_skill"]), 4),
            "settled_forecasts": st["n"],
            "settled_verdicts": st["verdict_n"],
            "selection_weight": round(float(st["weight"]), 4),
        }

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

    def _deliver_grounded_finding_to_inbox(
        self, contract: GroundedContract, finding: dict[str, Any], *, judge_handle: str,
    ) -> None:
        """Address one final grounded finding to the producer that earned it.

        The item identifies itself as the final grounded assessment so it cannot
        be mistaken for the provisional verdict delivered when the contract was
        opened.  Unknown remains an explicit absence of score.  Using the final
        judge handle as the inbox evidence identity also makes replay idempotent.
        """
        owner = (self.handle_to_assembly.get(contract.handle)
                 or self.outcomes.seat_of(contract.handle))
        if owner is None:
            return self._undeliverable(
                "grounded_finding", contract.handle, "no seat owns that decision")
        score = finding.get("score")
        self.outcomes.append(
            owner,
            handle=contract.handle,
            evidence=judge_handle,
            outcome={
                "kind": "grounded_evaluation",
                "phase": "final",
                "judge_handle": judge_handle,
                "status": finding.get("status"),
                "score": round(float(score), 4) if score is not None else None,
                "evidence": list(finding.get("evidence") or ()),
                "reason": finding.get("reason"),
            },
        )

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
        """The reward line must reach the primitive that acted, not only its router (essay
        II.I.b: memory across rounds, reward attributable to the decision). A producer learns
        whether its return paid off; a judge learns whether the return it blessed paid off and
        how its verdict scored. Private to the seat, never public. Run 7 showed judges blessing
        inaction at 1.0 while their standing fell, because nothing ever told them.

        The deque this replaces delivered only while the decision was still among a
        seat's last three returns; an inbox item is addressed by handle and waits.
        """
        if s.predicate_id != "return_paid_off":
            # R3-F: a forecast on any other predicate settles too, and its
            # forecaster used to learn the result only through a standing number
            # nobody addressed. The seat that made the claim is told what happened
            # to it, on the decision that carried it.
            return self._deliver_forecast_to_inbox(s)
        event_id = self.queue.get(s.handle).event_id
        prefix = "verdict-" if event_id.startswith("verdict-") else "self-"
        # The producer's own payoff is not delivered here: it arrives with its money
        # in ``_credit_consequence``, which knows the net, the cost and whether the
        # outcome was marked. Delivering it twice would tell one seat one settlement
        # twice, which is exactly the confusion an addressed inbox exists to end.
        if not event_id.startswith(prefix):
            return
        if s.brier is None:
            # A censored settlement scored nothing: there is no brier to deliver,
            # and the return's owner is told the outcome is unknown instead.
            return
        forecaster_handle = event_id[len(prefix):]
        forecaster = (self.handle_to_assembly.get(forecaster_handle)
                      or self.outcomes.seat_of(forecaster_handle))
        if forecaster is None:
            return self._undeliverable("payoff_forecast", forecaster_handle,
                                       "no seat owns that decision")
        outcome = {"your_payoff_brier": round(float(s.brier), 4),
                   "baseline_brier": (round(float(s.baseline_brier), 4)
                                      if s.baseline_brier is not None else None)}
        if prefix == "verdict-":
            outcome["judged_return_paid_off"] = s.y
        self.outcomes.append(forecaster, handle=forecaster_handle, evidence=s.handle,
                             outcome=outcome)

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

    def _exposure_evidence(self, handle: str) -> dict[str, bool]:
        """The three facts that can expose a judge on this return, each False until it lands."""
        evidence = self.exposure_evidence.setdefault(handle, {})
        for fact in ("judge_failed", "self_beat", "verdict_exposed"):
            evidence.setdefault(fact, False)
        return evidence

    def _settle_exposures(self, settled: list[Any]) -> None:
        """An antagonist wins only for a real, attributable failure of the judge.

        Exposure settles 1 when the evaluated verdict's mandatory payoff forecast
        on the antagonist's return scored worse than the prevalence baseline and
        the antagonist's own payoff forecast on that return beat it (essay
        II.III.b: the failures have to be real), or when the judge's verdict
        endorsed the return (at or above ``VERDICT_ENDORSEMENT``) and the
        return's window blamed it. Optional forecasts never count. Without
        either by the time nothing about the return is pending, neither a payoff
        forecast nor a verdict, it settles 0.
        """
        for s in settled:
            if (s.predicate_id != RETURN_PAID_OFF.id or s.about_handle not in self.pending_exposure
                    or s.brier is None or s.baseline_brier is None):
                continue
            evidence = self._exposure_evidence(s.about_handle)
            if s.evaluator_id == self.handle_to_assembly.get(s.about_handle):
                evidence["self_beat"] |= s.brier > s.baseline_brier
            else:
                evidence["judge_failed"] |= s.brier < s.baseline_brier
            if (evidence["judge_failed"] and evidence["self_beat"]) or evidence["verdict_exposed"]:
                self._settle_exposure(s.about_handle, 1.0)
        waiting = {f.about_handle for f in self.book.pending()} | self._verdicts_waiting()
        stale = [
            h
            for h, o in self.pending_exposure.items()
            if self.ticks_consumed - o > self.ev.verdict_timeout_ticks and h not in waiting
        ]
        for h in stale:
            self._settle_exposure(h, 0.0)

    def _settle_exposure(self, handle: str, score: float) -> None:
        evidence = {"judge_failed": False, "self_beat": False, "verdict_exposed": False,
                    **self.exposure_evidence.pop(handle, {})}
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

    def _settle_meta_consequence(
        self, meta_handle: str, conformity: float, y: int, forecast_handle: str
    ) -> None:
        """A top meta's conformity is a probability that the verdict was right; it is graded
        by Brier against whether the verdict was right on both counts: its payoff forecast
        beat the prevalence baseline and the verdict itself beat the baseline of charter
        blame on the judged return."""
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
        self._book_consequence(owner, payoff.net_micro, "return_paid_off")

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
            self._book_consequence(owner, payoff.net_micro, "return_known_portion")

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

    def _book_consequence(self, owner: str, micro: int, reason: str) -> None:
        """Book a return's realised venue P&L to its owner as a venue-custody claim."""
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
            self._book_consequence(owner, micro, "late_consequence")
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
        """
        program = item.get("program") or item.get("service")
        service = item.get("service")
        owner = self.tool_owner.get(program)
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
        if receive is not None:
            receive(micro)
        self.ledger.append({"kind": "income.custody", "service": service, "micro": micro,
                            "tx": item.get("tx"), "custody": "base_reserve",
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
        self._commit_verdicts()
        settled = self.settler.settle_due(self.n, self._facts_for)
        settled.extend(self.settler.settle_consequences(self.consequences.payoff))
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
        # Which payoff forecasts this pass settled, so a verdict whose window closes
        # unread in the same pass waits for its payoff fact below.
        self._settle_due_verdicts(landing={result.handle for result in settled})
        self._expire_pending_meta()
        self._settle_exposures(settled)
        backstop = self.ev.consequence_backstop_ticks
        for judge_handle in [h for h, (_y, at, _f) in self.verdict_outcomes.items()
                             if self.ticks_consumed - at > backstop]:
            del self.verdict_outcomes[judge_handle]
        for s in settled:
            if s.predicate_id == RETURN_PAID_OFF.id and s.brier is None:
                # A censored payoff is no fact: a verdict already closed that was
                # waiting only on it is decided on what exists, or closes unmeasured.
                commitment = self.pending.get(s.handle)
                if (commitment is not None and commitment.channel == NORM_COMMITMENT
                        and commitment.verdict_closed):
                    self._finalize_verdict(commitment)
                    del self.pending[commitment.handle]
            if s.predicate_id == RETURN_PAID_OFF.id and s.brier is not None:
                event_id = self.queue.get(s.handle).event_id
                if event_id.startswith("verdict-"):
                    self._count_consequence(s.evaluator_id)
                    judge_handle = event_id[len("verdict-"):]
                    y = int(s.brier >= s.baseline_brier)
                    commitment = self.pending.get(s.handle)
                    if commitment is not None and commitment.channel == NORM_COMMITMENT:
                        commitment.payoff_beat = y
                        # A verdict right on both counts needs both; wrong on one is decided.
                        if y == 0 or commitment.verdict_closed:
                            self._finalize_verdict(commitment)
                        if commitment.verdict_closed:
                            del self.pending[commitment.handle]
                    else:
                        # No verdict was announced for this forecast: the payoff fact alone
                        # grades the metas, as before the verdict had its own anchor.
                        for meta_handle, conformity in self.pending_meta.pop(judge_handle, []):
                            self._settle_meta_consequence(meta_handle, conformity, y, s.handle)
                        self.verdict_outcomes[judge_handle] = (y, self.ticks_consumed, s.handle)
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
            self._deliver_consequence_to_inbox(s)
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

    def _commit_verdicts(self) -> None:
        """Every announced verdict is committed once, before any payoff about its return
        can settle, and never twice.

        A verdict is a prediction that the judged return will not be blamed by the
        charter. The commitment is keyed by the judge's payoff-forecast handle, so
        the book's pending kernel forecasts enumerate the verdicts still owed a
        commitment; the verdict itself is read from the public Verdict event the
        judge's decision announced. A subject marker under the judged return keeps
        its attribution window from being released before the verdict settles.
        """
        for forecast in self.book.pending(predicate_id=RETURN_PAID_OFF.id):
            if forecast.handle in self.pending:
                continue
            decision = self.queue.get(forecast.handle)
            if not decision.event_id.startswith("verdict-"):
                continue
            judge = decision.event_id[len("verdict-"):]
            if judge in self.verdicts_closed_out:
                # This verdict was already closed out. Its payoff forecast stays
                # pending in the book until the world settles it, and re-committing
                # it here would close it unread again on every following event.
                continue
            announced = self.return_events.get(judge)
            if (announced is None or announced.kind is not EventKind.VERDICT
                    or announced.payload.get("payoff_handle") != forecast.handle):
                continue
            q = _as_unit(announced.payload.get("verdict"))
            if q is None:
                continue
            about = forecast.about_handle
            opened, opened_tick = self._account_opened(about)
            cards = _CARDS_FOR_CHANNEL.get(self.queue.get(about).channel, "producer")
            emitted = self.return_kinds.get(about)
            if emitted and emitted not in ("ProducerReturn", "Verdict", "MetaVerdict", "Exposure"):
                cards = emitted
            # A return that never contributed to a window (a router noop) still waits
            # for the window it was made in, so no verdict settles before its window.
            window = self.price_origins.get(about, {}).get("origin", self.window.index)
            self.pending[forecast.handle] = PendingJudgement(
                forecast.handle, NORM_COMMITMENT, opened, about=about, judge=judge,
                evaluator_id=forecast.evaluator_id, q=q, cards=cards, window=window,
                opened_at_tick=opened_tick,
            )
            self.pending.setdefault(about, PendingJudgement(
                about, NORM_SUBJECT, self.n, opened_at_tick=self.ticks_consumed))

    def _commit_verdict_without_payoff(
        self, judge_handle: str, evaluator_id: str, about: str, verdict: float
    ) -> None:
        """Commit a verdict that sealed no payoff forecast, under a key of its own.

        Payoff is optional in edition 3's third round (§7), and a judge that
        made no payoff claim still made a claim: that the charter will not blame
        the return it judged. It waits under its own key — the judge's own
        decision handle already carries its conformity settlement — and it is
        decided by the normative fact alone, or by nothing at all.
        """
        key = f"{NORM_COMMITMENT}:{judge_handle}"
        if key in self.pending or judge_handle in self.verdicts_closed_out:
            return
        opened, opened_tick = self._account_opened(about)
        cards = _CARDS_FOR_CHANNEL.get(self.queue.get(about).channel, "producer")
        emitted = self.return_kinds.get(about)
        if emitted and emitted not in ("ProducerReturn", "Verdict", "MetaVerdict", "Exposure"):
            cards = emitted
        window = self.price_origins.get(about, {}).get("origin", self.window.index)
        self.pending[key] = PendingJudgement(
            key, NORM_COMMITMENT, opened, about=about, judge=judge_handle,
            evaluator_id=evaluator_id, q=float(verdict), cards=cards, window=window,
            awaits_payoff=False, opened_at_tick=opened_tick,
        )
        self.ledger.append({"kind": "verdict.committed_without_payoff", "handle": judge_handle,
                            "about_handle": about, "evaluator_id": evaluator_id,
                            "q": float(verdict), "ts": self.clock.now_ns})
        self.pending.setdefault(about, PendingJudgement(
            about, NORM_SUBJECT, self.n, opened_at_tick=self.ticks_consumed))

    def _tick_age(self, judgement: PendingJudgement) -> int:
        """World ticks consumed since this judgement opened."""
        return self.ticks_consumed - (judgement.opened_at_tick or 0)

    def _account_opened(self, about: str) -> tuple[int, int]:
        """The event and the world tick the judged return's account opened at.

        A verdict's consequence backstop runs from its subject's own opening, so
        it closes when the subject's outcome is fixed, not later.
        """
        try:
            account = self.consequences.table.account(about)
        except KeyError:
            return self.n, self.ticks_consumed
        tick = account.opened_at_tick
        return account.opened_at_event, self.ticks_consumed if tick is None else tick

    def _verdicts_waiting(self) -> set[str]:
        """The judged returns with a verdict whose window has not yet judged it."""
        return {p.about for p in self.pending.values()
                if p.channel == NORM_COMMITMENT and not p.verdict_closed}

    def _verdict_window(self, commitment: PendingJudgement) -> str:
        """Whether the judged return's window has closed: open, closed, or released."""
        window = self.price_windows.get(commitment.window)
        if window is None:
            return "released"
        return "closed" if window.closed_values is not None else "open"

    def _settle_due_verdicts(self, landing: frozenset[str] | set[str] = frozenset()) -> None:
        """A verdict settles once, when the judged return's window has closed, against the
        share of that window's charter blame the pricing pass attributed to the return.

        The realised normative outcome is 1 minus that share (1 when the window
        closed and attributed nothing to the return). Nothing is skipped under the
        cadence: the commitment is closed out at the consequence backstop whatever
        happened. But a window that never closed never judged the return, and
        attribution evidence released before it could be read is gone: there is no
        fact either way, so the verdict carries no information. It is closed out
        unscored — nothing enters the judge's standing, nothing enters the base
        rate of unblamed returns, and the metas that conformed to it are graded on
        the payoff fact alone (``Settler``: missing facts never become
        performance). Where the fact is real, the Brier enters the judge's
        standing beside payoff skill; a high verdict on a blamed return exposes
        the judge to the antagonist that made it; the judge is told, privately.

        ``landing`` names the payoff forecasts this same pass settled: an unread
        verdict whose payoff fact is among them is decided by it when it is
        attached, rather than closing unmeasured a moment before.
        """
        backstop = self.ev.consequence_backstop_ticks
        due = [p for p in self.pending.values()
               if p.channel == NORM_COMMITMENT and not p.verdict_closed]
        for c in due:
            state = self._verdict_window(c)
            if state == "open" and self._tick_age(c) < backstop:
                continue
            c.verdict_closed = True
            # Closed out once: the commitment pass may not re-open this judge.
            self.verdicts_closed_out.add(c.judge)
            if not (state == "closed" and c.about in self.price_origins):
                # No window judged this return: an unread fact is not a good verdict.
                self.ledger.append({
                    "kind": "verdict.unread", "handle": c.judge,
                    "forecast_handle": c.handle, "about_handle": c.about,
                    "evaluator_id": c.evaluator_id, "cards": c.cards, "q": c.q,
                    "window": c.window, "reason": state, "ts": self.clock.now_ns,
                })
                # §7: when the normative consequence is unreadable the verdict
                # is unmeasured, not scored on payoff. The judge's own payoff
                # forecast still settles on its own handle, against the world;
                # what it may not do is stand in for the charter's judgement.
                if c.awaits_payoff and c.payoff_beat is None and c.handle in landing:
                    # The payoff fact settled in this same pass (a return judged while
                    # its position was open resolves at the same backstop that closes
                    # this window unread): it decides the verdict below, rather than
                    # the verdict closing unmeasured a moment before it is attached.
                    continue
                self._finalize_verdict(c)
                del self.pending[c.handle]
                continue
            terms = self._penalty_terms(c.cards, c.about)
            total = sum(t["weight"] for t in terms)
            share = sum(t["weight"] * t["share"] for t in terms) / total if total > 0 else 0.0
            share = min(1.0, max(0.0, share))
            objection = self.settler.record_objection(
                c.judge, [self.outcomes.entry_for(c.judge)], self.charter,
                evaluator_id=c.evaluator_id, about_handle=c.about)  # W3 seam (C3 fidelity)
            if objection is not None:
                self._queue_adjudication(c)
            result = self.settler.settle_verdict(
                evaluator_id=c.evaluator_id, about_handle=c.about, q=c.q, share=share,
                judge_handle=c.judge,
            )
            c.verdict_beat = int(result.brier >= result.baseline_brier)
            seq = self.ledger.append({
                "kind": "verdict.consequence", "handle": c.judge,
                "forecast_handle": c.handle, "about_handle": c.about,
                "evaluator_id": c.evaluator_id, "cards": c.cards, "q": c.q,
                "window": c.window, "window_closed": True, "share": share,
                "outcome": result.outcome, "brier": result.brier,
                "baseline_brier": result.baseline_brier, "beat_baseline": c.verdict_beat,
                "terms": terms, "ts": self.clock.now_ns,
            })
            self.outcomes.append(
                c.evaluator_id, handle=c.judge, evidence=seq,
                outcome={"judged_return_blamed": round(float(share), 4),
                         "your_verdict_brier": round(float(result.brier), 4),
                         "verdict_baseline_brier": round(float(result.baseline_brier), 4),
                         "beat_baseline": c.verdict_beat})
            if c.about in self.pending_exposure:
                evidence = self._exposure_evidence(c.about)
                evidence["verdict_exposed"] |= c.q >= VERDICT_ENDORSEMENT and share > 0
                if evidence["verdict_exposed"]:
                    self._settle_exposure(c.about, 1.0)
            # A verdict right on both counts needs both facts; wrong on one is decided
            # now. A verdict that committed no payoff forecast has one fact, and this
            # is it: the commitment closes here rather than waiting for a settlement
            # nobody owes it.
            if c.verdict_beat == 0 or c.payoff_beat is not None or not c.awaits_payoff:
                self._finalize_verdict(c)
            if c.payoff_beat is not None or not c.awaits_payoff:
                del self.pending[c.handle]
        # A judged return's attribution evidence is released once every verdict on it settled.
        waiting = self._verdicts_waiting()
        for handle in [h for h, p in self.pending.items()
                       if p.channel == NORM_SUBJECT and h not in waiting]:
            del self.pending[handle]

    def _finalize_verdict(self, commitment: PendingJudgement) -> None:
        """Whether the verdict was right is decided once, on whatever facts exist.

        The top metas that conformed to it are graded on that decision, and a
        meta arriving later finds the same outcome. The commitment stays until
        every fact it is waiting for is in, so nothing commits or grades it twice.

        Edition 3's third round removes the payoff privilege here (§7). A verdict
        is right on the facts the world produced about it: its normative outcome,
        its payoff forecast, or both. A judge that made no payoff forecast is
        decided by its verdict alone, exactly as a judge whose window never
        judged the return is decided by its payoff alone. When the world
        produced neither, there is nothing to be right about, and the verdict
        and every meta that conformed to it close unmeasured rather than at zero.
        """
        if commitment.graded or commitment.judge in self.verdicts_graded:
            commitment.graded = True
            return
        commitment.graded = True
        self.verdicts_graded.add(commitment.judge)
        facts = [f for f in (commitment.payoff_beat, commitment.verdict_beat) if f is not None]
        if not facts:
            self.ledger.append({"kind": "verdict.unmeasured", "handle": commitment.judge,
                                "forecast_handle": commitment.handle,
                                "about_handle": commitment.about,
                                "evaluator_id": commitment.evaluator_id,
                                "reason": UNMEASURED_REASON_UNREAD, "ts": self.clock.now_ns})
            commitment.unmeasured = True
            self._drain_pending_meta(commitment.judge, UNMEASURED_REASON_UNREAD)
            self.meta_waiting_since.pop(commitment.judge, None)
            return
        y = int(all(bool(f) for f in facts))
        self.verdict_outcomes[commitment.judge] = (y, self.ticks_consumed, commitment.handle)
        for meta_handle, conformity in self.pending_meta.pop(commitment.judge, []):
            self._settle_meta_consequence(meta_handle, conformity, y, commitment.handle)
        self.meta_waiting_since.pop(commitment.judge, None)

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

    def _grounded_unknown(self, contract: GroundedContract, reason: str) -> None:
        """Close one unavailable consequence once, without fabricating a learner sample."""
        handle = contract.handle
        if handle in self.grounded_closed:
            return
        self.ledger.append({"kind": "consequence.unknown", "handle": handle,
                            "reason": reason, "ts": self.clock.now_ns})
        if self.queue.get(handle).status in (SettleStatus.PENDING, SettleStatus.TIMED_OUT):
            if contract.provisional_score is not None:
                # The world did not speak; the fast opinion stands so the decision
                # still teaches its learner. It is labelled apart from a grounded score.
                self._settle_priced(
                    handle, channel=CH_VERDICT, score=contract.provisional_score,
                    definition_version=f"{GROUNDED_DEFINITION}-provisional",
                    sampling_ref=contract.provisional_judge, cards="producer")
                self.stats.verdicts += 1
            else:
                self.queue.settle(handle, channel=CH_VERDICT, score=0.0,
                                  status=SettleStatus.CENSORED,
                                  definition_version=f"{GROUNDED_DEFINITION}-unknown",
                                  sampling_ref=None)
                self.stats.censored += 1
                self.window.outcomes += 1
                self.window.censored += 1
        self.pending.pop(handle, None)
        self.grounded_pending.pop(handle, None)
        self.grounded_closed.add(handle)

    def _settle_due_grounded(self) -> None:
        """Commission one fresh final judge at maturity, then bound unanswered contracts."""
        if getattr(self.ev, "producer_feedback", "verdict") != "realized":
            return
        for handle, contract in list(self.grounded_pending.items()):
            if handle in self.grounded_closed:
                self.grounded_pending.pop(handle, None)
                continue
            if self.ticks_consumed >= contract.close_tick:
                self._grounded_unknown(contract, UNKNOWN_REASON)
                continue
            if self.ticks_consumed < contract.due_tick or contract.final_requested:
                continue
            if self._settle_opportunity_cost(contract):
                continue
            evidence = public_evidence(self, contract)
            # What the evidence set is, and when it was taken. Frozen here, at
            # assembly, and carried unchanged in the emitted event: a judge that
            # cannot tell a complete record from a partial one reads absence as
            # refutation. A later commission for the same contract (a bounded
            # retry) assembles its evidence again and takes its own snapshot;
            # this one is never re-timed.
            evidence_snapshot = {
                "as_of_tick": self.ticks_consumed,
                "event_cursor": len(self.events_log),
                "receipt_cursor": self.consequences.receipts.execution_count(),
                "observation_due_tick": contract.due_tick,
                "assessment_timeout_tick": contract.close_tick,
                "scope": (
                    "Attributable public observations addressed to this contract since its "
                    "frozen baseline, as of as_of_tick. Not an exhaustive record of the "
                    "external world and not a proof that anything else did not happen."
                ),
            }
            excluded = {contract.producer_id}
            excluded.update(filter(None, (
                self.handle_to_assembly.get(ancestor)
                for ancestor in self._ancestry(contract.handle)
            )))
            excluded.update(contract.initial_evaluators)
            excluded.update(contract.final_evaluators)
            self.ledger.append({
                "kind": "consequence.final_requested", "handle": handle,
                "due_tick": contract.due_tick, "evidence_refs": [e["ref"] for e in evidence],
                "excluded_evaluators": sorted(excluded), "ts": self.clock.now_ns,
            })
            self.grounded_pending[handle] = contract.requested()
            self._emit(contract.subject_kind, {
                "about_handle": handle,
                "description": "Final independent evaluation of a frozen producer contract",
                "inputs": {"kind": "RealizedConsequence", "payload": {}},
                "outputs": contract.producer_outputs,
                "cost": 0,
                "status": "ok",
                "propensity": self._public_propensity(handle),
                "grounded_consequence": True,
                "excluded_evaluators": sorted(excluded),
                "contract": asdict(contract),
                "evidence": evidence,
                "evidence_snapshot": evidence_snapshot,
            })

    def _settle_opportunity_cost(self, contract: GroundedContract) -> bool:
        """Settle a decision that traded nothing on the trade it passed up; True if settled.

        Guarantees only an inaction answer with no venue operation, and a contract
        that froze its reference mids, is priced here; every other decision keeps
        its grounded-judge path. The score is the world's (``opportunity_cost``),
        not an opinion, so no final judge is commissioned or paid for it, and the
        producer is told what it passed up in its own inbox.
        """
        outputs = contract.producer_outputs if isinstance(contract.producer_outputs, dict) else {}
        action = str(outputs.get("action", "")).strip().lower()
        if action not in INACTION_ACTIONS or not contract.reference_mids:
            return False
        if self.executed_operations(contract.handle):
            return False
        fee = getattr(self.exchange, "fee_bps", None)
        try:
            fee_bps = Decimal(str(fee)) if fee is not None else DEFAULT_TAKER_FEE_BPS
        except (InvalidOperation, ValueError):
            fee_bps = DEFAULT_TAKER_FEE_BPS
        declined = declined_trade(outputs)
        priced = opportunity_cost(contract.reference_mids, latest_mids(self), 2 * fee_bps,
                                  declined)
        if priced is None:
            return False
        handle = contract.handle
        self.ledger.append({"kind": "consequence.opportunity", "handle": handle,
                            "horizon_ticks": contract.due_tick - contract.opened_tick,
                            **priced, "ts": self.clock.now_ns})
        if self.queue.get(handle).status in (SettleStatus.PENDING, SettleStatus.TIMED_OUT):
            self._settle_priced(handle, channel=CH_VERDICT, score=priced["score"],
                                definition_version=OPPORTUNITY_DEFINITION,
                                sampling_ref=None, cards="producer")
            self.stats.verdicts += 1
        if (declined is not None and contract.provisional_score is not None
                and contract.initial_evaluator):
            # The judge's verdict on this decision was a prediction the world has now
            # priced: graded from outside the loop it judged (essay II.III, fourth
            # principle), against an uninformed 0.5, so a judge that praises caution
            # the market punished loses standing and one that saw it gains.
            world, said = float(priced["score"]), float(contract.provisional_score)
            brier, baseline = (said - world) ** 2, (0.5 - world) ** 2
            self.standing.record_verdict(contract.initial_evaluator, brier, baseline)
            self.ledger.append({"kind": "verdict.opportunity", "handle": handle,
                                "evaluator_id": contract.initial_evaluator,
                                "judge_handle": contract.provisional_judge,
                                "verdict": said, "world_score": world,
                                "brier": round(brier, 6), "baseline_brier": round(baseline, 6),
                                "ts": self.clock.now_ns})
        owner = self.handle_to_assembly.get(handle) or self.outcomes.seat_of(handle)
        if owner is not None:
            self.outcomes.append(owner, handle=handle, evidence=f"opportunity:{handle}",
                                 outcome={"kind": "opportunity_cost", "phase": "final",
                                          "score": priced["score"],
                                          "declined": priced["declined"],
                                          "declined_net_bps": priced.get("declined_net_bps"),
                                          "moves": priced["moves"],
                                          "basis": priced["basis"],
                                          "round_trip_fee_bps": priced["round_trip_fee_bps"]})
        self.pending.pop(handle, None)
        self.grounded_pending.pop(handle, None)
        self.grounded_closed.add(handle)
        return True

    def _complete_grounded_evaluation(
        self, judge_handle: str, evaluator_id: str, about: str | None, ret: Any,
        evidence: Any, snapshot: Any = None,
    ) -> None:
        """Settle a producer only from a fresh judge's cited, supplied public evidence."""
        contract = self.grounded_pending.get(str(about))
        if contract is None or contract.handle in self.grounded_closed:
            self._settle_unmeasured(judge_handle, CH_CONFORMITY,
                                    "the grounded contract is no longer open")
            return
        excluded = {contract.producer_id}
        excluded.update(contract.initial_evaluators)
        excluded.update(contract.final_evaluators)
        excluded.update(filter(None, (
            self.handle_to_assembly.get(ancestor)
            for ancestor in self._ancestry(contract.handle)
        )))
        if evaluator_id in excluded:
            self._settle_unmeasured(judge_handle, CH_CONFORMITY,
                                    "grounded consequence needs a fresh independent evaluator")
            return
        refs = {row.get("ref") for row in evidence if isinstance(row, dict)
                and isinstance(row.get("ref"), str)} if isinstance(evidence, list) else set()
        raw = (ret.outputs.get("realized_consequence")
               if ret.status == "ok" and isinstance(ret.outputs, dict) else None)
        try:
            status, score, cited, reason = parse_finding(
                raw, refs, observed_evidence_refs(evidence))
        except ValueError as exc:
            self.ledger.append({"kind": "consequence.finding_refused", "handle": about,
                                "judge_handle": judge_handle, "reason": str(exc),
                                "ts": self.clock.now_ns})
            self._settle_unmeasured(judge_handle, CH_CONFORMITY, str(exc))
            if contract.final_attempts < 2 and self.ticks_consumed < contract.close_tick:
                self.grounded_pending[contract.handle] = contract.retry_after(evaluator_id)
                self.ledger.append({"kind": "consequence.final_retry",
                                    "handle": contract.handle,
                                    "attempt": contract.final_attempts + 1,
                                    "excluded_evaluator": evaluator_id,
                                    "ts": self.clock.now_ns})
            else:
                self._grounded_unknown(contract, str(exc))
            return
        self.ledger.append({
            "kind": "consequence.finding", "handle": contract.handle,
            "judge_handle": judge_handle, "evaluator_id": evaluator_id,
            "status": status, "score": score, "evidence": list(cited),
            "reason": reason, "ts": self.clock.now_ns,
        })
        finding = {"status": status, "score": score,
                   "evidence": list(cited), "reason": reason}
        self._deliver_grounded_finding_to_inbox(
            contract, finding, judge_handle=judge_handle)
        if score is None:
            self._grounded_unknown(contract, reason)
            # An unknown answered with facts in hand is still a claim, and it is
            # released for review so an evasive one can be answered where it counts.
            # The producer stays unmeasured either way. An unknown with nothing
            # supplied to read keeps §6.B's no-penalty answer.
            if evidence and self._release_grounded_finding(
                contract, judge_handle, evaluator_id, ret, evidence, snapshot, finding
            ):
                return
            self._settle_unmeasured(judge_handle, CH_CONFORMITY, reason)
            return
        if self.queue.get(contract.handle).status in (
            SettleStatus.PENDING, SettleStatus.TIMED_OUT
        ):
            self._settle_priced(
                contract.handle, channel=CH_VERDICT, score=score,
                definition_version=GROUNDED_DEFINITION, sampling_ref=judge_handle,
                cards="producer")
            self.stats.verdicts += 1
        self.pending.pop(contract.handle, None)
        self.grounded_pending.pop(contract.handle, None)
        self.grounded_closed.add(contract.handle)
        if not self._release_grounded_finding(
            contract, judge_handle, evaluator_id, ret, evidence, snapshot, finding
        ):
            self.queue.settle(judge_handle, channel=CH_CONFORMITY, score=0.0,
                              status=SettleStatus.SETTLED,
                              definition_version=DEF_CONFORMITY, sampling_ref=None)
            self.stats.conformities += 1
            self.window.outcomes += 1

    def _release_grounded_finding(
        self, contract: GroundedContract, judge_handle: str, evaluator_id: str, ret: Any,
        evidence: Any, snapshot: Any, finding: dict[str, Any],
    ) -> bool:
        """Release one final grounded finding for review, under a delayed outside fact.

        Guarantees the announced verdict carries the frozen facts, snapshot and
        contract the finding rests on, that its conformity stays open for a
        reviewer, and that the judge is committed under the ordinary normative key:
        it settles against the charter's later blame on the return it judged, never
        against its own number. That fact bears on the return, not on whether this
        reading of the evidence was right; nothing here validates the finding.
        Answers whether a verdict was stated at all.
        """
        verdict = _as_unit(ret.outputs.get("verdict"))
        if verdict is None:
            return False
        self.pending[judge_handle] = PendingJudgement(
            judge_handle, CH_CONFORMITY, self.n, opened_at_tick=self.ticks_consumed)
        self._commit_verdict_without_payoff(
            judge_handle, evaluator_id, contract.handle, verdict)
        self._emit(EventKind.VERDICT, {
            "about_handle": contract.handle,
            "evaluator_handle": judge_handle,
            "verdict": verdict,
            "payoff": None,
            "payoff_handle": None,
            "rationale": str(ret.outputs.get("rationale", ""))[:2000],
            "producer_outputs": contract.producer_outputs,
            "realized_finding": finding,
            "grounded_contract": asdict(contract),
            "grounded_evidence": evidence,
            "grounded_evidence_snapshot": snapshot,
            "propensity": self._public_propensity(judge_handle),
            "grounded_consequence": True,
        })
        return True

    def _censor_stale_judgements(self) -> None:
        stale = [
            p
            for p in self.pending.values()
            if p.channel not in (NORM_COMMITMENT, NORM_SUBJECT)  # settled by the window
            and p.handle not in self.grounded_pending
            and self._tick_age(p) > self.ev.verdict_timeout_ticks
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

        Ordinarily the evidence is the decision's first outcome, by the same rule
        the router follows (``_deliver_returns``): a score before its cutoff, or
        else nothing observed. Grounded producers instead keep their frozen round
        through a wall timeout because their horizon counts world ticks. Their
        final grounded score overrides that timeout; final unknown discards the
        round without imputation. Every round closes at most once.
        """
        for handle in list(self.assembly_rounds):
            decision = self.queue.get(handle)
            if decision.status is SettleStatus.PENDING or (
                decision.status is SettleStatus.TIMED_OUT
                and handle in self.grounded_pending
            ):
                continue
            history = self.queue.history(handle)
            grounded = next(
                (item for item in history
                 if item.definition_version.startswith(GROUNDED_DEFINITION)),
                None,
            )
            if (
                grounded is not None
                and grounded.definition_version == f"{GROUNDED_DEFINITION}-unknown"
            ):
                assembly_id = self.assembly_rounds.pop(handle, None)
                learner = self.assembly_learners.get(assembly_id)
                if learner is not None:
                    try:
                        learner.discard_for(handle)
                    except KeyError:
                        pass
                continue
            first = grounded or next(iter(history), None)
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
        if lr.status is SettleStatus.TIMED_OUT and lr.handle in self.grounded_pending:
            # Wall-clock expiry during an outage cannot become an anticipatory
            # sample for a contract whose horizon is counted in world ticks.
            return
        prop = decision.propensity
        keyed = isinstance(state.learner, _KeyedLearner)
        key = self.snapshot_keys.pop(lr.handle, None) if keyed else None
        if not self._router_sampled(decision):
            if key is not None:
                state.learner.inner.discard_for(key)
            return
        if (
            not lr.definition_version.startswith(GROUNDED_DEFINITION)
            and lr.status is not SettleStatus.TIMED_OUT
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
        elif lr.definition_version == f"{GROUNDED_DEFINITION}-unknown":
            reward = None
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
            # Priced when due, on the scales of every seat round learned by then.
            neutral = self._successor_state(drawer).neutral()
            fb = BanditFeedback(NOOP, neutral, prop.probs[prop.action_ids.index(NOOP)])
            self._apply_router_round(drawer, handle, credit["p"], credit["executed"], fb)

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
        """
        target = self._successor_state(drawer)
        prop = self.queue.get(handle).propensity
        logged = dict(zip(prop.action_ids, prop.probs, strict=True))
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
                                "action": fb.action, "reward": fb.reward,
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
        for state in list(self.retired_routers.values()):
            lid = state.learner.id
            if (
                not self.queue.outstanding(lid)
                and not self._router_owns_grounded_pending(lid)
                and not self._router_owed_abstention(lid)
            ):
                self.queue.retire_actor(lid)
                self.ledger.append({"kind": "router.drained", "learner_id": lid})
                del self.retired_routers[lid]
