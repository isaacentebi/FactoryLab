import pytest

from factorylab.kernel.events import Bus
from factorylab.kernel.queue import PropensityRecord
from factorylab.kernel.termination import Termination
from factorylab.settlement import SEED_VOCABULARY, Observer, WindowFacts
from factorylab.settlement.consequence import ReturnConsequences
from factorylab.settlement.vocabulary import RETURN_PAID_OFF


def test_kernel_predicate_is_not_proposable_or_observable_from_population_facts():
    assert RETURN_PAID_OFF.proposable is False
    assert RETURN_PAID_OFF.id not in {p.id for p in SEED_VOCABULARY}
    with pytest.raises(ValueError, match="unknown predicate"):
        Observer().observe(
            RETURN_PAID_OFF.id,
            {"horizon_events": 200},
            WindowFacts(1, 10, 1, ({"kind": "Fill", "payload": {"return_paid_off": 1}},)),
        )


def test_kernel_forecast_settles_immediately_and_cannot_be_censored_by_window_observer(
    ledger,
    queue,
    book,
    standing,
    settler,
    baseline,
    seal_forecast,
):
    consequences = ReturnConsequences(ledger, 200)
    consequences.start("producer-1", 0)
    consequences.finish("producer-1", 5)
    consequences.resolve(0)
    forecast = seal_forecast(predicate_id=RETURN_PAID_OFF.id, q=1.0)
    assert (
        settler.settle_due(1000, lambda _: pytest.fail("kernel facts reached public observer"))
        == []
    )
    (result,) = settler.settle_consequences(consequences.payoff)
    assert result.handle == forecast.handle and result.y == 0 and result.brier == 0
    assert result.baseline_brier == 0.75
    assert standing.skill(forecast.evaluator_id) < 0
    assert baseline.baseline_q(RETURN_PAID_OFF.id) == 0
    assert settler.settle_consequences(consequences.payoff) == []
    assert len(queue.history(forecast.handle)) == 1 and book.outstanding() == 0


def test_verdict_commitment_uses_raw_q_original_evaluator_and_return_backstop(
    ledger,
    queue,
    book,
):
    parent = queue.open(
        actor="router",
        event_id="event",
        channel="conformity",
        deadline_ns=200,
        propensity=PropensityRecord(("judge",), (1.0,), "judge", 0, "router", "state"),
        parent_handle=None,
        cost_ceiling=0,
    )
    consequences = ReturnConsequences(ledger, 200)
    consequences.start("producer", 5)
    forecast = consequences.seal_verdict(
        book,
        queue,
        evaluator_handle=parent,
        evaluator_id="judge",
        about="producer",
        verdict=0.876,
        event=10,
        now_ns=100,
        tick_ns=1,
    )
    assert forecast.q == 0.876 and forecast.due_at_event == 205 and forecast.seal
    decision = queue.get(forecast.handle)
    assert decision.actor == "judge" and decision.channel == "consequence"
    assert decision.parent_handle == parent


def test_backstop_outcome_and_forecast_evidence_are_ledger_first_and_marked(
    ledger,
    queue,
    book,
    clock,
    standing,
    settler,
    seal_forecast,
):
    consequences = ReturnConsequences(ledger, 2)
    consequences.start("producer-1", 0)
    consequences.order_result(
        "producer-1",
        {"order_id": "1", "status": "filled", "filled_size": "1"},
        {},
        0,
    )
    consequences.observe(
        "Fill",
        {
            "order_id": "1",
            "coin": "BTC",
            "is_buy": True,
            "size": "1",
            "px": "100",
            "fee_usd": "1",
            "realized_usd": "0",
        },
        0,
    )
    consequences.finish("producer-1", 5)
    forecast = seal_forecast(predicate_id=RETURN_PAID_OFF.id)
    consequences.observe("MarketMid", {"coin": "BTC", "mid": "90"}, 2)
    consequences.resolve(2)
    (result,) = settler.settle_consequences(consequences.payoff)
    assert result.marked and result.y == 0
    marker = ledger.append({"kind": "test.marker"})
    Termination(ledger=ledger, bus=Bus(ledger), clock_ns=clock).kill("test")
    items = [ledger.decrypt_item(i) for i in range(marker)]
    outcome = next(i for i in items if i["kind"] == "consequence.outcome")
    evidence = next(i for i in items if i["kind"] == "forecast.consequence")
    settlement = next(i for i in items if i["kind"] == "decision.settle")
    assert outcome["marked"] and evidence["marked"]
    assert evidence["handle"] == forecast.handle
    assert evidence["about_handle"] == "producer-1" and evidence["net_micro"] == -11_000_000
    assert outcome["seq"] < evidence["seq"] < settlement["seq"]
    assert ledger.verify()


