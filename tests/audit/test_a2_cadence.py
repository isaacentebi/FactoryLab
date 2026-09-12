"""A2: slow unfinished feedback still constrains the governance clock."""

from factorylab.charter.amendment import Amendment
from factorylab.kernel.ledger import Ledger
from factorylab.runtime.cadence import GovernanceCadence
from factorylab.runtime.loop import Runtime
from factorylab.runtime.worlds import load_manifest


def test_a2_zero_timestamp_reproduction_retains_backstop():
    gate = GovernanceCadence(Ledger(), sample=200, min_ratio=3, backstop=200)
    for index in range(200):
        gate.record(handle=f"f{index}", predicate_id="return_paid_off",
                    opened_event=index, settled_event=index + 10,
                    opened_ns=0, settled_ns=0, status="settled")
    assert gate.slowest_period_events() >= 200
    assert gate.slowest_period_ns(60_000_000_000) >= 200 * 60_000_000_000
    assert gate.world_block(60_000_000_000)["slowest_period"] != "0s"


def test_a2_oldest_outstanding_forecast_counts_before_it_settles():
    gate = GovernanceCadence(Ledger(), sample=200, min_ratio=3, backstop=10)
    gate.record_open("slow", 7)
    gate.advance(157)
    assert gate.slowest_period_events() >= 150
    assert gate.world_block(1)["outstanding_forecasts"] == 1
    gate.record(handle="slow", predicate_id="return_paid_off", opened_event=7,
                settled_event=157, opened_ns=0, settled_ns=0, status="settled")
    assert gate.world_block(1)["outstanding_forecasts"] == 0
    assert gate.slowest_period_events() == 10  # still fewer than min_support samples


def test_a2_insufficient_support_and_nonzero_measured_p90():
    gate = GovernanceCadence(Ledger(), sample=30, min_ratio=3, backstop=10, min_support=30)
    for i in range(29):
        gate.record(handle=str(i), predicate_id="return_paid_off", opened_event=0,
                    settled_event=40, opened_ns=0, settled_ns=0, status="settled")
    assert gate.slowest_period_events() == 10
    gate.record(handle="last", predicate_id="return_paid_off", opened_event=0,
                settled_event=40, opened_ns=0, settled_ns=0, status="settled")
    assert gate.slowest_period_ns(3) == 120


def test_a2_two_approved_amendments_cannot_activate_at_one_boundary():
    rt = Runtime(load_manifest("scripted"), events=0, seed=1, initial_balance_micro=None,
                 ledger_path=None, drip=False, router_gamma=0.1)
    rt._manage_reserve_window()
    for name, interval in (("first", "2s"), ("second", "3s")):
        amendment = Amendment(name, "author", 1, (), (), (),
                              predicted_effect={"card_id": rt.charter.cards[0].id,
                                                "direction": "decrease", "window": 1},
                              tick_interval=interval)
        rt.charter_book.propose(amendment)
        committee = rt.charter_book.seat(name, {"a": "producer", "b": "evaluator"}, rt.rng)
        for seat in committee.seats:
            rt.charter_book.vote(committee, seat.alias, True, "Measured proposal.")
        rt.cadence.approve(name)
    rt.clock.now_ns = rt.cadence.earliest_ns(rt.tick_clock.interval_ns)
    rt.n = rt.cadence.earliest_event()
    rt.cadence.advance(rt.n)
    rt._activate_charter_if_due()
    assert rt.charter.edition == 2
    assert rt.cadence.world_block(rt.tick_clock.interval_ns)["waiting"] == ["second"]
    assert not rt.cadence.ready(now_ns=rt.clock.now_ns,
                                tick_interval_ns=rt.tick_clock.interval_ns, window=1)
