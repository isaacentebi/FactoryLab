"""Retained state is bounded in total, not per version (PR #146, P1; essay II.II.b).

The disk is the world's own and pays no one, so retained bytes are a constraint:
the kernel keeps one working-state head per seat (and one private state per
program seat), releases the reference a superseded one held, and collects its
bytes once no checkpoint a resume could start from names them. Nothing a seat or
the settlement machinery still references is ever collected, and nothing about a
released record's former writer survives into what a later writer is shown.

Retirement is final for a version, not for an id, so a retired id keeps its state
for its next version; the disk is finite, so the whole of retained private state
has a hard cap (``[storage] retained_private_bytes``) that releases retired ids'
state, oldest retirement first, before it refuses a write.
"""

import hashlib
from dataclasses import replace
from types import SimpleNamespace

import pytest

from factorylab.cortex.assembly import MAX_PROGRAM_STATE_BYTES
from factorylab.kernel.artifacts import (
    CAPACITY_REFUSAL,
    ArtifactCapacityError,
    ArtifactStore,
    artifact_root,
)
from factorylab.kernel.ledger import Ledger
from factorylab.runtime import worlds
from factorylab.runtime.continuity import HARD_STATE_BYTES, canonical
from factorylab.runtime.loop import Runtime
from factorylab.runtime.resume import _check_artifacts
from factorylab.runtime.worlds import (
    DEFAULT_RETAINED_PRIVATE_BYTES,
    StorageSpec,
    load_manifest,
    manifest_from_dict,
)
from tests.conftest import make_runtime
from tests.runtime.test_connectors import decision, ledger_items
from tests.runtime.test_real_flows import _world

DAY_NS = 86_400 * 1_000_000_000


def _next_window(rt):
    rt.clock.now_ns += DAY_NS
    rt.clockwork.force("price", rt.ticks_consumed)
    rt._manage_reserve_window()


def _boundary(rt):
    """A reserve-window boundary with its checkpoint, as the loop runs one."""
    _next_window(rt)
    rt._snapshot("reserve_window")


def _retained_by(rt, seat):
    return sum(record["bytes"] for sha, record in rt.artifacts.index.items()
               if seat in rt.artifacts.references(sha, record))


def _store(tmp_path=None):
    ledger = Ledger(None, manifest={"name": "artifacts"})
    root = artifact_root(tmp_path / "w.jsonl") if tmp_path is not None else None
    return ArtifactStore(ledger, root=root, clock_ns=lambda: 1), ledger


def _kinds(ledger, kind):
    return [i for i in ledger._recovery_items() if i["kind"] == kind]


def test_a_new_near_limit_state_every_wake_keeps_retained_bytes_bounded():
    """A seat writing a different near-64 KiB state on every wake holds one head; the
    archive holds at most that head, the heads the latest checkpoint named, and the
    ones written since, which the next boundary collects."""
    rt = make_runtime()
    rt._manage_reserve_window()
    rt._snapshot("reserve_window")
    seat = "seed-decider"
    base = rt.artifacts.retained()["bytes"]
    peak = 0
    for wake in range(30):
        state = {"wake": wake, "notes": "x" * (HARD_STATE_BYTES - 64)}
        rt.working_state.put(seat, state, handle=decision(rt))
        assert _retained_by(rt, seat) <= HARD_STATE_BYTES
        if wake % 3 == 2:
            _boundary(rt)
        peak = max(peak, rt.artifacts.retained()["bytes"] - base)
    # One window's writes (three) plus the one head the latest checkpoint named.
    assert peak <= 4 * HARD_STATE_BYTES
    _boundary(rt)
    _boundary(rt)
    assert rt.artifacts.retained()["bytes"] - base <= HARD_STATE_BYTES
    head = rt.working_state.head(seat)
    assert rt.working_state.render(seat)["bytes"] == head["bytes"]
    assert (ledger_items(rt, "artifact.retained")[-1]["bytes"]
            == rt.artifacts.retained()["bytes"])


def test_a_release_takes_nothing_still_referenced():
    """The current heads, an inbox body, bytes another seat holds and bytes the same
    seat holds under another kind all survive the release of a superseded head."""
    rt = make_runtime()
    rt._manage_reserve_window()
    handle = decision(rt)
    shared = {"shared": "bytes two seats wrote"}
    shared_sha = rt.working_state.put("seed-decider", shared, handle=handle)["sha"]
    rt.working_state.put("seed-observer", shared, handle=handle)
    both = {"both": "a head and an inbox body of one seat"}
    both_sha = rt.working_state.put("eval-a", both, handle=handle)["sha"]
    rt.artifacts.put(canonical(both), owner="eval-a", kind="outcome.item")
    item = rt.outcomes.append("seed-decider", handle=handle, outcome={"kind": "fill"},
                              evidence="e1")
    rt.working_state.put("seed-decider", {"next": 1}, handle=handle)
    rt.working_state.put("eval-a", {"next": 2}, handle=handle)
    for _ in range(3):
        _boundary(rt)
    for sha in (shared_sha, both_sha, item["sha"], rt.working_state.head("seed-decider")["sha"],
                rt.working_state.head("eval-a")["sha"]):
        assert sha in rt.artifacts.index and rt.artifacts.get(sha)
    assert set(rt.artifacts.references(shared_sha, rt.artifacts.index[shared_sha])) == {
        "seed-observer"}
    # The kind a partial release leaves names only what the owner still holds.
    assert rt.artifacts.index[both_sha]["refs"]["eval-a"]["kind"] == "outcome.item"
    assert rt.outcomes.get("seed-decider", f"outcome:{item['seq']}")["outcome"] == {
        "kind": "fill"}


