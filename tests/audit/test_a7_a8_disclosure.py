"""A7/A8: public schematics contain operative contracts without a wiring map."""

import json

from factorylab.cortex.registration import AssemblyProposal
from tests.runtime.test_fidelity import runtime


def test_a8_initial_block_has_counts_and_no_assembly_model_edges():
    """Ids and contracts are public schematics (round three, T7); what A8 seals is the
    wiring: which model and prompt sit behind an id, and the routers' menus."""
    rt = runtime()
    block = rt._world_block()
    for view in ("assemblies", "routers"):
        assert all(set(row) == {"event_kind", "count"} for row in block[view])
    assert all(set(row) == {"id", "version", "accepts", "emits"} for row in block["catalogue"])
    assert {row["id"] for row in block["catalogue"]} == {a.id for a in rt.m.assemblies}
    text = json.dumps(block)
    assert '"menu"' not in text
    for assembly in rt.m.assemblies:
        assert f'"{assembly.id}": ' not in text  # an id keys nothing: no id -> model edge
        assert assembly.model_id not in json.dumps(block["catalogue"])


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
