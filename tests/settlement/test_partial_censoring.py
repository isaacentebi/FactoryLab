"""An unresolved order censors only what it could have changed (defect 10).

R4-C released a hold the venue would never answer by censoring the return that
took it: the whole account closed at once, its known fills and open lots with it,
and every micro-dollar the return had already realised was deferred to a late
booking from zero. The unresolved order leaves the question ``did this return pay
off`` unanswerable, so the outcome is still censored; but the account keeps
resolving on its own schedule, and the part of its money that was observed is
booked as observed rather than as if it too were unknown.
"""

from factorylab.settlement.consequence import ReturnConsequences


def _fill(order_id, is_buy, px, size="1"):
    return {"order_id": order_id, "coin": "BTC", "is_buy": is_buy, "size": size, "px": px,
            "fee_usd": "0"}


def _account_with_known_profit(ledger):
    book = ReturnConsequences(ledger, 200)
    book.start("h", 0)
    book.start("later", 0)
    book.finish("later", 1)
    book.order_result("h", {"status": "filled", "order_id": "o1", "filled_size": "1"}, {}, 0)
    book.observe("Fill", _fill("o1", True, "100"), 0)
    return book


def test_an_unresolved_order_leaves_the_known_portion_on_its_own_schedule(ledger):
    book = _account_with_known_profit(ledger)
    book.finish("h", 5)
    book.order_intent("h:lost", "h", "BTC")
    book.release_unresolved("h:lost", 1)
    # The return still holds an open lot of its own: its account is not closed early.
    assert book.payoff("h") is None
    assert {p.handle for p in book.resolve(2)} == {"later"}
    # At its backstop it is marked like any other, and censored for the unknown order.
    book.observe("MarketMid", {"coin": "BTC", "mid": "110"}, 3)
    (payoff,) = book.resolve(200)
    assert payoff.handle == "h" and payoff.censored == "external_unobservable"
    assert payoff.marked and payoff.net_micro == 10_000_000


def test_the_observed_money_of_a_censored_return_is_not_deferred(ledger):
    book = _account_with_known_profit(ledger)
    book.order_result("h", {"status": "filled", "order_id": "o2", "filled_size": "1"}, {}, 0)
    book.observe("Fill", _fill("o2", False, "110"), 0)  # closes its own lot: +10 USD
    book.finish("h", 5)
    book.order_intent("h:lost", "h", "BTC")
    book.release_unresolved("h:lost", 1)
    fixed = {p.handle: p for p in book.resolve(2)}
    payoff = fixed["h"]
    assert payoff.censored == "external_unobservable" and not payoff.marked
    # The realised 10 USD is the known portion: carried on the outcome, not booked late.
    assert payoff.net_micro == 10_000_000
    assert book.settle_late(3) == {}
