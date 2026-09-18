"""Venue P&L is a claim on venue custody, never compute money from the pool (defect 6).

Since C5 a fill's P&L settles on the venue account and never passes through the
compute wallet. The consequence line still credited a profitable return's owner
out of the unallocated compute pool (``budget.credit``), and debited a losing
owner's compute entitlement into it: every other seat paid for a trader's venue
profit in thinking money the venue never sent, and a venue loss bought the pool
compute authority it never received. Venue P&L is now recorded as each seat's
claim on what the venue holds; compute entitlements move only with compute money.
"""

from types import SimpleNamespace

from factorylab.cortex.request import Return
from factorylab.kernel.budget import BudgetBook, unlocked_balance
from factorylab.kernel.events import Event, EventKind
from factorylab.kernel.ledger import Ledger
from factorylab.kernel.queue import PropensityRecord
from factorylab.kernel.wallet import Wallet
from factorylab.runtime.shared import CH_VERDICT
from tests.conftest import make_runtime


def _trade(rt, seat, side):
    """One producer decision by ``seat`` that places a market order of 0.001 BTC."""
    rt.n += 1
    prop = PropensityRecord((seat,), (1.0,), seat, 0, "router:Tick", "test")
    handle = rt.queue.open(actor="router:Tick", event_id=f"trade-{rt.n}", propensity=prop,
                           channel=CH_VERDICT, deadline_ns=10**15, parent_handle=None,
                           cost_ceiling=0)
    rt._producer_step(
        Event(f"tick-{rt.n}", EventKind.TICK, rt.clock.now_ns, {"index": 0}, "test"),
        handle, SimpleNamespace(chosen=seat), 10**15,
        returned=Return(handle, {"action": "order", "coin": "BTC", "side": side,
                                 "size": "0.001"}, 0, "ok"))
    return handle


def _compute_classified(book, wallet):
    return sum(book.entitlements().values()) + book.unallocated() == (
        unlocked_balance(wallet) - book.holds())


def test_a_venue_claim_moves_no_compute_money():
    ledger = Ledger()
    wallet = Wallet(1_000, ledger)
    book = BudgetBook(wallet, ledger, base_share="0.5")
    book.genesis(["a", "b"])
    before = (book.entitlements(), book.unallocated())
    book.book_venue(7_000, "fill:1")
    book.claim_venue("a", 9_000, "return_paid_off")
    book.claim_venue("b", -2_500, "return_paid_off")
    assert (book.entitlements(), book.unallocated()) == before
    assert book.venue_claims() == {"a": 9_000, "b": -2_500}
    assert book.venue_unattributed() == 7_000 - 6_500
    assert _compute_classified(book, wallet) and book.check_invariant()
    twin = BudgetBook(wallet, ledger, base_share="0.5")
    twin._restore_state(book.state())
    assert twin.venue_claims() == book.venue_claims()
    assert twin.venue_booked() == book.venue_booked()


def test_venue_pnl_reaches_its_owners_as_claims_and_conserves_across_custodies():
    """Wallet, venue and entitlements reconcile: compute is untouched by trading, and
    the venue's settled P&L is exactly the owners' claims plus what no return owns."""
    rt = make_runtime()
    compute_before = dict(rt.budget.entitlements())
    pool_before = rt.budget.unallocated()
    wallet_before = rt.wallet.balance
    opener = _trade(rt, "seed-decider", "buy")
    closer = _trade(rt, "seed-observer", "sell")
    rt._settle_due_forecasts()
    payoffs = {h: rt.consequences.payoff(h) for h in (opener, closer)}
    assert all(p is not None and not p.marked for p in payoffs.values())
    # Compute: nothing the venue did moved a micro-dollar of thinking money.
    assert rt.wallet.balance == wallet_before
    assert rt.budget.entitlements() == compute_before
    assert rt.budget.unallocated() == pool_before
    assert _compute_classified(rt.budget, rt.wallet) and rt.budget.check_invariant()
    # Venue: each owner's claim is what its return realised there.
    claims = rt.budget.venue_claims()
    assert claims["seed-decider"] == payoffs[opener].net_micro
    assert claims["seed-observer"] == payoffs[closer].net_micro
    settled = sum(i["amount"] for i in rt.ledger._recovery_items()
                  if i["kind"] == "venue.settled")
    assert rt.budget.venue_booked() == settled
    # What no return owns is the venue's rounding and its average-entry accounting,
    # never a claim the venue does not back.
    assert rt.budget.venue_unattributed() == settled - sum(claims.values())
    assert abs(rt.budget.venue_unattributed()) <= 2
