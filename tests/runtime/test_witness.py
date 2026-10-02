"""The death record outside the diary: a kill is witnessed where a copy of the diary is not.

A killed diary refuses to reopen. An earlier copy of it does not know it died:
the chain is valid, the key opens it, the release digest matches. These tests
pin the record that copy cannot carry (``factorylab/runtime/witness.py``): the
kill line every kill path appends beside the diary's directory and POSTs to a
receiver, the process's own memory of the kill, and the resume and restore
refusals that read them. Loopback receivers only; nothing here reaches a network.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import socket
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from threading import Thread

import pytest

from factorylab.kernel.events import Bus
from factorylab.kernel.ledger import Ledger, LedgerLock
from factorylab.kernel.termination import Termination
from factorylab.runtime import release, witness
from factorylab.runtime.loop import Runtime, run_world
from factorylab.runtime.resume import (
    ResumeError,
    restore_runtime,
    resume_runtime,
    resume_world,
    runtime_state,
)
from factorylab.runtime.worlds import load_manifest

HEX = set("0123456789abcdef")


def rows(path, m):
    """Every decrypted item of a diary, killed or not, without taking the writer's place."""
    return list(Ledger.open_read_only(path, manifest=json.loads(m.canonical_json())).items())


@pytest.fixture(autouse=True)
def _fresh_witness(monkeypatch):
    """No receiver unless a test sets one; no process keeps a record of kills to reset."""
    monkeypatch.delenv(witness.URL_ENV, raising=False)


class _Receiver(BaseHTTPRequestHandler):
    """A loopback receiver that stores every line and answers queries from what it stored."""

    lines: list[dict] = []

    def do_POST(self):
        length = int(self.headers.get("Content-Length", "0"))
        line = json.loads(self.rfile.read(length))
        _Receiver.lines.append(line)
        body = b""
        if line.get("event") == witness.QUERY:
            killed = any(k.get("event") == witness.KILL
                         and k.get("launch_nonce") == line.get("launch_nonce")
                         for k in _Receiver.lines)
            body = json.dumps({"killed": killed}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


@pytest.fixture
def receiver():
    _Receiver.lines = []
    server = HTTPServer(("127.0.0.1", 0), _Receiver)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}/witness"
    finally:
        server.shutdown()
        server.server_close()


def _closed_port_url() -> str:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    return f"http://127.0.0.1:{port}/witness"


def _first_token(path) -> bytes:
    with path.open("rb") as stream:
        stream.readline()
        return json.loads(stream.readline())["item"].encode("ascii")


def _world(tmp_path, name="w", *, kill_at_end=False):
    """A finished scripted world on disk under ``<tmp>/runs/``, resumable unless killed."""
    m = load_manifest("scripted")
    path = tmp_path / "runs" / f"{name}.jsonl"
    path.parent.mkdir(exist_ok=True)
    run_world(m, events=1, seed=1, ledger_path=str(path), kill_at_end=kill_at_end)
    return m, path


def _kill_from_outside(m, path, reason="explicit_kill:operator"):
    """The operator's kill: the CLI's path, a reopened ledger and a fresh Termination."""
    with LedgerLock(str(path)):
        ledger = Ledger.reopen(path, manifest=json.loads(m.canonical_json()))
        Termination(ledger=ledger, bus=Bus(ledger),
                    witness=witness.KillWitness()).kill(reason)
        return ledger.identity()


def test_the_worlds_own_death_writes_one_kill_line_beside_the_diary_directory(tmp_path):
    m, path = _world(tmp_path, kill_at_end=True)
    file = witness.witness_path(path)
    assert file == tmp_path / ".witness" / "w.jsonl"
    assert not (path.parent / ".witness").exists()  # never inside the diary's directory
    lines = [json.loads(raw) for raw in file.read_text().splitlines()]
    assert len(lines) == 1
    line = lines[0]
    assert line["event"] == "kill" and line["world"] == m.name
    assert line["reason"] == "explicit_kill:budget"
    assert len(line["launch_nonce"]) == 32 and set(line["launch_nonce"]) <= HEX
    assert line["release_digest"] == release.release_digest()
    assert line["ledger_head"] == hashlib.sha256(path.read_bytes()).hexdigest()
    assert line["diary"] == hashlib.sha256(_first_token(path)).hexdigest()
    assert line["ts"].endswith("Z")
    assert os.stat(file).st_mode & 0o777 == 0o600
    assert os.stat(file.parent).st_mode & 0o777 == 0o700
    launch = next(i for i in rows(path, m)
                  if i["kind"] == "event" and i["event"]["kind"] == "Launch")
    assert launch["event"]["payload"]["launch_nonce"] == line["launch_nonce"]


