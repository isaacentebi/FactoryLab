"""B1: published tool examples traverse the population's actual return validator."""

import json
from dataclasses import replace

import pytest

from factorylab.runtime.loop import Runtime, ScriptedProvider
from factorylab.runtime.worlds import load_manifest
from factorylab.world.exchange import FakeExchange
from factorylab.world.models import ModelResponse
from tests.runtime.test_fa_defects import make_runtime


def test_every_published_tool_accepts_its_examples():
    rt = make_runtime()
    try:
        request = rt._request("examples", "tool examples", {}, {}, 100, "test")
        for tool_id, spec in rt.tool_specs.items():
            assert spec["args_schema"]["examples"], tool_id
            for args in spec["args_schema"]["examples"]:
                rt._validate_output_contract({"tool_calls": [{"tool": tool_id, "args": args}]},
                                             request)
    finally:
        rt._ledger_lock.close()


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
        req = rt._request("transfer", "transfer", {}, {}, 100, "test")
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
