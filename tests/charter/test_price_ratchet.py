"""The penalty ratchets while a violation lasts (defect 12).

The charter says a card's price ratchets while the violation lasts and decays when
it stops: a shrinking violation that is still out of its region never lowers it.
"""

from factorylab.charter.controller import CardRegion, PriceController
from factorylab.kernel.ledger import Ledger


def _controller(**changes):
    params = dict(eta=0.5, decay=0.25, penalty_cap=0.9, min_window_events=1)
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


def test_a_spike_clips_the_price_and_clamps_the_accumulated_pressure_to_its_bound():
    """Wave 16, ruling R-E: the bound penalty_cap / v falls as v spikes, so the price
    falls with it (the penalty stays at the cap). Ruling R10-e refined (superseding
    R-E's held integral): at the card's own bound the integral is clamped to that
    bound, never held above it, and integrates again from there once the violation
    subsides."""
    prices = _controller()
    prices.register(CardRegion("cost", "max", None, 10.0, 2.0))
    for event in range(2):
        prices.observe("cost", 12, event)  # violation 1: 0.5, then the bound 0.9
    assert prices.price("cost") == 0.9
    prices.observe("cost", 100, 2)  # violation 45: bound 0.02
    assert prices.price("cost") == 0.9 / 45
    assert prices.snapshot()["cards"]["cost"]["integral"] == 0.9 / 45
    prices.observe("cost", 12, 3)  # violation 1 again: 0.02 + eta * 1
    assert prices.price("cost") == 0.9 / 45 + 0.5


def test_an_adopted_unbounded_price_unwinds_on_the_decay_schedule():
    """Ruling R10-e refined (Codex on #152): adopting 1e308 stores the price as given,
    but the integral the PID uses is clamped to the card's bound at its first
    observation. A violation at the cap, then compliant windows: the price falls by
    ``decay`` each window from the bound to zero, never locked at the cap (at 1e308,
    ``decay`` is a float no-op)."""
    prices = _controller()
    prices.register(CardRegion("cost", "max", None, 10.0, 2.0))
    prices.set_price("cost", 1e308, amendment_id="unbounded")
    assert prices.price("cost") == 1e308
    prices.observe("cost", 12, 0)  # violation 1: pressure at the cap, bound 0.9
    assert prices.price("cost") == 0.9
    assert prices.snapshot()["cards"]["cost"]["integral"] == 0.9
    seen = []
    for event in range(1, 6):
        prices.observe("cost", 9, event)  # compliant
        seen.append(prices.price("cost"))
    assert seen == [0.9 - 0.25, 0.9 - 0.5, 0.9 - 0.75, 0.0, 0.0]


def test_an_adopted_unbounded_price_unwinds_when_its_last_window_violated():
    """The same when the adoption is followed directly by compliance: the integral
    leaks from the bound of the last violation it saw, never from 1e308."""
    prices = _controller()
    prices.register(CardRegion("cost", "max", None, 10.0, 2.0))
    prices.observe("cost", 12, 0)  # violation 1
    prices.set_price("cost", 1e308, amendment_id="unbounded")
    prices.observe("cost", 9, 1)  # compliant
    assert prices.price("cost") == 0.9 - 0.25
