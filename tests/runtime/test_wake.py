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
    public_window_item,
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
    # The page, its document, and one returns page per window that saw a return.
    names = {p.name for p in out.iterdir()}
    assert {"wake.html", "wake.json"} <= names
    assert names - {"wake.html", "wake.json"} == {
        f"returns-{row['window']}.json" for row in data["returns"]["pages"]["windows"]}
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


SAMPLE_KEYS = {
    # Every key the published site's wake.sample.json carried before edition 2.
    "wallet_series", "spend_by_capability", "invocations_by_assembly", "action_frequencies",
    "settlement_latency", "roster", "tools", "connectors", "notes", "observations", "charter",
    "compute", "pots", "immune", "portfolio", "world", "manifest_hash", "uptime_ns",
    "last_event_time_ns",
}


def test_edition2_views_are_additive_and_read_only_public_items(tmp_path, scripted_run):
    # Long enough for price windows to close, so the standing pots and cells exist.
    world = scripted_run("scripted", 150, 1).copy_to(tmp_path / "world")
    manifest = load_manifest("scripted")
    data = collect_wake(world)
    assert SAMPLE_KEYS < set(data)
    assert set(data) - SAMPLE_KEYS == {"entitlements", "money", "deliveries", "commitments",
                                        "cells", "liveness", "returns", "prompt_sections"}
    # Existing keys keep their shape: the five pots the site reads are all present,
    # beside the endowment keys W1 added (C1, C2).
    assert {"venue", "reserve", "venice", "seed", "complete"} <= set(data["pots"]["current"])
    assert {"locked_micro", "unlocked_micro", "next_release_ns", "dormant"} <= set(
        data["pots"]["current"])
    assert set(data["pots"]["income"]) == {"earned_micro", "subsidy_micro",
                                           "converted_from_principal_micro"}
    assert data["pots"]["income"]["earned_micro"] == 0
    assert data["pots"]["income"]["subsidy_micro"] == 0  # a scripted world has no credit
    money = data["money"]
    assert money["in_by_class"]["initial"] == manifest.initial_balance_micro
    assert money["in_by_class"]["earned"] == 0 and money["in_by_class"]["subsidy"] == 0
    assert sum(money["out_by_class"].values()) > 0 and money["out_by_class"]["model"] > 0
    assert all(type(v) is int for v in (*money["in_by_class"].values(),
                                         *money["out_by_class"].values()))
    deliveries = data["deliveries"]["per_window"]
    assert deliveries and all(set(row) == {"window", "by_kind"} for row in deliveries)
    assert all(set(kind) == {"kind", "counts"} for row in deliveries for kind in row["by_kind"])
    assert sum(sum(k["counts"].values()) for row in deliveries for k in row["by_kind"]) > 0
    assert any(k["kind"] == "verdict" and k["counts"].get("settled", 0) > 0
               for row in deliveries for k in row["by_kind"])
    commitments = data["commitments"]
    assert set(commitments) == {"as_of_ns", "handles", "forecasts"}
    assert commitments["as_of_ns"] == data["last_event_time_ns"]
    for open_kind, count in (("handles", "unsettled"), ("forecasts", "pending")):
        rows = commitments[open_kind]["rows"]
        assert all(type(r["age_ns"]) is int and r["age_ns"] >= 0 for r in rows)
        assert len(rows) == min(commitments[open_kind][count], 200)
    cells = data["cells"]
    assert set(cells) == {"dimensions", "cuts", "windows", "transitions"}
    assert cells["dimensions"][-2:] == ["registrations", "revision"]
    assert all(set(row) == {"window", "cell", "changed", "charter_edition"}
               for row in cells["windows"])
    assert cells["transitions"] == sum(row["changed"] for row in cells["windows"])
    liveness = data["liveness"]
    assert liveness["status"] == "alive" and liveness["launched_ns"] == 0
    assert liveness["dormant_periods"] == [] and liveness["terminated_ns"] is None
    # No identity, handle, source or address reaches the page through the new views.
    views = {k: data[k] for k in ("money", "deliveries", "commitments", "cells", "liveness")}
    text = json.dumps(views)
    assert "seed-decider" not in text and "eval-a" not in text and "0x" not in text
    assert '"handle"' not in text and "decision-" not in text
    page = render_wake(data)
    for field in views:
        assert f"<h2>{field}</h2>" in page

    # The public items edition 2 writes are read as they land: dormancy, income, subsidy.
    writer = Ledger.reopen(world, manifest=json.loads(manifest.canonical_json()))
    writer.append({"kind": "dormant", "state": "entered", "ts": 10**12})
    data = collect_wake(world)
    assert data["liveness"]["status"] == "dormant"
    assert data["liveness"]["dormant_since_ns"] == 10**12
    writer.append({"kind": "dormant", "exited": 2 * 10**12})
    writer.append({"kind": "income.earned", "service": "doubler", "micro": 3000, "tx": "0x1",
                   "payer": "0x" + "12" * 20, "ts": 2 * 10**12})
    writer.append({"kind": "treasury.subsidy", "micro": 5_000_000, "ts": 2 * 10**12})
    # The wallet's own tranche item (C1), not a cancelled hold, is the release class.
    writer.append({"kind": "release", "tranche": 0, "amount": 7_000_000, "due_ns": 2 * 10**12,
                   "locked_after": 0, "balance_after": 90_000_000, "ts": 2 * 10**12})
    writer.append({"kind": "wallet.release", "reason": "hold", "amount": 1_000_000,
                   "ts": 2 * 10**12})
    writer.append({"kind": "treasury.confirmed", "ts": 2 * 10**12, "state": {
        "direction": "to_venice", "amount_micro": 5_000_000, "received_micro": 5_000_000,
        "fees_micro": 0}})
    data = collect_wake(world)
    assert data["liveness"]["status"] == "alive"
    assert data["liveness"]["dormant_periods"] == [{"entered_ns": 10**12,
                                                    "exited_ns": 2 * 10**12}]
    assert data["money"]["in_by_class"]["earned"] == 3000
    assert data["money"]["in_by_class"]["subsidy"] == 5_000_000
    assert data["money"]["in_by_class"]["release"] == 7_000_000
    assert data["money"]["in_by_class"]["converted_from_principal"] == 5_000_000
    assert "0x" not in json.dumps(data["money"])
    writer.append({"kind": "event", "event": {"kind": "Terminated", "ts_ns": 3 * 10**12}})
    data = collect_wake(world)
    assert data["liveness"]["status"] == "terminated"
    assert data["liveness"]["terminated_ns"] == 3 * 10**12


