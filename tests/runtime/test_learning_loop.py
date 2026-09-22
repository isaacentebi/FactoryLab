"""Every producer decision that a judge scored teaches its learner (PR121: 124 of 124 censored).

Under realized feedback a producer waits for a grounded finding. When the world
never speaks — unknown, malformed, or past the close horizon — the decision
used to be discarded, so a population of mostly holds learned nothing at all.
The fast provisional verdict now stands in, labelled apart from a grounded score.
"""

from factorylab.kernel.queue import SettleStatus
from factorylab.runtime.grounded import GROUNDED_DEFINITION
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
