"""Bounded memory (wave 17): the diary names its checkpoint and large answers by hash.

Essay II: the factory "cannot be rewound by restoring some prior configuration";
essay II.IV.c: the control apparatus may not become slower than its environment.
A checkpoint written into the chain at every boundary made the diary grow with the
square of its age and each checkpoint cost more than the one before. Here:

* resume refuses a missing, stale, foreign or tampered checkpoint, and a missing
  or altered recorded answer, and writes nothing to the diary when it does;
* pruning the retained state no reader can reach changes nothing any reader sees:
  a world run with and without it writes the same diary;
* the diary grows linearly and no item exceeds the published cap;
* a checkpoint that costs more than ``1/min_ratio`` of its period is a ledgered
  alarm, never a kill.
"""

import json
from pathlib import Path

import pytest

from factorylab.kernel.ledger import Ledger, canonical
from factorylab.runtime.loop import Runtime
from factorylab.runtime.resume import ResumeError, resume_runtime
from factorylab.runtime.sidecar import ITEM_CAP_BYTES, checkpoint_root, io_root
from factorylab.runtime.worlds import load_manifest
from tests.conftest import make_runtime
from tests.helpers import keep_every_checkpoint
from tests.runtime.test_retained_state_crash import Writer


class RecordedWriter:
    """``Writer``'s answers from an adapter the replay may not call again: a recorded
    answer is read back from the diary, as a real provider's is."""

    name = "recorded-writer"

    def __init__(self):
        self.inner = Writer()

    def complete(self, request):
        return self.inner.complete(request)


def _items(path, manifest=None):
    manifest = json.loads((manifest or load_manifest("scripted")).canonical_json())
    return list(Ledger.open_read_only(path, manifest=manifest).items())


def _run(path, events, **kwargs):
    rt = Runtime(load_manifest("scripted"), events=events, seed=1, initial_balance_micro=None,
                 ledger_path=str(path), router_gamma=.1, **kwargs)
    return rt.run()


def _evidence(path):
    """Every byte a refused resume could have touched: the diary and its head."""
    head = Path(str(path) + ".head")
    return path.read_bytes(), head.read_bytes() if head.exists() else None


def _latest_file(path):
    manifest = json.loads(load_manifest("scripted").canonical_json())
    ledger = Ledger.open_read_only(path, manifest=manifest)
    latest = [i for i in ledger.items() if i["kind"] == "snapshot"][-1]
    return latest, checkpoint_root(path) / ledger.sidecar_name(latest["state_sha"])


