"""The charter session's requests and its handling of malformed seat replies.

Chapter II §IV: the charter is "co-contributed to by the factory's governance
committee"; one seat's malformed reply is that seat's unusable answer, never the end
of the session. Chapter II §II.b, AGENTS rule 3: physics is enforced, not announced,
so the session's request text does not restate the rules its counting enforces.
"""

import json
import random

import pytest

from factorylab.runtime.worlds import load_manifest
from scripts import charter_session


class _Seats(charter_session.ScriptedCharterProvider):
    """The scripted population, with the first reply of one purpose replaced verbatim."""

    def __init__(self, manifest, purpose: str | None = None, reply: dict | None = None):
        super().__init__(manifest)
        self.purpose, self.reply, self.texts = purpose, reply, []

    def complete(self, req):
        text = "\n".join(str(m.get("content", "")) for m in req.messages)
        self.texts.append(text)
        purpose = ("propose" if text.startswith("REQUEST\nPropose")
                   else "vote" if text.startswith("REQUEST\nVote yes or no") else "adopt")
        if purpose == self.purpose and self.reply is not None:
            body, self.reply = json.dumps(self.reply), None
            return charter_session.ModelResponse(req.model_id, body, 1, 1, "stop",
                                                 cost_micro=0)
        return super().complete(req)


MALFORMED = [
    ("propose", {"cards": 1}),
    ("propose", {"cards": {"id": "x"}}),
    ("vote", {"votes": 1}),
    ("vote", {"votes": [{"proposal": [], "vote": True, "reason": "r"}]}),
    ("vote", {"votes": [{"proposal": {"k": 1}, "vote": True, "reason": "r"}]}),
]


@pytest.mark.parametrize(("purpose", "reply"), MALFORMED)
def test_one_malformed_seat_cannot_abort_charter_session(purpose, reply):
    manifest = load_manifest("scripted")
    provider = _Seats(manifest, purpose, reply)
    calls: list = []
    proposals, seats = charter_session.draft(manifest, provider, {}, random.Random(7), calls)
    proposers = {p.proposer for p in proposals}
    if purpose == "propose":
        # The malformed proposer offered nothing; every other seat's cards stand.
        assert len(proposers) == len(manifest.assemblies) - 1
        assert sum(c.error is not None for c in calls if c.purpose == "propose") == 1
    else:
        assert proposers == {a.id for a in manifest.assemblies}
        voted = [p for p in proposals if p.ballots]
        assert voted and all(len(p.ballots) == len(seats) for p in voted)
        # The malformed voter abstains on every card; the others voted yes.
        assert all(sum(v is None for v in p.ballots.values()) == 1 for p in voted)
        assert all(sum(v is True for v in p.ballots.values()) == len(seats) - 1
                   for p in voted)


def test_a_ballot_without_a_reason_abstains():
    manifest = load_manifest("scripted")
    keys = [f"p{n:02d}" for n in range(1, 10)]
    provider = _Seats(manifest, "vote", {"votes": [
        {"proposal": k, "vote": True} for k in keys]})
    proposals, seats = charter_session.draft(manifest, provider, {}, random.Random(7), [])
    voted = [p for p in proposals if p.ballots]
    assert voted and all(sum(v is None for v in p.ballots.values()) == 1 for p in voted)


CLAUSES = ("The norms are fixed", "the cards are the population's to write",
           "A card passes with", "yes votes of", "it does not edit or select cards",
           "The vote is adopt or reject")


def test_charter_requests_do_not_restate_enforced_rules():
    manifest = load_manifest("scripted")
    provider = _Seats(manifest)
    calls: list = []
    rng = random.Random(7)
    proposals, _ = charter_session.draft(manifest, provider, {}, rng, calls)
    passing = [(p.card, p.price) for p in proposals if p.problem is None]
    table = charter_session.tomllib.loads(
        charter_session.render_toml(passing, manifest.charter.norms, "run"))["charter"]
    charter_session.adopt(manifest, provider, table, {}, rng, None, calls)
    purposes = {t.split("\n", 2)[1].split()[0] for t in provider.texts}
    assert purposes == {"Propose", "Vote"}
    for text in provider.texts:
        request = text.split("\n\nINPUTS\n", 1)[0]
        for clause in CLAUSES:
            assert clause not in request, (clause, request)
    # The enforcement stands: an unknown norm is refused, a minority does not pass.
    raw = {f: "x" for f in charter_session.CARD_FIELDS}
    assert charter_session.card_from(raw, manifest.charter.norms)[2].startswith("norm ")
    lone = _Seats(manifest, "vote", None)
    lone.complete = _yes_from_one(lone)
    proposals, _ = charter_session.draft(manifest, lone, {}, random.Random(7), [])
    assert all(p.problem == "did not win a majority" for p in proposals if p.card)


def _yes_from_one(provider):
    """A population where only the first voter votes yes; the rest vote no."""
    inner, seen = provider.complete, []

    def complete(req):
        response = inner(req)
        text = "\n".join(str(m.get("content", "")) for m in req.messages)
        if not text.startswith("REQUEST\nVote yes or no"):
            return response
        seen.append(1)
        body = json.loads(response.text)
        for v in body["votes"]:
            v["vote"] = len(seen) == 1
        return charter_session.ModelResponse(req.model_id, json.dumps(body), 1, 1, "stop",
                                             cost_micro=0)
    return complete
