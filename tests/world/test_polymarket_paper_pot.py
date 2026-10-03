"""The paper pot: Polymarket's live books, a simulated pot (``world/polymarket_paper.py``).

The books and markets are served by ``FakeClob`` in process, in the published shapes,
from a seeded ``FakePolymarket`` whose prices the tests move by hand; nothing here
touches a network or signs anything. Chapter II §II.b (physics is enforced): every
order is a post-only BUY, a fill never exceeds its order or the ask it was read
against, the pot's USDC is exact integer micro-USD, and the pot's state is a function
of its calls and its reads' answers, so a replay of the journal re-runs it on the
recorded answers and reaches the same state without reading again.
"""

from decimal import Decimal

import pytest

from factorylab.world.polymarket_clob import BudgetSpent
from factorylab.world.polymarket_paper import PaperPolymarket, PaperReads
from tests.runtime.test_polymarket_surface import still_fake
from tests.world.fake_clob import FakeClob


class Wall:
    """A wall clock that stands still unless moved: the budget's window is explicit."""

    def __init__(self) -> None:
        self.now = 1_790_000_000_000_000_000

    def __call__(self) -> int:
        return self.now


def paper(*, start="50", budget=60, fake=None):
    """A paper pot over a ``FakeClob``: (pot, server, wall)."""
    server = FakeClob(fake if fake is not None else still_fake())
    wall = Wall()
    reads = PaperReads(budget=budget, get=lambda url: server("GET", url, {}, None),
                       wall=wall, nonce=lambda: 7)
    pot = PaperPolymarket(start_micro=int(Decimal(start) * 1_000_000), reads=reads)
    return pot, server, wall


def market(server, market_id="fake-1"):
    """The market a write is weighed on, as the runtime reads it."""
    return PaperReads(budget=10, get=lambda url: server("GET", url, {}, None),
                      nonce=lambda: 7).write_market(market_id)


def token(server, market_id="fake-1", side=0):
    return server.fake._markets[market_id]["tokens"][side]


def place(pot, server, client_id, *, price="0.39", size="10", market_id="fake-1", side=0):
    return pot.place(client_id=client_id, token_id=token(server, market_id, side), is_buy=True,
                     size=Decimal(size), price=Decimal(price), market=market(server, market_id))


def ask_at(server, price, market_id="fake-1"):
    """Move the market so its YES token's best ask is ``price`` (the fake's book is one
    tick either side of its mid)."""
    fake = server.fake
    fake._markets[market_id]["mid"] = Decimal(price) - fake.tick


def test_a_buy_that_would_cross_the_live_book_is_rejected_and_never_existed():
    pot, server, _ = paper()
    ask_at(server, "0.41")
    result = place(pot, server, "c:1", price="0.41")
    assert result["status"] == "rejected" and result["venue_refused"] is True
    assert result["error"] == "invalid post-only order: order crosses book"
    assert pot.account()["open_orders"] == [] and pot.cash_micro == 50_000_000


def test_a_resting_buy_holds_its_notional_and_fills_at_its_own_price_once_the_ask_meets_it():
    pot, server, _ = paper()
    ask_at(server, "0.41")
    result = place(pot, server, "c:1", price="0.39")
    assert result["status"] == "resting"
    account = pot.account()
    assert account["usdc"] == "50" and Decimal(account["usdc_available"]) == Decimal("46.1")
    assert pot.advance(1) == []  # the ask is above it: nothing fills
    ask_at(server, "0.38")  # the live ask falls below the resting buy
    (fill,) = pot.advance(2)
    assert fill == {"kind": "fill", "order_id": result["order_id"], "token_id": token(server),
                    "market_id": "fake-1", "is_buy": True, "size": "10", "px": "0.39",
                    "fee_usd": "0", "realized_usd": "0", "ts_ns": 2}
    assert pot.cash_micro == 46_100_000 and type(pot.cash_micro) is int
    (position,) = pot.account()["positions"]
    assert position["size"] == "10" and Decimal(position["avg_px"]) == Decimal("0.39")
    assert pot.lookup("c:1")["status"] == "filled"