def test_the_operators_kill_appends_the_same_line_and_posts_it(tmp_path, receiver, monkeypatch):
    m, path = _world(tmp_path)
    monkeypatch.setenv(witness.URL_ENV, receiver)
    identity = _kill_from_outside(m, path)
    assert identity["terminated"] and identity["launch_nonce"]
    line = json.loads(witness.witness_path(path).read_text())
    assert line["reason"] == "explicit_kill:operator"
    assert line["launch_nonce"] == identity["launch_nonce"]
    assert line["release_digest"] == identity["release_digest"] == release.release_digest()
    assert _Receiver.lines == [line]


def test_an_earlier_copy_of_the_killed_diary_is_refused_before_any_state_is_restored(
        tmp_path, monkeypatch):
    m, path = _world(tmp_path)
    shutil.copytree(path.parent, tmp_path / "earlier")  # the diary, its key and its head
    earlier = tmp_path / "earlier" / path.name
    _kill_from_outside(m, path)
    # The copy carries no witness; the kill is beside its parent, not inside it.
    assert not (tmp_path / "earlier" / ".witness").exists()
    # No process memory of the kill exists: the local file alone answers.
    before = earlier.read_bytes()
    with pytest.raises(ResumeError) as refused:
        resume_runtime(m, str(earlier))
    assert refused.value.code == "identity_killed"
    failed = [i for i in rows(earlier, m) if i["kind"] == "failed_resume"]
    assert [f["reason"] for f in failed] == ["identity_killed"]
    assert failed[0]["witness"] == "local" and len(failed[0]["launch_nonce"]) == 32
    assert earlier.read_bytes().startswith(before)  # the refusal is the only new record
    # A diary read back from disk carries no lineage: once the file is gone, only a
    # receiver could refuse it (the weaker guarantee, deploy/README.md).
    assert witness.killed(world=m.name, launch_nonce=failed[0]["launch_nonce"], diary=None,
                          ledger_path=earlier, remote=False) == "local"


def test_the_cli_refuses_the_copy_with_the_reason_code_and_exit_1(tmp_path, capsys, monkeypatch):
    from factorylab.runtime.cli import _cmd_resume, build_parser

    m, path = _world(tmp_path)
    shutil.copytree(path.parent, tmp_path / "earlier")
    earlier = tmp_path / "earlier" / path.name
    _kill_from_outside(m, path)
    monkeypatch.setenv("RUNTIME_DIRECTORY", str(tmp_path / "run"))
    (tmp_path / "run").mkdir()
    args = build_parser().parse_args(["resume", "--world", "scripted", "--ledger", str(earlier)])
    assert _cmd_resume(args) == 1
    assert capsys.readouterr().err == "factorylab resume: identity_killed\n"
    assert (tmp_path / "run" / "reason").read_text() == "identity_killed\n"


