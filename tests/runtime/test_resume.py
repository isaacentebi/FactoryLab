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
from factorylab.runtime.loop import Runtime, run_world
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
from factorylab.world.events import WorldEvent, WorldEventKind
from factorylab.world.exchange import FakeExchange, Order
from factorylab.world.scripted import ScriptedProvider

pytestmark = pytest.mark.slow


def child_timeout():
    """Retain the CI watchdog unless a slow host explicitly supplies a positive duration."""
    seconds = int(os.environ.get("FACTORYLAB_TEST_CHILD_TIMEOUT", "180"))
    if seconds <= 0:
        raise ValueError("FACTORYLAB_TEST_CHILD_TIMEOUT must be positive seconds")
    return seconds


def make_runtime(manifest, path, **kwargs):
    return Runtime(
        manifest,
        events=140,
        seed=1,
        initial_balance_micro=None,
        ledger_path=str(path),
        drip=True,
        router_gamma=0.1,
        **kwargs,
    )


def items(path, manifest):
    return Ledger.reopen(path, manifest=json.loads(manifest.canonical_json()))._recovery_items()


@pytest.fixture(scope="module")
def uninterrupted(scripted_run):
    return scripted_run("scripted", 140, 1).summary


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
    child = subprocess.run(
        [sys.executable, "-c", code, str(path), stop],
        capture_output=True, text=True, timeout=child_timeout()
    )
    assert child.returncode == -signal.SIGKILL, child.stderr
    assert os.stat(path).st_mode & 0o777 == 0o600
    assert os.stat(str(path) + ".key").st_mode & 0o777 == 0o600
    m = load_manifest("scripted")
    before = items(path, m)
    snapshots = [i for i in before if i["kind"] == "snapshot"]
    last = snapshots[-1]
    tail = before[last["seq"] + 1 :]
    assert any(i["kind"] == "decision.settle" for i in tail)
    if stop == "event250":
        assert before[-1]["n"] == 250
    else:
        assert len(snapshots) >= 3  # launch, first reserve window, next reserve window
        assert last["n"] < before[-1]["n"]
        # With the scripted consequence backstop at 20 events the first activation lands
        # before this crash point, so the resume must carry the activated edition forward
        # (the snapshot was taken after the activation, between reserve windows).
        assert decode(last["state"]["runtime"])["stats"].amendments_activated == 1
    resumed = resume_world(m, str(path))
    assert resumed["stats"]["resumes"] == 1
    resumed["stats"]["resumes"] = 0
    assert resumed == uninterrupted  # includes every aggregate; nothing else is excluded
    after = items(path, m)
    assert after[: len(before)] == before  # no truncation or duplicate historical writes
    assert sum(i["kind"] == "resume" for i in after) == 1


class ClockAmendmentProvider(ScriptedProvider):
    def complete(self, request):
        response = super().complete(request)
        body = json.loads(response.text)
        for proposal in body.get("register", []):
            if proposal.get("kind") == "amendment":
                proposal["tick_interval"] = "2s"
        return replace(response, text=json.dumps(body))


def clock_manifest():
    base = load_manifest("scripted")
    return replace(base, evaluation=replace(base.evaluation, consequence_backstop_events=20))


@pytest.fixture(scope="module")
def amended_uninterrupted():
    return run_world(
        clock_manifest(), events=140, seed=1, provider=ClockAmendmentProvider()
    )


