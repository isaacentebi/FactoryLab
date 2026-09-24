"""Released working state survives a crash at any point (the kernel cold review's probe).

A world whose provider writes a working state on every answer releases and collects
heads at every boundary. It is interrupted mid-window, right after an
``artifact.collected`` item, and between a blob's unlink and its ledger item, then
resumed through the real recovery journal. The diary's artifact trail and the final
summary must equal an uninterrupted run's: nothing still referenced is gone, and a
replay collects exactly what the recording collected. A world whose retained private
state cap is small releases a retired seat's kept state for room, and is interrupted
between that release's ledger line and its index change.
"""

import hashlib
import json
import pathlib
from dataclasses import replace
from decimal import Decimal
from types import SimpleNamespace

import pytest

from factorylab.kernel.artifacts import CAPACITY_REFUSAL
from factorylab.kernel.ledger import Ledger
from factorylab.runtime import worlds
from factorylab.runtime.loop import Runtime
from factorylab.runtime.resume import resume_world
from factorylab.runtime.worlds import StorageSpec, load_manifest
from factorylab.world.exchange import FakeExchange
from factorylab.world.scripted import ScriptedProvider

pytestmark = pytest.mark.gate

EVENTS = 140
TRAIL = ("state.put", "artifact.put", "artifact.released", "artifact.collected",
         "artifact.retained", "venue.read_answered", "tool.refused")


class Writer(ScriptedProvider):
    """Every answer carries a working state; few distinct ones, so heads recur
    (A -> B -> A) and seats share bytes."""

    def complete(self, request):
        response = super().complete(request)
        try:
            body = json.loads(response.text)
        except ValueError:
            return response
        if isinstance(body, dict):
            k = int(hashlib.sha256(response.text.encode()).hexdigest()[:2], 16) % 40
            body["working_state"] = {"k": k, "pad": "x" * (5000 if k % 2 else 10)}
            if "action" in body and not body.get("tool_calls") and k % 3 == 0:
                # A read the kernel already made this tick: answered from the tick.
                body["tool_calls"] = [{"tool": "venue.mids", "args": {}}]
        return replace(response, text=json.dumps(body))


class Crash(BaseException):
    """The process dies here: nothing after it runs, nothing catches it."""


def _runtime(path, manifest=None):
    return Runtime(manifest or load_manifest("scripted"), events=EVENTS, seed=1,
                   initial_balance_micro=None, ledger_path=str(path), router_gamma=.1,
                   provider=Writer())


def _items(path, manifest=None):
    manifest = json.loads((manifest or load_manifest("scripted")).canonical_json())
    return Ledger.reopen(str(path), manifest=manifest)._recovery_items()


def _trail(items):
    """The artifact trail: every put, release, collection and size, in order.

    An inbox body names its evidence by ledger sequence, and a resume adds its own
    items to the diary, so a body put after a resume differs by that pointer alone:
    a put is compared by kind, owner and size. Every working state, release and
    collection is compared by its hash.
    """
    return [(i["kind"], i.get("artifact_kind"),
             None if i["kind"] == "artifact.put" and i.get("artifact_kind") != "working.state"
             else i.get("sha"), i.get("bytes"), i.get("owner"), i.get("records"))
            for i in items if i["kind"] in TRAIL]


def _summary(summary):
    summary = json.loads(json.dumps(summary, default=str))
    summary["stats"]["resumes"] = 0
    return {k: v for k, v in summary.items() if k not in ("ledger_path", "ledger")}


@pytest.fixture(scope="module")
def uninterrupted(tmp_path_factory):
    path = tmp_path_factory.mktemp("base") / "world.jsonl"
    summary = _runtime(path).run()
    items = _items(path)
    kinds = {i["kind"] for i in items}
    assert {"artifact.released", "artifact.collected", "venue.read_answered"} <= kinds
    return _summary(summary), _trail(items)


def _kill_after_event(rt, n):
    original = rt._process_event

    def process(event):
        result = original(event)
        if rt.n == n:
            raise Crash
        return result

    rt._process_event = process


