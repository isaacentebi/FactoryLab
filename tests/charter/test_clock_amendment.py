import random

import pytest

from factorylab.charter.amendment import Amendment, proposed_tick_interval
from factorylab.runtime.loop import Runtime
from factorylab.runtime.worlds import load_manifest


def runtime():
    return Runtime(load_manifest("scripted"), events=1, seed=1, initial_balance_micro=None,
                   ledger_path=None, router_gamma=0.1)


@pytest.mark.parametrize("value", ["0.5s", "41s", "bad", "", None, True, 10, "0.0000000001s"])
def test_rejected_tick_has_public_reason_and_no_proposal(value, monkeypatch):
    rt = runtime()
    entries = []
    append = rt.ledger.append

    def capture(entry):
        entries.append(dict(entry))
        return append(entry)

    monkeypatch.setattr(rt.ledger, "append", capture)
    with pytest.raises(ValueError, match="tick_interval must be a duration string within"):
        rt._propose_amendment("decision-1", {"id": "bad-clock", "tick_interval": value})
    feedback = rt._world_block()["amendment_feedback"]
    assert entries[-1] == {"kind": "amendment.rejected", **feedback}
    assert rt.charter_book.pending() == []
    assert rt.stats.amendments_proposed == 0


def test_clock_duration_exact_and_inclusive():
    for value, expected in [("10s", 10**10), ("20m", 1200 * 10**9), ("10.000000001s", 10**10 + 1)]:
        assert proposed_tick_interval(value, 10**10, 1200 * 10**9) == expected


def test_activation_ledgers_before_clock_mutation(monkeypatch):
    rt = runtime()
    am = Amendment("new-clock", "decision-1", 1, (), (), (),
                   {"card_id": "cost_per_return", "direction": "decrease",
                    "window": 1},
                    tick_interval="2s")
    rt.charter_book.propose(am)
    rt._activate_charter_if_due()
    assert rt.tick_clock.interval_ns == 10**9
    committee = rt.charter_book.seat(am.id, {"one": "producer"}, random.Random(1))
    rt.charter_book.vote(committee, "seat-1", True, "yes")
    assert rt.charter_book.tally(committee) == "passed"
    rt.cadence.approve(am.id)
    # No settlements exist yet, so approval waits for the full backstop cadence.
    threshold = (rt.m.timing.min_ratio * rt.m.evaluation.consequence_backstop_events
                 * rt.tick_clock.interval_ns)
    rt.clock.now_ns = threshold - 1
    rt._activate_charter_if_due()
    assert rt.charter.edition == 1
    assert rt.charter_book.pending() == [am]
    assert rt.tick_clock.interval_ns == 10**9
    assert rt.stats.clock_changes == 0
    rt.clock.now_ns = threshold
    rt.n = rt.cadence.earliest_event()
    rt.cadence.advance(rt.n)
    append = rt.ledger.append
    changes = []

    def capture(entry):
        if entry["kind"] == "clock.changed":
            assert rt.tick_clock.interval_ns == 10**9
            assert rt.stats.clock_changes == 0
            changes.append(dict(entry))
        return append(entry)

    monkeypatch.setattr(rt.ledger, "append", capture)
    rt._activate_charter_if_due()
    assert changes == [{"kind": "clock.changed", "edition": 2, "old_ns": 10**9, "new_ns": 2*10**9}]
    assert rt.charter_book.activated_amendment(2).tick_interval == "2s"
    assert rt.tick_clock.interval_ns == 2 * 10**9
    assert rt.stats.clock_changes == 1
    assert rt._world_block()["clock"] == {
        "tick_interval": "2s", "min_tick": "1s", "max_tick": "40s",
    }


def test_clock_unchanged_when_ledger_write_fails(monkeypatch):
    rt = runtime()
    am = Amendment("new-clock", "decision-1", 1, (), (), (),
                   {"card_id": "cost_per_return", "direction": "decrease",
                    "window": 1},
                    tick_interval="2s")
    rt.charter_book.propose(am)
    committee = rt.charter_book.seat(am.id, {"one": "producer"}, random.Random(1))
    rt.charter_book.vote(committee, "seat-1", True, "yes")
    assert rt.charter_book.tally(committee) == "passed"
    rt.cadence.approve(am.id)
    # No settlements exist yet, so approval waits for the full backstop cadence.
    threshold = (rt.m.timing.min_ratio * rt.m.evaluation.consequence_backstop_events
                 * rt.tick_clock.interval_ns)
    rt.clock.now_ns = threshold - 1
    rt._activate_charter_if_due()
    assert rt.charter.edition == 1
    assert rt.charter_book.pending() == [am]
    assert rt.tick_clock.interval_ns == 10**9
    assert rt.stats.clock_changes == 0
    rt.clock.now_ns = threshold
    rt.n = rt.cadence.earliest_event()
    rt.cadence.advance(rt.n)
    append = rt.ledger.append

    def fail(entry):
        if entry["kind"] == "clock.changed":
            raise RuntimeError("ledger unavailable")
        return append(entry)

    monkeypatch.setattr(rt.ledger, "append", fail)
    with pytest.raises(RuntimeError, match="ledger unavailable"):
        rt._activate_charter_if_due()
    assert rt.tick_clock.interval_ns == 10**9
    assert rt.stats.clock_changes == 0
