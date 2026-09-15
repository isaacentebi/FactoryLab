"""Edition 2 hard casts (C4): the release that launched a world is the only one that resumes it.

Cold audit F1: identity bound the manifest and the venue account, never the
executable. These witnesses run the scripted world through a real ledger file:
Launch carries the digest, a different digest is refused with its own reason
code and a ledgered ``failed_resume``, the same digest resumes past that note,
and the deploy witness records the same facts outside the diary.
"""

import hashlib
import json
import os
import subprocess
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from threading import Thread

import pytest

from factorylab.kernel.events import EventKind
from factorylab.kernel.ledger import Ledger
from factorylab.runtime import release
from factorylab.runtime.cli import main
from factorylab.runtime.loop import run_world
from factorylab.runtime.reasons import Reason
from factorylab.runtime.resume import (
    ResumeError,
    restore_runtime,
    resume_reason,
    resume_world,
    runtime_state,
)
from factorylab.runtime.worlds import load_manifest
from tests.conftest import make_runtime

REPO = Path(__file__).resolve().parents[2]
OTHER_RELEASE = "f" * 64


def items(path, manifest):
    return Ledger.reopen(path, manifest=json.loads(manifest.canonical_json()))._recovery_items()


def launched(tmp_path):
    manifest = load_manifest("scripted")
    path = tmp_path / "world.jsonl"
    run_world(manifest, events=2, seed=1, ledger_path=str(path))
    return manifest, path


def test_launch_carries_the_release_digest_of_the_executing_code(tmp_path):
    manifest, path = launched(tmp_path)
    launch = next(item for item in items(path, manifest)
                  if item.get("kind") == "event" and item["event"]["kind"] == str(EventKind.LAUNCH))
    assert launch["event"]["payload"]["release_digest"] == release.release_digest()
    snapshot = next(item for item in items(path, manifest) if item.get("kind") == "snapshot")
    saved = dict(pair for pair in snapshot["state"]["runtime"]["$map"])
    assert saved["release_digest"] == release.release_digest()


def test_a_different_release_is_refused_with_a_ledgered_failed_resume(tmp_path, monkeypatch):
    manifest, path = launched(tmp_path)
    before = path.read_bytes()
    monkeypatch.setattr(release, "release_digest", lambda root=None: OTHER_RELEASE)
    with pytest.raises(ResumeError) as refused:
        resume_world(manifest, str(path))
    assert refused.value.code == "release_mismatch"
    assert resume_reason(refused.value) is Reason.RELEASE_MISMATCH
    # The diary grew by the refusal and nothing else; no earlier byte changed.
    assert path.read_bytes().startswith(before) and path.read_bytes() != before
    failed = [item for item in items(path, manifest) if item.get("kind") == "failed_resume"]
    assert len(failed) == 1
    assert failed[0]["reason"] == "release_mismatch"
    assert failed[0]["running_release_digest"] == OTHER_RELEASE
    assert failed[0]["ledgered_release_digest"] == release._info(str(release.ROOT))[
        "release_digest"]
    assert not any(item.get("kind") == "resume.begin" for item in items(path, manifest))


def test_the_same_release_resumes_past_a_refused_attempt(tmp_path, monkeypatch):
    manifest, path = launched(tmp_path)
    with monkeypatch.context() as patched:
        patched.setattr(release, "release_digest", lambda root=None: OTHER_RELEASE)
        with pytest.raises(ResumeError):
            resume_world(manifest, str(path))
    summary = resume_world(manifest, str(path))
    assert summary["stats"]["resumes"] == 1 and summary["ledger_verify"]
    kinds = [item.get("kind") for item in items(path, manifest)]
    assert kinds.count("failed_resume") == 1 and kinds.count("resume.begin") == 1
    assert kinds.index("failed_resume") < kinds.index("resume.begin")


def test_the_command_names_the_mismatch_where_the_supervisor_reads_it(tmp_path, monkeypatch,
                                                                       capsys):
    manifest, path = launched(tmp_path)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("RUNTIME_DIRECTORY", str(tmp_path / "run"))
    (tmp_path / "run").mkdir()
    monkeypatch.setattr(release, "release_digest", lambda root=None: OTHER_RELEASE)
    assert main(["resume", "--world", "scripted", "--ledger", str(path)]) == 1
    assert capsys.readouterr().err == f"factorylab resume: {Reason.RELEASE_MISMATCH.value}\n"
    assert (tmp_path / "run" / "reason").read_text() == "release_mismatch\n"


