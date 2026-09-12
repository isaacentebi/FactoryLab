"""A15: policy mistakes return to the same learner after observable consequences."""

from dataclasses import asdict, replace

from factorylab.cortex.request import Return
from factorylab.kernel.queue import PropensityRecord, SettleStatus
from factorylab.runtime.pricing import MeasureWindow
from factorylab.runtime.resume import restore_runtime, runtime_state
from tests.runtime.test_fidelity import decision, runtime


def proposal(rt, id="policy-test", k=2):
    card = replace(rt.charter.cards[0],
                   acceptable_region="below 400" if id == "policy-test" else "below 500")
    return {"id": id, "replace": [asdict(card)],
            "predicted_effect": {"card_id": card.id, "direction": "decrease", "window": k}}


def test_a15_bad_votes_lose_after_declared_window_and_survive_resume(monkeypatch):
    rt = runtime()
    rt._manage_reserve_window()
    rt.n = 10
    rt.window = MeasureWindow(1, rt.wallet.balance, costs=[100], invocations=1, ok=1)
    rt._close_price_window()
    members = list(rt.assemblies)[:5]
    monkeypatch.setattr(rt, "_committee_eligible", lambda: {
        a: rt.assemblies[a].spec.role for a in members
    })
    seen = []

    def ballot(assembly, request):
        seen.append(request)
        return Return(request.handle, {"vote": assembly != members[-1], "reason": "fixture"},
                      0, "ok")

    monkeypatch.setattr(rt, "_invoke_compute", ballot)
    rt._propose_amendment("external-author", proposal(rt))
    assert len(rt.pending_votes) == len(members)
    assert not any(rt.queue.history(v["handle"]) for v in rt.pending_votes)
    rt.clock.now_ns = (rt.m.timing.min_ratio * rt.ev.consequence_backstop_events
                       * rt.tick_clock.interval_ns)
    rt.window = MeasureWindow(2, rt.wallet.balance, costs=[200], invocations=1, ok=1)
    rt.n = max(rt.n, rt.cadence.earliest_event())  # A2: activation waits for fresh events too
    rt.cadence.advance(rt.n)
    rt._activate_charter_if_due()
    assert rt.charter.edition == 2
    assert all(v["baseline"] == 100 for v in rt.pending_votes)
    rt.n += 10
    rt._close_price_window()
    assert rt.pending_votes  # k=2 does not settle at the first post-activation close.
    restored = runtime()
    restore_runtime(restored, runtime_state(rt))
    handles = [(v["handle"], v["assembly"], v["vote"]) for v in rt.pending_votes]
    for current in (rt, restored):
        current.n += 10
        current.window = MeasureWindow(3, current.wallet.balance, costs=[300], invocations=1, ok=1)
        current._close_price_window()
        assert not current.pending_votes
        for handle, assembly, vote in handles:
            result = current.queue.history(handle)[-1]
            assert result.score == (0 if vote else 1)
            assert result.status is SettleStatus.SETTLED
            assert current.queue.get(handle).actor == f"assembly:{assembly}"
    assert runtime_state(rt) == runtime_state(restored)
    # Another ballot is offered the very same learner's prior policy return.
    rt.reserve.open_window(rt.clock.now_ns, rt.wallet.balance)
    rt._propose_amendment("external-author", proposal(rt, id="policy-next"))
    assert any(request.inputs["your_policy_returns"] for request in seen[len(members):])


def test_a15_proposer_is_excluded_and_noop_refused_before_vote_or_spend(monkeypatch):
    rt = runtime()
    rt._manage_reserve_window()
    for assembly in rt.assemblies:
        for _ in range(rt.m.committee.min_settled):
            decision(rt, assembly, settled=True)
    author = decision(rt, "seed-decider")
    before = rt.reserve.remaining()
    no_op = {"kind": "amendment", "id": "nothing-changes",
             "predicted_effect": {"card_id": "cost_per_return", "direction": "decrease",
                                  "window": 1}}
    rt._apply_registrations(author, Return(author, {"register": [no_op]}, 0, "ok"))
    assert rt.reserve.remaining() == before
    assert "unchanged" in rt.registration_feedback[-1]["reason"]
    assert not rt.vote_handles
    monkeypatch.setattr(rt, "_hold_vote", lambda *_: None)
    rt._propose_amendment(author, proposal(rt))
    committee = rt.charter_book._CharterBook__committees["policy-test"]
    assert committee.seats
    assert "seed-decider" not in {seat.assembly_id for seat in committee.seats}


def test_a15_self_requested_consequences_and_fast_settlements_do_not_qualify():
    rt = runtime()
    assembly = "seed-decider"
    for _ in range(rt.m.committee.min_settled + 1):
        parent = decision(rt, assembly)
        handle = rt.queue.open(
            actor=assembly, event_id="self", channel="verdict", parent_handle=parent,
            deadline_ns=10**18, cost_ceiling=0,
            propensity=PropensityRecord((assembly,), (1.0,), assembly, 0, assembly, "self"),
        )
        rt.handle_to_assembly[handle] = assembly
        rt.consequences.table = rt.consequences.table.start(handle, 1).finish(handle, 0)
        rt.consequences.table = rt.consequences.table.resolve(2, 10, {})
        rt.queue.settle(parent, channel="verdict", score=1, status=SettleStatus.SETTLED,
                        definition_version="fixture", sampling_ref=None)
    assert assembly not in rt._committee_eligible()
    for _ in range(rt.m.committee.min_settled):
        decision(rt, assembly, settled=True)
    assert assembly in rt._committee_eligible()


