"""T58: retained storage is a scored liability, not an unattributed wallet debit.

A note's recurring rent leaves the wallet against its writer's handle. Unless
that charge also reaches the writer's consequence cost while its outcome is
open, a trading return can resolve `return_paid_off = 1` on a margin its own
storage already consumed.
"""

from dataclasses import replace

import pytest

from factorylab.charter.charter import MetricCard
from factorylab.charter.measurement import CardSamples, measure_card
from factorylab.cortex.request import Return
from factorylab.runtime.notes import NotesSpec
from factorylab.runtime.resume import restore_runtime, runtime_state
from factorylab.settlement.lots import LotTable
from tests.audit.test_r3_d_card_evidence import cost_runtime as _cost_runtime
from tests.audit.test_r3_d_card_evidence import returned
from tests.conftest import make_runtime as _make_runtime
from tests.runtime.test_connectors import decision, ledger_items

#: Rent is by byte-time (C3); this byte-day rate makes one two-minute scripted window
#: cost exactly one micro-USD per byte, so every figure below is one window's rent.
PER_WINDOW_RATE = "720"
RENT = 15  # "fact" + "public fact" = 15 UTF-8 bytes at one micro-USD per byte-window
COST = 10  # the return's own metered compute
GROSS = 20  # marked profit: more than the compute cost, less than compute plus rent
NOTE_BYTES = 5_000  # key plus text bytes, at one micro-USD per byte-window


def rated(rt):
    """The scripted world at the per-window rate; the rate is read when rent falls due."""
    rt.m = replace(rt.m, notes=NotesSpec(micro_per_byte_day=PER_WINDOW_RATE))
    return rt


def make_runtime():
    return rated(_make_runtime())


def cost_runtime(*args, **kwargs):
    return rated(_cost_runtime(*args, **kwargs))


def writer(rt):
    """One return that both writes a public note and holds an open marked position."""
    handle = decision(rt)
    rt._start_return(handle)
    result, cost = rt._run_tool("seed-decider", handle, {
        "tool": "note.put", "args": {"key": "fact", "text": "public fact"}})
    # The put pays the flat call price; the 15 bytes it retains are what accrue RENT.
    assert "error" not in result and cost == rt.m.notes.byte_window_micro
    assert rt.notes["fact"]["bytes"] == RENT
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
    """One producer return that both answers and retains a public note.

    The put costs the notebook's flat call price and nothing per byte: edition 3
    removed the transfer toll and left byte-time rent, which is what every test
    below measures. The retained size is still ``note_bytes``, so the rent these
    tests move is unchanged.
    """
    handle = returned(rt, "seed-decider", "producer", own_cost, status=status)
    result, cost = rt._run_tool("seed-decider", handle, {
        "tool": "note.put", "args": {"key": "fact", "text": "x" * (note_bytes - 4)}})
    assert "error" not in result and cost == rt.m.notes.byte_window_micro
    assert rt.notes["fact"]["bytes"] == note_bytes
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


def cost_card(rt, n=2):
    """The same selected-response cost card `cost_runtime` prices on."""
    return MetricCard("cost", rt.charter.norms[1], "Selected response cost", "micro-USD",
                      {"kind": "returns", "n": n, "per": "role"}, "at most 500",
                      "cost_per_return", "producer")


def response(samples, handle, window, cost, *, status="ok"):
    samples.returned(handle=handle, assembly="seed-decider", role="producer",
                     window=window, ret=Return(handle, {}, cost, status))


def test_a_returns_horizon_selects_responses_and_rent_only_adds_their_cost_mass():
    """The horizon is n responses; the charge is mass on top of them, not one of them.

    Two 10,000-micro responses and one micro of rent are two responses costing
    20,001 together. Selecting the charge as one of the two would discard the
    older response and read 10,001 off a single one, so paying rent would cost
    a tenth of a micro rather than a whole one.
    """
    rt = cost_runtime("producer", kind="returns", n=2, per="role")
    writer = returned(rt, "seed-decider", "producer", 10_000)
    result, cost = rt._run_tool("seed-decider", writer, {
        "tool": "note.put", "args": {"key": "f", "text": ""}})
    assert "error" not in result and cost == RENT_ONE
    rt.n = 10
    boundary(rt)  # the rent falls due in a window its writer never responded in
    assert ledger_items(rt, "note.rent")[-1]["cost"] == RENT_ONE
    returned(rt, "seed-decider", "producer", 10_000)
    rows = rt.card_samples.returns
    assert [r.get("storage") is True for r in rows] == [False, True, False]

    assert measure_card(cost_card(rt), rt.card_samples) == {"producer": 10_000.5}
    # A median is a value one response took, and the charge is no response's cost.
    rt.n = 20
    rt._close_price_window()
    assert rt.card_samples.medians["cost"] == 10_000


def test_rent_never_supports_a_cost_horizon_that_is_short_a_response():
    """A scope with n-1 responses is unavailable however much rent it paid.

    Counting the charge as the missing response would price a two-response
    horizon off one response and one line of money.
    """
    rt = cost_runtime("producer", kind="returns", n=2, per="role")
    writer = returned(rt, "seed-decider", "producer", 10_000)
    result, cost = rt._run_tool("seed-decider", writer, {
        "tool": "note.put", "args": {"key": "f", "text": ""}})
    assert "error" not in result and cost == RENT_ONE
    rt.n = 10
    boundary(rt)
    assert rt.card_samples.returns[-1].get("storage") is True
    assert measure_card(cost_card(rt), rt.card_samples) == {}