def test_a_head_the_latest_checkpoint_named_is_kept_until_a_later_one_is_durable():
    """A crash between a boundary's collection and its checkpoint resumes from a
    checkpoint that still names the head: it is not collected until a later one."""
    rt = make_runtime()
    rt._manage_reserve_window()
    handle = decision(rt)
    old = rt.working_state.put("seed-decider", {"v": 1}, handle=handle)["sha"]
    rt._snapshot("reserve_window")  # this checkpoint names ``old`` as the head
    rt.working_state.put("seed-decider", {"v": 2}, handle=handle)
    assert rt.artifacts.index[old]["released"] == "pending"
    _next_window(rt)  # this boundary collects before its checkpoint is written
    assert old in rt.artifacts.index and rt.artifacts.get(old)
    rt._snapshot("reserve_window")
    assert rt.artifacts.index[old]["released"] == "sealed"
    _next_window(rt)
    assert old not in rt.artifacts.index
    assert ledger_items(rt, "artifact.collected")[-1]["sha"] == old


def test_a_head_written_and_superseded_since_the_checkpoint_goes_at_the_next_boundary():
    """No checkpoint names it and a replay re-creates it, so it need not wait."""
    rt = make_runtime()
    rt._manage_reserve_window()
    rt._snapshot("reserve_window")
    handle = decision(rt)
    brief = rt.working_state.put("seed-decider", {"v": "brief"}, handle=handle)["sha"]
    rt.working_state.put("seed-decider", {"v": "kept"}, handle=handle)
    assert rt.artifacts.index[brief]["released"] == "sealed"
    _next_window(rt)
    assert brief not in rt.artifacts.index


def test_a_seat_reading_its_own_released_head_is_told_it_was_released():
    rt = make_runtime()
    rt._manage_reserve_window()
    handle = decision(rt)
    old = rt.working_state.put("seed-decider", {"v": 1}, handle=handle)["sha"]
    rt.working_state.put("seed-decider", {"v": 2}, handle=handle)
    own = rt._run_tool("seed-decider", handle, {"tool": "artifact.get", "args": {"sha": old}})
    assert own[0] == {"sha": old, "error": "artifact_released"}
    assert ledger_items(rt, "artifact.get")[-1]["reason"] == "artifact_released"
    other = rt.artifacts.read(old, reader="seed-observer")
    assert other == {"sha": old, "error": "artifact_private"}


def test_an_artifact_list_cursor_outlives_the_row_it_named():
    rt = make_runtime()
    rt._manage_reserve_window()
    rt.DIRECTORY_PAGE = 1
    for n in range(3):
        rt.clock.now_ns += 1
        rt.artifacts.put(canonical({"n": n}), owner="seed-decider", kind="outcome.item")
    first = rt._artifact_page("seed-decider", {})
    sha = first["items"][0]["sha"]
    rt.artifacts.release(sha, owner="seed-decider", kind="outcome.item")
    second = rt._artifact_page("seed-decider", {"cursor": first["next_cursor"]})
    assert second["items"] and second["items"][0]["sha"] != sha
    assert "cursor_unknown" not in second
    lost = rt._artifact_page("seed-decider", {"cursor": "f" * 64})
    assert lost["items"] == [] and lost["cursor_unknown"] is True


def test_a_program_seat_keeps_one_private_state():
    store, _ = _store()
    first = store.put(b'{"s":1}', owner="prog", kind="program.state")
    second = store.put(b'{"s":2}', owner="prog", kind="program.state")
    assert store.release(first, owner="prog", kind="program.state") is True
    assert store.release(second, owner="prog", kind="working.state") is False
    assert store.references(second, store.index[second]) == {
        "prog": {"kind": "program.state", "ts": 1}}


