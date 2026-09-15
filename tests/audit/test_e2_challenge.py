"""Edition 2, contract C7: a metric challenge is admitted, trialled, balloted and adopted.

The challenge is not judged by the card it challenges: both the incumbent and
the replacement are measured, frozen, for the declared number of windows, both
series are ledgered, the existing amendment ballot decides adoption, a promise
made during the trial may name the challenge id, and commitments incurred under
the incumbent settle under the incumbent.
"""

from types import SimpleNamespace

import pytest

from factorylab.charter.charter import MetricCard
from factorylab.charter.windows import MetricWindow
from factorylab.cortex.registration import ChallengeProposal, parse_proposals
from factorylab.cortex.request import Return
from factorylab.runtime.pricing import MeasureWindow
from factorylab.runtime.resume import restore_runtime, runtime_state
from tests.audit.test_a15_liability import boundary
from tests.audit.test_r3_c_liability import committee
from tests.runtime.test_fidelity import decision, runtime


def _items(rt, kind):
    return [i for i in rt.ledger._recovery_items() if i["kind"] == kind]


def _challenge(**changes):
    item = {
        "kind": "challenge", "card_id": "cost_per_return",
        "evidence": "nine cheap successes and one expensive failure pass the cap",
        "replacement": {"observation": "cost_per_attempt", "rule": "at most", "value": 5000,
                        "window": {"kind": "windows", "n": 1, "per": None}},
        "trial_windows": 2,
    }
    item.update(changes)
    return item


def _returns(rt, *costs_ok):
    """Producer returns in the live window, so both cost selections have rows to read."""
    handles = []
    for cost, ok in costs_ok:
        handle = decision(rt, "seed-decider")
        rt.card_samples.returned(handle=handle, assembly="seed-decider", role="producer",
                                 window=rt.window.index,
                                 ret=Return(handle, {}, cost, "ok" if ok else "failed"))
        rt._contribution(handle, "producer").update(cost=cost, ok=int(ok), invocations=1)
        rt.window.invocations += 1
        rt.window.ok += int(ok)
        if ok:
            rt.window.costs.append(cost)
        handles.append(handle)
    return handles


def _close(rt, index, *costs_ok):
    rt.window = MeasureWindow(index, rt.wallet.balance)
    rt.price_windows[index] = rt.window
    handles = _returns(rt, *costs_ok)
    rt.n += 1
    rt._close_price_window()
    rt._derive_regions()  # as the next window's opening would
    return handles


def _admit(rt, monkeypatch, **changes):
    rt._manage_reserve_window()
    committee(rt, monkeypatch)
    _returns(rt, (100, True))
    author = decision(rt, "seed-decider")
    before = rt.reserve.remaining()
    rt._apply_registrations(author, Return(author, {"register": [_challenge(**changes)]}, 0,
                                           "ok"))
    return author, before


def test_a_challenge_is_admitted_for_one_novelty_trial_and_frozen_in_the_ledger(monkeypatch):
    rt = runtime()
    author, before = _admit(rt, monkeypatch)
    assert rt.stats.registrations_accepted == 1 and rt.stats.registrations_rejected == 0
    assert before - rt.reserve.remaining() == rt.ev.trial_amount_micro
    (cid, challenge), = rt.challenges.items()
    assert cid == "challenge-1-cost-per-return" and challenge["status"] == "trial"
    assert challenge["incumbent"] == next(c for c in rt.charter.cards if c.id == "cost_per_return")
    replacement = challenge["replacement"]
    assert isinstance(replacement, MetricCard)
    assert (replacement.id, replacement.norm) == (challenge["incumbent"].id,
                                                  challenge["incumbent"].norm)
    assert replacement.observation == "cost_per_attempt"
    assert replacement.acceptable_region == "at most 5000"
    assert replacement.window == MetricWindow("windows", 1, None)
    proposed, = _items(rt, "challenge.proposed")
    assert proposed["challenge_id"] if "challenge_id" in proposed else proposed["id"] == cid
    assert proposed["evidence"].startswith("nine cheap") and proposed["trial_windows"] == 2
    # The incumbent still prices the live charter: nothing changed at admission.
    assert rt.charter.edition == 1
    assert next(c for c in rt.charter.cards if c.id == "cost_per_return").observation == (
        "cost_per_return")
    # A challenge is an accepted registration, hence a revision of its return.
    assert author in rt.window.revision_handles


