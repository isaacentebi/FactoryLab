import random
from collections import Counter
from dataclasses import FrozenInstanceError, replace

import pytest

from factorylab.charter.amendment import Amendment
from factorylab.charter.book import CharterBook
from factorylab.charter.charter import Charter, seed_charter
from factorylab.charter.committee import Ballot, Committee, Seat, draw
from factorylab.kernel.events import Bus
from factorylab.kernel.ledger import Ledger
from factorylab.kernel.termination import Termination


def candidate(**changes) -> Amendment:
    return replace(
        Amendment(
            id="better-cost",
            proposer_handle="decision-1",
            edition_base=1,
            add=(),
            replace=(replace(seed_charter().cards[0], acceptable_region="below 100 micro-USD"),),
            remove=(),
            predicted_effect="Lower cost per useful return, with potentially less exploration.",
        ),
        **changes,
    )


def eligible(size: int = 5) -> dict[str, str]:
    roles = ("producer", "evaluator", "meta")
    return {f"assembly-{index}": roles[index % 3] for index in range(size)}


@pytest.fixture
def ledger() -> Ledger:
    return Ledger(clock_ns=lambda: 10)


@pytest.fixture
def book(ledger) -> CharterBook:
    return CharterBook(ledger, seed_charter())


def seated(book: CharterBook, size: int = 5, **changes) -> Committee:
    amendment = candidate(**changes)
    book.propose(amendment)
    return book.seat(amendment.id, eligible(size), random.Random(7))


def approve(book: CharterBook, committee: Committee) -> None:
    for seat in committee.seats[: len(committee.seats) // 2 + 1]:
        book.vote(committee, seat.alias, True, "Expected improvement is credible.")


def evidence(ledger: Ledger) -> list[dict]:
    termination = Termination(ledger=ledger, bus=Bus(ledger), clock_ns=lambda: 1000)
    termination.kill("test audit")
    items = []
    while True:
        item = ledger.decrypt_item(len(items))
        if item["kind"] == "event" and item["event"]["kind"] == "Terminated":
            break
        items.append(item)
    assert ledger.verify()
    return items


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("id", ""),
        ("id", "x"),
        ("id", "Wrong-case"),
        ("id", "has spaces"),
        ("id", "has_underscores"),
        ("id", "trailing-newline\n"),
        ("id", "x" * 49),
        ("id", None),
        ("proposer_handle", "  "),
        ("edition_base", 0),
        ("edition_base", -1),
        ("edition_base", 1.0),
        ("edition_base", True),
        ("edition_base", "1"),
        ("predicted_effect", ""),
        ("predicted_effect", " \n"),
        ("predicted_effect", "x" * 2001),
        ("predicted_effect", 1),
        ("add", ("a new norm",)),
        ("replace", ({"id": "cost_per_return"},)),
        ("remove", (seed_charter().cards[0],)),
        ("remove", ("",)),
        ("remove", (1,)),
        ("remove", "cost_per_return"),
        ("add", None),
    ],
)
def test_amendment_rejects_malformed_fields(field, value):
    with pytest.raises(ValueError):
        candidate(**{field: value})


def test_amendment_bounds_and_freezing():
    cards = [replace(seed_charter().cards[0], id="new-card")]
    amendment = candidate(id="a" * 48, add=cards, predicted_effect="x" * 2000)
    cards.clear()
    assert len(amendment.add) == 1
    assert isinstance(amendment.add, tuple)
    with pytest.raises(FrozenInstanceError):
        amendment.predicted_effect = "revised after seeing votes"
    with pytest.raises(FrozenInstanceError):
        amendment.add[0].description = "revised after seeing votes"


@pytest.mark.parametrize("field", ["norms", "add_norms", "remove_norms", "replace_norms"])
def test_norm_operations_are_not_part_of_an_amendment(field):
    with pytest.raises(TypeError):
        candidate(**{field: ("new norm",)})


@pytest.mark.parametrize("operation", ["add", "replace"])
def test_unknown_norms_rejected_at_proposal(book, ledger, operation):
    card = replace(seed_charter().cards[0], norm="maximize returns at any cost")
    if operation == "add":
        card = replace(card, id="new-card")
    with pytest.raises(ValueError, match="norms are read-only"):
        book.propose(candidate(**{operation: (card,)}))
    assert book.pending() == []
    assert evidence(ledger) == []


