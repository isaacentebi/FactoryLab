"""C8: a program seat runs in the jail through the meter and answers like a model seat."""


import pytest

from factorylab.cortex import sandbox
from factorylab.cortex.assembly import (
    ProgramAssembly,
    ProgramAssemblySpec,
)
from factorylab.cortex.request import Request
from factorylab.cortex.sandbox import ProgramRunner
from factorylab.kernel.artifacts import ArtifactStore
from factorylab.kernel.ledger import Ledger
from factorylab.world.metering import Meter
from tests.cortex.test_cortex import TinyWallet
from tests.cortex.test_jail import require_jail

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
    asm = ProgramAssembly(spec(**kw), ProgramRunner(), Meter(wallet), artifacts=archive,
                          record=recorded.append)
    return asm, wallet, recorded


# --- invocation through the meter -------------------------------------------

def test_program_answers_like_a_model_and_moves_no_money():
    """Wave 11: the jail pays no one, so a program call is metered at zero."""
    require_jail()
    asm, wallet, recorded = program()
    ret = asm.invoke(req())
    assert ret.status == "ok" and ret.served_by == "program"
    assert ret.outputs == {"action": "hold", "seen": 0, "you": "prog-a"}
    assert ret.cost == 0 and wallet.balance == 10_000
    assert wallet.log == [("reserve", 0), ("commit", 0)]
    assert ret.provider["state_sha"] == asm.state_sha and asm.state_sha is not None
    assert recorded == [{"kind": "program.call", "assembly_id": "prog-a", "handle": "h1",
                         "status": "ok", "cost": 0, "state_in": None,
                         "state_out": asm.state_sha}]


def test_a_program_has_no_price_to_set():
    """No door back to a fictitious debit: the executor takes no price, and the
    ceiling routing reads from it is zero."""
    with pytest.raises(TypeError):
        ProgramAssembly(spec(), ProgramRunner(), Meter(TinyWallet(1_000)), 50)
    asm, _, _ = program()
    assert asm.model.ceiling(req()) == 0


def test_state_restored_by_hash_is_what_the_next_call_sees():
    require_jail()
    asm, _, _ = program()
    asm.invoke(req("h1"))
    sha = asm.state_sha
    fresh = ProgramAssembly(spec(), ProgramRunner(), Meter(TinyWallet(1_000)),
                            artifacts=asm.artifacts)
    fresh.state_sha = sha
    assert fresh.invoke(req("h2")).outputs["seen"] == 1


#: A program that never stops is stopped by whichever bound trips first. The jail
#: sets the CPU rlimit to the same number of seconds as the wall timeout
#: (``sandbox.run_python``, ``cpu_s=timeout_s``), so a busy loop races them: the
#: wall clock reports ``timeout`` and SIGXCPU reports ``exit -24``, and how long
#: the program spent blocked reading its stdin decides which. Both are the same
#: contract — a billed malformed return, never an exception — and the runtime
#: never promised which limit would name it.
OVERRAN = ("timeout", "exit -24")


@pytest.mark.parametrize("code, reason", [
    ("raise SystemExit(3)", ("exit 3",)),
    ("import sys\nsys.stdin.read()\nwhile True:\n    pass\n", OVERRAN),
    ("print('not json')", None),
    ("import json\nprint(json.dumps({'status': 7}))", None),
])
@pytest.mark.gate  # measured over 0.9 s: a subprocess, a jail timeout or a long loop
def test_failure_is_a_malformed_return_never_an_exception(code, reason):
    require_jail()
    asm, wallet, recorded = program(code=code, timeout_s=1)
    ret = asm.invoke(req())
    assert ret.status == "malformed" and ret.cost == 0
    assert wallet.log == [("reserve", 0), ("commit", 0)]
    if reason:
        assert ret.outputs["reason"] in reason
    else:
        assert "raw" in ret.outputs
    assert asm.state_sha is None and recorded[0]["status"] == "malformed"


def test_an_infeasible_reservation_yields_failed_with_no_charge():
    asm, wallet, recorded = program(TinyWallet(balance=-1))
    ret = asm.invoke(req())
    assert ret.status == "failed" and ret.cost == 0 and wallet.log == []
    assert recorded == []


def test_no_jail_is_a_malformed_return(monkeypatch):
    asm, wallet, _ = program()
    asm.runner = ProgramRunner(available=False)
    ret = asm.invoke(req())
    assert ret.status == "malformed" and ret.outputs == {"reason": "no jail on this host"}
    assert ret.cost == 0 and wallet.balance == 10_000


def test_program_runner_never_raises(monkeypatch):
    def fail(*args, **kwargs):
        raise OSError("jail unavailable")
    monkeypatch.setattr(sandbox.subprocess, "Popen", fail)
    runner = ProgramRunner(available=True)
    assert runner.run("print(1)", stdin="{}", timeout_s=1) == {
        "error": "program execution failed"}
    assert runner.run("print(1)", stdin="{}", timeout_s=11) == {"error": "invalid program timeout"}


# --- registration ----------------------------------------------------------------