def test_a_checkpoint_before_release_identity_restores_and_is_bound_from_then_on():
    original = make_runtime()
    state = runtime_state(original)
    state["runtime"]["$map"] = [pair for pair in state["runtime"]["$map"]
                                if pair[0] != "release_digest"]
    legacy = make_runtime()
    restore_runtime(legacy, state)
    # Not yet launched: the historical Launch carried no digest and replays as such.
    assert legacy.release_digest is None
    original._launch()
    state = runtime_state(original)
    state["runtime"]["$map"] = [pair for pair in state["runtime"]["$map"]
                                if pair[0] != "release_digest"]
    continued = make_runtime()
    restore_runtime(continued, state)
    assert continued.release_digest == release.release_digest()


class _Receiver(BaseHTTPRequestHandler):
    bodies: list[bytes] = []

    def do_POST(self):
        length = int(self.headers.get("Content-Length", "0"))
        _Receiver.bodies.append(self.rfile.read(length))
        self.send_response(200)
        self.end_headers()

    def log_message(self, *args):  # keep the test output quiet
        pass


@pytest.fixture
def receiver():
    _Receiver.bodies = []
    server = HTTPServer(("127.0.0.1", 0), _Receiver)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()


def _witness(tmp_path, url, *args):
    env = {**os.environ, "FACTORYLAB_ROOT": str(tmp_path), "FACTORYLAB_REPO": str(REPO),
           "FACTORYLAB_WORLD": "rehearsal", "FACTORYLAB_PYTHON": sys.executable}
    if url is not None:
        env["FACTORYLAB_WITNESS_URL"] = url
    return subprocess.run(["bash", str(REPO / "deploy" / "witness.sh"), *args],
                          env=env, capture_output=True, text=True, timeout=120)


def test_the_witness_appends_one_line_per_event_and_posts_the_same_line(tmp_path, receiver):
    (tmp_path / "runs").mkdir()
    ledger = tmp_path / "runs" / "rehearsal.jsonl"
    ledger.write_bytes(b'{"format":1,"genesis_hash":"fixture"}\n')
    url = f"http://127.0.0.1:{receiver.server_address[1]}/witness"
    assert _witness(tmp_path, url, "launch").returncode == 0
    assert _witness(tmp_path, url, "failed_resume", "release_mismatch").returncode == 0
    assert _witness(tmp_path, url, "kill").returncode == 0
    lines = (tmp_path / ".witness" / "rehearsal.jsonl").read_text().splitlines()
    assert [json.loads(line)["event"] for line in lines] == ["launch", "failed_resume", "kill"]
    for line in lines:
        record = json.loads(line)
        assert record["world"] == "rehearsal"
        assert record["release_digest"] == release.release_digest()
        assert record["ledger_head"] == hashlib.sha256(ledger.read_bytes()).hexdigest()
        assert record["ts"].endswith("Z")
    assert json.loads(lines[1])["reason"] == "release_mismatch"
    assert "reason" not in json.loads(lines[0])
    assert _Receiver.bodies == [(line + "\n").encode() for line in lines]


def test_the_witness_refuses_unknown_events_and_survives_a_dead_receiver(tmp_path):
    (tmp_path / "runs").mkdir()
    assert _witness(tmp_path, None, "upgrade").returncode == 2
    assert not (tmp_path / ".witness" / "rehearsal.jsonl").exists()
    result = _witness(tmp_path, "http://127.0.0.1:9/witness", "dormant")
    assert result.returncode == 0
    record = json.loads((tmp_path / ".witness" / "rehearsal.jsonl").read_text())
    assert record["event"] == "dormant" and record["ledger_head"] == "absent"
    # A non-loopback plain-HTTP receiver is never contacted, and still appended.
    assert _witness(tmp_path, "http://example.invalid/witness", "kill").returncode == 0


def test_install_records_the_release_beside_a_checkout(tmp_path):
    checkout = tmp_path / "checkout"
    (checkout / "factorylab").mkdir(parents=True)
    (checkout / "factorylab" / "__init__.py").write_text("")
    (checkout / "uv.lock").write_text("version = 1\n")
    result = subprocess.run(
        ["bash", str(REPO / "deploy" / "install.sh"), str(checkout)],
        env={**os.environ, "FACTORYLAB_PYTHON": sys.executable, "PYTHONPATH": str(REPO)},
        capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, result.stderr
    record = json.loads((checkout / "RELEASE").read_text())
    assert result.stdout.strip() == record["release_digest"]
    assert record["git_head_source"] == "none" and record["git_head"] == release.UNRECORDED
    assert oct((checkout / "RELEASE").stat().st_mode & 0o777) == "0o644"