def test_norm_removal_rejected_at_proposal(book):
    with pytest.raises(ValueError, match="norms are read-only"):
        book.propose(candidate(remove=(seed_charter().norms[0],)))


@pytest.mark.parametrize("operation", ["replace", "remove"])
def test_unknown_card_ids_rejected(book, operation):
    value = (
        "missing-card" if operation == "remove" else replace(seed_charter().cards[0], id="missing")
    )
    with pytest.raises(ValueError, match="unknown card id"):
        book.propose(candidate(**{operation: (value,)}))


def test_wrong_base_and_existing_added_id_rejected(book):
    with pytest.raises(ValueError, match="edition_base"):
        book.propose(candidate(edition_base=2))
    with pytest.raises(ValueError, match="already exists"):
        book.propose(candidate(add=(seed_charter().cards[1],)))


@pytest.mark.parametrize(
    "changes",
    [
        {"add": (seed_charter().cards[1],) * 2},
        {"replace": (seed_charter().cards[0],) * 2},
        {"remove": ("well_formed_rate",) * 2},
        {"remove": ("cost_per_return",)},
        {"add": (seed_charter().cards[0],)},
        {"add": (seed_charter().cards[1],), "remove": ("well_formed_rate",)},
    ],
)
def test_duplicate_and_overlapping_card_operations_rejected(changes):
    with pytest.raises(ValueError, match="only once"):
        candidate(**changes)


def test_sortition_covers_roles_without_replacement():
    candidates = {
        **{f"producer-{index}": "producer" for index in range(20)}, "e": "evaluator", "m": "meta"
    }
    for seed in range(50):
        seats = draw(candidates, random.Random(seed))
        assert len(seats) == len({seat.assembly_id for seat in seats}) == 5
        assert {seat.role for seat in seats} == {"producer", "evaluator", "meta"}
        assert [seat.alias for seat in seats] == [f"seat-{index}" for index in range(1, 6)]
        assert all(candidates[seat.assembly_id] == seat.role for seat in seats)


def test_sortition_fills_remaining_seats_uniformly():
    # Core roles have one candidate each: the remaining two seats are an
    # unconstrained sample of ten others. Each should appear about 400 times.
    candidates = {"p": "producer", "e": "evaluator", "m": "meta"}
    candidates.update({f"other-{index}": "observer" for index in range(10)})
    counts = Counter()
    rng = random.Random(117)
    for _ in range(2000):
        counts.update(seat.assembly_id for seat in draw(candidates, rng))
    assert counts["p"] == counts["e"] == counts["m"] == 2000
    assert all(330 < counts[f"other-{index}"] < 470 for index in range(10))


def test_aliases_are_a_seeded_permutation_independent_of_input_order():
    candidates = eligible()
    seats = draw(candidates, random.Random(1))
    assert [seat.assembly_id for seat in seats] == [
        "assembly-3", "assembly-4", "assembly-0", "assembly-1", "assembly-2"
    ]
    assert seats == draw(dict(reversed(list(candidates.items()))), random.Random(1))
    assert seats != draw(candidates, random.Random(2))
    assert seats[0] == ("seat-1", "assembly-3", "producer")
    larger = eligible(30)
    assert draw(larger, random.Random(42)) == draw(
        dict(reversed(list(larger.items()))), random.Random(42)
    )


@pytest.mark.parametrize("size", range(6))
def test_short_population_seats_every_eligible_assembly(size):
    seats = draw(eligible(size), random.Random(0))
    assert {seat.assembly_id for seat in seats} == set(eligible(size))


def test_unavailable_roles_do_not_block_sortition():
    candidates = {f"producer-{index}": "producer" for index in range(8)}
    candidates["evaluator"] = "evaluator"
    for seed in range(20):
        seats = draw(candidates, random.Random(seed))
        assert len(seats) == 5
        assert {seat.role for seat in seats} == {"producer", "evaluator"}
    assert len(draw({"a": "producer", "b": "producer"}, random.Random(1), size=1)) == 1


