"""Every forecast of one question is scored against the base rate before its answer (defect 8).

``settle_consequences`` already snapshots the payoff base rate before a return's
outcome enters it. ``settle_due`` recorded each forecast's outcome as it scored
it, so a second judge forecasting the very same question was scored against a
base rate that already contained the answer, and the one observation entered
the base rate once per judge.
"""

from factorylab.settlement import WindowFacts


def test_two_judges_of_one_question_share_the_pre_outcome_baseline(
    queue, book, baseline, settler, seal_forecast, clock,
):
    common = dict(about_handle="producer-1", params={"horizon_events": 4}, due_at_event=4)
    first = seal_forecast(evaluator_id="judge-a", q=0.9, **common)
    second = seal_forecast(evaluator_id="judge-b", q=0.9, **common)
    facts = WindowFacts(1_000_000, 900_000, 800_000, ())  # the wallet fell: y = 0
    clock.now = 104
    results = {r.handle: r for r in settler.settle_due(4, lambda _f: facts)}
    assert results[first.handle].y == results[second.handle].y == 0
    # Both are scored against the base rate as it stood before the answer (0.5).
    assert results[first.handle].baseline_brier == results[second.handle].baseline_brier == 0.75
    # One question, one observation: the base rate learns y = 0 once.
    assert baseline.support("wallet_up") == 1
    assert baseline.baseline_q("wallet_up") == 0.0
