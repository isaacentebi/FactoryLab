"""Fix pass 1, B13: the jail is real, the gate cannot be green without it, and a world
that offers population tools does not launch on a host where the jail cannot start.

Nothing here touches a network. The scripted-world evidence (a producer tool registered
and called, ``tool.call`` items in the diary) runs only where a jail exists.
"""

import json
from dataclasses import replace

import pytest

from factorylab.cortex import sandbox
from factorylab.kernel.ledger import Ledger
from factorylab.runtime.loop import run_world
from factorylab.runtime.worlds import load_manifest
from tests.cortex.test_jail import require_jail


def _items(path, manifest):
    return Ledger.reopen(path, manifest=json.loads(manifest.canonical_json()))._recovery_items()


def test_a_claimed_jail_that_cannot_start_fails_the_gate_instead_of_skipping(monkeypatch):
    """A host with the jail executable installed but unable to run confined code must
    fail the jail tests loudly; only a host with no jail at all may skip them."""
    monkeypatch.setattr(sandbox, "jail_installed", lambda: True)
    monkeypatch.setattr(sandbox, "jail_probe", lambda: "jailed interpreter exited -6")
    with pytest.raises(pytest.fail.Exception, match="claims a jail that cannot start"):
        require_jail()
    monkeypatch.setattr(sandbox, "jail_installed", lambda: False)
    with pytest.raises(pytest.skip.Exception):
        require_jail()


def test_run_refuses_a_world_offering_tools_when_the_jail_cannot_start(monkeypatch, tmp_path):
    """The world block would publish tools no proposal could obtain; nothing is written."""
    monkeypatch.setattr("factorylab.runtime.loop.jail_probe", lambda: "no jail on this host")
    m = load_manifest("scripted")
    assert m.tools.population_tool_micro_per_call > 0
    ledger = tmp_path / "w.jsonl"
    with pytest.raises(sandbox.NoJail, match="offers population tools"):
        run_world(m, events=3, seed=1, ledger_path=str(ledger))
    assert not ledger.exists() and not (tmp_path / "w.jsonl.key").exists()
    # A manifest that prices no population tools promises none and still launches.
    free = replace(m, tools=replace(m.tools, population_tool_micro_per_call=0))
    monkeypatch.setattr("factorylab.cortex.tools.jail_available", lambda: False)
    summary = run_world(free, events=3, seed=1)
    assert summary["stats"]["events"] >= 3


def test_scripted_world_registers_and_calls_a_population_tool_in_the_jail(tmp_path):
    """Spec v0.6 section 7.2's condition: one population tool registered and then called,
    with the calls and their results in the diary, not only in the summary."""
    require_jail()
    m = load_manifest("scripted")
    path = str(tmp_path / "w.jsonl")
    summary = run_world(m, events=500, seed=1, ledger_path=path)
    assert summary["stats"]["population_tools_registered"] >= 1
    assert "spread-check" in summary["tools"]
    items = _items(path, m)
    calls = [i for i in items if i["kind"] == "tool.call" and i["tool"] == "spread-check"]
    assert len(calls) >= 6
    assert all(c["ok"] and c["cost"] == m.tools.population_tool_micro_per_call for c in calls)
    assert all(json.loads(c["args"]) == {"mid": 100.0, "bps": 3} for c in calls), calls
    # The result reached the calling assembly: its continuation reports the tool results.
    handles = {c["handle"] for c in calls}
    seen = [i for i in items if i["kind"] == "invocation" and i["handle"] in handles
            and '"seen_tool_results"' in i["outputs"]]
    assert len(seen) >= len(handles)