def test_a_fill_is_bounded_by_the_ask_it_was_read_against_and_shared_across_orders():
    fake = still_fake(depth_shares=Decimal("7.555"))
    pot, server, _ = paper(fake=fake)
    ask_at(server, "0.41")
    low = place(pot, server, "c:1", price="0.38", size="5")
    high = place(pot, server, "c:2", price="0.39", size="5")
    ask_at(server, "0.37")
    fills = pot.advance(1)
    # 7.555 at the ask: the higher-priced buy takes 5, the other the rest, quantized down
    # to the token's two size decimals; nothing past what the ask offered.
    assert [(f["order_id"], f["size"], f["px"]) for f in fills] == [
        (high["order_id"], "5", "0.39"), (low["order_id"], "2.55", "0.38")]
    assert pot.lookup("c:1")["status"] == "resting"
    assert pot.lookup("c:1")["filled_size"] == "2.55"
    assert pot.cash_micro == 50_000_000 - 1_950_000 - 969_000


def test_a_client_id_trades_once_and_a_cancel_releases_the_hold():
    pot, server, _ = paper()
    ask_at(server, "0.41")
    first = place(pot, server, "c:1")
    assert place(pot, server, "c:1") == first
    assert len(pot.orders) == 1
    cancelled = pot.cancel(client_id="k:1", order_id=first["order_id"])
    assert cancelled["status"] == "cancelled" and cancelled["filled_size"] == "0"
    assert pot.cancel(client_id="k:1", order_id=first["order_id"]) == cancelled
    assert pot.cancel(client_id="k:2", order_id=first["order_id"])["status"] == "rejected"
    assert pot.account()["usdc_available"] == "50"
    ask_at(server, "0.30")
    assert pot.advance(1) == []  # a cancelled order never fills


@pytest.mark.parametrize(("change", "reason"), [
    ({"price": "0.395"}, "tick"),
    ({"size": "4"}, "minimum order"),
    ({"size": "200"}, "balance"),
])
def test_an_order_the_market_or_the_pot_cannot_take_is_rejected(change, reason):
    pot, server, _ = paper()
    ask_at(server, "0.41")
    result = place(pot, server, "c:1", **change)
    assert result["status"] == "rejected" and reason in result["error"]
    assert result["venue_refused"] is True and pot.orders == {}


def test_a_sell_or_an_unlisted_token_or_a_closed_market_is_rejected():
    pot, server, _ = paper()
    weighed = market(server)
    assert pot.place(client_id="c:1", token_id=token(server), is_buy=False, size=Decimal(10),
                     price=Decimal("0.3"), market=weighed)["status"] == "rejected"
    assert pot.place(client_id="c:2", token_id="123", is_buy=True, size=Decimal(10),
                     price=Decimal("0.3"), market=weighed)["error"] == "unknown token"
    closed = {**weighed, "closed": True}
    assert "not accepting" in pot.place(client_id="c:3", token_id=token(server), is_buy=True,
                                        size=Decimal(10), price=Decimal("0.3"),
                                        market=closed)["error"]


def test_a_closed_market_cancels_its_resting_orders_and_a_resolution_pays_into_custody():
    pot, server, _ = paper()
    ask_at(server, "0.41")
    held = place(pot, server, "c:1", price="0.39")
    ask_at(server, "0.38")
    pot.advance(1)  # held: 10 YES at 0.39
    ask_at(server, "0.41")
    resting = place(pot, server, "c:2", price="0.30")
    fake = server.fake
    fake._markets["fake-1"]["closed"] = True  # closed, not yet resolved by UMA
    fake._markets["fake-1"]["winner"] = 0
    events = pot.advance(2)
    assert [e["kind"] for e in events] == ["cancelled", "resolution"]
    assert events[0]["order_id"] == resting["order_id"]
    resolution = events[1]
    assert resolution["payout"] == "1" and resolution["size"] == "10"
    assert Decimal(resolution["realized_usd"]) == Decimal("6.1")
    account = pot.account()
    # The tokens stay in the pot, worth their payout; nothing is redeemed into USDC.
    assert account["usdc"] == "46.1" and account["positions"][0]["payout"] == "1"
    assert account["open_orders"] == [] and held["status"] == "resting"
    assert pot.advance(3) == []  # nothing left to watch: no market is read again


