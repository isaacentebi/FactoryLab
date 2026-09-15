"""C8: a program seat runs in the jail through the meter and answers like a model seat."""

import json
from dataclasses import replace

import pytest

from factorylab.cortex import sandbox
from factorylab.cortex.assembly import (
    MAX_PROGRAM_STATE_BYTES,
    ProgramAssembly,
    ProgramAssemblySpec,
    validate_proposal,
)
from factorylab.cortex.registration import AssemblyProposal, Rejected, parse_proposals
from factorylab.cortex.request import Request
from factorylab.cortex.sandbox import ProgramRunner
from factorylab.kernel.artifacts import ArtifactStore
from factorylab.kernel.ledger import Ledger
from factorylab.world.metering import Meter
from tests.cortex.test_cortex import TinyWallet
from tests.cortex.test_jail import require_jail

PRICE = 50
ECHO = (
    "import json, sys\n"
    "d = json.load(sys.stdin)\n"
    "s = d['state'] or {'n': 0}\n"
    "print(json.dumps({'action': 'hold', 'seen': s['n'], 'you': d['inputs']['you'],\n"
    "                  'state': {'n': s['n'] + 1}}))\n"
)


def req(handle="h1", ceiling=1_000_000):
    return Request(
        handle=handle, description="Decide on the latest mid.", inputs={"mid": "60000"},
        capability_versions={}, outcome_schema={"type": "object", "properties": {
            "action": {"type": "string"}}, "required": ["action"]},
        deadline_ns=10**12, cost_ceiling=ceiling, parent_handle=None,
        completion_criterion="a JSON object with an action", scoring_channel="fast",
        resource_liability="self",
    )


def spec(**kw):
    fields = {"id": "prog-a", "version": 1, "model_id": "program", "code": ECHO,
              "timeout_s": 2, "state_policy": "private"}
    return ProgramAssemblySpec(**{**fields, **kw})


def program(wallet=None, *, ledger=None, **kw):
    wallet = wallet or TinyWallet(balance=10_000)
    ledger = ledger or Ledger(None, manifest={"name": "programs"})
    archive = ArtifactStore(ledger, root=None, clock_ns=lambda: 1)
    recorded = []
    asm = ProgramAssembly(spec(**kw), ProgramRunner(), Meter(wallet), PRICE, artifacts=archive,
                          record=recorded.append)
    return asm, wallet, recorded


# --- the spec ----------------------------------------------------------------

def test_spec_requires_program_model_code_timeout_and_policy():
    with pytest.raises(ValueError):
        spec(model_id="fake-haiku")
    with pytest.raises(ValueError):
        spec(code="   ")
    with pytest.raises(ValueError):
        spec(code="x" * 16_001)
    for timeout in (0, 11, 2.0, True):
        with pytest.raises(ValueError):
            spec(timeout_s=timeout)
    with pytest.raises(ValueError):
        spec(state_policy="shared")
    assert spec().reward_shapes == {"ProducerReturn": "judged"}
    custom = spec(emits=("Finding",), schemas={"Finding": {"type": "object"}},
                  reward_shapes={"Finding": "forecast"})
    assert custom.reward_shapes == {"Finding": "forecast"}


def test_routing_reads_a_flat_ceiling_from_the_program_model():
    asm, _, _ = program()
    assert asm.model.ceiling(None) == PRICE
    with pytest.raises(ValueError):
        ProgramAssembly(spec(), ProgramRunner(available=False), Meter(TinyWallet(1)), -1)


# --- invocation through the meter -------------------------------------------

def test_program_answers_like_a_model_and_is_charged_its_flat_price():
    require_jail()
    asm, wallet, recorded = program()
    ret = asm.invoke(req())
    assert ret.status == "ok" and ret.served_by == "program"
    assert ret.outputs == {"action": "hold", "seen": 0, "you": "prog-a"}
    assert ret.cost == PRICE and wallet.balance == 10_000 - PRICE
    assert wallet.log == [("reserve", PRICE), ("commit", PRICE)]
    assert ret.provider["state_sha"] == asm.state_sha and asm.state_sha is not None
    assert recorded == [{"kind": "program.call", "assembly_id": "prog-a", "handle": "h1",
                         "status": "ok", "cost": PRICE, "state_in": None,
                         "state_out": asm.state_sha}]


def test_private_state_written_by_one_call_is_read_by_the_next():
    require_jail()
    asm, _, recorded = program()
    first = asm.invoke(req("h1"))
    sha_after_first = asm.state_sha
    second = asm.invoke(req("h2"))
    assert (first.outputs["seen"], second.outputs["seen"]) == (0, 1)
    assert asm.state_sha != sha_after_first
    assert json.loads(asm.artifacts.get(asm.state_sha)) == {"n": 2}
    assert recorded[1]["state_in"] == sha_after_first
    assert recorded[1]["state_out"] == asm.state_sha
    assert asm.artifacts.owner_for(asm.state_sha) == "prog-a"
    # The artifact is the diary's evidence too: a put precedes each program.call.
    assert [r["kind"] for r in asm.artifacts.list()] == ["program.state", "program.state"]


def test_state_restored_by_hash_is_what_the_next_call_sees():
    require_jail()
    asm, _, _ = program()
    asm.invoke(req("h1"))
    sha = asm.state_sha
    fresh = ProgramAssembly(spec(), ProgramRunner(), Meter(TinyWallet(1_000)), PRICE,
                            artifacts=asm.artifacts)
    fresh.state_sha = sha
    assert fresh.invoke(req("h2")).outputs["seen"] == 1