def _event(kind, ts_ns, payload):
    return {"kind": "event", "event": {"kind": kind, "ts_ns": ts_ns, "payload": payload}}


def _invocation(handle, seat, role, outputs, *, ts_ns, cost=7, model="fake-haiku"):
    return {"kind": "invocation", "assembly_id": seat, "role": role, "handle": handle,
            "cost": cost, "status": "ok", "stop_reason": "end_turn", "served_by": model,
            "outputs": json.dumps(outputs), "ts": ts_ns}


def test_returns_publish_every_answer_live_with_its_verdicts(world, tmp_path):
    """One row per invocation as soon as it is in the ledger, linked to what judged it."""
    manifest = load_manifest("scripted")
    before = collect_wake(world)
    returns = before["returns"]
    assert set(returns) == {"limit", "total", "rows", "pages"}
    assert returns["limit"] == 500 and returns["total"] == len(returns["rows"]) > 0
    for row in returns["rows"]:
        assert set(row) == {"window", "ts_ns", "handle", "seat", "role", "model", "status",
                            "stop_reason", "cost_micro", "outputs", "tool_calls", "verdicts",
                            "meta_verdicts"}
        assert row["seat"] in {a.id for a in manifest.assemblies}
        assert isinstance(row["outputs"], dict)  # as written, parsed back from the diary
    # The scripted judges write a rationale; it is on the page, unredacted.
    assert any(row["outputs"].get("rationale") == "scripted judgement" for row in returns["rows"])

    writer = Ledger.reopen(world, manifest=json.loads(manifest.canonical_json()))
    late = 10**13
    writer.append({"kind": "tool.call", "handle": "decision-live", "assembly_id": "seed-decider",
                   "tool": "market.mid", "args": '{"coin": "BTC"}', "ok": True, "outcome": "ok",
                   "cost": 3, "ts": late})
    writer.append(_invocation("decision-live", "seed-decider", "producer", {
        "action": "hold", "rationale": "LIVE_RATIONALE_TEXT <b>unescaped?</b>",
        "register": [{"kind": "tool", "id": "spread-check"}],
        "forecasts": [{"predicate": "wallet_up", "q": 0.4}],
    }, ts_ns=late))
    after = collect_wake(world)["returns"]
    assert after["total"] == returns["total"] + 1
    row = after["rows"][-1]
    assert row["handle"] == "decision-live" and row["seat"] == "seed-decider"
    assert row["role"] == "producer" and row["model"] == "fake-haiku"
    assert row["status"] == "ok" and row["cost_micro"] == 7 and row["ts_ns"] == late
    assert row["outputs"]["rationale"].startswith("LIVE_RATIONALE_TEXT")
    assert row["outputs"]["register"] == [{"kind": "tool", "id": "spread-check"}]
    assert row["tool_calls"] == [{"ts_ns": late, "tool": "market.mid", "args": '{"coin": "BTC"}',
                                  "outcome": "ok", "ok": True, "cost_micro": 3}]
    assert row["verdicts"] == [] and row["meta_verdicts"] == []
    # A judge's verdict and a meta verdict about that return attach by handle once they land.
    writer.append(_event("Verdict", late + 1, {
        "about_handle": "decision-live", "evaluator_handle": "decision-judge",
        "verdict": 0.8, "payoff": 0.6, "rationale": "JUDGE_RATIONALE_TEXT",
        "producer_outputs": {"action": "hold"}, "propensity": {"chosen": "hold"}}))
    writer.append(_event("MetaVerdict", late + 2, {
        "about": "decision-live", "tier": 2, "score": 0.9, "by": "decision-meta",
        "evaluator_handle": "decision-judge", "rationale": "META_RATIONALE_TEXT",
        "propensity": {"chosen": "conform"}}))
    row = collect_wake(world)["returns"]["rows"][-1]
    assert row["verdicts"] == [{"ts_ns": late + 1, "by": "decision-judge", "verdict": 0.8,
                                "payoff": 0.6, "rationale": "JUDGE_RATIONALE_TEXT"}]
    assert row["meta_verdicts"] == [{"ts_ns": late + 2, "by": "decision-meta",
                                     "judge": "decision-judge", "tier": 2, "score": 0.9,
                                     "rationale": "META_RATIONALE_TEXT"}]
    # A verdict that lands before its return's row (an out-of-order diary) still attaches.
    writer.append(_event("Verdict", late + 3, {"about_handle": "decision-early",
                                                "evaluator_handle": "decision-judge",
                                                "verdict": 0.1, "payoff": 0.2,
                                                "rationale": "early"}))
    writer.append(_invocation("decision-early", "seed-decider", "producer", {"action": "noop"},
                              ts_ns=late + 4))
    row = collect_wake(world)["returns"]["rows"][-1]
    assert row["handle"] == "decision-early" and row["verdicts"][0]["rationale"] == "early"
    # A return the diary's cap cut is published as the text that survived, never invented.
    writer.append({**_invocation("decision-cut", "seed-decider", "producer", {}, ts_ns=late + 5),
                   "outputs": '{"action": "hold", "rationale": "cut off he'})
    row = collect_wake(world)["returns"]["rows"][-1]
    assert row["outputs"] == {"truncated_text": '{"action": "hold", "rationale": "cut off he'}
    # The page renders the rationale as readable, escaped prose, not only as JSON.
    page = render_wake(collect_wake(world))
    assert "<h2>returns</h2>" in page
    assert "<blockquote>LIVE_RATIONALE_TEXT &lt;b&gt;unescaped?&lt;/b&gt;</blockquote>" in page
    assert "<blockquote>JUDGE_RATIONALE_TEXT</blockquote>" in page
    assert "<blockquote>META_RATIONALE_TEXT</blockquote>" in page
    assert "<b>unescaped?</b>" not in page
    assert page.count("<article>") == collect_wake(world)["returns"]["total"]


