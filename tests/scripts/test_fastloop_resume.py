"""A crashed fastloop run resumes as the same run (Codex review of PR #151).

The admission cap is one money bound over the whole run, however many times it crashes;
the scripted stand-in continues its counters and its latency stream instead of
restarting them; a run replaying a diary's tick gaps resumes on that same clock.
"""

import json
from pathlib import Path

import pytest

from factorylab.runtime.loop import Runtime
from scripts import edition4_rehearsal as rehearsal
from scripts import fastloop

WORLD = Path(__file__).parents[2] / "worlds" / "edition6-testnet-rehearsal.toml"
TICKS = 10
CRASH_AT_EVENT = 30


class _Died(BaseException):
    """The process dies here."""


def _recording(monkeypatch, calls):
    """Record every call that reaches the scripted policy: (model, reply)."""
    complete = fastloop.PolicyProvider.complete

    def recorded(self, req):
        response = complete(self, req)
        calls.append((req.model_id, response.text))
        return response

    monkeypatch.setattr(fastloop.PolicyProvider, "complete", recorded)


@pytest.fixture(scope="module")
def reference(tmp_path_factory):
    """One uninterrupted run, shared by every comparison in this module."""
    calls: list = []
    with pytest.MonkeyPatch.context() as patch:
        _recording(patch, calls)
        card = fastloop.run("scripted", TICKS, WORLD, tmp_path_factory.mktemp("ref"),
                            cap_usd="2", seed=1)
    assert card["status"] == "completed", card.get("error")
    return card, calls


def _crash_then_resume(monkeypatch, out, **run_kwargs):
    calls: list = []
    _recording(monkeypatch, calls)
    process_event = Runtime._process_event

    def dies(self, ev):
        result = process_event(self, ev)
        if self.n == CRASH_AT_EVENT:
            raise _Died
        return result

    monkeypatch.setattr(Runtime, "_process_event", dies)
    with pytest.raises(_Died):
        fastloop.run("scripted", TICKS, WORLD, out, cap_usd="2", seed=1, **run_kwargs)
    before = list(calls)
    monkeypatch.setattr(Runtime, "_process_event", process_event)
    [target] = out.iterdir()
    card = fastloop.resume(target)
    assert card["status"] == "completed", card.get("error")
    return card, before, calls[len(before):]


@pytest.mark.gate
def test_a_resumed_run_spends_one_cap_and_continues_the_scripted_policy(
        reference, tmp_path, monkeypatch):
    ref_card, ref_calls = reference
    card, before, after = _crash_then_resume(monkeypatch, tmp_path / "out")
    assert before and after
    # P2: the policy's counters continued, so every call after the resume is the call
    # the uninterrupted run made at that point (before the fix they restarted at zero).
    assert before + after == ref_calls
    # P1: the resume began with the whole run's spend so far, not a fresh cap.
    cap = 2_000_000
    spent = sum(1 for _ in before)  # a scripted answer bills 1 micro-USD
    at_resume = card["admission_at_resume"]
    assert at_resume["known_micro"] == spent and at_resume["attempted"] == len(before)
    assert at_resume["remaining_micro"] == cap - spent
    # The card bills the whole run, exactly as the uninterrupted run was billed.
    assert card["billed_usd"] == ref_card["billed_usd"] != "0"


def test_a_call_the_process_died_inside_counts_its_whole_quote_on_resume(tmp_path):
    """Write-ahead: the record names a call's quote before the call is dispatched, so a
    death inside it leaves the quote counted against the cap as an uncertain bill."""
    from factorylab.world.models import ModelRequest

    manifest = fastloop.simulation_manifest(WORLD, 1)

    class Dies:
        def complete(self, req):
            raise _Died

    admission = rehearsal.Admission(cap_micro=1_000_000, max_calls=10)
    admission.known_micro = 1234  # what earlier calls billed
    prepaid = rehearsal.PrepaidProvider(Dies(), manifest, admission)
    record = tmp_path / fastloop.PROVIDER_RECORD
    provider = fastloop.RecordedProvider(prepaid, admission, record)
    request = ModelRequest(manifest.models[0].id, "s", ({"role": "user", "content": "x"},),
                           max_tokens=100)
    quote = prepaid._ceiling(request)
    provider._write(pending=quote)  # the write-ahead a death inside the call leaves
    fresh = rehearsal.Admission(cap_micro=1_000_000, max_calls=10)
    restored = fastloop.RecordedProvider(prepaid, fresh, record).restore()
    assert restored["died_inside_a_call"] is True
    assert fresh.known_micro == 1234 and fresh.uncertain_micro == quote
    assert restored["remaining_micro"] == 1_000_000 - 1234 - quote
    assert json.loads(record.read_text())["pending_quote_micro"] == quote
    with pytest.raises(_Died):  # the real path writes ahead, then records the outcome
        provider.complete(request)
    assert json.loads(record.read_text())["pending_quote_micro"] is None
    assert admission.attempted == 1


@pytest.mark.gate
def test_a_run_on_a_diarys_gaps_resumes_on_the_same_replay_clock(tmp_path, monkeypatch):
    """P2: run.json did not keep --gaps-from, so a resume built a plain clock and the
    restore refused it (tick_clock_mismatch)."""
    diary = tmp_path / "events.json"
    stamps = [0, 4 * 10**9, 21 * 10**9, 25 * 10**9]
    diary.write_text(json.dumps([{"kind": "event", "event": {"kind": "Tick", "ts_ns": ts}}
                                 for ts in stamps]))
    card, _before, _after = _crash_then_resume(monkeypatch, tmp_path / "out", gaps_from=diary)
    events = json.loads((Path(card["out"]) / "events.json").read_text())
    ticks = [e["event"]["ts_ns"] for e in events
             if e.get("kind") == "event" and e["event"]["kind"] == "Tick"]
    assert len(ticks) == TICKS
    # The recorded gaps, in order and cycled, across the crash.
    assert [b - a for a, b in zip(ticks, ticks[1:], strict=False)] == [
        [4, 17, 4][i % 3] * 10**9 for i in range(TICKS - 1)]