@pytest.mark.parametrize("size", [0, -1, 1.5, True, 2])
def test_invalid_or_insufficient_requested_size_rejected(size):
    with pytest.raises(ValueError, match="size"):
        draw(eligible(), random.Random(1), size=size)


def test_committee_and_ballot_are_frozen_and_validate_uniqueness():
    seats = [Seat("seat-1", "assembly-1", "producer")]
    committee = Committee("amendment-1", 1, seats)
    seats.clear()
    assert len(committee.seats) == 1
    with pytest.raises(FrozenInstanceError):
        committee.round = 2
    ballot = Ballot("seat-1", True, "reason")
    with pytest.raises(FrozenInstanceError):
        ballot.vote = False
    with pytest.raises(ValueError, match="aliases"):
        replace(committee, seats=(committee.seats[0], Seat("seat-1", "other", "meta")))
    with pytest.raises(ValueError, match="assemblies"):
        replace(committee, seats=(committee.seats[0], Seat("seat-2", "assembly-1", "meta")))
    with pytest.raises(ValueError, match="round"):
        replace(committee, round=True)


def test_proposals_and_seating_are_unique_and_rounds_advance(book):
    first = seated(book)
    with pytest.raises(ValueError, match="already proposed"):
        book.propose(candidate())
    with pytest.raises(ValueError, match="already has a committee"):
        book.seat(first.amendment_id, eligible(), random.Random(0))
    with pytest.raises(ValueError, match="unknown amendment"):
        book.seat("unknown", eligible(), random.Random(0))
    second = seated(book, id="another-amendment")
    assert (first.round, second.round) == (1, 2)


@pytest.mark.parametrize("first_abstains", [False, True])
def test_an_alias_can_cast_only_one_ballot(book, first_abstains):
    committee = seated(book)
    alias = committee.seats[0].alias
    if first_abstains:
        book.abstain(committee, alias)
    else:
        book.vote(committee, alias, True, "reason")
    with pytest.raises(ValueError, match="already cast"):
        book.vote(committee, alias, False, "changed mind")
    with pytest.raises(ValueError, match="already cast"):
        book.abstain(committee, alias)


def test_unknown_alias_and_forged_or_foreign_committee_rejected(book):
    committee = seated(book)
    with pytest.raises(ValueError, match="unknown seat alias"):
        book.vote(committee, "seat-99", True, "reason")
    with pytest.raises(ValueError, match="unknown seat alias"):
        book.abstain(committee, "seat-99")
    other = CharterBook(Ledger(), seed_charter())
    for invalid in (replace(committee, round=10), replace(committee), seated(other)):
        with pytest.raises(ValueError, match="not issued"):
            book.vote(invalid, "seat-1", True, "reason")
        with pytest.raises(ValueError, match="not issued"):
            book.tally(invalid)
    assert book.tally(committee) is None


@pytest.mark.parametrize(
    ("vote", "reason"), [(1, "r"), (0, "r"), ("yes", "r"), (None, "r"), (True, None)]
)
def test_malformed_vote_rejected_until_caller_records_abstention(book, vote, reason):
    committee = seated(book)
    with pytest.raises(ValueError):
        book.vote(committee, "seat-1", vote, reason)
    book.abstain(committee, "seat-1")


@pytest.mark.parametrize(
    ("size", "votes", "outcome"),
    [
        (5, "", None),
        (5, "YY", None),
        (5, "YYY", "passed"),
        (5, "NNN", "failed"),
        (5, "YYNN", None),
        (5, "YYNNY", "passed"),
        (5, "YYNNN", "failed"),
        (5, "YYNNA", "failed"),
        (5, "AAA", "failed"),
        (5, "NAA", "failed"),
        (5, "AAYY", None),
        (5, "AAYYY", "passed"),
        (3, "Y", None),
        (3, "YY", "passed"),
        (3, "NN", "failed"),
        (3, "NA", "failed"),
        (3, "AA", "failed"),
        (3, "YN", None),
        (3, "YNY", "passed"),
        (3, "YNA", "failed"),
        (4, "YYNN", "failed"),
        (4, "YYAA", "failed"),
        (4, "YYY", "passed"),
        (2, "Y", None),
        (2, "YN", "failed"),
        (2, "YA", "failed"),
        (2, "YY", "passed"),
        (1, "Y", "passed"),
        (1, "A", "failed"),
        (0, "", "failed"),
    ],
)
def test_majority_thresholds_ties_and_abstention_arithmetic(book, size, votes, outcome):
    committee = seated(book, size)
    for seat, vote in zip(committee.seats, votes, strict=False):
        if vote == "A":
            book.abstain(committee, seat.alias)
        else:
            book.vote(committee, seat.alias, vote == "Y", "reason")
    assert book.tally(committee) == outcome