def test_returns_are_bounded_on_the_page_and_paginated_by_window(world, tmp_path):
    manifest = load_manifest("scripted")
    writer = Ledger.reopen(world, manifest=json.loads(manifest.canonical_json()))
    for n in range(4):
        writer.append(_invocation(f"decision-w{n}", "seed-decider", "producer",
                                  {"action": "hold", "n": n}, ts_ns=10**13 + n))
    writer.append({"kind": "price.window", "window": 7, "ts": 10**13 + 10})
    writer.append(_invocation("decision-next", "eval-a", "evaluator", {"verdict": 0.5},
                              ts_ns=10**13 + 11))
    total = collect_wake(world)["returns"]["total"]
    out = tmp_path / "public"
    assert main(["wake", "--ledger", str(world), "--out", str(out), "--returns", "3"]) == 0
    data = json.loads((out / "wake.json").read_text())
    returns = data["returns"]
    assert returns["limit"] == 3 and returns["total"] == total and len(returns["rows"]) == 3
    assert [row["handle"] for row in returns["rows"]] == ["decision-w2", "decision-w3",
                                                          "decision-next"]
    assert returns["rows"][-1]["window"] == 8 and returns["rows"][0]["window"] == 0
    pages = returns["pages"]
    assert pages["file"] == "returns-{window}.json"
    assert pages["first_window"] == 0 and pages["last_window"] == 8
    assert {p["window"] for p in pages["windows"]} == {0, 8}
    assert sum(p["rows"] for p in pages["windows"]) == total
    # Every return is in its window's page, in ledger order, whatever the page limit.
    for page in pages["windows"]:
        document = json.loads((out / f"returns-{page['window']}.json").read_text())
        assert document["world"] == "scripted" and document["window"] == page["window"]
        assert len(document["rows"]) == page["rows"]
        assert all(row["window"] == page["window"] for row in document["rows"])
    first = json.loads((out / "returns-0.json").read_text())["rows"]
    assert [row["handle"] for row in first[-4:]] == [f"decision-w{n}" for n in range(4)]
    assert json.loads((out / "returns-8.json").read_text())["rows"][0]["handle"] == "decision-next"
    # The page shows exactly the bounded rows, and every page file is world-readable.
    html_page = (out / "wake.html").read_text()
    assert html_page.count("<article>") == 3 and "3 of " in html_page
    assert all((p.stat().st_mode & 0o777) == 0o644 for p in out.iterdir())
    # An unchanged page is left alone on republish; a changed one is replaced atomically.
    stamp = (out / "returns-0.json").stat().st_mtime_ns
    assert main(["wake", "--ledger", str(world), "--out", str(out), "--returns", "3"]) == 0
    assert (out / "returns-0.json").stat().st_mtime_ns == stamp
    assert main(["wake", "--ledger", str(world), "--out", str(out), "--returns", "0"]) == 0
    assert json.loads((out / "wake.json").read_text())["returns"]["rows"] == []


