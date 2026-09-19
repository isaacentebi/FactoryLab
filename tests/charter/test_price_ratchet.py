"""The penalty ratchets while a violation lasts and relief waits for its window (defect 12).

The charter says a card's price ratchets while the violation lasts and decays when
it stops. With eta = kappa = 0.5 a shrinking violation's damping outweighed its
step, so the price fell while the card was still out of its region. And the
immune organ's price relief, meant for the window after the one that diagnosed
stable failure, was already halving the diagnosing window's own closing prices.
"""

from factorylab.charter.controller import CardRegion, PriceController
from factorylab.kernel.ledger import Ledger


def _controller(**changes):
    params = dict(eta=0.5, kappa=0.5, decay=0.25, lambda_max=100.0, min_window_events=1)
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


def test_relief_does_not_reach_the_window_that_diagnosed_it(monkeypatch):
    """A relief issued at window k's close applies to window k+1, not to k's own prices."""
    import factorylab.runtime.pricing as pricing
    from tests.conftest import make_runtime

    rt = make_runtime()
    cards = [card.id for card in rt.charter.cards]
    for cid in cards:
        region = CardRegion(cid, "max", None, 1.0, 1.0)
        rt.regions[cid] = region
        rt.controller.register(region)
        rt.controller.set_price(cid, 1.0, amendment_id="test-start")
    # Every card measured compliant, so the close itself leaves each price at 1 - decay.
    monkeypatch.setattr(pricing, "measure_cards",
                        lambda cards, *_a, **_k: {c.id: 0.0 for c in cards})

    def diagnose(runtime, _values):  # the immune organ diagnosing stable failure
        for cid in cards:
            runtime.controller.relieve(cid, window=runtime.window.index + 1)

    monkeypatch.setattr(pricing, "close_window", diagnose)
    rt._close_price_window()
    underlying = {cid: row["lambda"] for cid, row in rt.controller.snapshot()["cards"].items()}
    assert set(rt.window.closed_prices) == set(cards)
    for cid, price in rt.window.closed_prices.items():
        assert price == underlying[cid] > 0, cid
    # The relief is live for the next window's own pricing.
    assert all(rt.controller.price(cid) == underlying[cid] / 2 for cid in cards)

    # The relieved window itself closes on relieved prices, and the relief ends there.
    monkeypatch.setattr(pricing, "close_window", lambda *_a: None)
    rt.window.index += 1
    rt._close_price_window()
    underlying = {cid: row["lambda"] for cid, row in rt.controller.snapshot()["cards"].items()}
    for cid, price in rt.window.closed_prices.items():
        assert price == underlying[cid] / 2, cid
    assert all(rt.controller.price(cid) == underlying[cid] for cid in cards)
