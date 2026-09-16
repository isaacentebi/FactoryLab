"""Round three, rehearsal row T54: a refused order is public before it is a statistic.

The rehearsal's SOL order was refused by ``_order_collateral`` before submission,
and the report reads "a rejected order leaves no ledger item at all". Half of
that is a naming mismatch: a collateral exclusion already wrote
``order.infeasible``. What was true is that no refusal of any shape reached the
population, so a producer could only try the same order again, and that two
refusal paths did write nothing at all — an order held off by a pending class
transfer, and an order output the kernel cannot read as an order. Every
pre-submission refusal now leaves one ledger item carrying its reason and one
line of ``registration_feedback``, the surface the world block already
publishes for refused proposals.
"""

from decimal import Decimal

import pytest

from factorylab.cortex.request import Return
from factorylab.kernel.queue import PropensityRecord
from tests.conftest import make_runtime
from tests.runtime.test_connectors import ledger_items


def decision(rt, owner="seed-decider"):
    handle = rt.queue.open(
        actor=owner, event_id="order-refusal", propensity=PropensityRecord(
            (owner,), (1.,), owner, 0, owner, "test"), channel="verdict",
        deadline_ns=rt.clock.now_ns + 10**12, parent_handle=None, cost_ceiling=10_000_000,
    )
    rt.handle_to_assembly[handle] = owner
    return handle


@pytest.fixture
def poor():
    """A world whose venue cannot collateralise the order these tests send.

    Rehearsal 3, defect 1 moved the check from the compute wallet to the venue's
    own free collateral, so what makes this world poor is the venue: the fake
    carries $100 and a 0.01 BTC order needs $200 of margin at its 3x. The wallet
    is small too, and no longer the reason. ``tests/audit/test_r1_venue_collateral``
    pins the semantics; these tests are about what a refusal publishes.
    """
    rt = make_runtime(balance=1_000_000)
    rt._manage_reserve_window()
    assert rt.exchange.account().equity_usd < Decimal("0.01") * rt.exchange.mids()["BTC"] / 3
    return rt


def refusals(rt):
    return [i for i in ledger_items(rt) if i["kind"] in ("order.infeasible", "order.refused")]


def feedback(rt):
    return [entry["reason"] for entry in rt.registration_feedback]


def test_an_excluded_order_output_is_ledgered_and_fed_back(poor):
    handle = decision(poor)
    poor.consequences.start(handle, poor.n)
    poor._execute_outputs(Return(handle, {
        "action": "order", "coin": "BTC", "side": "buy", "size": "0.01"}, 0, "ok"))
    assert poor.stats.orders_rejected == 1
    items = refusals(poor)
    assert len(items) == 1 and items[0]["handle"] == handle
    assert "collateral" in items[0]["reason"]
    assert any("collateral" in reason for reason in feedback(poor)), feedback(poor)


def test_an_excluded_order_tool_call_is_ledgered_and_fed_back(poor):
    handle = decision(poor)
    poor.consequences.start(handle, poor.n)
    result = poor._run_tool("seed-decider", handle, {
        "tool": "venue.place_market",
        "args": {"coin": "BTC", "side": "buy", "size": "0.01"}})[0]
    assert result["status"] == "rejected"
    assert [i["kind"] for i in refusals(poor)] == ["order.infeasible"]
    assert any("collateral" in reason for reason in feedback(poor)), feedback(poor)


def test_a_pending_class_transfer_refusal_is_ledgered_and_fed_back():
    rt = make_runtime()
    rt._manage_reserve_window()
    rt.treasury.state = {"status": "submitted", "direction": "perps_to_spot"}
    handle = decision(rt)
    rt.consequences.start(handle, rt.n)
    result = rt._venue_write(handle, "venue.place_market", {
        "coin": "BTC", "side": "buy", "size": "0.001"}, slot="output")
    assert result["status"] == "rejected"
    assert [i["kind"] for i in refusals(rt)] == ["order.refused"]
    assert any("class transfer" in reason for reason in feedback(rt)), feedback(rt)


def test_an_unreadable_order_output_is_ledgered_and_fed_back():
    rt = make_runtime()
    rt._manage_reserve_window()
    handle = decision(rt)
    rt.consequences.start(handle, rt.n)
    rt._execute_outputs(Return(handle, {"action": "order", "coin": "BTC", "size": "lots"},
                               0, "ok"))
    assert [i["kind"] for i in refusals(rt)] == ["order.refused"]
    assert any("not a readable order" in reason for reason in feedback(rt)), feedback(rt)


def test_an_accepted_order_is_neither_refused_nor_fed_back():
    rt = make_runtime()
    rt._manage_reserve_window()
    handle = decision(rt)
    rt.consequences.start(handle, rt.n)
    rt._execute_outputs(Return(handle, {
        "action": "order", "coin": "BTC", "side": "buy", "size": "0.0001"}, 0, "ok"))
    assert rt.stats.orders_placed == 1 and rt.stats.orders_rejected == 0
    assert not refusals(rt) and not feedback(rt)
    assert any(lot.coin == "BTC" for lot in rt.consequences.table.lots)
    assert rt.spot_inventory.get("BTC", (Decimal(0),))[0] == Decimal(0)
