"""A scoped card's violation is priced onto the seat that caused it, not split over everyone.

Edition 3's ``censorship-bound`` card measures ``avoidably_unresolved_share`` per
assembly: its violation belongs to the seat whose accepted commitments went
unresolved. The generic floor-split charged every decision in the window about the
same, guilty or not; these tests pin that the charge now lands on the violating
seat, in proportion to what it contributed, and that the decision which left the
commitment unresolved is priced instead of credited a free neutral.
"""

from dataclasses import replace

import pytest

from factorylab.charter.charter import MetricCard
from factorylab.charter.windows import MetricWindow
from factorylab.kernel.queue import LearningReturn, PropensityRecord, SettleStatus
from factorylab.runtime import pricing
from factorylab.runtime.feedback import _priced
from factorylab.runtime.loop import Runtime
from factorylab.runtime.pricing import UNRESOLVED_PRICED
from factorylab.runtime.worlds import load_manifest

GUILTY, PARTLY, INNOCENT = "eval-a", "eval-b", "eval-c"


def _card(per="assembly"):
    return MetricCard(
        id="censorship-bound", norm="truthful commitments",
        description="Avoidably unresolved accepted commitments.", units="fraction",
        window=MetricWindow("forecasts", 4, per), acceptable_region="at most 0.30",
        observation="avoidably_unresolved_share", answers_for="all")


def _runtime(card):
    seed = load_manifest("scripted")
    manifest = replace(seed, charter=replace(seed.charter, cards=(card,)))
    rt = Runtime(manifest, events=1, seed=1, initial_balance_micro=None,
                 ledger_path=None, drip=False, router_gamma=0.1)
    rt._derive_regions()
    rt.controller.set_price(card.id, 0.8, amendment_id="test")
    return rt


def _decision(rt, assembly, channel="consequence"):
    handle = rt.queue.open(
        actor="test-router", event_id="test", channel=channel, deadline_ns=10**18,
        parent_handle=None, cost_ceiling=0,
        propensity=PropensityRecord((assembly,), (1.0,), assembly, 0, "test-router", "state"),
    )
    rt.handle_to_assembly[handle] = assembly
    sample = rt._contribution(handle, "evaluator")
    sample["invocations"] = sample["ok"] = 1
    return handle


def _commitments(rt, assembly, censored: int, n: int = 4):
    for i in range(n):
        rt.card_samples.forecasts.append({
            "handle": f"f-{assembly}-{i}", "assembly": assembly, "role": "evaluator",
            "subject_handle": "s", "subject_assembly": "seed-decider",
            "subject_role": "producer", "window": rt.window.index, "skill": None,
            "predicate": "wallet_up", "y": None if i < censored else 1,
            "status": "censored" if i < censored else "settled", "verdict": None,
            "excluded": None,
        })


def _closed(card, monkeypatch):
    """A closed window where one seat left every commitment unresolved and one half."""
    monkeypatch.setattr(pricing, "close_window", lambda *_a: None)
    rt = _runtime(card)
    handles = {seat: [_decision(rt, seat) for _ in range(2)]
               for seat in (GUILTY, PARTLY, INNOCENT)}
    _commitments(rt, GUILTY, censored=4)
    _commitments(rt, PARTLY, censored=2)
    _commitments(rt, INNOCENT, censored=0)
    rt._close_price_window()
    return rt, handles


def test_blame_lands_on_the_violating_seats_in_proportion_and_not_on_the_compliant(
        monkeypatch):
    rt, handles = _closed(_card(), monkeypatch)
    assert rt.window.closed_scopes["censorship-bound"] == {
        GUILTY: 1.0, PARTLY: 0.5, INNOCENT: 0.0}
    shares = {seat: [rt._penalty_terms("all", h)[0]["share"] for h in hs]
              for seat, hs in handles.items()}
    penalties = {seat: [rt._penalty_for("all", h) for h in hs] for seat, hs in handles.items()}
    # Distances outside "at most 0.30": 7/3 and 2/3 of the bound, so 7/9 and 2/9 of it.
    assert shares[GUILTY] == pytest.approx([7 / 9 / 2] * 2)
    assert shares[PARTLY] == pytest.approx([2 / 9 / 2] * 2)
    assert shares[INNOCENT] == [0.0, 0.0]
    assert all(p > 0 for p in penalties[GUILTY] + penalties[PARTLY])
    assert penalties[INNOCENT] == [0.0, 0.0]
    assert penalties[GUILTY][0] == pytest.approx(3.5 * penalties[PARTLY][0])
    term = rt._penalty_terms("all", handles[GUILTY][0])[0]
    assert term["owner"] == GUILTY and term["lambda"] > 0