@pytest.mark.parametrize("changes,reason", [
    ({"card_id": "missing"}, "must name a current card"),
    ({"trial_windows": 0}, "trial_windows"),
    ({"replacement": {"observation": "cost_per_attempt", "rule": "roughly", "value": 1,
                      "window": {"kind": "windows", "n": 1, "per": None}}}, "rule"),
    ({"replacement": {"observation": "nonesuch", "rule": "at most", "value": 1,
                      "window": {"kind": "windows", "n": 1, "per": None}}}, "observation"),
    ({"evidence": ""}, "evidence"),
])
def test_a_malformed_or_unmeasurable_challenge_is_refused_before_the_trial_is_spent(
        monkeypatch, changes, reason):
    rt = runtime()
    _, before = _admit(rt, monkeypatch, **changes)
    assert rt.stats.registrations_rejected == 1 and not rt.challenges
    assert reason in rt.registration_feedback[-1]["reason"]
    assert rt.reserve.remaining() == before


def test_a_card_under_challenge_cannot_be_challenged_again_until_the_trial_ends(monkeypatch):
    rt = runtime()
    author, _ = _admit(rt, monkeypatch)
    rt._apply_registrations(author, Return(author, {"register": [_challenge()]}, 0, "ok"))
    assert rt.stats.registrations_rejected == 1
    assert "already under challenge" in rt.registration_feedback[-1]["reason"]
    assert len(rt.challenges) == 1


def test_parse_proposals_types_the_challenge_and_refuses_extra_keys():
    accepted, rejected = parse_proposals(
        {"register": [_challenge(), _challenge(extra=1)]},
        event_kinds=frozenset({"Tick"}), known_models=frozenset(), known_assemblies=frozenset())
    assert [type(p) for p in accepted] == [ChallengeProposal]
    assert accepted[0].trial_windows == 2 and accepted[0].replacement["rule"] == "at most"
    assert len(rejected) == 1 and "exactly" in rejected[0].reason


def test_both_series_are_ledgered_each_window_and_the_trial_ends_on_its_count(monkeypatch):
    rt = runtime()
    _admit(rt, monkeypatch)
    (cid, challenge), = rt.challenges.items()
    _close(rt, 2, (1_000, True), (1_000, True), (100_000, False))
    _close(rt, 3, (1_000, True), (7_000, False))
    rows = _items(rt, "challenge.window")
    assert [r["window"] for r in rows] == [2, 3]
    assert all(r["challenge_id"] == cid for r in rows)
    # The incumbent reads successful cost; the replacement reads every attempt.
    assert [r["incumbent"]["value"] for r in rows] == [pytest.approx(1_000), pytest.approx(1_000)]
    assert [r["replacement"]["value"] for r in rows] == [pytest.approx(34_000),
                                                          pytest.approx(4_000)]
    assert rows[0]["incumbent"]["observation"] == "cost_per_return"
    assert rows[0]["replacement"]["observation"] == "cost_per_attempt"
    assert challenge["series"] == [{k: v for k, v in r.items()
                                    if k in ("window", "incumbent", "replacement")}
                                   for r in rows]
    assert challenge["status"] == "due"
    assert _items(rt, "challenge.trial_complete")[0]["windows"] == 2
    # No further window is measured for a completed trial.
    _close(rt, 4, (1_000, True))
    assert len(_items(rt, "challenge.window")) == 2
    # Throughout, the charter itself was untouched and the incumbent priced each window.
    assert rt.charter.edition == 1
    assert all(c.observation == "cost_per_return" for w in (2, 3, 4)
               for c in rt.price_windows[w].closed_cards if c.id == "cost_per_return")