def test_re_writing_released_bytes_is_the_new_writer_s_alone():
    """A -> B -> A, by the same seat and by another: the record is re-owned, its owner
    and kind are the new writer's, and it is never collected while held."""
    store, _ = _store()
    a = store.put(b"A", owner="alice", kind="working.state")
    store.put(b"B", owner="alice", kind="working.state")
    store.release(a, owner="alice", kind="working.state")
    assert store.index[a]["released"] == "sealed"
    assert store.put(b"A", owner="alice", kind="working.state") == a
    assert "released" not in store.index[a] and a not in store.released_recent.get("alice", [])
    assert store.collect() == [] and store.get(a) == b"A"
    # Released by alice, then written by bob: bob sees his own reference alone.
    store.release(a, owner="alice", kind="working.state")
    store.put(b"A", owner="bob", kind="program.state")
    assert store.read(a, reader="bob") == {"sha": a, "kind": "program.state", "bytes": 1,
                                           "text": "A"}
    assert "alice" not in str(store.index[a]) and store.owner_for(a) == "bob"
    # Alice is told what happened to her own bytes, and nothing about who holds them.
    assert store.read(a, reader="alice") == {"sha": a, "error": "artifact_released"}
    assert store.visible_to(a, "bob-child", lambda seat: "bob")  # bob's lineage reads it


def test_a_co_holder_sees_nothing_of_the_other_holder_and_no_owner_flip():
    """Two seats writing the same bytes (``{}``): neither view names an owner or the
    other holder, and one letting go (the owner-of-record handoff) changes nothing
    the other can read."""
    store, _ = _store()
    sha = store.put(b"{}", owner="alice", kind="working.state")
    store.put(b"{}", owner="bob", kind="outcome.item")
    before = store.read(sha, reader="bob")
    assert before == {"sha": sha, "kind": "outcome.item", "bytes": 2, "text": "{}"}
    store.release(sha, owner="alice", kind="working.state")
    assert store.index[sha]["owner"] == "bob"  # kernel bookkeeping only
    assert store.read(sha, reader="bob") == before
    store.put(b"{}", owner="alice", kind="working.state")
    assert store.read(sha, reader="bob") == before
    assert store.read(sha, reader="alice") == {"sha": sha, "kind": "working.state",
                                               "bytes": 2, "text": "{}"}


def test_a_non_holder_gets_the_same_answer_whoever_else_holds_the_bytes(tmp_path):
    """No existence oracle: a seat with no reference to a hash gets one answer, byte
    for byte, whether nobody holds it, someone holds it, someone released it while
    another still holds it, it was collected, or leftover bytes sit on the disk."""
    import hashlib

    blob = b'"probe"'
    sha = hashlib.sha256(blob).hexdigest()
    answers = []
    store, _ = _store(tmp_path / "nobody")
    answers.append(store.read(sha, reader="carol"))
    store, _ = _store(tmp_path / "held")
    store.put(blob, owner="dave", kind="working.state")
    answers.append(store.read(sha, reader="carol"))
    store, _ = _store(tmp_path / "released-while-held")
    store.put(blob, owner="erin", kind="working.state")
    store.put(blob, owner="dave", kind="working.state")
    store.release(sha, owner="erin", kind="working.state")
    answers.append(store.read(sha, reader="carol"))
    store, _ = _store(tmp_path / "collected")
    store.put(blob, owner="erin", kind="working.state")
    store.release(sha, owner="erin", kind="working.state")
    store.collect()
    answers.append(store.read(sha, reader="carol"))
    store, _ = _store(tmp_path / "leftover")
    store._write(sha, blob)
    answers.append(store.read(sha, reader="carol"))
    assert answers == [{"sha": sha, "error": "artifact_private"}] * 5
    # A seat that released it is told so, whether or not another seat still holds it.
    for others in (False, True):
        store, _ = _store(tmp_path / f"own-{others}")
        store.put(blob, owner="carol", kind="working.state")
        if others:
            store.put(blob, owner="dave", kind="working.state")
        store.release(sha, owner="carol", kind="working.state")
        store.seal_released()
        store.collect()
        assert store.read(sha, reader="carol") == {"sha": sha, "error": "artifact_released"}


def test_a_program_s_lineage_reads_only_state_its_lineage_holds():
    lineage = {"prog": "L", "sib": "L", "bob": "M"}.get
    store, _ = _store()
    old = store.put(b'{"v":1}', owner="prog", kind="program.state")
    new = store.put(b'{"v":2}', owner="prog", kind="program.state")
    store.release(old, owner="prog", kind="program.state")
    assert store.read(new, reader="sib", lineage_of=lineage)["text"] == '{"v":2}'
    assert store.read(old, reader="sib", lineage_of=lineage)["error"] == "artifact_private"
    assert store.read(old, reader="prog", lineage_of=lineage)["error"] == "artifact_released"
    # Another lineage re-writes the released bytes: still not the program lineage's.
    store.put(b'{"v":1}', owner="bob", kind="working.state")
    assert store.read(old, reader="sib", lineage_of=lineage)["error"] == "artifact_private"
    # The owner of record keeps bytes under another kind only: the record's kind
    # follows what it holds, and the lineage no longer reads them as program state.
    both = store.put(b"{}", owner="prog", kind="program.state")
    store.put(b"{}", owner="prog", kind="working.state")
    store.release(both, owner="prog", kind="program.state")
    assert store.index[both]["kind"] == "working.state"
    assert store.read(both, reader="sib", lineage_of=lineage)["error"] == "artifact_private"


