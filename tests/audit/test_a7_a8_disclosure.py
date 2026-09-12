"""A7/A8: public schematics contain operative contracts without a wiring map."""

import json

from factorylab.cortex.registration import AssemblyProposal
from tests.runtime.test_fidelity import runtime


def test_a7_every_role_reads_the_full_charter_and_current_mechanics():
    rt = runtime()
    rt._derive_regions()
    rt.controller.set_price("well_formed_rate", 0.7, amendment_id="fixture")
    block = rt._world_block()
    for role in ("producer", "evaluator", "meta", "antagonist"):
        request = rt._request("fixture", "fixture", {"world": block}, {}, 100, role)
        for card in rt.charter.cards:
            assert card.id in request.prompt_text()
        assert "0.7" in request.prompt_text()
    mechanics = block["mechanics"]
    assert mechanics["committee"]["seats"] == rt.m.committee.seats
    assert mechanics["committee"]["min_settled"] == rt.m.committee.min_settled
    assert mechanics["novelty"]["share"] == rt.m.novelty.share
    assert mechanics["controller"]["eta"] == rt.m.prices.eta
    assert mechanics["cascade"]["jitter_fraction"] == rt.m.timing.jitter_fraction
    assert mechanics["consequence_mix"] == rt.ev.consequence_share
    assert mechanics["tick_bounds_ns"]["max"] == rt.m.max_tick_ns


def test_a8_initial_block_has_counts_and_no_assembly_model_edges():
    rt = runtime()
    block = rt._world_block()
    for view in ("assemblies", "routers"):
        assert all(set(row) == {"event_kind", "count"} for row in block[view])
    text = json.dumps(block)
    assert '"menu"' not in text
    for assembly in rt.m.assemblies:
        assert assembly.id not in text


def test_a8_registration_discloses_contract_without_prompt_model_or_author():
    rt = runtime()
    rt._manage_reserve_window()
    rt._register("hidden-author", AssemblyProposal(
        id="new-public", model_id="fake-haiku", role="producer", accepts=("Tick",),
        system_prompt="PRIVATE PROMPT", max_tokens=128, effort="low",
    ))
    event = rt.internal[-1]
    # A1 makes emits, its custom schemas and the contract version part of the public
    # contract; the prompt, the model and the proposing handle stay private (A8).
    assert dict(event.payload) == {"kind": "assembly", "id": "new-public", "version": 1,
                                   "role": "producer", "accepts": ("Tick",),
                                   "emits": ("ProducerReturn",), "schemas": {}}
    assert "PRIVATE PROMPT" not in json.dumps(rt._world_block())
