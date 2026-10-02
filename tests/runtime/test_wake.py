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
    write_wake,
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
    for path in out.rglob("*"):  # the public names and every generation behind them
        if path.is_file():
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
    assert calls.count("spend_by_capability") == 2 and sleeps == [0.1]
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


@pytest.mark.parametrize("verifier", ["proves", "absent", "refuses"])
def test_backup_captures_complete_prefix_and_pipes_to_age_before_upload(world, tmp_path,
                                                                         verifier):
    """The staged copy is uploaded only when the release's interpreter proved it
    restorable: an absent interpreter or a refused proof uploads nothing."""
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
    # The release's interpreter, standing in for verify_restorable: it records what it
    # was asked to prove. It has no package, so the release module is unavailable.
    verified = tmp_path / "verified.json"
    if verifier != "absent":
        interpreter = root / "repo/.venv/bin/python"
        interpreter.parent.mkdir(parents=True)
        interpreter.write_text(
            '#!/usr/bin/env python3\nimport json, os, sys\n'
            'if "factorylab.runtime.release" in sys.argv: sys.exit(1)\n'
            'assert sys.argv[1:3] == ["-m", "factorylab.runtime.sidecar"]\n'
            'open(os.environ["VERIFIED"], "w").write(json.dumps(sys.argv[3:]))\n'
            f'sys.exit({0 if verifier == "proves" else 1})\n')
        interpreter.chmod(0o700)
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
        "VERIFIED": str(verified),
    })
    if verifier != "proves":
        assert proc.returncode != 0
        assert b"backup not uploaded" in proc.stderr
        assert (b"no release interpreter" in proc.stderr) == (verifier == "absent")
        assert not capture.exists()
        return
    assert proc.returncode == 0, proc.stderr.decode()
    staged = json.loads(verified.read_text())
    assert [Path(p).name for p in staged] == ["funded.jsonl", "funded.toml"]
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


