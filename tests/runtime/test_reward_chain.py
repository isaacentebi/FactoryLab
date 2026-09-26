"""The reward chain (ruling R1; essay II.III.b).

Producers learn from their judges' verdicts. A verdict is also a prediction: the
world scores it against the judged return's measured outcome, and that score is
part of the judge's own reward beside the grade the tier above gives it. Metas are
graded by the world one tier up, antagonists earn by how wrong the judges were,
and a judgement the kernel cannot use is censored at its cost, never graded zero.
"""

from __future__ import annotations

import random
from collections import deque
from math import ceil
from types import SimpleNamespace

import pytest

from factorylab.cortex.request import Return
from factorylab.kernel.events import Event, EventKind
from factorylab.kernel.queue import PropensityRecord, SettleStatus
from factorylab.runtime.feedback import consequence_score, evaluation_reward
from factorylab.runtime.grounded import opportunity_cost
from factorylab.runtime.shared import CH_CONFORMITY, CH_EXPOSURE, CH_FAST
from factorylab.world.scripted import ScriptedProvider
from tests.runtime.test_loop import (
    _consequence_decision,
    _consequence_produce,
    _consequence_runtime,
    _pending_meta,
    _recursive_runtime,
    lists_nothing,
)


def _learned(rt, r, p, *, router=True):
    """Ruling R10-l: every learner learns (r + B - P) / (1 + B), no clip, B = 2 * cap for
    a router (card share plus thrash) and cap for a seat's own learner."""
    bound = rt.m.prices.penalty_cap * (2 if router else 1)
    return (r + bound - p) / (1 + bound)


class Population(ScriptedProvider):
    """Producers hold (naming a declined trade when told to); judges give queued verdicts."""

    def __init__(self, *, counterfactual=None, verdicts=(), conformity=0.8):
        super().__init__()
        self.counterfactual = counterfactual
        self.verdicts = deque(verdicts)
        self.conformity = conformity

    def _produce(self, desc, inputs):
        reply = {"action": "hold", "rationale": "no edge"}
        if self.counterfactual is not None:
            reply["counterfactual"] = dict(self.counterfactual)
        return reply

    def _evaluate(self, req, inputs):
        return {"verdict": self.verdicts.popleft(), "rationale": "scripted", "forecasts": []}

    def _meta(self, inputs):
        return {"conformity": self.conformity, "rationale": "scripted meta"}


def _mids(rt, **prices):
    """Broadcast ``prices`` as the world's latest mids at the world's clock, as a
    MarketMid event does: the venue's fee schedule is read at the first broadcast
    (wave 16, D1), and every named trade due by now is measured (D2)."""
    if rt._fee_schedule_due():
        rt._read_fee_schedule()
    for coin, mid in prices.items():
        rt.recent_mids.setdefault(coin, deque(maxlen=20)).append({"t_s": 0, "mid": mid})
        rt.consequences.observe("MarketMid", {"coin": coin, "mid": mid}, rt.n)
        rt._observe_mid(coin, rt.clock.now_ns, mid)


def _horizon(rt, **prices):
    """Move the world's clock one consequence horizon on and broadcast ``prices`` (the
    latest mids, where none is given) then, one tick later: the first mid at or after
    H fixes every outcome due (wave 16, D2)."""
    rt.clock.now_ns += rt._horizon_ns()
    latest = {coin: dq[-1]["mid"] for coin, dq in rt.recent_mids.items() if dq}
    _mids(rt, **{**latest, **prices})
    _advance(rt, 1)


def _runtime(**provider):
    rt = _consequence_runtime(provider=Population(**provider))
    rt._manage_reserve_window()
    return rt


def _judge(rt, event, judge="eval-a", *, returned=None, channel=CH_CONFORMITY):
    handle = _consequence_decision(rt, judge, channel)
    rt._evaluator_step(event, handle, SimpleNamespace(chosen=judge),
                       rt.queue.get(handle).deadline_ns, returned=returned)
    return handle


def _meta(rt, verdict_handle, meta="meta-a", *, returned=None):
    event = rt.return_events[verdict_handle]
    handle = _consequence_decision(rt, meta, CH_FAST)
    rt._meta_step(event, handle, SimpleNamespace(chosen=meta),
                  rt.queue.get(handle).deadline_ns, returned=returned)
    return handle


def _advance(rt, ticks):
    """``ticks`` later ticks: every world fact through the clock was delivered before
    them (what a Tick tells the runtime; ``_facts_through``)."""
    for _ in range(ticks):
        rt.n += 1
        rt.ticks_consumed += 1
        rt.tick_through_ns = rt.consequences.tick_through_ns = rt.clock.now_ns
        rt._settle_due_forecasts()


def _rows(rt, kind, **match):
    return [i for i in rt.ledger._recovery_items() if i.get("kind") == kind
            and all(i.get(k) == v for k, v in match.items())]


def _raw(rt, handle):
    """The score a settlement carried before its card penalty."""
    (row,) = _rows(rt, "price.penalty", handle=handle)
    return row["raw"]


# --- the two signals, as pure functions ------------------------------------------------


def test_a_judge_the_world_proved_wrong_earns_less_than_one_it_proved_right():
    """The sign of the consequence score (ruling R1): Brier is higher-is-better, and the
    score is centred on the base rate's own Brier."""
    y, base = 1.0, 0.5
    right = consequence_score(1 - (0.9 - y) ** 2, 1 - (base - y) ** 2)
    wrong = consequence_score(1 - (0.1 - y) ** 2, 1 - (base - y) ** 2)
    parrot = consequence_score(1 - (base - y) ** 2, 1 - (base - y) ** 2)
    assert wrong < parrot == 0.5 < right
    assert 0.0 <= wrong and right <= 1.0
    # The extremes stay in the unit interval without a clip.
    assert consequence_score(0.0, 1.0) == 0.0 and consequence_score(1.0, 0.0) == 1.0


