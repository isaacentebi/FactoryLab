"""Released working state survives a crash at any point (the kernel cold review's probe).

A world whose provider writes a working state on every answer releases and collects
heads at every boundary. It is interrupted mid-window, right after an
``artifact.collected`` item, and between a blob's unlink and its ledger item, then
resumed through the real recovery journal. The diary's artifact trail and the final
summary must equal an uninterrupted run's: nothing still referenced is gone, and a
replay collects exactly what the recording collected.
"""

import hashlib
import json
import pathlib
from dataclasses import replace
from decimal import Decimal

import pytest

from factorylab.kernel.ledger import Ledger
from factorylab.runtime.loop import Runtime
from factorylab.runtime.resume import resume_world
from factorylab.runtime.worlds import load_manifest
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


def _runtime(path):
    return Runtime(load_manifest("scripted"), events=EVENTS, seed=1,
                   initial_balance_micro=None, ledger_path=str(path), router_gamma=.1,
                   provider=Writer())


def _items(path):
    manifest = json.loads(load_manifest("scripted").canonical_json())
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
