import pytest

from factorylab.kernel.events import Bus
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


def test_backstop_outcome_is_ledger_first_and_marked(ledger, clock):
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
    consequences.observe("MarketMid", {"coin": "BTC", "mid": "90"}, 2)
    (payoff,) = consequences.resolve(2)
    assert payoff.marked and payoff.y == 0 and payoff.net_micro == -11_000_000
    marker = ledger.append({"kind": "test.marker"})
    Termination(ledger=ledger, bus=Bus(ledger), clock_ns=clock).kill("test")
    items = [ledger.decrypt_item(i) for i in range(marker)]
    outcome = next(i for i in items if i["kind"] == "consequence.outcome")
    assert outcome["marked"] and outcome["handle"] == "producer-1"
    assert outcome["net_micro"] == -11_000_000
    assert ledger.verify()


@pytest.mark.parametrize("operation", ["return", "cost", "order", "fill", "outcome"])
def test_failed_ledger_write_leaves_accounting_unchanged(operation, ledger, monkeypatch):
    consequences = ReturnConsequences(ledger, 2)
    if operation != "return":
        consequences.start("owner", 0)
    if operation == "outcome":
        consequences.finish("owner", 1)
    if operation == "fill":  # only an attributed order's fill reaches the book
        consequences.order_result(
            "owner", {"order_id": "1", "status": "filled", "filled_size": "1"}, {}, 0,
        )
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
    cursor = FillCursor(ledger, start_ns=0)
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
    cursor = FillCursor(ledger, start_ns=0)
    exchange = SimpleNamespace(fills=lambda _: [fill])

    def fail(item):
        raise OSError("write failed")

    with monkeypatch.context() as patch:
        patch.setattr(ledger, "append", fail)
        with pytest.raises(OSError):
            cursor.poll(exchange)
    assert cursor.since_ns == 0 and cursor.seen == {}
    assert len(cursor.poll(exchange)) == 1


def test_fill_cursor_filters_history_keeps_launch_peers_and_failed_poll_boundary(ledger):
    from types import SimpleNamespace

    from factorylab.settlement.consequence import FillCursor

    def fill(ts, order):
        return SimpleNamespace(order_id=order, coin="BTC", is_buy=True, size="1", px="1",
                               fee="0", realized="0", liquidation=False, ts_ns=ts)

    rows = [fill(9, "old"), fill(10, "launch"), fill(11, "new")]
    calls = []

    def fills(since):
        calls.append(since)
        return rows  # even an adapter returning older rows cannot bypass the boundary

    cursor = FillCursor(ledger, start_ns=10)
    exchange = SimpleNamespace(fills=fills)
    assert [p["order_id"] for _, p in cursor.poll(exchange)] == ["launch", "new"]
    assert calls == [10]
    assert cursor.poll(exchange) == []
    rows.append(fill(11, "late-peer"))
    assert [p["order_id"] for _, p in cursor.poll(exchange)] == ["late-peer"]


def test_a_released_unresolved_order_censors_its_return_and_frees_every_later_one(ledger):
    """R4-C. The venue lost one order and never reported it. The return that sent it
    has no fill status, so it has no payoff: its outcome is censored with the reason
    documented (and a verdict on it has no measured outcome to be scored against) --
    and the hold it was keeping on every later return's outcome is gone."""
    consequences = ReturnConsequences(ledger, 200)
    for handle in ("producer-1", "producer-2"):
        consequences.start(handle, 0)
        consequences.finish(handle, 5)
    consequences.order_intent("producer-1:output", "producer-1", "BTC")
    # The hold #105 left in place: nothing resolves while the intent is pending.
    assert consequences.resolve(0) == []

    consequences.release_unresolved("producer-1:output", 1)
    assert any(i["kind"] == "order.unresolved_released" for i in ledger._recovery_items())
    # Every later return resolves again, and the censored outcome is handed over with them.
    fixed = consequences.resolve(2)
    assert {p.handle for p in fixed} == {"producer-1", "producer-2"}
    censored = next(p for p in fixed if p.handle == "producer-1")
    assert censored.censored == "external_unobservable" and not censored.marked
    assert consequences.resolve(3) == []  # and nothing is resolved twice
