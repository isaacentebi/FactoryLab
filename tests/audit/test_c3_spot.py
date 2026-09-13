"""Cold audit round three, seat 3: spot inventory the runtime did not account for.

Each test reproduces one finding in docs/audits/v3/defects-fable.md and fails on the
audited commit. Nothing here touches a network.
"""

from decimal import Decimal

import pytest

from factorylab.runtime.loop import Runtime
from factorylab.runtime.worlds import load_manifest
from factorylab.world.exchange import FakeExchange, Order
from factorylab.world.scripted import ScriptedProvider
from tests.runtime.test_loop import _consequence_decision


def _venue():
    return FakeExchange(coins=("BTC", "ETH"), spot_pairs=("BTC/USDC",), start_cash_usd=Decimal(100),
                        start_prices={"BTC": Decimal(100), "ETH": Decimal(10)},
                        spread_bps=Decimal(0), fee_bps=Decimal(0), step_bps=Decimal(0))


def _runtime(exchange):
    return Runtime(load_manifest("scripted"), events=0, seed=1, initial_balance_micro=None,
                   ledger_path=None, drip=False, router_gamma=.1, provider=ScriptedProvider(),
                   exchange=exchange)


def _producer(rt):
    handle = _consequence_decision(rt, "seed-decider", "verdict")
    rt.consequences.start(handle, 0)
    return handle


def test_finding_4_closing_spot_inventory_the_world_did_not_buy_kills_the_world():
    """An account that already holds spot BTC when the world launches (a rehearsal on the
    same account, an operator's dust) has inventory the runtime's ``spot_inventory`` never
    saw. ``venue.close`` sells the venue's balance; the fill exceeds the accounted
    inventory and ``_settle_exchange_effects`` raises inside the event loop. The fill is
    journaled, so the exception replays on every resume."""
    exchange = _venue()
    exchange.class_transfer(Decimal(50), False)
    assert exchange.place(Order("BTC/USDC", True, Decimal("0.2"), market="spot")).status == "filled"
    exchange.drain_events()  # pre-launch: nobody's fill
    rt = _runtime(exchange)
    try:
        handle = _producer(rt)
        try:
            result, _cost = rt._run_tool("seed-decider", handle, {
                "tool": "venue.close", "args": {"coin": "BTC/USDC", "market": "spot"}})
        except ValueError as exc:
            pytest.fail(f"a spot close raised out of the event loop: {exc}")
        assert result["status"] in ("filled", "rejected")
    finally:
        rt._ledger_lock.close()


def test_finding_4_an_unattributed_spot_buy_poisons_the_lot_table_for_the_next_sell():
    """A spot buy fill whose order belongs to no open account (a resting order from before
    launch, an order the runtime never saw) is refused by the lot table but still enters
    ``spot_inventory``. The next producer sell of that inventory passes the runtime's
    check and raises in ``LotTable.fill``: two books disagree and the loop dies."""
    exchange = _venue()
    rt = _runtime(exchange)
    try:
        rt.treasury.transfer("perps_to_spot", "50", handle="fund", now_ns=1)
        rt.treasury.tick(2)
        exchange.sync_cash(rt.treasury.venue_balance_usd)
        stray = exchange.place(Order("BTC/USDC", True, Decimal("0.2"), market="spot"))
        assert stray.status == "filled"
        rt._settle_exchange_effects(exchange.drain_events())  # journaled, refused by the table
        refused = [i for i in rt.ledger._recovery_items() if i["kind"] == "consequence.refused"]
        # T36: the old assertion required the bug; a refused fill must not credit inventory.
        assert refused and rt.spot_inventory.get("BTC/USDC", (Decimal(0), Decimal(0)))[0] == 0
        handle = _producer(rt)
        try:
            result, _cost = rt._run_tool("seed-decider", handle, {
                "tool": "venue.place_market",
                "args": {"coin": "BTC/USDC", "market": "spot", "side": "sell", "size": "0.1"}})
        except ValueError as exc:
            pytest.fail(f"a spot sell raised out of the event loop: {exc}")
        assert result["status"] in ("filled", "rejected")
    finally:
        rt._ledger_lock.close()
