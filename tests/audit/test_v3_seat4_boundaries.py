"""Seat four: deterministic intended-behavior assertions for reproduced boundaries."""

import json
from dataclasses import replace
from types import SimpleNamespace

import pytest

from factorylab.charter.amendment import PredictedEffect
from factorylab.charter.charter import MetricCard
from factorylab.charter.committee import Committee, Seat
from factorylab.charter.controller import CardRegion
from factorylab.charter.measurement import CardSamples, measure_card
from factorylab.cortex.registration import ConnectorProposal
from factorylab.cortex.request import Return
from factorylab.kernel.queue import PropensityRecord
from factorylab.runtime.immune import close_window
from factorylab.runtime.pricing import MeasureWindow, PricingMixin
from factorylab.settlement.lots import LotTable
from factorylab.world.models import ModelResponse
from tests.conftest import make_runtime


def _decision(rt, assembly, *, channel="verdict"):
    actor = f"seat4:{assembly}:{rt.stats.decisions}"
    rt.stats.decisions += 1
    handle = rt.queue.open(
        actor=actor,
        event_id="seat4-input",
        propensity=PropensityRecord((assembly,), (1.0,), assembly, 0, actor, "test"),
        channel=channel,
        deadline_ns=10**12,
        parent_handle=None,
        cost_ceiling=1_000_000,
    )
    rt.handle_to_assembly[handle] = assembly
    rt._start_return(handle)
    return handle


@pytest.mark.parametrize("market,coin", [("perp", "BTC"), ("spot", "BTC/USDC")])
def test_one_decision_round_trip_counts_its_profit_once(market, coin):
    """A decision with 0.60 USD trading profit and 1 USD compute does not pay off."""
    table = LotTable().start("round-trip", 0)
    table = table.order("opening", "round-trip", "1").fill(
        order_id="opening",
        coin=coin,
        is_buy=True,
        size="1",
        px="100",
        fee_usd="0",
        market=market,
    )
    table = table.order("closing", "round-trip", "1").fill(
        order_id="closing",
        coin=coin,
        is_buy=False,
        size="1",
        px="100.60",
        fee_usd="0",
        market=market,
    )
    payoff = table.finish("round-trip", 1_000_000).resolve(1, 200, {}).account("round-trip").payoff
    assert (payoff.net_micro, payoff.y) == (600_000, 0)


@pytest.mark.xfail(strict=False, reason="round three, open: docs/audits/v3/triage.md")
def test_evaluator_cost_card_attributes_its_measured_cost():
    """The same evaluator cost selected for measurement supplies its penalty share."""
    card = MetricCard(
        "judge-cost",
        "care with scarce resources",
        "Judge response cost",
        "micro-USD",
        {"kind": "returns", "n": 1, "per": "role"},
        "at most 500",
        "cost_per_return",
        "evaluator",
    )
    samples = CardSamples()
    samples.returned(
        handle="judge",
        assembly="eval-a",
        role="evaluator",
        window=1,
        ret=Return("judge", {"verdict": 0.8}, 2_000, "ok"),
    )
    assert measure_card(card, samples) == {"evaluator": 2_000.0}
    window = MeasureWindow(1, 100_000_000)
    window.decisions["judge"] = {
        "role": "evaluator",
        "cost": 2_000,
        "ok": 1,
        "invocations": 1,
        "tool_calls": 0,
        "notional_micro": 0,
    }
    region = CardRegion(card.id, "max", None, 500, 1_000_000)
    share = PricingMixin._decision_share(
        window,
        "judge",
        card.observation,
        card.answers_for,
        region,
        2_000,
    )
    assert share == 1.0