def test_a_seat_s_own_releases_survive_a_checkpoint():
    from factorylab.runtime.resume import restore_runtime, runtime_state

    rt = make_runtime()
    rt._manage_reserve_window()
    handle = decision(rt)
    old = rt.working_state.put("seed-decider", {"v": 1}, handle=handle)["sha"]
    rt.working_state.put("seed-decider", {"v": 2}, handle=handle)
    rt2 = make_runtime()
    restore_runtime(rt2, runtime_state(rt))
    assert rt2.artifacts.read(old, reader="seed-decider")["error"] == "artifact_released"


def test_collect_returns_only_what_it_removed_and_ledgers_the_same_either_way(tmp_path):
    """An unlink that fails still takes the record out of the index and ledgers it,
    exactly as a replay whose unlink succeeds would; the bytes stay a leftover and the
    hash is not returned."""
    store, ledger = _store(tmp_path)
    sha = store.put(b"x", owner="a", kind="working.state")
    store.release(sha, owner="a", kind="working.state")
    store._remove_bytes = lambda _sha: False
    assert store.collect() == [] and sha not in store.index
    assert [i["sha"] for i in _kinds(ledger, "artifact.collected")] == [sha]
    del store._remove_bytes
    assert store.collect() == [] and (tmp_path / "w.artifacts" / sha).exists() is False


def test_release_refuses_a_non_owner_a_wrong_kind_and_an_unknown_hash():
    store, ledger = _store()
    sha = store.put(b"alice's", owner="alice", kind="working.state")
    before = {k: dict(v) for k, v in store.index.items()}
    assert store.release(sha, owner="mallory", kind="working.state") is False
    assert store.release(sha, owner="alice", kind="outcome.item") is False
    assert store.release("0" * 64, owner="alice", kind="working.state") is False
    with pytest.raises(ValueError):
        store.release("not a hash", owner="alice", kind="working.state")
    assert store.index == before and _kinds(ledger, "artifact.released") == []


def test_a_release_is_ledgered_before_the_index_changes():
    store, ledger = _store()
    sha = store.put(b"x", owner="alice", kind="working.state")
    seen = []
    append = ledger.append

    def watch(item):
        if item["kind"] == "artifact.released":
            seen.append(dict(store.index[sha]["refs"]))
        return append(item)

    ledger.append = watch
    store.release(sha, owner="alice", kind="working.state")
    assert seen == [{"alice": {"kind": "working.state", "ts": 1}}]


def test_a_resume_seals_what_the_recorded_run_sealed_and_skips_collected_bytes(tmp_path):
    """The checkpoint's released records may already be gone from disk; the restore
    does not refuse for them, and it seals them as the recording did."""
    store, ledger = _store(tmp_path)
    old = store.put(b'{"v":1}', owner="s", kind="working.state")
    store.seal_released()  # a checkpoint names ``old``
    new = store.put(b'{"v":2}', owner="s", kind="working.state")
    store.release(old, owner="s", kind="working.state")
    index = {sha: dict(record) for sha, record in store.index.items()}  # the next one
    assert store.collect() == []  # pending: the checkpoint still names it
    store.seal_released()
    assert store.collect() == [old]
    _check_artifacts(store, index=index, assemblies=[], heads={"s": {"sha": new}},
                     outcomes={})
    store.index = index  # what a restore assigns
    assert store.seal_released() == 1
    assert store.collect() == [old]  # replayed: ledgered again, exactly as recorded
    assert [i["sha"] for i in _kinds(ledger, "artifact.collected")] == [old, old]
    assert new in store.index and store.get(new)




# --- retirement keeps an id's state; a finite disk bounds the whole of it ---------------


def _register_program(rt, seat_id):
    from factorylab.cortex.request import Return

    handle = decision(rt)
    rt._apply_registrations(handle, Return(handle, {"register": [{
        "kind": "assembly", "id": seat_id, "model_id": "program", "accepts": ["Tick"],
        "code": "print('{}')", "state_policy": "private"}]}, 0, "ok"))
    assert seat_id in rt.assemblies, ledger_items(rt, "registration.rejected")
    assert seat_id not in rt.retired_assemblies
    return handle


def _write_both(rt, seat, obj):
    """A head through the kernel's own path, and a program state as a call prints one."""
    rt.working_state.put(seat, obj, handle=decision(rt, seat))
    asm = rt.assemblies[seat]
    asm.state_sha = rt.artifacts.put(canonical({"program": obj}), owner=seat,
                                     kind="program.state", supersedes=asm.state_sha)


def _private_bytes(rt):
    """Retained private state, recounted from the index: every reference held as a
    working-state head or a program private state, each holder's at its full size."""
    return sum(record["bytes"] for sha, record in rt.artifacts.index.items()
               for ref in rt.artifacts.references(sha, record).values()
               for k in ref.get("kinds", [ref["kind"]])
               if k in ("working.state", "program.state"))


