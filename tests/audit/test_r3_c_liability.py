"""Round three governance liability uses the promised card's acceptable region."""

from dataclasses import asdict, replace

import pytest

from factorylab.cortex.request import Return
from factorylab.kernel.queue import SettleStatus
from factorylab.runtime.pricing import MeasureWindow
from tests.audit.test_a15_liability import boundary
from tests.conftest import make_runtime
from tests.runtime.test_fidelity import decision, runtime


def committee(rt, monkeypatch):
    monkeypatch.setattr(
        rt,
        "_committee_eligible",
        lambda: {"eval-a": "evaluator", "eval-b": "evaluator", "meta-a": "meta"},
    )
    monkeypatch.setattr(
        rt,
        "_invoke_compute",
        lambda assembly, request: Return(
            request.handle, {"vote": assembly != "meta-a", "reason": "fixture"}, 0, "ok"
        ),
    )


def cost_card(rt):
    return replace(rt.charter.cards[0], acceptable_region="at most 500")


def effect(card_id="cost_per_return"):
    return {"card_id": card_id, "direction": "increase", "window": 2}


@pytest.mark.parametrize("value,yes_score", [(1000, 0), (500, 1), (100, 1)])
def test_amendment_votes_score_the_frozen_region(monkeypatch, value, yes_score):
    rt = runtime()
    rt._manage_reserve_window()
    committee(rt, monkeypatch)
    rt.window = MeasureWindow(1, rt.wallet.balance, costs=[100], invocations=1, ok=1)
    rt._close_price_window()
    rt._propose_amendment(
        "author",
        {"id": "harmful-rise", "replace": [asdict(cost_card(rt))], "predicted_effect": effect()},
    )
    votes = list(rt.pending_votes)
    boundary(rt, 2)
    # Changing the live region cannot rewrite the liability accepted at the vote.
    rt.charter = replace(
        rt.charter, cards=(replace(cost_card(rt), acceptable_region="at most 2000"),)
    )
    rt.window = MeasureWindow(2, rt.wallet.balance, costs=[value], invocations=1, ok=1)
    rt.n += 1
    rt._close_price_window()
    assert all(not rt.queue.history(v["handle"]) for v in votes)
    rt.window = MeasureWindow(3, rt.wallet.balance, costs=[value], invocations=1, ok=1)
    rt.n += 1
    rt._close_price_window()
    for vote in votes:
        result = rt.queue.history(vote["handle"])[-1]
        assert result.status is SettleStatus.SETTLED
        assert result.score == (yes_score if vote["vote"] else 1 - yes_score)


@pytest.mark.parametrize("kind", ["connector", "retire"])
@pytest.mark.parametrize(
    "prediction", [None, {}, {"card_id": "missing", "direction": "increase", "window": 1}]
)
def test_policy_proposals_require_a_measurable_prediction(monkeypatch, kind, prediction):
    rt = make_runtime()
    rt._manage_reserve_window()
    committee(rt, monkeypatch)
    author = decision(rt, "seed-decider")
    item = (
        {
            "kind": "connector",
            "id": "source",
            "description": "Data",
            "origin": "https://example.org",
        }
        if kind == "connector"
        else {"kind": "retire", "assembly_id": "seed-observer"}
    )
    if prediction is not None:
        item["predicted_effect"] = prediction
    before = rt.reserve.remaining()
    rt._apply_registrations(author, Return(author, {"register": [item]}, 0, "ok"))
    assert rt.stats.registrations_rejected == 1
    assert "predicted_effect" in rt.registration_feedback[-1]["reason"]
    assert not rt.vote_handles and rt.reserve.remaining() == before


@pytest.mark.parametrize("kind", ["connector", "retire"])
def test_connector_and_retirement_votes_have_delayed_liability(monkeypatch, kind):
    rt = runtime()
    rt._manage_reserve_window()
    committee(rt, monkeypatch)
    card = cost_card(rt)
    rt.charter = replace(rt.charter, cards=(card,))
    rt.window = MeasureWindow(1, rt.wallet.balance, costs=[100], invocations=1, ok=1)
    rt._close_price_window()
    author = decision(rt, "seed-decider")
    item = (
        {
            "kind": "connector",
            "id": "source",
            "description": "Data",
            "origin": "https://example.org",
        }
        if kind == "connector"
        else {"kind": "retire", "assembly_id": "seed-observer"}
    )
    item["predicted_effect"] = effect()
    rt._apply_registrations(author, Return(author, {"register": [item]}, 0, "ok"))
    assert rt.stats.registrations_accepted == 1, rt.registration_feedback
    votes = list(rt.pending_votes)
    assert len(votes) == 3
    if kind == "retire":
        assert all(v["activation_window"] is None for v in votes)
        boundary(rt, 2)
        assert "seed-observer" in rt.retired_assemblies
    else:
        assert rt.registry.get("connector:source").version == 1
    activation = votes[0]["activation_window"]
    rt.window = MeasureWindow(activation, rt.wallet.balance, costs=[1000], invocations=1, ok=1)
    rt.n += 1
    rt._close_price_window()
    assert all(not rt.queue.history(v["handle"]) for v in votes)
    rt.window = MeasureWindow(activation + 1, rt.wallet.balance, costs=[1000], invocations=1, ok=1)
    rt.n += 1
    rt._close_price_window()
    for vote in votes:
        result = rt.queue.history(vote["handle"])[-1]
        assert result.status is SettleStatus.SETTLED
        assert result.score == (0 if vote["vote"] else 1)