@pytest.mark.parametrize("base,p", [(0.1, 0.8), (0.5, 0.5), (0.9, 0.2), (0.3, 0.3),
                                    (0.05, 0.95), (0.7, 0.0)])
def test_the_consequence_score_is_proper_whatever_the_base_rate(base, p):
    """Architect's ruling on the #128 review: the clipped score was not proper (with
    base 0.1 and truth 0.8 the best report was 0.444). The expected score, for a
    binary outcome that is true with probability p, is maximised at q = p."""
    def expected(q):
        return sum(weight * consequence_score(1 - (q - y) ** 2, 1 - (base - y) ** 2)
                   for y, weight in ((1, p), (0, 1 - p)))

    grid = [i / 1000 for i in range(1001)]
    best = max(grid, key=expected)
    assert best == pytest.approx(p, abs=1e-3)
    assert all(expected(p) >= expected(q) - 1e-12 for q in grid)


def test_both_signals_count_equally_and_a_missing_one_is_never_imputed():
    assert evaluation_reward(0.8, 0.2) == pytest.approx(0.5)
    assert evaluation_reward(0.8, None) == 0.8  # no world outcome: the tier grade alone
    assert evaluation_reward(None, 0.2) == 0.2  # nobody above read it: the world alone
    assert evaluation_reward(None, None) is None  # censored, never a zero
    # Neither channel is zero-weighted: moving either moves the reward.
    assert evaluation_reward(0.9, 0.2) > evaluation_reward(0.8, 0.2)
    assert evaluation_reward(0.8, 0.3) > evaluation_reward(0.8, 0.2)


# --- producers learn from verdicts -------------------------------------------------------


def test_a_producer_learns_the_mean_of_the_verdicts_that_arrived():
    rt = _runtime(verdicts=(0.9, 0.3))
    producer, event = _consequence_produce(rt)
    first, second = _judge(rt, event, "eval-a"), _judge(rt, event, "eval-b")
    assert rt.queue.get(producer).status is SettleStatus.PENDING  # routing still running
    rt._settle_arrived_verdicts()
    (settled,) = rt.queue.history(producer)
    assert settled.definition_version == "verdict-v1" and settled.sampling_ref == first
    assert _raw(rt, producer) == pytest.approx(0.6)
    (mean,) = _rows(rt, "verdict.mean", handle=producer)
    assert mean["judges"] == [first, second] and mean["score"] == pytest.approx(0.6)


def test_one_judge_settles_its_return_on_its_verdict_alone():
    rt = _runtime(verdicts=(0.7,))
    producer, event = _consequence_produce(rt)
    _judge(rt, event)
    rt._settle_arrived_verdicts()
    assert _raw(rt, producer) == pytest.approx(0.7)
    assert not _rows(rt, "verdict.mean")


# --- a verdict is also a prediction ------------------------------------------------------


def test_a_verdict_on_a_bare_hold_has_no_world_outcome_and_settles_on_its_grade():
    rt = lists_nothing(_runtime(verdicts=(0.8, 0.4)))
    _producer, event = _consequence_produce(rt)
    graded, ungraded = _judge(rt, event, "eval-a"), _judge(rt, event, "eval-b")
    rt._settle_arrived_verdicts()
    _advance(rt, 1)
    assert not _rows(rt, "verdict.consequence")
    assert rt.pending[graded].consequence_closed and rt.pending[graded].consequence is None
    rt._deliver_meta_verdict(Event("meta-x", EventKind.META_VERDICT, rt.clock.now_ns,
                                   {"about": graded, "tier": 2, "score": 0.7, "by": "m-1"},
                                   "runtime"))
    assert _rows(rt, "evaluator.meta_grade", handle=graded)
    _advance(rt, rt.ev.verdict_timeout_ticks + 1)
    (settled,) = rt.queue.history(graded)
    assert settled.definition_version == "evaluation-v1" and settled.sampling_ref == "m-1"
    assert _raw(rt, graded) == pytest.approx(0.7)
    (unread,) = rt.queue.history(ungraded)
    assert unread.status is SettleStatus.CENSORED  # neither signal: no score, not zero


def _declined_trade_run(verdicts, move_to="101"):
    """A hold that named a declined BTC buy, judged, then priced at its horizon."""
    rt = _runtime(counterfactual={"coin": "BTC", "side": "buy"}, verdicts=verdicts)
    _mids(rt, BTC="100")
    producer, event = _consequence_produce(rt)
    judges = [_judge(rt, event, f"eval-{c}") for c in "abcd"[:len(verdicts)]]
    rt._settle_arrived_verdicts()
    _advance(rt, 1)
    assert not _rows(rt, "verdict.consequence")  # priced at the horizon, not before
    _horizon(rt, BTC=move_to)
    return rt, producer, judges


