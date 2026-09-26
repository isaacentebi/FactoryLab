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
                           channel="verdict", deadline_ns=rt.clock.now_ns + 10**18,
                           parent_handle=None, cost_ceiling=0)
    rt.consequences.start(handle, rt.n)
    rt.consequences.order_result(handle, {"status": "filled", "order_id": f"o-{coin}",
                                          "filled_size": "0.001"}, {"size": "0.001"}, rt.n)
    rt.consequences.observe("Fill", {"order_id": f"o-{coin}", "coin": coin, "is_buy": True,
                                     "size": "0.001", "px": px, "fee_usd": fee_usd}, rt.n)
    rt.consequences.finish(handle, 0)
    return handle


def test_a_resting_order_filled_at_h_in_the_batch_of_the_mid_at_h_is_marked_by_it():
    """A venue emits MarketMid(H) before the Fill a resting order makes at H, in the
    same batch (Codex on #152, fee12ff). The lot did not exist when the mid arrived,
    yet it is marked at MarketMid(H), never at the next mid: the mark does not depend
    on the order of events within a batch."""
    from factorylab.kernel.queue import PropensityRecord
    from factorylab.world.events import WorldEvent, WorldEventKind
    from tests.conftest import make_runtime

    rt = make_runtime()
    rt.fee_schedule = {"rates": {}, "read_ns": 0, "history": {"BTC": [[0, "0"]]}}
    prop = PropensityRecord(("seed-decider",), (1.0,), "seed-decider", 0, "router:Tick", "t")
    handle = rt.queue.open(actor="router:Tick", event_id="resting", propensity=prop,
                           channel="verdict", deadline_ns=rt.clock.now_ns + 10**18,
                           parent_handle=None, cost_ceiling=0)
    rt.consequences.start(handle, rt.n)
    rt.consequences.order_result(handle, {"status": "resting", "order_id": "o-rest",
                                          "filled_size": "0"}, {"size": "0.001"}, rt.n)
    rt.consequences.finish(handle, 0)
    at_h = rt.clock.now_ns + rt._horizon_ns()
    rt.clock.now_ns = at_h
    rt._settle_exchange_effects([  # one batch, in the venue's own order
        WorldEvent(WorldEventKind.MARKET_MID, at_h, "venue", {"coin": "BTC", "mid": "61000"}),
        WorldEvent(WorldEventKind.FILL, at_h, "venue", {
            "order_id": "o-rest", "coin": "BTC", "is_buy": True, "size": "0.001",
            "px": "60000", "fee_usd": "0", "realized_usd": "0", "liquidation": False}),
    ], observe_positions=False)
    rt.clock.now_ns = at_h + 1
    rt._settle_exchange_effects([WorldEvent(WorldEventKind.MARKET_MID, at_h + 1, "venue",
                                            {"coin": "BTC", "mid": "59000"})],
                                observe_positions=False)
    rt.consequences.resolve(rt.n)
    payoff = rt.consequences.payoff(handle)
    assert payoff is not None and payoff.marked
    assert payoff.net_micro == 1_000_000  # (61000 - 60000) * 0.001, at MarketMid(H)


