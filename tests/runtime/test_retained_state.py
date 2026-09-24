"""Retained state is bounded in total, not per version (PR #146, P1; essay II.II.b).

The disk is the world's own and pays no one, so retained bytes are a constraint:
the kernel keeps one working-state head per seat (and one private state per
program seat), releases the reference a superseded one held, and collects its
bytes once no checkpoint a resume could start from names them. Nothing a seat or
the settlement machinery still references is ever collected, and nothing about a
released record's former writer survives into what a later writer is shown.
"""

import pytest

from factorylab.kernel.artifacts import ArtifactStore, artifact_root
from factorylab.kernel.ledger import Ledger
from factorylab.runtime.continuity import HARD_STATE_BYTES, canonical
from factorylab.runtime.resume import _check_artifacts
from tests.conftest import make_runtime
from tests.runtime.test_connectors import decision, ledger_items

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


def _register_program(rt, seat_id):
    from factorylab.cortex.request import Return

    handle = decision(rt)
    rt._apply_registrations(handle, Return(handle, {"register": [{
        "kind": "assembly", "id": seat_id, "model_id": "program", "accepts": ["Tick"],
        "code": "print('{}')", "state_policy": "private"}]}, 0, "ok"))
    assert seat_id in rt.assemblies, ledger_items(rt, "registration.rejected")
    return handle


def _private_bytes(rt):
    """What the archive holds as somebody's private state: heads and program states."""
    return sum(record["bytes"] for sha, record in rt.artifacts.index.items()
               if rt.artifacts.references(sha, record)
               and {ref.get("kind") for ref in rt.artifacts.references(sha, record).values()}
               & {"working.state", "program.state"})


def test_retiring_seats_releases_their_private_state_so_it_stays_bounded():
    """Retirement is final, so a retired seat's head and program state reach no one and
    are released: register, write both, retire, N times, and the private bytes the
    archive holds stay within the live seats times the per-seat cap."""
    rt = make_runtime()
    rt._manage_reserve_window()
    rt._snapshot("reserve_window")
    blob = "x" * (HARD_STATE_BYTES - 64)
    for n in range(8):
        seat = f"prog-{n}"
        _register_program(rt, seat)
        rt.working_state.put(seat, {"n": n, "pad": blob}, handle=decision(rt, seat))
        rt.assemblies[seat].state_sha = rt.artifacts.put(
            canonical({"n": n, "pad": blob}), owner=seat, kind="program.state")
        rt._retire_assembly(seat, f"vote-{n}")
        assert rt.working_state.head(seat) is None and rt.assemblies[seat].state_sha is None
        _boundary(rt)
    live = len(rt._live_seats())
    assert _private_bytes(rt) <= live * 2 * HARD_STATE_BYTES
    assert _private_bytes(rt) < 2 * HARD_STATE_BYTES  # nothing of the retired seats
    released = [i for i in ledger_items(rt, "artifact.released") if i["owner"] == "prog-0"]
    assert {i["artifact_kind"] for i in released} == {"working.state", "program.state"}


def test_a_retired_seat_s_record_to_the_world_is_still_readable():
    """What a retired seat was told (its inbox bodies) and what it said (archived
    rationales) are the world's record and are not released."""
    rt = make_runtime()
    rt._manage_reserve_window()
    handle = decision(rt, "seed-observer")
    rt.working_state.put("seed-observer", {"v": 1}, handle=handle)
    item = rt.outcomes.append("seed-observer", handle=handle, outcome={"kind": "fill"},
                              evidence="e-retired")
    rt._retire_assembly("seed-observer", "vote-1")
    for _ in range(3):
        _boundary(rt)
    assert rt.artifacts.get(item["sha"])
    assert rt.outcomes.body(item["sha"])["outcome"] == {"kind": "fill"}
    assert rt.working_state.head("seed-observer") is None