def test_a_verdict_on_a_declined_trade_is_scored_against_its_opportunity_price():
    """Ruling R2: the priced road not taken is a world measurement of the verdict. It is
    fixed once, at H on the venue's clock, and scored once: the reward, standing and the
    base rate in one pass (wave 16, D2: no mark, no late re-scoring)."""
    rt, producer, (wrong, right) = _declined_trade_run((0.9, 0.1))
    price = opportunity_cost([("BTC", "100")], [("BTC", "101")], rt._taker_rate("BTC"),
                             rt._taker_rate("BTC"),
                             {"coin": "BTC", "side": "buy"})["score"]
    assert price == 0.0  # the rally beat the round trip: declining it was wrong
    scored = {row["handle"]: row for row in _rows(rt, "verdict.consequence")}
    assert scored[wrong]["y"] == scored[right]["y"] == pytest.approx(price)
    assert scored[wrong]["outcome"] == "declined-trade-net-v1"
    assert "phase" not in scored[wrong]
    # Both judges read one return and are scored against one base rate.
    assert scored[wrong]["baseline_brier"] == scored[right]["baseline_brier"]
    # The world proved the praise of a hold that passed up a rally wrong.
    assert scored[wrong]["score"] < 0.5 < scored[right]["score"]
    # Standing trains in the same pass; nothing is scored again later.
    assert rt.standing.snapshot()["eval-a"]["verdict_n"] == 1
    assert _rows(rt, "consequence.opportunity", handle=producer)
    _advance(rt, rt.ev.verdict_timeout_ticks + 1)
    assert _raw(rt, right) > _raw(rt, wrong)
    assert _raw(rt, right) == pytest.approx(scored[right]["score"])
    # The measurement is a fact told to the producer; its reward is still the verdicts.
    assert rt.queue.history(producer)[0].definition_version == "verdict-v1"
    _horizon(rt)
    assert not _rows(rt, "verdict.consequence_late")
    assert rt.standing.snapshot()["eval-a"]["verdict_n"] == 1
    assert len(rt.queue.history(wrong)) == len(rt.queue.history(right)) == 1
    assert len(_rows(rt, "verdict.consequence", handle=wrong)) == 1


def _unsettled_produce(rt, action="seed-decider"):
    """A producer step whose consequence has not been resolved yet."""
    rt.n += 1
    handle = _consequence_decision(rt, action, "verdict")
    rt._producer_step(Event(f"tick-{rt.n}", EventKind.TICK, rt.clock.now_ns, {"index": 0},
                            "test"), handle, SimpleNamespace(chosen=action),
                      rt.queue.get(handle).deadline_ns)
    event = next(e for e in rt.internal if e.kind == EventKind.PRODUCER_RETURN
                 and e.payload["about_handle"] == handle)
    return handle, event


def test_a_verdict_on_executed_operations_is_scored_against_return_paid_off():
    rt = _runtime(verdicts=(0.2, 0.9))
    producer, event = _unsettled_produce(rt)
    # The return bought one BTC at 100; the lot is open when the judges read it.
    rt.consequences.order_result(producer, {"order_id": "o-1", "status": "filled",
                                            "filled_size": "1"}, {"size": "1"}, rt.n)
    rt.consequences.observe("Fill", {"order_id": "o-1", "coin": "BTC", "is_buy": True,
                                     "size": "1", "px": "100", "fee_usd": "0",
                                     "realized_usd": "0"}, rt.n)
    wrong, right = _judge(rt, event, "eval-a"), _judge(rt, event, "eval-b")
    rt._settle_arrived_verdicts()
    _horizon(rt, BTC="200")
    payoff = rt.consequences.payoff(producer)
    assert payoff is not None and payoff.marked and payoff.y == 1
    # The open lot is marked at its liquidation value: the exit fee at the venue's rate.
    assert payoff.exit_fee_micro == 200 * 350
    scored = {row["handle"]: row for row in _rows(rt, "verdict.consequence")}
    assert scored[wrong]["outcome"] == scored[right]["outcome"] == "return_paid_off"
    assert scored[wrong]["y"] == 1.0
    assert scored[wrong]["score"] < scored[right]["score"]
    assert rt.window.consequences_settled >= 1  # the world's paid-off rate saw it


# --- metas are graded by the world too ---------------------------------------------------


def test_a_meta_is_scored_against_the_consequence_of_the_judge_it_graded():
    rt = _runtime(counterfactual={"coin": "BTC", "side": "buy"}, verdicts=(0.9,),
                  conformity=0.8)
    _mids(rt, BTC="100")
    _producer, event = _consequence_produce(rt)
    judge = _judge(rt, event)
    rt._settle_arrived_verdicts()
    meta = _meta(rt, judge)
    assert rt.pending[meta].about == judge and rt.pending[meta].grade_closed  # top tier
    _horizon(rt, BTC="101")
    (judged,) = _rows(rt, "verdict.consequence", handle=judge)
    (graded,) = _rows(rt, "meta.consequence", handle=meta)
    assert graded["judged_consequence"] == judged["score"] and graded["conformity"] == 0.8
    # The meta endorsed a verdict the world proved wrong: it earns less than 0.5.
    assert judged["score"] < 0.5 and graded["score"] < 0.5
    (settled,) = rt.queue.history(meta)
    assert settled.definition_version == "evaluation-v1"
    assert _raw(rt, meta) == pytest.approx(graded["score"])


def test_a_meta_that_names_its_own_handle_is_judged_on_the_delivered_verdict():
    """Evaluations P2: every meta grade in 18 runs was a kernel zero for this."""
    rt = _runtime(verdicts=(0.6,))
    _producer, event = _consequence_produce(rt)
    judge = _judge(rt, event)
    meta = _consequence_decision(rt, "meta-a", CH_FAST)
    rt._meta_step(rt.return_events[judge], meta, SimpleNamespace(chosen="meta-a"),
                  rt.queue.get(meta).deadline_ns,
                  returned=Return(meta, {"conformity": 0.75, "about_handle": meta}, 0, "ok"))
    assert not _rows(rt, "return.refused", handle=meta)
    assert _rows(rt, "about_handle.ignored", handle=meta)
    assert rt.pending[meta].about == judge
    emitted = [e for e in rt.internal if e.kind is EventKind.META_VERDICT
               and e.payload["by"] == meta]
    assert emitted and emitted[0].payload["about"] == judge


# --- the grade window is the read above it (essay II.IV.c, II.III.b) ------------------


def _held_metas(count, *, inner=21):
    """Tier-two meta decisions and their MetaVerdicts, the judges' loop measured at ``inner``."""
    runtime = _recursive_runtime(events=0)
    runtime.clockwork.record("scored:evaluator", inner)
    handles = [_pending_meta(runtime) for _ in range(count)]
    events = [Event(f"meta-{i}", EventKind.META_VERDICT, 0,
                    {"by": h, "about": "lower", "tier": 2, "score": 0.5}, "runtime")
              for i, h in enumerate(handles)]
    return runtime, handles, events