def test_a_fill_after_h_is_late_money_and_never_graded():
    """Codex on #152 (eaf23e0): a resting order filled after H, in a batch processed
    before the outcome is fixed, never enters the graded outcome and never unmarks it:
    the return is graded on what it held at H (nothing), and the lot is its late
    money."""
    from factorylab.kernel.queue import PropensityRecord
    from tests.conftest import make_runtime

    rt = make_runtime()
    rt.fee_schedule = {"rates": {}, "read_ns": 0, "history": {"BTC": [[0, "0"]]}}
    prop = PropensityRecord(("seed-decider",), (1.0,), "seed-decider", 0, "router:Tick", "t")
    handle = rt.queue.open(actor="router:Tick", event_id="late", propensity=prop,
                           channel="verdict", deadline_ns=rt.clock.now_ns + 10**18,
                           parent_handle=None, cost_ceiling=0)
    rt.consequences.start(handle, rt.n)
    rt.consequences.order_result(handle, {"status": "resting", "order_id": "o-late",
                                          "filled_size": "0"}, {"size": "0.001"}, rt.n)
    rt.consequences.finish(handle, 0)
    at_h = rt.clock.now_ns + rt._horizon_ns()
    rt.consequences.observe("MarketMid", {"coin": "BTC", "mid": "61000", "ts_ns": at_h}, rt.n)
    rt.consequences.observe("Fill", {"order_id": "o-late", "coin": "BTC", "is_buy": True,
                                     "size": "0.001", "px": "60000", "fee_usd": "0",
                                     "ts_ns": at_h + 5}, rt.n)
    rt.consequences.observe("MarketMid", {"coin": "BTC", "mid": "60500",
                                          "ts_ns": at_h + 6}, rt.n)
    rt.clock.now_ns = at_h + 6
    rt.consequences.resolve(rt.n)
    payoff = rt.consequences.payoff(handle)
    assert payoff is not None and not payoff.marked and payoff.net_micro == 0
    assert [lot.handle for lot in rt.consequences.table.lots] == [handle]  # late money


def test_an_instrument_never_priced_after_h_fixes_the_outcome_as_none_after_patience():
    """The instrument stops publishing once the lot opens (Codex on #152). The return
    waits for its mark no longer than a named trade would, a patience past its opening:
    then its outcome is fixed, uninformative (``no_mark``, naming the instrument), the
    world outcome is none, and once its position closes and its orders are confirmed
    terminal, the decision is releasable."""
    from factorylab.kernel.queue import PropensityRecord
    from factorylab.settlement.lots import NO_MARK
    from tests.conftest import make_runtime
    from tests.runtime.test_reward_chain import _rows

    rt = make_runtime()
    rt.fee_schedule = {"rates": {}, "read_ns": 0, "history": {"BTC": [[0, "0"]]}}
    handle = _open_long(rt, "BTC", "60000")
    opened = rt.clock.now_ns
    rt.consequences.observe("MarketMid", {"coin": "BTC", "mid": "60000", "ts_ns": opened},
                            rt.n)  # the instrument's last mid, before H
    rt.clock.now_ns = opened + rt._patience_ns()
    rt.consequences.tick_through_ns = rt.clock.now_ns  # a tick: facts through now are in
    assert all(p.handle != handle for p in rt.consequences.resolve(rt.n))  # still waiting
    assert rt.consequences.payoff(handle) is None
    rt.clock.now_ns += 1
    rt.consequences.tick_through_ns = rt.clock.now_ns
    (payoff,) = [p for p in rt.consequences.resolve(rt.n) if p.handle == handle]
    assert payoff.censored == NO_MARK and payoff.y == 0
    (row,) = _rows(rt, "consequence.uninformative", handle=handle)
    assert row["reason"] == NO_MARK and row["instruments"] == ["BTC"]
    assert rt._final_outcome(handle)[0] == "none"
    assert not rt.consequences.releasable(handle)  # its position is still open: money
    prop = PropensityRecord(("seed-decider",), (1.0,), "seed-decider", 0, "router:Tick", "t")
    closer = rt.queue.open(actor="router:Tick", event_id="close", propensity=prop,
                           channel="verdict", deadline_ns=rt.clock.now_ns + 10**18,
                           parent_handle=None, cost_ceiling=0)
    rt.consequences.start(closer, rt.n)
    rt.consequences.order_result(closer, {"status": "filled", "order_id": "o-close",
                                          "filled_size": "0.001"}, {"size": "0.001"}, rt.n)
    rt.consequences.observe("Fill", {"order_id": "o-close", "coin": "BTC", "is_buy": False,
                                     "size": "0.001", "px": "60000", "fee_usd": "0"}, rt.n)
    for order_id in ("o-BTC", "o-close"):
        rt.consequences.confirm_terminal(order_id, "filled", "0.001", rt.n)
    assert rt.consequences.releasable(handle)


