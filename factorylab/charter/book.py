"""Charter changes require sealed evidence, a committee majority and a boundary."""

import random
from dataclasses import asdict

from factorylab.charter.amendment import Amendment
from factorylab.charter.charter import Charter
from factorylab.charter.committee import Ballot, Committee, draw
from factorylab.charter.measurement import preflight_card
from factorylab.kernel.ledger import Ledger


class CharterBook:
    """Frozen proposals and editions survive every later vote and activation.

    All mutations append evidence before changing book state. The caller owns
    boundary scheduling; each ``activate_due`` call is one boundary notification.
    """

    def __init__(self, ledger: Ledger, seed_charter: Charter) -> None:
        self.__ledger = ledger
        # Detach even a seed constructed with lists from caller-owned containers.
        self.__editions = [
            Charter(seed_charter.edition, tuple(seed_charter.norms), tuple(seed_charter.cards))
        ]
        self.__proposals: dict[str, Amendment] = {}
        self.__committees: dict[str, Committee] = {}
        self.__ballots: dict[str, dict[str, Ballot]] = {}
        self.__activated: set[str] = set()
        self.__activations: dict[int, Amendment] = {}

    def current(self) -> Charter:
        """Return the most recently activated immutable edition."""
        return self.__editions[-1]

    def editions(self) -> tuple[Charter, ...]:
        """Return every edition in activation order without exposing mutable storage."""
        return tuple(self.__editions)

    def propose(self, amendment: Amendment) -> None:
        """Freeze a unique candidate valid against the current edition's cards and norms."""
        self.validate(amendment)
        self.__ledger.append({"kind": "charter.propose", **asdict(amendment)})
        self.__proposals[amendment.id] = amendment

    def validate(self, amendment: Amendment) -> None:
        """Reject unchanged or unmeasurable candidate editions before any vote or reservation."""
        if not isinstance(amendment, Amendment):
            raise ValueError("proposal must be an Amendment")
        if amendment.id in self.__proposals:
            raise ValueError("amendment id already proposed")
        charter = self.current()
        if amendment.edition_base != charter.edition:
            raise ValueError("edition_base must match the current charter edition")
        ids = {card.id for card in charter.cards}
        for card in (*amendment.add, *amendment.replace):
            if card.norm not in charter.norms:
                raise ValueError("card references an unknown norm; norms are read-only")
            preflight_card(card)
        for card_id in (*amendment.remove, *(card.id for card in amendment.replace)):
            if card_id not in ids:
                raise ValueError(f"unknown card id: {card_id}; norms are read-only")
        if any(card.id in ids for card in amendment.add):
            raise ValueError("added card id already exists")
        resulting = {c.id: c for c in charter.cards if c.id not in amendment.remove}
        resulting.update((c.id, c) for c in (*amendment.replace, *amendment.add))
        if amendment.predicted_effect.card_id not in ids | set(resulting):
            raise ValueError("predicted_effect.card_id must name a current or proposed card")
        if (tuple(resulting.values()) == charter.cards
                and not amendment.proposed_prices and amendment.tick_interval is None):
            raise ValueError("amendment leaves the charter unchanged")
        validate_observation_bindings(tuple(resulting.values()))

    def pending(self) -> list[Amendment]:
        """Return unseated, undecided and passed-but-unactivated proposals in proposal order."""
        return [
            amendment
            for amendment_id, amendment in self.__proposals.items()
            if amendment_id not in self.__activated
            and (
                amendment_id not in self.__committees
                or self.tally(self.__committees[amendment_id]) != "failed"
            )
        ]

    def seat(
        self, amendment_id: str, eligible: dict[str, str], rng: random.Random, *, size: int = 5,
    ) -> Committee:
        """Issue exactly one committee per proposal and seal its alias-to-assembly mapping."""
        if amendment_id not in self.__proposals:
            raise ValueError("unknown amendment id")
        if amendment_id in self.__committees:
            raise ValueError("amendment already has a committee")
        committee = Committee(amendment_id, len(self.__committees) + 1, draw(eligible, rng, size))
        self.__ledger.append(
            {
                "kind": "charter.seat",
                "amendment_id": amendment_id,
                "round": committee.round,
                "seats": [seat._asdict() for seat in committee.seats],
            }
        )
        self.__committees[amendment_id] = committee
        self.__ballots[amendment_id] = {}
        return committee

    def vote(self, committee: Committee, alias: str, vote: bool, reason: str) -> None:
        """Record one strictly boolean vote per seated alias, without an assembly id."""
        if type(vote) is not bool:
            raise ValueError("vote must be a boolean; use abstain for malformed votes")
        self._record(committee, Ballot(alias, vote, reason))

    def abstain(self, committee: Committee, alias: str) -> None:
        """Consume an alias's ballot without contributing a yes vote."""
        self._record(committee, Ballot(alias, None, "abstained"))

    def tally(self, committee: Committee) -> str | None:
        """Pass only a strict majority; fail once remaining seats cannot reach that majority."""
        self._require_committee(committee)
        ballots = self.__ballots[committee.amendment_id]
        threshold = len(committee.seats) // 2 + 1
        yes = sum(ballot.vote is True for ballot in ballots.values())
        if yes >= threshold:
            return "passed"
        if yes + len(committee.seats) - len(ballots) < threshold:
            return "failed"
        return None

    def activate_due(self, now_ns: int) -> Charter | None:
        """Activate the earliest passed candidate at the caller-supplied boundary.

        One call activates at most one proposal. Frozen patches apply to the
        latest edition, even if approved against an older base: remove, replace,
        then add, with later proposals winning conflicts. Existing cards retain
        their positions; absent replacement/addition ids append in patch order.
        Earlier removals make a repeated removal a no-op. Norms never change.
        """
        if type(now_ns) is not int or now_ns < 0:
            raise ValueError("now_ns must be nonnegative integer nanoseconds")
        for amendment_id, amendment in self.__proposals.items():
            committee = self.__committees.get(amendment_id)
            if (
                amendment_id in self.__activated
                or committee is None
                or self.tally(committee) != "passed"
            ):
                continue
            current = self.current()
            cards = {card.id: card for card in current.cards}
            for card_id in amendment.remove:
                cards.pop(card_id, None)
            for card in (*amendment.replace, *amendment.add):
                cards[card.id] = card
            try:
                validate_observation_bindings(tuple(cards.values()))
            except ValueError as exc:
                self.__ledger.append({"kind": "charter.refused", "amendment_id": amendment_id,
                                      "reason": str(exc), "ts": now_ns})
                self.__activated.add(amendment_id)
                continue
            edition = Charter(current.edition + 1, current.norms, tuple(cards.values()))
            self.__ledger.append(
                {
                    "kind": "charter.activate",
                    "amendment_id": amendment_id,
                    "round": committee.round,
                    "edition": edition.edition,
                    "ts": now_ns,
                }
            )
            self.__editions.append(edition)
            self.__activated.add(amendment_id)
            self.__activations[edition.edition] = amendment
            return edition
        return None

    def activated_amendment(self, edition: int) -> Amendment:
        """Return the frozen amendment that produced an edition; unknown editions raise KeyError."""
        return self.__activations[edition]

    def _require_committee(self, committee: Committee) -> None:
        if (
            not isinstance(committee, Committee)
            or self.__committees.get(committee.amendment_id) is not committee
        ):
            raise ValueError("committee was not issued by this charter book")

    def _record(self, committee: Committee, ballot: Ballot) -> None:
        self._require_committee(committee)
        if ballot.alias not in {seat.alias for seat in committee.seats}:
            raise ValueError("unknown seat alias")
        ballots = self.__ballots[committee.amendment_id]
        if ballot.alias in ballots:
            raise ValueError("alias already cast a ballot")
        if committee.amendment_id in self.__activated:
            raise ValueError("amendment already activated")
        self.__ledger.append(
            {
                "kind": "charter.vote",
                "amendment_id": committee.amendment_id,
                "round": committee.round,
                **asdict(ballot),
            }
        )
        ballots[ballot.alias] = ballot


def validate_observation_bindings(cards) -> None:
    """No role is charged twice for one observation, including overlap through all."""
    for index, card in enumerate(cards):
        for previous in cards[:index]:
            if card.observation.strip().lower() == previous.observation.strip().lower() and (
                card.answers_for == previous.answers_for or "all" in (
                    card.answers_for, previous.answers_for
                )
            ):
                raise ValueError(
                    f"card {card.id} observation: already named by live card {previous.id} "
                    "for the same role"
                )
