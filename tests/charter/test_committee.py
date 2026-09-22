import random
from dataclasses import FrozenInstanceError, asdict, replace

import pytest

from factorylab.charter.amendment import Amendment
from factorylab.charter.book import CharterBook
from factorylab.charter.committee import (
    Ballot,
    Committee,
    Seat,
    StandingCommittee,
    coverage,
    draw,
)
from factorylab.kernel.events import Bus
from factorylab.kernel.ledger import Ledger
from factorylab.kernel.termination import Termination
from tests.seed_charter import seed_charter


def candidate(**changes) -> Amendment:
    return replace(
        Amendment(
            id="better-cost",
            proposer_handle="decision-1",
            edition_base=1,
            add=(),
            replace=(replace(seed_charter().cards[0], acceptable_region="below 100"),),
            remove=(),
            predicted_effect={"card_id": "cost_per_return", "direction": "decrease", "window": 1},
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


def seated(book: CharterBook, size: int = 5, **changes) -> StandingCommittee:
    """Propose one motion and seat the next boundary's committee, whose agenda it is."""
    amendment = candidate(**changes)
    book.propose(amendment)
    boundary = len(book.sittings()) + book.deferrals() + 1
    committee = book.seat(boundary, eligible(size), random.Random(7), quorum=1)
    assert committee.agenda == (amendment.id,)
    return committee


def motion(committee: StandingCommittee) -> str:
    return committee.agenda[0]


def approve(book: CharterBook, committee: StandingCommittee) -> None:
    for seat in committee.seats[: len(committee.seats) // 2 + 1]:
        book.vote(committee, motion(committee), seat.alias, True,
                  "Expected improvement is credible.")


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


def test_amendment_bounds_and_freezing():
    cards = [replace(seed_charter().cards[0], id="new-card")]
    amendment = candidate(id="a" * 48, add=cards)
    cards.clear()
    assert len(amendment.add) == 1
    assert isinstance(amendment.add, tuple)
    with pytest.raises(FrozenInstanceError):
        amendment.predicted_effect = "revised after seeing votes"
    with pytest.raises(FrozenInstanceError):
        amendment.add[0].description = "revised after seeing votes"


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



@pytest.mark.parametrize("first_abstains", [False, True])
def test_an_alias_can_cast_only_one_ballot(book, first_abstains):
    committee = seated(book)
    alias = committee.seats[0].alias
    if first_abstains:
        book.abstain(committee, motion(committee), alias)
    else:
        book.vote(committee, motion(committee), alias, True, "reason")
    with pytest.raises(ValueError, match="already cast"):
        book.vote(committee, motion(committee), alias, False, "changed mind")
    with pytest.raises(ValueError, match="already cast"):
        book.abstain(committee, motion(committee), alias)


def test_unknown_alias_and_forged_or_foreign_committee_rejected(book):
    committee = seated(book)
    with pytest.raises(ValueError, match="unknown seat alias"):
        book.vote(committee, motion(committee), "seat-99", True, "reason")
    with pytest.raises(ValueError, match="unknown seat alias"):
        book.abstain(committee, motion(committee), "seat-99")
    other = CharterBook(Ledger(), seed_charter())
    for invalid in (replace(committee, round=10), replace(committee), seated(other)):
        with pytest.raises(ValueError, match="not issued"):
            book.vote(invalid, motion(committee), "seat-1", True, "reason")
        with pytest.raises(ValueError, match="not issued"):
            book.tally(invalid, motion(committee))
    assert book.tally(committee, motion(committee)) is None


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
    ],
)
def test_majority_thresholds_ties_and_abstention_arithmetic(book, size, votes, outcome):
    committee = seated(book, size)
    for seat, vote in zip(committee.seats, votes, strict=False):
        if vote == "A":
            book.abstain(committee, motion(committee), seat.alias)
        else:
            book.vote(committee, motion(committee), seat.alias, vote == "Y", "reason")
    assert book.tally(committee, motion(committee)) == outcome


def test_activation_waits_for_boundary_and_preserves_all_editions(book, ledger):
    original = book.current()
    original_render = original.render()
    added = replace(original.cards[0], id="inquiry-cost", description="Cost of inquiry.",
                    answers_for="meta")
    first = seated(book, remove=(original.cards[1].id,), add=(added,))
    second_card = replace(original.cards[2], description="A better forecast metric.")
    second = seated(book, id="better-forecasts", replace=(second_card,))
    approve(book, second)  # Approval order does not supersede proposal order.
    approve(book, first)
    # Remaining votes can still be recorded after a majority.
    book.abstain(first, motion(first), "seat-4")
    assert book.current() == original
    assert len(book.pending()) == 2
    edition2 = book.activate_due(100)
    assert edition2.edition == 2
    assert [card.id for card in edition2.cards] == [
        "cost_per_return", "forecast_skill", "inquiry-cost"
    ]
    assert edition2.cards[0] == candidate().replace[0]
    assert edition2.cards[-1] == added
    assert [amendment.id for amendment in book.pending()] == [motion(second)]
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
        book.vote(first, motion(first), "seat-5", True, "too late")
    activations = [item for item in evidence(ledger) if item["kind"] == "charter.activate"]
    assert [(item["amendment_id"], item["edition"], item["ts"]) for item in activations] == [
        (motion(first), 2, 100), (motion(second), 3, 200)
    ]
    assert all(item["change"] == ["cards"] for item in activations)


def test_same_base_conflicts_use_later_approved_patch_in_proposal_order(book):
    original = book.current()
    extra = replace(original.cards[0], id="extra-card", answers_for="meta")
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


def test_ledger_votes_use_only_aliases_and_seat_entry_seals_mapping(book, ledger):
    committee = seated(book)
    book.vote(committee, motion(committee), "seat-1", True, "a reason")
    book.vote(committee, motion(committee), "seat-2", False, "another reason")
    book.abstain(committee, motion(committee), "seat-3")
    with pytest.raises(PermissionError):
        ledger.decrypt_item(0)
    items = evidence(ledger)
    proposal, seating, *votes = items
    assert proposal["kind"] == "charter.propose"
    assert proposal["proposer_handle"] == "decision-1"
    assert proposal["predicted_effect"] == asdict(candidate().predicted_effect)
    assert proposal["replace"][0]["id"] == "cost_per_return"
    assert seating["kind"] == "charter.seat"
    assert seating["seats"] == [
        {"alias": seat.alias, "assembly_id": seat.assembly_id, "role": seat.role}
        for seat in committee.seats
    ]
    assert seating["agenda"] == [motion(committee)] and seating["boundary"] == 1
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
        assert item["amendment_id"] == motion(committee)
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
        book.seat(1, eligible(), random.Random(0))
    assert book.sittings() == () and book.agenda() == [candidate()]
    monkeypatch.setattr(ledger, "append", append)
    committee = book.seat(1, eligible(), random.Random(0))
    assert committee.round == 1
    monkeypatch.setattr(ledger, "append", reject)
    with pytest.raises(RuntimeError, match="ledger unavailable"):
        book.vote(committee, motion(committee), "seat-1", True, "reason")
    assert book.tally(committee, motion(committee)) is None
    monkeypatch.setattr(ledger, "append", append)
    approve(book, committee)
    monkeypatch.setattr(ledger, "append", reject)
    with pytest.raises(RuntimeError, match="ledger unavailable"):
        book.activate_due(100)
    assert book.current().edition == 1
    assert book.pending() == [candidate()]
    monkeypatch.setattr(ledger, "append", append)
    assert book.activate_due(200).edition == 2


# --- charter audit C1, C2: the standing committee -----------------------------------


def test_one_committee_per_boundary_votes_every_waiting_motion(book, ledger):
    """C1: motions wait for the boundary; its one committee has all of them on its agenda."""
    book.propose(candidate())
    card = replace(seed_charter().cards[1], acceptable_region="above 0.8")
    book.propose(candidate(id="second-motion", replace=(card,),
                           predicted_effect={"card_id": "well_formed_rate",
                                             "direction": "increase", "window": 1}))
    assert [am.id for am in book.agenda()] == ["better-cost", "second-motion"]
    committee = book.seat(1, eligible(), random.Random(1))
    assert committee.agenda == ("better-cost", "second-motion")
    assert book.agenda() == []
    with pytest.raises(ValueError, match="already has a committee"):
        book.seat(1, eligible(), random.Random(1))
    # A motion arriving after the boundary waits for the next committee, a new draw.
    book.propose(candidate(id="late-motion"))
    later = book.seat(2, eligible(10), random.Random(2))
    assert later.agenda == ("late-motion",) and later.round == 2
    assert {s.assembly_id for s in later.seats} != {s.assembly_id for s in committee.seats}


def test_below_quorum_no_rump_is_seated_and_the_motion_waits(book, ledger):
    """C2: with fewer eligible assemblies than quorum the boundary is deferred and ledgered."""
    book.propose(candidate())
    assert book.seat(1, eligible(2), random.Random(0), quorum=3) is None
    assert book.sittings() == () and book.deferrals() == 1
    assert [am.id for am in book.agenda()] == ["better-cost"]
    committee = book.seat(2, eligible(3), random.Random(0), quorum=3)
    assert committee.agenda == ("better-cost",)
    deferred = next(i for i in evidence(ledger) if i["kind"] == "charter.seat_deferred")
    assert deferred["boundary"] == 1 and deferred["eligible"] == 2 and deferred["quorum"] == 3
    assert deferred["agenda"] == ["better-cost"]


def test_the_proposer_does_not_vote_and_a_motion_short_of_quorum_waits(book):
    """The proposer is excluded per motion; three seats less one proposer is below quorum 3."""
    book.propose(candidate())
    pool = eligible(3)
    committee = book.seat(1, pool, random.Random(0), quorum=3,
                          recusals={"better-cost": frozenset({"assembly-0"})})
    assert committee.agenda == () and committee.deferred == ("better-cost",)
    assert [am.id for am in book.agenda()] == ["better-cost"]
    committee = book.seat(2, eligible(5), random.Random(0), quorum=3,
                          recusals={"better-cost": frozenset({"assembly-0"})})
    voters = book.voters(committee, "better-cost")
    assert len(voters) == 4
    proposer = next(s.alias for s in committee.seats if s.assembly_id == "assembly-0")
    assert proposer not in voters
    with pytest.raises(ValueError, match="unknown seat alias"):
        book.vote(committee, "better-cost", proposer, True, "my own motion")
    for alias in voters[:3]:
        book.vote(committee, "better-cost", alias, True, "yes")
    assert book.tally(committee, "better-cost") == "passed"


def test_stratified_draw_covers_every_role_and_learner_type():
    """C2: every role in ROLES, a declared role, and both learner types, as far as seats allow."""
    pool = {**{f"p{i}": "producer" for i in range(12)}, "e1": "evaluator", "e2": "evaluator",
            "m1": "meta", "a1": "antagonist", "w1": "watcher"}
    learners = {a: frozenset({"exp3"}) for a in pool}
    learners["p7"] = frozenset({"blum_mansour"})
    for seed in range(40):
        seats = draw(pool, random.Random(seed), 6, learners=learners)
        roles = {seat.role for seat in seats}
        assert roles == {"producer", "evaluator", "meta", "antagonist", "watcher"}
        # The one no-swap-regret learner is a producer, and a producer seat prefers it.
        assert "p7" in {seat.assembly_id for seat in seats}
        report = coverage(pool, seats, learners)
        assert report["roles"]["covered"] == report["roles"]["present"] == [
            "producer", "evaluator", "meta", "antagonist", "watcher"]
        assert report["learners"]["covered"] == ["blum_mansour", "exp3"]


def test_stratified_draw_covers_a_learner_type_outside_the_role_picks():
    pool = {"p1": "producer", "p2": "producer", "e1": "evaluator", "e2": "evaluator"}
    learners = {"p1": frozenset({"exp3"}), "p2": frozenset({"exp3"}),
                "e1": frozenset({"exp3"}), "e2": frozenset({"blum_mansour"})}
    for seed in range(30):
        seats = draw(pool, random.Random(seed), 3, learners=learners)
        types = {k for s in seats for k in learners[s.assembly_id]}
        assert types == {"exp3", "blum_mansour"}
        assert {s.role for s in seats} == {"producer", "evaluator"}


def test_more_roles_than_seats_covers_a_uniform_sample_of_roles():
    pool = {"p": "producer", "e": "evaluator", "m": "meta", "a": "antagonist",
            "x": "producer"}
    seen = set()
    for seed in range(60):
        seats = draw(pool, random.Random(seed), 3)
        roles = [s.role for s in seats]
        assert len(set(roles)) == 3  # three seats, three distinct roles
        seen.add(frozenset(roles))
    assert len(seen) > 1  # no role is structurally first


def test_seat_entry_reports_stratum_coverage(book, ledger):
    book.propose(candidate())
    pool = {"p1": "producer", "p2": "producer", "e1": "evaluator", "m1": "meta"}
    learners = {"p1": frozenset({"exp3"}), "p2": frozenset({"exp3"}),
                "e1": frozenset({"blum_mansour"}), "m1": frozenset({"exp3"})}
    book.seat(1, pool, random.Random(3), size=3, learners=learners)
    seating = next(i for i in evidence(ledger) if i["kind"] == "charter.seat")
    assert seating["coverage"]["roles"] == {"present": ["producer", "evaluator", "meta"],
                                            "covered": ["producer", "evaluator", "meta"]}
    assert seating["coverage"]["learners"] == {"present": ["blum_mansour", "exp3"],
                                               "covered": ["blum_mansour", "exp3"]}
    assert seating["quorum"] == 3 and seating["voters"] == {"better-cost": 3}


def test_standing_committee_is_frozen_and_validated():
    seats = (Seat("seat-1", "a", "producer"), Seat("seat-2", "b", "meta"))
    committee = StandingCommittee(1, 1, seats, ("m",))
    with pytest.raises(FrozenInstanceError):
        committee.round = 2
    with pytest.raises(ValueError, match="aliases"):
        StandingCommittee(1, 1, (seats[0], Seat("seat-1", "c", "meta")))
    with pytest.raises(ValueError, match="boundary"):
        StandingCommittee(-1, 1, seats)
