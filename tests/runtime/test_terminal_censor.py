"""Nothing is open at the seal: the kernel queue is the one source of truth (Codex on #152).

A kill censors every decision the queue holds without a final outcome, whatever book
awaited it (a forecast, an antagonist's exposure, a counter-verdict, an evaluator's
grade window, a ballot's post-activation window), and every lambda margin not yet
due, then empties every live book that awaited a later boundary. A guard pins the
books: a future live book must be classified, cleared or kept, or this fails.
"""

from __future__ import annotations

from types import SimpleNamespace

from factorylab.kernel.queue import SettleStatus
from factorylab.runtime.settled import LIVE_BOOKS
from factorylab.runtime.shared import CH_EXPOSURE
from factorylab.runtime.venue import CLEARED_AT_TERMINATION, KEPT_AT_TERMINATION, TERMINATION
from factorylab.settlement.forecast import Forecast
from tests.runtime.test_loop import _consequence_decision
from tests.runtime.test_reward_chain import _judge, _mids, _rows, _runtime, _unsettled_produce

OPEN = (SettleStatus.PENDING, SettleStatus.TIMED_OUT)


def test_every_live_book_is_cleared_or_kept_at_termination():
    """A live book added later that is in neither list fails here: it would be left
    holding work awaiting a boundary that never comes."""
    cleared, kept = set(CLEARED_AT_TERMINATION), set(KEPT_AT_TERMINATION)
    assert not cleared & kept
    assert cleared | kept == set(LIVE_BOOKS)


def test_a_kill_leaves_no_decision_open_and_no_awaiting_book_filled():
    rt = _runtime(verdicts=(0.7,))
    _mids(rt, BTC="100")
    producer, event = _unsettled_produce(rt)
    judge = _judge(rt, event, "eval-a")  # awaits the tier above's grade window
    forecast = _consequence_decision(rt, "eval-a", "consequence")
    rt.book.seal(Forecast(forecast, "eval-a", judge, "return_paid_off",
                          {"horizon_events": 10_000}, 0.5, rt.n,
                          rt.n + 10_000, "", rt.ticks_consumed + 10_000))
    rt.forecast_returns[judge] = {"handles": [forecast], "results": {}}
    exposure = _consequence_decision(rt, "antagonist", CH_EXPOSURE)
    rt.pending_exposure[exposure] = rt.ticks_consumed
    counter = _consequence_decision(rt, "counter", "counter")
    rt.pending_counters[counter] = {  # the shape ``_counter_step`` records
        "about": producer, "q": 0.4, "judge_handle": judge, "judge_q": 0.7,
        "evaluator_id": "counter", "tick": rt.ticks_consumed, "ns": rt.clock.now_ns}
    ballot = _consequence_decision(rt, "seed-decider", "policy")
    rt.pending_votes.append({"handle": ballot, "assembly": "seed-decider",
                             "amendment_id": "m-1", "vote": True,
                             # graded at a post-activation window this world never
                             # reaches (``_close_policy_window``)
                             "prediction": SimpleNamespace(window=5),
                             "card": rt.charter.cards[0],
                             "activation_window": 3, "baseline": None})
    timed_out = _consequence_decision(rt, "seed-decider", "verdict")
    rt.queue.time_out([timed_out], rt.clock.now_ns)
    rt.margin_windows[7] = {"due": 99, "decisions": {}, "cards": {}}
    rt.kill("test: every kind open")

    assert not [h for h in rt.queue.retained() if rt.queue.get(h).status in OPEN]
    for name in CLEARED_AT_TERMINATION:
        assert not getattr(rt, name, ()), name
    assert not rt.book.pending()
    kinds = {row["handle"]: row["kind"] for row in rt.ledger._recovery_items()
             if row.get("reason") == TERMINATION and "handle" in row}
    assert kinds[forecast] == "forecast.censored"
    assert kinds[exposure] == "exposure.censored"
    assert kinds[counter] == "counter.censored"
    assert kinds[ballot] == "ballot.censored"
    assert kinds[judge] == kinds[timed_out] == "decision.censored"
    # Ours, and the one the terminal close kept for its own window: each once.
    windows = [row["window"] for row in _rows(rt, "margin.censored")]
    assert 7 in windows and len(windows) == len(set(windows))
    for handle in (forecast, exposure, counter, ballot, judge, timed_out):
        assert rt.queue.history(handle)[-1].status is SettleStatus.CENSORED, handle
        assert [row.get("handle") for row in rt.ledger._recovery_items()
                if row.get("reason") == TERMINATION].count(handle) == 1  # once
    assert rt.termination.final