class _PolledVenue:
    """A live-like venue: mids at the request time, fills only as the venue reports them
    (``reported``), and a fills read that can fail (``down``)."""

    name = "polled"

    def __init__(self):
        self.reported: list = []
        self.down = False
        self.mid = "100"

    def mids(self):
        from decimal import Decimal

        return {"BTC": Decimal(self.mid)}

    def funding(self):
        return []

    def funding_payments(self, _since):
        return []

    def fills(self, _since):
        if self.down:
            raise RuntimeError("the venue did not answer")
        return list(self.reported)


def _polled_runtime():
    """A runtime reading a polled venue (ruling R10-o), with one resting BTC buy open."""
    from factorylab.kernel.queue import PropensityRecord
    from factorylab.runtime.live import LiveVenue
    from factorylab.settlement.consequence import FillCursor
    from tests.conftest import make_runtime

    rt = make_runtime()
    rt.fee_schedule = {"rates": {}, "read_ns": 0, "history": {"BTC": [[0, "0"]]}}
    start = rt.clock.now_ns
    venue = _PolledVenue()
    rt.venue = LiveVenue(venue, last_fill_ns=start, last_funding_ns=start)
    rt.consequence_fills = FillCursor(rt.ledger, start_ns=start)
    prop = PropensityRecord(("seed-decider",), (1.0,), "seed-decider", 0, "router:Tick", "t")
    handle = rt.queue.open(actor="router:Tick", event_id="polled", propensity=prop,
                           channel="verdict", deadline_ns=start + 10**18, parent_handle=None,
                           cost_ceiling=0)
    rt.consequences.start(handle, rt.n)
    rt.consequences.order_result(handle, {"status": "resting", "order_id": "o-polled",
                                          "filled_size": "0"}, {"size": "0.001"}, rt.n)
    rt.consequences.finish(handle, 0)
    return rt, venue, handle, start


def _polled_tick(rt, venue, now, *, poll=True):
    """One live tick: the tick itself, the venue's mids, and (``poll``) a fills read."""
    from factorylab.world.events import WorldEvent, WorldEventKind

    rt.clock.now_ns = now
    rt.tick_through_ns, rt.last_tick_ns = rt.last_tick_ns, now
    rt.consequences.tick_through_ns = rt.tick_through_ns
    events = rt.venue.on_tick(now, include_fills=False)
    if poll:
        events += [WorldEvent(WorldEventKind.FILL, max(now, ts), venue.name, payload)
                   for ts, payload in rt.consequence_fills.poll(venue, now_ns=now)]
    rt._settle_exchange_effects(events, observe_positions=False)
    rt.consequences.resolve(rt.n)