def test_activation_waits_for_boundary_and_preserves_all_editions(book, ledger):
    original = book.current()
    original_render = original.render()
    added = replace(original.cards[0], id="inquiry-cost", description="Cost of inquiry.")
    first = seated(book, remove=(original.cards[1].id,), add=(added,))
    second_card = replace(original.cards[2], description="A better forecast metric.")
    second = seated(book, id="better-forecasts", replace=(second_card,))
    approve(book, second)  # Approval order does not supersede proposal order.
    approve(book, first)
    book.abstain(first, "seat-4")  # Remaining votes can still be recorded after a majority.
    assert book.current() == original
    assert len(book.pending()) == 2
    edition2 = book.activate_due(100)
    assert edition2.edition == 2
    assert [card.id for card in edition2.cards] == [
        "cost_per_return", "forecast_skill", "inquiry-cost"
    ]
    assert edition2.cards[0] == candidate().replace[0]
    assert edition2.cards[-1] == added
    assert [amendment.id for amendment in book.pending()] == [second.amendment_id]
    with pytest.raises(ValueError, match="edition_base"):
        book.propose(candidate(id="stale-proposal"))
    edition3 = book.activate_due(200)
    assert edition3.edition == 3
    assert edition3.cards == (edition2.cards[0], second_card, added)
    assert book.editions() == (original, edition2, edition3)
    assert book.current() is edition3
    assert book.pending() == []
    assert book.activate_due(300) is None
    assert book.editions() == (original, edition2, edition3)
    assert original.render() == original_render
    assert all(edition.norms == original.norms for edition in book.editions())
    for number, edition in enumerate(book.editions(), 1):
        assert edition.render().startswith(f"CHARTER (edition {number})")
        with pytest.raises(FrozenInstanceError):
            edition.edition = 99
    with pytest.raises(ValueError, match="already activated"):
        book.vote(first, "seat-5", True, "too late")
    activations = [item for item in evidence(ledger) if item["kind"] == "charter.activate"]
    assert [(item["amendment_id"], item["edition"], item["ts"]) for item in activations] == [
        (first.amendment_id, 2, 100), (second.amendment_id, 3, 200)
    ]


def test_pending_is_detached_and_skips_failed_and_activated_proposals(book):
    failed = seated(book, id="failed-amendment")
    waiting = candidate(id="unseated-amendment")
    book.propose(waiting)
    passed = seated(book, id="passed-amendment")
    for seat in failed.seats[:3]:
        book.abstain(failed, seat.alias)
    book.pending().clear()
    assert [amendment.id for amendment in book.pending()] == [waiting.id, passed.amendment_id]
    assert book.activate_due(100) is None
    approve(book, passed)
    assert book.activate_due(200).edition == 2
    assert book.pending() == [waiting]


def test_same_base_conflicts_use_later_approved_patch_in_proposal_order(book):
    original = book.current()
    extra = replace(original.cards[0], id="extra-card")
    first = seated(book, replace=(), remove=(original.cards[0].id,), add=(extra,))
    changed_extra = replace(extra, description="Later approved description.")
    second = seated(book, id="second-amendment", add=(changed_extra,))
    approve(book, second)
    approve(book, first)
    assert book.activate_due(100).cards == (*original.cards[1:], extra)
    # Replacement restores the now-absent card at the end; the colliding add
    # updates its existing position. Both were valid against the frozen base.
    assert book.activate_due(200).cards == (
        *original.cards[1:], changed_extra, candidate().replace[0]
    )