def _register_by(rt, proposer, seat_id, *, admitted=True):
    from factorylab.cortex.request import Return

    handle = decision(rt, proposer)
    rt._apply_registrations(handle, Return(handle, {"register": [{
        "kind": "assembly", "id": seat_id, "model_id": "program", "accepts": ["Tick"],
        "code": "print('{}')", "state_policy": "private"}]}, 0, "ok"))
    if admitted:
        assert seat_id in rt.assemblies and seat_id not in rt.retired_assemblies, (
            ledger_items(rt, "registration.rejected"))
    return handle


def test_a_retired_program_registered_again_by_its_owner_keeps_its_head_only():
    """Retirement is final for a version, not for an id: the next version registered by
    the seat that registered the last one keeps the id's head (its memory), and starts
    with no program state, since new code cannot be assumed to read the old code's; the
    old version's state is released at the re-registration, journaled ``superseded``,
    and holds no capacity."""
    rt = make_runtime()
    rt._manage_reserve_window()
    _register_program(rt, "prog-a")  # registered by seed-decider
    assert rt.registrants["prog-a"] == rt.lineage_keys["seed-decider"]
    _write_both(rt, "prog-a", {"lesson": "keep"})
    head = rt.working_state.head("prog-a")
    state = rt.assemblies["prog-a"].state_sha
    first = rt.assemblies["prog-a"]
    rt._retire_assembly("prog-a", "vote-1")
    for _ in range(2):
        _boundary(rt)
    # Retirement released nothing: the id keeps both until it takes a next version.
    assert not [i for i in ledger_items(rt, "artifact.released") if i["owner"] == "prog-a"]
    kept = rt.artifacts.private_bytes()
    _register_program(rt, "prog-a")  # its owner, seed-decider, again
    second = rt.assemblies["prog-a"]
    assert second is not first and second.spec.version == first.spec.version + 1
    assert rt.working_state.head("prog-a") == head
    assert second.state_sha is None
    released = [i for i in ledger_items(rt, "artifact.released") if i["owner"] == "prog-a"]
    assert [(i["sha"], i["artifact_kind"], i["cause"]) for i in released] == [
        (state, "program.state", "superseded")]
    assert rt.artifacts.private_holdings("prog-a") == [(head["sha"], "working.state")]
    assert rt.artifacts.private_bytes() == kept - len(canonical({"program": {"lesson": "keep"}}))
    assert rt.artifacts.read(state, reader="prog-a") == {"sha": state,
                                                         "error": "artifact_released"}
    assert "prog-a" not in rt.retirement_order


def test_a_retired_id_takes_its_next_version_only_from_its_owner():
    """Codex P1 on d460d55: every record keyed by an id (its head, inbox, archived
    rationales, artifacts) is its lineage's private state, so no other lineage may
    ever hold the id. A non-owner's registration of a retired id is refused at
    admission, for a public reason, and changes nothing: the id stays retired, its
    key and its state as they were."""
    rt = make_runtime()
    rt._manage_reserve_window()
    _register_program(rt, "prog-b")  # registered by seed-decider
    _write_both(rt, "prog-b", {"lesson": "mine"})
    item = rt.outcomes.append("prog-b", handle=decision(rt, "prog-b"),
                              outcome={"kind": "fill"}, evidence="e-b")
    head, key = rt.working_state.head("prog-b"), rt.lineage_keys["prog-b"]
    rt._retire_assembly("prog-b", "vote-1")
    version = rt.assemblies["prog-b"].spec.version
    handle = _register_by(rt, "seed-observer", "prog-b", admitted=False)
    [rejected] = [i for i in ledger_items(rt, "registration.rejected")
                  if i["handle"] == handle]
    assert rejected["reason"].endswith(
        "a retired id takes its next version only from its owner")
    assert "prog-b" in rt.retired_assemblies
    assert rt.assemblies["prog-b"].spec.version == version
    assert rt.working_state.head("prog-b") == head and rt.lineage_keys["prog-b"] == key
    assert rt.outcomes.body(item["sha"])["outcome"] == {"kind": "fill"}
    assert not [i for i in ledger_items(rt, "artifact.released")
                if i["owner"] == "prog-b" and i.get("cause") == "superseded"]
    # A seed has no registrant: only the seed itself owns its id.
    assert "seed-observer" not in rt.registrants


def test_a_retired_version_writes_no_state():
    """A return pending across its seat's retirement may settle, but its working-state
    write is refused (``retired``), so a retired id never gains state after retiring."""
    rt = make_runtime()
    rt._manage_reserve_window()
    handle = decision(rt, "seed-observer")
    rt.working_state.put("seed-observer", {"v": 1}, handle=handle)
    head = rt.working_state.head("seed-observer")
    rt._retire_assembly("seed-observer", "vote-1")
    assert rt._write_working_state("seed-observer", handle,
                                   {"working_state": {"v": 2}}) is False
    refused = ledger_items(rt, "state.refused")[-1]
    assert (refused["assembly_id"], refused["reason"]) == ("seed-observer", "retired")
    assert rt.working_state.head("seed-observer") == head
    assert rt._state_write_refusal("seed-observer") == "retired"
    _register_program(rt, "prog-v")
    assert rt.assemblies["prog-v"].state_gate() is None
    rt._retire_assembly("prog-v", "vote-2")
    assert rt.assemblies["prog-v"].state_gate() == "retired"