def test_a_remote_kill_is_final_and_a_configured_remote_without_a_verdict_refuses(
        tmp_path, receiver, monkeypatch):
    m, path = _world(tmp_path)
    shutil.copytree(path.parent, tmp_path / "earlier")
    earlier = tmp_path / "earlier" / path.name
    monkeypatch.setenv(witness.URL_ENV, receiver)
    _kill_from_outside(m, path)
    kill = _Receiver.lines[-1]
    assert kill["event"] == "kill"
    # The host lost its witness and its memory of the kill; only the receiver knows.
    shutil.rmtree(tmp_path / ".witness")
    with pytest.raises(ResumeError) as refused:
        resume_runtime(m, str(earlier))
    assert refused.value.code == "identity_killed"
    query = _Receiver.lines[-1]
    assert query["event"] == "query" and query["launch_nonce"] == kill["launch_nonce"]
    assert query["diary"] == kill["diary"]
    failed = [i for i in rows(earlier, m) if i["kind"] == "failed_resume"]
    assert failed[-1]["witness"] == "remote"
    # A receiver is configured and cannot be reached: no verdict is a refusal, not a
    # resume on the local file alone (P1-01). The refusal is ledgered like the others.
    before = earlier.read_bytes()
    monkeypatch.setenv(witness.URL_ENV, _closed_port_url())
    with pytest.raises(ResumeError) as unreachable:
        resume_runtime(m, str(earlier))
    assert unreachable.value.code == "witness_unavailable"
    assert earlier.read_bytes().startswith(before)
    failed = [i for i in rows(earlier, m) if i["kind"] == "failed_resume"]
    assert failed[-1]["reason"] == "witness_unavailable"
    assert failed[-1]["launch_nonce"] == kill["launch_nonce"]
    # A plain-HTTP receiver off the loopback is never contacted, and a configured
    # receiver that cannot be used is no verdict either.
    monkeypatch.setenv(witness.URL_ENV, "http://example.invalid/witness")
    with pytest.raises(witness.WitnessUnavailable):
        witness.killed(world="scripted", launch_nonce=kill["launch_nonce"],
                       diary=kill["diary"], ledger_path=earlier)
    # A receiver that answers without a verdict is no verdict.
    monkeypatch.setattr(witness, "_post", lambda *a, **k: {"status": "ok"})
    monkeypatch.setenv(witness.URL_ENV, receiver)
    with pytest.raises(witness.WitnessUnavailable):
        witness.killed(world="scripted", launch_nonce=kill["launch_nonce"],
                       diary=kill["diary"], ledger_path=earlier)
    # An explicit "not killed" clears it.
    monkeypatch.setattr(witness, "_post", lambda *a, **k: {"killed": False})
    assert witness.killed(world="scripted", launch_nonce=kill["launch_nonce"],
                          diary=kill["diary"], ledger_path=earlier) is None


def test_without_a_receiver_the_local_file_alone_decides(tmp_path, monkeypatch):
    """The weaker guarantee, stated in deploy/README.md: no receiver, no remote verdict."""
    m, path = _world(tmp_path)
    # Launched without a receiver, the world never requires one afterwards.
    launch = next(i for i in Ledger.open_read_only(
        path, manifest=json.loads(m.canonical_json())).items()
        if i["kind"] == "event" and i["event"]["kind"] == "Launch")
    assert "witness_required" not in launch["event"]["payload"]
    shutil.copytree(path.parent, tmp_path / "earlier")
    earlier = tmp_path / "earlier" / path.name
    _kill_from_outside(m, path)
    with pytest.raises(ResumeError) as refused:
        resume_runtime(m, str(earlier))
    assert refused.value.code == "identity_killed"
    # The local file is all there is: once it is gone, nothing refuses the copy.
    shutil.rmtree(tmp_path / ".witness")
    assert resume_world(m, str(earlier))["ledger_verify"]


def test_the_cli_reports_witness_unavailable_with_the_retried_exit_code(tmp_path, capsys,
                                                                        monkeypatch):
    from factorylab.runtime.cli import TERMINATED_EXIT, _cmd_resume, build_parser

    m, path = _world(tmp_path)
    monkeypatch.setenv(witness.URL_ENV, _closed_port_url())
    monkeypatch.setenv("RUNTIME_DIRECTORY", str(tmp_path / "run"))
    (tmp_path / "run").mkdir()
    args = build_parser().parse_args(["resume", "--world", "scripted", "--ledger", str(path)])
    code = _cmd_resume(args)
    assert code == 1 and code != TERMINATED_EXIT  # exit 1: the unit restarts with backoff
    assert capsys.readouterr().err == "factorylab resume: witness_unavailable\n"
    assert (tmp_path / "run" / "reason").read_text() == "witness_unavailable\n"
    unit = (Path(__file__).resolve().parents[2] / "deploy" / "factorylab.service").read_text()
    assert "RestartPreventExitStatus=3\n" in unit and "Restart=always\n" in unit


