"""One grading horizon on the venue's clock (wave 16, D2; ruling R-C).

A judged return's outcome is fixed once, at H = timing.world_repricing /
timing.min_ratio after it opened, measured in venue time: the first mid timestamped at
or after the horizon prices a named trade, with the funding the named side would have
paid at the venue's funding times inside the window. There is no earlier mark, and
nothing is re-scored later. Because the horizon is venue time, not ticks, the fact a
verdict is graded on does not depend on how fast the factory itself runs.
"""

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal

import pytest

from factorylab.runtime.grounded import FUNDING_PENDING, advance_funding, funding_due
from factorylab.runtime.worlds import load_manifest, manifest_from_dict
from factorylab.world.exchange import NS_PER_HOUR
from tests.runtime.test_loop import _consequence_produce, _consequence_runtime
from tests.runtime.test_reward_chain import Population, _advance, _judge, _mids, _rows

S = 1_000_000_000
BUY = {"coin": "BTC", "side": "buy"}


def _world(tick_s: int, repricing_s: int = 270):
    """A scripted world whose horizon is ``repricing_s / 3`` seconds of venue time."""
    base = load_manifest("scripted")
    manifest = replace(base, tick_interval_ns=tick_s * S,
                       timing=replace(base.timing, world_repricing_ns=repricing_s * S))
    rt = _consequence_runtime(provider=Population(counterfactual=BUY, verdicts=(0.8,)),
                              manifest=manifest)
    rt._manage_reserve_window()
    return rt


def _named_hold(rt, at_ns: int, mid: str = "100"):
    """A hold naming a declined BTC buy, made at venue time ``at_ns``, and its judge."""
    rt.clock.now_ns = at_ns
    _mids(rt, BTC=mid)
    producer, event = _consequence_produce(rt)
    judge = _judge(rt, event)
    rt._settle_arrived_verdicts()
    return producer, judge


