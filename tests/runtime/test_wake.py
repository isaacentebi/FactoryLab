import hashlib
import json
import os
import subprocess
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest

from factorylab.kernel.ledger import Ledger, LedgerIntegrityError
from factorylab.runtime.cli import TERMINATED_EXIT, main
from factorylab.runtime.loop import run_world
from factorylab.runtime.wake import (
    UNAVAILABLE,
    VIEWS,
    _reserve,
    _venue,
    collect_wake,
    render_wake,
)
from factorylab.runtime.worlds import load_manifest

DEPLOY = Path(__file__).resolve().parents[2] / "deploy"


@pytest.fixture(autouse=True)
def isolated(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    for name in ("HL_PRIVATE_KEY", "RESERVE_PRIVATE_KEY", "OPENROUTER_API_KEY"):
        monkeypatch.delenv(name, raising=False)

    def deny(*args, **kwargs):
        pytest.fail("unexpected network request")

    monkeypatch.setattr("urllib.request.OpenerDirector.open", deny)
    monkeypatch.setattr("requests.sessions.Session.request", deny)


@pytest.fixture
def world(tmp_path, scripted_run):
    """A finished scripted world in its own directory, not the one the test chdir'd into."""
    return scripted_run("scripted", 5, 1).copy_to(tmp_path / "world")


def test_sealed_item_text_never_appears(world, tmp_path):
    manifest = load_manifest("scripted")
    writer = Ledger.reopen(world, manifest=json.loads(manifest.canonical_json()))
    marker = '<script>SECRET_PROMPT_VERDICT_DIARY</script>'
    writer.append({"kind": "private", "prompt": marker, "verdict": marker, "diary": marker})
    out = tmp_path / "public"
    assert main(["wake", "--ledger", str(world), "--out", str(out)]) == 0
    for path in out.iterdir():
        assert "SECRET_PROMPT_VERDICT_DIARY" not in path.read_text()
    malicious = dict.fromkeys(VIEWS, {"counts": {marker: 1}})
    malicious["wallet_series"] = {"series": []}
    assert "<script>" not in render_wake(malicious)
    assert "&lt;script&gt;" in render_wake(malicious)


def test_aggregate_verification_failure_discards_all_partial_views(world, monkeypatch):
    calls, sleeps = [], []
    original = Ledger.aggregate

    def fail(self, view):
        calls.append(view)
        if view == "invocations_by_assembly":
            raise LedgerIntegrityError("synthetic private failure")
        return original(self, view)

    monkeypatch.setattr(Ledger, "aggregate", fail)
    result = collect_wake(world, sleep=sleeps.append)
    assert calls.count("wallet_series") == 2 and sleeps == [0.1]
    assert set(result.values()) == {UNAVAILABLE}


def test_optional_accounts_are_projected_and_independent(world, monkeypatch):
    monkeypatch.setenv("HL_PRIVATE_KEY", "synthetic")
    monkeypatch.setenv("RESERVE_PRIVATE_KEY", "synthetic")
    venue = SimpleNamespace(
        account=lambda: SimpleNamespace(equity_usd=Decimal("1.123456"), positions=(
            SimpleNamespace(coin="BTC", size=Decimal(".2"), entry_px=Decimal("100")),
        ), private="NEVER SHOW"),
        _guarded=lambda label, call: call(), _address="fixture",
        _info=SimpleNamespace(user_fills_by_time=lambda *a: [
            {"closedPnl": "0.000003", "time": 1, "private": "NEVER SHOW"},
        ]),
    )
    monkeypatch.setattr("factorylab.world.exchange.HyperliquidExchange", lambda **kw: venue)

    def fail():
        raise RuntimeError("NEVER SHOW")

    monkeypatch.setattr("factorylab.world.x402.X402Client", lambda: SimpleNamespace(
        usdc_balance=lambda: 12, venice_balance=fail,
    ))
    assert _venue(load_manifest("testnet")) == {"equity_micro": 1123456,
                                               "realized_to_date_micro": 3}
    assert _reserve() == {"usdc_micro": 12, "venice_micro": UNAVAILABLE}
    # The keys are in the environment, but this world owns neither account: a
    # fake world's page must not carry the architect's real equity beside its own.
    result = collect_wake(world)
    assert result["venue"] == {"equity_micro": UNAVAILABLE,
                               "realized_to_date_micro": UNAVAILABLE}
    assert result["reserve"] == {"usdc_micro": UNAVAILABLE, "venice_micro": UNAVAILABLE}
    assert "NEVER SHOW" not in json.dumps(result)


def test_terminated_world_exit_contract_and_wake(tmp_path, capsys):
    path = tmp_path / "dead.jsonl"
    run_world(load_manifest("scripted"), events=1, seed=1, ledger_path=str(path), kill_at_end=True)
    # Finality remains final even when a provider key file would fail credential loading.
    (tmp_path / "reserve.key").write_text("synthetic-invalid-credential")
    (tmp_path / "reserve.key").chmod(0o644)
    before = path.read_bytes()
    assert main(["resume", "--world", "scripted", "--ledger", str(path)]) == TERMINATED_EXIT == 3
    assert path.read_bytes() == before
    assert collect_wake(path)["world"] == "scripted"
    assert capsys.readouterr().err == "factorylab resume: terminated\n"
    unit = (DEPLOY / "factorylab.service").read_text()
    assert f"RestartPreventExitStatus={TERMINATED_EXIT}\n" in unit
    assert f"SuccessExitStatus={TERMINATED_EXIT}\n" in unit
    assert "Restart=always\n" in unit and "RestartSec=30s\n" in unit


def test_resume_returns_final_code_when_world_dies_during_resume(world, monkeypatch, capsys):
    monkeypatch.setattr(
        "factorylab.runtime.resume.resume_world", lambda *a, **kw: {"terminated": True},
    )
    assert main(["resume", "--world", "scripted", "--ledger", str(world)]) == TERMINATED_EXIT


@pytest.mark.parametrize("exists", [False, True])
def test_supervisor_first_launch_and_restart_agree_on_exit_code(tmp_path, exists):
    root, runtime = tmp_path / "factory", tmp_path / "runtime"
    (root / "repo/.venv/bin").mkdir(parents=True)
    (root / "runs").mkdir()
    runtime.mkdir()
    cli = root / "repo/.venv/bin/factorylab"
    cli.write_text('#!/bin/bash\nprintf "%s\\n" "$1" >> "$CALLS"\n'
                   'if [[ $1 == run ]]; then touch "$LEDGER"; exit 0; fi\nexit 3\n')
    cli.chmod(0o700)
    # start.sh proves the jail before the first run; here the probe is a stand-in.
    python = root / "repo/.venv/bin/python"
    python.write_text('#!/bin/bash\nprintf "%s\n" "jail-check $*" >> "$CALLS"\nexit 0\n')
    python.chmod(0o700)
    ledger = root / "runs/funded.jsonl"
    if exists:
        ledger.touch()
    script = (DEPLOY / "start.sh").read_text().replace("/srv/factorylab", str(root))
    script = script.replace("/run/factorylab", str(runtime))
    calls = tmp_path / "calls"
    proc = subprocess.run(["bash", "-c", script], env={**os.environ, "CALLS": str(calls),
                                                      "LEDGER": str(ledger)}, capture_output=True)
    assert proc.returncode == TERMINATED_EXIT
    assert calls.read_text().splitlines() == (
        ["resume"] if exists else ["jail-check -m factorylab.cortex.sandbox", "run", "resume"]
    )
    assert (runtime / "mode").read_text().strip() == "resume"


@pytest.mark.parametrize("damage", ["ciphertext", "reorder", "header", "missing_key"])
def test_untrusted_snapshot_never_emits_aggregates(tmp_path, damage, scripted_run):
    # Each damage gets every ledger sidecar in a private directory.
    world = scripted_run("scripted", 5, 1).copy_to(tmp_path / f"damaged-{damage}")
    lines = world.read_bytes().splitlines(keepends=True)
    if damage == "ciphertext":
        # One byte of the last token, always changed: a Fernet token is base64url,
        # and the byte at this offset varies with the world's own random key.
        record = json.loads(lines[-1])
        flipped = "Y" if record["item"][30] == "X" else "X"
        record["item"] = record["item"][:30] + flipped + record["item"][31:]
        lines[-1] = json.dumps(record).encode() + b"\n"
    elif damage == "reorder":
        lines[1], lines[2] = lines[2], lines[1]
    elif damage == "header":
        lines[0] = b'{"format":1,"genesis_hash":"wrong"}\n'
    else:
        Path(str(world) + ".key").unlink()
    world.write_bytes(b"".join(lines))
    assert set(collect_wake(world, sleep=lambda _: None).values()) == {UNAVAILABLE}


@pytest.mark.parametrize("code,status,mode,event", [
    ("exited", "3", "resume", "terminated"),
    ("exited", "1", "resume", "failed_resume"),
    ("killed", "KILL", "resume", "failed_resume"),
    ("exited", "0", "resume", None),
    ("exited", "1", "run", None),
])
@pytest.mark.parametrize("reason", [None, "manifest_mismatch", "not a reason code"])
def test_alert_posts_only_allowed_json_line(tmp_path, code, status, mode, event, reason):
    runtime, bin_path = tmp_path / "runtime", tmp_path / "bin"
    runtime.mkdir()
    bin_path.mkdir()
    (runtime / "mode").write_text(mode)
    if reason is not None:
        (runtime / "reason").write_text(reason + "\n")
    expected_reason = reason if reason == "manifest_mismatch" else "none"
    curl = bin_path / "curl"
    # Capture the wire body and prove the webhook was not passed as an argument.
    curl.write_text('#!/usr/bin/env python3\nimport json, os, sys\n'
                    'from pathlib import Path\n'
                    'assert "webhook-secret" not in " ".join(sys.argv)\n'
                    'assert "webhook-secret" in sys.stdin.read()\n'
                    'body = sys.argv[sys.argv.index("--data-binary") + 1]\n'
                    'Path(os.environ["CAPTURE"]).write_text(body)\n')
    curl.chmod(0o700)
    script = (DEPLOY / "alert.sh").read_text().replace("/run/factorylab", str(runtime))
    capture = tmp_path / "capture"
    proc = subprocess.run(["bash", "-c", script], capture_output=True, env={
        **os.environ, "PATH": str(bin_path) + os.pathsep + os.environ["PATH"],
        "EXIT_CODE": code, "EXIT_STATUS": status, "CAPTURE": str(capture),
        "FACTORY_WEBHOOK_URL": "https://example.invalid/webhook-secret",
    })
    assert proc.returncode == 0 and not proc.stdout and not proc.stderr
    if event:
        assert capture.read_text().endswith("\n")
        assert json.loads(capture.read_text()) == {
            "world": "funded", "event": event, "reason": expected_reason
        }
    else:
        assert not capture.exists()


def test_backup_captures_complete_prefix_and_pipes_to_age_before_upload(world, tmp_path):
    import tarfile

    root, bin_path = tmp_path / "factory", tmp_path / "bin"
    (root / "runs").mkdir(parents=True)
    bin_path.mkdir()
    original = world.read_bytes()
    (root / "runs/funded.jsonl").write_bytes(original + b'{"item":')
    relatives = ("openrouter.key", "hyperliquid.key", "reserve.key", "runs/funded.jsonl.key")
    for relative in relatives:
        path = root / relative
        path.write_text("synthetic-fixture-only")
        path.chmod(0o600)
    manifest = root / "repo/worlds/funded.toml"
    manifest.parent.mkdir(parents=True)
    manifest.write_text('name = "funded"\n# exact synthetic launch bytes\n')
    # The sidecars the diary names by hash (wave 17): the rolling checkpoint, an
    # artifact and a recorded answer, each beside an in-progress temporary.
    artifact = b"a seat's kept note"
    sidecars = {
        "runs/funded.checkpoint/" + "c" * 64: b"sealed checkpoint",
        "runs/funded.artifacts/" + hashlib.sha256(artifact).hexdigest(): artifact,
        "runs/funded.io/" + "d" * 64: b"sealed answer",
    }
    for relative, data in sidecars.items():
        (root / relative).parent.mkdir(exist_ok=True)
        (root / relative).write_bytes(data)
        ((root / relative).parent / ".tmp-torn").write_bytes(b"partial")
    # These stand-ins validate orchestration, not age's cryptography or remote connectivity.
    age = bin_path / "age"
    age.write_text('#!/usr/bin/env python3\nimport sys\nfrom pathlib import Path\n'
                   'assert sys.argv[1:4] == ["--encrypt", "--recipient", "age1-fixture"]\n'
                   'Path(sys.argv[5]).write_bytes(sys.stdin.buffer.read())\n')
    rclone = bin_path / "rclone"
    rclone.write_text('#!/usr/bin/env python3\nimport os, shutil, sys\n'
                      'assert sys.argv[1] == "copyto"\n'
                      'assert sys.argv[3].startswith("fixture:bucket/factorylab-")\n'
                      'assert sys.argv[3].endswith(".tar.age")\n'
                      'shutil.copyfile(sys.argv[2], os.environ["CAPTURE"])\n')
    age.chmod(0o700)
    rclone.chmod(0o700)
    script = (DEPLOY / "backup.sh").read_text().replace("/srv/factorylab", str(root))
    capture = tmp_path / "captured.tar"
    proc = subprocess.run(["bash", "-c", script], capture_output=True, env={
        **os.environ, "PATH": str(bin_path) + os.pathsep + os.environ["PATH"],
        "AGE_RECIPIENT": "age1-fixture", "BACKUP_REMOTE": "fixture:bucket",
        "RCLONE_CONFIG": str(tmp_path / "fixture.conf"), "CAPTURE": str(capture),
        "COPYFILE_DISABLE": "1",  # macOS tar metadata is absent on the Ubuntu target.
    })
    assert proc.returncode == 0, proc.stderr.decode()
    with tarfile.open(capture) as archive:
        assert {m.name for m in archive if m.isfile()} == {
            "runs/funded.jsonl", "runs/funded.release.json", "repo/worlds/funded.toml",
            *relatives, *sidecars}
        assert archive.extractfile("runs/funded.jsonl").read() == original
        for relative, data in sidecars.items():
            assert archive.extractfile(relative).read() == data
        assert archive.extractfile("repo/worlds/funded.toml").read() == manifest.read_bytes()
        # The release record travels beside the ledger and names the exact bytes archived
        # (C4). This fixture root carries no package, so the digest is declared missing.
        record = json.loads(archive.extractfile("runs/funded.release.json").read())
        assert record["release_digest"] == "unavailable"
        assert record["ledger"] == {"bytes": len(original),
                                    "sha256": hashlib.sha256(original).hexdigest()}
        assert record["checkpoint"] == {"count": 1, "bytes": len(b"sealed checkpoint")}
        assert record["io"] == {"count": 1, "bytes": len(b"sealed answer")}
        assert record["artifacts"] == {"count": 1, "bytes": len(artifact)}
    # The pin that kept the sidecars' bytes through the copy is gone.
    assert not (root / "runs/.backup-pin").exists()
    assert (root / "runs/funded.jsonl").read_bytes() == original + b'{"item":'
