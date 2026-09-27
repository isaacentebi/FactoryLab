"""E2 W5: a seat can be a program (C8) and what it keeps is an artifact (C9).

A scripted world registers a program seat from a producer's ``register`` list;
the seat is instantiated, routed, judged and paid exactly like a model seat,
keeps private state across calls and across a crash, and its artifacts outlive
its retirement.
"""

import json

import pytest

from factorylab.cortex.assembly import ProgramAssembly
from factorylab.kernel.ledger import Ledger
from factorylab.runtime.loop import Runtime
from factorylab.runtime.resume import resume_runtime
from factorylab.runtime.worlds import load_manifest
from factorylab.world.scripted import ScriptedProvider
from tests.cortex.test_jail import require_jail

pytestmark = pytest.mark.slow

# A hold that executes nothing names the trade it declined, on a coin the world it was
# shown lists (the return contract of a producing kind), as a model seat's must.
COUNTER = (
    "import json, sys\n"
    "d = json.load(sys.stdin)\n"
    "s = d['state'] or {'n': 0}\n"
    "mids = (d['inputs'].get('world') or {}).get('recent_mids') or {}\n"
    "out = {'action': 'hold', 'payoff': 0.1, 'seen': s['n'], 'state': {'n': s['n'] + 1}}\n"
    "if mids:\n"
    "    out['counterfactual'] = {'coin': 'BTC' if 'BTC' in mids else min(mids),"
    " 'side': 'buy'}\n"
    "print(json.dumps(out))\n"
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
                   ledger_path=None if path is None else str(path), router_gamma=.1,
                   provider=Proposer(), **kwargs)


def items(path):
    manifest = json.loads(load_manifest("scripted").canonical_json())
    return Ledger.reopen(path, manifest=manifest)._recovery_items()


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
