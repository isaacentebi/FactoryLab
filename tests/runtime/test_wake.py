import hashlib
import json
import os
import subprocess
from decimal import Decimal
from html.parser import HTMLParser
from pathlib import Path
from types import SimpleNamespace

import pytest

from factorylab.kernel.ledger import Ledger, LedgerIntegrityError
from factorylab.runtime.cli import TERMINATED_EXIT, main
from factorylab.runtime.loop import run_world
from factorylab.runtime.wake import (
    SECTIONS,
    UNAVAILABLE,
    VIEWS,
    _open_snapshot,
    _realized,
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


class Page(HTMLParser):
    def __init__(self, source):
        super().__init__()
        self.tags, self.text = [], []
        self.feed(source)

    def handle_starttag(self, tag, attrs):
        self.tags.append((tag, dict(attrs)))

    def handle_data(self, data):
        self.text.append(data)


def test_cli_exact_public_content_and_self_contained_page(world, tmp_path, capsys):
    ledger, manifest = _open_snapshot(world)
    before = world.read_bytes()
    out = tmp_path / "public"
    assert main(["wake", "--ledger", str(world), "--out", str(out)]) == 0
    assert capsys.readouterr() == ("", "")
    data = json.loads((out / "wake.json").read_text())
    assert set(data) == {*VIEWS, *SECTIONS, "world", "manifest_hash", "uptime_ns",
                         "last_event_time_ns"}
    assert data["world"] == "scripted" and data["manifest_hash"] == manifest.manifest_hash()
    assert data["last_event_time_ns"] > 0 and data["uptime_ns"] == data["last_event_time_ns"]
    for view in VIEWS:
        assert data[view] == ledger.public_aggregates(manifest)[view]
    assert not ledger.seal_key_released()
    with pytest.raises(PermissionError):
        _ = ledger.key_store.key
    with pytest.raises(PermissionError):
        ledger.append({"kind": "bad"})
    assert world.read_bytes() == before
    assert {p.name for p in out.iterdir()} == {"wake.html", "wake.json"}
    page = Page((out / "wake.html").read_text())
    assert any(tag == "svg" for tag, _ in page.tags)
    assert any(tag == "details" and "open" not in attrs for tag, attrs in page.tags)
    assert (out / "wake.html").read_text().index("<h2>world") < (
        out / "wake.html"
    ).read_text().index("<h2>wallet_series")
    assert any(tag == "meta" and attrs.get("name") == "viewport" for tag, attrs in page.tags)
    assert not {tag for tag, _ in page.tags} & {"script", "link", "iframe", "img", "object"}
    assert not any(set(attrs) & {"src", "href", "srcset", "action"} for _, attrs in page.tags)
    for field in data:
        assert field in "".join(page.text)
    assert "url(" not in (out / "wake.html").read_text()


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


def test_mid_write_retries_once_then_succeeds(world):
    original = world.read_bytes()
    world.write_bytes(original + b'{"item":')
    sleeps = []

    def finish(delay):
        sleeps.append(delay)
        world.write_bytes(original)

    result = collect_wake(world, sleep=finish)
    assert result["world"] == "scripted" and sleeps == [0.1]


def test_mid_write_second_failure_replaces_stale_artifacts(world, tmp_path, monkeypatch):
    out = tmp_path / "public"
    assert main(["wake", "--ledger", str(world), "--out", str(out)]) == 0
    with world.open("ab") as stream:
        stream.write(b'{"item":')
    sleeps = []
    result = collect_wake(world, sleep=sleeps.append)
    assert set(result.values()) == {UNAVAILABLE} and sleeps == [0.1]
    assert main(["wake", "--ledger", str(world), "--out", str(out)]) == 1
    assert set(json.loads((out / "wake.json").read_text()).values()) == {UNAVAILABLE}


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


def test_snapshot_is_stable_across_appends(world):
    frozen, manifest = _open_snapshot(world)
    before = frozen.aggregate("wallet_series")
    writer = Ledger.reopen(world, manifest=json.loads(manifest.canonical_json()))
    writer.append({"kind": "wallet.settle", "balance_after": 123, "amount": 1,
                   "handle": "fixture", "reason": "fixture"})
    assert frozen.verify() and frozen.aggregate("wallet_series") == before
    assert collect_wake(world)["wallet_series"]["series"][-1]["balance"] == 123


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


def test_wake_venue_is_built_with_spot_pairs_and_counts_spot_equity(world, monkeypatch):
    from factorylab.world.exchange import HyperliquidExchange

    monkeypatch.setenv("HL_PRIVATE_KEY", "synthetic")
    built = {}

    def construct(**kwargs):
        built.update(kwargs)
        venue = HyperliquidExchange.__new__(HyperliquidExchange)
        venue.coins, venue.spot_pairs = kwargs["coins"], kwargs.get("spot_pairs", ())
        venue._address, venue._last_account = "fixture", None
        venue._spot_marks = {"BTC": "@7"}
        venue._guarded = lambda label, call: call()
        venue._info = SimpleNamespace(
            all_mids=lambda: {"BTC": "100", "@7": "100"},
            user_state=lambda _: {"marginSummary": {"accountValue": "1",
                                                    "totalMarginUsed": "0"}},
            spot_user_state=lambda _: {"balances": [{"coin": "USDC", "total": "2"},
                                                    {"coin": "BTC", "total": "3"}]},
            user_fills_by_time=lambda *a: [],
        )
        return venue

    monkeypatch.setattr("factorylab.world.exchange.HyperliquidExchange", construct)
    venue = _venue(load_manifest("scripted"))
    assert built["spot_pairs"] == load_manifest("scripted").exchange.spot_pairs == ("BTC/USDC",)
    # 1 USDC of perps account value, 2 of spot cash and 3 BTC marked at 100.
    assert venue == {"equity_micro": 303_000_000, "realized_to_date_micro": 0}
    # ...and the venue of a world whose exchange is fake is never constructed at all.
    built.clear()
    assert collect_wake(world)["venue"] == {"equity_micro": UNAVAILABLE,
                                            "realized_to_date_micro": UNAVAILABLE}
    assert built == {}


def test_realized_pagination_preserves_boundary_and_refuses_retention_limit():
    calls = []
    rows = [{"time": n, "closedPnl": "0.000001"} for n in range(2001)]

    def fetch(address, start):
        calls.append(start)
        return [row for row in rows if row["time"] >= start][:2000]

    venue = SimpleNamespace(_guarded=lambda label, call: call(), _address="fixture",
                            _info=SimpleNamespace(user_fills_by_time=fetch))
    assert _realized(venue) == 2001 and calls == [0, 1999]
    rows[:] = [{"time": n, "closedPnl": "1"} for n in range(10000)]
    assert _realized(venue) == UNAVAILABLE
    rows[:] = [{"time": 0, "closedPnl": "1"} for n in range(2000)]
    assert _realized(venue) == UNAVAILABLE


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


def test_resume_other_failures_remain_retryable_and_sanitized(world, monkeypatch, capsys):
    def fail(*args, **kwargs):
        raise RuntimeError("PRIVATE KEY OR PROVIDER BODY")

    monkeypatch.setattr("factorylab.runtime.resume.resume_world", fail)
    assert main(["resume", "--world", "scripted", "--ledger", str(world)]) == 1
    assert "PRIVATE" not in str(capsys.readouterr())


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


def test_live_uptime_uses_first_tick_and_stops_at_termination(tmp_path):
    path = tmp_path / "live.jsonl"
    manifest = load_manifest("testnet")
    ledger = Ledger(path, manifest=json.loads(manifest.canonical_json()),
                    key_path=str(path) + ".key")
    for kind, timestamp in (("Launch", 0), ("Tick", 1000), ("Tick", 2000)):
        ledger.append({"kind": "event", "event": {"kind": kind, "ts_ns": timestamp}})
    data = collect_wake(path, now_ns=4000)
    assert data["uptime_ns"] == 3000 and data["last_event_time_ns"] == 2000
    ledger.append({"kind": "event", "event": {"kind": "Terminated", "ts_ns": 2500}})
    assert collect_wake(path, now_ns=5000)["uptime_ns"] == 1500


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
            *relatives}
        assert archive.extractfile("runs/funded.jsonl").read() == original
        assert archive.extractfile("repo/worlds/funded.toml").read() == manifest.read_bytes()
        # The release record travels beside the ledger and names the exact bytes archived
        # (C4). This fixture root carries no package, so the digest is declared missing.
        record = json.loads(archive.extractfile("runs/funded.release.json").read())
        assert record["release_digest"] == "unavailable"
        assert record["ledger"] == {"bytes": len(original),
                                    "sha256": hashlib.sha256(original).hexdigest()}
    assert (root / "runs/funded.jsonl").read_bytes() == original + b'{"item":'