def test_retired_state_is_released_oldest_first_and_retained_stays_under_the_cap():
    """Register, write, retire with fresh ids past the cap: retained private state never
    exceeds it, the state of the oldest retirement is released first (journaled, cause
    ``capacity``), the latest retired ids keep theirs, and live seats are untouched."""
    rt = make_runtime()
    rt._manage_reserve_window()
    rt._snapshot("reserve_window")
    live_head = rt.working_state.put("seed-decider", {"live": True}, handle=decision(rt))
    live = {seat: rt.working_state.head(seat) for seat in rt.working_state.heads}
    pad = "x" * 20_000
    pair = len(canonical({"n": 0, "pad": pad})) + len(canonical({"program": {"n": 0,
                                                                            "pad": pad}}))
    cap = rt.artifacts.private_bytes() + 3 * pair
    rt.artifacts.private_cap = cap
    for n in range(8):
        seat = f"prog-{n}"
        _register_program(rt, seat)
        _write_both(rt, seat, {"n": n, "pad": pad})
        rt._retire_assembly(seat, f"vote-{n}")
        assert rt.artifacts.private_bytes() == _private_bytes(rt) <= cap
        if n % 2:
            _boundary(rt)
    released = [i for i in ledger_items(rt, "artifact.released") if i.get("cause")]
    assert {i["cause"] for i in released} == {"capacity"}
    assert list(dict.fromkeys(i["owner"] for i in released)) == [f"prog-{n}" for n in range(5)]
    assert {i["artifact_kind"] for i in released} == {"working.state", "program.state"}
    for n in range(5):
        assert rt.working_state.head(f"prog-{n}") is None
        assert rt.assemblies[f"prog-{n}"].state_sha is None
        assert not rt.artifacts.private_holdings(f"prog-{n}")
        assert f"prog-{n}" not in rt.retirement_order
    for n in range(5, 8):
        assert rt.working_state.head(f"prog-{n}") is not None
        assert rt.assemblies[f"prog-{n}"].state_sha is not None
    for seat, head in live.items():
        assert rt.working_state.head(seat) == head
        assert (head["sha"], "working.state") in rt.artifacts.private_holdings(seat)
    assert rt.working_state.head("seed-decider") == live_head
    # Its owner re-registers a kept id: the head passes on, the program state never does.
    _register_program(rt, "prog-7")
    assert rt.working_state.head("prog-7") is not None
    assert rt.assemblies["prog-7"].state_sha is None
    assert [i["cause"] for i in ledger_items(rt, "artifact.released")
            if i["owner"] == "prog-7"] == ["superseded"]
    _register_program(rt, "prog-0")
    assert rt.working_state.head("prog-0") is None
    assert rt.assemblies["prog-0"].state_sha is None


def test_a_live_write_that_cannot_fit_is_refused_as_on_a_full_disk():
    """No retired state to release: a live seat's write that would pass the cap is
    refused with the one capacity fact, ledgered, and changes nothing; a write that
    replaces the seat's own head at the cap is measured with that head gone."""
    rt = make_runtime()
    rt._manage_reserve_window()
    handle = decision(rt)
    rt.working_state.put("seed-decider", {"n": 1}, handle=handle)
    rt.artifacts.private_cap = rt.artifacts.private_bytes()
    before = rt.working_state.head("seed-decider")
    puts = len(ledger_items(rt, "artifact.put"))
    assert rt._write_working_state("seed-decider", handle, {"working_state": {"n": 12345}}) \
        is False
    refused = ledger_items(rt, "state.refused")[-1]
    assert refused["assembly_id"] == "seed-decider"
    assert refused["reason"] == CAPACITY_REFUSAL
    assert rt.working_state.head("seed-decider") == before
    assert len(ledger_items(rt, "artifact.put")) == puts
    assert rt.artifacts.private_bytes() <= rt.artifacts.private_cap
    # The same size again replaces the seat's own head: nothing is refused.
    assert rt._write_working_state("seed-decider", handle, {"working_state": {"n": 2}})
    assert rt.working_state.head("seed-decider")["sha"] != before["sha"]
    assert rt.artifacts.private_bytes() <= rt.artifacts.private_cap


def test_the_capacity_answer_says_nothing_about_another_seat_s_bytes():
    """Rule 5: at the cap, a seat writing exactly another seat's current bytes and a
    seat writing a wrong guess of the same size get the same answer, and neither is
    told any total. Every holder's reference counts in full, so identical bytes earn
    no credit."""
    rt = make_runtime()
    rt._manage_reserve_window()
    rt.working_state.put("seed-decider", {"secret": 4721}, handle=decision(rt))
    rt.artifacts.private_cap = rt.artifacts.private_bytes()
    answers = []
    for guess in ({"secret": 4721}, {"secret": 1111}):
        with pytest.raises(ArtifactCapacityError) as refused:
            rt.working_state.put("seed-observer", guess, handle=decision(rt, "seed-observer"))
        answers.append(str(refused.value))
    assert answers == [CAPACITY_REFUSAL, CAPACITY_REFUSAL]
    assert not any(ch.isdigit() for ch in CAPACITY_REFUSAL)
    assert not rt.artifacts.private_holdings("seed-observer")


