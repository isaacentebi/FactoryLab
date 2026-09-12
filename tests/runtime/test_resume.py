import json
import os
import signal
import subprocess
import sys
from dataclasses import replace
from decimal import Decimal

import pytest

from factorylab.kernel.ledger import Ledger, LedgerIntegrityError
from factorylab.kernel.queue import SettleStatus
from factorylab.runtime.loop import Runtime, ScriptedProvider, run_world
from factorylab.runtime.resume import (
    ResumeError,
    decode,
    encode,
    resume_runtime,
    resume_world,
    runtime_state,
)
from factorylab.runtime.worlds import load_manifest
from factorylab.world.clock import ClockSource
from factorylab.world.exchange import FakeExchange, Order


def make_runtime(manifest, path, **kwargs):
    return Runtime(manifest, events=140, seed=1, initial_balance_micro=None,
                   ledger_path=str(path), drip=True, router_gamma=.1, **kwargs)


def items(path, manifest):
    return Ledger.reopen(path, manifest=json.loads(manifest.canonical_json()))._recovery_items()


@pytest.fixture(scope="module")
def uninterrupted():
    return run_world(load_manifest("scripted"), events=140, seed=1)


@pytest.mark.parametrize("stop", ["event250", "between_windows"])
def test_sigkill_resume_matches_every_summary_field(tmp_path, uninterrupted, stop):
    path = tmp_path / "world.jsonl"
    code = """
import os, signal, sys
from factorylab.runtime.loop import Runtime
from factorylab.runtime.worlds import load_manifest
rt = Runtime(load_manifest('scripted'), events=140, seed=1, initial_balance_micro=None,
             ledger_path=sys.argv[1], drip=True, router_gamma=.1)
original = rt._process_event
def interrupt(event):
    result = original(event)
    stop = (rt.n == 250 if sys.argv[2] == 'event250' else
            rt.ticks_consumed == 125 and str(event.kind) == 'Tick')
    if stop:
        os.kill(os.getpid(), signal.SIGKILL)
    return result
rt._process_event = interrupt
rt.run()
raise AssertionError('kill point was not reached')
"""
    child = subprocess.run([sys.executable, "-c", code, str(path), stop],
                           capture_output=True, text=True, timeout=180)
    assert child.returncode == -signal.SIGKILL, child.stderr
    assert os.stat(path).st_mode & 0o777 == 0o600
    assert os.stat(str(path) + ".key").st_mode & 0o777 == 0o600
    m = load_manifest("scripted")
    before = items(path, m)
    snapshots = [i for i in before if i["kind"] == "snapshot"]
    last = snapshots[-1]
    tail = before[last["seq"] + 1:]
    assert any(i["kind"] == "decision.settle" for i in tail)
    if stop == "event250":
        assert before[-1]["n"] == 250
    else:
        assert len(snapshots) >= 3  # launch, first reserve window, next reserve window
        assert last["n"] < before[-1]["n"]
        assert decode(last["state"]["runtime"])["stats"].amendments_activated >= 1
    resumed = resume_world(m, str(path))
    assert resumed["stats"]["resumes"] == 1
    resumed["stats"]["resumes"] = 0
    assert resumed == uninterrupted  # includes every aggregate; nothing else is excluded
    after = items(path, m)
    assert after[:len(before)] == before  # no truncation or duplicate historical writes
    assert sum(i["kind"] == "resume" for i in after) == 1


class ClockAmendmentProvider(ScriptedProvider):
    def complete(self, request):
        response = super().complete(request)
        body = json.loads(response.text)
        for proposal in body.get("register", []):
            if proposal.get("kind") == "amendment":
                proposal["tick_interval"] = "2s"
        return replace(response, text=json.dumps(body))


@pytest.fixture(scope="module")
def amended_uninterrupted():
    return run_world(load_manifest("scripted"), events=140, seed=1,
                     provider=ClockAmendmentProvider())


