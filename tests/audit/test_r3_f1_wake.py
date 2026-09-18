"""Round three, group F1: the wake publishes this world's accounts, and no position.

T19 (seat 5 finding 5, seat 6 finding 8): a key in the environment says what the
host can reach, not what the world owns. T28 (seat 5 findings 8-10): A17 publishes
no positions, and a coin with a side is a position.
"""

import json

import pytest

from factorylab.runtime.loop import run_world
from factorylab.runtime.wake import collect_wake, render_wake
from factorylab.runtime.worlds import load_manifest

# One window must close before the world has a portfolio of its own to publish;
# the scripted world closes its first at event 121.
EVENTS = 150


@pytest.fixture(scope="module")
def scripted(tmp_path_factory):
    """The world the README says needs no network, no keys and no money."""
    path = tmp_path_factory.mktemp("r3f1") / "scripted.jsonl"
    run_world(load_manifest("scripted"), events=EVENTS, seed=1, ledger_path=str(path))
    return path


@pytest.fixture
def no_live_reads(monkeypatch):
    """Any live account read from a fake world's wake is the defect itself."""
    def refuse(*_args, **_kwargs):
        raise AssertionError("a fake world's wake read a live account")

    monkeypatch.setattr("factorylab.world.exchange.live_exchange", refuse)
    monkeypatch.setattr("factorylab.world.x402.X402Client", refuse)
    monkeypatch.setenv("HL_PRIVATE_KEY", "0x" + "11" * 32)
    monkeypatch.setenv("RESERVE_PRIVATE_KEY", "0x" + "22" * 32)


def test_the_wake_publishes_no_open_position(scripted, no_live_reads):
    data = collect_wake(scripted)
    assert set(data["portfolio"]) == {"equity_micro", "realized_to_date_micro"}
    assert "open_positions" not in json.dumps(data)
    assert "open_positions" not in render_wake(data)
