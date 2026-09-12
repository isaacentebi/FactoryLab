"""B16: no adapter failure creates a diary and every published Launch can be resumed."""

from dataclasses import replace

import pytest

from factorylab.runtime.loop import Runtime, run_world
from factorylab.runtime.resume import resume_world
from factorylab.runtime.worlds import load_manifest
from factorylab.world.exchange import VenueUnavailable
from factorylab.world.scripted import ScriptedProvider


def test_adapter_construction_failure_creates_no_ledger(tmp_path, monkeypatch):
    base = load_manifest("scripted")
    m = replace(base, exchange=replace(base.exchange, kind="hyperliquid"), drip=None)
    path = tmp_path / "startup.jsonl"

    def unavailable(**kwargs):
        raise VenueUnavailable("offline startup failure")

    monkeypatch.setattr("factorylab.runtime.bootstrap.HyperliquidExchange", unavailable)
    with pytest.raises(VenueUnavailable):
        run_world(m, events=1, ledger_path=str(path), provider=ScriptedProvider())
    assert not path.exists() and not path.with_suffix(".jsonl.key").exists()


def test_market_construction_failure_creates_no_ledger(tmp_path, monkeypatch):
    from factorylab.world.market import X402Provider

    class UnavailableMarket(X402Provider):
        def __init__(self, **kwargs):
            raise RuntimeError("offline startup failure")

    path = tmp_path / "market-startup.jsonl"
    monkeypatch.setattr("factorylab.runtime.bootstrap.X402Provider", UnavailableMarket)
    with pytest.raises(RuntimeError, match="offline startup failure"):
        run_world(load_manifest("scripted"), events=1, ledger_path=str(path),
                  provider=ScriptedProvider())
    assert not path.exists() and not path.with_suffix(".jsonl.key").exists()


@pytest.mark.parametrize("cut", ["snapshot", "launch"])
def test_crash_at_launch_boundary_has_a_recoverable_snapshot(tmp_path, cut):
    class Died(BaseException):
        pass

    m = load_manifest("scripted")
    path = str(tmp_path / "launch.jsonl")
    rt = Runtime(m, events=2, seed=1, initial_balance_micro=None, ledger_path=path,
                 drip=False, router_gamma=.1)
    if cut == "snapshot":
        snapshot = rt._snapshot

        def interrupt(boundary):
            snapshot(boundary)
            raise Died()

        rt._snapshot = interrupt
    else:
        launch = rt._launch

        def interrupt():
            launch()
            raise Died()

        rt._launch = interrupt
    with pytest.raises(Died):
        rt.run()
    result = resume_world(m, path)
    assert result["ledger_verify"] and result["stats"]["events"] >= 2
