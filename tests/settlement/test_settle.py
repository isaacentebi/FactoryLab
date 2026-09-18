from dataclasses import FrozenInstanceError, asdict

import pytest

from factorylab.kernel.events import Bus
from factorylab.kernel.queue import LearningReturn, PropensityRecord, SettleStatus
from factorylab.kernel.termination import Termination
from factorylab.settlement import Observer, Settled, Settler, WindowFacts


def test_three_forecasts_two_evaluators_settle_at_due_events_with_ledger_first(
    ledger, queue, book, baseline, standing, settler, seal_forecast, clock
):
    producer = queue.open(
        actor="producer",
        event_id="launch",
        propensity=PropensityRecord(("produce",), (1.0,), "produce", 0, "producer", "state"),
        channel="verdict",
        deadline_ns=1_000,
        parent_handle=None,
        cost_ceiling=0,
    )
    first = seal_forecast(
        q=0.8,
        due_at_event=2,
        params={"horizon_events": 2},
        parent_handle=producer,
        about_handle=producer,
    )
    second = seal_forecast(
        evaluator_id="judge-b",
        q=0.2,
        due_at_event=4,
        params={"horizon_events": 4},
        parent_handle=producer,
        about_handle=producer,
    )
    third = seal_forecast(
        q=0.9,
        due_at_event=4,
        params={"horizon_events": 4},
        parent_handle=producer,
        about_handle=producer,
    )
    windows = {
        first.handle: WindowFacts(1_000_000, 1_100_000, 900_000, ()),
        second.handle: WindowFacts(1_000_000, 900_000, 800_000, ()),
        third.handle: WindowFacts(1_000_000, 900_000, 800_000, ()),
    }
    seen = []

    def facts_for(forecast):
        assert forecast.seal
        assert clock.now >= 100 + forecast.due_at_event
        assert not queue.history(forecast.handle)
        seen.append(forecast)
        return windows[forecast.handle]

    assert settler.settle_due(1, facts_for) == []
    assert seen == []
    clock.now = 102
    first_results = settler.settle_due(2, facts_for)
    # Restated for R3-D: a settlement now also writes its own learning receipt
    # (GPT-6 third reading §6.A), whose id rides on the result. The scored fields
    # are unchanged; the assertion names the receipt instead of pinning it to None.
    assert first_results == [
        Settled(
            first.handle,
            "judge-a",
            producer,
            "wallet_up",
            1,
            pytest.approx(0.96),
            0.75,
            SettleStatus.SETTLED,
            receipt=first_results[0].receipt,
        )
    ]
    assert book.receipts.get(first_results[0].receipt).score == pytest.approx(0.96)
    assert baseline.baseline_q("wallet_up") == 1.0
    # Edition 3 (C3): every registered predicate trains standing, equally while no
    # card names a scope. A16's privilege for return_paid_off is gone.
    assert standing.snapshot()["judge-a"]["settled"] == 1
    assert standing.snapshot()["judge-a"]["mean_brier"] == pytest.approx(0.96)
    assert book.outstanding() == 2
    clock.now = 103
    assert settler.settle_due(3, facts_for) == []
    clock.now = 104
    remaining = settler.settle_due(4, facts_for)
    assert [result.handle for result in remaining] == [second.handle, third.handle]
    assert [result.y for result in remaining] == [0, 0]
    # Defect 8: the second and third forecasts ask one question (same subject, predicate,
    # parameters and interval), so both are scored against the base rate before its
    # answer, and the answer enters that base rate once.
    assert [result.baseline_brier for result in remaining] == [0.0, 0.0]
    assert [result.brier for result in remaining] == pytest.approx([0.96, 0.19])
    assert seen == [first, second, third]
    assert baseline.baseline_q("wallet_up") == pytest.approx(1 / 2)
    assert [standing.snapshot()[j]["settled"] for j in ("judge-a", "judge-b")] == [2, 1]
    # Coverage is every forecast this judge was asked for, not one privileged predicate.
    assert standing.coverage("judge-a") == 1.0 and standing.coverage("judge-b") == 1.0
    # Standing follows every claim. Scored against the pre-answer base rate, both judges'
    # claims beat it; judge-b's by more, so judge-a is weighted below judge-b.
    assert standing.skill("judge-a") == pytest.approx(0.2)
    assert standing.weight("judge-a") == pytest.approx(0.7)
    assert standing.weight("judge-b") == 1.0
    assert book.outstanding() == 0
    assert book.requested("judge-a") == 2 and book.requested("judge-b") == 1
    for result in first_results + remaining:
        assert queue.history(result.handle) == (
            LearningReturn(
                result.handle, "consequence", result.brier, "brier-v1", SettleStatus.SETTLED, None
            ),
        )
        assert result.about_handle == producer
        assert queue.get(result.handle).parent_handle == producer
        ledger.append({"kind": "test.forecast_settled", "payload": asdict(result)})
    marker = ledger.append({"kind": "test.marker"})
    Termination(ledger=ledger, bus=Bus(ledger), clock_ns=clock).kill("test audit")
    items = [ledger.decrypt_item(seq) for seq in range(marker)]
    seals = [item for item in items if item["kind"] == "forecast.seal"]
    outcomes = [item for item in items if item["kind"] == "decision.settle"]
    assert len(seals) == len(outcomes) == 3
    assert max(item["seq"] for item in seals) < min(item["seq"] for item in outcomes)
    assert [item["ts"] for item in seals] == [100, 100, 100]
    assert [item["ts"] for item in outcomes] == [102, 104, 104]
    assert ledger.verify()


