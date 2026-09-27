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