def _grade_from_above(runtime, handle, score=0.25):
    runtime._deliver_meta_verdict(Event("top", EventKind.META_VERDICT, 0,
                                        {"by": "top-1", "about": handle, "tier": 3,
                                         "score": score}, "runtime"))


def _censored_grades(runtime, handle):
    return [i for i in runtime.ledger._recovery_items()
            if i["kind"] == "evaluator.grade_censored" and i["handle"] == handle]


def test_a_grade_window_stays_open_until_the_window_above_reads_it():
    """II.IV.c: the tier-two window is at least min_ratio times the judges' loop, longer
    than verdict_timeout_ticks; the meta held in it is still gradable when it releases,
    and closes the tick after, with the one it passed over ledgered, not dropped."""
    runtime, (passed, read), (first, second) = _held_metas(2)
    assert runtime._cascade_arrival(first) is None
    window = runtime.cascade[2].window
    assert window >= runtime.m.timing.min_ratio * 21 > runtime.ev.verdict_timeout_ticks
    runtime.ticks_consumed = ceil(window)
    runtime.pending[read].opened_at_tick = runtime.ticks_consumed  # it arrives now
    runtime._settle_evaluations()
    assert not runtime.pending[passed].grade_closed  # held past verdict_timeout_ticks
    released = runtime._cascade_arrival(second)
    assert released.payload["by"] == read
    _grade_from_above(runtime, read)
    assert runtime.pending[read].grades == [0.25]
    runtime.ticks_consumed += 1
    runtime._settle_evaluations()
    assert runtime.pending[read].grade_closed and not _censored_grades(runtime, read)
    (unread,) = _censored_grades(runtime, passed)
    assert unread["reason"] == "unread: its window released 1 of 2 completed judgements"
    # A grade that reaches a closed window does not count, and is ledgered with its reason.
    _grade_from_above(runtime, read, 0.9)
    assert runtime.pending[read].grades == [0.25]
    (late,) = _censored_grades(runtime, read)
    assert late["by"] == "top-1" and late["reason"] == "its grade window had closed"


def test_a_window_that_never_releases_closes_its_grade_windows_at_the_backstop():
    """The backstop is the carry patience on the world's clock, one consequence patience
    per tier at or beneath the judgement's (two for a tier-two meta), plus the window's
    drawn duration (wave 16, D2); a judgement recorded without a clock reading is aged
    at the delivered tick."""
    from factorylab.runtime.clockwork import tick_ns

    runtime, (held,), (arrival,) = _held_metas(1)
    runtime._cascade_arrival(arrival)
    backstop = runtime._rise_backstop(runtime.pending[held])
    assert runtime._carry_patience_ns(runtime.pending[held]) == 2 * runtime._patience_ns()
    assert backstop == (2 * runtime._patience_ns()
                        + ceil(runtime.cascade[2].window) * tick_ns(runtime.tick_clock))
    runtime.ticks_consumed = backstop // tick_ns(runtime.tick_clock)
    runtime._settle_evaluations()
    assert not runtime.pending[held].grade_closed
    runtime.ticks_consumed += 1
    runtime._settle_evaluations()
    (censored,) = _censored_grades(runtime, held)
    assert censored["reason"] == ("backstop: its window did not release it within "
                                  f"{backstop // 10**9} s")
    # A judgement no window took waits verdict_timeout_ticks for a judge that chose it.
    runtime, (unrouted,), _events = _held_metas(1)
    runtime.ticks_consumed = runtime.ev.verdict_timeout_ticks + 1
    runtime._settle_evaluations()
    (censored,) = _censored_grades(runtime, unrouted)
    assert censored["reason"].startswith("no read: no cascade window took it")


def _open_subject(runtime):
    """A decision a meta can judge, pending until the test settles it."""
    return runtime.queue.open(
        actor="test-router", event_id="subject", channel=CH_CONFORMITY, deadline_ns=10**15,
        parent_handle=None, cost_ceiling=0,
        propensity=PropensityRecord(("judge",), (1.0,), "judge", 0, "test-router", "state"))


def _arrive(runtime, about, tick):
    """A tier-two meta decision opened at ``tick`` whose MetaVerdict on ``about`` arrives then."""
    runtime.ticks_consumed = tick
    handle = _pending_meta(runtime)
    runtime.pending[handle].opened_at_tick = tick
    runtime.pending[handle].about = about
    event = Event(f"meta-{handle}", EventKind.META_VERDICT, 0,
                  {"by": handle, "about": about, "tier": 2, "score": 0.5}, "runtime")
    return handle, runtime._cascade_releases(event)


def _carry_one(runtime):
    """A window releases while one of its judgements is of an unsettled subject."""
    subject = _open_subject(runtime)
    _arrive(runtime, "lower", 0)
    first = runtime.cascade[2].window
    held, _ = _arrive(runtime, subject, ceil(first) - 5)
    closer, rising = _arrive(runtime, "lower", ceil(first))
    assert rising and held not in {e.payload["by"] for e in rising}
    return subject, held, ceil(first)


