"""Nothing a world carries from one event to the next falls outside its checkpoint.

A resumed world must be the world that crashed. State kept on the runtime (or on
any object it owns) that the checkpoint neither saves nor rebuilds survives in
the running process and silently resets in the resumed one. The structural test
below restores ``runtime_state(rt)`` into a fresh runtime at many points of a
scripted run and walks both object graphs: every attribute that differs must be
declared in ``factorylab.runtime.resume`` as derived (rebuilt on demand from
checkpointed state), transient (belongs to this process, not the world) or
unordered (a mapping whose order carries no meaning). A new field that is none of
these fails here, naming its class and attribute.

The same 100-tick world is the uninterrupted reference of a late crash: a world
killed near its end resumes to the same diary and summary (the early crash points
are test_retained_state_crash's, over 30 ticks).
"""

import json
from collections import deque
from dataclasses import fields, is_dataclass
from decimal import Decimal
from enum import Enum
from fractions import Fraction
from types import MappingProxyType

import pytest

from factorylab.kernel.ledger import Ledger
from factorylab.runtime import resume
from factorylab.runtime.loop import Runtime
from factorylab.runtime.resume import restore_runtime, resume_world, runtime_state
from factorylab.runtime.worlds import load_manifest
from tests.conftest import make_runtime

_SCALARS = (bool, int, float, str, bytes, Enum, Fraction, Decimal, type(None))


def _declared(owner: str, attr: str, table) -> bool:
    return owner in table or f"{owner}.{attr}" in table


def walk(root) -> tuple[dict[tuple[str, str, str], object], set[str]]:
    """Every attribute of every object the runtime owns, as (class, attr, path) -> value.

    Containers and frozen records compare by value; any other object this package
    defines is walked once, by identity, so shared components are read once and
    back-references end the walk. Derived and transient attributes are not entered.
    The second result names every class and ``Class.attr`` the walk met.
    """
    out: dict[tuple[str, str, str], object] = {}
    present: set[str] = set()
    seen: set[int] = set()
    skip = {**resume._DERIVED_STATE, **resume._TRANSIENT_STATE}

    def value(v, path, stack, unordered=False):
        if isinstance(v, _SCALARS):
            return v
        if id(v) in stack:
            return ("cycle", type(v).__name__)
        stack = stack | {id(v)}
        if isinstance(v, (dict, MappingProxyType)):
            items = [(repr(k), value(x, f"{path}[{k!r}]", stack)) for k, x in v.items()]
            return ("dict", tuple(sorted(items) if unordered else items))
        if isinstance(v, (list, tuple, deque)):
            return (type(v).__name__,
                    tuple(value(x, f"{path}[{i}]", stack) for i, x in enumerate(v)))
        if isinstance(v, (set, frozenset)):
            return ("set", tuple(sorted(repr(x) for x in v)))
        if "Random" in type(v).__name__:
            return ("rng", v.getstate())
        if is_dataclass(v) and type(v).__dataclass_params__.frozen:
            return (type(v).__name__,
                    tuple(value(getattr(v, f.name), path, stack) for f in fields(v)))
        if (type(v).__module__ or "").startswith("factorylab") and hasattr(v, "__dict__"):
            owner = type(v).__name__
            present.add(owner)
            if owner in skip:
                return ("declared", owner)
            if id(v) in seen:
                return ("ref", owner)
            seen.add(id(v))
            for attr, x in vars(v).items():
                present.add(f"{owner}.{attr}")
                if _declared(owner, attr, skip):
                    continue
                out[(owner, attr, path)] = value(
                    x, f"{path}.{attr}", stack,
                    unordered=_declared(owner, attr, resume._UNORDERED_STATE))
            return ("obj", owner)
        return ("opaque", type(v).__name__)

    value(root, "", frozenset())
    return out, present


def restored_twin(rt):
    state = runtime_state(rt)
    twin = Runtime(rt.m, ledger_path=None, **state["config"])
    restore_runtime(twin, state)
    return twin


EVENTS = 100


def _runtime(path):
    return Runtime(load_manifest("scripted"), events=EVENTS, seed=1,
                   initial_balance_micro=None, ledger_path=str(path), router_gamma=.1)


def _items(path):
    manifest = json.loads(load_manifest("scripted").canonical_json())
    return Ledger.reopen(str(path), manifest=manifest)._recovery_items()


def _summary(summary):
    summary = json.loads(json.dumps(summary, default=str))
    summary["stats"]["resumes"] = 0
    return {k: v for k, v in summary.items() if k not in ("ledger_path", "ledger")}


@pytest.fixture(scope="module")
def world(tmp_path_factory):
    """One 100-tick scripted world, walked against a restored twin at every 150th event
    (the walk reads; it writes nothing the world or its diary sees)."""
    path = tmp_path_factory.mktemp("coverage") / "world.jsonl"
    rt = _runtime(path)
    original = rt._process_event
    differences: dict[str, tuple] = {}
    seen: set[str] = set()
    stops = []

    def compare(event):
        result = original(event)
        if rt.n % 150 == 0:
            del rt._process_event  # the hook is this test's, not the world's
            try:
                (before, present), (after, _) = walk(rt), walk(restored_twin(rt))
            finally:
                rt._process_event = compare
            stops.append(rt.n)
            seen.update(present)
            for key in before.keys() | after.keys():
                if before.get(key) != after.get(key):
                    owner, attr, path = key
                    differences.setdefault(f"{owner}.{attr}", (rt.n, path or "<runtime>"))
        return result

    rt._process_event = compare
    summary = rt.run()
    return {"differences": differences, "seen": seen, "stops": stops, "n": rt.n,
            "summary": _summary(summary), "items": _items(path)}