def test_settled_score_of_the_innocent_seat_is_untouched(monkeypatch):
    rt, handles = _closed(_card(), monkeypatch)
    rt._settle_priced(handles[INNOCENT][0], channel="consequence", score=0.9,
                      definition_version="t", sampling_ref=None, cards="evaluator")
    rt._settle_priced(handles[GUILTY][0], channel="consequence", score=0.9,
                      definition_version="t", sampling_ref=None, cards="evaluator")
    assert rt.queue.history(handles[INNOCENT][0])[-1].score == 0.9
    assert rt.queue.history(handles[GUILTY][0])[-1].score < 0.9


def test_a_violation_with_no_attributable_owner_keeps_the_generic_floor_split(monkeypatch):
    rt, handles = _closed(_card(per=None), monkeypatch)
    everyone = [h for hs in handles.values() for h in hs]
    shares = {rt._penalty_terms("all", h)[0]["share"] for h in everyone}
    assert shares == {max(rt.m.prices.min_blame_share, 1 / len(everyone))}
    assert all("owner" not in rt._penalty_terms("all", h)[0] for h in everyone)


def test_an_avoidably_unresolved_commitment_is_priced_for_its_owner(monkeypatch):
    """The decision that left it unresolved settles censored, carrying its price."""
    rt, handles = _closed(_card(), monkeypatch)
    owner, bystander = handles[GUILTY][0], handles[INNOCENT][0]
    for handle in (owner, bystander):
        rt._settle_priced(handle, channel="consequence", score=0.0, definition_version="t",
                          sampling_ref=None, cards="evaluator", unresolved=("f-1",))
    charged = rt.queue.history(owner)[-1]
    assert charged.status is SettleStatus.CENSORED
    assert charged.definition_version == UNRESOLVED_PRICED
    assert charged.score == pytest.approx(rt._penalty_for("evaluator", owner)) and charged.score > 0
    assert rt.queue.history(bystander)[-1].score == 0.0
    entry = [i for i in rt.ledger._recovery_items() if i["kind"] == "price.penalty"][0]
    assert entry["raw"] is None and entry["unresolved"] == ["f-1"]
    # Its learners are credited the neutral estimate less that price, never a zero score.
    assert _priced(0.6, charged) == pytest.approx(0.6 - charged.score)
    assert _priced(None, charged) is None
    plain = LearningReturn("h", "consequence", 0.0, "forecast-mean-v1",
                           SettleStatus.CENSORED, None)
    assert _priced(0.6, plain) == 0.6


def test_forecast_return_with_an_unresolved_commitment_is_settled_priced(monkeypatch):
    """``_settle_forecast_returns`` routes an avoidably unresolved commitment to pricing."""
    rt, handles = _closed(_card(), monkeypatch)
    parent = handles[GUILTY][1]
    child = rt.queue.open(
        actor=GUILTY, event_id="forecast", channel="consequence", deadline_ns=10**18,
        parent_handle=parent, cost_ceiling=0,
        propensity=PropensityRecord((GUILTY,), (1.0,), GUILTY, 0, GUILTY, "test"))
    rt.queue.settle(child, channel="consequence", score=0.0, status=SettleStatus.CENSORED,
                    definition_version="brier-v1", sampling_ref=None)
    rt.return_kinds[parent] = "Verdict"
    rt.forecast_returns[parent] = {"handles": [child], "results": {}, "unresolved": [child]}
    rt._settle_forecast_returns()
    settled = rt.queue.history(parent)[-1]
    assert settled.definition_version == UNRESOLVED_PRICED and settled.score > 0
    assert parent not in rt.forecast_returns
