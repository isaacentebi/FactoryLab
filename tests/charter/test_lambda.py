import random
from dataclasses import replace

import pytest

from factorylab.charter.amendment import Amendment, proposed_price
from factorylab.charter.book import CharterBook
from factorylab.charter.charter import seed_charter
from factorylab.charter.controller import CardRegion, PriceController
from factorylab.kernel.ledger import Ledger
from factorylab.kernel.timing import TimingRegistry
from factorylab.runtime.loop import Runtime
from factorylab.runtime.worlds import load_manifest


def runtime():
    return Runtime(load_manifest("scripted"), events=1, seed=1, initial_balance_micro=None,
                   ledger_path=None, drip=True, router_gamma=0.1)


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


@pytest.mark.parametrize("value", [True, False, None, "0.5", -0.1, 1.01,
                                   float("nan"), float("inf"), 10**1000])
def test_invalid_lambda_rejected_before_registration_with_public_reason(value):
    rt = runtime()
    before = rt.registry.available("tool")
    card = {**vars(rt.charter.cards[1]), "lambda": value}
    with pytest.raises(ValueError, match=r"lambda.*\[0, 1.0\]"):
        rt._propose_amendment("decision-1", {"id": "invalid-price", "replace": [card]})
    assert rt.charter_book.pending() == []
    assert rt.registry.available("tool") == before
    assert "lambda" in rt._world_block()["amendment_feedback"]["reason"]


@pytest.mark.parametrize("value", [0, 0.5, 1])
def test_inclusive_prices(value):
    assert proposed_price(value, 1) == value


def test_proposals_are_frozen_and_activation_identifies_exact_prices():
    prices = [["well_formed_rate", 0.7]]
    am = amendment(proposed_prices=prices)
    prices[0][1] = 0
    assert am.proposed_prices == (("well_formed_rate", 0.7),)
    book = CharterBook(Ledger(), seed_charter())
    book.propose(am)
    committee = book.seat(am.id, {"one": "producer"}, random.Random(1))
    book.vote(committee, "seat-1", True, "yes")
    assert book.activate_due(1).edition == 2
    assert book.activated_amendment(2) == am
    with pytest.raises(KeyError):
        book.activated_amendment(1)
    for values in ((("unknown", 0.5),), (("well_formed_rate", 0.5),) * 2):
        with pytest.raises(ValueError):
            amendment(proposed_prices=values)


def test_controller_ledger_first_bounds_history_and_removal(monkeypatch):
    ledger = Ledger()
    timing = TimingRegistry()
    controller = PriceController(ledger, eta=0.5, decay=0.1, lambda_max=1,
                                 min_window_events=3, timing=timing)
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
        **before["cards"]["card"], "lambda": 0.8, "effective_lambda": 0.8,
    }
    assert timing.closure_count("price:card") == 1
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


def test_activation_preserves_unpriced_changes_removes_and_readds_cards():
    rt = runtime()
    rt._derive_regions()
    rt.controller.set_price("well_formed_rate", 0.7, amendment_id="initial")
    pass_amendment(rt, amendment())
    activate_after_backstop(rt)
    assert rt.controller.price("well_formed_rate") == 0.7
    pass_amendment(rt, amendment(id="remove-card", edition_base=2, replace=(),
                                remove=("well_formed_rate",)))
    activate_after_backstop(rt)
    assert "well_formed_rate" not in rt.regions
    assert "well_formed_rate" not in rt.priced
    assert "well_formed_rate" not in rt.controller.snapshot()["cards"]
    pass_amendment(rt, amendment(id="restore-card", edition_base=3, replace=(),
                                add=(seed_charter().cards[1],),
                                proposed_prices=(("well_formed_rate", 0.4),)))
    activate_after_backstop(rt)
    assert rt.controller.price("well_formed_rate") == 0.4
    assert "well_formed_rate" in rt.regions


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


def test_added_unreadable_card_is_refused_before_prices_change():
    rt = runtime()
    card = replace(rt.charter.cards[1], id="new-card", acceptable_region="use judgment")
    with pytest.raises(ValueError, match="bounds"):
        pass_amendment(rt, amendment(replace=(), add=(card,), proposed_prices=((card.id, 0.6),)))
    assert rt.charter.edition == 1
    assert rt.controller.price(card.id) == 0