def test_the_archive_never_holds_more_private_state_than_its_cap(tmp_path):
    """The kernel invariant, attempted on a bare archive: a private write past the cap
    releases reclaimable references only when that makes it fit, and only until it
    does; without enough, it is refused having released and written nothing; bytes of
    any other kind are not private state and pass."""
    store, ledger = _store(tmp_path)
    store.private_cap = 100
    retired = []
    reclaimed = []
    store.reclaimable = lambda: list(retired)
    store.on_reclaimed = lambda owner, sha, kind: reclaimed.append((owner, kind))
    old = store.put(b"r" * 10, owner="r", kind="working.state")
    live = store.put(b"l" * 60, owner="l", kind="working.state")
    retired.append("r")
    assert store.private_bytes() == 70
    # 50 more at 70/100 needs 20 freed; the retired seat holds 10: refused, and its
    # state stays exactly where it was.
    with pytest.raises(ArtifactCapacityError, match="at the world's capacity"):
        store.put(b"w" * 50, owner="w", kind="program.state")
    assert not reclaimed and store.private_holdings("r") == [(old, "working.state")]
    assert not (store.root / hashlib.sha256(b"w" * 50).hexdigest()).exists()
    assert [i["sha"] for i in _kinds(ledger, "artifact.put")] == [old, live]
    assert not _kinds(ledger, "artifact.released")
    store.put(b"c" * 500, owner="t", kind="outcome")
    assert store.private_bytes() == 70
    # 35 more at 70/100 needs 5 freed; the retired reference (10) makes room and goes.
    store.put(b"w" * 35, owner="w", kind="program.state")
    assert reclaimed == [("r", "working.state")]
    assert store.private_bytes() == 95 <= store.private_cap
    assert _kinds(ledger, "artifact.released")[-1]["cause"] == "capacity"


def test_eviction_stops_as_soon_as_the_write_fits(tmp_path):
    store, _ledger = _store(tmp_path)
    store.private_cap = 100
    order = ["r1", "r2", "r3"]
    store.reclaimable = lambda: list(order)
    for owner in order:
        store.put(owner.encode() * 10, owner=owner, kind="working.state")  # 20 each
    store.put(b"l" * 40, owner="l", kind="working.state")
    assert store.private_bytes() == 100
    store.put(b"w" * 30, owner="w", kind="working.state")  # needs 30: r1 and r2 go
    assert store.private_holdings("r1") == store.private_holdings("r2") == []
    assert store.private_holdings("r3") and store.private_bytes() == 90


def test_a_live_seat_s_state_is_never_released_for_room():
    """Only retired ids are released for room: with none retired, a full cap refuses
    the write and every live head stays held."""
    rt = make_runtime()
    rt._manage_reserve_window()
    rt.working_state.put("seed-decider", {"n": 1}, handle=decision(rt))
    heads = dict(rt.working_state.heads)
    rt.artifacts.private_cap = rt.artifacts.private_bytes()
    assert rt._reclaimable_state() == []
    assert not rt._write_working_state("seed-observer", decision(rt, "seed-observer"),
                                       {"working_state": {"big": "x" * 1000}})
    assert rt.working_state.heads == heads
    assert not [i for i in ledger_items(rt, "artifact.released") if i.get("cause")]


# --- [storage] retained_private_bytes: a limit, validated at load ------------------------


def test_the_cap_defaults_to_64_mib_and_is_published_as_a_limit():
    rt = make_runtime()
    assert rt.m.storage.retained_private_bytes == DEFAULT_RETAINED_PRIVATE_BYTES == 64 << 20
    assert rt.artifacts.private_cap == DEFAULT_RETAINED_PRIVATE_BYTES
    storage = rt._world_block()["storage"]
    assert storage["retained_private_bytes"] == DEFAULT_RETAINED_PRIVATE_BYTES
    assert "at most retained_private_bytes, always" in storage["retention"]
    assert "kept until capacity is needed" in storage["retention"]
    assert "oldest retirement first" in storage["retention"]
    assert '"retained_private_bytes":67108864' in rt.m.canonical_json()


def test_a_cap_below_the_seeded_seats_is_refused():
    per_seat = HARD_STATE_BYTES + MAX_PROGRAM_STATE_BYTES
    with pytest.raises(ValueError, match="cannot hold the 1 seeded seats"):
        manifest_from_dict(_world(storage={"retained_private_bytes": per_seat - 1}))
    assert manifest_from_dict(_world(storage={"retained_private_bytes": per_seat}))