@pytest.mark.parametrize("damage", ["missing", "stale", "tampered", "foreign"])
def test_resume_refuses_a_bad_checkpoint_and_writes_nothing(tmp_path, monkeypatch, damage):
    path = tmp_path / "w.jsonl"
    keep_every_checkpoint(monkeypatch)  # older checkpoints to put back, for "stale"
    _run(path, 30)
    monkeypatch.undo()
    latest, named = _latest_file(path)
    others = sorted(p for p in checkpoint_root(path).iterdir() if p != named)
    assert named.exists() and others
    if damage == "missing":
        named.unlink()
    elif damage == "stale":
        # An older checkpoint put back under the latest name: the factory never rewinds.
        named.write_bytes(others[0].read_bytes())
    elif damage == "tampered":
        raw = bytearray(named.read_bytes())
        raw[len(raw) // 2] ^= 0x01
        named.write_bytes(bytes(raw))
    else:
        # The exact state, sealed under another diary's key.
        import zlib

        from factorylab.runtime.resume import checkpoint_state

        manifest = json.loads(load_manifest("scripted").canonical_json())
        state = checkpoint_state(Ledger.open_read_only(path, manifest=manifest), latest)
        named.write_bytes(Ledger().seal_bytes(zlib.compress(canonical(state))))
    before = _evidence(path)
    with pytest.raises(ResumeError) as refused:
        resume_runtime(load_manifest("scripted"), str(path))
    assert refused.value.code == ("checkpoint_missing" if damage == "missing"
                                  else "checkpoint_mismatch")
    assert _evidence(path) == before


@pytest.mark.parametrize("damage", ["missing", "tampered"])
def test_resume_refuses_a_bad_recorded_answer_and_writes_nothing(tmp_path, monkeypatch,
                                                                   damage):
    """An answer named by hash in the replayed tail must be the one recorded."""
    path = tmp_path / "w.jsonl"
    keep_every_checkpoint(monkeypatch)  # the diary is cut back below its last checkpoint
    _run(path, 60, provider=RecordedWriter())
    monkeypatch.undo()
    diary = _items(path)
    first = next(i["seq"] for i in diary if i["kind"] == "snapshot" and i["n"] > 0)
    named = next(i for i in diary if i["kind"] == "io.result" and "result_sha" in i
                 and i["seq"] > first)
    lines = path.read_bytes().splitlines(keepends=True)
    path.write_bytes(b"".join(lines[: named["seq"] + 3]))
    Path(str(path) + ".head").unlink()
    manifest = json.loads(load_manifest("scripted").canonical_json())
    blob = io_root(path) / Ledger.open_read_only(path, manifest=manifest).sidecar_name(
        named["result_sha"])
    assert blob.exists()
    if damage == "missing":
        blob.unlink()
    else:
        raw = bytearray(blob.read_bytes())
        raw[len(raw) // 2] ^= 0x01
        blob.write_bytes(bytes(raw))
    before = _evidence(path)
    with pytest.raises(ResumeError) as refused:
        resume_runtime(load_manifest("scripted"), str(path), provider=RecordedWriter())
    assert refused.value.code == "io_result_missing"
    assert _evidence(path) == before


def test_a_large_answer_is_named_by_hash_and_stored_once(tmp_path):
    path = tmp_path / "w.jsonl"
    _run(path, 40, provider=Writer())
    diary = _items(path)
    named = [i for i in diary if i["kind"] == "io.result" and "result_sha" in i]
    assert named and all("result" not in i for i in named)
    assert all(len(canonical(i["result"])) <= 1_024
               for i in diary if i["kind"] == "io.result" and "result" in i)
    files = [p for p in io_root(path).iterdir() if not p.name.startswith(".")]
    # Deduplicated: one file per distinct answer, however often it was recorded.
    assert len(files) == len({i["result_sha"] for i in named})


def test_pruning_changes_nothing_any_reader_sees(tmp_path, monkeypatch):
    """The retained state dropped at each boundary is unreachable: with and without the
    pruning, one manifest and seed write the same diary, snapshot references aside."""
    pruned, kept = tmp_path / "pruned" / "w.jsonl", tmp_path / "kept" / "w.jsonl"
    pruned.parent.mkdir()
    kept.parent.mkdir()
    rt = Runtime(load_manifest("scripted"), events=120, seed=1, initial_balance_micro=None,
                 ledger_path=str(pruned), router_gamma=.1, provider=Writer())
    expected = rt.run()
    # The pruning did prune: history before the oldest open forecast, and the payloads
    # of returns no judgement can accept any more.
    assert rt.event_log_base > 0 and len(rt.events_log) < rt.n / 2
    slim = [e for e in rt.return_events.values() if "propensity" not in e.payload]
    assert len(slim) > len(rt.return_events) / 2
    monkeypatch.setattr(Runtime, "_prune_retained", lambda self: None)
    summary = _run(kept, 120, provider=Writer())

    def comparable(items):
        return [{k: v for k, v in i.items() if k not in ("ts", "hash", "prev_hash")}
                for i in items if i["kind"] != "snapshot"]

    def plain(summary):
        return {k: v for k, v in json.loads(json.dumps(summary, default=str)).items()
                if k not in ("ledger_path", "ledger")}

    assert comparable(_items(pruned)) == comparable(_items(kept))
    assert plain(summary) == plain(expected)


def _price_loop_open(rt):
    rt._manage_reserve_window()
    assert rt.clockwork.opened("price") is not None


class _Wall:
    """A paced wall clock that advances ``step`` ns on every read."""

    def __init__(self, tick_ns, step):
        self.now, self.tick, self.step = 0, tick_ns, step

    def now_ns(self):
        self.now += self.step
        return self.now

    def tick_ns(self):
        return self.tick


@pytest.mark.parametrize("slow", [False, True])
def test_a_checkpoint_slower_than_its_ratio_is_an_alarm_never_a_kill(slow):
    rt = make_runtime()
    _price_loop_open(rt)
    tick = 1_000_000_000
    period = rt.clockwork.period("price")
    ratio = rt.m.timing.min_ratio
    # One read before the checkpoint and one after: the cost is one step.
    step = period * tick // ratio + (1 if slow else -1)
    rt.wall = _Wall(tick, step)
    assert rt._snapshot("test") is True
    items = rt.ledger._recovery_items()
    snapshot = items[-1]
    assert snapshot["kind"] == "snapshot"
    assert snapshot["cost_ns"] == step and snapshot["period_ticks"] == period
    alarms = [i for i in items if i["kind"] == "checkpoint.slow"]
    assert len(alarms) == int(slow)
    if slow:
        assert alarms[0]["cost_ns"] * ratio > period * tick
    assert not rt.termination.final


def test_a_launch_checkpoint_has_no_period_to_serve():
    rt = make_runtime()
    rt.wall = _Wall(1_000_000_000, 10**15)
    rt._snapshot("launch")
    items = rt.ledger._recovery_items()
    assert items[-1]["period_ticks"] is None
    assert not [i for i in items if i["kind"] == "checkpoint.slow"]


def test_every_diary_item_is_under_the_cap_and_the_diary_grows_linearly(tmp_path):
    short, long = tmp_path / "short" / "w.jsonl", tmp_path / "long" / "w.jsonl"
    short.parent.mkdir()
    long.parent.mkdir()
    _run(short, 60, provider=Writer())
    _run(long, 120, provider=Writer())
    for path in (short, long):
        sizes = {i["kind"]: len(canonical(i)) for i in _items(path)}
        assert max(sizes.values()) <= ITEM_CAP_BYTES, max(sizes.items(), key=lambda kv: kv[1])
    # Twice the events, about twice the diary: never the square.
    assert long.stat().st_size <= 2.3 * short.stat().st_size
    assert len(list(checkpoint_root(long).iterdir())) == 1


def test_a_replaced_checkpoint_file_leaves_one_file(tmp_path):
    path = tmp_path / "w.jsonl"
    _run(path, 30)
    files = list(checkpoint_root(path).iterdir())
    assert len(files) == 1
    latest, named = _latest_file(path)
    assert files == [named]
    assert latest["bytes"] > 0 and "state" not in latest


def test_a_backup_copy_is_proven_restorable_or_refused(tmp_path):
    """What ``deploy/backup.sh`` runs on its staged copy before it uploads it."""
    import shutil

    from factorylab.runtime.sidecar import verify_restorable

    path = tmp_path / "live" / "w.jsonl"
    path.parent.mkdir()
    _run(path, 30)
    manifest = Path(__file__).resolve().parents[2] / "worlds" / "scripted.toml"
    stage = tmp_path / "stage"
    shutil.copytree(path.parent, stage)
    copy = stage / "w.jsonl"
    before = copy.read_bytes()
    report = verify_restorable(copy, manifest)
    latest, _named = _latest_file(copy)
    assert report["state_sha"] == latest["state_sha"]
    assert copy.read_bytes() == before
    for named in checkpoint_root(copy).iterdir():
        named.unlink()
    with pytest.raises(ResumeError) as refused:
        verify_restorable(copy, manifest)
    assert refused.value.code == "checkpoint_missing"


@pytest.mark.parametrize("damage", ["missing", "corrupt"])
def test_a_backup_copy_missing_a_replayed_answer_is_refused_by_name(tmp_path, monkeypatch,
                                                                     damage):
    """Every answer the replay tail names by hash must be beside the copy and hash-true.

    ``Writer`` pads every answer's working state past the 1 KiB inline cutoff, so the
    tail after the checkpoint names answers that live only in ``<world>.io/``."""
    from factorylab.runtime.sidecar import verify_restorable

    path = tmp_path / "w.jsonl"
    keep_every_checkpoint(monkeypatch)  # the copy is cut back below its last checkpoint
    _run(path, 60, provider=Writer())
    monkeypatch.undo()
    diary = _items(path)
    first = next(i["seq"] for i in diary if i["kind"] == "snapshot" and i["n"] > 0)
    tail = [i for i in diary if i["kind"] == "io.result" and "result_sha" in i
            and i["seq"] > first]
    named = tail[len(tail) // 2]
    cut = named["seq"] + 3
    lines = path.read_bytes().splitlines(keepends=True)
    path.write_bytes(b"".join(lines[:cut]))
    Path(str(path) + ".head").unlink()
    manifest = Path(__file__).resolve().parents[2] / "worlds" / "scripted.toml"
    report = verify_restorable(path, manifest)
    assert report["snapshot_seq"] == max(i["seq"] for i in diary
                                         if i["kind"] == "snapshot" and i["seq"] < cut - 1)
    assert report["answers"] >= 1
    scripted = json.loads(load_manifest("scripted").canonical_json())
    blob = io_root(path) / Ledger.open_read_only(path, manifest=scripted).sidecar_name(
        named["result_sha"])
    if damage == "missing":
        blob.unlink()
    else:
        raw = bytearray(blob.read_bytes())
        raw[len(raw) // 2] ^= 0x01
        blob.write_bytes(bytes(raw))
    before = path.read_bytes()
    with pytest.raises(ResumeError) as refused:
        verify_restorable(path, manifest)
    assert refused.value.code == "io_result_missing"
    # One file per distinct answer: the refusal names the first tail item that needs it.
    first_use = next(i["seq"] for i in tail if i["result_sha"] == named["result_sha"]
                     and i["seq"] > report["snapshot_seq"])
    assert refused.value.details == {"sha": named["result_sha"], "seq": first_use}
    assert path.read_bytes() == before


def test_an_older_diary_s_inline_checkpoint_still_reads():
    from factorylab.runtime.resume import checkpoint_state

    assert checkpoint_state(Ledger(), {"kind": "snapshot", "state": {"format": 1}}) == {
        "format": 1}
