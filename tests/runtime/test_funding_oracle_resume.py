"""Chapter II §III.b: delayed settlement retains its original observation evidence."""
from dataclasses import replace
from decimal import Decimal

import pytest

from factorylab.runtime.loop import Runtime
from factorylab.runtime.resume import restore_runtime, resume_runtime, runtime_state
from factorylab.runtime.worlds import load_manifest
from factorylab.world.clock import ClockSource
from factorylab.world.events import WorldEventKind
from factorylab.world.exchange import NS_PER_HOUR, FakeExchange, FundingEvent


@pytest.mark.gate
def test_delayed_funding_oracle_evidence_survives_runtime_checkpoint(tmp_path):
    boundary = NS_PER_HOUR
    observed = boundary + 123_456_789

    class Venue(FakeExchange):
        settled_funding = True
        funding_interval_ns = NS_PER_HOUR

        def funding(self):
            return [FundingEvent("BTC", Decimal("0.9"), None, observed,
                                 Decimal("123.456789123"))]

        def settled_funding_history(self, coin, start, end):
            return []

    exchange = Venue(seed=1, coins=("BTC", "BTC/USDC"))
    base = load_manifest("scripted")
    manifest = replace(base, exchange=replace(base.exchange, kind="hyperliquid", coins=("BTC",)))
    path = tmp_path / "oracle.jsonl"
    runtime = Runtime(manifest, events=1, seed=1, initial_balance_micro=None,
                      ledger_path=str(path), router_gamma=0.1, exchange=exchange,
                      clock_source=ClockSource(observed, 1_000_000_000, 1).events())
    process = runtime._process_event

    class Interrupted(Exception):
        pass

    def checkpoint_after_poll(event):
        result = process(event)
        if event.kind == WorldEventKind.TICK:
            assert runtime._snapshot("oracle_observed")
            raise Interrupted
        return result

    runtime._process_event = checkpoint_after_poll
    with pytest.raises(Interrupted):
        runtime.run()

    # Restore through the durable runtime boundary, not a copied venue-state dict.
    restored = resume_runtime(manifest, str(path), exchange=exchange, now_ns=2 * boundary)
    try:
        exchange.settled_funding_history = lambda coin, start, end: [
            FundingEvent("BTC", Decimal("0.001"), None, boundary)]
        exchange.funding = lambda: [FundingEvent("BTC", Decimal("0.9"), None,
                                                2 * boundary, Decimal("500"))]
        events = [event for event in restored.venue.on_tick(2 * boundary)
                  if event.payload.get("settled")]
        assert len(events) == 1
        assert events[0].payload["mark"] == "123.456789123"
        assert events[0].payload["oracle_observed_at_ns"] == observed
        assert events[0].payload["oracle_offset_seconds"] == "0.123456789"
    finally:
        restored._ledger_lock.close()