def test_censored_forecast_has_no_outcome_or_score_and_does_not_train(
    seal_forecast, settler, baseline, standing, queue, book
):
    forecast = seal_forecast()
    (result,) = settler.settle_due(10, lambda forecast: None)
    # Restated for R3-D: the censored settlement writes a learning receipt too,
    # with a null score and the reason there is none — an assessment that could
    # not be made is recorded as such rather than as a zero.
    assert result == Settled(
        forecast.handle,
        forecast.evaluator_id,
        forecast.about_handle,
        forecast.predicate_id,
        None,
        None,
        None,
        SettleStatus.CENSORED,
        receipt=result.receipt,
    )
    receipt = book.receipts.get(result.receipt)
    assert receipt.score is None and receipt.reason == "no observed fact"
    assert queue.history(forecast.handle) == (
        LearningReturn(
            forecast.handle, "consequence", 0.0, "brier-v1", SettleStatus.CENSORED, None
        ),
    )
    assert not queue.has_history(queue.get(forecast.handle).propensity.chosen)
    assert baseline.baseline_q("wallet_up") == 0.5
    # A censored commitment is no fact, so it trains nothing and only costs coverage.
    assert standing.snapshot() == {}
    assert standing.weight(forecast.evaluator_id) == 0.5
    assert book.outstanding() == 0
    with pytest.raises(FrozenInstanceError):
        result.y = 0


def test_censored_windows_leave_existing_history_unchanged_and_reduce_coverage(
    seal_forecast, settler, baseline, standing
):
    observed = seal_forecast(q=1.0)
    seal_forecast()
    results = settler.settle_due(
        10, lambda forecast: WindowFacts(1, 2, 1, ()) if forecast == observed else None
    )
    assert [result.status for result in results] == [SettleStatus.SETTLED, SettleStatus.CENSORED]
    assert baseline.baseline_q("wallet_up") == 1.0
    assert standing.snapshot()["judge-a"]["settled"] == 1
    assert standing.coverage("judge-a") == 0.5
    assert standing.weight("judge-a") == 0.5


