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


def test_a_spike_clips_the_price_and_never_cuts_the_accumulated_pressure():
    """Wave 16, ruling R-E: the bound penalty_cap / v falls as v spikes, so the price
    falls with it (the penalty stays at the cap); the integral is held, and the price
    returns with the violation's old size."""
    prices = _controller()
    prices.register(CardRegion("cost", "max", None, 10.0, 2.0))
    for event in range(2):
        prices.observe("cost", 12, event)  # violation 1: 0.5, then the bound 0.9
    assert prices.price("cost") == 0.9
    prices.observe("cost", 100, 2)  # violation 45: bound 0.02
    assert prices.price("cost") == 0.9 / 45
    assert prices.snapshot()["cards"]["cost"]["integral"] == 0.9
    prices.observe("cost", 12, 3)
    assert prices.price("cost") == 0.9


def test_a_spike_of_any_length_keeps_the_integral_it_found():
    """Ruling R10-n: v = 1, 45, 45, 45, 1. The integral is used at most at the largest
    own bound of the failure episode (0.9, from v = 1), never the spike's own (0.02),
    so after three spike windows it is exactly its pre-spike value, and the window
    after the spike integrates from there. Clamping to the current bound, or to the
    larger of the current and previous bounds, would cut it in the third window."""
    prices = _controller()
    prices.register(CardRegion("cost", "max", None, 10.0, 2.0))
    prices.observe("cost", 12, 0)  # violation 1
    before = prices.snapshot()["cards"]["cost"]["integral"]
    assert before == 0.5
    for event in (1, 2, 3):
        prices.observe("cost", 100, event)  # violation 45
        card = prices.snapshot()["cards"]["cost"]
        assert card["integral"] == before and card["episode_bound"] == 0.9
        assert prices.price("cost") == 0.9 / 45
    prices.observe("cost", 12, 4)  # violation 1 again
    assert prices.snapshot()["cards"]["cost"]["integral"] == 0.9
    assert prices.price("cost") == 0.9


def test_the_episode_bound_resets_at_compliance():
    """Ruling R10-n: the episode ends when the card complies. A spike in a new episode
    is bounded by that episode's own bounds, never an earlier episode's."""
    prices = _controller()
    prices.register(CardRegion("cost", "max", None, 10.0, 2.0))
    prices.observe("cost", 12, 0)  # violation 1: integral 0.5, episode bound 0.9
    assert prices.snapshot()["cards"]["cost"]["episode_bound"] == 0.9
    prices.observe("cost", 9, 1)  # compliant: the episode ends, the integral leaks
    card = prices.snapshot()["cards"]["cost"]
    assert card["episode_bound"] == 0.0 and card["integral"] == 0.25
    prices.observe("cost", 100, 2)  # a new episode opens at violation 45
    card = prices.snapshot()["cards"]["cost"]
    assert card["episode_bound"] == 0.9 / 45
    assert card["integral"] == 0.9 / 45  # the old episode's 0.9 bound no longer holds it


def test_an_adopted_unbounded_price_unwinds_on_the_decay_schedule():
    """Rulings R10-e refined and R10-n (Codex on #152): adopting 1e308 stores the price
    as given, but the integral the PID uses is clamped to the failure episode's largest
    own bound at its first violating observation (cap / v then). A violation at the
    cap, then compliant windows: the price falls by ``decay`` each window from the
    bound to zero, never locked at the cap (at 1e308, ``decay`` is a float no-op)."""
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
    leaks from the largest bound of the episode that compliance ends, never from
    1e308."""
    prices = _controller()
    prices.register(CardRegion("cost", "max", None, 10.0, 2.0))
    prices.observe("cost", 12, 0)  # violation 1
    prices.set_price("cost", 1e308, amendment_id="unbounded")
    prices.observe("cost", 9, 1)  # compliant
    assert prices.price("cost") == 0.9 - 0.25
