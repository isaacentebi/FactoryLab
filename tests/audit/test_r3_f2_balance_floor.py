"""Round three, group F2, triage T30: a gap liquidation overshoots the balance floor.

Reproduced, then pinned rather than changed. ``scripted-crash`` dies with the wallet at
-$4.144312 and ``termination.balance_floor_usd = "0"``. The cause is the venue's, not an
ordering mistake in the kernel: a manifest shock halves BTC in one step while a 3x long
is open, and the maintenance margin a continuous market would liquidate against (half the
initial margin, about a sixth of notional) is gapped straight through. The realised loss
is larger than the equity behind it and lands in the wallet in one debit. That is gap
risk, and the scripted-crash manifest exists to demonstrate exactly it, so the venue is
left as it is and the overshoot is documented on ``FakeExchange._liquidate_if_needed``.

Money is still conserved and death is still final; what a floor does not promise is that
the last loss before it is small enough to land on it.
"""

from decimal import Decimal as D

from factorylab.runtime.loop import Runtime
from factorylab.runtime.worlds import load_manifest
from factorylab.world.exchange import FakeExchange, Order


def test_t30_a_gap_through_maintenance_margin_realises_more_than_the_equity_behind_it():
    """On the fake venue, directly: one 50% step with a 3x long open."""
    ex = FakeExchange(coins=("BTC",), start_cash_usd=D(20), start_prices={"BTC": D(100)},
                      spread_bps=D(0), fee_bps=D(0), step_bps=D(0),
                      shocks={1: {"BTC": D("0.5")}})
    assert ex.place(Order("BTC", True, D("0.6"))).status == "filled"  # $60 on $20, 3x
    maintenance = ex.account().margin_used_usd * ex.maintenance_fraction
    assert ex._perp_equity() > maintenance  # solvent before the gap
    events = ex.advance(1)
    assert any(e.payload.get("liquidation") for e in events)
    assert not ex.account().positions
    # The close is at the post-gap mid, so the realised loss exceeds the equity that
    # was backing it and the account is negative: no floor can be observed in between.
    assert ex._cash == D(-10) and ex.account().equity_usd == D(-10)


def test_t30_scripted_crash_dies_below_its_zero_floor_with_money_conserved():
    """The world-level reproduction, pinned: the terminal wallet is negative, not zero."""
    m = load_manifest("scripted-crash")
    assert m.termination.balance_floor_micro == 0
    rt = Runtime(m, events=600, seed=2, initial_balance_micro=None, ledger_path=None,
                 drip=False, router_gamma=.1)
    summary = rt.run()
    assert summary["termination_reason"] == "balance_zero" and summary["seal_key_released"]
    assert summary["wallet_balance_micro"] == -4_144_312 < m.termination.balance_floor_micro
    assert summary["wallet_conservation"] and summary["ledger_verify"]
