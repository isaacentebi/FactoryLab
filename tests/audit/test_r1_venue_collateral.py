"""Rehearsal 3, defect 1: an order is collateralised by the venue, not by thinking money.

The run's constructor sent a 0.005 BTC short, about $383 of notional at 2x, and
``_order_collateral`` refused it — "order collateral exceeds available wallet
balance" — on a venue account holding $851 of perps cash, because the compute
wallet net of the protected novelty reserve had $107 left. Edition 3 C5 keeps
those two pots apart by design: the compute wallet buys thoughts and the trading
principal lives on the venue. The requirement is now weighed against the venue's
free collateral, equity minus margin used, and the refusal carries that figure
as ``venue_available_usd`` instead of the wallet's.

What did not change: the refusal kind is still ``order.infeasible``, existing
margin and resting orders still count before new exposure, a reduction is still
always allowed, and ``_order_leverage`` is still the hard cast — margin is
charged at the leverage this world acknowledged and no more.
"""

from decimal import Decimal

import pytest

from tests.helpers import place, venue_runtime
from tests.runtime.test_connectors import ledger_items


def infeasible(rt):
    return ledger_items(rt, "order.infeasible")


def free_collateral(rt) -> Decimal:
    account = rt.exchange.account()
    return account.equity_usd - account.margin_used_usd


@pytest.fixture
def rt():
    return venue_runtime()


def test_the_venue_carries_an_order_the_thinking_pot_could_never_collateralise(rt):
    """Defect 1 exactly: the margin fits the venue and dwarfs the compute wallet."""
    margin = Decimal("0.02") * rt.exchange.mids()["BTC"] / 3  # $400 at 3x
    assert margin < free_collateral(rt)
    assert margin > Decimal(rt.wallet.available) / 1_000_000  # the thinking pot has $1
    assert place(rt, "0.02")["status"] == "filled"
    assert rt.stats.fills == 1 and not infeasible(rt)


def test_an_order_past_the_venue_free_collateral_is_refused_with_the_venue_figure(rt):
    """The kernel still refuses; the figure it names is the venue's, not the wallet's."""
    available = free_collateral(rt)
    assert Decimal("0.06") * rt.exchange.mids()["BTC"] / 3 > available  # $1,200 at 3x
    result = place(rt, "0.06")
    assert result["status"] == "rejected"
    assert result["error"] == "order collateral exceeds venue free collateral"
    items = infeasible(rt)
    assert len(items) == 1
    assert items[0]["venue_available_usd"] == str(available)
    assert "venue_available_usd" in items[0] and "available" not in items[0]
    assert any("collateral" in entry["reason"] for entry in rt.registration_feedback)


def test_margin_already_used_counts_before_new_exposure(rt):
    """Two orders that each fit alone do not both fit: the first one's margin is spent."""
    assert place(rt, "0.04")["status"] == "filled"  # $800 of the $1,000
    assert free_collateral(rt) < Decimal("0.02") * rt.exchange.mids()["BTC"] / 3
    assert place(rt, "0.02")["status"] == "rejected"
    assert len(infeasible(rt)) == 1


def test_a_reduction_is_always_allowed(rt):
    """Unwinding risk is never blocked by the collateral it is about to release."""
    assert place(rt, "0.04")["status"] == "filled"
    assert place(rt, "0.02", side="sell", reduce_only=True)["status"] == "filled"
    assert not infeasible(rt)


def test_the_leverage_wall_is_still_the_hard_cast(rt):
    """The same order at the leverage this world acknowledged needs three times the margin."""
    assert rt.venue_tools.call("venue.set_leverage", {"coin": "BTC", "leverage": 1})
    assert place(rt, "0.02")["status"] == "rejected"
    assert len(infeasible(rt)) == 1


def test_a_world_whose_venue_is_empty_still_refuses():
    """The check does not depend on the novelty reserve it used to be about."""
    poor = venue_runtime(venue_usd="1", wallet_micro=1_000_000_000)
    assert poor.wallet.available > 0
    assert place(poor, "0.02")["status"] == "rejected"
    assert len(infeasible(poor)) == 1