def test_a_judgement_unsettled_at_release_is_carried_and_rises_once_its_subject_settles():
    """Essay II.IV.c: a verdict is withheld "until it settles", not dropped. It is carried
    into the tier's next window, which is drawn by the same law, and read in the first
    release after its subject settles, before the window's own arrivals."""
    runtime, _handles, _events = _held_metas(0)
    subject, held, released_at = _carry_one(runtime)
    (carry,) = _rows(runtime, "cascade.carry")
    assert carry["carried"] == [f"meta-{held}"] and carry["backstop"] == []
    gate = runtime.cascade[2]
    assert [e.payload["by"] for e in gate.arrivals] == [held] and gate.carried == 1
    # The carried window is not shortened: min_ratio times the inner loop, opened now.
    assert gate.opened == released_at and gate.window >= runtime.m.timing.min_ratio * 21
    rec = runtime.pending[held]
    assert rec.risen_at_tick is None and rec.rise_window == gate.window
    assert rec.opened_at_tick == released_at - 5  # it keeps its own open time
    runtime._settle_evaluations()
    assert not rec.grade_closed and not _censored_grades(runtime, held)
    # Before its window is due nothing rises, settled or not.
    runtime.queue.settle(subject, channel=CH_CONFORMITY, score=0.5,
                         status=SettleStatus.SETTLED, definition_version="test",
                         sampling_ref=None)
    _other, rising = _arrive(runtime, "lower", released_at + 1)
    assert rising == []
    _last, rising = _arrive(runtime, "lower", released_at + ceil(gate.window))
    assert rising[0].payload["by"] == held
    _grade_from_above(runtime, held)
    assert runtime.pending[held].grades == [0.25]
    runtime.ticks_consumed += 1
    runtime._settle_evaluations()
    assert rec.grade_closed and not _censored_grades(runtime, held)


def test_a_judgement_whose_subject_never_settles_is_censored_at_the_backstop():
    runtime, _handles, _events = _held_metas(0)
    _subject, held, released_at = _carry_one(runtime)
    window = runtime.cascade[2].window
    horizon = runtime._carry_patience_ns(runtime.pending[held]) // 10**9
    _arrive(runtime, "lower", released_at + ceil(window) + horizon)
    (_first, lapsed) = _rows(runtime, "cascade.carry")
    assert lapsed["carried"] == [] and lapsed["backstop"] == [f"meta-{held}"]
    assert all(e.payload["by"] != held for g in runtime.cascade.values() for e in g.arrivals)
    runtime.ticks_consumed += 1
    runtime._settle_evaluations()
    (censored,) = _censored_grades(runtime, held)
    assert censored["reason"] == f"backstop: what it judged had not settled within {horizon} s"


# --- the antagonist -----------------------------------------------------------------------


def test_the_antagonist_earns_by_how_wrong_the_judge_was_and_only_with_a_world_outcome():
    rt = _runtime(counterfactual={"coin": "BTC", "side": "buy"}, verdicts=(0.9,))
    _mids(rt, BTC="100")
    antagonist, event = _consequence_produce(rt, "antagonist-a", CH_EXPOSURE)
    judge = _judge(rt, event)
    rt._settle_arrived_verdicts()
    _horizon(rt, BTC="101")
    (judged,) = _rows(rt, "verdict.consequence", handle=judge)
    (exposure,) = _rows(rt, "exposure.settled", handle=antagonist)
    # Centred on the judge's ordinary score (0.5 before it has one; the Wave 2 review,
    # item 6): the antagonist earns above 0.5 only by making the judge miss more.
    assert exposure["judge_ordinary"] == [0.5]
    assert exposure["score"] == pytest.approx(0.5 + 0.5 * (0.5 - judged["score"]))
    assert exposure["score"] > 0.5
    assert rt.queue.history(antagonist)[0].definition_version == "exposure-v2"
    # A bare hold has no world outcome, so its antagonist earns nothing and loses nothing.
    bare = lists_nothing(_runtime(verdicts=(0.9,)))
    antagonist, event = _consequence_produce(bare, "antagonist-a", CH_EXPOSURE)
    _judge(bare, event)
    bare._settle_arrived_verdicts()
    _advance(bare, bare.ev.verdict_timeout_ticks + 1)
    (settled,) = bare.queue.history(antagonist)
    assert settled.status is SettleStatus.CENSORED


# --- form is not a grade --------------------------------------------------------------


@pytest.mark.parametrize("returned", [
    {"outputs": {"rationale": "no number"}, "status": "ok"},
    {"outputs": {"status": "unmeasured", "reason": "was an answer; now it is not"},
     "status": "ok"},
    {"outputs": {"reason": "provider refusal"}, "status": "refused"},
    {"outputs": {"reason": "bad json"}, "status": "malformed"},
])
def test_a_judgement_the_kernel_cannot_use_is_censored_at_its_cost_never_zero(returned):
    rt = _runtime()
    _producer, event = _consequence_produce(rt)
    handle = _consequence_decision(rt, "eval-a", CH_CONFORMITY)
    rt._evaluator_step(event, handle, SimpleNamespace(chosen="eval-a"),
                       rt.queue.get(handle).deadline_ns,
                       returned=Return(handle, returned["outputs"], 700, returned["status"]))
    (settled,) = rt.queue.history(handle)
    assert settled.status is SettleStatus.CENSORED and settled.score == 0.0
    assert settled.definition_version == "judgement-censored-v1"
    assert rt.consequences.table.account(handle).cost_micro == 700  # charged its call
    assert handle not in rt.pending


def test_a_malformed_meta_is_censored_and_a_decline_costs_only_the_call():
    rt = _runtime(verdicts=(0.6,))
    _producer, event = _consequence_produce(rt)
    judge = _judge(rt, event)
    meta = _consequence_decision(rt, "meta-a", CH_FAST)
    rt._meta_step(rt.return_events[judge], meta, SimpleNamespace(chosen="meta-a"),
                  rt.queue.get(meta).deadline_ns,
                  returned=Return(meta, {"conformity": "high"}, 0, "ok"))
    (settled,) = rt.queue.history(meta)
    assert settled.status is SettleStatus.CENSORED  # never the old fast-v1 zero
    declined = _consequence_decision(rt, "eval-b", CH_CONFORMITY)
    rt._evaluator_step(event, declined, SimpleNamespace(chosen="eval-b"),
                       rt.queue.get(declined).deadline_ns,
                       returned=Return(declined, {"status": "cannot", "reason": "no view"},
                                       0, "ok"))
    (answer,) = rt.queue.history(declined)
    assert answer.status is SettleStatus.INAPPLICABLE
    assert answer.definition_version == "declined-v1"


