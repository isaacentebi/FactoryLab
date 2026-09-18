"""Spot accounting admits launch balances and excludes refused inventory."""

from decimal import Decimal

import pytest

from factorylab.runtime.resume import restore_runtime, runtime_state
from factorylab.settlement.lots import LotTable
from factorylab.world.exchange import Order
from tests.helpers import spot_producer as _producer
from tests.helpers import spot_runtime as _runtime
from tests.helpers import spot_venue as _venue


def test_launch_inventory_has_unowned_lots_and_survives_restore():
    exchange = _venue()
    exchange.class_transfer(Decimal(50), False)
    exchange.place(Order("BTC/USDC", True, Decimal("0.2"), market="spot"))
    exchange.drain_events()
    rt = _runtime(exchange)
    restored = _runtime(_venue())
    try:
        assert rt.spot_inventory["BTC/USDC"] == (Decimal("0.2"), Decimal(100))
        lot, = rt.consequences.table.lots
        assert (lot.handle, lot.coin, lot.size, lot.px, lot.market) == (
            None, "BTC/USDC", Decimal("0.2"), 100, "spot",
        )
        restore_runtime(restored, runtime_state(rt))
        assert restored.spot_inventory == rt.spot_inventory
        assert restored.consequences.table == rt.consequences.table
        handle = _producer(restored)
        result, _ = restored._run_tool("seed-decider", handle, {
            "tool": "venue.close", "args": {"coin": "BTC/USDC", "market": "spot"},
        })
        assert result["status"] == "filled"
        assert restored.spot_inventory["BTC/USDC"][0] == 0
        assert restored.consequences.table.lots == ()
        assert restored.wallet.check_conservation()
    finally:
        rt._ledger_lock.close()
        restored._ledger_lock.close()


@pytest.mark.parametrize("tool", ["venue.place_market", "venue.close"])
def test_refused_buy_never_credits_inventory_or_enables_a_sell(tool):
    exchange = _venue()
    rt = _runtime(exchange)
    restored = _runtime(_venue())
    try:
        rt.treasury.transfer("perps_to_spot", "50", handle="fund", now_ns=1)
        rt.treasury.tick(2)
        exchange.sync_cash(rt.treasury.venue_balance_usd)
        result = exchange.place(Order("BTC/USDC", True, Decimal("0.2"), market="spot"))
        assert result.status == "filled"
        rt._settle_exchange_effects(exchange.drain_events())
        assert any(i["kind"] == "consequence.refused" for i in rt.ledger._recovery_items())
        assert rt.spot_inventory.get("BTC/USDC", (0, 0))[0] == 0
        assert not rt.consequences.table.lots
        restore_runtime(restored, runtime_state(rt))
        handle = _producer(restored)
        args = {"coin": "BTC/USDC", "market": "spot", "size": "0.1"}
        if tool == "venue.place_market":
            args["side"] = "sell"
        result, _ = restored._run_tool("seed-decider", handle, {"tool": tool, "args": args})
        assert result["status"] == "rejected"
        assert result["error"] == "spot sell exceeds accounted inventory"
        assert restored.spot_inventory.get("BTC/USDC", (0, 0))[0] == 0
        assert not restored.consequences.table.lots
    finally:
        rt._ledger_lock.close()
        restored._ledger_lock.close()


def test_launch_spot_seed_is_one_time_and_never_owns_a_decision():
    table = LotTable().seed_spot("BTC/USDC", "1", "100")
    assert not table.returns and not table.orders
    with pytest.raises(ValueError, match="once before decisions"):
        table.seed_spot("BTC/USDC", "1", "100")
    with pytest.raises(ValueError, match="once before decisions"):
        LotTable().start("a", 0).seed_spot("BTC/USDC", "1", "100")
    with pytest.raises(ValueError, match="invalid launch spot inventory"):
        LotTable().seed_spot("BTC/USDC", "-1", "100")


def test_deferred_spot_fill_updates_both_books_only_after_acknowledgement():
    exchange = _venue()
    rt = _runtime(exchange)
    restored = _runtime(_venue())
    try:
        rt.treasury.transfer("perps_to_spot", "50", handle="fund", now_ns=1)
        rt.treasury.tick(2)
        exchange.sync_cash(rt.treasury.venue_balance_usd)
        handle = _producer(rt)
        args = {"coin": "BTC/USDC", "market": "spot", "side": "buy", "size": "0.2"}
        client_id = handle + ":pending"
        intent = {"handle": handle, "client_id": client_id, "operation": "venue.place_market",
                  "args": args, "result": {"status": "uncertain"}}
        rt.ledger.append({"kind": "order.intent", **intent})
        rt.order_intents[client_id] = intent
        rt.consequences.order_intent(client_id, handle, args["coin"])
        result = exchange.place(Order("BTC/USDC", True, Decimal("0.2"),
                                      client_id=client_id, market="spot"))
        assert result.status == "filled"
        rt._settle_exchange_effects(exchange.drain_events())
        assert not rt.spot_inventory and not rt.consequences.table.lots
        assert rt.consequences.deferred_events
        restore_runtime(restored, runtime_state(rt))
        restored._recover_order(client_id)
        assert restored.spot_inventory["BTC/USDC"][0] == Decimal("0.2")
        assert restored.consequences.table.lots[0].size == Decimal("0.2")
        assert not restored.consequences.deferred_events
        inventory = dict(restored.spot_inventory)
        restored._recover_order(client_id)
        assert restored.spot_inventory == inventory
        close_args = {"coin": "BTC/USDC", "market": "spot"}
        closed = restored._venue_write(handle, "venue.close", close_args, slot="close")
        assert closed["status"] == "filled"
        restored._settle_exchange_effects(restored.exchange.drain_events())
        assert restored._venue_write(handle, "venue.close", close_args, slot="close") == closed
        assert not restored.consequences.table.lots
        assert restored.wallet.check_conservation()
    finally:
        rt._ledger_lock.close()
        restored._ledger_lock.close()


def test_durable_spot_inventory_write_replays_once_after_interruption(tmp_path):
    from factorylab.runtime.loop import Runtime
    from factorylab.runtime.resume import resume_runtime
    from factorylab.runtime.worlds import load_manifest

    class Interrupted(BaseException):
        pass

    def build(path=None):
        return Runtime(load_manifest("scripted"), events=40, seed=1,
                       initial_balance_micro=None, ledger_path=path, drip=False, router_gamma=.1)

    expected = build().run()
    path = str(tmp_path / "spot.jsonl")
    rt = build(path)
    append = rt.ledger.append

    def interrupt(item):
        result = append(item)
        if item["kind"] == "spot.inventory":
            raise Interrupted
        return result

    rt.ledger.append = interrupt
    with pytest.raises(Interrupted):
        rt.run()
    restored = resume_runtime(load_manifest("scripted"), path)
    actual = restored.run()
    assert actual["stats"]["resumes"] == 1
    actual["stats"]["resumes"] = 0
    assert actual == expected