def test_every_attribute_a_world_carries_is_checkpointed_or_declared(world):
    differences, seen, stops = world["differences"], world["seen"], world["stops"]
    assert len(stops) >= 8, stops
    assert not differences, (
        "state that is neither checkpointed nor declared derived/transient/unordered "
        f"in factorylab/runtime/resume.py (attribute: first stop, path): {differences}")
    declared = (resume._DERIVED_STATE.keys() | resume._TRANSIENT_STATE.keys()
                | resume._UNORDERED_STATE.keys())
    assert not declared - seen, f"declared state no runtime carries: {declared - seen}"


class Crash(BaseException):
    """The process dies here: nothing after it runs, nothing catches it."""


def test_a_late_crash_resumes_to_the_uninterrupted_run(world, tmp_path):
    """Killed between events at nine tenths of the world, long after every early
    crash point and after its reserve windows have closed many times, the world
    resumes to the uninterrupted one's diary and summary."""
    path = tmp_path / "world.jsonl"
    rt = _runtime(path)
    late = world["n"] * 9 // 10
    process = rt._process_event

    def die_late(event):
        result = process(event)
        if rt.n == late:
            raise Crash
        return result

    rt._process_event = die_late
    with pytest.raises(Crash):
        rt.run()
    before = _items(path)
    summary = resume_world(load_manifest("scripted"), str(path))
    after = _items(path)
    assert after[:len(before)] == before
    assert _summary(summary) == world["summary"]
    # Past the resume's own block (resume.begin, its reconciling read, and the items up
    # to the closing ``resume``), the diary is the uninterrupted one's, item kind by
    # item kind. Contents are not compared: an item may point at a sequence number,
    # which the resume's own items shift.
    kinds = [i["kind"] for i in after if i["kind"] != "snapshot"]
    begin = kinds.index("resume.begin")
    end = kinds.index("resume", begin)
    assert set(kinds[begin:end + 1]) <= {"resume.begin", "io.call", "io.result",
                                         "resume.reconcile", "resume.timeouts", "resume"}
    assert kinds[:begin] + kinds[end + 1:] == [
        i["kind"] for i in world["items"] if i["kind"] != "snapshot"]


def test_declarations_say_why():
    for table in (resume._DERIVED_STATE, resume._TRANSIENT_STATE, resume._UNORDERED_STATE):
        for name, reason in table.items():
            assert isinstance(reason, str) and len(reason) > 20, name
    assert len(resume._RUNTIME_FIELDS) == len(set(resume._RUNTIME_FIELDS))


def test_receipts_waits_and_venue_deltas_survive_a_restore():
    from factorylab.settlement.receipts import ExecutionReceipt, LearningReceipt

    rt = make_runtime()
    learning = LearningReceipt(
        handle="decision-7", assessed="eval-a", scoring_rule="brier", rule_version="v1",
        horizon=3, outcome=1, score=.1)
    identity = rt.book.receipts.record(learning)
    rt.consequences.receipts.record(ExecutionReceipt(
        kind="fill", handle="decision-3", owner="seed-decider", at_event=4,
        facts={"coin": "BTC"}))
    rt.consequence_scores["decision-9"] = (0.625, 17)
    rt.world_outcomes["decision-3"] = {"state": "measured", "y": 1.0,
                                       "kind": "return_paid_off", "tick": 17}
    rt.venue_deltas["decision-3"] = {"venue_perps": -20}
    twin = restored_twin(rt)
    assert list(twin.book.receipts) == list(rt.book.receipts)
    assert list(twin.consequences.receipts) == list(rt.consequences.receipts)
    assert twin.settler.receipts() is twin.book.receipts
    assert twin.consequence_scores == {"decision-9": (0.625, 17)}
    assert twin.world_outcomes == rt.world_outcomes
    assert twin.venue_deltas == {"decision-3": {"venue_perps": -20}}
    # An identical re-record after the restore writes nothing, as it would have before.
    written = []
    twin.ledger.append = written.append
    assert twin.book.receipts.record(learning) == identity
    assert written == []


def test_a_checkpoint_carrying_the_deleted_adjudication_pipeline_restores_without_it():
    """Evaluations U1: the fidelity adjudication is deleted; an older checkpoint's
    adjudication receipt, open adjudication queue and settler objections are read and
    ignored, and everything else restores exactly."""
    from factorylab.settlement.receipts import LearningReceipt

    rt = make_runtime()
    rt.book.receipts.record(LearningReceipt(
        handle="decision-7", assessed="eval-a", scoring_rule="brier", rule_version="v1",
        horizon=3, outcome=1, score=.1))
    state = runtime_state(rt)
    adjudication = {"$record": "Adjudication", "fields": {
        "value": "useful inquiry", "measurement": "well_formed_rate",
        "evidence": "counted, not read", "objector": "eval-a",
        "objection_handle": "decision-7", "about_handle": "decision-3",
        "uncertainty": .25}}
    books = dict(state["receipts"]["$map"])
    books["book.receipts"].append(adjudication)
    state["runtime"]["$map"].append(["open_adjudications", {"$map": [["eval-b", "adjud:x"]]}])
    settler = dict(state["components"]["$map"])["settler"]["$map"]
    settler.append(["objections", {"$map": [["decision-7", {
        "$record": "FidelityObjection", "fields": {"value": "v"}}]]}])
    twin = Runtime(rt.m, ledger_path=None, **state["config"])
    restore_runtime(twin, state)
    assert list(twin.book.receipts) == list(rt.book.receipts)
    assert not hasattr(twin, "open_adjudications")
    assert not hasattr(twin.settler, "_Settler__objections")
