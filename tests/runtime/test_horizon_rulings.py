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
    listing = {"perp": [{"coin": "BTC", "taker_fee_rate": "0.00045"}], "spot": []}
    rt.exchange.instruments = lambda: listing
    rt.clock.now_ns = 1_000
    rt._read_fee_schedule()
    rt.exchange.instruments = lambda: {}  # a read that states nothing
    rt.clock.now_ns = 2_000
    rt._read_fee_schedule()
    assert rt._taker_rate("BTC") == "0.00045"
    assert rt._rate_at("BTC", 999) is None
    assert rt._rate_at("BTC", 1_500) == rt._rate_at("BTC", 2_500) == "0.00045"
    assert rt._rate_at("PURR/USDC", 2_500) is None


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


# --- world truth at an instant (Codex on #152) -----------------------------------------


def _open_long(rt, coin="BTC", px="60000", fee_usd="0"):
    from factorylab.kernel.queue import PropensityRecord

    prop = PropensityRecord(("seed-decider",), (1.0,), "seed-decider", 0, "router:Tick", "t")
    handle = rt.queue.open(actor="router:Tick", event_id=f"trade-{coin}", propensity=prop,
                           channel="verdict", deadline_ns=10**18, parent_handle=None,
                           cost_ceiling=0)
    rt.consequences.start(handle, rt.n)
    rt.consequences.order_result(handle, {"status": "filled", "order_id": f"o-{coin}",
                                          "filled_size": "0.001"}, {"size": "0.001"}, rt.n)
    rt.consequences.observe("Fill", {"order_id": f"o-{coin}", "coin": coin, "is_buy": True,
                                     "size": "0.001", "px": px, "fee_usd": fee_usd}, rt.n)
    rt.consequences.finish(handle, 0)
    return handle


def test_a_tick_at_h_before_the_mid_at_h_marks_at_the_mid_at_h():
    """D2: the mark is the first venue mid timestamped at or after the horizon. The
    batch's Tick at H comes first, with an earlier instant's mid cached: nothing is
    fixed on it, and the mid at H, when it arrives, is the mark."""
    from tests.conftest import make_runtime

    rt = make_runtime()
    rt.fee_schedule = {"rates": {}, "read_ns": 0, "history": {"BTC": [[0, "0"]]}}
    handle = _open_long(rt)
    rt.consequences.observe("MarketMid", {"coin": "BTC", "mid": "60000",
                                          "ts_ns": rt.clock.now_ns}, rt.n)
    rt.clock.now_ns += rt._horizon_ns()
    assert all(p.handle != handle for p in rt.consequences.resolve(rt.n))  # Tick(H)
    rt.consequences.observe("MarketMid", {"coin": "BTC", "mid": "61000",
                                          "ts_ns": rt.clock.now_ns}, rt.n)  # MarketMid(H)
    rt.consequences.observe("MarketMid", {"coin": "BTC", "mid": "59000",
                                          "ts_ns": rt.clock.now_ns + 1}, rt.n)  # later
    (payoff,) = [p for p in rt.consequences.resolve(rt.n) if p.handle == handle]
    assert payoff.net_micro == 1_000_000  # (61000 - 60000) * 0.001, marked at MarketMid(H)


def test_a_tape_s_per_coin_rates_price_each_trade_at_its_own_rate():
    """Fees are per instrument: a tape recording BTC at 4.5 bp and ETH at 3.5 bp prices
    a BTC trade's round trip at 9 bp and an ETH trade's at 7, and a lot's exit at its
    own coin's rate; neither is pooled into one schedule, nor censored for disagreeing."""
    from decimal import Decimal

    from factorylab.runtime.grounded import opportunity_cost
    from factorylab.world.tape import Tape, TapeVenue
    from tests.conftest import make_runtime
    from tests.world.test_tape import LONGRUN

    tape = Tape.load(LONGRUN)
    fees = {coin: {"venue_read": {"taker": [[tape.start_ns, rate, ["test read"]]],
                                  "maker": [[tape.start_ns, rate, ["test read"]]]}}
            for coin, rate in (("BTC", "0.00045"), ("ETH", "0.00035"))}
    venue = TapeVenue(Tape.from_data(dict(tape.data, fees=fees)), coins=("BTC", "ETH"),
                      start_cash_usd=Decimal(120))
    rt = make_runtime()
    rt.exchange, rt.fee_schedule = venue, None
    rt.clock.now_ns = tape.start_ns
    rt._read_fee_schedule()
    assert rt._taker_rate("BTC") == "0.00045" and rt._taker_rate("ETH") == "0.00035"
    for coin, bps in (("BTC", "9"), ("ETH", "7")):
        priced = opportunity_cost([(coin, "100")], [(coin, "100")], rt._taker_rate(coin),
                                  rt._taker_rate(coin),
                                  {"coin": coin, "side": "buy"})
        assert Decimal(priced["round_trip_fee_bps"]) == Decimal(bps), coin
    assert rt._rate_at("BTC", tape.start_ns) == "0.00045"
    assert rt._rate_at("ETH", tape.start_ns) == "0.00035"