def test_a15_duplicate_observation_binding_is_refused_before_seating():
    rt = runtime()
    rt._manage_reserve_window()
    duplicate = replace(rt.charter.cards[1], id="duplicate-quality", answers_for="producer")
    rt._apply_registrations("author", Return("author", {"register": [{
        "kind": "amendment", "id": "double-charge", "add": [asdict(duplicate)],
        "predicted_effect": {"card_id": duplicate.id, "direction": "increase", "window": 1},
    }]}, 0, "ok"))
    assert "already named" in rt.registration_feedback[-1]["reason"]
    assert not rt.vote_handles


def all_yes_committee(rt, monkeypatch) -> list[str]:
    """Seat the same five eligible assemblies and have every seat vote yes."""
    members = list(rt.assemblies)[:5]
    monkeypatch.setattr(rt, "_committee_eligible",
                        lambda: {a: rt.assemblies[a].spec.role for a in members})
    monkeypatch.setattr(rt, "_invoke_compute", lambda assembly, request: Return(
        request.handle, {"vote": True, "reason": "fixture"}, 0, "ok"))
    return members


def boundary(rt, index: int) -> None:
    """Advance to the next activation boundary and offer it to governance."""
    rt.clock.now_ns += (rt.m.timing.min_ratio * rt.ev.consequence_backstop_events
                        * rt.tick_clock.interval_ns)
    rt.window = MeasureWindow(index, rt.wallet.balance, costs=[100], invocations=1, ok=1)
    rt.n = max(rt.n, rt.cadence.earliest_event())  # A2: activation waits for fresh events too
    rt.cadence.advance(rt.n)
    rt._activate_charter_if_due()


def refusals(rt) -> list[dict]:
    return [item for item in rt.ledger._recovery_items() if item["kind"] == "charter.refused"]


def ballots_on(rt, amendment_id: str) -> list[dict]:
    return [v for v in rt.pending_votes if v["amendment_id"] == amendment_id]


def test_a15_activation_conflict_is_refused_and_censors_the_amendment_ballots(monkeypatch):
    """A patch a prior activation made conflicting owes its seats a closed ballot."""
    rt = runtime()
    rt._manage_reserve_window()
    rt.n = 10
    rt.window = MeasureWindow(1, rt.wallet.balance, costs=[100], invocations=1, ok=1)
    rt._close_price_window()
    members = all_yes_committee(rt, monkeypatch)
    skill = rt.charter.cards[2]
    # Each is valid against edition 1: "producer" and "evaluator" are distinct roles.
    rt._propose_amendment("external-author", {
        "id": "skill-for-all", "replace": [asdict(replace(skill, answers_for="all"))],
        "predicted_effect": {"card_id": skill.id, "direction": "increase", "window": 1}})
    shadow = replace(skill, id="shadow-skill", answers_for="producer")
    rt._propose_amendment("external-author", {
        "id": "shadow-skill", "add": [asdict(shadow)],
        "predicted_effect": {"card_id": shadow.id, "direction": "increase", "window": 1}})
    assert len(ballots_on(rt, "shadow-skill")) == len(members)
    handles = [v["handle"] for v in ballots_on(rt, "shadow-skill")]

    boundary(rt, 2)
    assert rt.charter.edition == 2  # The widened card now answers for every role.
    assert all(v["activation_window"] == 2 for v in ballots_on(rt, "skill-for-all"))
    assert all(v["activation_window"] is None for v in ballots_on(rt, "shadow-skill"))

    boundary(rt, 3)
    assert rt.charter.edition == 2  # The conflicting patch produced no edition.
    assert [(item["amendment_id"], "already named" in item["reason"])
            for item in refusals(rt)] == [("shadow-skill", True)]
    assert not ballots_on(rt, "shadow-skill")
    for handle in handles:
        result = rt.queue.history(handle)[-1]
        assert result.status is SettleStatus.CENSORED
        assert result.score == 0
    # The refused card is released, so the window close cannot retain its samples.
    rt.n += 10
    rt._close_price_window()
    assert shadow.id not in rt.card_samples.values


def test_a15_second_amendment_making_no_further_change_is_refused_at_activation(monkeypatch):
    """Two passed amendments with the same patch: the second leaves the edition unchanged."""
    rt = runtime()
    rt._manage_reserve_window()
    rt.n = 10
    rt.window = MeasureWindow(1, rt.wallet.balance, costs=[100], invocations=1, ok=1)
    rt._close_price_window()
    all_yes_committee(rt, monkeypatch)
    cheaper = asdict(replace(rt.charter.cards[0], acceptable_region="below 400"))
    for amendment_id in ("cheaper-returns", "cheaper-returns-twin"):
        rt._propose_amendment("external-author", {
            "id": amendment_id, "replace": [cheaper],
            "predicted_effect": {"card_id": cheaper["id"], "direction": "decrease", "window": 1}})

    boundary(rt, 2)
    assert rt.charter.edition == 2
    assert rt.charter.cards[0].acceptable_region == "below 400"
    handles = [v["handle"] for v in ballots_on(rt, "cheaper-returns-twin")]

    boundary(rt, 3)
    # Identical to the current edition: no third edition, and no ballots left to grade.
    assert rt.charter.edition == 2
    assert len(rt.charter_book.editions()) == 2
    assert [(item["amendment_id"], item["reason"]) for item in refusals(rt)] == [
        ("cheaper-returns-twin", "amendment leaves the charter unchanged")
    ]
    assert not ballots_on(rt, "cheaper-returns-twin")
    assert all(rt.queue.history(h)[-1].status is SettleStatus.CENSORED for h in handles)
