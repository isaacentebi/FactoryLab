"""Charter changes require sealed evidence, a committee majority and a boundary."""

import random
from copy import deepcopy
from dataclasses import asdict, dataclass

from factorylab.charter.amendment import Amendment
from factorylab.charter.charter import Charter
from factorylab.charter.committee import Ballot, Committee, StandingCommittee, draw
from factorylab.charter.measurement import preflight_card
from factorylab.kernel.ledger import Ledger


@dataclass(frozen=True)
class Refusal:
    """A passed amendment that cannot take effect at its boundary, and why.

    The refusal is an outcome of ``activate_due``, not a silent skip: the
    caller owes every ballot on the named amendment a closed, ungraded
    settlement, because the predicted effect will never be observable.
    """

    amendment_id: str
    reason: str


class CharterBook:
    """Frozen proposals and editions survive every later vote and activation.

    All mutations append evidence before changing book state. The caller owns
    boundary scheduling; each ``activate_due`` call is one boundary notification.
    """

    def __init__(self, ledger: Ledger, seed_charter: Charter, observations=None) -> None:
        self.__ledger = ledger
        self.__observations = observations
        # Detach even a seed constructed with lists from caller-owned containers.
        self.__editions = [
            Charter(seed_charter.edition, tuple(seed_charter.norms), tuple(seed_charter.cards))
        ]
        self.__proposals: dict[str, Amendment] = {}
        self.__committees: dict[str, Committee] = {}
        self.__ballots: dict[str, dict[str, Ballot]] = {}
        self.__activated: set[str] = set()
        self.__activations: dict[int, Amendment] = {}
        self.__bindings: dict[str, dict[str, dict]] = {}
        # Standing committees by governance boundary (charter audit C1), the
        # boundaries that fell below quorum, each motion's voting aliases, and the
        # editions a norm edition produced (charter audit M4).
        self.__sittings: dict[int, StandingCommittee] = {}
        self.__deferrals: set[int] = set()
        self.__voters: dict[str, tuple[str, ...]] = {}
        self.__norm_editions: dict[int, dict] = {}

    def bind_observations(self, observations) -> None:
        """Resolve the runtime vocabulary afresh, including after checkpoint restoration."""
        self.__observations = observations

    def _observation_book(self, observations=None):
        from factorylab.runtime.observations import seed_book

        book = observations if observations is not None else self.__observations
        return (book() if callable(book) else book) or seed_book()

    def current(self) -> Charter:
        """Return the most recently activated immutable edition."""
        return self.__editions[-1]

    def editions(self) -> tuple[Charter, ...]:
        """Return every edition in activation order without exposing mutable storage."""
        return tuple(self.__editions)

    def propose(self, amendment: Amendment, observations=None) -> None:
        """Freeze a unique candidate valid against the current edition's cards and norms.

        The observation version behind every card is frozen with the candidate,
        in the ledger and in book state, because a committee votes on a measured
        promise and not on an id: see ``_observation_drift``.
        """
        self.validate(amendment, observations)
        book = self._observation_book(observations)
        cards = {c.id: c for c in (*self.current().cards, *amendment.replace, *amendment.add)}
        bindings = {card.id: {"id": observation.id, "version": observation.version}
                    for card in cards.values()
                    if (observation := book.get(card.observation)) is not None}
        self.__ledger.append({"kind": "charter.propose", **asdict(amendment),
                              "observation_bindings": bindings})
        self.__proposals[amendment.id] = amendment
        self.__bindings[amendment.id] = bindings

    def validate(self, amendment: Amendment, observations=None) -> None:
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
            preflight_card(card, self._observation_book(observations))
        for card_id in (*amendment.remove, *(card.id for card in amendment.replace)):
            if card_id not in ids:
                raise ValueError(f"unknown card id: {card_id}; norms are read-only")
        if any(card.id in ids for card in amendment.add):
            raise ValueError("added card id already exists")
        for card_id, _ in amendment.proposed_prices:
            if card_id not in ids:
                raise ValueError(f"lambda names card {card_id}, which the current edition "
                                 "does not carry")
        resulting = {c.id: c for c in charter.cards if c.id not in amendment.remove}
        resulting.update((c.id, c) for c in (*amendment.replace, *amendment.add))
        if amendment.holdout is not None:
            appended, reason = _append_holdout(charter, amendment.holdout)
            if reason is not None:
                raise ValueError(reason)
            resulting[appended.id] = appended
        effect = amendment.predicted_effect
        if effect.observation is None and effect.card_id not in ids | set(resulting):
            raise ValueError("predicted_effect.card_id must name a current or proposed card")
        if (tuple(resulting.values()) == charter.cards and not amendment.proposed_prices
                and amendment.tick_interval is None and amendment.holdout is None):
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
                or self.tally(self.__committees[amendment_id], amendment_id) != "failed"
            )
        ]

    def agenda(self) -> list[Amendment]:
        """Motions no committee has voted on yet, in proposal order: the next committee's."""
        return [amendment for amendment_id, amendment in self.__proposals.items()
                if amendment_id not in self.__activated and amendment_id not in self.__committees]

    def sittings(self) -> tuple[StandingCommittee, ...]:
        """Every standing committee seated so far, in boundary order."""
        return tuple(self.__sittings.values())

    def deferrals(self) -> int:
        """How many governance boundaries seated no committee, for want of a quorum."""
        return len(self.__deferrals)

    def seat(
        self, boundary: int, eligible: dict[str, str], rng: random.Random, *, size: int = 5,
        quorum: int = 3, learners: dict[str, frozenset[str]] | None = None,
        recusals: dict[str, frozenset[str]] | None = None,
    ) -> StandingCommittee | None:
        """Seat exactly one committee per governance boundary, or none below quorum.

        Essay II.IV.a: "On the cadence of charter revision, a sample of the
        factory's population is seated … and that seat is consistently rotated."
        The committee's agenda is every motion no committee has voted on. Each
        motion's proposer (``recusals``) does not vote on it; a motion left with
        fewer voting seats than ``quorum`` is deferred to the next boundary. A
        population with fewer eligible assemblies than ``quorum`` seats no one:
        the deferral is ledgered and every motion waits. The seating entry seals
        the alias-to-assembly mapping and states the draw's stratum coverage.
        """
        from factorylab.charter.committee import coverage

        if type(boundary) is not int or boundary < 0:
            raise ValueError("boundary must be a nonnegative integer")
        if boundary in self.__sittings or boundary in self.__deferrals:
            raise ValueError("this boundary already has a committee")
        if type(quorum) is not int or quorum < 1:
            raise ValueError("quorum must be a positive integer")
        motions = [amendment.id for amendment in self.agenda()]
        if len(eligible) < quorum:
            self.__ledger.append({"kind": "charter.seat_deferred", "boundary": boundary,
                                  "eligible": len(eligible), "quorum": quorum,
                                  "agenda": motions,
                                  "coverage": coverage(eligible, (), learners)})
            self.__deferrals.add(boundary)
            return None
        seats = draw(eligible, rng, size, learners=learners)
        recused = recusals or {}
        voters = {motion: tuple(seat.alias for seat in seats
                                if seat.assembly_id not in recused.get(motion, ()))
                  for motion in motions}
        agenda = tuple(motion for motion in motions if len(voters[motion]) >= quorum)
        deferred = tuple(motion for motion in motions if motion not in agenda)
        committee = StandingCommittee(boundary, len(self.__sittings) + 1, seats,
                                      agenda, deferred)
        self.__ledger.append(
            {
                "kind": "charter.seat",
                "boundary": boundary,
                "round": committee.round,
                "seats": [seat._asdict() for seat in committee.seats],
                "agenda": list(agenda),
                "deferred": list(deferred),
                "voters": {motion: len(voters[motion]) for motion in motions},
                "quorum": quorum,
                "coverage": coverage(eligible, seats, learners),
            }
        )
        self.__sittings[boundary] = committee
        for motion in agenda:
            self.__committees[motion] = committee
            self.__voters[motion] = voters[motion]
            self.__ballots[motion] = {}
        return committee

    def voters(self, committee, motion_id: str) -> tuple[str, ...]:
        """The aliases entitled to vote on a motion: every seat but its proposer's."""
        self._require_committee(committee, motion_id)
        return self.__voters.get(motion_id, tuple(seat.alias for seat in committee.seats))

    def vote(self, committee, motion_id: str, alias: str, vote: bool, reason: str) -> None:
        """Record one strictly boolean vote per seated alias, without an assembly id."""
        if type(vote) is not bool:
            raise ValueError("vote must be a boolean; use abstain for malformed votes")
        self._record(committee, motion_id, Ballot(alias, vote, reason))

    def abstain(self, committee, motion_id: str, alias: str) -> None:
        """Consume an alias's ballot without contributing a yes vote."""
        self._record(committee, motion_id, Ballot(alias, None, "abstained"))

    def tally(self, committee, motion_id: str) -> str | None:
        """Pass only a strict majority of the motion's voters; fail once it is out of reach."""
        voters = self.voters(committee, motion_id)
        ballots = self.__ballots[motion_id]
        threshold = len(voters) // 2 + 1
        yes = sum(ballot.vote is True for ballot in ballots.values())
        if yes >= threshold:
            return "passed"
        if yes + len(voters) - len(ballots) < threshold:
            return "failed"
        return None

    def activate_due(self, now_ns: int) -> Charter | Refusal | None:
        """Activate the earliest passed candidate at the caller-supplied boundary.

        One call activates at most one proposal, or returns the ``Refusal`` of
        the earliest passed candidate whose frozen patch cannot take effect
        against the current edition. Frozen patches apply to the latest edition,
        even if approved against an older base: remove, replace, then add, with
        later proposals winning conflicts. Existing cards retain their positions;
        absent replacement/addition ids append in patch order. Earlier removals
        make a repeated removal a no-op. Norms never change.

        A candidate is refused when an earlier activation has since made its
        patch conflict, or has already made the very same change, so that the
        patched candidate leaves the current edition unchanged, or when an
        observation behind one of its own cards has been re-registered since the
        vote. Either way the refusal is evidence, and the candidate is spent.
        """
        if type(now_ns) is not int or now_ns < 0:
            raise ValueError("now_ns must be nonnegative integer nanoseconds")
        for amendment_id, amendment in self.__proposals.items():
            committee = self.__committees.get(amendment_id)
            if (
                amendment_id in self.__activated
                or committee is None
                or self.tally(committee, amendment_id) != "passed"
            ):
                continue
            current = self.current()
            cards = {card.id: card for card in current.cards}
            for card_id in amendment.remove:
                cards.pop(card_id, None)
            for card in (*amendment.replace, *amendment.add):
                cards[card.id] = card
            reason = None
            if amendment.holdout is not None:
                # Appended to the card as it stands now: a cards motion activated during
                # the holdout's trial is kept, never reverted by a frozen copy.
                appended, reason = _append_holdout(current, amendment.holdout)
                if appended is not None:
                    cards[appended.id] = appended
            patched = tuple(cards.values())
            unknown_norm = next((card for card in (*amendment.replace, *amendment.add)
                                 if card.norm not in current.norms), None)
            unpriced = next((card_id for card_id, _ in amendment.proposed_prices
                             if card_id not in cards), None)
            if reason is not None:
                pass  # the holdout cannot be appended to the card as it stands
            elif unknown_norm is not None:
                # A norm edition removed the norm this card interprets (essay II.IV.a:
                # the norm layer sits behind a read-only wall).
                reason = (f"card {unknown_norm.id} names norm {unknown_norm.norm}, which "
                          f"edition {current.edition} does not carry")
            elif unpriced is not None:
                reason = f"lambda names card {unpriced}, which edition {current.edition} " \
                         "does not carry"
            elif (patched == current.cards and not amendment.proposed_prices
                    and amendment.tick_interval is None and amendment.holdout is None):
                # An earlier activation already made this exact change; proposal
                # time checked a base edition that no longer states the effect.
                reason = "amendment leaves the charter unchanged"
            else:
                try:
                    validate_observation_bindings(patched)
                except ValueError as exc:
                    reason = str(exc)
                else:
                    reason = self._observation_drift(amendment)
            if reason is not None:
                self.__ledger.append({"kind": "charter.refused", "amendment_id": amendment_id,
                                      "reason": reason, "ts": now_ns})
                self.__activated.add(amendment_id)
                return Refusal(amendment_id, reason)
            edition = Charter(current.edition + 1, current.norms, patched)
            self.__ledger.append(
                {
                    "kind": "charter.activate",
                    "amendment_id": amendment_id,
                    "round": committee.round,
                    "change": list(amendment.change_classes()),
                    "edition": edition.edition,
                    "ts": now_ns,
                }
            )
            self.__editions.append(edition)
            self.__activated.add(amendment_id)
            self.__activations[edition.edition] = amendment
            return edition
        return None

    def _observation_drift(self, amendment: Amendment) -> str | None:
        """Name the first card of a candidate whose observation is no longer the voted one.

        A committee votes on a card's measurement, not on the id it names, and a
        registered observation can be superseded between the ballot and the
        boundary. The book keeps only the newest implementation of an id, so the
        voted version cannot be pinned for the activated edition; the candidate
        is refused instead, and the proposer may re-propose against the new
        measurement. Seed observations are single-version and never drift.
        """
        bindings = self.__bindings.get(amendment.id) or {}
        book = self._observation_book()
        for card in (*amendment.replace, *amendment.add):
            voted = bindings.get(card.id)
            if voted is None:
                continue
            live = book.get(card.observation)
            if live is not None and live.id == voted["id"] and live.version == voted["version"]:
                continue
            now = "withdrawn" if live is None else f"version {live.version}"
            return (f"card {card.id} observation: committee voted on {voted['id']} "
                    f"version {voted['version']}, now {now}")
        return None

    def activated_amendment(self, edition: int) -> Amendment:
        """Return the frozen amendment that produced an edition; unknown editions raise KeyError."""
        return self.__activations[edition]

    def norm_editions(self) -> dict[int, dict]:
        """Each charter edition a norm edition produced, with its sequence, digest and signer."""
        return deepcopy(self.__norm_editions)

    def apply_norm_edition(self, norms, *, sequence: int, digest: str, signer: str,
                           now_ns: int) -> tuple[Charter, tuple[str, ...], tuple[str, ...]]:
        """Issue ``edition + 1`` with the norm house's norms and the factory's cards carried over.

        Essay II.IV.a: the norm layer is "read-only" from the factory's
        perspective, authored by a house outside it, "though the factory is
        expected to testify within the assembly". Only norms change. Every card
        whose norm the edition keeps is carried over unchanged; a card on a
        removed norm is refused, and so is every undecided or passed motion
        whose cards name a removed norm. Each refusal is ledgered before the
        edition exists. Returns the edition, the refused card ids and the
        refused motion ids.
        """
        if type(sequence) is not int or sequence != len(self.__norm_editions) + 1:
            raise ValueError("norm edition sequence must follow the last one applied")
        current = self.current()
        edition = Charter(current.edition + 1, tuple(norms),
                          tuple(card for card in current.cards if card.norm in norms))
        kept = set(edition.norms)
        refused_cards = tuple(card.id for card in current.cards if card.norm not in kept)
        refused_motions = tuple(
            amendment.id for amendment in self.pending()
            if any(card.norm not in kept for card in (*amendment.replace, *amendment.add)))
        self.__ledger.append({
            "kind": "charter.norm_edition", "sequence": sequence, "digest": digest,
            "signer": signer, "edition": edition.edition, "base_edition": current.edition,
            "norms": [norm.as_dict() for norm in edition.norms],
            "removed": [str(n) for n in current.norms if n not in kept],
            "added": [str(n) for n in edition.norms if n not in current.norms],
            "ts": now_ns,
        })
        for card in current.cards:
            if card.norm not in kept:
                self.__ledger.append({"kind": "charter.refused", "card_id": card.id,
                                      "reason": f"norm {card.norm} removed by norm edition "
                                                f"{sequence}", "ts": now_ns})
        for amendment_id in refused_motions:
            self.__ledger.append({"kind": "charter.refused", "amendment_id": amendment_id,
                                  "reason": f"a card names a norm removed by norm edition "
                                            f"{sequence}", "ts": now_ns})
            self.__activated.add(amendment_id)
        self.__editions.append(edition)
        self.__norm_editions[edition.edition] = {"sequence": sequence, "digest": digest,
                                                 "signer": signer}
        return edition, refused_cards, refused_motions

    def _require_committee(self, committee, motion_id: str) -> None:
        if (
            not isinstance(committee, (Committee, StandingCommittee))
            or self.__committees.get(motion_id) is not committee
        ):
            raise ValueError("committee was not issued by this charter book")

    def _record(self, committee, motion_id: str, ballot: Ballot) -> None:
        if ballot.alias not in self.voters(committee, motion_id):
            raise ValueError("unknown seat alias")
        ballots = self.__ballots[motion_id]
        if ballot.alias in ballots:
            raise ValueError("alias already cast a ballot")
        if motion_id in self.__activated:
            raise ValueError("amendment already activated")
        self.__ledger.append(
            {
                "kind": "charter.vote",
                "amendment_id": motion_id,
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


def _append_holdout(charter: Charter, holdout: tuple[str, str]):
    """The card with one more holdout, as the edition carries it; or None and why not.

    Refused when the edition no longer carries the card, or the card already holds a
    version of the predicate (a cards motion during the trial may have added or kept
    one).
    """
    from dataclasses import replace

    card_id, entry = holdout
    card = next((c for c in charter.cards if c.id == card_id), None)
    if card is None:
        return None, (f"holdout names card {card_id}, which edition {charter.edition} "
                      "does not carry")
    if entry.split("@")[0] in {h.split("@")[0] for h in card.holdout}:
        return None, f"card {card_id} already holds predicate {entry.split('@')[0]}"
    return replace(card, holdout=(*card.holdout, entry)), None
