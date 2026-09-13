"""Fixtures and factories shared by every test directory."""

from dataclasses import replace

from factorylab.runtime.loop import Runtime
from factorylab.runtime.worlds import load_manifest
from factorylab.world.exchange import FakeExchange
from factorylab.world.scripted import ScriptedProvider


def make_runtime(*, balance=100_000_000, live=False, clock_source=None):
    """Return a scripted-world runtime with no ledger file, no network and no credentials."""
    manifest = load_manifest("scripted")
    if live:
        manifest = replace(manifest, exchange=replace(manifest.exchange, kind="hyperliquid"),
                           drip=None)
    return Runtime(manifest, events=0, seed=1, initial_balance_micro=balance,
                   ledger_path=None, drip=False, router_gamma=.1,
                   exchange=FakeExchange(), provider=ScriptedProvider(),
                   clock_source=clock_source)