def test_a_checkpoint_cannot_revive_a_killed_runtime():
    m = load_manifest("scripted")
    rt = Runtime(m, events=1, seed=1, initial_balance_micro=None, ledger_path=None)
    rt.run()
    state = runtime_state(rt)
    # The fingerprint rides beside the mapping, never in it: two runs of one manifest
    # and seed still write byte-identical checkpoints.
    assert state.diary == rt.ledger.diary_id and len(state.diary) == 64
    assert "diary" not in state
    rt.termination.kill("explicit_kill:operator")
    assert rt.termination.final and rt.ledger.identity()["terminated"]
    # Into the killed runtime itself: refused, and still final afterwards.
    with pytest.raises(ResumeError) as own:
        restore_runtime(rt, state)
    assert own.value.code == "identity_killed" and rt.termination.final
    rt.termination.kill("balance_zero")
    assert rt.termination.reason == "explicit_kill:operator"
    # Into a fresh runtime in the same process: the checkpoint names a killed identity.
    twin = Runtime(m, ledger_path=None, **state["config"])
    with pytest.raises(ResumeError) as fresh:
        restore_runtime(twin, state)
    assert fresh.value.code == "identity_killed"
    assert twin.launch_nonce != rt.launch_nonce or not twin.started


def test_a_memory_only_kill_touches_no_file(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    m = load_manifest("scripted")
    rt = Runtime(m, events=1, seed=1, initial_balance_micro=None, ledger_path=None,
                 kill_at_end=True)
    rt.run()
    assert rt.termination.final
    assert list(tmp_path.iterdir()) == []
    assert rt.kill_witness.lineage.killed


def test_a_deterministic_twin_of_the_killed_world_is_a_different_diary(tmp_path):
    """Two rehearsals of one manifest and seed share a nonce; their diaries tell them apart."""
    m = load_manifest("scripted")
    killed = tmp_path / "a" / "w.jsonl"
    killed.parent.mkdir()
    run_world(m, events=1, seed=1, ledger_path=str(killed), kill_at_end=True)
    other = tmp_path / "b" / "w.jsonl"
    other.parent.mkdir()
    run_world(m, events=1, seed=1, ledger_path=str(other))
    assert witness.witness_path(killed) == witness.witness_path(other)
    line = json.loads(witness.witness_path(killed).read_text())
    launch = next(i for i in rows(other, m)
                  if i["kind"] == "event" and i["event"]["kind"] == "Launch")
    assert launch["event"]["payload"]["launch_nonce"] == line["launch_nonce"]
    assert hashlib.sha256(_first_token(other)).hexdigest() != line["diary"]
    assert resume_world(m, str(other))["ledger_verify"]


def test_the_witness_never_raises_into_the_kill(tmp_path, monkeypatch):
    m, path = _world(tmp_path)
    monkeypatch.setattr(witness, "witness_path", lambda p: tmp_path / "runs" / "w.jsonl" / "x")
    identity = _kill_from_outside(m, path)
    assert identity["terminated"]
    def broken(path):
        raise OSError("no witness here")

    monkeypatch.setattr(witness, "witness_path", broken)
    m2, path2 = _world(tmp_path, "v")
    assert _kill_from_outside(m2, path2)["terminated"]


def test_memory_only_kill_cannot_contaminate_another_world(tmp_path):
    """s05 #2: a wind-down note belongs to the world that noted it. World A, memory-only,
    notes seven orders and a flat account and is killed; world B, on disk, is killed
    with no wind-down and is witnessed as exactly that."""
    from tests.conftest import make_runtime

    a = make_runtime()
    a.kill_witness.note_wind_down(wind_down=True, orders=7, exposure_state="flat")
    a.termination.kill("explicit_kill:operator")
    m, path = _world(tmp_path, "b")
    with LedgerLock(str(path)):
        ledger = Ledger.reopen(path, manifest=json.loads(m.canonical_json()))
        Termination(ledger=ledger, bus=Bus(ledger),
                    witness=witness.KillWitness()).kill("explicit_kill:operator")
    (line,) = [json.loads(raw) for raw in witness.witness_path(path).read_text().splitlines()]
    assert (line["wind_down"], line["wind_down_orders"], line["exposure_state"]) == (
        False, 0, "unknown")
    assert not hasattr(witness, "_pending_wind_down")


def test_kills_accumulate_nothing_global_and_a_killed_checkpoint_still_refuses():
    """s05 #2, closing review: no process-wide record of kills. Two independent kills in
    one process leave the witness module exactly as it was; each world's kill is named
    on its own lineage, which its checkpoints carry, so a checkpoint of a killed world
    still refuses to restore into a fresh runtime, and an unrelated world's does not."""
    import types

    m = load_manifest("scripted")

    def module_state():
        return {name: repr(value) for name, value in vars(witness).items()
                if not isinstance(value, types.ModuleType | type | types.FunctionType)}

    before = module_state()
    worlds = []
    for seed in (1, 2):
        rt = Runtime(m, events=1, seed=seed, initial_balance_micro=None, ledger_path=None)
        rt.run()
        worlds.append((rt, runtime_state(rt)))
    (a, state_a), (b, state_b) = worlds
    a.termination.kill("explicit_kill:operator")
    b.termination.kill("explicit_kill:operator")
    assert module_state() == before
    assert not [n for n, v in vars(witness).items()
                if not n.startswith("__") and isinstance(v, set | list | dict)]
    assert a.kill_witness.lineage is not b.kill_witness.lineage
    assert state_a["lineage"] is a.kill_witness.lineage
    assert a.kill_witness.lineage.killed and b.kill_witness.lineage.killed
    twin = Runtime(m, ledger_path=None, **state_a["config"])
    with pytest.raises(ResumeError) as refused:
        restore_runtime(twin, state_a)
    assert refused.value.code == "identity_killed"
    # A live world's checkpoint restores, and the restored runtime joins its lineage.
    c = Runtime(m, events=1, seed=3, initial_balance_micro=None, ledger_path=None)
    c.run()
    state_c = runtime_state(c)
    assert not state_c["lineage"].killed  # another world's kills are not this lineage's
    heir = Runtime(m, ledger_path=None, **state_c["config"])
    restore_runtime(heir, state_c)
    assert heir.kill_witness.lineage is c.kill_witness.lineage
    heir.termination.kill("explicit_kill:operator")
    with pytest.raises(ResumeError):
        restore_runtime(Runtime(m, ledger_path=None, **state_c["config"]), state_c)


def _killed_after_checkpoint(copy_of):
    """A memory-only world checkpointed, the checkpoint copied by ``copy_of``, then killed."""
    m = load_manifest("scripted")
    rt = Runtime(m, events=1, seed=1, initial_balance_micro=None, ledger_path=None)
    rt.run()
    saved = copy_of(runtime_state(rt))
    rt.termination.kill("explicit_kill:operator")
    return m, saved


@pytest.mark.parametrize("copy_of", [
    __import__("copy").deepcopy, __import__("copy").copy, lambda s: s.copy(), dict],
    ids=["deepcopy", "copy", "dict.copy", "dict"])
def test_no_copy_of_a_killed_worlds_checkpoint_restores(copy_of):
    """Sol on bb146b74: every copy of a checkpoint carries the one live death record."""
    m, saved = _killed_after_checkpoint(copy_of)
    twin = Runtime(m, ledger_path=None, **saved["config"])
    with pytest.raises(ResumeError) as refused:
        restore_runtime(twin, saved)
    assert refused.value.code == "identity_killed"


def test_a_serialised_memory_checkpoint_cannot_be_restored():
    """Fail closed: a memory checkpoint that lost its lineage (serialised and reloaded,
    or a lineage of the wrong type) is refused; a disk world resumes through its diary."""
    import pickle

    from factorylab.runtime.resume import durable_state

    m, saved = _killed_after_checkpoint(lambda s: s)
    reloaded = json.loads(json.dumps(durable_state(saved)))
    for state in (reloaded, {**reloaded, "lineage": object()}, {**reloaded, "lineage": True}):
        twin = Runtime(m, ledger_path=None, **saved["config"])
        with pytest.raises(ResumeError) as refused:
            restore_runtime(twin, state)
        assert refused.value.code == "lineage_missing"
    # A lineage is live state: it does not pickle, so no pickle detaches a fresh one.
    with pytest.raises(TypeError):
        pickle.dumps(saved)
