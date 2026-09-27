"""Event predicates: claims about Polymarket markets that the world settles.

A forecast on ``event_pays`` or ``event_price_above`` is graded by a fact the world
measures at settlement, a market's resolution or its midpoint, never by another
model's reading (essay II.III.b). The vocabulary is offered only where the world
enables ``[polymarket]``, and every parameter is checked before a claim is sealed.
"""

import pytest

from factorylab.settlement import SEED_VOCABULARY, Forecast, Observer, WindowFacts
from factorylab.settlement.vocabulary import (
    EVENT_PREDICATE_IDS,
    EVENT_VOCABULARY,
    PredicateBook,
    _validate_params,
    validate_predicate_definition,
)

YES = "100000000000000000000"
PAYS = {"horizon_events": 5, "token_id": YES}
ABOVE = {"horizon_events": 5, "token_id": YES, "level": 0.5}


def facts(**event):
    return WindowFacts(10, 10, 10, (), event={"listed": True, "closed": False,
                                              "payout": None, "midpoint": None, **event})


def observer():
    return Observer(PredicateBook(world=EVENT_VOCABULARY))


def test_the_vocabulary_is_the_worlds_and_absent_where_the_world_offers_no_market():
    plain, world = PredicateBook(), PredicateBook(world=EVENT_VOCABULARY)
    assert [p.id for p in plain.all()] == [p.id for p in SEED_VOCABULARY]
    assert [p.id for p in world.all()][len(SEED_VOCABULARY):] == [
        "event_pays", "event_price_above"]
    assert plain.get("event_pays") is None and world.get("event_pays") is EVENT_VOCABULARY[0]
    assert {row["id"] for row in world.catalogue()} >= EVENT_PREDICATE_IDS
    assert not EVENT_PREDICATE_IDS & {p.id for p in SEED_VOCABULARY}
    # Only the kernel's own event definitions may be offered as a world's.
    with pytest.raises(ValueError, match="event vocabulary"):
        PredicateBook(world=SEED_VOCABULARY[:1])


def test_a_world_without_the_market_cannot_resolve_or_validate_an_event_claim():
    with pytest.raises(ValueError, match="unknown predicate"):
        Observer().observe("event_pays", PAYS, facts(payout="1"))
    with pytest.raises(ValueError, match="unknown predicate"):
        _validate_params("event_pays", PAYS, predicate=PredicateBook().get("event_pays"))
    # The kernel seals only what it admitted, so a sealed record needs no book.
    Forecast("f", "judge", "about", "event_pays", PAYS, 0.5, 1, 6)


@pytest.mark.parametrize(("pid", "params", "reason"), [
    ("event_pays", {"horizon_events": 5}, "exactly the predicate's declared"),
    ("event_pays", {**PAYS, "level": 0.5}, "exactly the predicate's declared"),
    ("event_price_above", PAYS, "exactly the predicate's declared"),
    ("event_pays", {**PAYS, "token_id": "BTC"}, "decimal digits"),
    ("event_pays", {**PAYS, "token_id": 100}, "decimal digits"),
    ("event_pays", {**PAYS, "token_id": "1" * 101}, "decimal digits"),
    ("event_pays", {**PAYS, "horizon_events": 0}, "positive"),
    ("event_price_above", {**ABOVE, "level": 0}, "strictly between"),
    ("event_price_above", {**ABOVE, "level": 1}, "strictly between"),
    ("event_price_above", {**ABOVE, "level": 1.5}, "in \\[0, 1\\]"),
    ("event_price_above", {**ABOVE, "level": "0.5"}, "in \\[0, 1\\]"),
])
def test_malformed_event_claims_are_refused(pid, params, reason):
    book = PredicateBook(world=EVENT_VOCABULARY)
    with pytest.raises(ValueError, match=reason):
        _validate_params(pid, params, predicate=book.get(pid))
    with pytest.raises(ValueError, match=reason):
        Forecast("f", "judge", "about", pid, params, 0.5, 1, 6)


def test_event_ids_cannot_be_redefined_by_the_population():
    for pid in EVENT_PREDICATE_IDS:
        with pytest.raises(ValueError, match="cannot be redefined"):
            validate_predicate_definition(pid, "mine", "def resolve(facts):\n    return True\n")


def test_event_pays_is_one_only_for_a_resolved_winning_token():
    obs = observer()
    assert obs.observe("event_pays", PAYS, facts(closed=True, payout="1")) == 1
    assert obs.observe("event_pays", PAYS, facts(closed=True, payout="0")) == 0
    # A 50-50 answer pays half a token, and half is not one.
    assert obs.observe("event_pays", PAYS, facts(closed=True, payout="0.5")) == 0
    # Not resolved by settlement: open, or closed with no final resolution yet.
    assert obs.observe("event_pays", PAYS, facts(midpoint="0.99")) == 0
    assert obs.observe("event_pays", PAYS, facts(closed=True)) == 0


def test_event_price_above_reads_the_redemption_once_resolved_and_the_midpoint_before():
    obs = observer()
    assert obs.observe("event_price_above", ABOVE, facts(midpoint="0.51")) == 1
    # Exact, not float: a midpoint at the level does not exceed it.
    assert obs.observe("event_price_above", ABOVE, facts(midpoint="0.5")) == 0
    assert obs.observe("event_price_above", {**ABOVE, "level": 0.3},
                       facts(midpoint="0.30000000000000001")) == 1
    assert obs.observe("event_price_above", ABOVE,
                       facts(closed=True, payout="1", midpoint="0.2")) == 1
    assert obs.observe("event_price_above", ABOVE, facts(closed=True, payout="0")) == 0


def test_no_read_or_an_unlisted_token_supplies_no_outcome():
    obs = observer()
    assert obs.observe("event_pays", PAYS, WindowFacts(10, 10, 10, ())) is None
    unlisted = WindowFacts(10, 10, 10, (), event={"listed": False})
    assert obs.observe("event_pays", PAYS, unlisted) is None
    assert obs.observe("event_price_above", ABOVE, facts()) is None
    with pytest.raises(ValueError, match="whether the token is listed"):
        WindowFacts(10, 10, 10, (), event={"payout": "1"})