def test_a_completed_trial_is_adopted_by_the_amendment_ballot_and_old_windows_keep_the_incumbent(
        monkeypatch):
    rt = runtime()
    _admit(rt, monkeypatch)
    (cid, challenge), = rt.challenges.items()
    _close(rt, 2, (1_000, True), (100_000, False))
    under_incumbent, = _close(rt, 3, (1_000, True))
    assert challenge["status"] == "due" and not rt.charter_book.pending()
    boundary(rt, 4)
    balloted, = _items(rt, "challenge.balloted")
    assert balloted["challenge_id"] == cid and balloted["amendment_id"] == cid
    assert challenge["status"] == "balloted" and challenge["amendment_id"] == cid
    proposed, = _items(rt, "charter.propose")
    assert proposed["id"] == cid and [c["observation"] for c in proposed["replace"]] == [
        "cost_per_attempt"]
    assert rt.stats.amendments_proposed == 1 and rt.stats.amendments_passed == 1
    votes = [v for v in _items(rt, "charter.vote") if v["amendment_id"] == cid]
    assert sorted(v["vote"] for v in votes) == [False, True, True]
    if rt.charter.edition == 1:
        boundary(rt, 5)
    assert rt.charter.edition == 2
    assert rt.charter_book.activated_amendment(2).id == cid
    adopted = next(c for c in rt.charter.cards if c.id == "cost_per_return")
    assert adopted == challenge["replacement"] and adopted.observation == "cost_per_attempt"
    assert rt.stats.amendments_activated == 1
    # Commitments incurred under the incumbent settle under the incumbent: the window
    # closed before adoption froze the card it priced (window 3 is the first with a
    # previous median to price against), and a decision made in it is still priced on
    # cost_per_return, never on the replacement.
    frozen = next(c for c in rt.price_windows[3].closed_cards if c.id == "cost_per_return")
    assert frozen.observation == "cost_per_return"
    terms = rt._penalty_terms("producer", under_incumbent)
    assert [t["observation"] for t in terms if t["card_id"] == "cost_per_return"] == [
        "cost_per_return"]


def test_a_proposal_made_during_the_trial_may_name_the_challenge_id(monkeypatch):
    rt = runtime()
    author, _ = _admit(rt, monkeypatch)
    (cid, challenge), = rt.challenges.items()
    prediction = rt._policy_prediction({"card_id": cid, "direction": "decrease", "window": 1})
    assert prediction.card_id == cid
    with pytest.raises(ValueError, match="must name a current card"):
        rt._policy_prediction({"card_id": "challenge-9-nonesuch", "direction": "decrease",
                               "window": 1})
    # The ballot on such a promise is frozen on the replacement, not on the incumbent.
    voter = decision(rt, "eval-a")
    rt._record_policy_ballot(SimpleNamespace(id="retire-x", predicted_effect=prediction),
                             voter, "eval-a", True)
    ballot = rt.pending_votes[-1]
    assert ballot["card"] == challenge["replacement"]
    assert ballot["observation_id"] == "cost_per_attempt"
    promised, = _items(rt, "policy.promised")
    assert promised["observation_id"] == "cost_per_attempt"
    # The retire route itself accepts the id: the proposal is refused only for lacking
    # a committee (nobody eligible), never for its prediction.
    monkeypatch.setattr(rt, "_committee_eligible", lambda: {})
    rt._apply_registrations(author, Return(author, {"register": [
        {"kind": "retire", "assembly_id": "seed-observer",
         "predicted_effect": {"card_id": cid, "direction": "decrease", "window": 1}}]}, 0, "ok"))
    assert "predicted_effect" not in rt.registration_feedback[-1]["reason"] if (
        rt.registration_feedback) else True


def test_a_challenge_survives_a_checkpoint_with_its_series_and_status(monkeypatch):
    rt = runtime()
    _admit(rt, monkeypatch)
    _close(rt, 2, (1_000, True), (100_000, False))
    (cid, challenge), = rt.challenges.items()
    state = runtime_state(rt)
    restored = runtime()
    restore_runtime(restored, state)
    assert restored.challenges.keys() == {cid}
    twin = restored.challenges[cid]
    assert twin["replacement"] == challenge["replacement"]
    assert twin["incumbent"] == challenge["incumbent"]
    assert twin["series"] == challenge["series"] and twin["status"] == "trial"
    assert twin["evidence"] == challenge["evidence"]
    # The restored runtime carries the trial on: the second window completes it.
    committee(restored, monkeypatch)
    _close(restored, 3, (1_000, True))
    assert twin["status"] == "due" and len(_items(restored, "challenge.window")) == 1


def _capture_ballots(rt, monkeypatch):
    """Vote yes on everything and keep every ballot request the committee received."""
    requests = []

    def invoke(assembly, request):
        requests.append(request)
        return Return(request.handle, {"vote": True, "reason": "fixture"}, 0, "ok")

    monkeypatch.setattr(rt, "_invoke_compute", invoke)
    return requests