@pytest.mark.parametrize("facts", [None, WindowFacts(100, 90, 80, ())])
def test_no_double_settlement_even_with_a_new_settler(
    facts, seal_forecast, settler, queue, book, baseline, standing, ledger
):
    forecast = seal_forecast()
    assert len(settler.settle_due(10, lambda forecast: facts)) == 1
    snapshot = standing.snapshot()
    before_q = baseline.baseline_q("wallet_up")
    next_seq = ledger.append({"kind": "test.before_retry"})

    def forbidden(forecast):
        pytest.fail("a settled forecast requested facts again")

    assert settler.settle_due(10, forbidden) == []
    assert Settler(book, queue, standing, baseline, Observer()).settle_due(100, forbidden) == []
    assert ledger.append({"kind": "test.after_retry"}) == next_seq + 1
    assert len(queue.history(forecast.handle)) == 1
    assert standing.snapshot() == snapshot
    assert baseline.baseline_q("wallet_up") == before_q


def test_due_batch_uses_seal_order_even_when_deadlines_are_out_of_order(seal_forecast, settler):
    later = seal_forecast(due_at_event=20)
    earlier = seal_forecast(due_at_event=10)
    results = settler.settle_due(20, lambda forecast: WindowFacts(1, 2, 1, ()))
    assert [result.handle for result in results] == [later.handle, earlier.handle]
    assert [result.baseline_brier for result in results] == [0.75, 1.0]


def test_failed_queue_write_leaves_baseline_and_standing_retryable(
    monkeypatch, seal_forecast, settler, queue, ledger, baseline, standing, book
):
    forecast = seal_forecast()
    original_append = ledger.append

    def fail_settlement(entry):
        if entry["kind"] == "decision.settle":
            raise OSError("injected settlement write failure")
        return original_append(entry)

    with monkeypatch.context() as patch:
        patch.setattr(ledger, "append", fail_settlement)
        with pytest.raises(OSError, match="injected settlement write failure"):
            settler.settle_due(10, lambda forecast: WindowFacts(1, 2, 1, ()))
    assert baseline.baseline_q("wallet_up") == 0.5
    # The failed write left nothing behind: no baseline, no standing, retryable.
    assert standing.snapshot() == {}
    assert book.outstanding() == 1
    assert queue.history(forecast.handle) == ()
    (result,) = settler.settle_due(10, lambda forecast: WindowFacts(1, 2, 1, ()))
    assert result.baseline_brier == 0.75
    assert baseline.baseline_q("wallet_up") == 1.0
    assert standing.snapshot()["judge-a"]["settled"] == 1


def test_facts_callback_error_does_not_manufacture_censoring(seal_forecast, settler, book, queue):
    forecast = seal_forecast()

    def fail(forecast):
        raise RuntimeError("observation unavailable due to a bug")

    with pytest.raises(RuntimeError, match="observation unavailable"):
        settler.settle_due(10, fail)
    assert book.outstanding() == 1
    assert queue.history(forecast.handle) == ()


def test_timed_out_forecast_keeps_original_handle_for_late_consequence(
    seal_forecast, settler, queue, clock
):
    forecast = seal_forecast()
    clock.now = 2_000
    assert queue.expire(clock.now) == [forecast.handle]
    (result,) = settler.settle_due(10, lambda forecast: WindowFacts(100, 100, 100, ()))
    history = queue.history(forecast.handle)
    assert [(item.channel, item.status) for item in history] == [
        ("timeout", SettleStatus.TIMED_OUT),
        ("consequence", SettleStatus.SETTLED),
    ]
    assert history[1].score == result.brier


def test_retired_evaluator_keeps_attribution_and_historical_feedback(
    seal_forecast, settler, queue, standing
):
    forecast = seal_forecast()
    queue.retire_actor(forecast.evaluator_id)
    (result,) = settler.settle_due(10, lambda forecast: WindowFacts(1, 2, 1, ()))
    assert result.status == SettleStatus.SETTLED
    assert queue.history(forecast.handle)[0].status == SettleStatus.HISTORICAL
    assert queue.history(forecast.handle)[0].score == result.brier
    assert standing.snapshot()[forecast.evaluator_id]["settled"] == 1


