"""Compact context retrieves public contracts without opening private state."""

from dataclasses import replace

from factorylab.kernel.queue import PropensityRecord
from factorylab.runtime.loop import Runtime
from factorylab.runtime.worlds import PromptSpec, load_manifest


def runtime(mode="compact"):
    manifest = replace(load_manifest("scripted"), prompt=PromptSpec(mode=mode))
    return Runtime(manifest, events=0, seed=1, initial_balance_micro=None,
                   ledger_path=None, drip=False, router_gamma=0.1)


def read(rt, section):
    seat = "seed-decider"
    handle = rt.queue.open(
        actor=seat, event_id="contract-read",
        propensity=PropensityRecord((seat,), (1.0,), seat, 0, seat, "test"),
        channel="verdict", deadline_ns=10**15, parent_handle=None, cost_ceiling=1000,
    )
    rt.handle_to_assembly[handle] = seat
    return rt._run_tool(seat, handle, {"tool": "world.read", "args": {"section": section}})


def test_retrieved_contract_matches_authoritative_current_section_without_model_call():
    rt = runtime()
    before = rt.wallet.balance
    result, cost = read(rt, "composition")
    assert result == {"section": "composition", "value": rt.institution_section("composition")}
    assert cost == 0 and rt.wallet.balance == before
    assert rt.stats.invocations_by_assembly == {}


def test_private_and_bulk_sections_are_not_retrievable():
    rt = runtime()
    for section in ("seats", "your_state", "custody", "stable_prefix", "*", ""):
        result, cost = read(rt, section)
        assert "error" in result
        assert cost == 0


def test_reference_control_does_not_gain_an_extra_capability():
    rt = runtime("reference")
    assert "world.read" not in rt.tool_specs