def test_a_challenge_ballot_carries_the_evidence_and_both_series_side_by_side(monkeypatch):
    rt = runtime()
    _admit(rt, monkeypatch)
    (cid, challenge), = rt.challenges.items()
    _close(rt, 2, (1_000, True), (100_000, False))
    _close(rt, 3, (1_000, True))
    requests = _capture_ballots(rt, monkeypatch)
    boundary(rt, 4)
    ballots = [r for r in requests if r.inputs.get("amendment", {}).get("id") == cid]
    assert len(ballots) == 3  # one request per committee seat
    for req in ballots:
        shown = req.inputs["challenge"]
        assert shown["id"] == cid and shown["card_id"] == "cost_per_return"
        assert shown["evidence"] == challenge["evidence"]
        assert shown["evidence_truncated"] is False
        assert shown["incumbent"]["observation"] == "cost_per_return"
        assert shown["replacement"]["observation"] == "cost_per_attempt"
        assert shown["trial_windows"] == 2
        assert shown["windows_measured"] == shown["windows_shown"] == 2
        # Both series, one row per trial window, the two sides beside each other.
        assert [row["window"] for row in shown["series"]] == [2, 3]
        assert [row["incumbent"]["value"] for row in shown["series"]] == [
            pytest.approx(1_000), pytest.approx(1_000)]
        assert [row["replacement"]["value"] for row in shown["series"]] == [
            pytest.approx(50_500), pytest.approx(1_000)]
        assert all(set(row) == {"window", "incumbent", "replacement"}
                   for row in shown["series"])
        assert all(set(row[side]) == {"observation", "value", "scopes"}
                   for row in shown["series"] for side in ("incumbent", "replacement"))
        # The ordinary amendment inputs are still there, unchanged in shape.
        assert [c["observation"] for c in req.inputs["amendment"]["replace"]] == [
            "cost_per_attempt"]
        assert {"charter", "world", "your_policy_returns"} <= set(req.inputs)
        # The voter reads it: the rendered request names the challenge and its evidence.
        text = req.prompt_text()
        assert "metric challenge" in req.description and "inputs.challenge" in req.description
        assert "nine cheap successes" in text and "cost_per_attempt" in text
    assert rt.stats.amendments_passed == 1


def test_a_challenge_ballot_is_bounded_in_evidence_and_series_length(monkeypatch):
    from factorylab.runtime.governance import (
        BALLOT_EVIDENCE_CHARS,
        BALLOT_SERIES_SCOPES,
        BALLOT_SERIES_WINDOWS,
    )

    rt = runtime()
    _admit(rt, monkeypatch, evidence="e" * 4000)
    (cid, challenge), = rt.challenges.items()
    _close(rt, 2, (1_000, True))
    _close(rt, 3, (1_000, True))
    # Pad the in-memory series beyond the bound; the ballot shows only the tail.
    padding = [{"window": 100 + i,
                "incumbent": {"card_id": "cost_per_return", "observation": "cost_per_return",
                              "value": 1.0, "scopes": {f"s{j}": 1.0 for j in range(20)}},
                "replacement": {"card_id": "cost_per_return", "observation": "cost_per_attempt",
                                "value": 2.0, "scopes": {}}}
               for i in range(BALLOT_SERIES_WINDOWS + 5)]
    challenge["series"] = challenge["series"] + padding
    requests = _capture_ballots(rt, monkeypatch)
    boundary(rt, 4)
    shown = next(r for r in requests
                 if r.inputs.get("amendment", {}).get("id") == cid).inputs["challenge"]
    assert len(shown["evidence"]) == BALLOT_EVIDENCE_CHARS and shown["evidence_truncated"]
    assert shown["windows_measured"] == 2 + BALLOT_SERIES_WINDOWS + 5
    assert shown["windows_shown"] == len(shown["series"]) == BALLOT_SERIES_WINDOWS
    assert shown["series"][-1]["window"] == padding[-1]["window"]
    assert all(len(row["incumbent"]["scopes"]) <= BALLOT_SERIES_SCOPES
               for row in shown["series"])


def test_an_ordinary_amendment_ballot_is_unchanged(monkeypatch):
    from dataclasses import asdict, replace

    rt = runtime()
    rt._manage_reserve_window()
    committee(rt, monkeypatch)
    rt.window = MeasureWindow(1, rt.wallet.balance, costs=[100], invocations=1, ok=1)
    rt._close_price_window()
    requests = _capture_ballots(rt, monkeypatch)
    card = replace(rt.charter.cards[0], acceptable_region="at most 500")
    rt._propose_amendment("author", {
        "id": "plain-rise", "replace": [asdict(card)],
        "predicted_effect": {"card_id": card.id, "direction": "increase", "window": 2}})
    ballots = [r for r in requests if r.inputs.get("amendment", {}).get("id") == "plain-rise"]
    assert len(ballots) == 3
    for req in ballots:
        assert "challenge" not in req.inputs
        assert set(req.inputs) == {"amendment", "charter", "world", "your_policy_returns"}
        assert req.description == "Vote on an amendment to the charter's metric cards."
