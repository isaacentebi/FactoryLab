"""A spot fill the lot book refused is not P&L evidence (defect 15b).

The consequence book refuses a spot fill it cannot account (no owning account,
or a sell beyond inventory). The refusal was ledgered, and then the venue's own
realized figure for that same fill was booked as a venue settlement and counted
into the window's realized P&L anyway.
"""

from factorylab.world.events import WorldEvent, WorldEventKind
from tests.conftest import make_runtime


def test_a_refused_spot_fill_books_no_settlement_and_no_realized_pnl():
    rt = make_runtime()
    before = (rt.window.realized_pnl_micro, rt.realized_to_date, rt.fees_to_date)
    fill = WorldEvent(WorldEventKind.FILL, rt.clock.now_ns, "fake", {
        "order_id": "stranger-1", "coin": "PURR/USDC", "is_buy": False, "size": "10",
        "px": "2", "fee_usd": "0.01", "realized_usd": "5", "liquidation": False,
        "market": "spot"})
    rt._settle_exchange_effects([fill], observe_positions=False)
    items = rt.ledger._recovery_items()
    assert any(i["kind"] == "consequence.refused" and i.get("order_id") == "stranger-1"
               for i in items)
    assert not any(i["kind"] == "venue.settled" and i.get("reference") == "fill:stranger-1"
                   for i in items)
    assert (rt.window.realized_pnl_micro, rt.realized_to_date, rt.fees_to_date) == before
