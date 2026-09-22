"""Trading profit becomes thinking money (architect review #1).

The essay (II.IV): "a continuous, reciprocal flow of capital is an objective
requirement" of any factory, and the only kill switch greater than a kernel
teardown is "a token budget of $0". Until now a conversion of venue capital into
provider credit released a hold and minted no spending authority, so the world
was a countdown on its endowment that no trade could extend.
"""

from tests.runtime.test_fidelity import runtime


def _convert(rt, seat, handle, usd):
    rt.handle_to_assembly[handle] = seat
    rt.treasury.open_window(1)
    rt.treasury.transfer("to_reserve", "20", handle="fund-reserve", now_ns=1)
    rt.treasury.tick(2)
    before = rt.wallet.balance, rt.budget.unallocated()
    assert rt.treasury.transfer("to_venice", usd, handle=handle, now_ns=3)[
        "status"] == "submitted"
    rt.treasury.tick(4)
    rt._classify_financing()
    return before


def test_a_seats_own_profit_converted_becomes_its_own_entitlement():
    rt = runtime()
    seat = next(iter(rt.assemblies))
    rt.budget.claim_venue(seat, 3_000_000, "a profitable close")
    entitlement = rt.budget.entitlement(seat)
    before, pool = _convert(rt, seat, "decision-convert", "5")
    assert rt.wallet.balance == before + 5_000_000  # authority grew by the credit received
    assert rt.budget.entitlement(seat) == entitlement + 3_000_000  # its own profit
    assert rt.budget.venue_claims()[seat] == 0  # the claim became thinking money
    assert rt.budget.unallocated() == pool + 2_000_000  # the rest was shared principal
    assert rt.wallet.check_conservation() and rt.budget.check_invariant()
    classified = [i for i in rt.ledger._recovery_items() if i["kind"] == "financing.classified"]
    assert [(c["seat"], c["to_seat_micro"], c["to_pool_micro"]) for c in classified] == [
        (seat, 3_000_000, 2_000_000)]


def test_a_seat_with_no_profit_converts_shared_principal_into_the_pool():
    rt = runtime()
    seat = next(iter(rt.assemblies))
    rt.budget.claim_venue(seat, -1_000_000, "a losing close")
    entitlement = rt.budget.entitlement(seat)
    _, pool = _convert(rt, seat, "decision-convert", "5")
    assert rt.budget.entitlement(seat) == entitlement
    assert rt.budget.unallocated() == pool + 5_000_000
    assert rt.budget.venue_claims()[seat] == -1_000_000  # a loss is not laundered


def test_the_capital_loop_world_converts_a_seats_testnet_profit_into_its_venice_credit():
    """The hybrid rehearsal end to end on the fast harness's fakes: profit -> Venice
    credit -> spending authority -> the converting seat's own entitlement."""
    from pathlib import Path

    from factorylab.runtime.loop import Runtime
    from scripts.fastloop import PolicyProvider, simulation_manifest

    world = Path("worlds/edition5-capital-loop.toml")
    manifest = simulation_manifest(world, 1)
    rt = Runtime(manifest, events=1, seed=1, initial_balance_micro=None, ledger_path=None,
                 router_gamma=0.1, provider=PolicyProvider(world))
    assert rt.treasury.rail.name == "scripted-hybrid"
    assert "to_venice pays its $5 from the venue" in rt.tool_specs["treasury.transfer"][
        "description"]
    seat = "opportunity"  # a producer that thinks on Venice in this world
    rt.budget.claim_venue(seat, 3_000_000, "a profitable close")
    entitlement, pots = rt.budget.entitlement(seat), rt.treasury.pots()
    balance = rt.wallet.balance
    rt.handle_to_assembly["decision-convert"] = seat
    rt.treasury.open_window(1)
    assert rt.treasury.transfer("to_venice", "5", handle="decision-convert", now_ns=3)[
        "status"] == "submitted"
    rt.treasury.tick(4)
    assert rt.treasury.tick(5)[0]["status"] == "confirmed"
    rt._classify_financing()
    after = rt.treasury.pots()
    assert after["venue"] == pots["venue"] - 5_000_000  # testnet profit spent
    assert after["sellers"]["venice"] == pots["sellers"]["venice"] + 5_000_000  # credit in
    assert after["total_micro"] == pots["total_micro"]
    assert rt.wallet.balance == balance + 5_000_000
    assert rt.budget.entitlement(seat) == entitlement + 3_000_000
    assert rt.wallet.check_conservation() and rt.budget.check_invariant()
    assert not rt.wallet.dead


def test_financing_is_never_income_and_never_negative():
    import pytest

    rt = runtime()
    with pytest.raises(ValueError, match="financing must be positive"):
        rt.wallet.settle(-1, "h", "financing")
    _convert(rt, next(iter(rt.assemblies)), "decision-convert", "5")
    assert rt.treasury.income["earned_micro"] == 0