@pytest.mark.parametrize("stop", ["activation", "snapshot"])
def test_sigkill_after_clock_amendment_preserves_interval_and_summary(
    tmp_path,
    amended_uninterrupted,
    stop,
):
    path = tmp_path / "amended.jsonl"
    code = """
import os, signal, sys
sys.path.insert(0, 'tests/runtime')
from test_resume import ClockAmendmentProvider, clock_manifest
from factorylab.runtime.loop import Runtime
rt = Runtime(clock_manifest(), events=140, seed=1, initial_balance_micro=None,
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
    child = subprocess.run(
        [sys.executable, "-c", code, str(path), stop],
        capture_output=True, text=True, timeout=child_timeout()
    )
    assert child.returncode == -signal.SIGKILL, child.stderr
    m = clock_manifest()
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
    assert after[: len(before)] == before
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


def test_partial_event_replays_settlement_after_durable_item_before_mutation(
    tmp_path, scripted_run,
):
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
    assert resumed == scripted_run(m, 8, 1).summary


@pytest.mark.parametrize("endowed", [False, True])
def test_snapshot_and_tail_restore_all_state_with_delayed_router_and_assembly_memory(
    tmp_path, endowed
):
    from factorylab.runtime.worlds import NS_PER_DAY, EndowmentSpec

    base = load_manifest("scripted")
    m = replace(
        base,
        novelty=replace(base.novelty, window_ns=2 * base.tick_interval_ns),
        assemblies=tuple(replace(a, memory_policy="handle-scoped") for a in base.assemblies),
    )
    if endowed:
        # Everything locked (C1): the world launches dormant (C2), a first tranche too
        # small to buy any seat releases during the run, the rest waits past the stop.
        m = replace(m, endowment=EndowmentSpec(m.initial_balance_micro, (
            (base.tick_interval_ns + 1, 1_000), (NS_PER_DAY, m.initial_balance_micro - 1_000))))
    path = tmp_path / "state.jsonl"
    rt = make_runtime(m, path)
    rt.events_budget = 6
    rt._build_router("Tick", "blum_mansour", 0.2)
    rt._build_router("Tick", "exp3", 0.3, replace=False)
    stop_after(rt, lambda r, e: r.ticks_consumed == 4 and str(e.kind) == "Tick")
    if endowed:
        assert rt.dormancy is not None and rt.wallet.released_tranches == 1
        assert rt.wallet.locked == m.initial_balance_micro - 1_000
        kinds = [i for i in items(path, m) if i["kind"] in ("dormant", "release")]
        assert [(i["kind"], i.get("state")) for i in kinds] == [
            ("dormant", "entered"), ("release", None)]
        assert not any(a.memory for a in rt.assemblies.values())  # no paid cognition
    else:
        assert any(a.memory for a in rt.assemblies.values())
    assert len([i for i in items(path, m) if i["kind"] == "snapshot"]) >= 3
    restored = resume_runtime(m, str(path))
    if endowed:
        assert restored.dormancy == rt.dormancy and restored.wallet.state() == rt.wallet.state()
    assert restored.stats.resumes == 1
    with pytest.raises(PermissionError):
        _ = restored.ledger.key_store.key
    restored.stats.resumes = 0  # compare the entire checkpoint schema, not just the summary
    assert runtime_state(restored) == runtime_state(rt)
    restored.stats.resumes = 1
    assert restored.run()["ledger_verify"]


def test_second_resume_replays_the_first_resume_items(tmp_path, scripted_run):
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
    assert resumed == scripted_run(m, 8, 1).summary


def test_resume_before_first_decision_keeps_sample_handle_and_every_summary_field(
    tmp_path, scripted_run,
):
    m = load_manifest("scripted")
    record = scripted_run(m, 3, 1)
    path = record.copy_to(tmp_path / "early")
    expected = record.summary
    snapshot = next(i for i in items(path, m) if i["kind"] == "snapshot")
    prefix = b"".join(path.read_bytes().splitlines(keepends=True)[: snapshot["seq"] + 2])
    path.write_bytes(prefix)
    # This synthetic crash predates the completed run's authenticated head.
    path.with_suffix(path.suffix + ".head").unlink()
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


def test_unacknowledged_live_model_call_books_uncertainty_without_resubmission(tmp_path):
    base = load_manifest("scripted")
    m = replace(base, exchange=replace(base.exchange, kind="hyperliquid"), drip=None)
    path = tmp_path / "unacknowledged.jsonl"
    provider, venue = RecordedProvider(), CountingVenue()
    run_world(
        m,
        events=4,
        seed=1,
        ledger_path=str(path),
        provider=provider,
        exchange=venue,
        clock_source=ClockSource(1_000_000_000, 1_000_000_000, 4).events(),
    )
    diary = items(path, m)
    call = next(
        i
        for i in diary
        if i["kind"] == "io.call"
        and i["name"] == "provider.complete"
        and i["seq"] > next(s["seq"] for s in diary if s["kind"] == "snapshot")
    )
    # A prefix ending after dispatch intent models the exact durable evidence at that cut.
    prefix = b"".join(path.read_bytes().splitlines(keepends=True)[: call["seq"] + 2])
    path.write_bytes(prefix)
    # A process dying at this call could not have written the final run's head.
    path.with_suffix(path.suffix + ".head").unlink()
    calls, orders = provider.calls, venue.orders_sent
    restored = resume_runtime(m, str(path), provider=provider, exchange=venue, now_ns=10**15)
    assert (provider.calls, venue.orders_sent) == (calls, orders)
    assert path.read_bytes().startswith(prefix)
    evidence = restored.ledger._recovery_items()
    assert any(i["kind"] == "metering.uncertain" for i in evidence)
    assert not restored.wallet.state()["reservations"]
    assert restored.wallet.check_conservation()
    restored._ledger_lock.close()


def test_live_resume_reconciles_open_position_and_times_out_outage_deadlines(tmp_path):
    base = load_manifest("scripted")
    m = replace(
        base, exchange=replace(base.exchange, kind="hyperliquid", coins=("BTC",)), drip=None
    )
    venue = CountingVenue()
    venue.place(Order("BTC", True, Decimal("0.0001")))
    venue.drain_events()
    path = tmp_path / "live.jsonl"
    rt = make_runtime(
        m, path, exchange=venue, clock_source=ClockSource(1_000_000_000, 1_000_000_000, 8).events()
    )
    rt.events_budget = 8
    stop_after(rt, lambda r, e: r.n == 25)
    outstanding = rt.queue.outstanding()
    assert outstanding and venue.account().positions
    orders_sent = venue.orders_sent
    now = max(d.deadline_ns for d in outstanding) + 1
    restored = resume_runtime(
        m,
        str(path),
        exchange=venue,
        now_ns=now,
        clock_source=ClockSource(now + 1, m.tick_interval_ns, 8 - rt.ticks_consumed).events(),
    )
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

    value = {
        5: deque([Fraction(1, 17), -0.0, float.fromhex("0x1.fffffffffffp-1022")], maxlen=3),
        "z": ("b", "a"),
        "a": frozenset({"x", "y"}),
    }
    saved = json.loads(json.dumps(encode(value), sort_keys=True, allow_nan=False))
    restored = decode(saved)
    assert restored == value and list(restored) == list(value)
    assert restored[5].maxlen == 3
    assert restored[5][1].hex() == value[5][1].hex()


def test_resume_command_keeps_saved_seed_and_budget(tmp_path, capsys, scripted_run):
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
    assert summary == json.loads(json.dumps(scripted_run(m, 3, 1).summary))


def test_resume_recovers_a_torn_tail_and_replays_past_repair_items(tmp_path, scripted_run):
    m = load_manifest("scripted")
    path = tmp_path / "torn.jsonl"
    rt = make_runtime(m, path)
    rt.events_budget = 5
    stop_after(rt, lambda r, e: r.n == 8)
    raw = path.read_bytes()
    prefix = b"".join(raw.splitlines(keepends=True)[:-1])
    path.write_bytes(raw[:-17])
    resumed = resume_world(m, str(path))
    resumed["stats"]["resumes"] = 0
    assert resumed == scripted_run(m, 5, 1).summary
    assert path.read_bytes().startswith(prefix)
    diary = items(path, m)
    assert sum(i["kind"] == "ledger.repaired" for i in diary) == 1
    again = resume_world(m, str(path))
    assert again["stats"]["resumes"] == 2 and again["ledger_verify"]


def test_repeated_resume_replays_old_jail_availability_before_refresh(tmp_path, monkeypatch):
    m = load_manifest("scripted")
    path = tmp_path / "host-change.jsonl"
    monkeypatch.setattr("factorylab.cortex.tools.jail_available", lambda: True)
    run_world(m, events=0, ledger_path=str(path))
    assert resume_world(m, str(path))["stats"]["resumes"] == 1
    monkeypatch.setattr("factorylab.cortex.tools.jail_available", lambda: False)
    restored = resume_runtime(m, str(path))
    assert restored.tool_jail_available is False
    assert restored.run()["stats"]["resumes"] == 2
    assert resume_world(m, str(path))["stats"]["resumes"] == 3


def test_resume_replays_a_disputed_vendor_bill_once_without_death(tmp_path):
    class Overrun(ScriptedProvider):
        def complete(self, request):
            return replace(super().complete(request), cost_micro=1_000_000_000)

    m = load_manifest("scripted")
    path = tmp_path / "overrun.jsonl"
    rt = make_runtime(m, path, provider=Overrun())
    rt.events_budget = 2
    append = rt.ledger.append

    def crash_after_commit(entry):
        seq = append(entry)
        if entry["kind"] == "wallet.commit" and entry["reason"].startswith("model:"):
            raise ProcessDeath
        return seq

    rt.ledger.append = crash_after_commit
    with pytest.raises(ProcessDeath):
        rt.run()
    first = next(i for i in rt.ledger._recovery_items() if i["kind"] == "wallet.commit")
    restored = resume_runtime(m, str(path), provider=Overrun())
    summary = restored.run()
    assert not summary["terminated"] and summary["wallet_conservation"]
    diary = restored.ledger._recovery_items()
    assert not any(i["kind"] == "metering.overrun" for i in diary)
    commits = [i for i in diary if i["kind"] == "wallet.commit"
               and i["reservation_id"] == first["reservation_id"]]
    assert commits == [first]
    dispute, = [i for i in diary if i["kind"] == "metering.disputed"
                and i["reservation_id"] == first["reservation_id"]]
    assert dispute["reported"] == 1_000_000_000 and dispute["booked"] == first["amount"]


@pytest.mark.parametrize("cut", ["fill:0", "fill:1", "consequence.refused"])
@pytest.mark.parametrize("recover_cash", [False, True])
def test_resume_books_the_entire_venue_fill_batch_once(tmp_path, cut, recover_cash):
    """A cut inside one exchange batch replays every venue effect exactly once.

    This was the fatal-fill test: the batch's fees and funding used to settle on the
    compute wallet (``wallet.settle``, handle ``fill:N``) and took a ten-micro wallet
    below zero. Since 7b3b509 venue P&L, fees and funding settle on the venue accounts
    only (``venue.settled``; the compute wallet's ``settle`` is reached by income
    alone), so no fill batch can kill a world and the old cut point is never written.
    The same batch is cut at the same places in their new form, and the resumed diary
    and summary must be the uninterrupted ones.
    """
    class BatchVenue(FakeExchange):
        def advance(self, ts_ns):
            events = [WorldEvent(WorldEventKind.FILL, ts_ns, self.name, {
                "order_id": str(i), "coin": "BTC", "is_buy": True, "size": "1", "px": "1",
                "fee_usd": "0.000020",
                "realized_usd": "0.000100" if recover_cash and i == 1 else "0",
            }) for i in range(2)]
            return [*events, WorldEvent(WorldEventKind.FUNDING, ts_ns, self.name, {
                "coin": "BTC", "paid_usd": "0.000005",
            })]

    m = replace(load_manifest("scripted"), initial_balance_micro=10, drip=None)

    def world(path):
        rt = make_runtime(m, path, exchange=BatchVenue())
        rt.events_budget = 1
        return rt

    whole = world(tmp_path / "whole.jsonl")
    expected = whole.run()
    path = tmp_path / "fill-batch.jsonl"
    rt = world(path)
    append = rt.ledger.append
    cuts = []

    def crash_after_append(entry):
        seq = append(entry)
        if (entry["kind"] == cut or
                entry["kind"] == "venue.settled" and entry["reference"] == cut):
            cuts.append(entry)
            raise ProcessDeath
        return seq

    rt.ledger.append = crash_after_append
    with pytest.raises(ProcessDeath):
        rt.run()
    assert len(cuts) == 1  # the process died at the item the parameter names
    prefix = path.read_bytes()
    restored = resume_runtime(m, str(path), exchange=BatchVenue())
    summary = restored.run()
    assert summary["stats"]["resumes"] == 1
    summary["stats"]["resumes"] = 0
    assert summary == expected
    assert summary["wallet_balance_micro"] == 10  # the venue batch never touched it
    assert summary["wallet_conservation"] and summary["ledger_verify"]
    assert summary["stats"]["fills"] == 2
    assert restored.fees_to_date == 40 and restored.funding_to_date == -5
    assert path.read_bytes().startswith(prefix)
    diary = restored.ledger._recovery_items()
    settlements = [item for item in diary if item["kind"] == "venue.settled"]
    assert [(item["reference"].split(":")[0], item["amount"]) for item in settlements] == [
        ("fill", -20), ("fill", -20 + 100 * recover_cash), ("funding", -5)]
    assert not any(item["kind"] == "wallet.settle" for item in diary)
    # Item for item, the resumed diary is the uninterrupted one with the resume's own
    # block (resume.begin .. resume: its reconciliation reads) inserted where the
    # replayed tail ran out, and nothing else added, dropped or reordered.
    body = ("seq", "prev_hash", "hash")
    kinds = [i["kind"] for i in diary]
    begin, end = kinds.index("resume.begin"), kinds.index("resume")
    resumed = [{k: v for k, v in i.items() if k not in body}
               for i in diary[:begin] + diary[end + 1:]]
    reference = [{k: v for k, v in i.items() if k not in body}
                 for i in whole.ledger._recovery_items()]
    assert resumed == reference
    # Fills nobody with an open account ordered are refused by the book (A9); the refusal
    # is booked once, like the fill it replaces, and the venue still saw every event.
    assert sum(item["kind"] == "consequence.fill" for item in diary) == 0
    assert sum(item["kind"] == "consequence.refused" for item in diary) == 2
    assert sum(item["kind"] == "consequence.funding" for item in diary) == 1


class CompositionProvider(ScriptedProvider):
    def complete(self, request):
        response = super().complete(request)
        text = request.messages[-1]['content']
        if self._producer_calls == 1:
            return replace(response, text=json.dumps({'requests': [{
                'target': 'self', 'description': 'child task', 'inputs': {},
                'outcome_schema': {'type': 'object', 'properties': {'answer': {'type': 'integer'}},
                                   'required': ['answer']}}]}))
        if 'REQUEST\nchild task' in text:
            return replace(response, text='{"answer":42}')
        return response


def test_child_dispatch_after_durable_intent_replays_without_duplicate_decisions(tmp_path):
    m = load_manifest('scripted')
    path = tmp_path / 'child.jsonl'
    rt = make_runtime(m, path, provider=CompositionProvider())
    rt.events_budget = 2
    append = rt.ledger.append

    def interrupt(entry):
        seq = append(entry)
        if entry['kind'] == 'request.child':
            raise ProcessDeath
        return seq

    rt.ledger.append = interrupt
    with pytest.raises(ProcessDeath):
        rt.run()
    restored = resume_runtime(m, str(path), provider=CompositionProvider())
    children = [i for i in restored.ledger._recovery_items() if i['kind'] == 'request.child']
    assert len(children) == 1
    child = children[0]
    assert restored.queue.get(child['handle']).parent_handle == child['resource_liability']
    calls = [i for i in restored.ledger._recovery_items()
             if i['kind'] == 'invocation' and i['handle'] == child['handle']]
    assert len(calls) == 1 and json.loads(calls[0]['outputs']) == {'answer': 42}
    assert restored.run()['ledger_verify']


def test_fake_treasury_trading_shock_replays_fee_unfunded_cut(tmp_path, monkeypatch):
    from factorylab.world.treasury import FakeRail

    base = load_manifest('scripted')
    m = replace(base, treasury=replace(base.treasury, fake_fee_micro=1_000_000))
    path = tmp_path / 'treasury-shock.jsonl'
    rt = Runtime(m, events=1, seed=1, initial_balance_micro=6_400_000,
                 ledger_path=str(path), drip=False, router_gamma=.1)
    poll = FakeRail.poll

    def cheaper_receipt(rail, step, state):
        return {**poll(rail, step, state), 'fee_micro': 10_000,
                'received_micro': state['amount_micro'] - 10_000}

    monkeypatch.setattr(FakeRail, 'poll', cheaper_receipt)
    submitted = rt.treasury.transfer('to_reserve', '5', handle='parent', now_ns=0)
    assert submitted['status'] == 'submitted'
    # The shock used to be a venue funding payment, but since 7b3b509 venue P&L, fees
    # and funding settle on the venue accounts and never reach the compute wallet, so
    # that shock no longer moves ``available`` at all. What still drives the wallet
    # below its holds mid-transfer is a vendor bill booked above its hold (a metered
    # overrun inside the reported-cost multiple): the same 0.9 USD leaves the wallet.
    bill = rt.wallet.reserve(rt.wallet.available, 'shock', 'model:shock')
    assert rt.wallet.commit_reported(bill, 900_000) == 900_000
    assert rt.wallet.available < 0
    append = rt.ledger.append
    cuts = []

    def interrupt(entry):
        seq = append(entry)
        if entry['kind'] == 'treasury.fee_unfunded':
            cuts.append(entry)
            raise ProcessDeath
        return seq

    rt.ledger.append = interrupt
    with pytest.raises(ProcessDeath):
        rt.run()
    assert len(cuts) == 1 and cuts[0]['reserved_micro'] < cuts[0]['required_micro']
    restored = resume_runtime(m, str(path))
    assert restored.treasury.state['status'] == 'confirmed'
    assert restored.treasury.state['fees_micro'] == 10_000
    assert restored.wallet.check_conservation()
    assert not restored.wallet.state()['reservations']
    assert restored.run()['ledger_verify']


@pytest.mark.parametrize('cut', ['treasury.advance', 'treasury.step_submitted',
                                 'treasury.confirmed'])
def test_hybrid_conversion_killed_between_its_legs_resumes_without_a_second_spend(
        tmp_path, cut):
    """A kill anywhere in a hybrid conversion replays each leg once: one shadow send out
    of the venue, one real top-up, one financing, whatever the cut point."""
    sink = '0x000000000000000000000000000000000000dEaD'
    base = load_manifest('scripted')
    m = replace(base, treasury=replace(base.treasury, venice_network='base-mainnet',
                                       venice_shadow_sink=sink))
    path = tmp_path / 'hybrid-cut.jsonl'
    rt = Runtime(m, events=4, seed=1, initial_balance_micro=None, ledger_path=str(path),
                 drip=False, router_gamma=.1)
    assert rt.treasury.rail.name == 'scripted-hybrid'
    cash = rt.exchange._cash
    mainnet = rt.treasury.rail.hybrid_books['mainnet_reserve']
    submitted = rt.treasury.transfer('to_venice', '5', handle='parent', now_ns=0)
    assert submitted['status'] == 'submitted'
    append = rt.ledger.append

    def interrupt(entry):
        seq = append(entry)
        if entry['kind'] == cut:
            raise ProcessDeath
        return seq

    rt.ledger.append = interrupt
    with pytest.raises(ProcessDeath):
        rt.run()
    restored = resume_runtime(m, str(path))
    now = restored.clock.now_ns
    for step in range(1, 4):
        if restored.treasury.state['status'] == 'confirmed':
            break
        restored.treasury.tick(now + step)
    assert restored.treasury.state['status'] == 'confirmed'
    books = restored.treasury.rail.hybrid_books
    assert books['shadow_sent'] == 5_000_000 and restored.exchange._cash == cash - 5
    assert books['mainnet_reserve'] == mainnet - 5_000_000
    assert len(books['submissions']) == 1 and restored.treasury.rail.venice == 5_000_000
    financing = [i for i in items(path, m) if i['kind'] == 'treasury.financing']
    assert len(financing) == 1 and financing[0]['source'] == 'venue_perps'
    assert restored.wallet.check_conservation()
    assert not restored.wallet.state()['reservations']
    assert restored.run()['ledger_verify']


class OrderCutProvider(ScriptedProvider):
    def __init__(self, operation, args):
        super().__init__()
        self.operation, self.args = operation, args

    def complete(self, request):
        response = super().complete(request)
        if self._producer_calls == 1:
            return replace(response, text=json.dumps({'tool_calls': [{
                'tool': self.operation, 'args': self.args}]}))
        return response


@pytest.mark.parametrize('operation', ['place_market', 'close', 'cancel'])
def test_live_order_process_cut_after_acceptance_recovers_original_handle(tmp_path, operation):
    from factorylab.world.exchange import OrderKind

    class Venue(FakeExchange):
        def __getattribute__(self, name):
            if name in ('drain_events', 'sync_cash'):
                raise AttributeError(name)  # match the live adapter's pull-based fill surface
            return super().__getattribute__(name)

        def __init__(self):
            super().__init__(coins=('BTC',))
            self.armed = False
            self.writes = 0
            self.now = lambda: 0

        def collateral_view(self, coin, market='perp'):
            # A live adapter stamps the view with the moment of its account read
            # (d016697: an older stamp is refused as stale). Nothing advances this
            # fake's own time in a live-kind world, so without the read-time stamp
            # every new-risk order is refused as stale before it reaches place().
            return {**super().collateral_view(coin, market), 'observed_at_ns': self.now()}

        def place(self, order):
            result = super().place(order)
            if self.armed and operation == 'place_market':
                self.writes += 1
                raise ProcessDeath
            return result

        def close(self, *args, **kwargs):
            result = super().close(*args, **kwargs)
            if self.armed and operation == 'close':
                self.writes += 1
                raise ProcessDeath
            return result

        def cancel(self, *args, **kwargs):
            result = super().cancel(*args, **kwargs)
            if self.armed and operation == 'cancel':
                self.writes += 1
                raise ProcessDeath
            return result

    base = load_manifest('scripted')
    m = replace(base, exchange=replace(base.exchange, kind='hyperliquid', coins=('BTC',)),
                drip=None)
    venue = Venue()
    args = {'coin': 'BTC'}
    if operation == 'cancel':
        order = venue.place(Order('BTC', True, Decimal('.001'), OrderKind.LIMIT, Decimal(1)))
        args['order_id'] = order.order_id
    elif operation == 'close':
        venue.place(Order('BTC', True, Decimal('.001')))
    else:
        args.update(side='buy', size='.001')
    FakeExchange.drain_events(venue)
    venue.armed = True
    provider = OrderCutProvider(f'venue.{operation}', args)
    path = tmp_path / 'order-cut.jsonl'
    # Three ticks, because a live tick now broadcasts only the world's own
    # trading markets: one BTC MarketMid per tick, so the producer that writes
    # the order needs one more routed event than the old whole-venue broadcast.
    rt = make_runtime(m, path, provider=provider, exchange=venue,
                      clock_source=ClockSource(1_000_000_000, 1_000_000_000, 3).events())
    rt.events_budget = 3
    venue.now = lambda: rt.clock.now_ns
    with pytest.raises(ProcessDeath):
        rt.run()
    before = items(path, m)
    assert not any(i['kind'] in ('order.infeasible', 'order.refused') for i in before)
    intent = next(i for i in before if i['kind'] == 'order.intent')
    assert venue.writes == 1
    restored = resume_runtime(m, str(path), provider=provider, exchange=venue,
                              now_ns=rt.clock.now_ns)
    assert venue.writes == 1
    saved = restored.order_intents[intent['client_id']]
    assert saved['handle'] == intent['handle']
    assert saved['result']['status'] == ('cancelled' if operation == 'cancel' else 'filled')
    assert not restored.consequences.pending_orders
    assert restored.wallet.check_conservation()
    restored._ledger_lock.close()