# --- the router learns the same reward -------------------------------------------------


def test_an_evaluator_decision_trains_its_router_on_the_combined_reward():
    rt = lists_nothing(_runtime(verdicts=(0.8,)))
    _producer, event = _consequence_produce(rt)
    state = rt.routers["ProducerReturn"][0]
    feasible = lambda a: (a == "eval-a", "")  # noqa: E731 - one seat may be woken
    draws = (state.router.route("ProducerReturn", feasible, random.Random(seed))
             for seed in range(50))
    sample = next(s for s in draws if s.chosen == "eval-a")
    handle = rt.queue.open(actor=state.learner.id, event_id="judge-draw",
                           propensity=rt._propensity(sample), channel=CH_CONFORMITY,
                           deadline_ns=10**18, parent_handle=None,
                           cost_ceiling=rt.wallet.available)
    rt._evaluator_step(event, handle, SimpleNamespace(chosen="eval-a"), 10**18)
    rt._settle_arrived_verdicts()
    rt._deliver_meta_verdict(Event("meta-x", EventKind.META_VERDICT, rt.clock.now_ns,
                                   {"about": handle, "tier": 2, "score": 0.6, "by": "m-1"},
                                   "runtime"))
    _advance(rt, rt.ev.verdict_timeout_ticks + 1)
    rt._deliver_returns()
    assert state.definitions.get("evaluation-v1") == [1, pytest.approx(0.6)]


def test_a_judge_decision_lives_until_its_return_can_be_measured():
    """A judge settles after its return's horizon, so its decision's deadline covers its
    consequence patience (wave 16, D2) and the router never learns a neutral cutoff in its
    place."""
    rt = _runtime(verdicts=(0.5,))
    rt._route(Event("probe", EventKind.PRODUCER_RETURN, rt.clock.now_ns, {
        "about_handle": "nothing", "outputs": {"action": "hold"}, "cost": 0,
        "status": "ok"}, "runtime"))
    opened = [i for i in rt.ledger._recovery_items() if i.get("kind") == "decision.open"
              and i.get("channel") == CH_CONFORMITY]
    assert opened
    assert all(i["deadline_ns"] - i["opened_ns"] > rt._patience_ns() for i in opened)


def test_a_verdict_redirected_to_a_judges_decision_sits_one_tier_above_it_end_to_end():
    """Codex review of #128: a Verdict whose about_handle names a pending judge decision
    graded it from the tier above but was itself opened at tier one, so the world
    'scored' it against the economic account of a judgement. It now carries the derived
    tier: it grades the judge, is published at that tier, and is scored against the
    judge's consequence score (ruling R1: a tier grades the tier below)."""
    from factorylab.runtime.cascade import event_tier

    rt = _runtime(counterfactual={"coin": "BTC", "side": "buy"}, verdicts=(0.9,))
    _mids(rt, BTC="100")
    _producer, event = _consequence_produce(rt)
    lower = _judge(rt, event, "eval-a")
    rt._settle_arrived_verdicts()
    upper = _judge(rt, event, "eval-b",
                   returned=Return("x", {"verdict": 0.2, "about_handle": lower}, 0, "ok"))
    record = rt.pending[upper]
    assert record.about == lower and record.tier == 2
    (grade,) = _rows(rt, "evaluator.meta_grade", handle=lower)
    assert grade["by"] == upper and grade["tier"] == 2
    published = next(e for e in rt.internal if e.kind is EventKind.VERDICT
                     and e.payload["evaluator_handle"] == upper)
    assert published.payload["tier"] == 2 and published.payload["about_handle"] == lower
    assert event_tier(published) == 2
    # The world resolves the return: the lower judge is scored on it, the upper one on
    # the lower judge's consequence score, never on a world outcome of a judgement.
    _horizon(rt, BTC="101")
    (judged,) = _rows(rt, "verdict.consequence", handle=lower)
    assert not _rows(rt, "verdict.consequence", handle=upper)
    (graded,) = _rows(rt, "meta.consequence", handle=upper)
    assert graded["about_handle"] == lower
    assert graded["judged_consequence"] == judged["score"]
    # The upper verdict (0.2) doubted a judge the world proved wrong: it earns above 0.5.
    assert judged["score"] < 0.5 < graded["score"]


def test_a_judgement_may_not_name_a_judge_decision_whose_consequence_is_known():
    rt = _runtime(verdicts=(0.9,))
    _producer, event = _consequence_produce(rt)
    lower = _judge(rt, event, "eval-a")
    rt._settle_arrived_verdicts()
    rt._close_consequence(lower, 0.3, rt.pending[lower])
    upper = _judge(rt, event, "eval-b",
                   returned=Return("x", {"verdict": 0.4, "about_handle": lower}, 0, "ok"))
    (settled,) = rt.queue.history(upper)
    assert settled.status is SettleStatus.CENSORED
    (refused,) = _rows(rt, "return.refused", handle=upper)
    assert "consequence is still open" in refused["reason"]


# --- anticipatory settlement (the #128 review, finding 3) ------------------------------