def _kill_after_collected(rt, n):
    store = rt.artifacts
    append = store.ledger.append
    seen = [0]

    def watch(item):
        seq = append(item)
        if item.get("kind") == "artifact.collected":
            seen[0] += 1
            if seen[0] == n:
                raise Crash
        return seq

    store.ledger = type("Ledger", (), {"append": staticmethod(watch),
                                       "recovering": False})()


def _kill_after_unlink(rt, n, monkeypatch):
    unlink = pathlib.Path.unlink
    seen = [0]

    def watch(self, *args, **kwargs):
        unlink(self, *args, **kwargs)
        if self.parent.name.endswith(".artifacts"):
            seen[0] += 1
            if seen[0] == n:
                raise Crash

    monkeypatch.setattr(pathlib.Path, "unlink", watch)


LEFTOVER = b"bytes a lost put left behind"


def _kill_in_write(rt, n, how):
    """Die inside the Nth durable write: after it (``write``: the bytes are on disk and
    the ``artifact.put`` item is not, plus a blob no put will ever name), with only a
    partial temporary file (``torn``), or with a partial file under the final name
    (``torn-final``, a disk that tore)."""
    store = rt.artifacts
    write = store._write
    seen = [0]

    def watch(sha, data):
        # Only a write of bytes the disk does not hold yet can tear: an existing,
        # verified blob is never rewritten.
        if store.root is None or (store.root / sha).exists():
            return write(sha, data)
        seen[0] += 1
        if seen[0] != n:
            return write(sha, data)
        store.root.mkdir(parents=True, exist_ok=True)
        if how == "write":
            write(sha, data)
            write(hashlib.sha256(LEFTOVER).hexdigest(), LEFTOVER)
        elif how == "torn":
            (store.root / f".{sha[:12]}-torn").write_bytes(data[: len(data) // 2])
        else:
            (store.root / sha).write_bytes(data[: len(data) // 2])
        raise Crash

    store._write = watch


@pytest.mark.parametrize("mode,n", [("event", 37), ("event", 90), ("collected", 1),
                                    ("unlink", 2), ("write", 40), ("torn", 60),
                                    ("torn-final", 80)])
def test_a_crash_anywhere_resumes_to_the_uninterrupted_run(uninterrupted, tmp_path,
                                                           monkeypatch, mode, n):
    path = tmp_path / "world.jsonl"
    rt = _runtime(path)
    if mode == "event":
        _kill_after_event(rt, n)
    elif mode == "collected":
        _kill_after_collected(rt, n)
    elif mode == "unlink":
        _kill_after_unlink(rt, n, monkeypatch)
    else:
        _kill_in_write(rt, n, mode)
    with pytest.raises(Crash):
        rt.run()
    monkeypatch.undo()
    before = _items(path)
    summary = resume_world(load_manifest("scripted"), str(path), provider=Writer())
    after = _items(path)
    assert after[:len(before)] == before
    expected_summary, expected_trail = uninterrupted
    assert _trail(after) == expected_trail
    assert _summary(summary) == expected_summary
    # The disk holds no torn temporary file and no bytes a lost put left behind, and
    # removing them left no item in the diary.
    root = path.with_suffix(".artifacts")
    assert not list(root.glob(".*"))
    leftover = hashlib.sha256(LEFTOVER).hexdigest()
    assert not (root / leftover).exists()
    assert leftover not in json.dumps(after)


class WeighedVenue(FakeExchange):
    """A live-shaped venue: it keeps the adapter's count of request weight sent."""

    def __init__(self):
        super().__init__(seed=1, coins=("BTC",), start_cash_usd=Decimal("100"))
        self.sent = 0

    def request_weight_sent(self):
        return self.sent

    @property
    def drain_events(self):
        # The live adapter keeps no local event queue: its fills are polled.
        raise AttributeError("drain_events")

    def candles(self, coin, interval, n):
        self.sent += 20 + n // 60
        return super().candles(coin, interval, n)

    def mids(self):
        self.sent += 2
        return super().mids()


def test_a_kill_between_the_weight_counter_call_and_its_result_resumes(tmp_path):
    """The counter is a read: an unanswered call to it is not an unacknowledged write,
    a replay re-reads it, it counts no venue write, and nothing escapes the tool."""
    from factorylab.runtime.loop import run_world
    from factorylab.runtime.resume import _read_only, resume_runtime
    from factorylab.world.clock import ClockSource

    assert _read_only("exchange.request_weight_sent")
    base = load_manifest("scripted")
    m = replace(base, exchange=replace(base.exchange, kind="hyperliquid", coins=("BTC",)))
    path = tmp_path / "live.jsonl"
    venue = WeighedVenue()
    run_world(m, events=30, seed=1, ledger_path=str(path), provider=Writer(),
              exchange=venue, clock_source=ClockSource(1_000_000_000, 1_000_000_000, 30).events())
    diary = [i for i in Ledger.reopen(str(path), manifest=json.loads(m.canonical_json()))
             ._recovery_items()]
    snapshot = next(i["seq"] for i in diary if i["kind"] == "snapshot")
    call = next(i for i in diary if i["kind"] == "io.call"
                and i["name"] == "exchange.request_weight_sent" and i["seq"] > snapshot)
    prefix = b"".join(path.read_bytes().splitlines(keepends=True)[: call["seq"] + 2])
    path.write_bytes(prefix)
    path.with_suffix(path.suffix + ".head").unlink()
    restored = resume_runtime(m, str(path), provider=Writer(), exchange=venue, now_ns=10**15)
    assert path.read_bytes().startswith(prefix)
    # A read of the counter is no venue write: the treasury's balance memo, keyed on
    # the journal's venue write count, stays valid across it.
    writes = dict(restored.ledger.writes)
    assert restored._venue_weight_sent() == venue.sent
    assert restored.ledger.writes == writes
    restored._ledger_lock.close()


RETIRE_AT = 60


def _retiring(monkeypatch, retire=None, register=None):
    """Every runtime this test builds, a resumed one included, retires each seat of
    ``retire`` (event -> seat; by default seed-observer after ``RETIRE_AT``) and
    registers each ``register`` (event -> (proposer, id)) as a program seat, a new id
    or a retired id's next version, right after that event; a program's first version
    is given a private state. Governance recorded in the diary like any other, so the
    replay applies it at the same point."""
    from factorylab.cortex.request import Return
    from factorylab.kernel.queue import PropensityRecord

    retire = {RETIRE_AT: "seed-observer"} if retire is None else retire
    register = register or {}
    process = Runtime._process_event

    def process_then_govern(self, event):
        result = process(self, event)
        seat = retire.get(self.n)
        if seat is not None and seat not in self.retired_assemblies:
            self._retire_assembly(seat, f"vote-{seat}")
        if self.n in register and (register[self.n][1] not in self.assemblies
                                   or register[self.n][1] in self.retired_assemblies):
            proposer, seat_id = register[self.n]
            handle = self.queue.open(
                actor=proposer, event_id="re-register", propensity=PropensityRecord(
                    (proposer,), (1.,), proposer, 0, proposer, "test"), channel="verdict",
                deadline_ns=10**15, parent_handle=None, cost_ceiling=10_000_000)
            self.handle_to_assembly[handle] = proposer
            self._apply_registrations(handle, Return(handle, {"register": [{
                "kind": "assembly", "id": seat_id, "model_id": "program",
                "accepts": ["Tick"], "code": "print('{}')", "state_policy": "private"}]},
                0, "ok"))
            program = self.assemblies[seat_id]
            if program.spec.version == 1:
                program.state_sha = self.artifacts.put(
                    json.dumps({"kept": seat_id}).encode(), owner=seat_id,
                    kind="program.state")
        return result

    monkeypatch.setattr(Runtime, "_process_event", process_then_govern)


#: Small enough that the live seats' heads fill it after the retirements: the retired
#: seats' kept heads are released for room, and later live writes are refused.
TIGHT_CAP = 30_000
#: Three seats retire in a row, so a live write can need all three kept heads.
THREE_RETIRE = {60: "seed-observer", 61: "antagonist-a", 62: "eval-d"}


def _capacity(items):
    return [(i["owner"], i["artifact_kind"], i["sha"]) for i in items
            if i["kind"] == "artifact.released" and i.get("cause") == "capacity"]


def test_retirement_keeps_the_seat_s_state_in_a_world_run(tmp_path, monkeypatch):
    """Retirement releases nothing: under the default cap the retired seat's head is
    still held at the end of the run."""
    _retiring(monkeypatch)
    rt = _runtime(tmp_path / "world.jsonl")
    rt.run()
    assert "seed-observer" in rt.retired_assemblies
    assert rt.working_state.head("seed-observer") is not None
    # A head the seat replaced while it served is released as any seat's is; from its
    # retirement on, nothing of it is.
    items = _items(tmp_path / "world.jsonl")
    (retired,) = [i["seq"] for i in items if i["kind"] == "assembly.retired"
                  and i.get("assembly_id") == "seed-observer"]
    assert not [i for i in items if i["kind"] == "artifact.released"
                and i.get("owner") == "seed-observer" and i["seq"] > retired]


def test_a_crash_between_an_eviction_s_ledger_line_and_its_index_change_resumes(
        tmp_path, monkeypatch):
    _retiring(monkeypatch, retire=THREE_RETIRE)
    manifest = replace(load_manifest("scripted"), storage=StorageSpec(TIGHT_CAP))
    base = tmp_path / "base" / "world.jsonl"
    base.parent.mkdir()
    expected_summary = _summary(_runtime(base, manifest).run())
    expected = _items(base, manifest)
    evicted = _capacity(expected)
    assert [(owner, kind) for owner, kind, _ in evicted] == [
        (seat, "working.state") for seat in THREE_RETIRE.values()], (
        "the retired seats' heads were released for room, oldest retirement first")
    assert any(i["kind"] == "state.refused" and i["reason"] == CAPACITY_REFUSAL
               for i in expected)
    path = tmp_path / "crash" / "world.jsonl"
    path.parent.mkdir()
    rt = _runtime(path, manifest)
    append = rt.ledger.append

    def die_after_eviction(item):
        seq = append(item)
        if item.get("kind") == "artifact.released" and item.get("cause") == "capacity":
            raise Crash  # the release is ledgered; the index has not changed
        return seq

    rt.ledger.append = die_after_eviction
    with pytest.raises(Crash):
        rt.run()
    before = _items(path, manifest)
    assert before[-1]["kind"] == "artifact.released" and before[-1]["cause"] == "capacity"
    assert rt.working_state.head("seed-observer") is not None  # the index never moved
    summary = resume_world(manifest, str(path), provider=Writer())
    after = _items(path, manifest)
    assert after[:len(before)] == before
    assert _trail(after) == _trail(expected)
    assert _capacity(after) == evicted
    assert _summary(summary) == expected_summary


def test_a_resume_is_never_refused_for_the_host_s_free_disk(uninterrupted, tmp_path,
                                                             monkeypatch):
    """The free-disk bound is a genesis admission: a world whose recorded cap now exceeds
    half the host's free disk resumes, and runs to the uninterrupted result; a new world
    on that host is refused."""
    path = tmp_path / "world.jsonl"
    rt = _runtime(path)
    _kill_after_event(rt, 50)
    with pytest.raises(Crash):
        rt.run()
    cap = load_manifest("scripted").storage.retained_private_bytes
    free = cap  # half of it is below the recorded cap
    monkeypatch.setattr(worlds.shutil, "disk_usage",
                        lambda where: SimpleNamespace(total=free, used=0, free=free))
    assert "exceeds 1/2 of the host's free disk" in load_manifest("scripted").host_disk_problem()
    with pytest.raises(ValueError, match="exceeds 1/2 of the host's free disk"):
        _runtime(tmp_path / "new.jsonl")
    before = _items(path)
    summary = resume_world(load_manifest("scripted"), str(path), provider=Writer())
    after = _items(path)
    assert after[:len(before)] == before
    expected_summary, expected_trail = uninterrupted
    assert _trail(after) == expected_trail
    assert _summary(summary) == expected_summary


def _crash_and_resume(tmp_path, manifest, dies_after):
    """Run uninterrupted, then again killed right after the first ledger item
    ``dies_after`` accepts, resume, and return (expected items, crashed prefix, resumed
    items, expected summary, resumed summary)."""
    base = tmp_path / "base" / "world.jsonl"
    base.parent.mkdir()
    expected_summary = _summary(_runtime(base, manifest).run())
    expected = _items(base, manifest)
    path = tmp_path / "crash" / "world.jsonl"
    path.parent.mkdir()
    rt = _runtime(path, manifest)
    append = rt.ledger.append
    seen = []

    def die(item):
        seq = append(item)
        if dies_after(item, seen):
            raise Crash
        return seq

    rt.ledger.append = die
    with pytest.raises(Crash):
        rt.run()
    before = _items(path, manifest)
    summary = resume_world(manifest, str(path), provider=Writer())
    return expected, before, _items(path, manifest), expected_summary, _summary(summary)


def test_a_crash_partway_through_a_put_s_evictions_resumes(tmp_path, monkeypatch):
    """Three seats retire; one live write needs all three kept heads released. The
    process dies after the second release's ledger line: the replay releases the same
    references in the same order, and the world ends as the uninterrupted one."""
    _retiring(monkeypatch, retire=THREE_RETIRE)
    manifest = replace(load_manifest("scripted"), storage=StorageSpec(TIGHT_CAP))

    def second_eviction(item, seen):
        if item.get("kind") == "artifact.released" and item.get("cause") == "capacity":
            seen.append(item)
        return len(seen) == 2 and item is seen[-1]

    expected, before, after, expected_summary, summary = _crash_and_resume(
        tmp_path, manifest, second_eviction)
    evicted = _capacity(expected)
    assert [owner for owner, _kind, _sha in evicted] == [
        "seed-observer", "antagonist-a", "eval-d"]
    assert before[-1]["cause"] == "capacity" and len(_capacity(before)) == 2
    assert after[:len(before)] == before
    assert _trail(after) == _trail(expected)
    assert _capacity(after) == evicted
    assert summary == expected_summary


def test_a_crash_between_a_superseded_release_and_the_registration_resumes(
        tmp_path, monkeypatch):
    """seed-decider registers the program ``prog-c``, which keeps a private state; it
    retires, and seed-decider, its owner, registers its next version: new code starts
    with no program state, so the old one is superseded and released. The process
    dies right after that release's ledger line, before the registration completes:
    the replay repeats it, and the world ends as the uninterrupted one."""
    from tests.cortex.test_jail import require_jail

    require_jail()
    _retiring(monkeypatch, retire={60: "prog-c"},
              register={40: ("seed-decider", "prog-c"), 80: ("seed-decider", "prog-c")})

    def superseded(item, _seen):
        return item.get("kind") == "artifact.released" and item.get("cause") == "superseded"

    manifest = load_manifest("scripted")
    expected, before, after, expected_summary, summary = _crash_and_resume(
        tmp_path, manifest, superseded)
    released = [i for i in expected if i.get("cause") == "superseded"]
    assert [(i["owner"], i["artifact_kind"]) for i in released] == [
        ("prog-c", "program.state")]
    assert before[-1]["cause"] == "superseded"
    assert after[:len(before)] == before
    assert _trail(after) == _trail(expected)
    assert [i for i in after if i.get("cause") == "superseded"] == released
    assert summary == expected_summary
