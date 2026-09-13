"""T58: retained storage is a scored liability, not an unattributed wallet debit.

A note's recurring rent leaves the wallet against its writer's handle. Unless
that charge also reaches the writer's consequence cost while its outcome is
open, a trading return can resolve `return_paid_off = 1` on a margin its own
storage already consumed.
"""

import pytest

from factorylab.charter.charter import MetricCard
from factorylab.charter.measurement import measure_card
from factorylab.runtime.resume import restore_runtime, runtime_state
from factorylab.settlement.lots import LotTable
from tests.audit.test_r3_d_card_evidence import cost_runtime, returned
from tests.conftest import make_runtime
from tests.runtime.test_connectors import decision, ledger_items

RENT = 15  # "fact" + "public fact" = 15 UTF-8 bytes at one micro-USD per byte-window
COST = 10  # the return's own metered compute
GROSS = 20  # marked profit: more than the compute cost, less than compute plus rent
NOTE_BYTES = 5_000  # key plus text bytes, at one micro-USD per byte-window


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


def stored_writer(rt, *, own_cost, note_bytes, status="ok"):
    """One producer return that both answers and retains a public note."""
    handle = returned(rt, "seed-decider", "producer", own_cost, status=status)
    result, cost = rt._run_tool("seed-decider", handle, {
        "tool": "note.put", "args": {"key": "fact", "text": "x" * (note_bytes - 4)}})
    assert "error" not in result and cost == note_bytes
    return handle


def test_recurring_rent_moves_the_cost_card_and_its_penalty_share():
    """The charge is measured where the cost card and its shares actually read.

    Rent falls due in a window the writer never responded in. Unless the charge
    enters the selected return rows of that window, the card measures the same
    mean it would have measured without the note and the writer carries the same
    share of its violation.
    """
    rt = cost_runtime("producer", kind="windows", n=2, per="role")
    writer = stored_writer(rt, own_cost=1_000, note_bytes=NOTE_BYTES)
    other = returned(rt, "seed-decider", "producer", 3_000)
    rt.n = 10
    boundary(rt)
    assert ledger_items(rt, "note.rent")[-1]["cost"] == NOTE_BYTES
    assert rt.window.index == 2  # the charge landed in the window that just opened

    rt.n = 20
    rt._close_price_window()
    # Without the charge the same two returns measure 2,000 and leave the writer
    # a quarter of the violation; the rent is the whole of the difference, spread
    # over the two responses it did not become a third of.
    assert rt.window.closed_values == {"cost": pytest.approx(4_500)}
    shares = rt.window.closed_shares[0]["shares"]
    assert shares[writer] == pytest.approx(2 / 3)
    assert shares[other] == pytest.approx(1 / 3)


def well_formed_card(rt, n=2):
    """A rate card over the same selected responses the cost card is priced on."""
    return MetricCard("wf", rt.charter.norms[1], "Selected response schema", "rate",
                      {"kind": "returns", "n": n, "per": "role"}, "at least 0.9",
                      "well_formed_rate", "producer")


def test_a_storage_charge_is_measured_as_a_cost_and_never_as_a_response():
    """The charge is money the decision spent, not an answer it gave.

    It carries the writer's own handle and measured role in the window it landed
    in, so the cost cards read it; the rate observations, which count responses,
    do not — and a horizon of responses it cannot answer in is not filled by it.
    """
    rt = cost_runtime("producer", kind="returns", n=2, per="role")
    writer = stored_writer(rt, own_cost=1_000, note_bytes=NOTE_BYTES, status="malformed")
    rt.n = 10
    boundary(rt)
    row = rt.card_samples.returns[-1]
    assert (row["handle"], row["role"], row["window"], row["cost"]) == (
        writer, "producer", 2, NOTE_BYTES)

    # One malformed response and one charge are not two responses: the rate
    # stays unsupported rather than reading 0.0 off a half-filled horizon.
    assert measure_card(well_formed_card(rt), rt.card_samples) == {}


def test_a_storage_charge_does_not_displace_a_response_in_a_full_horizon():
    """A full horizon selects the last n responses, and the charge is not one.

    With two real responses behind it, a charge that took a slot would push the
    oldest response out of the window and measure the rate over what is left.
    """
    rt = cost_runtime("producer", kind="returns", n=2, per="role")
    stored_writer(rt, own_cost=1_000, note_bytes=NOTE_BYTES)
    returned(rt, "seed-decider", "producer", 1_000, status="malformed")
    rt.n = 10
    boundary(rt)
    assert rt.card_samples.returns[-1].get("storage") is True
    assert measure_card(well_formed_card(rt), rt.card_samples) == {"producer": 0.5}


def test_recurring_rent_moves_a_global_window_cost_card_and_its_shares():
    """A card over whole closed windows is measured from their own cost statistics.

    That path never reads the selected return rows, so a charge that only
    reaches those rows leaves the card exactly where it was while the window's
    frozen shares still hand the writer the larger part of its violation.
    """
    rt = cost_runtime("producer", kind="windows", n=2, per=None)
    writer = stored_writer(rt, own_cost=1_000, note_bytes=NOTE_BYTES)
    other = returned(rt, "seed-decider", "producer", 3_000)
    rt.n = 10
    boundary(rt)
    assert ledger_items(rt, "note.rent")[-1]["cost"] == NOTE_BYTES
    assert rt.window.index == 2  # the charge landed in the window that just opened

    rt.n = 20
    rt._close_price_window()
    # Without the charge the same two returns measure 2,000 over the two windows;
    # the rent raises what they cost without becoming a third return.
    assert rt.window.closed_values == {"cost": pytest.approx(4_500)}
    # A median is a value one return took, and the charge is no return's cost.
    assert rt.card_samples.medians["cost"] == 2_000
    shares = rt.window.closed_shares[0]["shares"]
    assert shares[writer] == pytest.approx(2 / 3)
    assert shares[other] == pytest.approx(1 / 3)


RENT_ONE = 1  # a one-byte key with empty text, at one micro-USD per byte-window


def test_rent_adds_cost_mass_to_a_global_window_card_and_no_phantom_return():
    """Rent is money the window spent, never a return it received.

    A global closed-window cost card divides the window's cost mass by the
    returns it holds. Two 10,000-micro responses and one micro of rent are two
    returns costing 20,001 together, not three costing 20,001: counting the
    charge in the denominator would let paying rent improve the cost card.
    """
    rt = cost_runtime("producer", kind="windows", n=2, per=None)
    writer = returned(rt, "seed-decider", "producer", 10_000)
    result, cost = rt._run_tool("seed-decider", writer, {
        "tool": "note.put", "args": {"key": "f", "text": ""}})
    assert "error" not in result and cost == RENT_ONE
    other = returned(rt, "seed-decider", "producer", 10_000)
    rt.n = 10
    boundary(rt)  # the rent falls due in a window neither decision responded in
    assert ledger_items(rt, "note.rent")[-1]["cost"] == RENT_ONE

    rt.n = 20
    rt._close_price_window()
    assert rt.window.closed_values == {"cost": pytest.approx(10_000.5)}
    # The two returns cost the same, so the rent alone separates their shares.
    shares = rt.window.closed_shares[0]["shares"]
    assert shares[writer] > shares[other]

    # The window's separate cost mass is checkpointed with the window itself.
    restored = make_runtime()
    restore_runtime(restored, runtime_state(rt))
    assert restored.window.storage_cost_micro == rt.window.storage_cost_micro == RENT_ONE
    assert restored.window.costs == rt.window.costs == []
