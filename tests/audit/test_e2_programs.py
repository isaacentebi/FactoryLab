"""E2 W5: a seat can be a program (C8) and what it keeps is an artifact (C9).

A scripted world registers a program seat from a producer's ``register`` list;
the seat is instantiated, routed, judged and paid exactly like a model seat,
keeps private state across calls and across a crash, and its artifacts outlive
its retirement.
"""

import json
from dataclasses import replace

import pytest

from factorylab.cortex.assembly import ProgramAssembly
from factorylab.kernel.ledger import Ledger
from factorylab.runtime.loop import Runtime
from factorylab.runtime.resume import resume_runtime, runtime_state
from factorylab.runtime.worlds import load_manifest
from factorylab.world.scripted import ScriptedProvider
from tests.cortex.test_jail import require_jail

pytestmark = pytest.mark.slow

COUNTER = (
    "import json, sys\n"
    "d = json.load(sys.stdin)\n"
    "s = d['state'] or {'n': 0}\n"
    "print(json.dumps({'action': 'hold', 'payoff': 0.1, 'seen': s['n'],"
    " 'state': {'n': s['n'] + 1}}))\n"
)
PROGRAM = {"kind": "assembly", "id": "prog-a", "model_id": "program", "accepts": ["Tick"],
           "code": COUNTER, "state_policy": "private"}
BROKEN = {"kind": "assembly", "id": "prog-bad", "model_id": "program", "accepts": ["Tick"],
          "code": "raise SystemExit(3)"}


class Proposer(ScriptedProvider):
    """The first producer call registers the counter, the second a program that raises."""

    def _produce(self, description, inputs):
        self._producer_calls += 1
        reply = {"action": "hold", "payoff": 0.1}
        if self._producer_calls == 1:
            reply["register"] = [PROGRAM]
        elif self._producer_calls == 2:
            reply["register"] = [BROKEN]
        return reply


def world(path, events, **kwargs):
    return Runtime(load_manifest("scripted"), events=events, seed=1, initial_balance_micro=None,
                   ledger_path=None if path is None else str(path), drip=False,
                   router_gamma=.1, provider=Proposer(), **kwargs)


def items(path):
    manifest = json.loads(load_manifest("scripted").canonical_json())
    return Ledger.reopen(path, manifest=manifest)._recovery_items()


def events_of(diary, kind):
    return [i["event"]["payload"] for i in diary
            if i["kind"] == "event" and i["event"]["kind"] == kind]


class ProcessDeath(Exception):
    pass


def stop_after(rt, predicate):
    original = rt._process_event

    def interrupted(event):
        result = original(event)
        if predicate(rt, event):
            raise ProcessDeath
        return result

    rt._process_event = interrupted
    with pytest.raises(ProcessDeath):
        rt.run()


@pytest.fixture(scope="module")
def program_world(tmp_path_factory):
    require_jail()
    path = tmp_path_factory.mktemp("programs") / "world.jsonl"
    rt = world(path, 80)
    summary = rt.run()
    return rt, summary, items(path), path


