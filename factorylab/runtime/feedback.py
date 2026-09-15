"""Runtime feedback method group."""

from __future__ import annotations

import random
from dataclasses import dataclass
from statistics import fmean
from typing import Any

from factorylab.kernel.events import Event, EventKind
from factorylab.kernel.queue import LearningReturn, SettleStatus
from factorylab.kernel.wallet import Infeasible
from factorylab.learners.base import BanditFeedback
from factorylab.runtime.cascade import CascadeGate, event_tier, release_threshold
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
    _to_plain,
)
from factorylab.runtime.summary import _as_unit
from factorylab.settlement import (
    Forecast,
    WindowFacts,
    brier,
    open_forecast_decision,
)
from factorylab.settlement.settle import PredicateForecast
from factorylab.settlement.vocabulary import RETURN_PAID_OFF

# A verdict is a prediction that the judged return will not be blamed by the charter. Its
# commitment waits in ``pending`` under the judge's payoff-forecast handle until the
# return's window closes; a subject marker under the judged return's handle keeps that
# window's attribution evidence alive until every verdict on the return has settled.
NORM_COMMITMENT = "verdict.norm"
NORM_SUBJECT = "verdict.subject"
# A verdict at or above this endorses the return; on a return the window blamed, it
# exposes the judge to the antagonist that made the return.
VERDICT_ENDORSEMENT = 0.8
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
    # The verdict is closed out once, informatively or not. ``verdict_beat`` stays None
    # when the window never judged the return: there is no fact, so there is no score.
    verdict_closed: bool = False
    verdict_beat: int | None = None  # the verdict beat the baseline, once scored
    graded: bool = False  # the metas that conformed to it have their outcome


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
                if not results or any(r.status is not SettleStatus.SETTLED for r in results):
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
                        self.verdict_outcomes[handle] = (y, self.n, forecasts[0])
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
            if self.n - o > self.ev.verdict_timeout_events and h not in waiting
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

        Profit credits the owning seat's entitlement, bounded by the pool so it
        classifies money the wallet has already booked and never mints. A loss
        debits the owner down to a floor of zero; what the seat cannot cover lands
        on the pool and is ledgered as such. A marked outcome is an estimate at the
        backstop, not settled money, and moves nothing here: what its lots realise
        later is booked to the owner as a late consequence (``_settle_late``).
        """
        owner = self.handle_to_assembly.get(payoff.handle)
        if payoff.marked or owner is None or owner not in self.assemblies:
            return
        self._book_consequence(owner, payoff.net_micro, "return_paid_off")

    def _book_consequence(self, owner: str, micro: int, reason: str) -> None:
        if micro > 0:
            self.budget.credit(owner, micro, reason)
        elif micro < 0:
            self.budget.charge(owner, -micro, reason)

    def _settle_late(self) -> None:
        """Money realised after an outcome was fixed still belongs to the return's owner.

        A position marked at the backstop and closed later is booked to the wallet
        by the venue; the opener's entitlement is charged (or credited) by the
        realised result, ledgered as a late consequence. The learning score of the
        marked outcome stays as it was; only the money moves.
        """
        for handle, micro in self.consequences.settle_late(self.n).items():
            owner = self.handle_to_assembly.get(handle)
            if owner is None or owner not in self.assemblies:
                continue
            self._book_consequence(owner, micro, "late_consequence")

    def _book_income(self, item: dict) -> None:
        """Earned x402 income is new money: it enters the root wallet and is the
        owning seat's (C11, C10).

        A paid call settled USDC to the reserve, so the wallet books the receipt
        like venue P&L (``income``) and ``unlocked`` rises by it; the seat that owns
        the service's program is credited from that new money, never from the
        pool, so an empty pool still pays the seller. A service whose program has
        no live owner (retired, or seeded without one) leaves the receipt in the
        pool. The receipt is also the economic consequence of the return that
        registered the service, while that outcome is open.
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

    def _settle_due_forecasts(self) -> None:
        self._settle_late()
        for payoff in self.consequences.resolve(self.n):
            self._credit_consequence(payoff)
            try:
                top_level = self.queue.get(payoff.handle).parent_handle is None
            except KeyError:
                top_level = True
            if top_level:  # continuations and children are not trials
                self._count_consequence(self.handle_to_assembly.get(payoff.handle))
        self._commit_verdicts()
        pending = {f.handle: f for f in self.book.pending()}
        settled = self.settler.settle_due(self.n, self._facts_for)
        settled.extend(self.settler.settle_consequences(self.consequences.payoff))
        for result in settled:
            parent = self.queue.get(result.handle).parent_handle
            if parent in self.forecast_returns and result.brier is not None:
                self.forecast_returns[parent]["results"][result.handle] = (
                    result.brier, result.baseline_brier)
        self._settle_forecast_returns()
        self._settle_due_verdicts()
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
            announced = self.return_events.get(judge)
            if (announced is None or announced.kind is not EventKind.VERDICT
                    or announced.payload.get("payoff_handle") != forecast.handle):
                continue
            q = _as_unit(announced.payload.get("verdict"))
            if q is None:
                continue
            about = forecast.about_handle
            try:
                opened = self.consequences.table.account(about).opened_at_event
            except KeyError:
                opened = self.n
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
            )
            self.pending.setdefault(about, PendingJudgement(about, NORM_SUBJECT, self.n))

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

    def _settle_due_verdicts(self) -> None:
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
        """
        backstop = self.ev.consequence_backstop_events
        due = [p for p in self.pending.values()
               if p.channel == NORM_COMMITMENT and not p.verdict_closed]
        for c in due:
            state = self._verdict_window(c)
            if state == "open" and self.n < c.opened_at_event + backstop:
                continue
            c.verdict_closed = True
            if not (state == "closed" and c.about in self.price_origins):
                # No window judged this return: an unread fact is not a good verdict.
                self.ledger.append({
                    "kind": "verdict.unread", "handle": c.judge,
                    "forecast_handle": c.handle, "about_handle": c.about,
                    "evaluator_id": c.evaluator_id, "cards": c.cards, "q": c.q,
                    "window": c.window, "reason": state, "ts": self.clock.now_ns,
                })
                if c.payoff_beat is not None:
                    self._finalize_verdict(c)
                    del self.pending[c.handle]
                continue
            terms = self._penalty_terms(c.cards, c.about)
            total = sum(t["weight"] for t in terms)
            share = sum(t["weight"] * t["share"] for t in terms) / total if total > 0 else 0.0
            share = min(1.0, max(0.0, share))
            result = self.settler.settle_verdict(
                evaluator_id=c.evaluator_id, about_handle=c.about, q=c.q, share=share,
            )
            c.verdict_beat = int(result.brier >= result.baseline_brier)
            self.ledger.append({
                "kind": "verdict.consequence", "handle": c.judge,
                "forecast_handle": c.handle, "about_handle": c.about,
                "evaluator_id": c.evaluator_id, "cards": c.cards, "q": c.q,
                "window": c.window, "window_closed": True, "share": share,
                "outcome": result.outcome, "brier": result.brier,
                "baseline_brier": result.baseline_brier, "beat_baseline": c.verdict_beat,
                "terms": terms, "ts": self.clock.now_ns,
            })
            for entry in self.memory.get(c.evaluator_id, ()):
                if entry["handle"] == c.judge:
                    entry["judged_return_blamed"] = round(float(share), 4)
                    entry["your_verdict_brier"] = round(float(result.brier), 4)
                    entry["verdict_baseline_brier"] = round(float(result.baseline_brier), 4)
            if c.about in self.pending_exposure:
                evidence = self._exposure_evidence(c.about)
                evidence["verdict_exposed"] |= c.q >= VERDICT_ENDORSEMENT and share > 0
                if evidence["verdict_exposed"]:
                    self._settle_exposure(c.about, 1.0)
            # A verdict right on both counts needs both facts; wrong on one is decided now.
            if c.verdict_beat == 0 or c.payoff_beat is not None:
                self._finalize_verdict(c)
            if c.payoff_beat is not None:
                del self.pending[c.handle]
        # A judged return's attribution evidence is released once every verdict on it settled.
        waiting = self._verdicts_waiting()
        for handle in [h for h, p in self.pending.items()
                       if p.channel == NORM_SUBJECT and h not in waiting]:
            del self.pending[handle]

    def _finalize_verdict(self, commitment: PendingJudgement) -> None:
        """Whether the verdict was right is decided once: the top metas that conformed to it
        are graded on it, and a meta arriving later finds the same outcome. The commitment
        stays until both facts are in, so nothing commits or grades it twice.

        A verdict the charter never judged has only one fact to be right about, so
        the payoff forecast alone decides it, exactly as it did before the verdict
        had a normative outcome of its own.
        """
        if commitment.graded:
            return
        commitment.graded = True
        y = int(bool(commitment.payoff_beat)
                and (commitment.verdict_beat is None or bool(commitment.verdict_beat)))
        self.verdict_outcomes[commitment.judge] = (y, self.n, commitment.handle)
        for meta_handle, conformity in self.pending_meta.pop(commitment.judge, []):
            self._settle_meta_consequence(meta_handle, conformity, y, commitment.handle)

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
            if p.channel not in (NORM_COMMITMENT, NORM_SUBJECT)  # settled by the window
            and self.n - p.opened_at_event > self.ev.verdict_timeout_events
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
        """Train an assembly's own learner from the reward that settled its decision.

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
                else:  # an action this router cannot hold: settle it where it can be held
                    self._settle_outside_universe(lr.handle, prop, fb)
            self.delivered_seen[lid] = len(returns)
            if lid in self.retired_routers and not self.queue.outstanding(lid):
                self.queue.retire_actor(lid)
                self.ledger.append({"kind": "router.drained", "learner_id": lid})
                del self.retired_routers[lid]
