"""T58: retained storage is a scored liability, not an unattributed wallet debit.

A note's recurring rent leaves the wallet against its writer's handle. Unless
that charge also reaches the writer's consequence cost while its outcome is
open, a trading return can resolve `return_paid_off = 1` on a margin its own
storage already consumed.
"""

import pytest

from factorylab.runtime.resume import restore_runtime, runtime_state
from factorylab.settlement.lots import LotTable
from tests.conftest import make_runtime
from tests.runtime.test_connectors import decision, ledger_items

RENT = 15  # "fact" + "public fact" = 15 UTF-8 bytes at one micro-USD per byte-window
COST = 10  # the return's own metered compute
GROSS = 20  # marked profit: more than the compute cost, less than compute plus rent


def writer(rt):
    """One return that both writes a public note and holds an open marked position."""
    handle = decision(rt)
    rt._start_return(handle)
    result, cost = rt._run_tool("seed-decider", handle, {
        "tool": "note.put", "args": {"key": "fact", "text": "public fact"}})
    assert "error" not in result and cost == RENT
    rt.consequences.order_result(
        handle, {"status": "filled", "order_id": "1", "filled_size": "1"}, {"size": "1"}, 0)
    rt.consequences.observe("Fill", {
        "order_id": "1", "coin": "BTC", "is_buy": True, "size": "1", "px": "100",
        "fee_usd": "0", "liquidation": False, "market": "perp"}, 0)
    rt.consequences.observe("MarketMid", {"coin": "BTC", "mid": "100.00002"}, 0)
    rt.consequences.finish(handle, COST)
    return handle


def boundary(rt):
    """Cross a real reserve-window boundary, which is when rent falls due."""
    rt.clock.now_ns += rt.m.novelty.window_ns
    rt._manage_reserve_window()


def resolve(rt):
    rt.consequences.resolve(rt.consequences.backstop + 1)
    return rt.consequences.payoff


def test_the_same_return_pays_off_before_rent_and_not_after_it():
    profitable = make_runtime()
    profitable._manage_reserve_window()
    handle = writer(profitable)
    payoff = resolve(profitable)(handle)
    assert (payoff.net_micro, payoff.cost_micro, payoff.y) == (GROSS, COST, 1)

    rt = make_runtime()
    rt._manage_reserve_window()
    handle = writer(rt)
    boundary(rt)
    assert ledger_items(rt, "note.rent")[-1]["cost"] == RENT
    assert rt.consequences.table.account(handle).carried_micro == RENT
    # The charge also reaches the cost card of the window it landed in.
    assert rt.window.decisions[handle]["cost"] == RENT

    restored = make_runtime()
    restore_runtime(restored, runtime_state(rt))
    assert restored.consequences.table.account(handle).carried_micro == RENT
    payoff = resolve(restored)(handle)
    assert (payoff.net_micro, payoff.cost_micro, payoff.y) == (GROSS, COST + RENT, 0)


def test_rent_after_a_final_outcome_stays_a_cost_line_of_its_owner_decision():
    rt = make_runtime()
    rt._manage_reserve_window()
    handle = writer(rt)
    boundary(rt)
    payoff = resolve(rt)(handle)
    assert payoff.y == 0 and payoff.cost_micro == COST + RENT

    boundary(rt)
    assert ledger_items(rt, "note.rent")[-1]["cost"] == RENT
    # A fixed outcome cannot be reopened, so the liability lands on the note's
    # current owner decision as its own cost contribution for this window.
    assert rt.consequences.table.account(handle).carried_micro == RENT
    assert rt.window.decisions[handle]["cost"] == RENT
    assert rt.consequences.payoff(handle) == payoff


def test_a_carried_liability_is_money_and_cannot_reopen_a_fixed_outcome():
    table = LotTable().start("r", 1).finish("r", 5)
    assert table.carry("r", 3).account("r").carried_micro == 3
    assert table.account("r").carried_micro == 0  # the input table never changes
    with pytest.raises(ValueError, match="nonnegative"):
        table.carry("r", -1)
    with pytest.raises(TypeError, match="integer micro-USD"):
        table.carry("r", 1.0)
    with pytest.raises(KeyError):
        table.carry("absent", 1)
    fixed = table.resolve(2, 10, {})
    assert fixed.account("r").payoff is not None
    with pytest.raises(ValueError, match="already final"):
        fixed.carry("r", 1)