def test_program_seat_is_registered_routed_judged_paid_and_in_the_catalogue(program_world):
    rt, summary, diary, _ = program_world
    price = rt.m.prices.program_micro_per_call
    registered = {p["id"]: p for p in events_of(diary, "Registered") if p.get("kind") == "assembly"}
    assert registered["prog-a"]["program"] is True
    assert registered["prog-a"]["state_policy"] == "private"
    assert registered["prog-a"]["emits"] == ["ProducerReturn"]
    assert isinstance(rt.assemblies["prog-a"], ProgramAssembly)
    assert rt.registry.get("prog-a").kind == "assembly"
    # Admission cost the trial amount, like any registration.
    assert any(i["kind"] == "reserve.reserve" and i.get("contract") == "prog-a"
               for i in diary) or summary["stats"]["registrations_accepted"] >= 2
    # Routed on later ticks, answering like a model seat at the flat price.
    calls = [i for i in diary if i["kind"] == "invocation" and i["assembly_id"] == "prog-a"]
    assert len(calls) >= 3 and all(c["status"] == "ok" and c["cost"] == price for c in calls)
    assert all(c["served_by"] == "program" for c in calls)
    assert [json.loads(c["outputs"])["seen"] for c in calls] == list(range(len(calls)))
    # Every call is a wallet transaction: one reserve and one commit of the price.
    wallet = [(i["kind"], i["amount"]) for i in diary
              if i["kind"] in ("wallet.reserve", "wallet.commit")
              and i.get("reason") == "model:program"]
    assert wallet.count(("wallet.reserve", price)) == wallet.count(("wallet.commit", price))
    assert wallet.count(("wallet.commit", price)) == summary["stats"][
        "invocations_by_assembly"]["prog-a"] + summary["stats"][
        "invocations_by_assembly"]["prog-bad"]
    # Judged by evaluators, whose standing the judged returns feed.
    handles = {c["handle"] for c in calls}
    verdicts = [v for v in events_of(diary, "Verdict") if v["about_handle"] in handles]
    assert verdicts and all("seen" in v["producer_outputs"] for v in verdicts)
    assert set(summary["standing"]) >= {v["evaluator_handle"] and rt.handle_to_assembly[
        v["evaluator_handle"]] for v in verdicts}
    assert "prog-a" in json.dumps(rt._world_block()["catalogue"])
    assert summary["wallet_conservation"] and summary["ledger_verify"]


def test_a_program_that_raises_is_a_billed_malformed_return(program_world):
    rt, _, diary, _ = program_world
    price = rt.m.prices.program_micro_per_call
    calls = [i for i in diary if i["kind"] == "invocation" and i["assembly_id"] == "prog-bad"]
    assert calls and all(c["status"] == "malformed" and c["cost"] == price for c in calls)
    assert all(json.loads(c["outputs"])["reason"] == "exit 3" for c in calls)
    for call in calls:
        # Reserved, then committed in full, before the invocation is ledgered.
        moves = [i for i in diary if i.get("handle") == call["handle"]
                 and i["kind"] in ("wallet.reserve", "wallet.commit")
                 and i.get("reason") == "model:program"]
        assert [(m["kind"], m["amount"]) for m in moves] == [
            ("wallet.reserve", price), ("wallet.commit", price)]
        assert moves[-1]["seq"] < call["seq"]
    program_calls = [i for i in diary if i["kind"] == "program.call"
                     and i["assembly_id"] == "prog-bad"]
    assert all(p["status"] == "malformed" and p["state_out"] is None for p in program_calls)


def test_private_state_is_an_artifact_ledgered_with_each_return(program_world):
    rt, _, diary, path = program_world
    calls = [i for i in diary if i["kind"] == "program.call" and i["assembly_id"] == "prog-a"]
    puts = [i for i in diary if i["kind"] == "artifact.put"]
    assert [c["state_in"] for c in calls] == [None, *(c["state_out"] for c in calls[:-1])]
    assert [p["sha"] for p in puts] == [c["state_out"] for c in calls]
    assert all(p["owner"] == "prog-a" and p["artifact_kind"] == "program.state" for p in puts)
    for put, call in zip(puts, calls, strict=True):
        assert put["seq"] < call["seq"]  # the archive record precedes the return
    invocation = next(i for i in diary if i["kind"] == "invocation"
                      and i["handle"] == calls[-1]["handle"])
    assert calls[-1]["seq"] < invocation["seq"]
    last = calls[-1]["state_out"]
    assert rt.assemblies["prog-a"].state_sha == last
    assert json.loads(rt.artifacts.get(last)) == {"n": len(calls)}
    assert (path.with_suffix(".artifacts") / last).exists()