@pytest.mark.gate
def test_stale_mark_retains_gap_for_later_named_trade_and_releases_it():
    from tests.runtime.test_consequence_horizon import S
    from tests.runtime.test_loop import _consequence_produce
    from tests.runtime.test_reward_chain import Population, _judge, _rows

    interval = 60 * S

    class Venue(FakeExchange):
        settled_funding = True
        funding_interval_ns = interval

        def mids(self):
            return {}  # The cached mark is the only available opening price.

        def settled_funding_history(self, coin, start, end):
            return [row for row in self.published if start <= row.ts_ns <= end]

    exchange = Venue(seed=1, coins=("BTC",), funding_interval_ns=interval)
    exchange.published = []
    base = load_manifest("scripted")
    manifest = replace(base, exchange=replace(base.exchange, kind="hyperliquid", coins=("BTC",)),
                       timing=replace(base.timing, world_repricing_ns=270 * S))
    rt = Runtime(manifest, events=0, seed=1, initial_balance_micro=None,
                 ledger_path=None, router_gamma=0.1, exchange=exchange,
                 provider=Population(counterfactual={"coin": "BTC", "side": "buy"},
                                     verdicts=(0.8,)),
                 clock_source=ClockSource(0, S, 1).events())
    try:
        rt._manage_reserve_window()
        rt._observe_mid("BTC", 0, "100")
        rt.recent_mids["BTC"] = [{"t_s": 0, "mid": "100"}]
        rt.venue.settled_launch_ns = 0
        rt.fee_schedule = {"rates": {"BTC": "0"}, "read_ns": 0,
                           "history": {"BTC": [[0, "0"]]}}
        rt.venue.funding_oracles["BTC"] = {interval: ("100", interval)}
        assert not rt.reference_mids
        rt.venue._settled_rates(2 * interval, {"BTC"})
        rt.venue._settled_rates(2 * interval + S, {"BTC"})
        assert interval in rt.venue.settled_gaps["BTC"]
        rt.clock.now_ns = 2 * interval + S
        producer, event = _consequence_produce(rt)
        judge = _judge(rt, event)
        rt._settle_arrived_verdicts()
        assert rt.reference_mids[producer]["open_ns"] == 0
        rt._observe_mid("BTC", rt.reference_mids[producer]["due_ns"], "90")
        exchange.published = [FundingEvent("BTC", Decimal("0.001"), None, interval)]
        events = rt.venue._settled_rates(3 * interval, {"BTC"})
        for event in events:
            p = event.payload
            rt._observe_funding(p["coin"], p["funding_ns"], p["rate"], p["mark"], settled=True)
        rt.tick_through_ns = rt.clock.now_ns
        rt.venue.through.update(mids=rt.clock.now_ns, rates=rt.clock.now_ns)
        assert rt._reference_outcome(rt.reference_mids[producer])[0] == "measured", (
            rt.reference_mids[producer], rt._patience_ns(), rt.clock.now_ns)
        rt._settle_evaluations()
        assert rt.world_outcomes[producer]["state"] == "measured", (
            _rows(rt, "consequence.uninformative"), rt.fee_schedule)
        assert len(_rows(rt, "verdict.consequence", handle=judge)) == 1
        # Advancing marks bound retention; each future trade now opens after old gaps.
        for step in range(4, 24):
            rt.clock.now_ns = step * interval
            rt._observe_mid("BTC", rt.clock.now_ns, "90")
            rt.venue._settled_rates(rt.clock.now_ns, {"BTC"})
            assert len(rt.venue.settled_gaps["BTC"]) <= 1
            assert len(rt.venue.funding_oracles["BTC"]) <= 1
            assert len(rt.venue.settled_emitted["BTC"]) <= 1
        assert len(_rows(rt, "verdict.consequence", handle=judge)) == 1
    finally:
        rt._ledger_lock.close()


