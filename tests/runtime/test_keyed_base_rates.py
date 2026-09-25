"""Base rates keyed per (definition, coin, side, horizon), and the easy-question rule on
verdicts (wave 16, D3; ruling R-B).

A verdict is scored against the base rate of its own kind of outcome, coin, side and
horizon, so a judge that knows only which coins or sides the world usually proves
right earns exactly the base rate's score and nothing more. A key whose base rate
already answers the question issues no consequence score at all: the verdict closes
``consequence.uninformative`` with the base rate, trains no standing, and the judge's
reward is its tier grade alone. The outcome still enters the base rate, so a key the
world changes comes back.
"""

from __future__ import annotations

from factorylab.kernel.ledger import Ledger
from factorylab.kernel.queue import DecisionQueue
from factorylab.settlement.forecast import ForecastBook
from factorylab.settlement.scoring import (
    UNINFORMATIVE_HIGH,
    UNINFORMATIVE_SUPPORT,
    PrevalenceBaseline,
)
from factorylab.settlement.settle import Settler
from factorylab.settlement.standing import ConsequenceStanding
from factorylab.settlement.vocabulary import Observer
from tests.runtime.test_loop import _consequence_produce
from tests.runtime.test_reward_chain import (
    _advance,
    _horizon,
    _judge,
    _mids,
    _rows,
    _runtime,
)


def _settler():
    ledger = Ledger(clock_ns=lambda: 0)
    baseline, standing = PrevalenceBaseline(), ConsequenceStanding(min_coverage=0.5)
    settler = Settler(ForecastBook(ledger), DecisionQueue(ledger, clock_ns=lambda: 0),
                      standing, baseline, Observer())
    return settler, baseline, standing


def test_a_dull_coin_pays_nothing_and_then_retires():
    """Dull-coin collusion: a judge that always says 1 about holds on a coin that never
    moves is right every time, because a flat coin never beats the round trip. It earns
    the base rate's score once the base rate has learnt that, then, once the base rate
    answers the question, nothing is scored at all and no standing is trained."""
    rounds = UNINFORMATIVE_SUPPORT + 4
    rt = _runtime(counterfactual={"coin": "BTC", "side": "buy"}, verdicts=(1.0,) * rounds)
    judges = []
    for _ in range(rounds):
        _mids(rt, BTC="100")
        _producer, event = _consequence_produce(rt)
        judges.append(_judge(rt, event))
        rt._settle_arrived_verdicts()
        _horizon(rt, BTC="100")
    scored = _rows(rt, "verdict.consequence")
    retired = _rows(rt, "consequence.uninformative")
    assert [r["handle"] for r in scored] == judges[:UNINFORMATIVE_SUPPORT]
    assert [r["handle"] for r in retired] == judges[UNINFORMATIVE_SUPPORT:]
    assert all(r["y"] == 1.0 for r in scored + retired)
    assert sum(r["score"] for r in scored) / len(scored) <= 0.5 + 0.01
    assert all(r["base_rate"] >= UNINFORMATIVE_HIGH and r["support"] >= UNINFORMATIVE_SUPPORT
               for r in retired)
    assert rt.standing.snapshot()["eval-a"]["verdict_n"] == UNINFORMATIVE_SUPPORT
    # Absent, not 0.5: a retired verdict closes its consequence empty.
    last = judges[-1]
    assert last in rt.consequence_scores and rt.consequence_scores[last][0] is None
    assert not _rows(rt, "verdict.consequence", handle=last)
    _advance(rt, rt.ev.verdict_timeout_ticks + 1)
    settled = [r for r in _rows(rt, "evaluator.settled") if r["handle"] == last]
    assert settled and settled[0]["consequence"] is None


def test_keys_are_isolated_by_coin_side_and_horizon():
    rt = _runtime(counterfactual={"coin": "BTC", "side": "buy"}, verdicts=(0.7,))
    _mids(rt, BTC="100", ETH="10")
    producer, event = _consequence_produce(rt)
    judge = _judge(rt, event)
    rt._settle_arrived_verdicts()
    _horizon(rt, BTC="100.5")
    (scored,) = _rows(rt, "verdict.consequence", handle=judge)
    key = rt._verdict_key(producer, scored["outcome"])
    assert key == f"verdict:declined-trade-net-v1:BTC:buy:{rt._horizon_ns()}"
    settler, baseline, _standing = _settler()
    keys = [f"verdict:declined-trade-net-v1:{coin}:{side}:{h}"
            for coin, side, h in (("BTC", "buy", 1), ("ETH", "buy", 1), ("BTC", "sell", 1),
                                  ("BTC", "buy", 2))]
    for i in range(5):
        settler.settle_verdict(evaluator_id="j", about_handle=f"r{i}", q=0.5, outcome=1.0,
                               key=keys[0])
    assert baseline.baseline_q(keys[0]) == 1.0
    assert all(baseline.baseline_q(k) == 0.5 and baseline.support(k) == 0 for k in keys[1:])
    other = settler.settle_verdict(evaluator_id="j", about_handle="x", q=0.5, outcome=1.0,
                                   key=keys[1])
    assert other.base_rate == 0.5  # BTC's history is not ETH's


def test_a_retired_key_keeps_recording_and_comes_back_when_the_world_changes():
    settler, baseline, standing = _settler()
    key = "verdict:declined-trade-net-v1:BTC:sell:1"
    for i in range(UNINFORMATIVE_SUPPORT):
        assert not settler.settle_verdict(evaluator_id="j", about_handle=f"a{i}", q=0.9,
                                          outcome=1.0, key=key).uninformative
    assert baseline.uninformative(key)
    retired = settler.settle_verdict(evaluator_id="j", about_handle="b0", q=0.9, outcome=0.0,
                                     key=key)
    assert retired.uninformative and retired.base_rate == 1.0
    assert standing.snapshot()["j"]["verdict_n"] == UNINFORMATIVE_SUPPORT
    # Every judge of one return faces the one answer, however the key moved since.
    assert settler.settle_verdict(evaluator_id="k", about_handle="b0", q=0.1, outcome=0.0,
                                  key=key).uninformative
    i = 1
    while baseline.uninformative(key):
        settler.settle_verdict(evaluator_id="j", about_handle=f"b{i}", q=0.9, outcome=0.0,
                               key=key)
        i += 1
    assert baseline.baseline_q(key) < UNINFORMATIVE_HIGH
    back = settler.settle_verdict(evaluator_id="j", about_handle="c", q=0.9, outcome=0.0,
                                  key=key)
    assert not back.uninformative
    assert standing.snapshot()["j"]["verdict_n"] == UNINFORMATIVE_SUPPORT + 1


def test_a_meta_is_scored_against_the_base_rate_of_its_own_tier():
    """Tiers score different random variables, so each keeps its own base rate."""
    rt = _runtime(counterfactual={"coin": "BTC", "side": "buy"}, verdicts=(0.9,),
                  conformity=0.8)
    _mids(rt, BTC="100")
    _producer, event = _consequence_produce(rt)
    judge = _judge(rt, event)
    rt._settle_arrived_verdicts()
    from tests.runtime.test_reward_chain import _meta

    meta = _meta(rt, judge)
    _horizon(rt, BTC="101")
    assert _rows(rt, "meta.consequence", handle=meta)
    tier = rt.pending.get(meta).tier if meta in rt.pending else 2
    assert rt.baseline.support(f"evaluation_consequence:{tier}") == 1
    assert rt.baseline.support("evaluation_consequence") == 0