def test_the_declined_road_pays_each_leg_at_its_own_instant_as_an_acting_lot_does():
    """Wave 16, D7: both roads pay the same round trip. The venue's rate falls from
    4.5 bp to 3.5 bp between the decision and H: the declined road pays 4.5 bp in (the
    rate at the decision, D1's ex-ante leg) and 3.5 bp out (the rate at H), 8 bp, the
    fee an acting lot opened and marked at the same instants pays. Twice the entry rate
    (9 bp) would price a round trip no acting lot pays."""
    from decimal import Decimal

    from tests.runtime.test_reward_chain import _rows

    rt = _world(10)
    start = 3 * NS_PER_HOUR
    change = start + rt._horizon_ns() // 2

    def listing():
        rate = "0.00045" if rt.clock.now_ns < change else "0.00035"
        return {"perp": [{"coin": "BTC", "taker_fee_rate": rate}], "spot": []}

    rt.exchange.instruments = listing
    rt.fee_schedule = None
    rt.clock.now_ns = start
    _mids(rt, BTC="100")
    producer, _event = _consequence_produce(rt)
    assert rt.reference_mids[producer]["taker_rate"] == "0.00045"
    lot = _open_long(rt, "BTC", "100", fee_usd="0.000045")  # 4.5 bp of a $0.1 fill
    rt.clock.now_ns = change
    rt._read_fee_schedule()
    _walk(rt, start, 10, 120, lambda s: "100")
    rt._settle_evaluations()
    (priced,) = _rows(rt, "consequence.opportunity", handle=producer)
    assert (priced["entry_fee_bps"], priced["exit_fee_bps"]) == ("4.5", "3.5")
    assert Decimal(priced["round_trip_fee_bps"]) == 8
    payoff = rt.consequences.payoff(lot)
    assert payoff is not None and payoff.censored is None
    notional_micro = 100_000  # 0.001 BTC at 100
    assert Decimal(-payoff.net_micro) / notional_micro * 10_000 == 8  # flat mid: fees only


def test_a_leg_whose_rate_was_never_read_leaves_the_declined_road_uninformative():
    """Ruling R10-i, per leg: no rate read by the decision (entry) or by H (exit) fixes
    the outcome with no y, ledgered ``consequence.uninformative``, reason fee_unknown."""
    from factorylab.settlement.lots import FEE_UNKNOWN
    from tests.runtime.test_reward_chain import _rows

    for unread in ("entry", "exit"):
        rt = _world(10)
        start = 3 * NS_PER_HOUR
        rt.clock.now_ns = start
        _mids(rt, BTC="100")
        producer, _event = _consequence_produce(rt)
        frozen = rt.reference_mids[producer]
        if unread == "entry":
            frozen["taker_rate"] = None
        else:  # the first rate the venue ever stated came after H
            rt.fee_schedule["history"]["BTC"] = [[frozen["due_ns"] + 1, "0.00045"]]
        _walk(rt, start, 10, 120, lambda s: "90")
        rt._settle_evaluations()
        assert rt.world_outcomes[producer]["state"] == "none", unread
        assert not _rows(rt, "consequence.opportunity", handle=producer), unread
        (row,) = _rows(rt, "consequence.uninformative", handle=producer)
        assert row["reason"] == FEE_UNKNOWN, unread
