"""The unattended host: backups that run, liveness that fails closed, alerts that arrive.

Chapter II §III (an online evaluation runs continuously in production) and §IV.c
(neither the factory nor its control apparatus may be slower than its environment):
every check here attempts to certify a dead, stalled or unobserved world as alive, or
to lose a failure on its way to the owner, and asserts that it cannot. The scripts run
as subprocesses against fixtures; curl, sleep, df, age and rclone are stand-ins on PATH,
so nothing here reaches a network. systemd itself is not available on the test host,
so the unit files are checked as text.
"""

import importlib.util
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

import pytest

DEPLOY = Path(__file__).resolve().parents[2] / "deploy"
S = 1_000_000_000
URL = "https://example.invalid/webhook-secret"


def _load(name: str):
    spec = importlib.util.spec_from_file_location(f"deploy_{name}", DEPLOY / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # dataclasses resolve the module by name
    spec.loader.exec_module(module)
    return module


liveness = _load("witness_liveness")


def _executable(path: Path, text: str) -> Path:
    path.write_text(text)
    path.chmod(0o700)
    return path


@pytest.fixture
def fakes(tmp_path, monkeypatch):
    """curl that records each attempt and fails the first FAIL_FIRST; sleep that records."""
    bin_path = tmp_path / "bin"
    bin_path.mkdir()
    _executable(bin_path / "curl", (
        "#!/usr/bin/env python3\nimport json, os, sys\nfrom pathlib import Path\n"
        "assert 'webhook-secret' not in ' '.join(sys.argv)\n"
        "assert 'webhook-secret' in sys.stdin.read()\n"
        "assert sys.argv[sys.argv.index('--proto') + 1] == '=https'\n"
        "body = (sys.argv[sys.argv.index('--data-binary') + 1] if '--data-binary' in sys.argv\n"
        "        else '{\"get\": true}\\n')\n"
        "log = Path(os.environ['CURL_LOG'])\n"
        "attempts = len(log.read_text().splitlines()) if log.exists() else 0\n"
        "with log.open('a') as out: out.write(json.dumps(body) + '\\n')\n"
        "sys.exit(22 if attempts < int(os.environ.get('FAIL_FIRST', '0')) else 0)\n"))
    _executable(bin_path / "sleep", (
        "#!/usr/bin/env python3\nimport os, sys\n"
        "open(os.environ['SLEEP_LOG'], 'a').write(sys.argv[1] + '\\n')\n"))
    monkeypatch.setenv("PATH", str(bin_path) + os.pathsep + os.environ["PATH"])
    monkeypatch.setenv("CURL_LOG", str(tmp_path / "curl.log"))
    monkeypatch.setenv("SLEEP_LOG", str(tmp_path / "sleep.log"))
    monkeypatch.setenv("FACTORY_WEBHOOK_URL", URL)
    for name in ("EXIT_CODE", "EXIT_STATUS", "SERVICE_RESULT", "FAIL_FIRST",
                 "FACTORY_HEARTBEAT_URL"):
        monkeypatch.delenv(name, raising=False)

    class Fakes:
        root = tmp_path / "srv"
        runtime = tmp_path / "run"
        alert = tmp_path / "alert.sh"

        @staticmethod
        def bodies() -> list[dict]:
            log = tmp_path / "curl.log"
            if not log.exists():
                return []
            lines = [json.loads(line) for line in log.read_text().splitlines()]
            assert all(body.endswith("\n") for body in lines)
            return [json.loads(body) for body in lines]

        @staticmethod
        def sleeps() -> list[str]:
            log = tmp_path / "sleep.log"
            return log.read_text().split() if log.exists() else []

    (Fakes.root / "runs").mkdir(parents=True)
    Fakes.runtime.mkdir()
    Fakes.alert.write_text((DEPLOY / "alert.sh").read_text()
                           .replace("/run/factorylab", str(Fakes.runtime))
                           .replace("/srv/factorylab", str(Fakes.root)))
    return Fakes


def _alert(fakes, *args: str, **env: str) -> subprocess.CompletedProcess:
    return subprocess.run(["bash", str(fakes.alert), *args], capture_output=True, text=True,
                          env={**os.environ, **env}, timeout=60)


def _wake(now: int, *, status="alive", last=None, rows=None, open_rows=(), venue=None,
          launched=None) -> dict:
    """A wake.json as runtime/wake.py publishes it, reduced to what the verdict reads."""
    last = now - 10 * S if last is None else last
    doc = {
        "liveness": {"status": status,
                     "launched_ns": now - 30 * 86400 * S if launched is None else launched},
        "last_event_time_ns": last,
        "commitments": {"as_of_ns": now, "handles": {"unsettled": len(open_rows),
                                                      "rows": list(open_rows)}},
        "returns": {"rows": [{"ts_ns": last - 60 * S, "status": "ok"}] if rows is None
                    else rows},
    }
    if venue is not None:
        doc["venue"] = venue
    return doc


NOW = 1_790_000_000 * S


def _assess(doc, *, published=NOW, disk=10.0):
    return liveness.assess(doc, wake_mtime_ns=published, now_ns=NOW, disk=disk)


# --- liveness fails closed --------------------------------------------------------------


def test_missing_stale_and_error_only_evidence_cannot_certify_liveness(tmp_path, fakes,
                                                                       monkeypatch):
    """Every way of having no evidence of work is unhealthy or unknown, exits non-zero
    and reaches the owner through the real alert.sh; an initial run that crashes does
    too. Nothing here is ever reported alive."""
    monkeypatch.setattr(liveness, "disk_percent", lambda path: 10.0)
    now = time.time_ns()
    cases = {
        "missing": (None, None, 11, "unknown", "wake_missing"),
        "unreadable": ("{not json", None, 11, "unknown", "wake_missing"),
        "unavailable": ({"liveness": {"status": "unavailable"}}, None, 11, "unknown",
                        "wake_unavailable"),
        # The audit's exact document: alive, with an event from the epoch's first second.
        "ancient": ({"liveness": {"status": "alive"}, "last_event_time_ns": 1}, None, 10,
                    "unhealthy", "ledger_stale"),
        "stale_publication": (_wake(now - 3 * 3600 * S), now - 3 * 3600 * S, 11, "unknown",
                              "wake_stale"),
        "failed_only": (_wake(now, rows=[{"ts_ns": now - k * S, "status": "failed"}
                                         for k in range(20, 40)]), None, 10, "unhealthy",
                        "provider_failing"),
        "refused_only": (_wake(now, rows=[{"ts_ns": now - 30 * S, "status": "refused"}]),
                         None, 10, "unhealthy", "provider_failing"),
        "no_work": (_wake(now, rows=[]), None, 10, "unhealthy", "no_provider_success"),
        "old_success_only": (_wake(now, rows=[{"ts_ns": now - 3 * 3600 * S, "status": "ok"}]),
                             None, 10, "unhealthy", "no_provider_success"),
    }
    for name, (doc, mtime, code, status, reason) in cases.items():
        case = tmp_path / name
        case.mkdir()
        wake, health = case / "wake.json", case / "funded.health"
        if doc is not None:
            wake.write_text(doc if isinstance(doc, str) else json.dumps(doc))
            if mtime is not None:
                os.utime(wake, ns=(mtime, mtime))
        before = len(fakes.bodies())
        got = liveness.main(["--wake", str(wake), "--state", str(case / "funded.liveness"),
                             "--health", str(health), "--alert", str(fakes.alert)])
        assert got == code != 0, name
        assert health.read_text().split()[:2] == [status, reason], name
        assert fakes.bodies()[before:] == [
            {"world": "funded", "event": status, "reason": reason}], name
    # The initial run, not only a resume, crashing (here: the unit's memory cap).
    (fakes.runtime / "mode").write_text("run\n")
    proc = _alert(fakes, EXIT_CODE="killed", EXIT_STATUS="KILL", SERVICE_RESULT="oom-kill")
    assert proc.returncode == 0 and not proc.stdout and not proc.stderr
    assert fakes.bodies()[-1] == {"world": "funded", "event": "failed_run",
                                  "reason": "oom_kill"}


def test_fresh_progress_and_successful_work_are_healthy_and_silent(tmp_path, fakes,
                                                                   monkeypatch):
    monkeypatch.setattr(liveness, "disk_percent", lambda path: 10.0)
    wake = tmp_path / "wake.json"
    wake.write_text(json.dumps(_wake(time.time_ns())))
    assert liveness.main(["--wake", str(wake), "--state", str(tmp_path / "s"),
                          "--health", str(tmp_path / "h"), "--alert", str(fakes.alert)]) == 0
    assert (tmp_path / "h").read_text() == "healthy\n"
    assert (tmp_path / "h").stat().st_mode & 0o777 == 0o600
    assert fakes.bodies() == []


def test_recorded_dormancy_waives_only_the_work_check():
    """A dormant world routes no paid cognition (C2) but still ticks and settles."""
    assert _assess(_wake(NOW, status="dormant", rows=[])) == liveness.Verdict("dormant")
    stale = _assess(_wake(NOW, status="dormant", rows=[], last=NOW - 3600 * S))
    assert stale == liveness.Verdict("unhealthy", ("ledger_stale",))
    overdue = {"channel": "verdict", "age_ns": 7 * 3600 * S, "deadline_ns": NOW - 6 * 3600 * S}
    assert _assess(_wake(NOW, status="dormant", rows=[], open_rows=[overdue])).reasons == (
        "settlement_overdue",)


def test_a_new_world_is_not_failed_for_having_called_nothing_yet():
    assert _assess(_wake(NOW, rows=[], launched=NOW - 600 * S)).status == "healthy"
    # ...but failures are failures from the first call.
    failed = [{"ts_ns": NOW - 60 * S, "status": "failed"}]
    assert _assess(_wake(NOW, rows=failed, launched=NOW - 600 * S)).reasons == (
        "provider_failing",)


def test_overdue_settlements_and_unreadable_venue_are_unhealthy():
    # Opened 2 h ago with a 1 h deadline: within its own span past the deadline, so a
    # tick-counted cutoff running slow is not yet overdue...
    slow = {"channel": "verdict", "age_ns": 2 * 3600 * S, "deadline_ns": NOW - 3600 * S}
    assert _assess(_wake(NOW, open_rows=[slow])).status == "healthy"
    # ...opened 5 h ago with a 1 h span, 4 h past its deadline: overdue.
    late = {"channel": "verdict", "age_ns": 5 * 3600 * S, "deadline_ns": NOW - 4 * 3600 * S}
    assert _assess(_wake(NOW, open_rows=[late])).reasons == ("settlement_overdue",)
    assert _assess(_wake(NOW, venue={"equity_micro": "unavailable"})).reasons == (
        "venue_unreadable",)
    assert _assess(_wake(NOW, venue={"equity_micro": 5_000_000})).status == "healthy"
    # Commitments the wake could not compute are not "nothing open".
    doc = _wake(NOW)
    doc["commitments"] = "unavailable"
    assert _assess(doc) == liveness.Verdict("unknown", ("commitments_unknown",))


def test_disk_at_threshold_is_unhealthy_and_unmeasured_disk_is_unknown():
    assert _assess(_wake(NOW), disk=80.0).reasons == ("disk_high",)
    assert _assess(_wake(NOW), disk=79.9).status == "healthy"
    assert _assess(_wake(NOW), disk=None) == liveness.Verdict("unknown", ("disk_unknown",))
    # A full disk with no wake at all is still reported as the disk.
    assert _assess(None, disk=95.0) == liveness.Verdict(
        "unhealthy", ("disk_high", "wake_missing"))


def test_a_clock_that_runs_backwards_is_unknown_not_fresh():
    """A last event ahead of the publication is a clock jump, never proof of progress."""
    assert _assess(_wake(NOW, last=NOW + 3600 * S)).reasons[0] == "clock_skew"
    assert _assess(_wake(NOW), published=NOW + 3600 * S).status == "unknown"
    doc = _wake(NOW)
    del doc["last_event_time_ns"]
    assert _assess(doc).status == "unknown"


def test_the_ledger_is_aged_at_the_check_not_at_the_publication():
    """Sol 6.1 r5: an event 19 minutes old when the wake published read as fresh half
    an hour later, when it was 49 minutes old. The ledger's age is read now, so the
    check runs right after each publication (the wake unit's OnSuccess), where a fresh
    event is still healthy."""
    published = NOW - 30 * 60 * S
    doc = _wake(published, last=published - 19 * 60 * S)
    assert _assess(doc, published=published).reasons == ("ledger_stale",)
    just = NOW - 60 * S
    assert _assess(_wake(just), published=just).status == "healthy"
    # A stale publication still measures nothing now: unknown, as before.
    old = NOW - 3 * 3600 * S
    assert _assess(_wake(old), published=old) == liveness.Verdict("unknown", ("wake_stale",))


def test_terminated_is_final_and_not_alive(tmp_path, fakes, monkeypatch):
    monkeypatch.setattr(liveness, "disk_percent", lambda path: 10.0)
    assert _assess(_wake(NOW, status="terminated", last=1)) == liveness.Verdict("terminated")
    wake = tmp_path / "wake.json"
    wake.write_text(json.dumps(_wake(time.time_ns(), status="terminated")))
    code = liveness.main(["--wake", str(wake), "--state", str(tmp_path / "s"),
                          "--health", str(tmp_path / "h"), "--alert", str(fakes.alert)])
    assert code == 12 and (tmp_path / "h").read_text() == "terminated\n"
    assert fakes.bodies() == []  # the stop itself was alerted, by factorylab.service


def test_dormancy_transitions_are_still_witnessed(tmp_path, fakes, monkeypatch):
    monkeypatch.setattr(liveness, "disk_percent", lambda path: 10.0)
    calls = tmp_path / "witnessed"
    script = _executable(tmp_path / "witness.sh",
                         f'#!/bin/bash\necho "$@" >> {calls}\nexit "${{WITNESS_EXIT:-0}}"\n')
    wake, state = tmp_path / "wake.json", tmp_path / "funded.liveness"
    argv = ["--wake", str(wake), "--state", str(state), "--witness", str(script),
            "--alert", str(fakes.alert)]
    wake.write_text(json.dumps(_wake(time.time_ns(), status="dormant", rows=[])))
    monkeypatch.setenv("WITNESS_EXIT", "1")
    assert liveness.main(argv) == 1 and not state.exists()  # retried on the next run
    monkeypatch.setenv("WITNESS_EXIT", "0")
    assert liveness.main(argv) == 0 and state.read_text() == "dormant\n"
    assert liveness.main(argv) == 0  # unchanged: nothing witnessed again
    wake.write_text(json.dumps(_wake(time.time_ns())))
    assert liveness.main(argv) == 0 and state.read_text() == "alive\n"
    assert calls.read_text().splitlines() == ["dormant entered", "dormant entered",
                                              "dormant exited"]


# --- alert delivery ---------------------------------------------------------------------


def test_alert_retries_with_backoff_until_delivered(fakes):
    proc = _alert(fakes, "--event", "backup_failed", FAIL_FIRST="2")
    assert proc.returncode == 0 and not proc.stdout and not proc.stderr
    assert fakes.bodies() == [{"world": "funded", "event": "backup_failed",
                               "reason": "none"}] * 3
    assert fakes.sleeps() == ["2", "4"]


def test_alert_gives_up_inside_the_world_units_stop_timeout(fakes):
    proc = _alert(fakes, "--event", "wake_failed", FAIL_FIRST="99")
    assert proc.returncode == 1 and len(fakes.bodies()) == 5
    assert fakes.sleeps() == ["2", "4", "8", "16"]
    # ExecStopPost runs under TimeoutStopSec: every attempt's cap plus every pause fits.
    script = (DEPLOY / "alert.sh").read_text()
    per_attempt = int(re.search(r"--max-time (\d+)", script).group(1))
    unit = (DEPLOY / "factorylab.service").read_text()
    stop = int(re.search(r"^TimeoutStopSec=(\d+)s$", unit, re.M).group(1))
    assert 5 * per_attempt + sum(map(int, fakes.sleeps())) < stop


def test_alert_test_mode_proves_the_path_with_a_synthetic_event(fakes, monkeypatch):
    proc = _alert(fakes, "--test")
    assert proc.returncode == 0 and proc.stdout == "alert test: delivered\n"
    assert fakes.bodies() == [{"world": "funded", "event": "test", "reason": "none"}]
    assert URL not in proc.stdout + proc.stderr
    proc = _alert(fakes, "--test", FAIL_FIRST="99")
    assert proc.returncode == 1 and proc.stdout == "alert test: not delivered\n"
    monkeypatch.delenv("FACTORY_WEBHOOK_URL")
    proc = _alert(fakes, "--test")
    assert proc.returncode == 1 and proc.stdout == "alert test: not configured\n"


def test_alert_keeps_its_closed_vocabulary_and_https_only(fakes):
    assert _alert(fakes, "--event", "anything_else").returncode == 2
    assert _alert(fakes, "--free", "text").returncode == 2
    assert _alert(fakes, "--event", "unhealthy", "$(reboot) or prose").returncode == 0
    assert fakes.bodies() == [{"world": "funded", "event": "unhealthy", "reason": "none"}]
    for url in ("http://example.invalid/webhook-secret", 'https://x.invalid/"secret',
                "https://x.invalid/back\\slash"):
        assert _alert(fakes, "--test", FACTORY_WEBHOOK_URL=url).returncode == 1
    assert len(fakes.bodies()) == 1


def test_heartbeat_reports_the_last_verdict_and_never_a_stale_one(fakes):
    health = fakes.root / "runs/funded.health"
    expected = []
    for record, reason in (("healthy\n", "healthy"), ("unhealthy ledger_stale\n", "unhealthy"),
                           ("dormant\n", "dormant"), ("alive and well\n", "unknown")):
        health.write_text(record)
        assert _alert(fakes, "--event", "heartbeat", "healthy").returncode == 0
        expected.append(reason)
    old = time.time() - 3 * 3600
    health.write_text("healthy\n")
    os.utime(health, (old, old))
    assert _alert(fakes, "--event", "heartbeat").returncode == 0
    health.unlink()
    assert _alert(fakes, "--event", "heartbeat").returncode == 0
    expected += ["unknown", "unknown"]
    assert [b["reason"] for b in fakes.bodies()] == expected
    assert {b["event"] for b in fakes.bodies()} == {"heartbeat"}


@pytest.mark.parametrize("code,status,result,mode,event,reason", [
    ("exited", "1", "exit-code", "run", "failed_run", "exit_code"),
    ("killed", "KILL", "oom-kill", "run", "failed_run", "oom_kill"),
    ("exited", "1", "exit-code", "resume", "failed_resume", "exit_code"),
    ("exited", "1", "exit-code", None, "failed_start", "exit_code"),
    ("killed", "SEGV", "core-dump", "resume", "failed_resume", "core_dump"),
    ("killed", "TERM", "success", "resume", "stopped", "none"),
    ("killed", "TERM", "timeout", "resume", "failed_resume", "timeout"),
    ("exited", "3", "success", "resume", "terminated", "none"),
    ("exited", "0", "success", "resume", None, None),
])
def test_every_stop_of_the_world_unit_but_a_clean_exit_reaches_the_owner(
        fakes, code, status, result, mode, event, reason):
    if mode is not None:
        (fakes.runtime / "mode").write_text(mode + "\n")
    proc = _alert(fakes, EXIT_CODE=code, EXIT_STATUS=status, SERVICE_RESULT=result)
    assert proc.returncode == 0 and not proc.stdout and not proc.stderr
    assert fakes.bodies() == ([] if event is None else
                              [{"world": "funded", "event": event, "reason": reason}])
    # A reason code the runtime recorded wins over systemd's own result.
    if event is not None and event != "stopped":
        (fakes.runtime / "reason").write_text("manifest_mismatch\n")
        _alert(fakes, EXIT_CODE=code, EXIT_STATUS=status, SERVICE_RESULT=result)
        assert fakes.bodies()[-1]["reason"] == "manifest_mismatch"


def test_the_dead_mans_switch_is_pinged_only_while_healthy(tmp_path, fakes, monkeypatch):
    """FACTORY_HEARTBEAT_URL is an outside service that alerts when pings stop: so an
    unhealthy, unknown, dormant or dead factory must stop pinging."""
    monkeypatch.setattr(liveness, "disk_percent", lambda path: 10.0)
    monkeypatch.setenv("FACTORY_HEARTBEAT_URL", "https://hc.invalid/ping/webhook-secret")
    wake = tmp_path / "wake.json"
    argv = ["--wake", str(wake), "--state", str(tmp_path / "s"), "--alert", str(fakes.alert)]
    now = time.time_ns()
    for doc, pings in ((_wake(now), 1), (_wake(now, rows=[]), 0),
                       (_wake(now, status="dormant", rows=[]), 0),
                       (_wake(now, status="terminated"), 0), (None, 0)):
        before = [b for b in fakes.bodies() if b == {"get": True}]
        wake.unlink(missing_ok=True)
        if doc is not None:
            wake.write_text(json.dumps(doc))
        liveness.main(argv)
        assert len([b for b in fakes.bodies() if b == {"get": True}]) - len(before) == pings
    # The ping keeps the URL out of argv (the fake curl asserts it) and is HTTPS only.
    assert _alert(fakes, "--ping", FACTORY_HEARTBEAT_URL="http://hc.invalid/x").returncode == 1
    proc = _alert(fakes, "--test-ping")
    assert proc.returncode == 0 and proc.stdout == "ping test: delivered\n"
    assert fakes.bodies()[-1] == {"get": True}  # a bare GET: no body, no reason
    # Optional: unset is silent for the hourly ping and a failure for a person's test.
    monkeypatch.delenv("FACTORY_HEARTBEAT_URL")
    calls = len(fakes.bodies())
    assert _alert(fakes, "--ping").returncode == 0 and len(fakes.bodies()) == calls
    proc = _alert(fakes, "--test-ping")
    assert proc.returncode == 1 and proc.stdout == "ping test: not configured\n"


# --- the units --------------------------------------------------------------------------


def _unit(name: str) -> dict[str, list[str]]:
    keys: dict[str, list[str]] = {}
    for line in (DEPLOY / name).read_text().splitlines():
        if "=" in line and not line.startswith(("#", "[")):
            key, value = line.split("=", 1)
            keys.setdefault(key, []).append(value)
    return keys


def test_the_backup_unit_can_write_the_pin_backup_sh_creates_and_alerts_on_failure():
    """s11 #3: ProtectSystem=strict with no writable path failed every backup at the pin."""
    unit = _unit("factorylab-backup.service")
    assert unit["ProtectSystem"] == ["strict"] and unit["PrivateTmp"] == ["true"]
    assert unit["ReadWritePaths"] == ["/srv/factorylab/runs"]
    assert unit["OnFailure"] == ["factorylab-alert@backup_failed.service"]
    script = (DEPLOY / "backup.sh").read_text()
    assert "root=/srv/factorylab\n" in script and "runs = root / 'runs'" in script
    # The pin is hard-linked beside the files it pins: one writable mount holds both.
    assert "pin = runs / '.backup-pin'" in script and "os.link(entry, target" in script


def test_every_failure_path_names_an_event_alert_sh_sends(fakes):
    events = {"test"}
    for path in DEPLOY.glob("factorylab*.service"):
        for target in _unit(path.name).get("OnFailure", []):
            events.add(re.fullmatch(r"factorylab-alert@([a-z_]+)\.service", target).group(1))
    assert {"backup_failed", "wake_failed", "health_failed"} <= events
    heartbeat = _unit("factorylab-heartbeat.timer")
    assert heartbeat["Unit"] == ["factorylab-alert@heartbeat.service"]
    events.add("heartbeat")
    for event in sorted(events):
        assert _alert(fakes, "--event", event).returncode == 0, event
    assert sorted(b["event"] for b in fakes.bodies()) == sorted(events)


def test_the_health_heartbeat_and_alert_units_are_wired_as_documented():
    template = _unit("factorylab-alert@.service")
    assert template["ExecStart"] == [
        "/bin/bash /srv/factorylab/repo/deploy/alert.sh --event %i"]
    assert template["EnvironmentFile"] == ["/srv/factorylab/ops.env"]  # required, not "-"
    assert template["Type"] == ["oneshot"] and template["ProtectSystem"] == ["strict"]
    health = _unit("factorylab-health.service")
    handled = sorted(code for status, code in liveness.EXIT.items() if code)
    assert health["SuccessExitStatus"] == [" ".join(map(str, handled))] == ["10 11 12"]
    assert health["OnFailure"] == ["factorylab-alert@health_failed.service"]
    assert "--health /srv/factorylab/runs/funded.health" in health["ExecStart"][0]
    # The verdict ages the ledger at the check, so it runs as each publication
    # finishes, however long the wake took; the timer only covers a boot.
    assert "OnCalendar" not in _unit("factorylab-health.timer")
    assert _unit("factorylab-heartbeat.timer")["OnCalendar"] == ["*-*-* 00/6:45:00"]
    wake = _unit("factorylab-wake.service")
    assert wake["OnFailure"] == ["factorylab-alert@wake_failed.service"]
    assert wake["OnSuccess"] == ["factorylab-health.service"]
    assert "ExecStartPost" not in wake  # the witness moved to the health unit
    # ProtectSystem=strict refuses a unit whose writable path does not exist: every one
    # is a directory the provisioner creates before any world.
    provision = (DEPLOY / "cloud-init.yaml").read_text()
    created = set(re.findall(r"install -d -o factory -g factory -m 0\d{3} (\S+)", provision))
    for path in DEPLOY.glob("factorylab*.service"):
        for value in _unit(path.name).get("ReadWritePaths", []):
            assert set(value.split()) <= created, path.name


# --- backup staging and disk ------------------------------------------------------------


def _backup_root(tmp_path: Path) -> Path:
    root = tmp_path / "factory"
    (root / "runs/funded.checkpoint").mkdir(parents=True)
    (root / "runs/funded.jsonl").write_bytes(b'{"item": 1}\n{"item": 2}\n')
    (root / "runs/funded.checkpoint" / ("c" * 64)).write_bytes(b"sealed checkpoint")
    for relative in ("openrouter.key", "hyperliquid.key", "reserve.key",
                     "runs/funded.jsonl.key"):
        (root / relative).write_text("synthetic-fixture-only")
        (root / relative).chmod(0o600)
    (root / "repo/worlds").mkdir(parents=True)
    (root / "repo/worlds/funded.toml").write_text('name = "funded"\n')
    # The release interpreter, standing in for verify_restorable: it proves the copy.
    (root / "repo/.venv/bin").mkdir(parents=True)
    _executable(root / "repo/.venv/bin/python", (
        "#!/usr/bin/env python3\nimport sys\n"
        "sys.exit(1 if 'factorylab.runtime.release' in sys.argv else 0)\n"))
    return root


def _run_backup(tmp_path: Path, root: Path, *, avail_kb: int) -> subprocess.CompletedProcess:
    bin_path, staging = tmp_path / "backup-bin", tmp_path / "staging"
    bin_path.mkdir()
    staging.mkdir()
    _executable(bin_path / "df", (
        "#!/bin/bash\nprintf 'Filesystem 1024-blocks Used Available Capacity Mounted on\\n'\n"
        f"printf '/dev/vda1 80000000 1 {avail_kb} 1%% /\\n'\n"))
    _executable(bin_path / "age", (
        "#!/usr/bin/env python3\nimport os, sys\nfrom pathlib import Path\n"
        "open(os.environ['CALLS'], 'a').write('age\\n')\n"
        "Path(sys.argv[5]).write_bytes(sys.stdin.buffer.read())\n"))
    # The upload sees what is left in staging while it runs.
    _executable(bin_path / "rclone", (
        "#!/usr/bin/env python3\nimport os, sys\n"
        "seen = sorted(os.listdir(os.path.dirname(sys.argv[2])))\n"
        "open(os.environ['CALLS'], 'a').write('rclone ' + ' '.join(seen) + '\\n')\n"))
    script = (DEPLOY / "backup.sh").read_text().replace("/srv/factorylab", str(root))
    return subprocess.run(["bash", "-c", script], capture_output=True, timeout=60, env={
        **os.environ, "PATH": str(bin_path) + os.pathsep + os.environ["PATH"],
        "TMPDIR": str(staging), "CALLS": str(tmp_path / "calls"), "COPYFILE_DISABLE": "1",
        "AGE_RECIPIENT": "age1-fixture", "BACKUP_REMOTE": "fixture:bucket",
        "RCLONE_CONFIG": str(tmp_path / "fixture.conf")})


def _staged(tmp_path: Path) -> list[str]:
    """What the backup left in its staging directory. macOS's xcrun writes its own
    cache (``xcrun_db``) into TMPDIR when a stand-in tool runs; that is the host's,
    not the backup's."""
    return sorted(p.name for p in (tmp_path / "staging").iterdir()
                  if not p.name.startswith("xcrun_db"))


def test_backup_prunes_plaintext_before_upload_and_all_staging_after(tmp_path):
    root = _backup_root(tmp_path)
    proc = _run_backup(tmp_path, root, avail_kb=60_000_000)
    assert proc.returncode == 0, proc.stderr.decode()
    assert (tmp_path / "calls").read_text().splitlines() == ["age", "rclone backup.tar.age"]
    assert _staged(tmp_path) == []
    assert not (root / "runs/.backup-pin").exists()
    assert (root / "runs/funded.jsonl").read_bytes() == b'{"item": 1}\n{"item": 2}\n'


def test_backup_refuses_to_stage_without_room_beside_the_world(tmp_path):
    """Staging a copy into the disk the diary needs would kill the world's own writes."""
    root = _backup_root(tmp_path)
    # 80 GB filesystem: the floor kept free is a tenth of it, 8 GB.
    proc = _run_backup(tmp_path, root, avail_kb=8_000_000)
    assert proc.returncode == 1
    assert b"backup not taken: not enough free space" in proc.stderr
    assert not (tmp_path / "calls").exists()
    assert _staged(tmp_path) == []


def test_the_supervisor_launches_and_resumes_the_edition8_roster():
    """Sol 6.1 r5: start.sh's own arguments, parsed by the CLI's parser, name a world
    that loads, and it is edition 8's fourteen-seat roster. A missing world made every
    start a manifest_unavailable refusal that Restart=always repeated forever."""
    import shlex

    from factorylab.runtime.cli import build_parser
    from factorylab.runtime.worlds import load_manifest

    text = (DEPLOY / "start.sh").read_text()
    ledger = re.search(r"^ledger=(\S+)$", text, re.M).group(1)
    invocations = re.findall(r'"\$cli" ((?:run|resume) [^;|\n]*)', text)
    assert [line.split()[0] for line in invocations] == ["run", "resume"]
    edition8 = load_manifest("edition8-launch")
    for line in invocations:
        argv = [ledger if a == "$ledger" else a for a in shlex.split(line)]
        args = build_parser().parse_args(argv)
        assert args.ledger == ledger
        world = load_manifest(args.world)
        assert len(world.assemblies) == 14
        assert world.assemblies == edition8.assemblies and world.models == edition8.models
    # The backup stages the manifest the supervisor runs, by its fixed path.
    backup = (DEPLOY / "backup.sh").read_text()
    assert f"repo/worlds/{args.world}.toml" in backup


def test_the_static_server_masks_every_key_and_starts_without_optional_files():
    """Sol 6.1 r5: the static unit masked rclone.conf as mandatory, so a host without
    backups could not start it, and it never masked polymarket.key. Every root key the
    CLI reads is masked, each file optionally ("-": absent is nothing to mask)."""
    from factorylab.runtime import cli

    (paths,) = _unit("factorylab-static.service")["InaccessiblePaths"]
    masked = paths.split()
    keys = re.findall(r'\("([a-z]+\.key)", "[A-Z_]+"\)', (Path(cli.__file__)).read_text())
    assert {"openrouter.key", "hyperliquid.key", "reserve.key", "polymarket.key"} <= set(keys)
    for name in (*keys, "ops.env", "rclone.conf"):
        assert f"-/srv/factorylab/{name}" in masked, name
    # The directories the provisioner creates stay mandatory: a missing one is an error.
    assert {"/srv/factorylab/runs", "/srv/factorylab/repo"} <= set(masked)
