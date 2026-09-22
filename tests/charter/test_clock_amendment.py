import pytest

from factorylab.charter.amendment import Amendment, proposed_tick_interval
from factorylab.runtime.loop import Runtime
from factorylab.runtime.worlds import load_manifest

BURN = {"observation": "burn_per_window", "direction": "decrease", "window": 1}


def runtime():
    return Runtime(load_manifest("scripted"), events=1, seed=1, initial_balance_micro=None,
                   ledger_path=None, router_gamma=0.1)


def seated_yes(rt, monkeypatch):
    """Three eligible seats, and a committee whose every voter says yes."""
    seats = sorted(rt.assemblies)[:3]
    monkeypatch.setattr(rt, "_committee_eligible",
                        lambda: {a: rt.assemblies[a].spec.role for a in seats})

    def yes(am, committee, **_):
        for alias in rt.charter_book.voters(committee, am.id):
            rt.charter_book.vote(committee, am.id, alias, True, "yes")
        rt.cadence.approve(am.id)

    monkeypatch.setattr(rt, "_hold_vote", yes)


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


def _at_threshold(rt):
    """Move the world to the first governance boundary the backstop cadence allows."""
    threshold = (rt.m.timing.min_ratio * rt.m.evaluation.consequence_backstop_events
                 * rt.tick_clock.interval_ns)
    rt.clock.now_ns = threshold - 1
    rt._activate_charter_if_due()
    assert rt.charter.edition == 1
    assert rt.tick_clock.interval_ns == 10**9
    assert rt.stats.clock_changes == 0
    rt.clock.now_ns = threshold
    rt.n = rt.cadence.earliest_event()
    rt.cadence.advance(rt.n)


def test_activation_ledgers_before_clock_mutation(monkeypatch):
    rt = runtime()
    seated_yes(rt, monkeypatch)
    am = Amendment("new-clock", "decision-1", 1, (), (), (), BURN, tick_interval="2s")
    rt.charter_book.propose(am)
    _at_threshold(rt)
    assert rt.charter_book.pending() == [am]
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
    seated_yes(rt, monkeypatch)
    am = Amendment("new-clock", "decision-1", 1, (), (), (), BURN, tick_interval="2s")
    rt.charter_book.propose(am)
    _at_threshold(rt)
    assert rt.charter_book.pending() == [am]
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


# --- charter audit M6 and P3: speed is cash burn, one change per motion ----------------


def test_a_clock_motion_must_predict_its_effect_on_a_burn_observation():
    rt = runtime()
    for effect in ({"card_id": "cost_per_return", "direction": "decrease", "window": 1},
                   {"observation": "well_formed_rate", "direction": "decrease", "window": 1},
                   None):
        with pytest.raises(ValueError, match="burn observation"):
            rt._propose_amendment("decision-1", {"id": "faster", "tick_interval": "2s",
                                                 "predicted_effect": effect})
    assert rt.charter_book.pending() == []
    with pytest.raises(ValueError, match="clock motion predicts its effect on an observation"):
        Amendment("x-clock", "d", 1, (), (), (),
                  {"card_id": "cost_per_return", "direction": "decrease", "window": 1},
                  tick_interval="2s")


def test_a_bundled_motion_is_refused_before_any_trial(monkeypatch):
    rt = runtime()
    card = rt.charter.cards[0]
    entries = []
    append = rt.ledger.append
    monkeypatch.setattr(rt.ledger, "append", lambda e: (entries.append(dict(e)), append(e))[1])
    bundles = [
        {"remove": [card.id], "tick_interval": "2s"},
        {"remove": [card.id], "lambda": {card.id: 0.2}},
        {"lambda": {card.id: 0.2}, "tick_interval": "2s"},
    ]
    for bundle in bundles:
        with pytest.raises(ValueError, match="one change class"):
            rt._propose_amendment("decision-1", {
                "id": "bundled", **bundle, "predicted_effect": BURN})
    assert [e["kind"] for e in entries] == ["amendment.rejected"] * 3
    assert "cards and clock" in entries[0]["reason"]
    # A card that carries its own lambda is a bundle too.
    with pytest.raises(ValueError, match="a card carries no lambda"):
        rt._propose_amendment("decision-1", {
            "id": "priced-add", "add": [{"lambda": 0.3}],
            "predicted_effect": {"card_id": card.id, "direction": "decrease", "window": 1}})
    with pytest.raises(ValueError, match="one change class"):
        Amendment("bundled", "d", 1, (), (), (card.id,),
                  {"card_id": card.id, "direction": "decrease", "window": 1},
                  proposed_prices=((card.id, 0.2),))
    assert rt.charter_book.pending() == [] and rt.stats.amendments_proposed == 0


def test_a_clock_ballot_is_graded_on_the_burn_observation(monkeypatch):
    """The clock motion's promise is frozen on burn_per_window and graded like a card's."""
    import factorylab.runtime.governance as governance
    from factorylab.charter.amendment import PredictedEffect
    from factorylab.kernel.queue import PropensityRecord, SettleStatus

    rt = runtime()
    reading = {"value": 500.0}
    monkeypatch.setattr(governance, "measure_card",
                        lambda card, samples, observations=None: {"all": reading["value"]})
    am = Amendment("slower", "d", 1, (), (), (),
                   PredictedEffect(None, "decrease", 1, "burn_per_window"), tick_interval="2s")
    handle = rt.queue.open(
        actor="assembly:eval-a", event_id="t", channel="policy", deadline_ns=10**18,
        parent_handle=None, cost_ceiling=0,
        propensity=PropensityRecord(("eval-a",), (1.0,), "eval-a", 0, "assembly:eval-a", "x"))
    rt._record_policy_ballot(am, handle, "eval-a", True)
    ballot = rt.pending_votes[-1]
    assert ballot["observation_id"] == "burn_per_window"
    assert ballot["card"].window.kind == "windows" and ballot["card"].window.n == 1
    rt._activate_policy_ballots(am.id)
    reading["value"] = 300_000.0  # burn rose by 0.3 of the observation's range
    rt._close_policy_window(rt.window.index)
    outcome = [i for i in rt.ledger._recovery_items() if i["kind"] == "policy.outcome"][-1]
    assert outcome["observation_id"] == "burn_per_window"
    assert outcome["y"] is False and outcome["score"] == 0.0
    assert rt.queue.history(handle)[-1].status is SettleStatus.SETTLED


def test_burn_per_window_is_the_windows_compute_spend():
    from factorylab.runtime.observations import observation_for
    from factorylab.runtime.pricing import MeasureWindow

    assert observation_for("burn_per_window").measure(
        MeasureWindow(1, 100, compute_spend_micro=1234)) == 1234.0
    assert observation_for("burn_per_window").measure(MeasureWindow(1, 100)) == 0.0
