"""Money reports and classifications agree with the money that moved.

Defect 11: a commit whose cost
overran its hold spent the seat's other holds, and a novelty seat's cover counted
the pool twice.
"""

from factorylab.kernel.budget import BudgetBook, SeatWallet, unlocked_balance
from factorylab.kernel.wallet import Wallet


def _invariant(book, wallet) -> bool:
    return (sum(book.entitlements().values()) + book.unallocated()
            == unlocked_balance(wallet) - book.holds()) and book.check_invariant()


def test_an_overrun_commit_never_spends_the_seats_other_holds(ledger, clock):
    wallet = Wallet(1_000, ledger, clock_ns=clock)
    book = BudgetBook(wallet, ledger, clock_ns=clock, base_share="1")
    book.genesis(["a"])  # a: 1_000, pool 0
    seat = SeatWallet(wallet, book, "a")
    first = seat.reserve(600, "h1", "model:m")
    second = seat.reserve(400, "h2", "model:m")
    assert book.entitlement("a") == 0
    # The first call reports 900 against a 600 hold: the 300 overrun is not the seat's
    # to pay out of the 400 it is holding for its second call.
    seat.commit_reported(first, 900)
    assert book.entitlement("a") >= 0
    assert book.held_by("a") == 400
    seat.commit(second, 400)
    assert book.entitlement("a") >= 0
    assert _invariant(book, wallet)


def test_a_novelty_seats_cover_counts_the_pool_once(clock):
    """The bridge and the protected share are both backed by the unallocated pool."""
    from tests.conftest import make_runtime

    rt = make_runtime()
    seat = next(s for s in rt.budget.seats())
    handle = "bridged-call"
    pool = max(0, rt.budget.unallocated())
    rt.entitlement_bridges[handle] = pool
    rt._novelty_compute = lambda _h, _r: True
    rt._protected_share = lambda _seat: pool
    rt.queue = type("Q", (), {"get": staticmethod(
        lambda _h: type("D", (), {"propensity": type("P", (), {"chosen": seat})()})())})()
    assert rt._novelty_protection(handle, "model:m") == pool
