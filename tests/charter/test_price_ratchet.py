"""The penalty ratchets while a violation lasts (defect 12).

The charter says a card's price ratchets while the violation lasts and decays when
it stops: a shrinking violation that is still out of its region never lowers it.
"""

from factorylab.charter.controller import CardRegion, PriceController
from factorylab.kernel.ledger import Ledger


def _controller(**changes):
    params = dict(eta=0.5, decay=0.25, lambda_max=100.0, min_window_events=1)
    params.update(changes)
    return PriceController(Ledger(), **params)


def test_price_never_falls_while_the_card_is_still_violating():
    prices = _controller()
    prices.register(CardRegion("cost", "max", None, 10.0, 2.0))
    prices.observe("cost", 40, 0)  # violation 15
    history = [prices.price("cost")]
    for event, value in enumerate((20, 14, 12, 10.5), 1):  # shrinking, still violating
        prices.observe("cost", value, event)
        history.append(prices.price("cost"))
    assert history == sorted(history), history
    peak = history[-1]
    prices.observe("cost", 9, 10)  # compliant: now, and only now, it decays
    assert prices.price("cost") == peak - 0.25