def _walk(rt, start_ns: int, tick_s: int, until_s: int, price):
    """Broadcast ``price(seconds since start)`` every ``tick_s`` of venue time."""
    for step in range(1, until_s // tick_s + 1):
        rt.clock.now_ns = start_ns + step * tick_s * S
        _mids(rt, BTC=price(step * tick_s))
        _advance(rt, 1)


def test_the_same_prices_at_10s_and_30s_ticks_give_the_same_y_at_the_same_venue_time():
    """The mids a slow factory and a fast one saw, on the same venue clock, grade the
    same verdict identically and at the same venue nanosecond, whatever their ticks."""
    results = []
    for tick_s in (10, 30):
        rt = _world(tick_s)
        assert rt._horizon_ns() == 90 * S
        start = 10 * NS_PER_HOUR + 7 * S
        producer, judge = _named_hold(rt, start)
        ticks_before = rt.ticks_consumed
        _walk(rt, start, tick_s, 120, lambda s: str(Decimal("100") + Decimal("0.002") * s))
        (priced,) = _rows(rt, "consequence.opportunity", handle=producer)
        (scored,) = _rows(rt, "verdict.consequence", handle=judge)
        results.append((priced["resolved_ns"] - start, priced["net_bps"], scored["y"],
                        scored["score"], rt.ticks_consumed - ticks_before))
    (fast, slow) = results
    assert fast[:4] == slow[:4] == (90 * S, "11.0000", 0.0, fast[3])
    assert fast[4] != slow[4]  # different tick counts, one venue-clock fact


def test_nothing_is_marked_before_the_horizon_and_nothing_is_scored_after_it():
    rt = _world(10)
    start = 3 * NS_PER_HOUR
    producer, judge = _named_hold(rt, start)
    _walk(rt, start, 10, 80, lambda s: "101")
    assert not _rows(rt, "verdict.consequence") and producer not in rt.world_outcomes
    _walk(rt, start + 80 * S, 10, 200, lambda s: "90")
    (scored,) = _rows(rt, "verdict.consequence", handle=judge)
    assert scored["y"] == 1.0  # at 90 s BTC was 90: the declined buy would have lost
    for kind in ("consequence.opportunity_mark", "consequence.marked",
                 "verdict.consequence_late"):
        assert not _rows(rt, kind)


def test_a_funding_time_inside_the_window_is_charged_at_the_venues_rate():
    """The named buy pays the rate in force at the venue's hourly funding time inside
    its window: here it turns a winner (+10 bp against a 7 bp round trip) into a loser."""
    rt = _world(10)
    boundary = 5 * NS_PER_HOUR
    start = boundary - 30 * S
    rt._observe_funding("BTC", start - 10 * S, "0.0004")  # the rate in force at open
    producer, judge = _named_hold(rt, start)
    rt._observe_funding("BTC", boundary + 5 * S, "0.0001")  # printed after the boundary
    _walk(rt, start, 10, 90, lambda s: "100.1")
    (priced,) = _rows(rt, "consequence.opportunity", handle=producer)
    assert priced["funding_payments"] == 1 and priced["funding_bps"] == "-4.0000"
    assert priced["net_bps"] == "-1.0000" and priced["score"] == 1.0
    assert rt.world_outcomes[producer]["y"] == 1.0


def test_a_funding_time_with_no_rate_read_before_it_prices_nothing():
    """An unread rate is never a number: the world has no y for that trade."""
    rt = _world(10)
    boundary = 5 * NS_PER_HOUR
    start = boundary - 30 * S
    producer, judge = _named_hold(rt, start)
    rt._observe_funding("BTC", boundary + 5 * S, "0.0001")
    _walk(rt, start, 10, 90, lambda s: "100.1")
    assert rt.world_outcomes[producer]["state"] == "none"
    assert not _rows(rt, "consequence.opportunity", handle=producer)
    assert rt.pending[judge].consequence_closed and rt.pending[judge].consequence is None


def test_a_trade_the_venue_never_prices_is_none_after_its_patience():
    rt = _world(10)
    start = 7 * NS_PER_HOUR
    producer, judge = _named_hold(rt, start)
    rt.clock.now_ns = start + rt._patience_ns()
    _advance(rt, 1)
    assert producer not in rt.world_outcomes  # still waiting for a mid at the horizon
    rt.clock.now_ns += 1
    _advance(rt, 1)
    assert rt.world_outcomes[producer]["state"] == "none"
    assert rt.pending[judge].consequence_closed and rt.pending[judge].consequence is None


def test_a_world_that_lists_a_venue_must_state_its_repricing_period():
    from tests.runtime.test_manifests import _base

    raw = _base()
    raw.pop("timing")
    with pytest.raises(ValueError, match="world_repricing is required"):
        manifest_from_dict(raw)
    raw["timing"] = {"world_repricing": "1h"}
    manifest = manifest_from_dict(raw)
    assert manifest.consequence_horizon_ns == 20 * 60 * S
    assert manifest.max_tick_ns == manifest.consequence_horizon_ns // 3


def test_a_mark_in_ticks_is_refused():
    from tests.runtime.test_manifests import _base

    raw = _base()
    raw["evaluation"] = {"consequence_horizon_ticks": 10}
    with pytest.raises(ValueError, match="consequence_horizon_ticks was removed"):
        manifest_from_dict(raw)


def test_funding_times_take_the_rate_of_the_latest_print_at_or_before_them():
    state = {"interval": 100, "cursor": 50, "rate": "0.1", "rates": []}
    assert funding_due(state, 50, 250) == FUNDING_PENDING
    advance_funding(state, 170, "0.2")  # passes 100: the print before it, 0.1
    advance_funding(state, 200, "0.3")  # a print at 200 is the rate at 200
    assert state["rates"] == [[100, "0.1"], [200, "0.3"]]
    assert funding_due(state, 50, 250) == ["0.1", "0.3"]
    assert funding_due(state, 150, 199) == []
    assert funding_due(None, 50, 250) == []  # a spot pair pays no funding
    unread = {"interval": 100, "cursor": 50, "rate": None, "rates": []}
    advance_funding(unread, 120, "0.2")
    assert funding_due(unread, 50, 110) is None
