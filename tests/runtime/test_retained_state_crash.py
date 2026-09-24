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

import pytest

from factorylab.kernel.ledger import Ledger
from factorylab.runtime.loop import Runtime
from factorylab.runtime.resume import resume_world
from factorylab.runtime.worlds import load_manifest
from factorylab.world.scripted import ScriptedProvider

pytestmark = pytest.mark.gate

EVENTS = 140
TRAIL = ("state.put", "artifact.put", "artifact.released", "artifact.collected",
         "artifact.retained")


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
    assert {"artifact.released", "artifact.collected"} <= kinds
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


@pytest.mark.parametrize("mode,n", [("event", 37), ("event", 90), ("collected", 1),
                                    ("unlink", 2)])
def test_a_crash_anywhere_resumes_to_the_uninterrupted_run(uninterrupted, tmp_path,
                                                           monkeypatch, mode, n):
    path = tmp_path / "world.jsonl"
    rt = _runtime(path)
    if mode == "event":
        _kill_after_event(rt, n)
    elif mode == "collected":
        _kill_after_collected(rt, n)
    else:
        _kill_after_unlink(rt, n, monkeypatch)
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