@pytest.mark.parametrize("stop", ["activation", "snapshot"])
def test_sigkill_after_clock_amendment_preserves_interval_and_summary(
    tmp_path, amended_uninterrupted, stop,
):
    path = tmp_path / "amended.jsonl"
    code = """
import os, signal, sys
sys.path.insert(0, 'tests/runtime')
from test_resume import ClockAmendmentProvider
from factorylab.runtime.loop import Runtime
from factorylab.runtime.worlds import load_manifest
rt = Runtime(load_manifest('scripted'), events=140, seed=1, initial_balance_micro=None,
             ledger_path=sys.argv[1], drip=True, router_gamma=.1,
             provider=ClockAmendmentProvider())
snapshot = rt._snapshot
def interrupt_snapshot(boundary):
    if sys.argv[2] == 'activation' and rt.stats.clock_changes == 1:
        os.kill(os.getpid(), signal.SIGKILL)
    snapshot(boundary)
rt._snapshot = interrupt_snapshot
original = rt._process_event
def interrupt(event):
    previous_window = rt.reserve_window_start
    result = original(event)
    if rt.stats.clock_changes == 1 and previous_window != rt.reserve_window_start:
        os.kill(os.getpid(), signal.SIGKILL)
    return result
rt._process_event = interrupt
rt.run()
raise AssertionError('clock amendment kill point was not reached')
"""
    child = subprocess.run([sys.executable, "-c", code, str(path), stop],
                           capture_output=True, text=True, timeout=180)
    assert child.returncode == -signal.SIGKILL, child.stderr
    m = load_manifest("scripted")
    before = items(path, m)
    assert sum(i["kind"] == "clock.changed" for i in before) == 1
    last = next(i for i in reversed(before) if i["kind"] == "snapshot")
    assert last["state"]["tick_clock"]["interval_ns"] == (
        2_000_000_000 if stop == "snapshot" else m.tick_interval_ns
    )
    restored = resume_runtime(m, str(path), provider=ClockAmendmentProvider())
    assert restored.tick_clock.interval_ns == 2_000_000_000
    resumed = restored.run()
    assert resumed["stats"]["clock_changes"] == amended_uninterrupted["stats"]["clock_changes"] == 1
    assert resumed["stats"]["resumes"] == 1
    resumed["stats"]["resumes"] = 0
    assert resumed == amended_uninterrupted
    after = items(path, m)
    assert after[:len(before)] == before
    assert sum(i["kind"] == "clock.changed" for i in after) == 1
    assert sum(i["kind"] == "event" and i["event"]["kind"] == "Launch" for i in after) == 1


class ProcessDeath(BaseException):
    pass


def stop_after(rt, predicate):
    original = rt._process_event

    def interrupted(event):
        result = original(event)
        if predicate(rt, event):
            raise ProcessDeath
        return result

    rt._process_event = interrupted
    with pytest.raises(ProcessDeath):
        rt.run()


def test_partial_event_replays_settlement_after_durable_item_before_mutation(tmp_path):
    m = load_manifest("scripted")
    path = tmp_path / "partial.jsonl"
    rt = make_runtime(m, path)
    rt.events_budget = 8
    append = rt.ledger.append

    def crash_after_append(entry):
        seq = append(entry)
        if rt.n > 12 and entry["kind"] == "decision.settle":
            raise ProcessDeath
        return seq

    rt.ledger.append = crash_after_append
    with pytest.raises(ProcessDeath):
        rt.run()
    resumed = resume_world(m, str(path))
    resumed["stats"]["resumes"] = 0
    assert resumed == run_world(m, events=8, seed=1)


