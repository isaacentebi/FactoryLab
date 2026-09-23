"""The capital loop's keyless chain reads: the launch check and the outstanding report.

Nothing here touches a network or a signing key: Base is a fake JSON-RPC transport and
each run's diary is a real encrypted ledger written with its own sealing key file, the
way a rehearsal writes one and ``factorylab postmortem`` reads one.
"""

import json
from dataclasses import replace

import pytest

from factorylab.kernel.ledger import Ledger
from factorylab.runtime.capital_loop import (
    CapitalLoopRefused,
    journaled_references,
    keyless_base,
    launch_check,
    outstanding,
    read_items,
)
from factorylab.runtime.worlds import load_manifest
from factorylab.world.evm import BASE
from factorylab.world.x402 import HTTPResponse

RESERVE = "0x1228e5620944a79D268Afc7522E00891526EdEBb"
LIVE_NONCE = "0x" + "11" * 32
OLD_NONCE = "0x" + "22" * 32
SETTLED_NONCE = "0x" + "33" * 32


class Rpc:
    """Base mainnet as the public JSON-RPC answers it, and a record of every request."""

    def __init__(self):
        self.balance = 10_000_000
        self.final_number, self.final_ts = 900, 2_000
        self.used = {SETTLED_NONCE}
        self.requests = []

    def __call__(self, method, url, payload, headers):
        self.requests.append(payload)
        name, params = payload["method"], payload["params"]
        if name == "eth_chainId":
            result = hex(BASE.id)
        elif name == "eth_getBlockByNumber":
            result = ({"number": hex(self.final_number), "timestamp": hex(self.final_ts)}
                      if params[0] == "finalized" else {"hash": "0x" + "ab" * 32})
        elif name == "eth_call":
            data = params[0]["data"]
            if data.startswith("0x70a08231"):  # balanceOf(address)
                result = hex(self.balance)
            else:  # authorizationState(address,bytes32)
                result = hex(int("0x" + data[-64:] in self.used))
        elif name == "eth_getLogs":
            nonce = params[0]["topics"][2]
            result = ([{"address": BASE.usdc, "topics": params[0]["topics"],
                        "transactionHash": "0x" + "cd" * 32, "blockNumber": hex(850),
                        "blockHash": "0x" + "ab" * 32}]
                      if nonce in self.used else [])
        else:
            raise AssertionError(f"unexpected RPC {name}")
        return HTTPResponse(200, {"jsonrpc": "2.0", "id": 1, "result": result})


def reference(nonce, valid_before):
    return {"authorization": {"nonce": nonce, "validBefore": valid_before, "to": "0x" + "9" * 40},
            "start_block": 800, "network": "eip155:8453"}


def write_run(run_dir, *, crash=True):
    """A capital-loop diary: a settled conversion, a superseded authorization, a current
    one still inside its validity window, and a shadow send left unconfirmed."""
    run_dir.mkdir(parents=True)
    path = run_dir / "ledger.jsonl"
    ledger = Ledger(str(path), manifest={"name": "edition5-capital-loop"},
                    clock_ns=lambda: 0, key_path=str(path) + ".key")
    steps = ["shadow_send", "venice_top_up"]
    settled = {"id": "treasury-0", "steps": steps, "index": 1, "status": "submitted",
               "reference": reference(SETTLED_NONCE, 1_000), "route_data": {}}
    ledger.append({"kind": "treasury.step_submitted", "state": settled})
    ledger.append({"kind": "treasury.confirmed", "state": {**settled, "status": "confirmed"}})
    current = {"id": "treasury-1", "steps": steps, "index": 1, "status": "submitted",
               "reference": reference(LIVE_NONCE, 3_000),
               "route_data": {"superseded_references": [reference(OLD_NONCE, 1_500)]}}
    ledger.append({"kind": "treasury.step_submitted", "state": current})
    prepared_only = reference("0x" + "44" * 32, 3_000)  # never journaled toward a signature
    ledger.append({"kind": "io.result", "call": 1, "result": json.dumps(prepared_only)})
    shadow = {"id": "treasury-2", "steps": steps, "index": 0, "status": "submitted",
              "amount_micro": 5_000_000,
              "reference": {"nonce": 1_700_000_000_000, "destination": "0x" + "de" * 20}}
    ledger.append({"kind": "treasury.submitted", "state": shadow})
    if crash:
        with path.open("ab") as stream:
            stream.write(b'{"item": "gAAAAA-torn')  # the process died mid-append
    return run_dir


def test_a_crashed_run_is_read_with_its_own_ledger_key_and_nothing_else(tmp_path):
    items = read_items(write_run(tmp_path / "run"))
    top_ups, shadows = journaled_references(items)
    assert sorted(r["authorization"]["nonce"] for r in top_ups) == sorted(
        [SETTLED_NONCE, LIVE_NONCE, OLD_NONCE])  # the merely prepared one is not listed
    assert shadows == [{"transfer_id": "treasury-2", "nonce": 1_700_000_000_000,
                        "sink": "0x" + "de" * 20, "amount_micro": 5_000_000}]
    (tmp_path / "run" / "ledger.jsonl.key").unlink()
    with pytest.raises(CapitalLoopRefused, match="run_ledger_key_missing"):
        read_items(tmp_path / "run")