def test_replaced_population_definition_starts_its_own_prevalence_baseline(
    queue, book, baseline, standing, clock
):
    """A new version is a new claim: it cannot be scored against, or feed, the old base rate."""
    from factorylab.settlement import PrevalenceBaseline, open_forecast_decision
    from factorylab.settlement.settle import PredicateForecast, baseline_key
    from factorylab.settlement.vocabulary import PredicateBook

    predicates = PredicateBook(run=lambda code, facts: (True, None))
    settler = Settler(book, queue, standing, baseline, Observer(predicates))
    facts = WindowFacts(1_000_000, 1_100_000, 900_000, (), public_window={"fills": 1})

    def register(description, code):
        return predicates.register("has-fill", description, code, facts={"fills": 1},
                                   persist=lambda predicate: None)

    def seal(predicate, due):
        handle = open_forecast_decision(
            queue, evaluator_id="judge-a", event_id=f"forecast-{due}", q=0.5,
            deadline_ns=clock.now + 10_000, parent_handle=None, now_event=0, horizon=due)
        return book.seal(PredicateForecast(
            handle, "judge-a", "producer-1", "has-fill", {"horizon_events": due}, 0.5, 0, due,
            "", predicate=predicate))

    first = register("A fill occurred.", "def resolve(facts): return facts['fills'] > 0")
    seal(first, 1)
    (settled,) = settler.settle_due(1, lambda forecast: facts)
    assert settled.y == 1 and settled.baseline_brier == 0.75  # no history yet: base rate 0.5
    assert baseline.baseline_q("has-fill@1") == 1.0

    second = register("Any fill at all.", "def resolve(facts): return bool(facts['fills'])")
    assert (first.version, second.version) == (1, 2)
    assert baseline_key(seal(second, 2)) == "has-fill@2"
    (replaced,) = settler.settle_due(2, lambda forecast: facts)
    # The replacement is scored against an empty history, not its predecessor's certainty.
    assert replaced.baseline_brier == 0.75
    assert baseline.baseline_q("has-fill@2") == 1.0
    assert baseline.baseline_q("has-fill") == PrevalenceBaseline().baseline_q("has-fill") == 0.5


def test_verdicts_are_scored_against_a_fractional_unblamed_base_rate(settler, baseline):
    """Edition 2 (cold audit F3): the baseline learns the same target the judge is scored on.

    Ten percent of blame on every return used to teach the base rate "never
    unblamed" (target 0) while the judge was scored against 0.9, so a judge
    that always answered 0.9 showed 0.81 of invented skill.
    """
    first = settler.settle_verdict(evaluator_id="judge-a", about_handle="r1", q=0.9, share=0.1)
    assert first.outcome == pytest.approx(0.9)
    assert first.baseline_brier == pytest.approx(1 - (0.5 - 0.9) ** 2)  # the prior, once
    assert baseline.baseline_q("verdict_not_blamed") == pytest.approx(0.9)
    second = settler.settle_verdict(evaluator_id="judge-a", about_handle="r2", q=0.9, share=0.1)
    assert second.brier == second.baseline_brier == 1.0
    # One return, one observation: a second judge of r2 shares its pre-outcome base rate.
    again = settler.settle_verdict(evaluator_id="judge-b", about_handle="r2", q=0.5, share=0.1)
    assert again.baseline_brier == second.baseline_brier
    assert baseline.baseline_q("verdict_not_blamed") == pytest.approx(0.9)
    # The binary controls keep their matched targets.
    clean = settler.settle_verdict(evaluator_id="judge-a", about_handle="r3", q=1.0, share=0.0)
    assert clean.outcome == 1.0 and clean.brier == 1.0
    blamed = settler.settle_verdict(evaluator_id="judge-a", about_handle="r4", q=0.0, share=1.0)
    assert blamed.outcome == 0.0 and blamed.brier == 1.0
    assert baseline.baseline_q("verdict_not_blamed") == pytest.approx((0.9 + 0.9 + 1 + 0) / 4)