@pytest.mark.xfail(strict=False, reason="round three, open: docs/audits/v3/triage.md")
def test_immune_uses_the_cards_configured_sample():
    """A compliant 100-return card cannot become stable failure from one bad return."""
    rt = make_runtime()
    card = MetricCard(
        "formation",
        "truthful commitments",
        "Valid responses",
        "fraction",
        {"kind": "returns", "n": 100, "per": None},
        "at least 0.9",
        "well_formed_rate",
        "all",
    )
    rt.charter = replace(rt.charter, cards=(card,))
    rt.regions = {card.id: CardRegion(card.id, "min", 0.9, None, 1)}
    rt.controller.register_pending(card.id)
    rt.controller.update_region(rt.regions[card.id])
    samples = CardSamples()
    for i in range(100):
        samples.returned(
            handle=f"sample-{i}",
            assembly="seed-decider",
            role="producer",
            window=0,
            ret=Return(f"sample-{i}", {}, 1, "ok"),
        )
    rt.card_samples = samples
    for i in range(1, rt.m.immune.k + 1):
        samples.returned(
            handle=f"bad-{i}",
            assembly="seed-decider",
            role="producer",
            window=i,
            ret=Return(f"bad-{i}", {}, 1, "malformed"),
        )
        measured = measure_card(card, samples)["all"]
        assert measured >= 0.9
        rt.card_samples.values = {card.id: measured}
        rt.window = MeasureWindow(i, 100_000_000, invocations=1, ok=0)
        rt.window.closed_values = {card.id: measured}
        rt.window.closed_regions = dict(rt.regions)
        close_window(rt, {"well_formed_rate": 0.0, "registrations": 0, "revision_rate": 0})
    assert not rt.stats.pathologies["stable_failure"]


def test_settled_terminal_meta_consequences_qualify_for_committee():
    """Completed terminal meta consequence scores count toward sortition experience."""
    rt = make_runtime()
    for i in range(rt.m.committee.min_settled):
        handle = _decision(rt, "meta-a", channel="fast")
        rt.consequences.finish(handle, 0)
        rt._settle_meta_consequence(handle, 0.8, 1, f"forecast-{i}")
    assert rt.stats.consequences_by_assembly["meta-a"] == rt.m.committee.min_settled
    assert "meta-a" in rt._committee_eligible()


def test_policy_ballot_has_no_venue_write_authority(monkeypatch):
    """A metered policy ballot remains a judging request even for a producer assembly."""
    rt = make_runtime()
    calls = []

    def complete(request):
        calls.append(request)
        body = {"vote": True, "reason": "source is useful"}
        if len(calls) == 1:
            body["tool_calls"] = [
                {
                    "tool": "venue.place_market",
                    "args": {"coin": "BTC", "side": "buy", "size": "0.0001"},
                }
            ]
        return ModelResponse(request.model_id, json.dumps(body), 1, 1, "stop")

    monkeypatch.setattr(rt.provider.target, "complete", complete)
    committee = Committee("source-vote", 1, (Seat("seat-1", "seed-decider", "producer"),))
    rt._hold_vote(
        SimpleNamespace(
            id="source-vote",
            proposer_handle=None,
            predicted_effect=PredictedEffect("cost_per_return", "decrease", 1),
        ),
        committee,
        connector=ConnectorProposal("weather", "Weather", "https://example.com"),
    )
    assert len(calls) == 2
    assert rt.exchange.fills(0) == []


def test_registered_observation_can_enter_a_charter_proposal():
    """A measurable registered observation stays available through charter validation."""
    rt = make_runtime()
    rt._manage_reserve_window()
    rt.registered_observations["fresh-measure"] = {
        "description": "Public window measurement",
        "units": "fraction",
        "unit_range": [0, 1],
        "code": "def observe(facts): return 0.5",
        "version": 1,
        "provenance": "population",
        "history": [1],
    }
    rt.observation_runner = SimpleNamespace(run=lambda code, facts: (0.5, None))
    handle = _decision(rt, "seed-decider")
    card = {
        "id": "fresh-card",
        "norm": "useful inquiry",
        "description": "Population-defined measurement",
        "units": "fraction",
        "window": {"kind": "windows", "n": 1, "per": None},
        "acceptable_region": "at least 0.4",
        "observation": "fresh-measure",
        "answers_for": "producer",
    }
    rt._propose_amendment(
        handle,
        {
            "id": "fresh-amendment",
            "add": [card],
            "predicted_effect": {"card_id": "fresh-card", "direction": "increase", "window": 1},
        },
    )
    assert rt.stats.amendments_proposed == 1