def test_an_unread_book_or_market_changes_nothing_and_a_spent_budget_sends_nothing():
    pot, server, wall = paper(budget=3)
    ask_at(server, "0.41")
    place(pot, server, "c:1", price="0.39")  # one book read
    ask_at(server, "0.38")
    server.fail_next = [OSError("down"), OSError("down")]
    assert pot.advance(1) == []  # the book and the market read both failed
    sent = len(server.calls)
    assert pot.advance(2) == []  # the third request fits; the fourth does not
    assert len(server.calls) == sent  # ... and the budget refused before sending
    with pytest.raises(BudgetSpent):
        pot.reads.mark_book(token(server))
    wall.now += 10_000_000_000  # the window slides past every request
    assert [e["kind"] for e in pot.advance(3)] == ["fill"]


def test_the_state_round_trips_and_carries_open_orders_across_a_restore():
    pot, server, _ = paper()
    ask_at(server, "0.41")
    place(pot, server, "c:1", price="0.39")
    saved = pot.pot_state()
    twin, _server, _ = paper(start="1")
    twin.reads = pot.reads
    twin.load_pot_state(saved)
    assert twin.account() == pot.account() and twin.pot_state() == saved
    ask_at(server, "0.38")
    assert twin.advance(1) == pot.advance(1)
    with pytest.raises(ValueError):
        PaperPolymarket(start_micro=1.5, reads=None)  # money is integer micro-USD


def test_a_replay_re_runs_the_pot_on_the_recorded_reads_and_never_reads_again():
    """The pot is journaled as deterministic over its own journaled reads: replaying the
    journal re-runs every call on the answers the run read, reaching the same answers
    and the same state, and the network is never asked."""
    from factorylab.runtime.resume import JournalProxy
    from tests.runtime.test_loop import _consequence_runtime

    rt = _consequence_runtime()
    journal = rt.ledger
    journal.active = True
    try:
        pot, server, _ = paper()
        start = pot.pot_state()
        reads = JournalProxy(pot.reads, journal, "polymarket")
        pot.reads = reads
        proxy = JournalProxy(pot, journal, "polymarket", deterministic=True)
        prefix = len(journal.ledger._recovery_items())
        weighed = market(server)  # what the runtime weighed the writes on

        def drive(move):
            answers = []

            def call(name, *args, **kwargs):
                answers.append(getattr(proxy, name)(*args, **kwargs))

            move("0.41")
            call("place", client_id="c:1", token_id=token(server), is_buy=True,
                 size=Decimal(10), price=Decimal("0.39"), market=weighed)
            call("place", client_id="c:2", token_id=token(server), is_buy=True,
                 size=Decimal(10), price=Decimal("0.41"), market=weighed)
            move("0.38")
            call("advance", 1)
            move("0.41")
            call("place", client_id="c:3", token_id=token(server), is_buy=True,
                 size=Decimal(5), price=Decimal("0.35"), market=weighed)
            call("cancel", client_id="k:3", order_id="paper-2")
            call("account")
            return answers, pot.pot_state()

        expected = drive(lambda price: ask_at(server, price))
        rows = journal.ledger._recovery_items()[prefix:]
        assert any(row["kind"] == "io.call" and row["name"] == "polymarket.mark_book"
                   for row in rows)
        pot.load_pot_state(start)
        calls = len(server.calls)
        pot.reads.target.get = lambda url: pytest.fail(f"a replay read {url}")
        journal.tail = iter(rows)
        journal.recovering = True
        assert drive(lambda price: None) == expected
        assert journal.peek() is None and len(server.calls) == calls
    finally:
        journal.recovering = False
        rt._ledger_lock.close()