@pytest.mark.parametrize("rent_window,expected", [(2, 10_000.5), (1, 10_000.0)])
def test_only_rent_from_the_selected_responses_own_windows_joins_their_horizon(
        rent_window, expected):
    """A charge belongs to the horizon measured over the windows it landed in.

    The selected responses span windows 2 through 2, so a charge metered there
    is part of what they cost and one metered in window 1, beside a response the
    horizon already rolled past, is not.
    """
    rt = make_runtime()
    samples = CardSamples()
    response(samples, "old", 1, 2_000)
    response(samples, "a", 2, 10_000)
    response(samples, "b", 2, 10_000)
    samples.stored(handle="rent", assembly="seed-decider", role="producer",
                   window=rent_window, cost=1)
    assert measure_card(cost_card(rt), samples) == {"producer": expected}


def test_pruning_keeps_the_responses_a_horizon_needs_and_not_rent_it_never_reads():
    """Retention follows the same selection: rent is kept beside responses, never over them.

    A charge that displaced a response here would evict the horizon's own
    samples, and a charge outside every horizon's window span is money already
    measured that no future selection reads.
    """
    rt = cost_runtime("producer", kind="returns", n=2, per="role")
    samples = CardSamples()
    response(samples, "old", 1, 2_000)
    response(samples, "a", 2, 10_000)
    response(samples, "b", 2, 10_000)
    samples.stored(handle="stale", assembly="seed-decider", role="producer",
                   window=1, cost=1)
    samples.stored(handle="live", assembly="seed-decider", role="producer",
                   window=2, cost=1)

    samples.prune(rt.charter.cards)
    assert [row["handle"] for row in samples.returns] == ["a", "b", "live"]
    assert measure_card(cost_card(rt), samples) == {"producer": 10_000.5}


def rent_row(rt, assembly, cost, window=None):
    """A charge that falls due for a decision with no response in that window."""
    handle = f"rent-{assembly}-{cost}"
    rt.card_samples.stored(handle=handle, assembly=assembly, role="producer",
                           window=rt.window.index if window is None else window, cost=cost)
    return handle


def test_rent_is_cost_mass_in_a_scope_and_never_one_of_its_responses():
    """A scope's share of the blame follows its measured cost, rent included.

    Assembly A answers once for 10,000 and holds 10,000 of rent; assembly B
    answers once for 10,000. Measurement reads 20,000 and 10,000, so A owns two
    thirds of the violation. Dividing A's rows by two responses instead of one
    hands each assembly a half and lets rent dilute the writer it belongs to.
    """
    rt = cost_runtime("producer", kind="returns", n=1, per="assembly")
    a = returned(rt, "asm-a", "producer", 10_000)
    rent = rent_row(rt, "asm-a", 10_000)
    b = returned(rt, "asm-b", "producer", 10_000)
    rt.n = 10
    rt._close_price_window()

    assert rt.card_samples.scopes["cost"] == {"asm-a": 20_000, "asm-b": 10_000}
    shares = rt.window.closed_shares[0]["shares"]
    assert shares[a] == pytest.approx(1 / 3)
    assert shares[rent] == pytest.approx(1 / 3)
    assert shares[b] == pytest.approx(1 / 3)
    assert shares[a] + shares[rent] == pytest.approx(2 / 3)


def test_a_rent_only_span_still_reads_the_windows_own_cost_records():
    """A global window card is measured from window statistics, and rent is not a return.

    The span's only sample row is a charge, so there is no response row to
    attribute over. Treating that charge as the span's returns would hand the
    whole violation to the renter instead of the decisions whose returns the
    window's own statistics measured.
    """
    rt = cost_runtime("producer", kind="windows", n=1, per=None)
    a = returned(rt, "seed-decider", "producer", 10_000)
    b = returned(rt, "seed-decider", "producer", 30_000)
    rent = rent_row(rt, "seed-decider", 10_000)
    rt.card_samples.returns[:] = [r for r in rt.card_samples.returns if r.get("storage")]
    rt.n = 10
    rt._close_price_window()

    shares = rt.window.closed_shares[0]["shares"]
    assert rent not in shares
    assert shares[a] == pytest.approx(0.25)
    assert shares[b] == pytest.approx(0.75)


def test_rent_does_not_dilute_the_equal_split_of_a_non_cost_violation():
    """An equal split divides a violation over the decisions that responded.

    A charge falling due in a window its writer never responded in opens a cost
    line there and nothing else. Counting that line as a supporting decision
    would halve what the one real responder owes for its own noop share.
    """
    rt = cost_runtime("producer", kind="returns", n=1, per="role")
    responder = returned(rt, "seed-decider", "producer", 10_000)
    renter = decision(rt)
    rt._charge_storage(renter, 10_000)
    assert rt.window.decisions[renter] == {
        "role": "producer", "cost": 10_000, "ok": 0, "invocations": 0,
        "tool_calls": 0, "notional_micro": 0}

    with_rent = rt._decision_share(
        rt.window, responder, "noop_share", "producer", None, 1.0)
    del rt.window.decisions[renter]
    without_rent = rt._decision_share(
        rt.window, responder, "noop_share", "producer", None, 1.0)
    assert with_rent == without_rent == pytest.approx(1.0)
