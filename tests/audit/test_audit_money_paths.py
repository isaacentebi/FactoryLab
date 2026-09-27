"""Cold audit, seat 4: money paths that lose or misbook micro-dollars.

Each test reproduces one finding in docs/audits/v2/defects-fable.md and fails on the
audited commit. Nothing here touches a network.
"""

import json
from dataclasses import replace
from decimal import Decimal

from factorylab.kernel.ledger import Ledger
from factorylab.runtime.loop import run_world
from factorylab.runtime.resume import resume_runtime
from factorylab.runtime.worlds import load_manifest
from factorylab.world.clock import ClockSource
from factorylab.world.exchange import FakeExchange
from factorylab.world.scripted import ScriptedProvider
from tests.helpers import keep_every_checkpoint


class RecordedProvider:
    name = "recorded-provider"

    def __init__(self):
        self.inner = ScriptedProvider()
        self.calls = 0

    def complete(self, request):
        self.calls += 1
        return self.inner.complete(request)


class Venue(FakeExchange):
    def __init__(self):
        super().__init__(seed=1, coins=("BTC",), start_cash_usd=Decimal("100"))


def _items(path, manifest):
    return Ledger.reopen(path, manifest=json.loads(manifest.canonical_json()))._recovery_items()


def test_replay_of_an_interrupted_event_does_not_charge_undispatched_model_calls(
        tmp_path, monkeypatch):
    """Finding 4: a process death after decision.open but before io.call means the provider was
    never contacted; the journal knows this ("never dispatched") yet metering books the full
    ceiling as an uncertain bill. Real money is not owed to anyone."""
    base = load_manifest("scripted")
    m = replace(base, exchange=replace(base.exchange, kind="hyperliquid", coins=("BTC",)))
    path = str(tmp_path / "w.jsonl")
    clock = ClockSource(1_000_000_000, 1_000_000_000, 8)
    keep_every_checkpoint(monkeypatch)  # the diary is cut back to its first checkpoint
    run_world(m, events=8, seed=1, ledger_path=path, provider=RecordedProvider(),
              exchange=Venue(), clock_source=clock.events())
    diary = _items(path, m)
    snap = next(s["seq"] for s in diary if s["kind"] == "snapshot")
    call = next(i for i in diary if i["kind"] == "io.call" and i["name"] == "provider.complete"
                and i["seq"] > snap)
    balance_at_cut = next(i["balance_after"] for i in reversed(diary)
                          if i["seq"] < call["seq"] and i["kind"].startswith("wallet."))
    lines = (tmp_path / "w.jsonl").read_bytes().splitlines(keepends=True)
    (tmp_path / "w.jsonl").write_bytes(b"".join(lines[: call["seq"] + 1]))
    # A process dying at this call could not have written the head of a snapshot after it
    # (price windows close every few ticks now, time audit T1).
    (tmp_path / "w.jsonl.head").unlink(missing_ok=True)
    provider = RecordedProvider()
    rt = resume_runtime(m, path, provider=provider, exchange=Venue(),
                        clock_source=ClockSource(1_000_000_000, 1_000_000_000, 8).events(),
                        now_ns=10**15)
    try:
        evidence = rt.ledger._recovery_items()
        booked = [i for i in evidence if i["kind"] == "metering.uncertain"
                  and i["seq"] >= call["seq"]]
        assert provider.calls == 0  # the journal refused to dispatch it, correctly
        assert booked == [], "an undispatched call was booked as an uncertain vendor bill"
        assert rt.wallet.balance == balance_at_cut
    finally:
        rt._ledger_lock.close()

