"""B1: published tool examples traverse the population's actual return validator."""

import json
from dataclasses import replace

import pytest

from factorylab.runtime.loop import Runtime
from factorylab.runtime.worlds import load_manifest
from factorylab.world.exchange import FakeExchange
from factorylab.world.models import ModelResponse
from factorylab.world.scripted import ScriptedProvider
from tests.conftest import make_runtime


def test_every_published_tool_accepts_its_examples():
    rt = make_runtime()
    try:
        seeded = dict(rt.tool_specs)
        # The shared directory's indexes are registered on first use rather than at
        # launch; they carry examples their own schemas accept like every other tool.
        rt._ensure_connector_tool()
        directory = {k: v for k, v in rt.tool_specs.items()
                     if k in ("note.list", "artifact.list")}
        assert len(directory) == 2
        request = rt._request("examples", "tool examples", {}, {}, 100, "test")
        for tool_id, spec in {**seeded, **directory}.items():
            assert spec["args_schema"]["examples"], tool_id
            for args in spec["args_schema"]["examples"]:
                rt._validate_output_contract({"tool_calls": [{"tool": tool_id, "args": args}]},
                                             request)
    finally:
        rt._ledger_lock.close()


def _decision(rt):
    """Open an ordinary producing decision so a tool call has a real resource liability."""
    from factorylab.kernel.queue import PropensityRecord

    return rt.queue.open(
        actor="test-router", event_id="test-transfer",
        propensity=PropensityRecord(("seed-decider",), (1.0,), "seed-decider", 0,
                                    "test-router", "state"),
        channel="verdict", deadline_ns=rt.clock.now_ns + 100_000_000_000,
        parent_handle=None, cost_ceiling=rt.wallet.available,
    )


@pytest.mark.parametrize("amount", ["5", 5])
def test_treasury_transfer_survives_assembly_and_reaches_the_rail(amount, monkeypatch):
    rt = make_runtime()
    try:
        rt.reserve.open_window(rt.clock.now_ns, rt.wallet.balance)
        body = {"action": "hold", "tool_calls": [{"tool": "treasury.transfer", "args": {
            "direction": "to_reserve", "usd": amount, "reason": "rehearsal",
        }}]}
        monkeypatch.setattr(rt.provider.target, "complete", lambda req: ModelResponse(
            req.model_id, json.dumps(body), 1, 1, "stop"))
        # A1 routes write authority through the decision's own ancestry, so the caller
        # must be a real kernel decision, not a bare handle with an account attached.
        req = rt._request(_decision(rt), "transfer", {}, {}, 100, "test")
        rt.consequences.start(req.handle, 0)  # treasury writes need an open account (A9)
        ret = rt.assemblies["seed-decider"].invoke(req)
        assert ret.status == "ok" and len(ret.tool_calls) == 1
        result, _ = rt._run_tool("seed-decider", req.handle, ret.tool_calls[0])
        assert result["status"] == "submitted"
        rt.treasury.tick(rt.clock.now_ns + 1)
        assert rt.treasury.state["status"] == "confirmed"
        assert rt.wallet.check_conservation()
    finally:
        rt._ledger_lock.close()


def test_testnet_manifest_constructs_the_configured_treasury_rail_offline(monkeypatch):
    m = load_manifest("testnet")
    assert m.treasury.reserve_address
    assert m.treasury.hyperevm_gas_budget_wei > 0 and m.treasury.base_gas_budget_wei > 0
    constructed = []

    class Rail:
        name = "offline-testnet"

        def __init__(self, exchange, spec):
            constructed.append(spec)

    monkeypatch.setattr("factorylab.world.treasury_rails.LiveRail", Rail)
    rt = Runtime(replace(m, drip=None), events=0, seed=1, initial_balance_micro=None,
                 ledger_path=None, drip=False, router_gamma=.1, provider=ScriptedProvider(),
                 exchange=FakeExchange())
    try:
        assert constructed == [m.treasury]
        assert rt.treasury.rail.name == "offline-testnet"
    finally:
        rt._ledger_lock.close()
