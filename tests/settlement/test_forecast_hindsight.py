"""A payoff forecast precedes its outcome (defect 7).

A payoff forecast could be sealed on a return whose outcome was already fixed, or
on a finished return that held no position and no order, whose outcome
(``y = 0``: it did nothing that could pay) was determined the moment it
finished. Scoring that claim pays a judge for reading an answer, not for
forecasting one. Such a forecast is refused, with the reason ledgered.
"""

from factorylab.kernel.queue import PropensityRecord
from factorylab.settlement.consequence import ReturnConsequences


def _seal(book, queue, consequences, about="producer"):
    judge = queue.open(actor="router", event_id="event", channel="conformity", deadline_ns=200,
                       propensity=PropensityRecord(("judge",), (1.0,), "judge", 0, "router", "s"),
                       parent_handle=None, cost_ceiling=0)
    return consequences.seal_verdict(book, queue, evaluator_handle=judge,
                                     evaluator_id="judge", about=about, payoff=0.9, event=10,
                                     now_ns=100, tick_ns=1)


def _refusals(ledger):
    return [i for i in ledger._recovery_items() if i["kind"] == "forecast.refused"]


def test_a_forecast_on_a_fixed_outcome_is_refused(ledger, queue, book):
    consequences = ReturnConsequences(ledger, 200)
    consequences.start("producer", 5)
    consequences.finish("producer", 3)
    consequences.resolve(6)
    assert consequences.payoff("producer") is not None
    assert _seal(book, queue, consequences) is None
    assert book.outstanding() == 0
    (refusal,) = _refusals(ledger)
    assert refusal["about_handle"] == "producer" and "fixed" in refusal["reason"]


def test_a_forecast_on_a_finished_return_with_no_exposure_is_refused(ledger, queue, book):
    consequences = ReturnConsequences(ledger, 200)
    consequences.start("producer", 5)
    consequences.finish("producer", 3)  # finished, no order, no lot: y is already 0
    assert consequences.payoff("producer") is None
    assert _seal(book, queue, consequences) is None
    assert "determin" in _refusals(ledger)[0]["reason"]


def test_a_forecast_on_open_exposure_is_sealed(ledger, queue, book):
    consequences = ReturnConsequences(ledger, 200)
    consequences.start("producer", 5)
    consequences.order_result("producer", {"status": "resting", "order_id": "o1"},
                              {"size": "1"}, 5)
    consequences.finish("producer", 3)
    forecast = _seal(book, queue, consequences)
    assert forecast is not None and forecast.seal and not _refusals(ledger)
