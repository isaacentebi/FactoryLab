"""Every producer decision that a judge scored teaches its learner (PR121: 124 of 124 censored).

Under realized feedback a producer waits for a grounded finding. When the world
never speaks — unknown, malformed, or past the close horizon — the decision
used to be discarded, so a population of mostly holds learned nothing at all.
The fast provisional verdict now stands in, labelled apart from a grounded score.
"""

from collections import deque
from decimal import Decimal

from factorylab.kernel.queue import SettleStatus
from factorylab.runtime.grounded import (
    GROUNDED_DEFINITION,
    OPPORTUNITY_DEFINITION,
    freeze_contract,
    opportunity_cost,
)
from factorylab.runtime.shared import CH_VERDICT
from tests.runtime.test_grounded_feedback import _judge, _open_contract, _runtime


def test_unknown_finding_settles_the_producer_on_its_provisional_verdict():
    rt = _runtime()
    producer, contract = _open_contract(rt)
    rt.grounded_pending[producer] = contract.with_initial(
        judge_handle="judge-1", evaluator_id="eval-a", forecast_handles=(), score=0.7)
    _judge(rt, producer, {"status": "unknown", "evidence": [],
                          "reason": "no attributable receipt"})
    settled = rt.queue.history(producer)[0]
    assert settled.status is SettleStatus.SETTLED
    assert settled.definition_version == f"{GROUNDED_DEFINITION}-provisional"
    assert 0 < settled.score <= 0.7  # priced like any verdict; never above the opinion
    assert settled.sampling_ref == "judge-1"


def test_close_horizon_without_a_finding_also_falls_back_to_the_provisional_verdict():
    rt = _runtime()
    producer, contract = _open_contract(rt)
    rt.grounded_pending[producer] = contract.with_initial(
        judge_handle="judge-1", evaluator_id="eval-a", forecast_handles=(), score=0.4)
    rt._grounded_unknown(rt.grounded_pending[producer], "horizon closed")
    assert rt.queue.history(producer)[0].definition_version.endswith("-provisional")


def test_no_provisional_verdict_still_closes_unknown_without_a_sample():
    rt = _runtime()
    producer, _ = _open_contract(rt)
    _judge(rt, producer, {"status": "unknown", "evidence": [],
                          "reason": "no attributable receipt"})
    settled = rt.queue.history(producer)[0]
    assert settled.status is SettleStatus.CENSORED
    assert settled.definition_version.endswith("-unknown")


def test_first_provisional_verdict_is_kept_and_a_grounded_score_still_wins():
    rt = _runtime()
    producer, contract = _open_contract(rt)
    contract = contract.with_initial(judge_handle="judge-1", evaluator_id="eval-a",
                                     forecast_handles=(), score=0.2)
    contract = contract.with_initial(judge_handle="judge-2", evaluator_id="eval-c",
                                     forecast_handles=(), score=0.9)
    assert (contract.provisional_score, contract.provisional_judge) == (0.2, "judge-1")
    rt.grounded_pending[producer] = contract
    _judge(rt, producer, {"status": "supported", "score": 0.8, "evidence": ["event:7"],
                          "reason": "the fill supports the claim"})
    assert rt.queue.history(producer)[0].definition_version == GROUNDED_DEFINITION


# --- the road not taken: inaction is priced by the market, not by an opinion ---------


def test_holding_through_a_flat_market_is_right_and_scores_one():
    priced = opportunity_cost([("BTC", "100")], [("BTC", "100.05")], Decimal("9"))
    assert priced["regret_bps"] == "0.00" and priced["score"] == 1.0


def test_sitting_through_a_move_worth_taking_is_priced_as_regret():
    # A 1% rally against a 9 bp round trip: 91 bp of profit passed up.
    priced = opportunity_cost([("BTC", "100"), ("ETH", "10")],
                              [("BTC", "101"), ("ETH", "10")], Decimal("9"))
    assert priced["best_declined"] == {"coin": "BTC", "side": "buy", "gross_bps": "100.00"}
    assert priced["regret_bps"] == "91.00"
    assert priced["score"] == round(9 / 100, 4)
    # A fall is a missed short, priced the same way.
    short = opportunity_cost([("BTC", "100")], [("BTC", "99")], Decimal("9"))
    assert short["best_declined"]["side"] == "sell" and short["score"] == priced["score"]


def test_no_shared_prices_means_the_world_did_not_speak():
    assert opportunity_cost([("BTC", "100")], [("ETH", "10")], Decimal("9")) is None
    assert opportunity_cost([], [], Decimal("9")) is None


def _mids(rt, **prices):
    for coin, mid in prices.items():
        rt.recent_mids.setdefault(coin, deque(maxlen=20)).append({"t_s": 0, "mid": mid})


def _frozen_hold(rt, action="hold"):
    from factorylab.runtime.feedback import PendingJudgement
    from tests.runtime.test_loop import _consequence_decision

    producer = _consequence_decision(rt, "seed-decider", CH_VERDICT)
    rt.handle_to_assembly[producer] = "seed-decider"
    contract = freeze_contract(rt, producer, "seed-decider", {}).with_outputs(
        {"action": action, "rationale": "no edge yet"})
    rt.grounded_pending[producer] = contract
    rt.pending[producer] = PendingJudgement(producer, CH_VERDICT, rt.n,
                                            opened_at_tick=rt.ticks_consumed)
    return producer, contract


def test_a_hold_is_settled_by_the_trade_it_passed_up_at_the_horizon():
    rt = _runtime()
    _mids(rt, BTC="100")
    producer, contract = _frozen_hold(rt)
    assert contract.reference_mids == (("BTC", "100"),)
    _mids(rt, BTC="102")
    rt.ticks_consumed = contract.due_tick
    rt._settle_due_grounded()
    settled = rt.queue.history(producer)[0]
    assert settled.status is SettleStatus.SETTLED
    assert settled.definition_version == OPPORTUNITY_DEFINITION
    assert 0 < settled.score < 0.1  # 200 bp passed up against a small round trip
    assert producer not in rt.grounded_pending
    # No final judge was commissioned or paid for a fact the world already priced.
    assert not any(e.payload.get("grounded_consequence") for e in rt.internal)
    inbox = rt.outcomes.unread("seed-decider")
    assert any(item.get("kind") == "opportunity_cost" for item in inbox.get("items", []))


def test_defer_is_inaction_too_but_a_decision_that_traded_keeps_the_judge_path():
    rt = _runtime()
    _mids(rt, BTC="100")
    deferred, contract = _frozen_hold(rt, action="defer")
    rt.ticks_consumed = contract.due_tick
    rt._settle_due_grounded()
    assert rt.queue.history(deferred)[0].definition_version == OPPORTUNITY_DEFINITION
    rt2 = _runtime()
    _mids(rt2, BTC="100")
    ordered, contract2 = _frozen_hold(rt2, action="order")
    rt2.ticks_consumed = contract2.due_tick
    rt2._settle_due_grounded()
    assert rt2.grounded_pending[ordered].final_requested  # a fresh judge is commissioned
