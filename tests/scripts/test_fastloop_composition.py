"""The fast-loop scorecard counts composition (W4) from the diary alone."""

from pathlib import Path

import pytest

from factorylab.cortex.sandbox import jail_available
from scripts import fastloop

WORLD = Path(__file__).parents[2] / "worlds" / "edition6-testnet-rehearsal.toml"


@pytest.mark.gate
def test_the_scripted_population_reaches_requests_tools_and_both_credits(tmp_path):
    """The free tier's plumbing covers the composition path end to end, on every seed.

    Requests and forwarded propensities are reached on every seed. Executor credits,
    cross-lineage tool calls and builder credits depend on scripted verdicts that vary
    with the seated model ids, so on any one 80-tick seed each can be zero (edition 6
    since #135, seeds 1-8: 0 to 1, 0 to 4 and 0 to 2 a seed). The path is asserted over
    four seeds, never a chosen one: each reaches requests and forwarded propensities,
    and between them executors and builders are credited and tools are called across
    lineages.
    """
    executor_credits = cross_lineage_calls = builder_credits = 0
    for seed in (1, 2, 3, 4):
        card = fastloop.run("scripted", 80, WORLD, tmp_path / f"s{seed}", cap_usd="2",
                            seed=seed)
        assert card["status"] == "completed", card.get("error")
        composed = card["composition"]
        assert composed["child_requests_by_kind"].get("ProducerReturn", 0) >= 1, seed
        assert composed["child_requests_forwarding_propensity"] >= 1, seed
        executor_credits += composed["executor_credits_paid"]
        cross_lineage_calls += composed["population_tool_calls_by_non_builder"]
        builder_credits += composed["tool_builder_credits"]
    assert executor_credits >= 1
    if jail_available():
        assert cross_lineage_calls >= 1
        assert builder_credits >= 1


def test_the_composition_card_counts_requests_credits_and_cross_lineage_tool_calls():
    events = [
        {"kind": "request.child", "requested": "ProducerReturn",
         "forwarded_propensity": {"request": 0.5, "hold": 0.5}},
        {"kind": "request.child", "requested": "ProducerReturn", "forwarded_propensity": None},
        {"kind": "request.child", "requested": "self"},
        {"kind": "requests.refused", "target": "Missing"},
        {"kind": "credit.composed"}, {"kind": "credit.composed"},
        {"kind": "credit.withheld"},
        {"kind": "composed.settled", "credit": 0.4},
        {"kind": "composed.settled", "credit": None, "tool_use_credit": 0.7},
        {"kind": "tool.population_call", "across_lineage": True},
        {"kind": "tool.population_call", "across_lineage": False},
        {"kind": "credit.tool", "credit": 0.7, "applied": "hold"},
        {"kind": "credit.tool", "credit": 0.9, "applied": "late"},
    ]
    card = fastloop.composition(events)
    assert card == {
        "child_requests_by_kind": {"ProducerReturn": 2, "self": 1},
        "child_requests_forwarding_propensity": 1,
        "requests_refused": 1,
        "executor_credits_held": 2,
        "executor_credits_withheld_same_lineage": 1,
        "executor_credits_paid": 1,
        "executor_credit_sum": 0.4,
        "population_tool_calls": 2,
        "population_tool_calls_by_non_builder": 1,
        "tool_builder_credits": 1,
        "tool_builder_credit_sum": 0.7,
        "tool_builder_settlements_credited": 1,
    }
    assert fastloop.scorecard(events)["composition"] == card
    combined = fastloop.combine([{"composition": card}, {"composition": card}])
    assert combined["composition"]["executor_credits_paid"] == 2
    assert combined["composition"]["child_requests_by_kind"] == {"ProducerReturn": 4, "self": 2}
