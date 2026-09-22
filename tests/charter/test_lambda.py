import random
from dataclasses import replace

import pytest

from factorylab.charter.amendment import Amendment
from factorylab.charter.controller import CardRegion, PriceController
from factorylab.kernel.ledger import Ledger
from factorylab.runtime.loop import Runtime
from factorylab.runtime.worlds import load_manifest
from tests.seed_charter import seed_charter


def runtime():
    return Runtime(load_manifest("scripted"), events=1, seed=1, initial_balance_micro=None,
                   ledger_path=None, router_gamma=0.1)


def amendment(**changes):
    return replace(Amendment("priced-card", "decision-1", 1, (),
                             (replace(seed_charter().cards[1], acceptable_region="above 0.8"),), (),
                   {"card_id": "cost_per_return", "direction": "decrease",
                    "window": 1}),
                   **changes)


def pass_amendment(rt, am):
    rt.charter_book.propose(am)
    committee = rt.charter_book.seat(am.id, {"one": "producer"}, random.Random(1))
    rt.charter_book.vote(committee, "seat-1", True, "yes")
    assert rt.charter_book.tally(committee) == "passed"
    rt.cadence.approve(am.id)


def activate_after_backstop(rt):
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
    assert rt.charter.edition == edition + 1


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
        pass_amendment(rt, amendment(replace=(card,), proposed_prices=((card.id, 0.8),)))
    assert rt.charter.edition == 1
    assert rt.controller.price(card.id) == 0


def test_prices_wait_for_approval_and_later_passed_proposals_win():
    rt = runtime()
    rt._derive_regions()
    first = amendment(proposed_prices=(("well_formed_rate", 0.3),))
    rt.charter_book.propose(first)
    rt._activate_charter_if_due()
    assert rt.controller.price("well_formed_rate") == 0
    second = amendment(id="second-price", proposed_prices=(("well_formed_rate", 0.9),))
    pass_amendment(rt, second)
    committee = rt.charter_book.seat(first.id, {"one": "producer"}, random.Random(1))
    rt.charter_book.vote(committee, "seat-1", True, "yes")
    assert rt.charter_book.tally(committee) == "passed"
    rt.cadence.approve(first.id)
    activate_after_backstop(rt)
    assert rt.charter.edition == 2
    assert rt.controller.price("well_formed_rate") == 0.3
    assert rt.charter_book.pending() == [second]
    activate_after_backstop(rt)
    assert rt.charter.edition == 3
    assert rt.controller.price("well_formed_rate") == 0.9
