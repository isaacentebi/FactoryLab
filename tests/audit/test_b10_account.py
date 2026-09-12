"""B10: a different venue account cannot inherit the original world's lots or learning."""

from dataclasses import replace

import pytest

from factorylab.runtime.loop import ScriptedProvider, run_world
from factorylab.runtime.resume import ResumeError, resume_runtime
from factorylab.runtime.worlds import load_manifest
from factorylab.world.clock import ClockSource
from factorylab.world.exchange import FakeExchange


def test_resume_refuses_a_changed_venue_address_before_live_reads(tmp_path):
    base = load_manifest("scripted")
    m = replace(base, exchange=replace(base.exchange, kind="hyperliquid"), drip=None)
    a, b = FakeExchange(), FakeExchange()
    a.address, b.address = "0x" + "11" * 20, "0x" + "22" * 20
    path = str(tmp_path / "account.jsonl")
    run_world(m, events=0, ledger_path=path, exchange=a, provider=ScriptedProvider(),
              clock_source=ClockSource(1, 1, 0))
    b.mids = lambda: pytest.fail("mismatched account was read")
    with pytest.raises(ResumeError) as error:
        resume_runtime(m, path, exchange=b, provider=ScriptedProvider(),
                       clock_source=ClockSource(1, 1, 0))
    assert error.value.code == "venue_account_mismatch"
