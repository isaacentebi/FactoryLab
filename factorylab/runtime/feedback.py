"""Runtime feedback method group."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from math import ceil
from statistics import fmean
from typing import Any

from factorylab.kernel.events import Event, EventKind
from factorylab.kernel.queue import LearningReturn, SettleStatus
from factorylab.kernel.wallet import Infeasible
from factorylab.learners.base import NEUTRAL_REWARD, BanditFeedback
from factorylab.runtime.cascade import CascadeGate, event_tier, release_window
from factorylab.runtime.clockwork import deadline_ticks, tick_ns
from factorylab.runtime.grounded import (
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
    CH_COUNTER,
    CH_EXPOSURE,
    CH_FAST,
    CH_VERDICT,
    DEF_COUNTER,
    DEF_EVALUATION,
    DEF_EXPOSURE,
    DEF_VERDICT,
    MAX_FORECAST_HORIZON,
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
    EVENT_PREDICATE_IDS,
    RETURN_PAID_OFF,
    UNOBSERVABLE,
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
                      CH_EXPOSURE: "antagonist", CH_COUNTER: "adversary"}


def exposure_score(consequences: list[float], ordinary: list[float]) -> float:
    """An antagonist's reward: how much worse its judges did on its return than usual.

    Guarantees ``0.5 + 0.5 * (mean(ordinary) - mean(consequences))`` in [0, 1], with no
    clipping: ``consequences`` are the consequence scores (``consequence_score``, each in
    [0, 1]) of the judges scored on the antagonist's return, and ``ordinary`` each of
    those same judges' mean consequence score on ordinary returns, 0.5 (the base
    rate's score) for a judge the world has not yet scored on one. An antagonist
    whose judges did on its return exactly as they do elsewhere earns 0.5, and it
    earns more only when it made them miss more than they usually miss: a judge that
    is merely miscalibrated misses ordinary returns as much and pays nothing extra
    (the Wave 2 review, item 6). Essay II.III.b: the adversarial layer farms realized
    consequence; it is not paid for noise the judges carry everywhere.
    """
    return 0.5 + 0.5 * (fmean(ordinary) - fmean(consequences))


def counter_score(q: float, judged: float, y: float) -> float:
    """A counter-verdict's reward: how far its prediction beat the verdict it read.

    Guarantees ``0.5 + 0.5 * ((1 - (q - y)^2) - (1 - (judged - y)^2))`` in [0, 1] with
    no clipping. The verdict it read is fixed before the counter is made, so the
    term the counter controls is its own Brier score: the rule is proper, and a
    counter that repeats the verdict earns 0.5 whatever the world does. Essay
    II.III.b: an adversarial judge is paid by realized consequence, "a judgment of
    whether a given verdict predicted real downstream outcomes", and only when the
    judge it read was the one the world proved wrong.
    """
    return 0.5 + 0.5 * ((1 - (q - y) ** 2) - (1 - (judged - y) ** 2))


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


def composed_reward(verdict: float | None, credit: float | None,
                    use: float | None = None) -> float | None:
    """A requested child's reward: the equal mean of its verdict and its requester's score.

    Guarantees a value in [0, 1] when either signal exists, that signal alone when
    only one does, and None (the child settles censored) when neither does.
    ``verdict`` is the mean verdict of the child's own judges; ``credit`` is the
    settled score of the decision that requested the child and consumed its
    return, before that decision's own card penalty (``_settle_priced``'s raw
    score). This is the collaboration credit (Chapter II rulings §2, Composition:
    "the reward flows back to each executor it composed ... carried by the
    ordinary reward channel").

    Why this attribution. Essay II.I.b gives the reward channel three properties,
    and the credit keeps all three:

    * Thin: "the reward should always be a score". The credit is one number on
      the unit scale, delivered as part of the child's one settlement; no rich
      account of how the child helped travels back, so no learner forms a
      dependency on a requester's prose.
    * Delayed: "reward is always delayed, sometimes by many rounds". The child's
      handle waits for its requester's settlement, which itself waits for the
      requester's judges; credit never arrives before the requester is scored.
    * Addressed to the exact decision: it "must find its way back to the exact
      decision (and the exact propensity) that produced it". It settles the
      child's own handle, whose propensity is the request router's draw, so the
      router learns who to draw for this kind and the executor's own learner
      learns what it did; nothing is paid to the executor's other decisions.

    Why the requester's whole score, not a share of it. A score is a grade, not
    money: dividing it among the children a requester consumed would make one
    child's reward depend on how many siblings it had, which prices parallel
    composition by an architect's rule (II.I.a, Carroll: "robust designs are
    those that rely on weak claims"). The requester's outcome is the one fact
    the world gives about whether the composition paid; without a counterfactual
    per child there is no ground for splitting it, and a per-child judgement of
    "whether it helped" would be a new commissioned judge and a new request
    structure, which a thin reward channel does not carry.

    Why a mean with the child's own verdict, not a replacement. Ruling R1: a
    producer "learns from evaluator agents through the coupling of scoring and
    propensity". The verdict reads the child's work in a clean context, as a
    machine (II.I.b, after Yan); the requester's score says whether the work paid
    off to the one consumer that used it. The verdict alone pays nothing for
    collaboration; the credit alone lets a child ride on a requester that
    ignored it and drops the clean read. Linear and equal, as
    ``evaluation_reward`` argues: no product (a threshold by another name), no
    architect's weight, one scale (both are producer-scale verdict means), and
    nothing imputed ("missing facts never become performance").

    Why the raw score. Each decision answers for its own cards: the child's own
    penalty is subtracted from this reward when it settles, and subtracting the
    requester's too would charge one price twice.

    Who is never credited this way, and why. A requested judge cannot exist (a
    judging contract is not commissioned), and in any case the signal that grades
    an evaluator must sit outside the loop it judges (II.III.b). A forecast-shaped
    child is scored by a proper rule, which any added term would make improper.
    An exposure-shaped child is paid by its judges' error: paying an adversary its
    requester's success would make it the requester's collaborator. A child of
    the requester's own lineage, or ``self``, earns nothing extra: a lineage
    crediting its own request is one outcome paid twice (no self-dealing). The
    requester's own reward is untouched; the credit is a signal, not a transfer,
    so nothing is counted twice. Credit flows only from a requester that settled
    on a producer verdict or as a composed return: an exposure score, an
    evaluation reward or a Brier score is not a measure of whether work paid.

    A population tool is the same primitive. The decision that registered a tool
    is held for a bounded window, and ``use`` is the mean raw score of the
    other-lineage decisions that called the tool and settled on a verdict inside
    it: one signal, however many callers, so heavy use cannot drown the builder's
    own verdict, and the same equal mean applies. A decision that is both a
    requested child and a builder settles on the equal mean of all three.
    """
    signals = [s for s in (verdict, credit, use) if s is not None]
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
    # A requested child only (W4): the handle of the decision that requested and
    # consumed it, its judges' verdicts held until that decision settles, and the
    # collaboration credit it then carried (``composed_reward``). A judgement
    # checkpointed before these fields restores as an ordinary one.
    requester: str | None = None
    verdicts: list = field(default_factory=list)
    credit: float | None = None
    credit_closed: bool = False
    # A producer decision whose seat answered ``{"status": "cannot", "reason": ...}``:
    # the reason it gave. A judgement checkpointed before this field restores as an
    # ordinary one.
    declined: str | None = None

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

    def _cascade_priority(self, ev: Event) -> int:
        """1 for a verdict the world will never grade, so the tier above reads it first.

        Essay II.III.b: an evaluator is graded from above and by realized consequence.
        A verdict on a bare hold has no world outcome, so the tier above is its only
        grader; reading the window's world-graded verdicts first left those verdicts
        ungraded and paid the ones that avoided the world best (the #128 review:
        meta-only judge decisions 0.80 against world-scored 0.52). Guarantees 1 only
        for a first-tier verdict whose judged return executed nothing and named no
        declined trade; 0 otherwise.
        """
        if ev.kind is not EventKind.VERDICT or "tier" in ev.payload:
            return 0
        about = ev.payload.get("about_handle")
        if not isinstance(about, str) or about in self.reference_mids:
            return 0
        try:
            self.consequences.table.account(about)
        except KeyError:
            return 0
        return int(not self._acted(about))

    def _cascade_arrival(self, ev: Event) -> Event | None:
        """The representative one arrival releases upward, or None (``_cascade_releases``)."""
        released = self._cascade_releases(ev)
        return released[0] if released else None

    def _cascade_releases(self, ev: Event) -> list[Event]:
        """Ledger every arrival and release before changing buffers or routing upward.

        Separation is time and completed evidence (§6.C), counted in world ticks.
        A window's duration is drawn once, when it opens, as ``min_ratio`` times the
        measured period of the loop it gates (time audit T10): how long a return this
        tier judges takes to reach the scored outcome its judgement is evidence of
        (the producer's scored loop at tier one, the judges' at tier two, the metas'
        at tier three and above), never below one tick. The jitter is continuous and
        the tier's own (``clockwork.jitter_draw``); arrivals never shorten a window.

        A window that releases hands the tier above its representative and, beside
        it, the next completed arrivals by the same rank, until
        ``evaluation.meta_read_share`` of its completed evidence is released (at
        least the representative). Essay II.III: "evaluations of evaluations ...
        stacking to some arbitrary level"; one reading a window left most verdicts
        read by nobody above them (evaluations C7). Each carries the window it came
        from, so the tier above still reads it as a distribution (II.IV.c).        """
        from factorylab.runtime.clockwork import jitter_draw

        tier = event_tier(ev)
        gate = self.cascade.get(tier)
        now = self.ticks_consumed
        if gate is None:
            judged = {1: "producer", 2: "evaluator"}.get(tier, "meta")
            inner = self.clockwork.measured(f"scored:{judged}")
            loop = f"cascade:{tier}"
            count = self.clockwork.loops.get(loop, {}).get("fires", 0) + 1
            window = release_window(self.m.timing.min_ratio, self.m.timing.jitter_fraction,
                                    jitter_draw(self.clockwork.seed, loop, count), inner)
            self.clockwork.loops[loop] = {"opened": now, "due": now + ceil(window),
                                          "period": window, "inner": inner, "fires": count}
            gate = CascadeGate(window, opened=now)
        next_gate, released = gate.add(ev, now=now, complete=self._cascade_evidence_complete,
                                       priority=self._cascade_priority)
        self.ledger.append(
            {
                "kind": "cascade.arrival",
                "tier": tier,
                "event_id": ev.id,
                "window_ticks": gate.window,
                "opened_tick": gate.opened,
                "elapsed_ticks": gate.elapsed(now),
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
        if next_gate is None:
            self.cascade.pop(tier, None)
        else:
            self.cascade[tier] = next_gate
        # The meta reads the window as a distribution (essay II.IV.c) and grades each
        # arrival it was released. The window's other verdicts were not read and
        # borrow no grade (evaluations U2): each settles on its own signals.
        if released is None:
            return []
        return [released, *self._cascade_companions(gate, ev, released)]

    def _cascade_companions(self, gate: CascadeGate, ev: Event, released: Event) -> list[Event]:
        """The completed arrivals released beside a window's representative, best first.

        Guarantees at most ``ceil(meta_read_share * completed) - 1`` of them, ranked as
        the gate ranks its representative (``_cascade_priority``, then the latest),
        each with the representative's window evidence and a ``cascade.release``
        ledger item of its own.
        """
        from math import ceil

        arrivals = [*gate.arrivals, ev]
        finished = [(i, e) for i, e in enumerate(arrivals) if self._cascade_evidence_complete(e)]
        reads = max(1, ceil(self.ev.meta_read_share * len(finished)))
        ranked = sorted(finished, key=lambda item: (self._cascade_priority(item[1]), item[0]),
                        reverse=True)
        chosen = [e for _i, e in ranked if e.id != released.id][:reads - 1]
        companions = []
        for arrival in chosen:
            companion = replace(arrival, payload={**arrival.payload,
                                                  "window": released.payload["window"]})
            self.ledger.append({"kind": "cascade.release", "tier": event_tier(ev),
                                "event_id": arrival.id, "arrival_event_id": ev.id,
                                "companion_of": released.id,
                                "window": _to_plain(released.payload["window"]),
                                "ts": self.clock.now_ns})
            companions.append(companion)
        return companions

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
            if type(horizon) is not int or not 1 <= horizon <= MAX_FORECAST_HORIZON:
                continue
            params = dict(params, **{known[pid].horizon_param: horizon})
            try:
                from factorylab.settlement.vocabulary import _validate_params

                _validate_params(pid, params, predicate=known[pid])
                if pid in EVENT_PREDICATE_IDS:
                    from factorylab.runtime.polymarket import open_claim

                    # The claim's token is looked up now, as the sealing seat's own
                    # read, and its settlement read opens under the world's limit
                    # (``open_limit``); a refused claim is not sealed.
                    refused = open_claim(self, evaluator_id, params["token_id"],
                                         self.ticks_consumed + horizon)
                    if refused is not None:
                        self.ledger.append({"kind": "forecast.refused",
                                            "handle": evaluator_handle, "predicate": pid,
                                            "token_id": params["token_id"],
                                            "reason": refused, "ts": self.clock.now_ns})
                        self._refusal_to_owner(evaluator_handle, "forecast", refused,
                                               predicate=pid, token_id=params["token_id"])
                        continue
                fh = open_forecast_decision(
                    self.queue,
                    evaluator_id=evaluator_id,
                    event_id=f"forecast-{evaluator_handle}",
                    q=q,
                    # The horizon counts world ticks and the cutoff adds a ratio slack
                    # (time audit T3): converted here only because the queue shows it.
                    deadline_ns=self.clock.now_ns + deadline_ticks(
                        horizon, self.m.timing.min_ratio) * tick_ns(self.tick_clock),
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
                    self.ticks_consumed + horizon, **population))
            except (ValueError, KeyError):
                continue
            self.stats.forecasts_sealed += 1
            opened.append(fh)
        return opened

    def _settle_forecast_returns(self) -> None:
        """Guarantees a forecast-shaped invocation settles once, on all its resolved predictions.

        It earns their mean, or settles censored (priced when it left one of them
        avoidably unresolved). A decision that timed out first settles too (the
        queue keeps a late settlement's right): its cutoff and its forecasts'
        horizons both count ticks, so this happens only when a prediction's horizon
        outran the invocation's own. Its router was credited once, at the cutoff,
        and is not trained again; the late settlement is what puts the charter's
        price on the decision's record.
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

    def _carried_decline(self, handle: str) -> str | None:
        """The reason a still-open decision's seat declined it, or None.

        Guarantees every open decision that answered ``status: cannot`` and waits on
        a grade is found, whichever channel it waits on: a verdict-channel return
        (routed or requested) in ``pending``, an Exposure in ``declined_exposures``.
        Every other decline settles when it is answered and is never open.
        """
        pend = self.pending.get(handle)
        if pend is not None and pend.declined is not None:
            return pend.declined
        return self.declined_exposures.get(handle)

    def _settle_declined(self, handle: str, reason: str) -> bool:
        """Close one declined commission with no score, no price and no standing.

        A seat may decline paid judging work (§6.B), and a producer may decline the
        event it was woken for when no judge grades its refusal: the call it made is
        its only money cost, and its learners price the decline as an abstention
        (``_learn_router_return``, ``_close_assembly_round``). Declining is the
        seat's own choice, never a list the kernel keeps of what may be judged
        (evaluations S1). Nothing enters a standing, nothing
        enters a base rate, no card is blamed, and no money moves.

        Guarantees the settlement addresses the decision's own channel as the
        contract queue reports it: the kind a polymorphic decision selected (in a
        tool round, before it declined) when it selected one, so no caller can name
        a channel the queue refuses and abort the world.
        """
        definition = DECLINED_DEFINITION
        try:
            decision = self.queue.get(handle)
        except KeyError:
            return False
        status, channel = decision.status, decision.channel
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

    def _facts_for(self, f: Forecast, snapshots: dict | None = None) -> WindowFacts | None:
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
        if f.predicate_id in EVENT_PREDICATE_IDS:
            from factorylab.runtime.polymarket import event_facts

            # A Polymarket claim settles on the world's own read of its token now,
            # at settlement (essay II.III.b): the market's resolution or its price.
            # One snapshot a token a pass, so one question has one answer.
            event = event_facts(self, f.predicate_id, f.params["token_id"], snapshots,
                                due_tick=f.due_at_tick)
            if event is UNOBSERVABLE:
                return UNOBSERVABLE
            public["event"] = event
        events = tuple(self.events_log[start + 1 : self.n + 1])
        if f.predicate_id == "failure_within":
            public["independent_failures"] = self._independent_failures(f.evaluator_id, events)
        return WindowFacts(
            balance_at_forecast=self.balance_at[start],
            balance_at_settlement=self.wallet.balance,
            min_balance_in_window=min(window_balances) if window_balances else self.wallet.balance,
            events=events,
            **public,
        )

    def _independent_failures(self, forecaster: str, events: tuple) -> int:
        """Failures in ``events`` the forecaster's own lineage did not cause (``failure_within``).

        Guarantees a count of: chaos faults drawn for a tick (no seat's action draws
        them); chaos faults on calls by a seat of another lineage; OrderRejected events
        and liquidation fills whose order belongs to a decision of another lineage, or
        to no decision this world knows. Anything the forecaster's lineage caused is
        left out, so a forecast cannot manufacture its own outcome (the #132 review,
        item 4).
        """
        lineage = self.budget.lineage(forecaster)

        def own(seat: str | None) -> bool:
            return seat is not None and self.budget.lineage(seat) == lineage

        count = 0
        for event in events:
            for fault in event.get("faults", ()):
                if isinstance(fault, dict) and not own(fault.get("seat")):
                    count += 1
            kind, payload = event.get("kind"), event.get("payload") or {}
            liquidation = kind == EventKind.FILL and payload.get("liquidation") is True
            if kind == EventKind.ORDER_REJECTED or liquidation:
                handle = self._order_owner(payload.get("order_id"))
                if not own(self.handle_to_assembly.get(handle) if handle else None):
                    count += 1
        return count

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
        """One observed consequence delivered to an assembly ends one of its novelty trials.

        Callers count resolved evidence only: a censored payoff or an unmeasured
        commission told nobody anything about the seat, so it spends no trial.
        """
        if assembly_id is None:
            return
        delivered = self.stats.consequences_by_assembly.get(assembly_id, 0)
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
        # The world's reads of each Polymarket token, once for this whole pass: every
        # forecast due now on one token is graded against the same state of it.
        snapshots: dict = {}
        settled = self.settler.settle_due(self.n, lambda f: self._facts_for(f, snapshots),
                                          tick=self.ticks_consumed)
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
            opened = self.cadence.opened_at(s.handle, self.ticks_consumed)
            self.cadence.record(
                handle=s.handle,
                predicate_id=s.predicate_id,
                opened_event=opened,
                settled_event=self.ticks_consumed,
                opened_ns=self.queue.get(s.handle).opened_ns,
                settled_ns=self.clock.now_ns,
                status=str(s.status),
            )
            # The forecast loop a forecast card's samples come from (time audit T2).
            self.clockwork.record("forecast", max(0, self.ticks_consumed - opened))
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
            if self._held(pend):
                # A requested child's verdicts wait for its requester's settlement, and
                # a tool builder's for its tool-use window: it settles on all its
                # signals (``composed_reward``, ``_settle_composed``).
                pend.verdicts.extend(verdicts)
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
        closed from the start and it settles on the world's grade alone. Every path
        that opens an evaluation comes here, so this is where hindsight is refused at
        every tier: a judgement made when its target's consequence was already known
        (``_consequence_known``) is a reading of the answer, not a prediction, and the
        world never scores it (its consequence closes empty at once; the tier above
        may still grade it). A chosen target that is already known is refused earlier,
        by ``_hindsight_reason``; this also covers the delivered subject.
        """
        channel = self.queue.get(handle).channel
        rec = PendingJudgement(handle, channel, self.n, tier, opened_at_tick=self.ticks_consumed,
                               about=about, q=float(q), evaluator_id=evaluator_id,
                               grade_closed=channel == CH_FAST)
        self.pending[handle] = rec
        if self._consequence_known(about, tier):
            self.ledger.append({"kind": "evaluation.hindsight", "handle": handle,
                                "about_handle": about, "tier": tier,
                                "ts": self.clock.now_ns})
            self._close_consequence(handle, None, rec)
        return rec

    def _consequence_known(self, about: str, tier: int) -> bool:
        """Whether the world had already answered what a judgement of ``about`` predicts.

        Guarantees True for a judgement of an evaluator decision (tier above one) whose
        consequence score is closed, and for a first-tier judgement of a return that
        acted and whose payoff is fixed, or whose mark or final price was taken.
        """
        if tier > 1:
            return about in self.consequence_scores
        if about in self.marked_outcomes or (
                self.world_outcomes.get(about, {}).get("state") == "measured"):
            return True
        try:
            account = self.consequences.table.account(about)
        except KeyError:
            return False
        return account.payoff is not None and self._acted(about)

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

    def _horizon_reached(self, about: str, account: Any, frozen: dict | None,
                         ticks: int) -> bool:
        """Whether ``ticks`` world ticks have passed since the judged return opened."""
        opened = account.opened_at_tick
        if opened is None:
            opened = frozen["tick"] if frozen is not None else self.ticks_consumed
        return self.ticks_consumed >= opened + ticks

    def _price_declined(self, about: str, frozen: dict) -> dict[str, Any] | None:
        """The named declined trade priced from its frozen mids to the mids now (R2)."""
        return opportunity_cost(tuple(tuple(m) for m in frozen["mids"]), latest_mids(self),
                                self.ev.opportunity_scale_bps, frozen["declined"])

    def _final_outcome(self, about: str) -> tuple[str, float | None, str | None]:
        """The final measured outcome of a judged return: ``(state, y, kind)``.

        ``state`` is ``open`` while the world has not answered, ``none`` when it
        never will, and ``measured`` with ``y`` in [0, 1] and the kind of measurement
        (ruling R1):

        * a return that executed venue operations is measured by ``return_paid_off``,
          realized or marked P&L net of its compute and tool cost, as 0 or 1, once the
          consequence book fixes it (at its backstop at the latest);
        * a return that executed nothing and named the trade it declined is measured
          by that trade's gross move at the consequence backstop (ruling R2,
          ``opportunity_cost``), from the mids frozen when the return was made;
        * anything else (a bare hold) has no world outcome, and only the tier above
          grades a verdict about it.

        A measurement is taken once and kept, so every verdict about one return reads
        the same fact.
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
                return self._keep_outcome(self.world_outcomes, about, "none", None, None)
            return self._keep_outcome(self.world_outcomes, about, "measured",
                                      float(payoff.y), RETURN_PAID_OFF.id)
        frozen = self.reference_mids.get(about)
        if frozen is None:
            return self._keep_outcome(self.world_outcomes, about, "none", None, None)
        if not self._horizon_reached(about, account, frozen, self.ev.consequence_backstop_ticks):
            return "open", None, None
        priced = self._price_declined(about, frozen)
        self.reference_mids.pop(about, None)
        if priced is None:
            return self._keep_outcome(self.world_outcomes, about, "none", None, None)
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
                                          "gross_bps": priced["gross_bps"],
                                          "moves": priced["moves"]})
        return self._keep_outcome(self.world_outcomes, about, "measured",
                                  float(priced["score"]), OPPORTUNITY_DEFINITION)

    def _reward_outcome(self, about: str) -> tuple[str, float | None, str | None, str]:
        """The outcome a verdict's reward is scored on: ``(state, y, kind, phase)``.

        Anticipatory settlement (essay II.IV.b: an explorer is compensated sooner than
        the lifetime of what it found). The final measurement when the world has
        already fixed it (``phase`` ``final``); otherwise, once
        ``consequence_horizon_ticks`` have passed since the judged return opened, its
        mark (``phase`` ``mark``): an executed return's lots marked to the mids then,
        a declined trade priced to the mids then. The mark is taken once and kept, so
        every verdict about one return is rewarded on the same fact; the final
        measurement later updates standing and the base rate only.
        """
        state, y, kind = self._final_outcome(about)
        if state != "open":
            return state, y, kind, "final"
        known = self.marked_outcomes.get(about)
        if known is not None:
            return known["state"], known["y"], known["kind"], "mark"
        account = self.consequences.table.account(about)
        frozen = self.reference_mids.get(about)
        if not self._horizon_reached(about, account, frozen, self.ev.consequence_horizon_ticks):
            return "open", None, None, "mark"
        if self._acted(about):
            payoff = self.consequences.mark(about, self.n)
            if payoff is None:
                return "open", None, None, "mark"
            self._keep_outcome(self.marked_outcomes, about, "measured", float(payoff.y),
                               RETURN_PAID_OFF.id)
            self.ledger.append({"kind": "consequence.marked", "handle": about,
                                "y": payoff.y, "net_micro": payoff.net_micro,
                                "cost_micro": payoff.cost_micro, "ts": self.clock.now_ns})
            return "measured", float(payoff.y), RETURN_PAID_OFF.id, "mark"
        priced = self._price_declined(about, frozen)
        if priced is None:
            return "open", None, None, "mark"
        self.ledger.append({"kind": "consequence.opportunity_mark", "handle": about,
                            "horizon_ticks": self.ev.consequence_horizon_ticks,
                            **priced, "ts": self.clock.now_ns})
        self._keep_outcome(self.marked_outcomes, about, "measured", float(priced["score"]),
                           OPPORTUNITY_DEFINITION)
        return "measured", float(priced["score"]), OPPORTUNITY_DEFINITION, "mark"

    def _keep_outcome(self, kept: dict, about: str, state: str, y: float | None,
                      kind: str | None) -> tuple[str, float | None, str | None]:
        """Keep a measurement so every verdict about the return reads the same one."""
        kept[about] = {"state": state, "y": y, "kind": kind, "tick": self.ticks_consumed}
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

    def _score_verdict(self, rec: PendingJudgement, y: float, kind: str, phase: str) -> None:
        """Score one judge's verdict against its return's measured outcome (ruling R1).

        The verdict is a prediction: it is scored by Brier (higher is better,
        ``settle.normative_brier``) against y, beside the base rate of that kind of
        outcome before this return's own entered it. The consequence score is the
        judge's own reward on its second signal and the judge is told, privately. On
        a final measurement it also trains the judge's standing and the base rate; on
        a mark (anticipatory settlement) those wait for the final measurement, which
        is scored once more, late, and never re-settles the reward.
        """
        key = f"{VERDICT_BASE}{kind}"
        if phase == "final":
            result = self.settler.settle_verdict(
                evaluator_id=rec.evaluator_id, about_handle=rec.about, q=rec.q, outcome=y,
                key=key)
            brier, baseline = result.brier, result.baseline_brier
        else:
            brier, baseline = self.settler.score_verdict(about_handle=rec.about, q=rec.q,
                                                         outcome=y, key=key)
            self.late_verdicts[rec.handle] = {"about": rec.about, "q": rec.q,
                                              "evaluator_id": rec.evaluator_id,
                                              "tick": self.ticks_consumed}
        score = consequence_score(brier, baseline)
        seq = self.ledger.append({
            "kind": "verdict.consequence", "handle": rec.handle, "about_handle": rec.about,
            "evaluator_id": rec.evaluator_id, "q": rec.q, "y": y, "outcome": kind,
            "phase": phase, "brier": brier, "baseline_brier": baseline,
            "score": score, "ts": self.clock.now_ns,
        })
        self.outcomes.append(rec.evaluator_id, handle=rec.handle, evidence=seq,
                             outcome={"judged_outcome": kind, "judged_y": round(y, 4),
                                      "phase": phase,
                                      "your_verdict_brier": round(brier, 4),
                                      "baseline_brier": round(baseline, 4),
                                      "consequence_score": round(score, 4)})
        self._count_consequence(rec.evaluator_id)
        if rec.about in self.pending_exposure:
            self.exposure_scores.setdefault(rec.about, []).append([rec.evaluator_id, score])
        else:
            # What this judge scores on an ordinary return: the centre an exposure on
            # its verdicts is measured from (``exposure_score``).
            tally = self.judge_ordinary.setdefault(rec.evaluator_id, [0.0, 0])
            tally[0] += score
            tally[1] += 1
        self._close_consequence(rec.handle, score, rec)

    def _settle_late_verdicts(self) -> None:
        """A verdict rewarded on a mark is scored on the final measurement, late.

        Guarantees the final measurement trains the judge's standing and the base
        rate once, against the same pre-outcome base rate the mark was scored on,
        and never settles the judge's reward a second time (no double scoring).
        """
        for handle, late in sorted(self.late_verdicts.items()):
            state, y, kind = self._final_outcome(late["about"])
            if state == "open":
                continue
            del self.late_verdicts[handle]
            if state != "measured":
                continue
            result = self.settler.settle_verdict(
                evaluator_id=late["evaluator_id"], about_handle=late["about"], q=late["q"],
                outcome=y, key=f"{VERDICT_BASE}{kind}")
            self.ledger.append({
                "kind": "verdict.consequence_late", "handle": handle,
                "about_handle": late["about"], "evaluator_id": late["evaluator_id"],
                "q": late["q"], "y": y, "outcome": kind, "brier": result.brier,
                "baseline_brier": result.baseline_brier, "ts": self.clock.now_ns})
            self.outcomes.append(late["evaluator_id"], handle=handle,
                                 evidence=f"late:{handle}",
                                 outcome={"judged_outcome": kind, "judged_y": round(y, 4),
                                          "phase": "final",
                                          "your_verdict_brier": round(result.brier, 4),
                                          "baseline_brier": round(result.baseline_brier, 4)})

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
                state, y, kind, phase = self._reward_outcome(rec.about)
                if state == "measured":
                    self._score_verdict(rec, y, kind, phase)
                    continue
                if state == "none":
                    self._close_consequence(rec.handle, None, rec)
                    continue
            if self._tick_age(rec) > backstop + timeout:
                self._close_consequence(rec.handle, None, rec)
        self._settle_late_verdicts()
        self._settle_exposures()
        self._settle_counters()
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
        for kept in (self.consequence_scores, self.world_outcomes, self.reference_mids,
                     self.marked_outcomes, self.late_verdicts, self.verdict_views):
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

    def _ordinary_consequence(self, judge: str | None) -> float:
        """A judge's mean consequence score on ordinary returns; 0.5 before its first."""
        total, count = self.judge_ordinary.get(judge, (0.0, 0)) if judge else (0.0, 0)
        return total / count if count else 0.5

    def _settle_exposures(self) -> None:
        """An antagonist earns by how much worse than usual the judges' verdicts on it were.

        Guarantees a continuous reward, only where the world measured the return:
        ``exposure_score`` of the judges' consequence scores on it, centred on those
        same judges' scores on ordinary returns (the Wave 2 review, item 6), so an
        antagonist earns above 0.5 only when it made its judges miss more than they
        miss elsewhere (II.III.b: the adversarial layer farms realized consequence;
        evaluations S4 removed the fixed endorsement threshold). A return no judge
        was scored on settles censored once no judge is still waiting on it and its
        verdict window has passed, or declined when its seat answered ``cannot``.
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
            declined = self.declined_exposures.pop(handle, None)
            if self.queue.get(handle).status not in (SettleStatus.PENDING,
                                                     SettleStatus.TIMED_OUT):
                continue
            if not scores and declined is not None:
                # A refusal no judge was scored on is an abstention, priced as one
                # (ruling R9), never censored at a free neutral.
                self.ledger.append({"kind": "exposure.settled", "handle": handle,
                                    "score": None, "declined": True, "ts": self.clock.now_ns})
                self._settle_declined(handle, declined)
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
            scored = [entry if isinstance(entry, list) else [None, entry] for entry in scores]
            consequences = [float(s) for _judge, s in scored]
            ordinary = [self._ordinary_consequence(judge) for judge, _s in scored]
            score = exposure_score(consequences, ordinary)
            self.ledger.append({"kind": "exposure.settled", "handle": handle, "score": score,
                                "judge_consequences": consequences,
                                "judge_ordinary": ordinary, "ts": self.clock.now_ns})
            self._settle_priced(handle, channel=CH_EXPOSURE, score=score,
                                definition_version=DEF_EXPOSURE, sampling_ref=None,
                                cards="antagonist")
            self.stats.exposures_settled += 1
            self.window.exposures_settled += 1
            # Won: the judges predicted the return worse than its base rate did.
            if score > 0.5:
                self.stats.exposures_won += 1
                self.window.exposures_won += 1

    def _world_will_measure(self, ev: Event) -> bool:
        """Whether a first-tier verdict judged a return the world has yet to measure.

        Guarantees True only for a Verdict (no tier) on a return that acted or named a
        declined trade (``_final_outcome``'s two measurements) and whose outcome is not
        already known; a bare hold has no world outcome at all.
        """
        if ev.kind is not EventKind.VERDICT or "tier" in ev.payload:
            return False
        about = ev.payload.get("about_handle")
        if not isinstance(about, str) or self._consequence_known(about, 1):
            return False
        return about in self.reference_mids or self._acted(about)

    def _early_warning_view(self) -> dict[str, Any]:
        """The early-warning table an evaluator is shown (``runtime.ews``; ruling R3).

        Guarantees the last window close's statistics and nothing else: it goes to
        the seats that judge (judges, metas, adversarial judges), never into a
        producer's request (evaluations M2).
        """
        from factorylab.runtime import ews

        return ews.view(self.stats.early_warning)

    def _settle_counters(self) -> None:
        """Settle every counter-verdict whose return the world has measured (``counter_score``).

        Guarantees the measurement is the one every verdict about that return is
        rewarded on (``_reward_outcome``: its mark at the consequence horizon, or its
        final measurement), so the counter and the verdict it read face one fact; a
        return the world will never measure (a bare hold) or has not measured by the
        consequence backstop settles the counter censored. A counter never touches
        the judge's reward, the producer's or any standing: it is paid for exposing a
        miss, not for grading anyone.
        """
        backstop = self.ev.consequence_backstop_ticks + self.ev.verdict_timeout_ticks
        for handle, rec in sorted(self.pending_counters.items()):
            state, y, kind, phase = self._reward_outcome(rec["about"])
            if state == "open" and self.ticks_consumed - rec["tick"] <= backstop:
                continue
            del self.pending_counters[handle]
            try:
                status = self.queue.get(handle).status
            except KeyError:
                continue
            if status not in (SettleStatus.PENDING, SettleStatus.TIMED_OUT):
                continue
            if state != "measured":
                self.ledger.append({"kind": "counter.settled", "handle": handle,
                                    "about_handle": rec["about"], "score": None,
                                    "ts": self.clock.now_ns})
                self.queue.settle(handle, channel=CH_COUNTER, score=0.0,
                                  status=SettleStatus.CENSORED,
                                  definition_version=DEF_COUNTER, sampling_ref=None)
                self.stats.censored += 1
                self.window.outcomes += 1
                self.window.censored += 1
                continue
            score = counter_score(rec["q"], rec["judge_q"], y)
            seq = self.ledger.append({
                "kind": "counter.settled", "handle": handle, "about_handle": rec["about"],
                "judge_handle": rec["judge_handle"], "q": rec["q"], "judge_q": rec["judge_q"],
                "y": y, "outcome": kind, "phase": phase, "score": score,
                "ts": self.clock.now_ns})
            self.outcomes.append(rec["evaluator_id"], handle=handle, evidence=seq,
                                 outcome={"judged_outcome": kind, "judged_y": round(y, 4),
                                          "phase": phase, "counter_score": round(score, 4)})
            self._settle_priced(handle, channel=CH_COUNTER, score=score,
                                definition_version=DEF_COUNTER, sampling_ref=None,
                                cards="adversary")
            self.stats.counters_settled += 1

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
        ``evaluation.sampling_step``, capped at ``evaluation.sampling_cap``; without
        divergence it steps back toward the manifest's ``consequence_share``. Every
        change is a ledger item.

        Every closed window is read; the mix moves only on the actuator's own loop
        (time audit T1, T2): at least ``min_ratio`` measured consequence periods
        apart, with its own jitter, because a mix change returns as forecast skill
        only when the consequences it selected for have settled.
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
        now, inner = self.ticks_consumed, self.cadence.consequence_period_events()
        if not self.clockwork.due("sampling", now, inner):
            return
        self._ledger_loop("sampling", self.clockwork.fire("sampling", now, inner),
                          inner_loop="consequence")
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

    def _held(self, pend: PendingJudgement) -> bool:
        """Whether a verdict-channel decision waits on a credit beside its verdict (W4)."""
        return not pend.evaluation and (pend.requester is not None
                                        or pend.handle in getattr(self, "tool_holds", {}))

    def _censor_stale_judgements(self) -> None:
        """A decision nobody judged within the verdict timeout settles censored.

        A refusal nobody judged settles declined instead (``_settle_declined``), so
        its learners credit it as an abstention, less its role's price. Evaluator
        decisions are not here: they close on their own two signals
        (``_settle_evaluations``).
        """
        # A requested child is not stale while it waits for its requester: it
        # settles through ``_settle_composed``, on whatever of its two signals came.
        stale = [
            p
            for p in self.pending.values()
            if not p.evaluation and not self._held(p)
            and self._tick_age(p) > self.ev.verdict_timeout_ticks
        ]
        for p in stale:
            if p.declined is not None:
                # A refusal no judge graded is a seat choosing to do nothing with the
                # work it was woken for: it settles as a declined commission, so its
                # learners are credited as an abstention is, at the zero-consequence
                # reward less the charter price of its role (ruling R9). Censored, it
                # was credited that reward unpriced, and declining escaped the price
                # a NOOP draw and a judged hold both bear (essay II.I.a: selection
                # moves share only where abstaining is not free).
                self._settle_declined(p.handle, p.declined)
            elif self.queue.get(p.handle).status is SettleStatus.PENDING:
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
        inapplicable, or past its cutoff) is credited zero consequence, never the
        action's own long-run mean (time audit T4), less the price its settlement
        carries, as the router's are; a declined one is credited as an abstention.
        """
        assembly_id = self.assembly_rounds.pop(handle, None)
        if assembly_id is None:
            return
        learner = self.assembly_learners.get(assembly_id)
        if learner is None:
            return
        declared = self.queue.declared_propensity(handle)
        imputed = reward is None
        if (declared is not None and reward is None and priced is not None
                and priced.definition_version == DECLINED_DEFINITION):
            # Declining is priced for the seat's own learner as for its router
            # (``_learn_router_return``): an abstention's credit, never a free neutral.
            reward, _penalty = self._priced_abstention(handle, NEUTRAL_REWARD)
        elif declared is not None and reward is None:
            reward = _priced(NEUTRAL_REWARD, priced)
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

        A request for a kind of work is drawn by that kind's request router, with
        the propensity it sampled, so the router learns composition from the
        child's settlement (primitive audit F5). A ``self`` request is opened under
        its parent's router so its score has an addressable home, but the parent
        chose itself: its propensity of 1.0 is not a probability any router
        sampled, and a router trained on it would learn from a round it never
        played.
        """
        from factorylab.runtime.shared import is_request_router

        prop = decision.propensity
        return ((decision.parent_handle is None or is_request_router(decision.actor))
                and prop.source == "sampled" and prop.learner_state_hash != "parent-selected")

    def _learn_router_return(self, state: Any, lr: LearningReturn) -> None:
        """Train a router once per decision it drew, on the evidence that decision has.

        The rule (defects 2 and 4): a decision's cutoff is its tick cutoff (its own
        horizon plus a ratio slack, time audit T3). Its first outcome is its one
        update. A score that settled it before the cutoff is observed and trains the
        router at that score. A decision that closed without an observed score
        (censored, inapplicable) or reached its cutoff unscored (timed out) is not a
        zero, and it is not the arm's own long-run mean either: a population paid
        long-run averages "ceases to produce variation" (essay II.IV.b; time audit
        T4). It is credited the router's zero-consequence reward (``RouterState.
        neutral``: what a woken seat that delivered nothing scores), less the price
        its settlement carries. A score that arrives after the cutoff still settles
        the decision for the kernel -- its money, its standing, its history -- but
        trains no learner a second time.

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
        elif lr.definition_version == DECLINED_DEFINITION:
            # A declined commission is a seat choosing to do nothing with work it was
            # handed: credited like an abstention, the zero-consequence reward less the
            # card penalty of its role, never its own mean (the #128 review: judges
            # otherwise earned more by avoiding the world than by facing it).
            reward, penalty = self._priced_abstention(lr.handle, target.neutral())
            self.ledger.append({"kind": "router.decline_priced", "handle": lr.handle,
                                "router": state.learner.id, "neutral": target.neutral(),
                                "penalty": penalty, "reward": reward,
                                "ts": self.clock.now_ns})
        else:
            # The router's zero-consequence baseline on the live successor, never the
            # arm's own mean (time audit T4), less any charter price its settlement
            # carries (routers-learn + charter-price-bites).
            reward = _priced(target.neutral(), lr)
        if reward is None:
            if key is not None:
                state.learner.inner.discard_for(key)
            return
        if keyed and key is None:
            return  # its frozen round is already spent: nothing trains, nothing is booked
        charged = self._thrash_charged(state, lr.handle, reward)
        fb = BanditFeedback(prop.chosen, charged, prop.probs[prop.action_ids.index(prop.chosen)])
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
        opened = self.queue.opened_tick(lr.handle)
        if opened is not None:
            # The router's own loop, in world ticks (time audit T3): the delay its
            # abstentions wait and the period its epochs and gain steps respect (T6).
            ticks = max(0, self.ticks_consumed - opened)
            target.latency[0] += ticks
            target.latency[1] += 1
            self.clockwork.record(f"router:{state.kind}", ticks)
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
        propensity, no earlier than its open tick plus the mean delay, in world
        ticks, the router's learned seat rounds took (its tick cutoff while there is
        none), and that a swap router's frozen round is detached now and survives a
        checkpoint in ``noop_credits``.
        """
        p, executed = state.learner.inner.take_for(key) if key is not None else (None, None)
        total, count = self._successor_state(state).latency
        opened = self.queue.opened_tick(decision.handle)
        opened = self.ticks_consumed if opened is None else opened
        cutoff = self.queue.deadline_tick(decision.handle)
        due = (opened + -(-total // count) if count
               else cutoff if cutoff is not None else self.ticks_consumed)
        self.noop_credits[decision.handle] = {"router": state.learner.id, "due_tick": due,
                                              "p": p, "executed": executed}
        self._credit_abstentions()

    def _credit_abstentions(self) -> None:
        """Apply every owed abstention credit that is due, in the order it was owed."""
        now = self.clock.now_ns
        routers = {st.learner.id: st
                   for st in self._all_router_states() + list(self.retired_routers.values())}
        for handle, credit in list(self.noop_credits.items()):
            # A credit owed before the tick clock keeps the wall-clock due it was owed at.
            if (credit["due_ns"] > now if "due_tick" not in credit
                    else credit["due_tick"] > self.ticks_consumed):
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
            # Ruling R9: waking nobody bears the thrash price a woken round of the core
            # would, so abstaining is never the way out of paying for thrash.
            reward = self._thrash_charged(drawer, handle, reward)
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
        An abstention drawn before its window recorded it is credited unpriced. A
        declined commission is priced the same way, on the role its seat was
        measured in when it answered. Returns (reward, penalty).
        """
        origin = self.price_origins.get(handle, {}).get("origin")
        window = self.price_windows.get(origin)
        sample = window.decisions.get(handle) if window is not None else None
        if sample is None:
            return neutral, 0.0
        roles = sample.get("menu_roles") or {sample["role"]: 1.0}
        # Each role's price is measured with the abstention scoped in that role (the
        # Wave 2 review, item 8b): a less-weighted role's floor and attribution are
        # that role's, never the role the window filed the abstention under.
        penalty = sum(weight * self._penalty_for(role, handle, as_role=role)
                      for role, weight in sorted(roles.items()))
        return min(1.0, max(0.0, neutral - penalty)), penalty

    def _thrash_charged(self, state: Any, handle: str, reward: float) -> float:
        """A no-swap-regret router's reward, less the thrash charge on its own movement.

        Essay II.II.b: "in the case of thrash, one should penalize the duration of
        spectral-gap volatility, incentivizing the surplus-retaining core of
        no-swap-regret learners to stabilize" (versioning audit C2). A charge every
        round bore alike would be a constant shift a no-regret learner ignores (the
        #134 review), so each round is charged ``c = min(prices.penalty_cap, lambda *
        m)``: the thrash price in force when the round was drawn times the router's
        own policy movement at that draw (``RoutingMixin._record_movement``, the TV
        from its previous draw). A router that holds its policy still is charged
        nothing; abstentions are charged the same way (ruling R9).

        Guarantees the charged reward is ``(r + cap - c) / (1 + cap)`` for every round
        of a core router, charged or not: one affine map, so no clip at 0 lets a
        low-reward arm escape part of its charge and an uncharged round sits on the
        same scale as a charged one. Rounds of other routers are untouched. A charge
        is ledgered (``thrash.charged``).
        """
        if state.kind not in self.m.evaluation.no_swap_regret_kinds:
            return reward
        cap = self.m.prices.penalty_cap
        charge = self.thrash_charges.pop(handle, 0.0)
        charged = (reward + cap - charge) / (1.0 + cap)
        if charge > 0:
            self.ledger.append({"kind": "thrash.charged", "handle": handle,
                                "router": state.learner.id, "charge": charge,
                                "reward_before": reward, "reward": charged,
                                "ts": self.clock.now_ns})
        return charged

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