def test_an_open_lot_is_scored_once_at_the_horizon_at_its_liquidation_value():
    """Wave 16, D2 and D7: an acting return is fixed at the same horizon on the venue's
    clock as a named trade, its open lot marked to the mid less the venue's taker exit
    fee. There is no earlier mark and no later re-scoring: the judge is scored once and
    standing trains in the same pass."""
    rt = _runtime(verdicts=(0.2,))
    producer, event = _unsettled_produce(rt)
    rt.consequences.order_result(producer, {"order_id": "o-1", "status": "filled",
                                            "filled_size": "1"}, {"size": "1"}, rt.n)
    rt.consequences.observe("Fill", {"order_id": "o-1", "coin": "BTC", "is_buy": True,
                                     "size": "1", "px": "100", "fee_usd": "0",
                                     "realized_usd": "0"}, rt.n)
    judge = _judge(rt, event)
    rt._settle_arrived_verdicts()
    _mids(rt, BTC="200")
    _advance(rt, rt.ev.consequence_backstop_ticks + 1)  # ticks alone fix nothing
    assert rt.consequences.payoff(producer) is None
    assert not _rows(rt, "verdict.consequence", handle=judge)
    _horizon(rt)
    payoff = rt.consequences.payoff(producer)
    assert payoff is not None and payoff.marked and payoff.exit_fee_micro == 200 * 350
    (scored,) = _rows(rt, "verdict.consequence", handle=judge)
    assert scored["y"] == 1.0 and "phase" not in scored
    assert not _rows(rt, "consequence.marked") and not _rows(rt, "verdict.consequence_late")
    assert rt.standing.snapshot()["eval-a"]["verdict_n"] == 1
    _advance(rt, rt.ev.verdict_timeout_ticks + 1)
    (settled,) = rt.queue.history(judge)
    assert settled.definition_version == "evaluation-v1"
    assert _raw(rt, judge) == pytest.approx(scored["score"])
    _horizon(rt)
    assert len(rt.queue.history(judge)) == 1  # the reward settled once
    assert len(_rows(rt, "verdict.consequence", handle=judge)) == 1

def test_a_verdict_on_an_already_fixed_outcome_is_scored_final_at_once():
    rt = _runtime(verdicts=(0.2,))
    producer, event = _unsettled_produce(rt)
    # The return opened and closed its own lot at a loss: its outcome is already fixed.
    for order, is_buy, px in (("o-1", True, "100"), ("o-2", False, "90")):
        rt.consequences.order_result(producer, {"order_id": order, "status": "filled",
                                                "filled_size": "1"}, {"size": "1"}, rt.n)
        rt.consequences.observe("Fill", {"order_id": order, "coin": "BTC", "is_buy": is_buy,
                                         "size": "1", "px": px, "fee_usd": "0",
                                         "realized_usd": "0"}, rt.n)
    judge = _judge(rt, event)
    rt._settle_arrived_verdicts()
    _advance(rt, 1)
    (scored,) = _rows(rt, "verdict.consequence", handle=judge)
    assert scored["y"] == 0.0
    assert rt.standing.snapshot()["eval-a"]["verdict_n"] == 1
    _horizon(rt)
    assert len(_rows(rt, "verdict.consequence", handle=judge)) == 1


# --- judges may not earn more by avoiding the world (finding 1) -------------------------


def test_a_declined_commission_is_priced_like_an_abstention_not_its_own_mean(monkeypatch):
    from factorylab.runtime import pricing
    from tests.runtime.test_attributable_blame import _card, _commitments
    from tests.runtime.test_attributable_blame import _runtime as _priced_runtime

    monkeypatch.setattr(pricing, "close_window", lambda *_a: None)
    rt = _priced_runtime(_card(per=None))
    state = rt.routers["ProducerReturn"][0]
    state.observed.record("eval-a", 0.9)  # the arm's own mean, which a decline used to earn
    feasible = lambda a: (a == "eval-a", "")  # noqa: E731
    sample = next(s for s in (state.router.route("ProducerReturn", feasible, random.Random(i))
                              for i in range(50)) if s.chosen == "eval-a")
    handle = rt.queue.open(actor=state.learner.id, event_id="declined",
                           propensity=rt._propensity(sample), channel=CH_CONFORMITY,
                           deadline_ns=10**18, parent_handle=None, cost_ceiling=0)
    rt._contribution(handle, "evaluator")["invocations"] = 1
    _commitments(rt, "eval-a", censored=4)
    rt._close_price_window()
    rt._settle_declined(handle, "no view")
    rt._deliver_returns()
    (priced,) = _rows(rt, "router.decline_priced", handle=handle)
    assert priced["penalty"] > 0
    assert priced["reward"] == pytest.approx(_learned(rt, state.neutral(), priced["penalty"]))
    assert priced["reward"] < 0.9


def test_the_meta_tier_reads_a_verdict_the_world_will_never_grade_first():
    """A cascade window releases, as its representative, a verdict on a bare hold ahead
    of verdicts on returns the world will measure."""
    from factorylab.runtime.cascade import CascadeGate

    rt = _runtime(counterfactual={"coin": "BTC", "side": "buy"}, verdicts=(0.6,))
    _mids(rt, BTC="100")
    graded_by_world, event = _unsettled_produce(rt)
    world_verdict = Event("v-world", EventKind.VERDICT, 0, {
        "about_handle": graded_by_world, "evaluator_handle": "j-1", "verdict": 0.6}, "rt")
    bare = _runtime()
    bare_return, _event = _unsettled_produce(bare)
    rt.consequences.start(bare_return + "-bare", rt.n)
    rt.consequences.finish(bare_return + "-bare", 0)
    bare_verdict = Event("v-bare", EventKind.VERDICT, 1, {
        "about_handle": bare_return + "-bare", "evaluator_handle": "j-2", "verdict": 0.4},
        "rt")
    assert rt._cascade_priority(bare_verdict) == 1
    assert rt._cascade_priority(world_verdict) == 0
    gate = CascadeGate(5, opened=0)
    gate, _ = gate.add(bare_verdict, now=0, priority=rt._cascade_priority)
    late_world = Event("v-world-2", EventKind.VERDICT, 10, dict(world_verdict.payload), "rt")
    _none, released = gate.add(late_world, now=10, priority=rt._cascade_priority)
    assert released.payload["evaluator_handle"] == "j-2"
    # Without the priority the latest completed arrival (the world-graded one) is read.
    plain, _ = CascadeGate(5, opened=0).add(bare_verdict, now=0)
    _none, unprioritised = plain.add(late_world, now=10)
    assert unprioritised.payload["evaluator_handle"] == "j-1"
    # A Verdict published above tier one is windowed at its tier and read by its verdict.
    upper = Event("v-upper", EventKind.VERDICT, 0, {"about_handle": "j-1",
                                                    "evaluator_handle": "j-3",
                                                    "verdict": 0.2, "tier": 2}, "rt")
    gate2, _ = CascadeGate(5, opened=0).add(upper, now=0)
    _none, released2 = gate2.add(Event("v-upper-2", EventKind.VERDICT, 10,
                                       dict(upper.payload, evaluator_handle="j-4"), "rt"), now=10)
    assert released2.payload["window"]["mean"] == pytest.approx(0.2)


