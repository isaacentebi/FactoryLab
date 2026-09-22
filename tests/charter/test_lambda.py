from dataclasses import replace

import pytest

from factorylab.charter.amendment import Amendment
from factorylab.charter.controller import CardRegion, PriceController
from factorylab.kernel.ledger import Ledger
from factorylab.runtime.loop import Runtime
from factorylab.runtime.worlds import load_manifest


def runtime():
    return Runtime(load_manifest("scripted"), events=1, seed=1, initial_balance_micro=None,
                   ledger_path=None, router_gamma=0.1)


def amendment(**changes):
    """A lambda motion: one change class, prices for current cards only (charter audit P3)."""
    return replace(Amendment("priced-card", "decision-1", 1, (), (), (),
                   {"card_id": "cost_per_return", "direction": "decrease",
                    "window": 1}, proposed_prices=(("well_formed_rate", 0.8),)),
                   **changes)


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


def activate_after_backstop(rt, activated=1):
    edition = rt.charter.edition
    # No settlement samples exist: the current backstop is the period.
    delay = (rt.m.timing.min_ratio * rt.m.evaluation.consequence_backstop_events
             * rt.tick_clock.interval_ns)
    rt.clock.now_ns += delay - 1
    rt._activate_charter_if_due()
    assert rt.charter.edition == edition
    rt.clock.now_ns += 1
    rt.n = rt.cadence.earliest_event()
    rt.cadence.advance(rt.n)
    rt._activate_charter_if_due()
    assert rt.charter.edition == edition + activated


def test_controller_ledger_first_bounds_history_and_removal(monkeypatch):
    ledger = Ledger()
    controller = PriceController(ledger, eta=0.5, decay=0.1, lambda_max=1,
                                 min_window_events=3)
    controller.register(CardRegion("card", "max", None, 1, 1))
    controller.observe("card", 2, 1)
    before = controller.snapshot()
    append = ledger.append
    entries = []

    def capture(entry):
        assert controller.snapshot() == before
        entries.append(entry)
        return append(entry)

    with monkeypatch.context() as patch:
        patch.setattr(ledger, "append", capture)
        controller.set_price("card", 0.8, amendment_id="adopted")
    assert entries[0] == {"kind": "price.proposed", "card_id": "card",
                          "amendment_id": "adopted", "lambda_before": 0.5, "lambda_after": 0.8}
    assert controller.snapshot()["cards"]["card"] == {
        # An adopted price becomes the card's accumulated pressure (bumpless for the PID).
        **before["cards"]["card"], "lambda": 0.8, "integral": 0.8,
    }
    for value in (True, -1, 2, float("nan")):
        with pytest.raises(ValueError):
            controller.set_price("card", value, amendment_id="invalid")
    with pytest.raises(KeyError):
        controller.set_price("missing", 0.5, amendment_id="invalid")
    controller.observe("card", 0, 4)
    assert controller.price("card") == pytest.approx(0.7)
    before = controller.snapshot()

    def fail(entry):
        raise RuntimeError("ledger unavailable")

    with monkeypatch.context() as patch:
        patch.setattr(ledger, "append", fail)
        with pytest.raises(RuntimeError):
            controller.set_price("card", 0, amendment_id="failed")
        with pytest.raises(RuntimeError):
            controller.remove("card", amendment_id="failed")
    assert controller.snapshot() == before
    controller.remove("card", amendment_id="removed")
    assert "card" not in controller.snapshot()["cards"]
    assert controller.price("card") == 0
    with pytest.raises(KeyError):
        controller.violation("card", 2)
    controller.register_pending("card")
    controller.update_region(CardRegion("card", "max", None, 1, 1))
    controller.observe("card", 2, 7)
    assert controller.price("card") == 0.5


def test_unreadable_bounds_are_refused_before_prices_change():
    rt = runtime()
    card = replace(rt.charter.cards[1], acceptable_region="use judgment")
    with pytest.raises(ValueError, match="bounds"):
        rt.charter_book.propose(amendment(replace=(card,), proposed_prices=()))
    assert rt.charter.edition == 1
    assert rt.controller.price(card.id) == 0


def test_lambda_names_only_a_current_card():
    rt = runtime()
    with pytest.raises(ValueError, match="lambda names card no-such-card"):
        rt.charter_book.propose(amendment(proposed_prices=(("no-such-card", 0.4),)))


def test_prices_wait_for_the_boundary_and_later_proposals_win(monkeypatch):
    rt = runtime()
    seated_yes(rt, monkeypatch)
    rt._derive_regions()
    first = amendment(proposed_prices=(("well_formed_rate", 0.3),))
    rt.charter_book.propose(first)
    rt._activate_charter_if_due()
    assert rt.controller.price("well_formed_rate") == 0
    second = amendment(id="second-price", proposed_prices=(("well_formed_rate", 0.9),))
    rt.charter_book.propose(second)
    # One boundary: its committee passes both, and each activates as its own edition,
    # in proposal order, so the later proposal's price stands.
    activate_after_backstop(rt, activated=2)
    assert rt.charter.edition == 3
    assert rt.charter_book.activated_amendment(2).id == first.id
    assert rt.controller.price("well_formed_rate") == 0.9
    assert rt.charter_book.pending() == []
