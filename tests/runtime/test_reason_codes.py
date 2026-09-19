"""A failing command tells an operator one code from a closed vocabulary, and nothing else."""

import re

import pytest

from factorylab.runtime.cli import main
from factorylab.runtime.reasons import CredentialMissing, Reason

SECRET = "0xdeadbeefprivatekeymaterial"

# alert.sh only forwards a reason matching this; anything else becomes "none".
WEBHOOK_SHAPE = re.compile(r"^[a-z_]{1,40}$")


def test_every_code_survives_the_webhook_filter():
    values = [reason.value for reason in Reason]
    assert len(set(values)) == len(values)
    for value in values:
        assert WEBHOOK_SHAPE.match(value), value


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
