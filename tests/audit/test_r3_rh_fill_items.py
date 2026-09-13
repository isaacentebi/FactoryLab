"""Round three, rehearsal row T56: a counted fill is a ledgered fill.

The rehearsal read ``stats.fills == 2`` and a window ``fills`` observation of
``2.0`` while ``factorylab postmortem --kinds event:Fill`` over all 40,651 items
matched nothing. ``event:Fill`` is written by the bus when the queued Fill is
*delivered* to the population, which on that world sat behind about 1,687
MarketMid events per tick and never came. Counting happens earlier, in
``_settle_exchange_effects``. The count and the diary now move together: each
counted fill writes one ``fill.counted`` item at the moment it is counted,
whatever the delivery queue is doing.
"""

from decimal import Decimal

from factorylab.cortex.request import Return
from factorylab.kernel.queue import PropensityRecord
from factorylab.runtime.live import LiveClock
from factorylab.runtime.loop import Runtime
from factorylab.runtime.worlds import manifest_from_dict
from tests.conftest import make_runtime
from tests.runtime.test_connectors import ledger_items
from tests.runtime.test_live import FakeTime, StubExchange


def decision(rt, owner="seed-decider"):
    handle = rt.queue.open(
        actor=owner, event_id="fill-items", propensity=PropensityRecord(
            (owner,), (1.,), owner, 0, owner, "test"), channel="verdict",
        deadline_ns=rt.clock.now_ns + 10**12, parent_handle=None, cost_ceiling=10_000_000,
    )
    rt.handle_to_assembly[handle] = owner
    return handle


def counted(rt):
    return [i for i in ledger_items(rt, "fill.counted")]


def delivered(rt):
    """The ``event:Fill`` items a postmortem would match: written on delivery, not on counting."""
    return [i for i in ledger_items(rt, "event") if i["event"]["kind"] == "Fill"]


def test_every_counted_fill_has_a_ledgered_item_on_the_fake_exchange():
    rt = make_runtime()
    rt._manage_reserve_window()
    for size in ("0.0002", "0.0003"):
        handle = decision(rt)
        rt.consequences.start(handle, rt.n)
        rt._execute_outputs(Return(handle, {
            "action": "order", "coin": "BTC", "side": "buy", "size": size}, 0, "ok"))
    assert rt.stats.fills == 2
    items = counted(rt)
    assert len(items) == rt.stats.fills
    assert [i["coin"] for i in items] == ["BTC", "BTC"]
    assert {i["market"] for i in items} == {"perp"}
    assert all(i["order_id"] and Decimal(i["px"]) > 0 for i in items)
    assert sum(i["realized_micro"] for i in items) == rt.realized_to_date
    # The rehearsal's exact shape: counted and settled, still queued for delivery,
    # so no event:Fill exists yet. The count is no longer alone in the diary.
    assert not delivered(rt) and len(rt.internal) == 2


LIVE_SHAPED = {
    "name": "stubnet",
    "seed": 5,
    "initial_balance_usd": "20",
    "tick_interval": "1s",
    "exchange": {"kind": "hyperliquid", "mainnet": False, "coins": ["BTC", "ETH"]},
    "models": [{"id": "fake-haiku", "provider": "fake", "input_usd_per_mtok": "1",
                "output_usd_per_mtok": "5"}],
    "assemblies": [
        {"id": "seed-decider", "role": "producer", "model_id": "fake-haiku",
         "accepts": ["Tick", "Fill"]},
        {"id": "eval-a", "role": "evaluator", "model_id": "fake-haiku",
         "accepts": ["ProducerReturn"]},
        {"id": "meta-a", "role": "meta", "model_id": "fake-haiku", "accepts": ["Verdict"]},
    ],
    "novelty": {"share": 0.1, "window": "1d"},
}


def test_every_counted_fill_has_a_ledgered_item_on_a_live_shaped_stub():
    ft = FakeTime()
    clock = LiveClock(interval_ns=1_000_000_000, count=25,
                      now_ns=ft.now_ns, sleep=ft.sleep).events()
    rt = Runtime(manifest_from_dict(LIVE_SHAPED), events=25, seed=5,
                 initial_balance_micro=None, ledger_path=None, drip=True, router_gamma=.1,
                 exchange=StubExchange(ft.now_ns()), clock_source=clock)
    summary = rt.run()
    assert summary["live"] is True and summary["stats"]["fills"] == 2
    items = counted(rt)
    assert len(items) == summary["stats"]["fills"]
    assert {i["coin"] for i in items} == {"BTC"}
    # A bounded broadcast lets the queue drain, so delivery agrees with counting here.
    assert len(delivered(rt)) == summary["stats"]["fills"]
    # The window's own count and the diary agree about the same two fills.
    assert sum(i["window"] == rt.window.index for i in items) == rt.window.fills
    assert summary["wallet_conservation"] is True
