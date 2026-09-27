"""The committed wallet floor is an irreversible death threshold."""

import json
from dataclasses import replace

import pytest

from factorylab.kernel.ledger import Ledger
from factorylab.kernel.wallet import Infeasible, Wallet
from factorylab.runtime.loop import run_world
from factorylab.runtime.worlds import load_manifest
from tests.runtime.test_loop import _consequence_runtime


def test_positive_floor_world_dies_at_floor():
    manifest = load_manifest("scripted")
    manifest = replace(manifest, termination=replace(manifest.termination, balance_floor_micro=10))
    rt = _consequence_runtime(manifest=manifest)
    rt.wallet.settle(10 - rt.wallet.balance, "loss", "exchange_pnl")
    assert rt.wallet.dead
    reason = rt.termination.check(rt.wallet, 0)
    assert reason == "balance_floor"
    assert rt._check_termination()
    assert rt.termination.reason == reason
    assert rt.ledger.final
    terminated = [i["event"] for i in rt.ledger._recovery_items()
                  if i.get("kind") == "event" and i["event"]["kind"] == "Terminated"]
    assert len(terminated) == 1 and terminated[0]["payload"]["reason"] == "balance_floor"
    with pytest.raises(Infeasible):
        rt.wallet.reserve(1, "after-death", "compute")


def test_floor_crossing_in_batch_cannot_be_undone_and_survives_restore():
    wallet = Wallet(100, Ledger(), balance_floor_micro=10)
    wallet.settle_batch([(-90, "loss", "exchange_pnl"), (50, "gain", "exchange_pnl")])
    assert wallet.balance == 60 and wallet.dead
    restored = Wallet(100, Ledger(), balance_floor_micro=10)
    restored._restore_state(wallet.state())
    assert restored.dead
    with pytest.raises(Infeasible):
        restored.reserve(1, "after-death", "compute")


def test_floor_checkpoint_cannot_change_launch_threshold():
    wallet = Wallet(100, Ledger(), balance_floor_micro=10)
    other = Wallet(100, Ledger(), balance_floor_micro=20)
    with pytest.raises(ValueError, match="floor"):
        other._restore_state(wallet.state())


@pytest.mark.parametrize("floor", [-1, 0.5, True])
def test_floor_rejects_invalid_money(floor):
    with pytest.raises((ValueError, TypeError)):
        Wallet(100, Ledger(), balance_floor_micro=floor)


def test_floor_is_immutable_and_paid_compute_reaches_it():
    wallet = Wallet(100, Ledger(), balance_floor_micro=10)
    with pytest.raises(AttributeError):
        wallet.balance_floor_micro = 0
    wallet.commit(wallet.reserve(90, "compute", "model"), 90)
    assert wallet.dead and wallet.balance == 10 and wallet.check_conservation()


def test_a_world_launched_at_its_floor_dies_before_its_first_event(tmp_path):
    """An unstarted runtime checked nothing, so the first event dripped into a dead
    wallet, raised ``Infeasible`` out of ``run`` and abandoned the ledger unsealed."""
    path = tmp_path / "at-floor.jsonl"
    manifest = load_manifest("scripted")
    manifest = replace(
        manifest, termination=replace(manifest.termination, balance_floor_micro=10_000)
    )
    summary = run_world(manifest, events=6, seed=1, initial_balance_micro=10_000,
                        ledger_path=str(path))
    assert summary["terminated"] and summary["termination_reason"] == "balance_floor"
    assert summary["seal_key_released"] and summary["wallet_balance_micro"] == 10_000
    frozen = Ledger.open_read_only(path, manifest=json.loads(manifest.canonical_json()))
    assert frozen.verify()
    times = frozen.event_times()
    assert times["launch"] and times["terminated"]
    terminated = [item for item in frozen.items()
                  if item.get("kind") == "event" and item["event"]["kind"] == "Terminated"]
    assert [i["event"]["payload"]["reason"] for i in terminated] == ["balance_floor"]


def test_a_world_launched_with_no_money_dies_as_balance_zero(tmp_path):
    """Without a declared floor the same launch is the ordinary zero-balance death."""
    path = tmp_path / "empty.jsonl"
    manifest = load_manifest("scripted")
    summary = run_world(manifest, events=6, seed=1, initial_balance_micro=0,
                        ledger_path=str(path))
    assert summary["terminated"] and summary["termination_reason"] == "balance_zero"
    assert summary["seal_key_released"]
    frozen = Ledger.open_read_only(path, manifest=json.loads(manifest.canonical_json()))
    assert frozen.verify() and frozen.event_times()["terminated"]