def test_a_fill_before_h_reported_after_the_tick_past_h_is_waited_for_and_graded():
    """R10-o: fills are polled. The resting order filled 5 s before H, but no fills read
    ran until well after the tick that passed H: the outcome waits for the fills stream
    to be read through H, and then includes the fill, marked at the first mid after H."""
    from decimal import Decimal

    from factorylab.world.exchange import Fill

    rt, venue, handle, start = _polled_runtime()
    horizon = rt._horizon_ns()
    _polled_tick(rt, venue, start)
    venue.mid = "101"
    for step in range(1, horizon // (10 * 10**9) + 3):  # ticks past H, no fills read
        _polled_tick(rt, venue, start + step * 10 * 10**9, poll=False)
    assert rt.tick_through_ns > start + horizon
    assert rt.consequences.payoff(handle) is None  # the fills stream is read through start
    venue.reported = [Fill("o-polled", "BTC", True, Decimal("0.001"), Decimal("100"),
                           Decimal(0), start + horizon - 5 * 10**9)]
    _polled_tick(rt, venue, start + horizon + 40 * 10**9)
    payoff = rt.consequences.payoff(handle)
    assert payoff is not None and payoff.marked
    assert payoff.net_micro == 1_000  # (101 - 100) * 0.001: the pre-H fill is graded


def test_a_failed_fills_read_holds_the_outcome():
    """R10-o: a failed fills read advances nothing, so the outcome of a resting order
    waits past every tick until a read succeeds through H."""
    rt, venue, handle, start = _polled_runtime()
    horizon = rt._horizon_ns()
    _polled_tick(rt, venue, start)
    venue.down = True
    for step in range(1, horizon // (10 * 10**9) + 3):
        _polled_tick(rt, venue, start + step * 10 * 10**9)
    assert rt.consequence_fills.through_ns == start
    assert rt.consequences.payoff(handle) is None
    venue.down = False
    _polled_tick(rt, venue, start + horizon + 40 * 10**9)
    payoff = rt.consequences.payoff(handle)
    assert payoff is not None and not payoff.marked and payoff.net_micro == 0  # no fill


def _tape_runtime():
    """A runtime on the recorded longrun1 slice (a venue that advances), BTC fees read."""
    from decimal import Decimal

    from factorylab.world.tape import Tape, TapeVenue
    from tests.world.test_tape import LONGRUN

    tape = Tape.load(LONGRUN)
    fees = {"BTC": {"venue_read": {side: [[tape.start_ns, "0", ["test read"]]]
                                   for side in ("taker", "maker")}}}
    venue = TapeVenue(Tape.from_data(dict(tape.data, fees=fees)), coins=("BTC",),
                      start_cash_usd=Decimal(120))
    rt = _world(10)
    rt.exchange, rt.fee_schedule = venue, None
    return rt, tape


def test_a_safety_pass_advance_accounts_the_mid_at_h_it_delivers(monkeypatch):
    """Codex on #152 (a9e7e7e): a safety pass advances a recorded venue while a model
    thinks. Its watermark covers every fact of that advance, so every fact is accounted,
    the mid at or after H included, though it is not broadcast to the seats: the return
    is marked at that mid, never at the next tick's."""
    from types import SimpleNamespace

    from factorylab.runtime import loop as loop_module

    rt, tape = _tape_runtime()
    horizon = rt._horizon_ns()
    ticks = tape.ticks
    # An opening tick whose H falls between two recorded ticks with different mids.
    for opened_at in ticks:
        due = opened_at + horizon
        later = [t for t in ticks if t > due]
        if later and tape.mid_at("BTC", due)[1] != tape.mid_at("BTC", later[0])[1]:
            break
    rt.clock.now_ns = opened_at
    rt._settle_exchange_effects(rt._advance_venue(opened_at))
    handle = _open_long(rt, "BTC", str(tape.mid_at("BTC", opened_at)[1]))
    monkeypatch.setattr(loop_module, "wall_paced", lambda _clock: True)
    rt.wall = SimpleNamespace(now_ns=lambda: due, tick_ns=lambda: 1)
    rt._safety_ns = 0
    internal = len(rt.internal)
    rt._safety_pass()  # advances the tape to H, between two ticks
    assert not [e for e in list(rt.internal)[internal:] if str(e.kind) == "MarketMid"]
    rt.clock.now_ns = later[0]
    rt._settle_exchange_effects(rt._advance_venue(later[0]))  # the next tick's mids
    rt.tick_through_ns = rt.consequences.tick_through_ns = later[0]
    rt.consequences.resolve(rt.n)
    payoff = rt.consequences.payoff(handle)
    assert payoff is not None and payoff.marked
    at_h = tape.mid_at("BTC", due)[1]
    opened_px = tape.mid_at("BTC", opened_at)[1]
    assert payoff.net_micro == int((at_h - opened_px) * 1000)  # 0.001 BTC, in micro-USD


def test_a_mark_the_final_tape_advance_delivers_is_graded_before_terminated():
    """Codex on #152 (a9e7e7e): a declined trade whose mark at H arrives only in the
    tape's final advance through its close is priced, and its judge's consequence grade
    delivered, before the kill winds down and the world is Terminated."""
    from tests.runtime.test_consequence_horizon import _named_hold
    from tests.runtime.test_reward_chain import _advance, _rows

    rt, tape = _tape_runtime()
    closes = rt.exchange.closes_ns
    decided = closes - rt._horizon_ns() - 20 * S
    rt.clock.now_ns = decided
    rt._settle_exchange_effects(rt._advance_venue(decided))
    producer, judge = _named_hold(rt, decided, str(tape.mid_at("BTC", decided)[1]))
    before_close = decided + 30 * S
    rt.clock.now_ns = before_close
    rt._settle_exchange_effects(rt._advance_venue(before_close))
    _advance(rt, 1)
    assert not _rows(rt, "consequence.opportunity", handle=producer)  # H not reached
    rt.kill("tape ended", through_tape_end=True)
    (priced,) = _rows(rt, "consequence.opportunity", handle=producer)
    (graded,) = _rows(rt, "verdict.consequence", handle=judge)
    items = rt.ledger._recovery_items()
    killed = next(i["seq"] for i in items if i.get("kind") == "kill.production")
    assert priced["seq"] < killed and graded["seq"] < killed


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


# --- retention is need-based (Codex on #152, 7e78d6a) ----------------------------------


def _long_patience_world(verdict_timeout_ticks: int):
    from dataclasses import replace

    rt = _world(10)
    rt.m = replace(rt.m, evaluation=replace(rt.m.evaluation,
                                            verdict_timeout_events=verdict_timeout_ticks))
    rt.ev = rt.m.evaluation
    return rt


def test_the_rate_at_h_is_kept_while_the_mid_at_h_is_awaited_past_many_repricings():
    """The first mid after H arrives more than three repricing periods after H, inside
    a long verdict window. The fee is re-read every period meanwhile, and falls from
    4.5 bp to 3.5 bp just after H: both roads still exit at the rate at H, never
    fee_unknown. A fixed two-period window had dropped it."""
    from decimal import Decimal

    from tests.runtime.test_reward_chain import _advance, _rows

    rt = _long_patience_world(400)
    period, horizon = rt.m.timing.world_repricing_ns, rt._horizon_ns()
    start = 3 * NS_PER_HOUR
    assert rt._patience_ns() > horizon + 4 * period

    def listing():
        rate = "0.00045" if rt.clock.now_ns <= start + horizon else "0.00035"
        return {"perp": [{"coin": "BTC", "taker_fee_rate": rate}], "spot": []}

    rt.exchange.instruments = listing
    rt.fee_schedule = None
    rt.clock.now_ns = start
    _mids(rt, BTC="100")
    producer, _event = _consequence_produce(rt)
    lot = _open_long(rt, "BTC", "100", fee_usd="0.000045")
    for k in range(1, 5):  # four re-reads, no BTC mid: the mid at H is not yet broadcast
        rt.clock.now_ns = start + k * period
        rt._read_fee_schedule()
    rt.clock.now_ns = start + horizon + 3 * period + 10 * 10**9
    _mids(rt, BTC="100")
    _advance(rt, 1)
    rt._settle_evaluations()
    (priced,) = _rows(rt, "consequence.opportunity", handle=producer)
    assert (priced["entry_fee_bps"], priced["exit_fee_bps"]) == ("4.5", "4.5")
    assert not _rows(rt, "consequence.uninformative", reason="fee_unknown")
    payoff = rt.consequences.payoff(lot)
    assert payoff is not None and payoff.censored is None
    assert Decimal(-payoff.net_micro) / 100_000 * 10_000 == 9  # 4.5 in, 4.5 out at H


def test_with_nothing_open_the_fee_history_is_the_latest_read():
    """Bounded: with no open consequence, a read keeps only the latest rate per
    instrument, however many were read before."""
    rt = _world(10)
    rates = iter(["0.00045", "0.00040", "0.00035", "0.00030"])

    def listing():
        return {"perp": [{"coin": "BTC", "taker_fee_rate": next(rates)}], "spot": []}

    rt.exchange.instruments = listing
    rt.fee_schedule = None
    period = rt.m.timing.world_repricing_ns
    for k in range(4):
        rt.clock.now_ns = 3 * NS_PER_HOUR + k * period
        rt._read_fee_schedule()
    assert not rt.reference_mids
    assert rt.fee_schedule["history"]["BTC"] == [[3 * NS_PER_HOUR + 3 * period, "0.00030"]]


def test_a_named_trade_opened_after_its_decision_is_kept_until_its_own_lapse():
    """R10-h: a trade opened at its coin's first venue mid after the decision is priced
    up to a patience past that opening; the prune never ages it from its decision."""
    rt = _world(10)
    start = 5 * NS_PER_HOUR
    rt.clock.now_ns = start
    _mids(rt, BTC="100")
    rt.venue_marks.clear()
    producer, _event = _consequence_produce(rt)
    opened = start + rt._patience_ns() // 2
    rt.clock.now_ns = opened
    rt._observe_mid("BTC", opened, "100")
    frozen = rt.reference_mids[producer]
    rt.clock.now_ns = start + rt._patience_ns() + 1  # a patience past the decision
    rt.tick_through_ns = rt.clock.now_ns  # a tick: every fact through now is in
    rt._settle_evaluations()
    assert rt.reference_mids.get(producer) is frozen  # not yet a patience past opening
    rt.clock.now_ns = opened + rt._patience_ns() + 1
    rt.tick_through_ns = rt.clock.now_ns
    rt._settle_evaluations()
    assert producer not in rt.reference_mids


# --- R10-m: funding stops at H -----------------------------------------------------------


def _funding_past_h(late: bool):
    """A declined BTC buy and an acting long, both opened 100 s before an hourly funding
    boundary (H = 90 s: the boundary is 10 s past H). A funding payment before H counts;
    the boundary past H is printed at a large rate. ``late``: the first mid at or after
    H arrives only after that boundary."""
    from tests.runtime.test_consequence_horizon import _named_hold
    from tests.runtime.test_reward_chain import _advance, _rows

    rt = _long_patience_world(40)
    boundary = 5 * NS_PER_HOUR
    start = boundary - 100 * S
    assert start + rt._horizon_ns() < boundary
    rt._observe_funding("BTC", start - 10 * S, "0.0004")
    producer, _judge = _named_hold(rt, start)
    lot = _open_long(rt, "BTC", "100")
    rt.consequences.observe("Funding", {"coin": "BTC", "paid_usd": "0.00002",
                                        "ts_ns": start + 60 * S}, rt.n)  # before H
    if late:
        rt._observe_funding("BTC", boundary + 5 * S, "0.01")
        rt.consequences.observe("Funding", {"coin": "BTC", "paid_usd": "0.001",
                                            "ts_ns": boundary}, rt.n)  # past H
        rt.clock.now_ns = boundary + 10 * S
        _mids(rt, BTC="100.1")
        _advance(rt, 1)
    else:
        _walk(rt, start, 10, 90, lambda s: "100.1")
    rt._settle_evaluations()
    (priced,) = _rows(rt, "consequence.opportunity", handle=producer)
    payoff = rt.consequences.payoff(lot)
    assert payoff is not None and payoff.censored is None
    return priced, payoff


def test_a_funding_boundary_past_h_changes_neither_road_however_late_the_mark():
    """Ruling R10-m: both roads accrue funding only for funding times at or before H.
    The mark that fixes them arriving after an extra funding boundary past H changes
    neither outcome: the named trade pays no funding at it, and the acting lot's
    payment for it is set aside (the money itself is still booked as charged)."""
    on_time, on_time_lot = _funding_past_h(late=False)
    late, late_lot = _funding_past_h(late=True)
    for key in ("funding_payments", "funding_bps", "net_bps", "score"):
        assert late[key] == on_time[key], key
    assert late["funding_payments"] == 0
    assert late_lot.net_micro == on_time_lot.net_micro
    assert (late_lot.y, late_lot.cost_micro) == (on_time_lot.y, on_time_lot.cost_micro)
    # (100.1 - 100) * 0.001 BTC, less the $0.00002 paid before H and the exit fee at H.
    assert on_time_lot.net_micro == 100 - 20 - on_time_lot.exit_fee_micro


def test_a_tape_advance_past_an_hour_boundary_grades_both_roads_at_that_boundary_s_rate():
    """A tape reports a crossed hour boundary's payment at its advance time, with the
    boundary itself as ``funding_ns`` (Codex on #152, c92a7b8). The rate changes at
    the boundary; the named trade spanning it is graded at the new rate, the rate the
    acting lot's payment was charged at, and the lot's payment counts as inside H."""
    from decimal import Decimal

    from factorylab.world.exchange import Position
    from factorylab.world.tape import Tape, TapeVenue
    from tests.runtime.test_consequence_horizon import _named_hold
    from tests.runtime.test_reward_chain import _advance, _rows
    from tests.world.test_tape import BOUNDARY, LONGRUN

    tape = Tape.load(LONGRUN)
    old, new = "0.0001", "0.0009"
    funding = dict(tape.data["funding"], BTC=[[tape.start_ns, old, None],
                                               [BOUNDARY, new, None]])
    fees = {"BTC": {"venue_read": {side: [[tape.start_ns, "0.00045", ["test read"]]]
                                   for side in ("taker", "maker")}}}
    venue = TapeVenue(Tape.from_data(dict(tape.data, funding=funding, fees=fees)),
                      coins=("BTC",), start_cash_usd=Decimal(120))
    rt = _world(10)
    rt.exchange, rt.fee_schedule = venue, None
    start = BOUNDARY - 30 * S
    venue.advance(start)
    rt._observe_funding("BTC", start - 10 * S, old)  # the rate in force at the decision
    producer, _judge = _named_hold(rt, start)
    lot = _open_long(rt, "BTC", "100")
    venue._positions["BTC"] = Position("BTC", Decimal("0.001"), Decimal("100"))
    reported = BOUNDARY + 20 * S  # the tick that advances past the boundary
    rt.clock.now_ns = reported
    rt.internal.clear()
    events = venue.advance(reported)
    (paid,) = [e for e in events if e.kind == "Funding" and e.payload["coin"] == "BTC"]
    assert (paid.ts_ns, paid.payload["funding_ns"], paid.payload["rate"]) == (
        reported, BOUNDARY, new)
    rt._settle_exchange_effects(events)
    while rt.internal:
        rt._process_event(rt.internal.popleft())
    assert rt.reference_mids[producer]["funding"]["rates"] == [[BOUNDARY, new]]
    rt.clock.now_ns = start + rt._horizon_ns()
    _mids(rt, BTC="100")
    _advance(rt, 1)
    rt._settle_evaluations()
    (priced,) = _rows(rt, "consequence.opportunity", handle=producer)
    assert priced["funding_payments"] == 1
    assert Decimal(priced["funding_bps"]) == -Decimal(new) * 10_000  # the buy pays 9 bp
    payoff = rt.consequences.payoff(lot)  # the boundary is inside the lot's H
    assert payoff is not None and payoff.censored is None
    # The lot bore that very payment: its size times the tape's mark at the boundary
    # times the new rate, in micro-USD, beside the same exit fee.
    charged = Decimal("0.001") * tape.mid_at("BTC", BOUNDARY)[1] * Decimal(new) * 10**6
    assert payoff.net_micro == int(-(charged + payoff.exit_fee_micro).to_integral_value(
        rounding="ROUND_CEILING"))