@pytest.mark.gate
def test_permanently_stale_mark_retains_only_frozen_horizon_and_replays():
    from copy import deepcopy

    from factorylab.runtime.resume import JournalProxy, RecoveryJournal

    interval = 60_000_000_000

    class Venue(FakeExchange):
        settled_funding = True
        funding_interval_ns = interval

        def mids(self):
            return {}

        def settled_funding_history(self, coin, start, end):
            return []

    exchange = Venue(seed=1, coins=('BTC',), funding_interval_ns=interval)
    base = load_manifest('scripted')
    manifest = replace(base, exchange=replace(base.exchange, kind='hyperliquid', coins=('BTC',)),
                       timing=replace(base.timing, world_repricing_ns=270_000_000_000))
    rt = Runtime(manifest, events=0, seed=1, initial_balance_micro=None,
                 ledger_path=None, router_gamma=0.1, exchange=exchange,
                 clock_source=ClockSource(0, 1_000_000_000, 1).events())
    try:
        rt._observe_mid('BTC', 0, '100')
        rt.venue.settled_launch_ns = 0
        rt._freeze_named('probe', {'coin': 'BTC', 'side': 'buy'}, (('BTC', '100'),),
                         declined=None, attempted=None)
        frozen = rt.reference_mids.pop('probe')
        assert frozen['open_ns'] == 0
        assert frozen['due_ns'] == rt._horizon_ns()
        assert rt.venue.funding_needed('BTC', frozen['due_ns'])
        assert not rt.venue.funding_needed('BTC', frozen['due_ns'] + 1)
        assert not rt.venue.funding_needed('BTC', 0)
        before = runtime_state(rt)
        prefix = len(rt.ledger.ledger._recovery_items())
        rt.ledger.active = True

        def drive():
            states = []
            for step in range(1, 12):
                rt.clock.now_ns = step * interval
                rt.venue._settled_rates(rt.clock.now_ns, {'BTC'})
                # One new frontier boundary may await next poll's pruning; the
                # predicate itself never retains it beyond the frozen horizon.
                rt.venue._settled_rates(rt.clock.now_ns, {'BTC'})
                gaps = rt.venue.settled_gaps['BTC']
                assert all(0 < boundary <= frozen['due_ns'] for boundary in gaps)
                if rt._funding_patience_over(frozen):
                    assert not gaps
                states.append(deepcopy((rt.venue.settled_gaps, rt.venue.settled_emitted)))
            return states

        expected = drive()
        rows = rt.ledger.ledger._recovery_items()[prefix:]
        replay = RecoveryJournal(rt.ledger.ledger, lambda: rt.clock.now_ns)
        replay.io_store = rt.ledger.io_store
        replay.active = True
        replay.tail = iter(rows)
        restore_runtime(rt, before)
        def forbidden(*args, **kwargs):
            raise AssertionError('replay called venue')
        exchange.settled_funding_history = forbidden
        rt.venue.exchange = JournalProxy(exchange, replay, 'exchange')
        rt.venue.ledger = replay
        assert drive() == expected
        assert replay.peek() is None
    finally:
        rt._ledger_lock.close()


@pytest.mark.gate
def test_settled_launch_fingerprints_and_gaps_survive_checkpoint():
    class Venue(FakeExchange):
        settled_funding = True
        funding_interval_ns = NS_PER_HOUR

        def settled_funding_history(self, coin, start, end):
            return [row for row in self.published if start <= row.ts_ns <= end]

    exchange = Venue(seed=1, coins=("BTC",))
    exchange.published = []
    base = load_manifest("scripted")
    manifest = replace(base, exchange=replace(base.exchange, kind="hyperliquid", coins=("BTC",)))
    runtime = Runtime(manifest, events=1, seed=1, initial_balance_micro=None,
                      ledger_path=None, router_gamma=0.1, exchange=exchange,
                      clock_source=ClockSource(NS_PER_HOUR // 2, 1_000_000_000, 1).events())
    restored = Runtime(manifest, events=1, seed=1, initial_balance_micro=None,
                       ledger_path=None, router_gamma=0.1, exchange=exchange,
                       clock_source=ClockSource(3 * NS_PER_HOUR, 1_000_000_000, 1).events())
    try:
        restore_runtime(restored, runtime_state(runtime))
        assert restored.venue.settled_launch_ns == NS_PER_HOUR // 2
        restored.venue.funding_needed = lambda coin, stamp: stamp == NS_PER_HOUR
        exchange.published = [FundingEvent("BTC", Decimal("0.002"), None, 2 * NS_PER_HOUR)]
        assert len(restored.venue._settled_rates(3 * NS_PER_HOUR, {"BTC"})) == 1
        saved = runtime_state(restored)
        restore_runtime(runtime, saved)
        runtime.venue.funding_needed = lambda coin, stamp: stamp == NS_PER_HOUR
        assert runtime.venue.settled_gaps["BTC"] == {NS_PER_HOUR}
        assert runtime.venue._settled_rates(3 * NS_PER_HOUR, {"BTC"}) == []
        exchange.published.insert(0, FundingEvent("BTC", Decimal("0.001"), None, NS_PER_HOUR))
        events = runtime.venue._settled_rates(4 * NS_PER_HOUR, {"BTC"})
        assert [event.payload["funding_ns"] for event in events] == [NS_PER_HOUR]
    finally:
        runtime._ledger_lock.close()
        restored._ledger_lock.close()