def _returns_diary(path: Path, count: int) -> None:
    """``count`` decisions, each with one 4 KiB return, a price window every ten, and a
    verdict on each return three windows after it, past its decision's settlement."""
    manifest = load_manifest("scripted")
    ledger = Ledger(path, manifest=json.loads(manifest.canonical_json()),
                    key_path=str(path) + ".key", clock_ns=lambda: 1)
    late = []
    for n in range(count):
        handle = f"decision-{n}"
        ledger.append({"kind": "decision.open", "handle": handle, "channel": "verdict",
                       "deadline_ns": 5, "propensity": {"chosen": "seed-decider"}})
        ledger.append({"kind": "invocation", "handle": handle, "assembly_id": "seed-decider",
                       "role": "producer", "outputs": {"action": "hold",
                                                       "rationale": f"{n:06d}" + "x" * 4096}})
        ledger.append({"kind": "decision.settle", "latency_ns": 1, "return": {"handle": handle,
                                                             "channel": "verdict"}})
        late.append(handle)
        if n % 10 == 9:
            ledger.append({"kind": "price.window", "window": n // 10})
            for about in late[:-30]:
                ledger.append({"kind": "event", "event": {
                    "kind": "Verdict", "ts_ns": 1,
                    "payload": {"about_handle": about, "evaluator_handle": "judge",
                                "verdict": 1.0, "rationale": "late"}}})
            late = late[-30:]


@pytest.mark.gate
def test_the_wake_holds_outstanding_returns_not_every_return(tmp_path):
    """s09: the wake's memory follows the returns still being joined, not every return
    the world ever made (essay II.II.b, "memory"; AGENTS rule 12). Ten times the history
    leaves the peak where it was; the totals, the latest rows and every page still hold
    every return with the verdicts that landed windows after it."""
    import tracemalloc

    peaks = []
    for count in (100, 1000):
        path = tmp_path / f"w{count}" / "world.jsonl"
        path.parent.mkdir()
        _returns_diary(path, count)
        if not peaks:  # the first read's one-time costs (manifests, imports) are not history
            write_wake(path, tmp_path / "warm", returns=10)
        tracemalloc.start()
        data = write_wake(path, path.parent / "www", returns=10)
        peaks.append(tracemalloc.get_traced_memory()[1])
        tracemalloc.stop()
        assert data["returns"]["total"] == count
        rows = data["returns"]["rows"]
        assert [row["handle"] for row in rows] == [f"decision-{n}"
                                                   for n in range(count - 10, count)]
        page = json.loads((path.parent / "www" / "returns-3.json").read_text())
        assert len(page["rows"]) == 10
        assert all(row["verdicts"] and row["verdicts"][0]["rationale"] == "late"
                   for row in page["rows"])
    assert peaks[1] - peaks[0] < 1_000_000, peaks


def test_a_return_joins_what_was_said_about_it_read_newest_first():
    """Each tool call or verdict joins the latest row of its handle written before it,
    or the first row when it precedes them all; each window's page gets its rows in
    ledger order, once, and only after nothing older can still name them."""
    from factorylab.runtime.wake import _Returns

    def call(n):
        return {"kind": "tool.call", "handle": "h", "tool": f"t{n}"}

    def verdict(about, n):
        return {"kind": "event", "event": {"kind": "Verdict", "payload": {
            "about_handle": about, "verdict": n}}}

    def row(handle):
        return {"kind": "invocation", "handle": handle, "outputs": {}}

    diary = [{"kind": "decision.open", "handle": "h"}, call(0), verdict("h", 0), row("h"),
             call(1), {"kind": "decision.open", "handle": "g"}, row("g"),
             {"kind": "price.window", "window": 0}, verdict("h", 1), row("h"), verdict("h", 2),
             verdict("g", 3), verdict("none", 4)]
    pages = []
    folded = _Returns(1, lambda window, rows: pages.append((window, rows)))
    for item in reversed(diary):
        folded.feed(item)
    section = folded.result()
    assert section["total"] == 3 and [r["handle"] for r in section["rows"]] == ["h"]
    assert [(w, [(r["handle"], [c["tool"] for c in r["tool_calls"]],
                  [v["verdict"] for v in r["verdicts"]]) for r in rows]) for w, rows in pages] \
        == [(1, [("h", [], [2])]), (0, [("h", ["t0", "t1"], [0, 1]), ("g", [], [3])])]
    assert section["pages"]["windows"] == [{"window": 0, "rows": 2}, {"window": 1, "rows": 1}]


def test_the_seller_catalogue_reads_the_diary_without_holding_it(tmp_path):
    """s09: ``deploy/serve.py`` re-reads the diary every refresh; it keeps the newest
    version of each service, the Launch event and any death, never the diary."""
    import importlib.util
    import tracemalloc

    spec = importlib.util.spec_from_file_location("serve", DEPLOY / "serve.py")
    serve = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(serve)
    manifest = load_manifest("scripted")
    peaks = []
    for count in (50, 500):
        path = tmp_path / f"w{count}" / "world.jsonl"
        path.parent.mkdir()
        ledger = Ledger(path, manifest=json.loads(manifest.canonical_json()),
                        key_path=str(path) + ".key", clock_ns=lambda: 1)
        ledger.append({"kind": "event", "event": {"kind": "Launch", "ts_ns": 1, "payload": {
            "facilitator_url": "https://facilitator.example"}}})
        for version in (1, 2):
            ledger.append({"kind": "service.registered", "id": "oracle", "program_id": "p",
                           "description": f"v{version}", "args_schema": {}, "code": "",
                           "timeout_s": 1, "handle": "h", "price_micro": 10 * version,
                           "version": version})
        for n in range(count):
            ledger.append({"kind": "tool.call", "handle": f"decision-{n}",
                           "args": {"text": f"{n:06d}" + "x" * 4096}})
        if not peaks:  # the first read's one-time costs are not history
            serve.load_catalogue(path)
        liveness = serve.Liveness(max_age_s=60)
        tracemalloc.start()
        services, _reserve_address, facilitator = serve.load_catalogue(path, liveness)
        peaks.append(tracemalloc.get_traced_memory()[1])
        tracemalloc.stop()
        assert services["oracle"].price_micro == 20 and services["oracle"].version == 2
        assert facilitator == "https://facilitator.example" and liveness()
    assert peaks[1] - peaks[0] < 500_000, peaks


def test_the_published_balance_series_is_bounded_and_keeps_every_drawdown(tmp_path):
    """The wake publishes at most ``SERIES_POINTS`` balance points however long the
    world lives (essay II.II.b, "memory"; the full series stays in the diary): the
    first, the last, and each bucket's first, last, lowest and highest, so a drawdown
    is never smoothed away. A short series is published exactly."""
    from factorylab.runtime.wake import SERIES_POINTS

    manifest = load_manifest("scripted")
    for count in (2_500, 8_000):
        path = tmp_path / f"w{count}" / "world.jsonl"
        path.parent.mkdir()
        ledger = Ledger(path, manifest=json.loads(manifest.canonical_json()),
                        key_path=str(path) + ".key", clock_ns=lambda: 1)
        balances = [1_000_000 + (n * 7919) % 1000 for n in range(count)]
        balances[count // 3] = 5  # the drawdown
        balances[2 * count // 3] = 9_000_000  # the peak
        for n, balance in enumerate(balances):
            ledger.append({"kind": "wallet.drip", "amount": 1, "balance_after": balance,
                           "ts": 1_000 + n})
        series = collect_wake(path)["wallet_series"]
        points = series["series"]
        assert series["observations"] == count
        assert len(points) <= SERIES_POINTS
        assert points[0] == {"ts": 1_000, "balance": balances[0]}
        assert points[-1] == {"ts": 1_000 + count - 1, "balance": balances[-1]}
        assert {"ts": 1_000 + count // 3, "balance": 5} in points
        assert {"ts": 1_000 + 2 * count // 3, "balance": 9_000_000} in points
        assert [p["ts"] for p in points] == sorted({p["ts"] for p in points})
    short = tmp_path / "short" / "world.jsonl"
    short.parent.mkdir()
    ledger = Ledger(short, manifest=json.loads(manifest.canonical_json()),
                    key_path=str(short) + ".key", clock_ns=lambda: 1)
    for n in range(10):
        ledger.append({"kind": "wallet.drip", "amount": 1, "balance_after": n, "ts": n})
    assert collect_wake(short)["wallet_series"] == {
        "series": [{"ts": n, "balance": n} for n in range(10)], "observations": 10}


def _published(out: Path) -> dict[str, str]:
    """What a reader of ``out`` is served: every top-level name, followed to its bytes."""
    return {path.name: path.read_text() for path in sorted(out.iterdir())
            if not path.name.startswith(".") and path.is_file()}


def _wake_diary(path: Path, count: int, *, malformed: bool = False) -> None:
    _returns_diary(path, count)
    if malformed:
        manifest = load_manifest("scripted")
        writer = Ledger.reopen(path, manifest=json.loads(manifest.canonical_json()))
        # Authenticated, but not a charter: the final projection cannot read it.
        writer.append({"kind": "wake.public", "window": 99, "charter": "not a charter"})


@pytest.mark.parametrize("failure", ["obstructed page", "malformed charter"])
def test_a_failed_publication_leaves_the_previous_generation_whole(tmp_path, failure):
    """A wake publishes a whole generation or nothing: the pages, ``wake.json`` and
    ``wake.html`` of one collection, switched in at once. A page the next generation
    cannot place, or a projection that fails after every page was written, leaves the
    previous generation served exactly as it was."""
    out = tmp_path / "www"
    first = tmp_path / "first" / "world.jsonl"
    first.parent.mkdir()
    _wake_diary(first, 40)
    write_wake(first, out, returns=10)
    before = _published(out)
    assert json.loads(before["wake.json"])["returns"]["total"] == 40
    assert {"returns-0.json", "returns-3.json"} <= set(before)
    second = tmp_path / "second" / "world.jsonl"
    second.parent.mkdir()
    _wake_diary(second, 60, malformed=failure == "malformed charter")
    if failure == "obstructed page":
        (out / "returns-2.json").unlink()
        (out / "returns-2.json").mkdir()
        before.pop("returns-2.json")
        with pytest.raises(OSError):
            write_wake(second, out, returns=10)
    else:
        assert write_wake(second, out, returns=10)["world"] == UNAVAILABLE
    assert _published(out) == before
    generations = [p for p in (out / ".wake").iterdir() if not p.is_symlink()]
    assert len(generations) == 1


def test_only_the_current_and_previous_generations_are_kept(tmp_path):
    out = tmp_path / "www"
    for count in (20, 30, 40, 50):
        path = tmp_path / f"w{count}" / "world.jsonl"
        path.parent.mkdir()
        _wake_diary(path, count)
        write_wake(path, out, returns=5)
        assert json.loads((out / "wake.json").read_text())["returns"]["total"] == count
        assert json.loads((out / f"returns-{count // 10 - 1}.json").read_text())["rows"]
    assert len([p for p in (out / ".wake").iterdir() if not p.is_symlink()]) == 2
