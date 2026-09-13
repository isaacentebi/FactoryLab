"""Round three, group F1: the architect's one control after launch is kill.

T8 (seat 1 finding 2, seat 3 finding 6). The command records
``explicit_kill:operator`` through ``Termination``, which is the only authority
that may publish ``Terminated`` and the only path that releases the seal. It
steers nothing: a world is created once and ended once.
"""

import json

import pytest

from factorylab.kernel.ledger import Ledger, LedgerLock
from factorylab.runtime.cli import LEDGER_BUSY_EXIT, TERMINATED_EXIT, build_parser, main
from factorylab.runtime.loop import run_world
from factorylab.runtime.worlds import load_manifest


@pytest.fixture
def living(tmp_path):
    """A world that ran, was interrupted, and is neither dead nor readable."""
    path = tmp_path / "scripted.jsonl"
    manifest = load_manifest("scripted")
    summary = run_world(manifest, events=6, seed=1, ledger_path=str(path))
    assert not summary["terminated"] and not summary["seal_key_released"]
    return path, manifest


def _terminated(path, manifest) -> dict:
    frozen = Ledger.open_read_only(path, manifest=json.loads(manifest.canonical_json()))
    return frozen.event_times()


def test_kill_ends_a_living_world_and_releases_its_seal(living, capsys):
    path, manifest = living
    code = main(["kill", "--world", "scripted", "--ledger", str(path)])
    out = json.loads(capsys.readouterr().out)
    assert code == TERMINATED_EXIT
    assert out["terminated"] and out["seal_key_released"]
    assert out["termination_reason"] == "explicit_kill:operator"
    assert _terminated(path, manifest)["terminated"]


def test_the_killed_world_records_the_operators_reason_in_its_own_diary(living):
    path, manifest = living
    assert main(["kill", "--world", "scripted", "--ledger", str(path)]) == TERMINATED_EXIT
    frozen = Ledger.open_read_only(path, manifest=json.loads(manifest.canonical_json()))
    final = [item for item in frozen.items()
             if item.get("kind") == "event" and item["event"]["kind"] == "Terminated"]
    assert [item["event"]["payload"]["reason"] for item in final] == ["explicit_kill:operator"]


def test_kill_refuses_while_another_process_holds_the_writer_lock(living, capsys):
    path, _ = living
    with LedgerLock(path):
        code = main(["kill", "--world", "scripted", "--ledger", str(path)])
    assert code == LEDGER_BUSY_EXIT
    assert capsys.readouterr().err.splitlines()[0] == "factorylab kill: ledger_busy"


def test_killing_a_dead_world_changes_nothing_and_stays_final(living, capsys):
    path, _ = living
    assert main(["kill", "--world", "scripted", "--ledger", str(path)]) == TERMINATED_EXIT
    capsys.readouterr()
    assert main(["kill", "--world", "scripted", "--ledger", str(path)]) == TERMINATED_EXIT
    assert capsys.readouterr().err.splitlines()[0] == "factorylab kill: terminated"


def test_kill_takes_no_argument_that_could_steer_a_world():
    """Nothing else: no steering, no arguments beyond world and ledger."""
    parser = build_parser()
    kill = parser._subparsers._group_actions[0].choices["kill"]
    options = {name for action in kill._actions for name in action.option_strings}
    assert options == {"-h", "--help", "--world", "--ledger"}
