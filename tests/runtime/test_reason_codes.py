"""A failing command tells an operator one code from a closed vocabulary, and nothing else."""

import os
import re
import stat
from pathlib import Path

import pytest

from factorylab.runtime.cli import main
from factorylab.runtime.reasons import CredentialMissing, Reason, record

SECRET = "0xdeadbeefprivatekeymaterial"

# alert.sh only forwards a reason matching this; anything else becomes "none".
WEBHOOK_SHAPE = re.compile(r"^[a-z_]{1,40}$")


def test_every_code_survives_the_webhook_filter():
    values = [reason.value for reason in Reason]
    assert len(set(values)) == len(values)
    for value in values:
        assert WEBHOOK_SHAPE.match(value), value


@pytest.mark.parametrize("argv,code,status", [
    (["manifest", "--world", "no-such-world"], Reason.MANIFEST_UNAVAILABLE, 1),
    (["report", "no-such-summary.json"], Reason.EVIDENCE_UNREADABLE, 1),
    (["postmortem", "no-such.jsonl", "no-such.jsonl.key"], Reason.EVIDENCE_UNREADABLE, 1),
    (["versions", "no-such.jsonl", "no-such.jsonl.key"], Reason.EVIDENCE_UNREADABLE, 1),
])
def test_read_only_commands_refuse_with_one_code(tmp_path, monkeypatch, capsys, argv, code,
                                                 status):
    monkeypatch.chdir(tmp_path)
    assert main(argv) == status
    captured = capsys.readouterr()
    assert captured.err == f"factorylab {argv[0]}: {code.value}\n"


def test_unreachable_venue_reports_a_code_and_not_the_provider(tmp_path, monkeypatch, capsys):
    def unreachable(**kwargs):
        raise RuntimeError(f"venue said: {SECRET}")

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("factorylab.world.probe.probe_hyperliquid", unreachable)
    assert main(["probe", "--world", "testnet"]) == 1
    captured = capsys.readouterr()
    assert captured.err == f"factorylab probe: {Reason.VENUE_UNREACHABLE.value}\n"
    assert SECRET not in captured.out + captured.err


@pytest.mark.parametrize("error,code,status", [
    (CredentialMissing(f"needs {SECRET}"), Reason.CREDENTIAL_MISSING, 2),
    (RuntimeError(f"provider body: {SECRET}"), Reason.ADAPTER_UNAVAILABLE, 1),
])
def test_a_world_that_cannot_start_says_so_without_its_interior(tmp_path, monkeypatch, capsys,
                                                                error, code, status):
    def refuse(*args, **kwargs):
        raise error

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("factorylab.runtime.loop.run_world", refuse)
    assert main(["run", "--world", "scripted", "--events", "1"]) == status
    captured = capsys.readouterr()
    lines = captured.err.splitlines()
    assert lines[0] == f"factorylab run: {code.value}"
    # A launch refusal also names the subsystem that refused: a class and a module
    # this repository wrote, never a provider's words. Nothing else follows.
    assert all(re.fullmatch(r"factorylab run: raised [A-Za-z]+ in factorylab(\.[a-z_]+)+", line)
               for line in lines[1:])
    assert len(lines) <= 2
    assert SECRET not in captured.out + captured.err


def test_resuming_another_manifest_names_the_mismatch(tmp_path, monkeypatch, capsys):
    """The wiring audit's unresumable world: the operator is told which fix to make."""
    monkeypatch.chdir(tmp_path)
    source = (Path(__file__).resolve().parents[2] / "worlds" / "scripted.toml").read_text()
    manifest = tmp_path / "rehearsal.toml"
    manifest.write_text(source.replace('name = "scripted"', 'name = "rehearsal"'))
    ledger = tmp_path / "rehearsal.jsonl"
    assert main(["run", "--world", str(manifest), "--events", "2",
                 "--ledger", str(ledger)]) == 0
    capsys.readouterr()
    manifest.write_text(manifest.read_text().replace('tick_interval = "1s"',
                                                     'tick_interval = "10s"'))
    assert main(["resume", "--world", str(manifest), "--ledger", str(ledger)]) == 1
    assert capsys.readouterr().err == f"factorylab resume: {Reason.MANIFEST_MISMATCH.value}\n"


def test_the_code_is_left_where_a_supervisor_reads_it(tmp_path, monkeypatch):
    monkeypatch.setenv("RUNTIME_DIRECTORY", str(tmp_path))
    record(Reason.LEDGER_INTEGRITY)
    written = tmp_path / "reason"
    assert written.read_text() == "ledger_integrity\n"
    assert stat.S_IMODE(written.stat().st_mode) == 0o600
    record(Reason.MANIFEST_MISMATCH)
    assert written.read_text() == "manifest_mismatch\n"


def test_without_a_runtime_directory_nothing_is_written(tmp_path, monkeypatch):
    monkeypatch.delenv("RUNTIME_DIRECTORY", raising=False)
    monkeypatch.chdir(tmp_path)
    record(Reason.LEDGER_INTEGRITY)
    assert not list(Path(tmp_path).iterdir())
    assert "RUNTIME_DIRECTORY" not in os.environ
