"""A6: sample selectors execute and the survey cannot anchor on existing cards."""

import json
from dataclasses import asdict, replace

import pytest

from factorylab.charter.charter import MetricCard, seed_charter
from factorylab.charter.measurement import CardSamples, measure_card, measure_cards, preflight_card
from factorylab.charter.windows import MetricWindow
from factorylab.cortex.request import Return
from factorylab.runtime.pricing import MeasureWindow
from factorylab.runtime.worlds import manifest_from_dict
from scripts import draft_edition1 as draft
from tests.runtime.test_fidelity import runtime


def card(kind="returns", n=2, per=None, observation="cost_per_return"):
    return MetricCard("selected", seed_charter().norms[0], "Selected samples", "micro-USD",
                      MetricWindow(kind, n, per), "below 500", observation, "all")


def test_a6_return_windows_select_exact_count_and_scope():
    samples = CardSamples()
    for i, (assembly, role, cost) in enumerate([
        ("p1", "producer", 10), ("e1", "evaluator", 90),
        ("p2", "producer", 30), ("e1", "evaluator", 70), ("p1", "producer", 50),
    ]):
        samples.returned(handle=str(i), assembly=assembly, role=role, window=1,
                         ret=Return(str(i), {}, cost, "ok"))
    assert measure_card(card(), samples) == {"all": 60}
    assert measure_card(card(per="role"), samples) == {"producer": 40, "evaluator": 80}
    assert measure_card(card(per="assembly"), samples) == {"p1": 30, "e1": 80}
    assert measure_card(replace(card(per="role"), answers_for="producer"), samples) == {
        "producer": 40,
    }
    assert measure_card(card(n=6), samples) == {}


def test_a6_forecast_window_uses_recent_outcomes_not_lifetime_standing():
    samples = CardSamples(forecasts=[
        {"handle": str(i), "assembly": "judge", "role": "evaluator", "window": 1,
         "skill": skill, "predicate": "return_paid_off", "y": 0, "status": "settled"}
        for i, skill in enumerate([1.0, 0.2, -0.4])
    ])
    c = card("forecasts", observation="forecast_skill")
    assert measure_card(c, samples)["all"] == pytest.approx(-0.1)


def test_a6_closed_windows_recompute_rate_denominators():
    samples = CardSamples()
    c = card("windows", observation="well_formed_rate")
    assert measure_cards((c,), samples, MeasureWindow(1, 100, invocations=1, ok=1)) == {}
    assert measure_cards((c,), samples, MeasureWindow(2, 100, invocations=9, ok=0)) == {
        "selected": 0.1,
    }
    assert measure_cards((c,), samples, MeasureWindow(3, 100, invocations=1, ok=1)) == {
        "selected": 0.1,
    }


@pytest.mark.parametrize("window", ["last 100 returns", {"kind": "returns", "n": 2},
                                    {"kind": "returns", "n": True, "per": None},
                                    {"kind": "returns", "n": 0, "per": None}])
def test_a6_prose_and_malformed_windows_fail(window):
    with pytest.raises(ValueError, match="window"):
        replace(card(), window=window)


def test_a6_unmeasurable_window_is_publicly_refused_before_reservation():
    rt = runtime()
    rt._manage_reserve_window()
    c = card(observation="turnover")
    with pytest.raises(ValueError, match="cannot be measured"):
        preflight_card(c)
    before = rt.reserve.remaining()
    rt._apply_registrations("proposal", Return("proposal", {"register": [{
        "kind": "amendment", "id": "unmeasurable", "add": [asdict(c)],
        "predicted_effect": {"card_id": c.id, "direction": "decrease", "window": 1},
    }]}, 0, "ok"))
    assert rt.reserve.remaining() == before
    assert "cannot be measured" in rt._world_block()["registration_feedback"][-1]["reason"]
    assert not rt.charter_book.pending()


def test_a6_survey_unanchored_uncapped_and_bound_to_roster(monkeypatch):
    rt = runtime()
    c = replace(card(), description="EXISTING CARD ANCHOR")
    charter = replace(rt.charter, cards=(c,))
    world = {**rt._world_block(), "charter": charter.render()}
    prompt = draft.proposal_prompt(charter, world)
    assert "EXISTING CARD ANCHOR" not in prompt
    assert "up to three" not in prompt
    assert "maxItems" not in prompt.split("OUTCOME SCHEMA\n")[1]
    assert "EXISTING CARD ANCHOR" not in draft.vote_prompt(charter, world, [])
    raw = [{**asdict(replace(card(), id=f"card-{i}")), "reason": "fixture"} for i in range(5)]
    monkeypatch.setattr(draft, "complete", lambda *a: type("Response", (), {
        "text": json.dumps({"cards": raw}), "stop_reason": "end_turn",
    })())
    proposals = draft.collect_proposals(rt.m, None, None, charter, world, [])
    assert len(proposals) == 5 * len(rt.m.assemblies)
    assert all(p.card and p.problem is None for p in proposals)
    changed = replace(rt.m, assemblies=(replace(rt.m.assemblies[0], effort="low"),
                                        *rt.m.assemblies[1:]))
    assert draft.roster_hash(changed) != draft.roster_hash(rt.m)


def test_a6_draft_toml_round_trips_typed_null_scope():
    import tomllib

    from factorylab.runtime.worlds import WORLDS_DIR

    raw = tomllib.loads((WORLDS_DIR / "scripted.toml").read_text())
    raw.update(tomllib.loads(draft.render_toml([(card(), None)], seed_charter().norms)))
    assert manifest_from_dict(raw).charter.cards == (card(),)


def test_a6_cost_card_window_is_the_rolling_last_100_returns_not_the_reserve_window():
    """class3-codex finding 6: 100 zero-cost returns, then one 1,000-micro return in the next
    window. The declared window is the last 100 returns, so the observation is 10, not 1,000."""
    cost_card = next(c for c in seed_charter().cards if c.observation == "cost_per_return")
    assert cost_card.window == MetricWindow("returns", 100, "role")
    samples = CardSamples()
    for i in range(100):
        samples.returned(handle=f"a{i}", assembly="p1", role="producer", window=1,
                         ret=Return(f"a{i}", {}, 0, "ok"))
    measure_cards((cost_card,), samples, MeasureWindow(1, 100, invocations=100, ok=100))
    assert samples.scopes[cost_card.id] == {"producer": 0}
    samples.returned(handle="b", assembly="p1", role="producer", window=2,
                     ret=Return("b", {}, 1000, "ok"))
    measure_cards((cost_card,), samples, MeasureWindow(2, 100, invocations=1, ok=1))
    assert samples.scopes[cost_card.id] == {"producer": 10}