@pytest.mark.parametrize("bad", [0, -1, "67108864", True, 1.5])
def test_a_cap_that_is_not_a_positive_integer_is_refused(bad):
    with pytest.raises(ValueError, match="retained_private_bytes must be a positive integer"):
        manifest_from_dict(_world(storage={"retained_private_bytes": bad}))


def test_a_cap_over_half_the_free_disk_is_refused_at_genesis(monkeypatch):
    """The free disk is a genesis admission: a manifest loads whatever the host's disk,
    and a new world is refused before anything is written."""
    free = 100 << 20
    monkeypatch.setattr(worlds.shutil, "disk_usage",
                        lambda path: SimpleNamespace(total=free, used=0, free=free))
    big = manifest_from_dict(_world(storage={"retained_private_bytes": (50 << 20) + 1}))
    assert big.host_disk_problem().startswith(
        f"storage.retained_private_bytes = {(50 << 20) + 1} exceeds 1/2 of the host's free disk")
    fits = manifest_from_dict(_world(storage={"retained_private_bytes": 50 << 20}))
    assert fits.host_disk_problem() is None
    scripted = load_manifest("scripted")  # 64 MiB, over half of this host's 100 MiB
    with pytest.raises(ValueError, match=r"exceeds 1/2 of the host's free disk"):
        Runtime(scripted, events=0, seed=1, initial_balance_micro=None, ledger_path=None,
                router_gamma=.1)
    admitted = replace(scripted, storage=StorageSpec(50 << 20))
    assert Runtime(admitted, events=0, seed=1, initial_balance_micro=None, ledger_path=None,
                   router_gamma=.1).artifacts.private_cap == 50 << 20


def test_a_storage_price_or_an_unknown_storage_key_is_refused():
    with pytest.raises(ValueError, match=r"\[storage\] was removed"):
        manifest_from_dict(_world(storage={"retained_private_bytes": 1 << 20,
                                           "micro_per_byte_day": "0"}))
    with pytest.raises(ValueError, match="unknown storage manifest key"):
        manifest_from_dict(_world(storage={"max_bytes": 1 << 20}))


def _fund(rt, seat):
    """Give a registered seat enough of its own entitlement to register a child."""
    rt.budget.transfer("seed-decider", seat, 20 * rt.ev.trial_amount_micro, "test:fund")


def test_no_path_lets_a_different_lineage_hold_a_retired_id():
    """The review's parent/child scenario (cold #1). seed-decider registers ``parent``;
    ``parent`` registers ``child``; both retire. Neither seed-observer nor seed-decider
    (not ``child``'s registrant) may take ``child``, and seed-observer may not take
    ``parent``, so no other lineage ever holds either id; the owners may."""
    rt = make_runtime()
    rt._manage_reserve_window()
    _register_by(rt, "seed-decider", "parent")
    parent_key = rt.lineage_keys["parent"]
    _fund(rt, "parent")
    _register_by(rt, "parent", "child")
    assert rt.registrants["child"] == parent_key
    rt.working_state.put("child", {"secret": "child's own"}, handle=decision(rt, "child"))
    head = rt.working_state.head("child")
    rt._retire_assembly("child", "vote-1")
    rt._retire_assembly("parent", "vote-2")
    for proposer, seat_id in (("seed-observer", "parent"), ("seed-observer", "child"),
                              ("seed-decider", "child")):
        _register_by(rt, proposer, seat_id, admitted=False)
        assert seat_id in rt.retired_assemblies
    assert rt.lineage_keys["parent"] == parent_key
    assert rt.working_state.head("child") == head
    # The owners may: seed-decider re-versions parent, which re-versions child.
    _register_by(rt, "seed-decider", "parent")
    assert rt.lineage_keys["parent"] == parent_key
    _fund(rt, "parent")
    _register_by(rt, "parent", "child")
    assert rt.working_state.head("child") == head


def test_the_owner_re_versioning_keeps_its_key_head_and_outcomes():
    rt = make_runtime()
    rt._manage_reserve_window()
    _register_by(rt, "seed-decider", "prog-k")
    key = rt.lineage_keys["prog-k"]
    handle = decision(rt, "prog-k")
    rt.working_state.put("prog-k", {"kept": 1}, handle=handle)
    item = rt.outcomes.append("prog-k", handle=handle, outcome={"kind": "fill"},
                              evidence="e-k")
    head = rt.working_state.head("prog-k")
    rt._retire_assembly("prog-k", "vote-1")
    _register_by(rt, "seed-decider", "prog-k")
    assert rt.lineage_keys["prog-k"] == key
    assert rt.working_state.head("prog-k") == head
    assert rt.outcomes.get("prog-k", f"outcome:{item['seq']}")["outcome"] == {"kind": "fill"}


def test_a_program_state_refusal_is_ledgered_with_its_time():
    rt = make_runtime()
    rt._manage_reserve_window()
    rt._record_program({"kind": "state.refused", "assembly_id": "p", "handle": "h",
                        "state_kind": "program.state", "reason": "retired"})
    assert ledger_items(rt, "state.refused")[-1]["ts"] == rt.clock.now_ns