def test_state_is_the_programs_and_never_the_returns():
    require_jail()
    asm, wallet, _ = program(state_policy="none")
    ret = asm.invoke(req())
    assert ret.status == "malformed" and asm.state_sha is None and ret.cost == PRICE
    asm, wallet, _ = program()
    asm.invoke(req())
    assert "state" not in asm.invoke(req()).outputs


@pytest.mark.parametrize("code, reason", [
    ("raise SystemExit(3)", "exit 3"),
    ("import sys\nsys.stdin.read()\nwhile True:\n    pass\n", "timeout"),
    ("print('not json')", None),
    ("import json\nprint(json.dumps({'status': 7}))", None),
])
def test_failure_is_a_billed_malformed_return_never_an_exception(code, reason):
    require_jail()
    asm, wallet, recorded = program(code=code, timeout_s=1)
    ret = asm.invoke(req())
    assert ret.status == "malformed" and ret.cost == PRICE
    assert wallet.log == [("reserve", PRICE), ("commit", PRICE)]
    if reason:
        assert ret.outputs["reason"] == reason
    else:
        assert "raw" in ret.outputs
    assert asm.state_sha is None and recorded[0]["status"] == "malformed"


def test_oversize_or_unserialisable_state_is_malformed_and_kept_out_of_the_archive():
    require_jail()
    code = ("import json,sys\nprint(json.dumps({'action': 'hold', 'state': "
            f"{{'blob': 'x' * {MAX_PROGRAM_STATE_BYTES}}}}}))\n")
    asm, _, _ = program(code=code)
    ret = asm.invoke(req())
    assert ret.status == "malformed" and "exceeds" in ret.outputs["reason"]
    assert asm.artifacts.list() == []


def test_infeasible_reservation_and_ceiling_yield_failed_with_no_charge():
    asm, wallet, recorded = program(TinyWallet(balance=PRICE - 1))
    ret = asm.invoke(req())
    assert ret.status == "failed" and ret.cost == 0 and wallet.log == []
    asm, wallet, _ = program()
    ret = asm.invoke(req(ceiling=PRICE - 1))
    assert ret.status == "failed" and ret.cost == 0 and wallet.log == []
    assert recorded == []


def test_no_jail_is_a_billed_malformed_return(monkeypatch):
    asm, wallet, _ = program()
    asm.runner = ProgramRunner(available=False)
    ret = asm.invoke(req())
    assert ret.status == "malformed" and ret.outputs == {"reason": "no jail on this host"}
    assert ret.cost == PRICE and wallet.balance == 10_000 - PRICE


def test_program_runner_never_raises(monkeypatch):
    def fail(*args, **kwargs):
        raise OSError("jail unavailable")
    monkeypatch.setattr(sandbox.subprocess, "Popen", fail)
    runner = ProgramRunner(available=True)
    assert runner.run("print(1)", stdin="{}", timeout_s=1) == {
        "error": "program execution failed"}
    assert runner.run("print(1)", stdin="{}", timeout_s=11) == {"error": "invalid program timeout"}


def test_stdin_carries_the_rendered_prompt_the_inputs_and_the_state():
    asm, _, _ = program()
    data = json.loads(asm.build_stdin(req(), {"n": 4}))
    assert set(data) == {"prompt", "description", "inputs", "outcome_schema", "state"}
    assert data["inputs"] == {"mid": "60000", "you": "prog-a"} and data["state"] == {"n": 4}
    assert data["prompt"] == replace(req(), inputs=data["inputs"]).prompt_text()


# --- registration ----------------------------------------------------------------

def proposal(**kw):
    return {"kind": "assembly", "id": "prog-a", "model_id": "program", "accepts": ["Tick"],
            "code": ECHO, **kw}


def parse(item, *, jail=True):
    return parse_proposals({"register": [item]}, event_kinds=frozenset({"Tick"}),
                           known_models=frozenset({"fake-haiku"}), known_assemblies=frozenset(),
                           tool_jail=jail)


def test_program_proposal_is_an_assembly_with_code_and_needs_the_jail():
    accepted, rejected = parse(proposal(timeout_s=3, state_policy="private"))
    assert not rejected and isinstance(accepted[0], AssemblyProposal)
    assert (accepted[0].model_id, accepted[0].code, accepted[0].timeout_s,
            accepted[0].state_policy) == ("program", ECHO, 3, "private")
    assert accepted[0].system_prompt == "program"
    assert parse(proposal(), jail=False) == ([], [Rejected(0, "no jail on this host")])
    validate_proposal(proposal(timeout_s=10, state_policy="private"))


@pytest.mark.parametrize("changes, reason", [
    ({"code": None}, "a program seat needs code"),
    ({"code": ""}, "a program seat needs code"),
    ({"code": "x" * 16_001}, "code exceeds 16000 chars"),
    ({"timeout_s": 0}, "timeout_s must be an int in [1, 10]"),
    ({"timeout_s": 11}, "timeout_s must be an int in [1, 10]"),
    ({"state_policy": "shared"}, "state_policy must be none or private"),
    ({"model_id": "unknown"}, "model_id must name a registered model"),
])
def test_malformed_program_proposals_are_refused_with_a_reason(changes, reason):
    assert parse(proposal(**changes)) == ([], [Rejected(0, reason)])


def test_model_seats_cannot_carry_program_fields():
    item = {"kind": "assembly", "id": "seat", "model_id": "fake-haiku", "accepts": ["Tick"],
            "system_prompt": "Reply with JSON.", "code": "print(1)"}
    assert parse(item) == ([], [Rejected(0, "code, timeout_s and state_policy belong to a "
                                            "program seat")])
