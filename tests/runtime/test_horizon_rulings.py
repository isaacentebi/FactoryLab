"""The horizon opens and closes on the venue's own facts (wave 16, R10-h, R10-i, R10-j, R10-k).

- R10-h: a trade named before its coin's first venue mid opens at that mid.
- R10-i: the exit fee is the venue's most recent successful read at or before H; a
  failed read never erases one, and none read by H is ``fee_unknown``.
- R10-j: an unjudged named trade's outcome is fixed at its horizon and counted.
- R10-k: the margin horizon counts the verdict window once.
"""

from __future__ import annotations

from factorylab.runtime.grounded import OPPORTUNITY_DEFINITION
from factorylab.world.exchange import NS_PER_HOUR
from tests.runtime.test_consequence_horizon import S, _walk, _world
from tests.runtime.test_loop import _consequence_produce
from tests.runtime.test_reward_chain import _mids


def test_a_trade_named_before_its_coin_s_first_venue_mid_opens_at_that_mid():
    rt = _world(10)
    start = 5 * NS_PER_HOUR
    rt.clock.now_ns = start
    _mids(rt, BTC="100")
    rt.venue_marks.clear()  # no venue mid of the coin has been read yet
    producer, _event = _consequence_produce(rt)
    frozen = rt.reference_mids[producer]
    assert frozen["open_ns"] is None and frozen["due_ns"] is None
    rt._observe_mid("BTC", start - 5 * S, "99")  # before the decision: not its opening
    assert frozen["open_ns"] is None
    rt.clock.now_ns = start + 7 * S  # the world moved on; the horizon never uses it
    rt._observe_mid("BTC", start + 7 * S, "101")
    assert frozen["open_ns"] == start + 7 * S
    assert frozen["due_ns"] == start + 7 * S + rt._horizon_ns()
    assert ["BTC", "101"] in frozen["mids"]


def test_a_failed_fee_read_keeps_the_last_successful_rate_and_its_time():
    rt = _world(10)
    listing = {"perp": [{"taker_fee_rate": "0.00045"}], "spot": []}
    rt.exchange.instruments = lambda: listing
    rt.clock.now_ns = 1_000
    rt._read_fee_schedule()
    rt.exchange.instruments = lambda: {}  # a read that states nothing
    rt.clock.now_ns = 2_000
    rt._read_fee_schedule()
    assert rt._taker_rate("BTC") == "0.00045"
    assert rt._rate_at("perp", 999) is None
    assert rt._rate_at("perp", 1_500) == rt._rate_at("perp", 2_500) == "0.00045"
    assert rt._rate_at("spot", 2_500) is None


def test_an_unjudged_named_trade_is_fixed_at_its_horizon_and_counted():
    """No first-tier judge read the hold; its outcome still enters the published
    non-acting observations and the keyed prevalence before it is pruned."""
    rt = _world(10)
    start = 3 * NS_PER_HOUR
    rt.clock.now_ns = start
    _mids(rt, BTC="100")
    producer, _event = _consequence_produce(rt)  # no judge
    _walk(rt, start, 10, 120, lambda s: "90")  # the declined buy would have lost
    rt._settle_evaluations()
    assert rt.world_outcomes[producer]["y"] == 1.0
    assert producer not in rt.reference_mids
    closed = sum(record.get("non_acting_outcomes") or 0 for record in rt.card_samples.windows)
    assert closed + rt.window.non_acting_outcomes == 1
    key = rt._verdict_key(producer, OPPORTUNITY_DEFINITION)
    later = rt.settler.settle_verdict(evaluator_id="eval-a", about_handle="another-return",
                                      q=0.5, outcome=1.0, key=key)
    assert later.base_rate == 1.0 and later.support == 1  # the unjudged outcome entered it


def test_the_margin_horizon_counts_the_verdict_window_once():
    rt = _world(10)
    window = rt.clockwork.period("price", default=rt.m.timing.min_ratio)
    assert rt._margin_horizon() == max(rt.m.timing.min_ratio,
                                       -(-rt._patience_ticks() // window))