def test_returns_are_additive_and_leave_every_existing_key_unchanged(world):
    with_returns = collect_wake(world)
    without = collect_wake(world, returns=0)
    assert set(with_returns) == set(without) == {*VIEWS, *SECTIONS, "world", "manifest_hash",
                                                 "uptime_ns", "last_event_time_ns"}
    assert SAMPLE_KEYS < set(with_returns) and "returns" in SECTIONS
    for key in set(with_returns) - {"returns"}:
        assert with_returns[key] == without[key], key
    # The other sections still fold to role totals: no seat id or handle outside returns.
    text = json.dumps({k: v for k, v in with_returns.items() if k != "returns"})
    assert "seed-decider" not in text and "eval-a" not in text and "decision-" not in text
    # A failed chain leaves returns unavailable with everything else, never half a list.
    with world.open("ab") as stream:
        stream.write(b'{"item":')
    assert collect_wake(world, sleep=lambda _: None)["returns"] == UNAVAILABLE


def test_public_window_item_carries_the_income_classes_beside_the_pots():
    from tests.conftest import make_runtime

    rt = make_runtime()
    item = public_window_item(rt, window=0, event=0)
    assert {"venue", "reserve", "venice", "seed", "complete", "locked_micro", "unlocked_micro",
            "next_release_ns", "dormant"} == set(item["pots"])
    assert item["income"] == {"earned_micro": 0, "subsidy_micro": 0,
                              "converted_from_principal_micro": 0}
    rt.treasury.earn("doubler", 2500, "0x1")
    assert public_window_item(rt, window=0, event=0)["income"]["earned_micro"] == 2500