def test_repeated_removals_and_later_replacements_have_deterministic_effects(book):
    original = book.current()
    first = seated(book, remove=(original.cards[1].id,))
    last_card = replace(original.cards[0], description="Latest cost metric.")
    second = seated(
        book, id="later-amendment", remove=(original.cards[1].id,), replace=(last_card,)
    )
    approve(book, first)
    approve(book, second)
    book.activate_due(100)
    assert book.activate_due(200).cards == (last_card, original.cards[2])


@pytest.mark.parametrize("now_ns", [-1, 1.5, True, "100"])
def test_invalid_boundary_time_cannot_activate(book, now_ns):
    committee = seated(book)
    approve(book, committee)
    with pytest.raises(ValueError, match="nanoseconds"):
        book.activate_due(now_ns)
    assert book.current().edition == 1


def test_book_detaches_seed_containers(ledger):
    seed = seed_charter()
    cards, norms = list(seed.cards), list(seed.norms)
    book = CharterBook(ledger, Charter(1, norms, cards))
    cards.clear()
    norms.clear()
    assert book.current() == seed
    assert book.editions() == (seed,)


def test_ledger_votes_use_only_aliases_and_seat_entry_seals_mapping(book, ledger):
    committee = seated(book)
    book.vote(committee, "seat-1", True, "a reason")
    book.vote(committee, "seat-2", False, "another reason")
    book.abstain(committee, "seat-3")
    with pytest.raises(PermissionError):
        ledger.decrypt_item(0)
    items = evidence(ledger)
    proposal, seating, *votes = items
    assert proposal["kind"] == "charter.propose"
    assert proposal["proposer_handle"] == "decision-1"
    assert proposal["predicted_effect"] == candidate().predicted_effect
    assert proposal["replace"][0]["id"] == "cost_per_return"
    assert seating["kind"] == "charter.seat"
    assert seating["seats"] == [
        {"alias": seat.alias, "assembly_id": seat.assembly_id, "role": seat.role}
        for seat in committee.seats
    ]
    assert [(item["alias"], item["vote"], item["reason"]) for item in votes] == [
        ("seat-1", True, "a reason"),
        ("seat-2", False, "another reason"),
        ("seat-3", None, "abstained"),
    ]
    for item in votes:
        assert set(item) == {
            "kind", "amendment_id", "round", "alias", "vote", "reason",
            "ts", "seq", "prev_hash", "hash",
        }
        assert item["kind"] == "charter.vote"
        assert item["amendment_id"] == committee.amendment_id
        assert item["round"] == committee.round
        assert all(seat.assembly_id not in str(item) for seat in committee.seats)


def test_rejected_ledger_writes_cannot_change_book_state(book, ledger, monkeypatch):
    append = ledger.append

    def reject(entry):
        raise RuntimeError("ledger unavailable")

    monkeypatch.setattr(ledger, "append", reject)
    with pytest.raises(RuntimeError, match="ledger unavailable"):
        book.propose(candidate())
    assert book.pending() == []
    monkeypatch.setattr(ledger, "append", append)
    book.propose(candidate())
    monkeypatch.setattr(ledger, "append", reject)
    with pytest.raises(RuntimeError, match="ledger unavailable"):
        book.seat("better-cost", eligible(), random.Random(0))
    monkeypatch.setattr(ledger, "append", append)
    committee = book.seat("better-cost", eligible(), random.Random(0))
    assert committee.round == 1
    monkeypatch.setattr(ledger, "append", reject)
    with pytest.raises(RuntimeError, match="ledger unavailable"):
        book.vote(committee, "seat-1", True, "reason")
    assert book.tally(committee) is None
    monkeypatch.setattr(ledger, "append", append)
    approve(book, committee)
    monkeypatch.setattr(ledger, "append", reject)
    with pytest.raises(RuntimeError, match="ledger unavailable"):
        book.activate_due(100)
    assert book.current().edition == 1
    assert book.pending() == [candidate()]
    monkeypatch.setattr(ledger, "append", append)
    assert book.activate_due(200).edition == 2