# --- chosen targets (finding 5) -----------------------------------------------------------


def test_a_judge_may_choose_an_older_open_return_until_its_horizon():
    """The #128 review: judges now live until the backstop, so comparing their deadline
    with the target's refused every chosen target. A chosen target is refused only when
    the world has answered it (fixed or priced) or its horizon on the venue's clock has
    passed (wave 16, D2)."""
    rt = _runtime(counterfactual={"coin": "BTC", "side": "buy"}, verdicts=(0.7, 0.3))
    _mids(rt, BTC="100")
    older, _older_event = _unsettled_produce(rt)
    unread, _unread_event = _unsettled_produce(rt)
    _advance(rt, 1)
    _producer, event = _consequence_produce(rt)
    chosen = _judge(rt, event, "eval-a",
                    returned=Return("x", {"verdict": 0.7, "about_handle": older}, 0, "ok"))
    assert rt.pending[chosen].about == older and rt.pending[chosen].tier == 1
    assert not _rows(rt, "return.refused", handle=chosen)
    _advance(rt, rt.ev.consequence_backstop_ticks)  # ticks alone are no horizon
    assert rt._hindsight_reason("probe", unread) is None
    rt.clock.now_ns += rt._horizon_ns()
    late = _judge(rt, event, "eval-b",
                  returned=Return("x", {"verdict": 0.3, "about_handle": unread}, 0, "ok"))
    (refused,) = _rows(rt, "return.refused", handle=late)
    assert "before its consequence horizon" in refused["reason"]
    assert rt.queue.history(late)[0].status is SettleStatus.CENSORED


# --- no judgement at any tier is scored on a consequence already known ------------------


def _closed_judge(rt):
    """A judge decision whose consequence the world has already scored."""
    _mids(rt, BTC="100")
    _producer, event = _consequence_produce(rt)
    judge = _judge(rt, event, "eval-a")
    rt._settle_arrived_verdicts()
    _horizon(rt, BTC="101")
    assert judge in rt.consequence_scores and rt.consequence_scores[judge][0] is not None
    return judge, event


def test_a_meta_redirected_to_a_judge_whose_consequence_is_known_is_refused():
    """Codex re-review of #128: a meta's about_handle skipped the hindsight check, and
    a meta that chose a closed judge was scored at once against the known value."""
    rt = _runtime(counterfactual={"coin": "BTC", "side": "buy"}, verdicts=(0.9, 0.5))
    closed, event = _closed_judge(rt)
    fresh = _judge(rt, event, "eval-b")
    meta = _consequence_decision(rt, "meta-a", CH_FAST)
    rt._meta_step(rt.return_events[fresh], meta, SimpleNamespace(chosen="meta-a"),
                  rt.queue.get(meta).deadline_ns,
                  returned=Return(meta, {"conformity": 0.1, "about_handle": closed}, 0, "ok"))
    (refused,) = _rows(rt, "return.refused", handle=meta)
    assert "consequence is still open" in refused["reason"]
    assert rt.queue.history(meta)[0].status is SettleStatus.CENSORED
    assert not _rows(rt, "meta.consequence", handle=meta)


def test_a_meta_delivered_a_judge_whose_consequence_is_known_is_never_world_scored():
    rt = _runtime(counterfactual={"coin": "BTC", "side": "buy"}, verdicts=(0.9,))
    closed, _event = _closed_judge(rt)
    meta = _meta(rt, closed)
    assert rt.pending[meta].consequence_closed and rt.pending[meta].consequence is None
    assert _rows(rt, "evaluation.hindsight", handle=meta)
    _advance(rt, rt.ev.verdict_timeout_ticks + 1)
    assert not _rows(rt, "meta.consequence", handle=meta)
    assert rt.queue.history(meta)[0].status is SettleStatus.CENSORED


def test_a_judge_delivered_a_return_whose_payoff_is_fixed_is_never_world_scored():
    rt = _runtime(verdicts=(0.2,))
    producer, event = _unsettled_produce(rt)
    for order, is_buy, px in (("o-1", True, "100"), ("o-2", False, "90")):
        rt.consequences.order_result(producer, {"order_id": order, "status": "filled",
                                                "filled_size": "1"}, {"size": "1"}, rt.n)
        rt.consequences.observe("Fill", {"order_id": order, "coin": "BTC", "is_buy": is_buy,
                                         "size": "1", "px": px, "fee_usd": "0",
                                         "realized_usd": "0"}, rt.n)
    _advance(rt, 1)
    assert rt.consequences.payoff(producer) is not None  # fixed before the judge read it
    judge = _judge(rt, event)
    assert _rows(rt, "evaluation.hindsight", handle=judge)
    _advance(rt, 1)
    assert not _rows(rt, "verdict.consequence", handle=judge)