def test_snapshot_and_tail_restore_all_state_with_delayed_router_and_assembly_memory(tmp_path):
    base = load_manifest("scripted")
    m = replace(
        base, novelty=replace(base.novelty, window_ns=2 * base.tick_interval_ns),
        assemblies=tuple(replace(a, memory_policy="handle-scoped") for a in base.assemblies),
    )
    path = tmp_path / "state.jsonl"
    rt = make_runtime(m, path)
    rt.events_budget = 6
    rt._build_router("Tick", "blum_mansour", .2)
    rt._build_router("Tick", "exp3", .3, replace=False)
    stop_after(rt, lambda r, e: r.ticks_consumed == 4 and str(e.kind) == "Tick")
    assert any(a.memory for a in rt.assemblies.values())
    assert len([i for i in items(path, m) if i["kind"] == "snapshot"]) >= 3
    restored = resume_runtime(m, str(path))
    assert restored.stats.resumes == 1
    with pytest.raises(PermissionError):
        _ = restored.ledger.key_store.key
    restored.stats.resumes = 0  # compare the entire checkpoint schema, not just the summary
    assert runtime_state(restored) == runtime_state(rt)
    restored.stats.resumes = 1
    assert restored.run()["ledger_verify"]


def test_second_resume_replays_the_first_resume_items(tmp_path):
    m = load_manifest("scripted")
    path = tmp_path / "twice.jsonl"
    rt = make_runtime(m, path)
    rt.events_budget = 8
    stop_after(rt, lambda r, e: r.n == 20)
    once = resume_runtime(m, str(path))
    stop_after(once, lambda r, e: r.n == 40)
    resumed = resume_world(m, str(path))
    assert resumed["stats"]["resumes"] == 2
    resumed["stats"]["resumes"] = 0
    assert resumed == run_world(m, events=8, seed=1)


def test_resume_before_first_decision_keeps_sample_handle_and_every_summary_field(tmp_path):
    m = load_manifest("scripted")
    path = tmp_path / "early.jsonl"
    expected = run_world(m, events=3, seed=1, ledger_path=str(path))
    snapshot = next(i for i in items(path, m) if i["kind"] == "snapshot")
    prefix = b"".join(path.read_bytes().splitlines(keepends=True)[:snapshot["seq"] + 2])
    path.write_bytes(prefix)
    resumed = resume_world(m, str(path))
    assert resumed["stats"]["resumes"] == 1
    resumed["stats"]["resumes"] = 0
    assert resumed == expected


class CountingVenue(FakeExchange):
    def __init__(self):
        super().__init__(seed=1, coins=("BTC",), start_cash_usd=Decimal("100"))
        self.orders_sent = 0

    def place(self, order):
        self.orders_sent += 1
        return super().place(order)


class RecordedProvider:
    name = "recorded-provider"

    def __init__(self):
        self.inner = ScriptedProvider()
        self.calls = 0

    def complete(self, request):
        self.calls += 1
        return self.inner.complete(request)


def test_unacknowledged_live_model_call_refuses_without_release_or_resubmission(tmp_path):
    base = load_manifest("scripted")
    m = replace(base, exchange=replace(base.exchange, kind="hyperliquid"), drip=None)
    path = tmp_path / "unacknowledged.jsonl"
    provider, venue = RecordedProvider(), CountingVenue()
    run_world(m, events=4, seed=1, ledger_path=str(path), provider=provider, exchange=venue,
              clock_source=ClockSource(1_000_000_000, 1_000_000_000, 4).events())
    diary = items(path, m)
    call = next(i for i in diary if i["kind"] == "io.call" and i["name"] == "provider.complete"
                and i["seq"] > next(s["seq"] for s in diary if s["kind"] == "snapshot"))
    # A prefix ending after dispatch intent models the exact durable evidence at that cut.
    prefix = b"".join(path.read_bytes().splitlines(keepends=True)[:call["seq"] + 2])
    path.write_bytes(prefix)
    calls, orders = provider.calls, venue.orders_sent
    with pytest.raises(ResumeError, match="unacknowledged external write"):
        resume_runtime(m, str(path), provider=provider, exchange=venue, now_ns=10**15)
    assert (provider.calls, venue.orders_sent) == (calls, orders)
    assert path.read_bytes() == prefix  # Meter's exception cleanup must not release the hold