@pytest.mark.parametrize("operation", ["return", "cost", "order", "fill", "outcome"])
def test_failed_ledger_write_leaves_accounting_unchanged(operation, ledger, monkeypatch):
    consequences = ReturnConsequences(ledger, 2)
    if operation != "return":
        consequences.start("owner", 0)
    if operation == "outcome":
        consequences.finish("owner", 1)
    before = consequences.table
    append = ledger.append

    def failing_append(item):
        if item["kind"] == f"consequence.{operation}":
            raise OSError("write failed")
        return append(item)

    monkeypatch.setattr(ledger, "append", failing_append)
    actions = {
        "return": lambda: consequences.start("owner", 0),
        "cost": lambda: consequences.finish("owner", 1),
        "order": lambda: consequences.order_result(
            "owner",
            {"order_id": "1", "status": "filled", "filled_size": "1"},
            {},
            0,
        ),
        "fill": lambda: consequences.observe(
            "Fill",
            {
                "order_id": "1",
                "coin": "BTC",
                "is_buy": True,
                "size": "1",
                "px": "100",
                "fee_usd": "0",
                "realized_usd": "0",
            },
            0,
        ),
        "outcome": lambda: consequences.resolve(0),
    }
    with pytest.raises(OSError, match="write failed"):
        actions[operation]()
    assert consequences.table == before


def test_failed_consequence_score_delivery_does_not_train_baseline_or_standing(
    ledger,
    queue,
    standing,
    baseline,
    book,
    settler,
    seal_forecast,
    monkeypatch,
):
    consequences = ReturnConsequences(ledger, 2)
    consequences.start("producer-1", 0)
    consequences.finish("producer-1", 5)
    consequences.resolve(0)
    forecast = seal_forecast(predicate_id=RETURN_PAID_OFF.id)
    append = ledger.append

    def failing_append(item):
        if item["kind"] == "decision.settle":
            raise OSError("write failed")
        return append(item)

    with monkeypatch.context() as patch:
        patch.setattr(ledger, "append", failing_append)
        with pytest.raises(OSError):
            settler.settle_consequences(consequences.payoff)
    assert standing.snapshot() == {}
    assert baseline.baseline_q(RETURN_PAID_OFF.id) == 0.5
    assert queue.history(forecast.handle) == ()
    assert len(settler.settle_consequences(consequences.payoff)) == 1
    assert book.outstanding() == 0


def test_fill_cursor_keeps_partial_and_identical_fills_and_deduplicates_polls(ledger):
    from types import SimpleNamespace

    from factorylab.settlement.consequence import FillCursor

    def fill(ts, size="1"):
        return SimpleNamespace(
            order_id="same-order",
            coin="BTC",
            is_buy=True,
            size=size,
            px="100",
            fee="0.01",
            realized="0",
            liquidation=False,
            ts_ns=ts,
        )

    class Exchange:
        rows = [fill(1), fill(1)]

        def fills(self, since_ns):
            return [f for f in self.rows if f.ts_ns >= since_ns]

    exchange = Exchange()
    cursor = FillCursor(ledger)
    assert len(cursor.poll(exchange)) == 2
    assert cursor.poll(exchange) == []
    exchange.rows.append(fill(1))
    assert len(cursor.poll(exchange)) == 1
    exchange.rows.extend([fill(2, "0.5"), fill(3)])
    assert [ts for ts, _ in cursor.poll(exchange)] == [2, 3]
    assert cursor.poll(exchange) == []
    assert cursor.since_ns == 3 and len(cursor.seen) == 1


def test_fill_cursor_does_not_advance_on_failed_ledger_write(ledger, monkeypatch):
    from types import SimpleNamespace

    from factorylab.settlement.consequence import FillCursor

    fill = SimpleNamespace(
        order_id="1",
        coin="BTC",
        is_buy=True,
        size="1",
        px="100",
        fee="0",
        realized="0",
        liquidation=False,
        ts_ns=1,
    )
    cursor = FillCursor(ledger)
    exchange = SimpleNamespace(fills=lambda _: [fill])

    def fail(item):
        raise OSError("write failed")

    with monkeypatch.context() as patch:
        patch.setattr(ledger, "append", fail)
        with pytest.raises(OSError):
            cursor.poll(exchange)
    assert cursor.since_ns == 0 and cursor.seen == {}
    assert len(cursor.poll(exchange)) == 1
