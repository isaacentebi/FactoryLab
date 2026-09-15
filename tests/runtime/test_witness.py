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
    """No receiver unless a test sets one; the process record starts empty for each test."""
    monkeypatch.delenv(witness.URL_ENV, raising=False)
    monkeypatch.setattr(witness, "_killed_here", set())


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
        Termination(ledger=ledger, bus=Bus(ledger)).kill(reason)
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
    # Forget the kill in this process so the local file alone must answer.
    monkeypatch.setattr(witness, "_killed_here", set())
    before = earlier.read_bytes()
    with pytest.raises(ResumeError) as refused:
        resume_runtime(m, str(earlier))
    assert refused.value.code == "identity_killed"
    failed = [i for i in rows(earlier, m) if i["kind"] == "failed_resume"]
    assert [f["reason"] for f in failed] == ["identity_killed"]
    assert failed[0]["witness"] == "local" and len(failed[0]["launch_nonce"]) == 32
    assert earlier.read_bytes().startswith(before)  # the refusal is the only new record
    # Killed in this process, the record answers even without the file.
    shutil.rmtree(tmp_path / ".witness")
    monkeypatch.setattr(witness, "_killed_here", {(failed[0]["launch_nonce"], None)})
    with pytest.raises(ResumeError) as again:
        resume_runtime(m, str(earlier))
    assert again.value.code == "identity_killed"
    assert [i["witness"] for i in rows(earlier, m) if i["kind"] == "failed_resume"] == [
        "local", "process"]


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


def test_a_remote_kill_is_final_and_an_unreachable_remote_is_not_a_verdict(
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
    monkeypatch.setattr(witness, "_killed_here", set())
    with pytest.raises(ResumeError) as refused:
        resume_runtime(m, str(earlier))
    assert refused.value.code == "identity_killed"
    query = _Receiver.lines[-1]
    assert query["event"] == "query" and query["launch_nonce"] == kill["launch_nonce"]
    assert query["diary"] == kill["diary"]
    failed = [i for i in rows(earlier, m) if i["kind"] == "failed_resume"]
    assert failed[-1]["witness"] == "remote"
    # A receiver that cannot be reached is logged and does not stand in for a verdict:
    # the copy resumes, because nothing this process can read says it died.
    monkeypatch.setenv(witness.URL_ENV, _closed_port_url())
    summary = resume_world(m, str(earlier))
    assert summary["ledger_verify"]
    # A plain-HTTP receiver off the loopback is never contacted at all.
    monkeypatch.setenv(witness.URL_ENV, "http://example.invalid/witness")
    assert witness.killed(world="scripted", launch_nonce=kill["launch_nonce"],
                          diary=kill["diary"], ledger_path=earlier) is None


def test_a_checkpoint_cannot_revive_a_killed_runtime():
    m = load_manifest("scripted")
    rt = Runtime(m, events=1, seed=1, initial_balance_micro=None, ledger_path=None,
                 drip=True, router_gamma=.1)
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
                 drip=True, router_gamma=.1, kill_at_end=True)
    rt.run()
    assert rt.termination.final
    assert list(tmp_path.iterdir()) == []
    assert (rt.launch_nonce, rt.ledger.diary_id) in witness._killed_here


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