def test_artifact_survives_its_owners_retirement_and_any_seat_reads_it(program_world):
    rt, _, _, _ = program_world
    sha = rt.assemblies["prog-a"].state_sha
    rt._retire_assembly("prog-a", "retire-prog-a")
    assert "prog-a" in rt.retired_assemblies
    assert rt.artifacts.get(sha) and rt.artifacts.owner_for(sha) == "prog-a"
    before = rt.wallet.balance
    result, cost = rt._run_tool("seed-decider", "reader", {"tool": "artifact.get",
                                                           "args": {"sha": sha}})
    assert cost == 0 and rt.wallet.balance == before
    assert result == {"sha": sha, "owner": "prog-a", "kind": "program.state", "bytes": 9,
                      "text": json.dumps(json.loads(rt.artifacts.get(sha)))}
    assert rt.tool_specs["artifact.get"]["price_micro_per_call"] == 0
    unknown, _ = rt._run_tool("seed-decider", "reader", {"tool": "artifact.get",
                                                         "args": {"sha": "0" * 64}})
    assert unknown == {"error": "unknown artifact"}


def test_state_survives_a_crash_and_the_next_call_after_resume_sees_it(tmp_path):
    require_jail()
    path = tmp_path / "crash.jsonl"
    rt = world(path, 80)
    stop_after(rt, lambda r, e: r.stats.invocations_by_assembly.get("prog-a", 0) >= 3
               and str(e.kind) == "Tick")
    before = items(path)
    seen_before = [json.loads(i["outputs"])["seen"] for i in before
                   if i["kind"] == "invocation" and i["assembly_id"] == "prog-a"]
    assert seen_before == list(range(len(seen_before)))
    last_state = [i for i in before if i["kind"] == "program.call"
                  and i["assembly_id"] == "prog-a"][-1]["state_out"]
    restored = resume_runtime(load_manifest("scripted"), str(path), provider=Proposer())
    assert isinstance(restored.assemblies["prog-a"], ProgramAssembly)
    assert restored.assemblies["prog-a"].state_sha == last_state
    assert restored.artifacts.root == path.with_suffix(".artifacts")
    assert json.loads(restored.artifacts.get(last_state)) == {"n": len(seen_before)}
    assert restored.artifacts.index == rt.artifacts.index
    restored.run()
    after = items(path)
    seen_after = [json.loads(i["outputs"])["seen"] for i in after
                  if i["kind"] == "invocation" and i["assembly_id"] == "prog-a"]
    assert len(seen_after) > len(seen_before)
    assert seen_after == list(range(len(seen_after)))
    assert after[:len(before)] == before


def test_program_world_is_deterministic():
    require_jail()
    first, second = world(None, 40).run(), world(None, 40).run()
    assert first == second
    assert first["stats"]["invocations_by_assembly"]["prog-a"] >= 2


def test_checkpoint_carries_program_specs_and_state_by_hash():
    require_jail()
    rt = world(None, 20)
    rt.run()
    state = runtime_state(rt)
    fresh = Runtime(load_manifest("scripted"), ledger_path=None, **state["config"])
    from factorylab.runtime.resume import restore_runtime

    restore_runtime(fresh, state)
    prog = fresh.assemblies["prog-a"]
    assert isinstance(prog, ProgramAssembly) and prog.spec == rt.assemblies["prog-a"].spec
    assert prog.state_sha == rt.assemblies["prog-a"].state_sha
    assert fresh.artifacts.index == rt.artifacts.index
    assert runtime_state(fresh) == state


def test_program_registration_needs_the_jail_and_refunds_the_trial(monkeypatch):
    from factorylab.cortex.registration import AssemblyProposal
    from factorylab.kernel.wallet import Infeasible
    from tests.conftest import make_runtime

    rt = make_runtime()
    rt._manage_reserve_window()
    rt.tool_jail_available = False
    proposal = replace(  # what the parser would have produced on a jailed host
        AssemblyProposal("prog-a", "producer", "program", "program", ("Tick",), 512, "low"),
        code=COUNTER, state_policy="private")
    with pytest.raises(Infeasible):
        rt._register("proposal", proposal)
    assert "prog-a" not in rt.assemblies
    assert not any(c.id == "prog-a" for c in rt.registry.available("assembly"))