def _liveness_helper():
    import importlib.util

    spec = importlib.util.spec_from_file_location("witness_liveness",
                                                  DEPLOY / "witness_liveness.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _fake_witness(tmp_path):
    """A witness.sh stand-in that records its arguments; WITNESS_FAIL makes it refuse."""
    script, log = tmp_path / "witness.sh", tmp_path / "witness.log"
    script.write_text('#!/bin/bash\nset -eu\n[[ -z ${WITNESS_FAIL:-} ]] || exit 1\n'
                      f'printf \'%s\\n\' "$*" >> "{log}"\n')
    return script, log


def _publish(wake: Path, status) -> None:
    wake.parent.mkdir(parents=True, exist_ok=True)
    wake.write_text(json.dumps({"liveness": {"status": status}} if status is not None
                               else {"wallet_series": "unavailable"}))


def test_witness_liveness_emits_one_line_into_dormancy_and_one_back(tmp_path, monkeypatch):
    helper = _liveness_helper()
    script, log = _fake_witness(tmp_path)
    wake, state = tmp_path / "www" / "wake.json", tmp_path / "runs" / "funded.liveness"
    run = lambda: helper.main(["--wake", str(wake), "--state", str(state),  # noqa: E731
                               "--witness", str(script)])
    monkeypatch.delenv("WITNESS_FAIL", raising=False)
    # Alive from the start: nothing to witness, and no record is needed yet.
    _publish(wake, "alive")
    assert run() == 0 and not log.exists() and not state.exists()
    # Into dormancy: exactly one line, however many times the same wake is republished.
    _publish(wake, "dormant")
    assert run() == 0 and run() == 0 and run() == 0
    assert log.read_text().splitlines() == ["dormant entered"]
    assert state.read_text() == "dormant\n" and (state.stat().st_mode & 0o777) == 0o600
    # Back to alive: one more line, idempotent again.
    _publish(wake, "alive")
    assert run() == 0 and run() == 0
    assert log.read_text().splitlines() == ["dormant entered", "dormant exited"]
    assert state.read_text() == "alive\n"
    # A second pause is its own pair of lines.
    _publish(wake, "dormant")
    assert run() == 0
    _publish(wake, "alive")
    assert run() == 0
    assert log.read_text().splitlines() == ["dormant entered", "dormant exited"] * 2


def test_witness_liveness_ignores_unreadable_wakes_and_leaves_kills_to_the_supervisor(
        tmp_path, monkeypatch):
    helper = _liveness_helper()
    script, log = _fake_witness(tmp_path)
    wake, state = tmp_path / "www" / "wake.json", tmp_path / "runs" / "funded.liveness"
    run = lambda: helper.main(["--wake", str(wake), "--state", str(state),  # noqa: E731
                               "--witness", str(script)])
    monkeypatch.delenv("WITNESS_FAIL", raising=False)
    # No wake, an unverified wake, a wake without liveness, an unknown status: nothing.
    assert run() == 0
    _publish(wake, None)
    assert run() == 0
    wake.write_text("not json")
    assert run() == 0
    _publish(wake, "sleeping")
    assert run() == 0
    assert not log.exists() and not state.exists()
    # A world found dormant on the helper's first run is witnessed as entering.
    _publish(wake, "dormant")
    assert run() == 0 and log.read_text().splitlines() == ["dormant entered"]
    # A failed append keeps the old record, so the next publish tries again.
    _publish(wake, "alive")
    monkeypatch.setenv("WITNESS_FAIL", "1")
    assert run() == 1 and state.read_text() == "dormant\n"
    monkeypatch.delenv("WITNESS_FAIL")
    assert run() == 0 and log.read_text().splitlines() == ["dormant entered", "dormant exited"]
    # Termination is start.sh's kill line, never an exited line from the wake.
    _publish(wake, "dormant")
    assert run() == 0
    _publish(wake, "terminated")
    assert run() == 0 and run() == 0
    assert log.read_text().splitlines() == ["dormant entered", "dormant exited",
                                            "dormant entered"]
    assert state.read_text() == "terminated\n"


def test_witness_liveness_drives_the_real_witness_script(tmp_path, monkeypatch):
    import sys

    helper = _liveness_helper()
    (tmp_path / "runs").mkdir()
    ledger = tmp_path / "runs" / "rehearsal.jsonl"
    ledger.write_bytes(b'{"format":1,"genesis_hash":"fixture"}\n')
    monkeypatch.setenv("FACTORYLAB_ROOT", str(tmp_path))
    monkeypatch.setenv("FACTORYLAB_REPO", str(DEPLOY.parent))
    monkeypatch.setenv("FACTORYLAB_WORLD", "rehearsal")
    monkeypatch.setenv("FACTORYLAB_PYTHON", sys.executable)
    monkeypatch.delenv("FACTORYLAB_WITNESS_URL", raising=False)
    wake, state = tmp_path / "www" / "wake.json", tmp_path / "runs" / "rehearsal.liveness"
    _publish(wake, "dormant")
    assert helper.main(["--wake", str(wake), "--state", str(state)]) == 0
    _publish(wake, "alive")
    assert helper.main(["--wake", str(wake), "--state", str(state)]) == 0
    lines = [json.loads(line)
             for line in (tmp_path / ".witness" / "rehearsal.jsonl").read_text().splitlines()]
    assert [(line["event"], line["reason"]) for line in lines] == [("dormant", "entered"),
                                                                    ("dormant", "exited")]
    assert all(line["world"] == "rehearsal"
               and line["ledger_head"] == hashlib.sha256(ledger.read_bytes()).hexdigest()
               for line in lines)


def test_wake_unit_witnesses_liveness_after_each_publish():
    unit = (DEPLOY / "factorylab-wake.service").read_text()
    assert "ExecStartPost=-" in unit and "deploy/witness_liveness.py" in unit
    assert "--wake /srv/factorylab/www/wake.json" in unit
    assert "--state /srv/factorylab/runs/funded.liveness" in unit
    assert ("ReadWritePaths=/srv/factorylab/www /srv/factorylab/runs /srv/factorylab/.witness"
            in unit)
    assert "EnvironmentFile=-/srv/factorylab/ops.env" in unit