def test_outstanding_reports_each_authorization_from_the_chain(tmp_path):
    rpc = Rpc()
    report = outstanding(write_run(tmp_path / "run"), reserve_address=RESERVE,
                         base=keyless_base(transport=rpc))
    rows = {row["nonce"]: row for row in report["top_ups"]}
    assert rows[SETTLED_NONCE]["authorization_used"] and rows[SETTLED_NONCE]["debits"]
    assert rows[LIVE_NONCE]["live"] and not rows[LIVE_NONCE]["expired"]
    assert rows[LIVE_NONCE]["finalized_timestamp"] == 2_000
    assert rows[OLD_NONCE]["expired"] and not rows[OLD_NONCE]["live"]
    assert len(report["shadow_sends"]) == 1
    methods = {r["method"] for r in rpc.requests}
    assert methods <= {"eth_chainId", "eth_getBlockByNumber", "eth_call", "eth_getLogs"}


def test_the_script_prints_every_authorization_and_flags_what_may_still_settle(
        tmp_path, capsys):
    from scripts import capital_loop_outstanding

    code = capital_loop_outstanding.main([str(write_run(tmp_path / "run"))], transport=Rpc())
    out = capsys.readouterr().out
    assert code == 1  # a live authorization and a pending shadow send
    assert f"nonce {LIVE_NONCE}" in out and "LIVE: may still settle" in out
    assert "authorizationState True" in out and "settled (debited)" in out
    assert "expired unused" in out and "shadow send pending treasury-2" in out
    assert "validBefore 3000" in out and "finalized_ts 2000" in out


def capital_loop_world():
    return load_manifest("worlds/edition6-capital-loop.toml")


def test_the_launch_check_needs_the_chain_to_bound_what_a_fresh_run_can_spend():
    world = capital_loop_world()
    world = replace(world, treasury=replace(world.treasury, venice_reserve_floor_micro=5_000_000))
    rpc = Rpc()
    rpc.balance = 15_000_000  # $10 above a $5 floor: exactly the total cap
    numbers = launch_check(world, transport=rpc)
    assert numbers["reserve_usdc_micro"] == 15_000_000
    assert numbers["spendable_above_floor_micro"] == 10_000_000
    rpc.balance = 15_000_001
    with pytest.raises(CapitalLoopRefused, match="reserve_floor_leaves_more") as refused:
        launch_check(world, transport=rpc)
    assert refused.value.detail["reserve_usdc_micro"] == 15_000_001
    # The review's case: a $1 floor on a $15 reserve would have allowed $14.
    low = replace(world, treasury=replace(world.treasury, venice_reserve_floor_micro=1_000_000))
    rpc.balance = 15_000_000
    with pytest.raises(CapitalLoopRefused, match="reserve_floor_leaves_more"):
        launch_check(low, transport=rpc)


def test_the_launch_check_refuses_while_an_earlier_run_can_still_settle(tmp_path):
    world = capital_loop_world()
    rpc = Rpc()
    run = write_run(tmp_path / "old")
    with pytest.raises(CapitalLoopRefused, match="previous_run_authorization") as refused:
        launch_check(world, previous_runs=(run,), transport=rpc)
    assert [row["nonce"] for row in refused.value.detail["authorizations"]] == [LIVE_NONCE]
    rpc.final_ts = 3_001  # finalized Base is now past its validBefore, still unused
    assert launch_check(world, previous_runs=(run,), transport=rpc)["previous_runs"]


def test_the_rehearsal_runner_runs_the_launch_check_before_anything_is_built(
        tmp_path, capsys):
    from scripts import edition4_rehearsal as rehearsal

    write_run(tmp_path / "runs" / "earlier")
    rpc = Rpc()
    report = rehearsal.run_rehearsal("worlds/edition6-capital-loop.toml",
                                     out=tmp_path / "runs" / "now", capital_loop=True,
                                     capital_loop_transport=rpc, source_root=tmp_path,
                                     provider=object())
    assert report["status"] == "failed"
    assert report["refusal"]["reason"] == "previous_run_authorization_may_still_settle"
    rpc.final_ts = 3_001
    report = rehearsal.run_rehearsal("worlds/edition6-capital-loop.toml",
                                     out=tmp_path / "runs" / "later", capital_loop=True,
                                     capital_loop_transport=rpc, source_root=tmp_path,
                                     provider=object())
    # The chain checks passed; the next refusal is the frozen-source check's.
    assert report["refusal"]["reason"] == "source_root_mismatch"
    printed = capsys.readouterr().out
    assert '"reserve_usdc_micro": 10000000' in printed
    floor = load_manifest("worlds/edition6-capital-loop.toml").treasury.venice_reserve_floor_micro
    assert f'"venice_reserve_floor_micro": {floor}' in printed