def test_live_resume_reconciles_open_position_and_times_out_outage_deadlines(tmp_path):
    base = load_manifest("scripted")
    m = replace(base, exchange=replace(base.exchange, kind="hyperliquid", coins=("BTC",)),
                drip=None)
    venue = CountingVenue()
    venue.place(Order("BTC", True, Decimal("0.0001")))
    venue.drain_events()
    path = tmp_path / "live.jsonl"
    rt = make_runtime(m, path, exchange=venue,
                      clock_source=ClockSource(1_000_000_000, 1_000_000_000, 8).events())
    rt.events_budget = 8
    stop_after(rt, lambda r, e: r.n == 25)
    outstanding = rt.queue.outstanding()
    assert outstanding and venue.account().positions
    orders_sent = venue.orders_sent
    now = max(d.deadline_ns for d in outstanding) + 1
    restored = resume_runtime(m, str(path), exchange=venue, now_ns=now,
                              clock_source=ClockSource(now + 1, m.tick_interval_ns,
                                                       8 - rt.ticks_consumed).events())
    assert venue.orders_sent == orders_sent  # replay made no duplicate venue submissions
    diary = items(path, m)
    timeouts = [i for i in diary if i["kind"] == "resume.timeouts"][-1]
    assert set(timeouts["handles"]) == {d.handle for d in outstanding}
    for d in outstanding:
        assert restored.queue.get(d.handle).status == SettleStatus.TIMED_OUT
        history = restored.queue.history(d.handle)
        assert sum(r.status == SettleStatus.TIMED_OUT for r in history) == 1
    reconcile = [i for i in diary if i["kind"] == "resume.reconcile"][-1]
    assert reconcile["positions"] and reconcile["venue_equity_usd"] is not None
    assert restored.stats.resumes == 1 and restored.wallet.check_conservation()
    assert restored.run()["ledger_verify"]


@pytest.mark.parametrize("failure", ["manifest", "corruption", "key", "terminated"])
def test_resume_refuses_invalid_evidence_without_writes(tmp_path, failure):
    m = load_manifest("scripted")
    path = tmp_path / "refused.jsonl"
    run_world(m, events=1, seed=1, ledger_path=str(path), kill_at_end=failure == "terminated")
    if failure == "manifest":
        m = replace(m, seed=m.seed + 1)
    elif failure == "corruption":
        raw = bytearray(path.read_bytes())
        raw[-40] ^= 1
        path.write_bytes(raw)
    elif failure == "key":
        (tmp_path / "refused.jsonl.key").unlink()
    before = path.read_bytes()
    with pytest.raises((ResumeError, LedgerIntegrityError)):
        resume_world(m, str(path))
    assert path.read_bytes() == before


def test_snapshot_codec_preserves_fraction_float_mapping_order_and_deque():
    from collections import deque
    from fractions import Fraction

    value = {5: deque([Fraction(1, 17), -0.0, float.fromhex("0x1.fffffffffffp-1022")], maxlen=3),
             "z": ("b", "a"), "a": frozenset({"x", "y"})}
    saved = json.loads(json.dumps(encode(value), sort_keys=True, allow_nan=False))
    restored = decode(saved)
    assert restored == value and list(restored) == list(value)
    assert restored[5].maxlen == 3
    assert restored[5][1].hex() == value[5][1].hex()


def test_resume_command_keeps_saved_seed_and_budget(tmp_path, capsys):
    from factorylab.runtime.cli import _cmd_resume, build_parser

    m = load_manifest("scripted")
    path = tmp_path / "cli.jsonl"
    rt = make_runtime(m, path)
    rt.events_budget = 3
    stop_after(rt, lambda r, e: r.n == 5)
    args = build_parser().parse_args(["resume", "--world", "scripted", "--ledger", str(path)])
    assert _cmd_resume(args) == 0
    summary = json.loads(capsys.readouterr().out)
    assert summary["seed"] == 1 and summary["manifest_hash"] == m.manifest_hash()
    assert summary["stats"]["resumes"] == 1
    summary["stats"]["resumes"] = 0
    assert summary == json.loads(json.dumps(run_world(m, events=3, seed=1)))
