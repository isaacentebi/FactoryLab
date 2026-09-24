"""Retained state is bounded in total, not per version (PR #146, P1; essay II.II.b).

The disk is the world's own and pays no one, so retained bytes are a constraint:
the kernel keeps one working-state head per seat (and one private state per
program seat), releases the reference a superseded one held, and collects its
bytes only once no durable checkpoint names it. Nothing a seat or the settlement
machinery still references is ever collected.
"""

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


def test_a_new_near_limit_state_every_wake_keeps_retained_bytes_bounded():
    """A seat writing a different near-64 KiB state on every wake holds one head, and
    the archive stays within a constant of that however many wakes pass."""
    rt = make_runtime()
    rt._manage_reserve_window()
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
    # The head, the heads released since the last checkpoint, and those it sealed
    # that the next boundary collects: two windows' worth, never thirty.
    assert peak <= 7 * HARD_STATE_BYTES
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
    assert rt.outcomes.get("seed-decider", f"outcome:{item['seq']}")["outcome"] == {
        "kind": "fill"}


def test_a_superseded_head_is_kept_until_a_checkpoint_no_longer_names_it():
    """Collected only once sealed: a crash between a boundary's collection and its
    checkpoint resumes from a checkpoint that does not need the bytes."""
    rt = make_runtime()
    rt._manage_reserve_window()
    handle = decision(rt)
    old = rt.working_state.put("seed-decider", {"v": 1}, handle=handle)["sha"]
    rt.working_state.put("seed-decider", {"v": 2}, handle=handle)
    assert rt.artifacts.index[old]["released"] == "pending"
    assert rt.artifacts.read(old, reader="seed-decider")["error"] == "artifact_private"
    _next_window(rt)  # this boundary collects before its checkpoint is written
    assert old in rt.artifacts.index and rt.artifacts.get(old)
    rt._snapshot("reserve_window")
    assert rt.artifacts.index[old]["released"] == "sealed"
    _next_window(rt)
    assert old not in rt.artifacts.index
    assert ledger_items(rt, "artifact.collected")[-1]["sha"] == old


def test_a_program_seat_keeps_one_private_state():
    rt = make_runtime()
    store = rt.artifacts
    first = store.put(b'{"s":1}', owner="prog", kind="program.state")
    second = store.put(b'{"s":2}', owner="prog", kind="program.state")
    assert store.release(first, owner="prog", kind="program.state") is True
    assert store.release(second, owner="prog", kind="working.state") is False
    assert store.references(second, store.index[second]) == {
        "prog": {"kind": "program.state", "ts": store.index[second]["ts"]}}


def test_a_resume_seals_what_the_recorded_run_sealed_and_skips_collected_bytes(tmp_path):
    """The checkpoint's released records may already be gone from disk; the restore
    does not refuse for them, and it seals them as the recording did."""
    ledger = Ledger(None, manifest={"name": "artifacts"})
    store = ArtifactStore(ledger, root=artifact_root(tmp_path / "w.jsonl"),
                          clock_ns=lambda: 1)
    old = store.put(b'{"v":1}', owner="s", kind="working.state")
    new = store.put(b'{"v":2}', owner="s", kind="working.state")
    store.release(old, owner="s", kind="working.state")
    index = {sha: dict(record) for sha, record in store.index.items()}  # the checkpoint
    assert store.collect() == []  # pending: the checkpoint may still be the old one
    store.seal_released()
    assert store.collect() == [old]
    _check_artifacts(store, index=index, assemblies=[], heads={"s": {"sha": new}},
                     outcomes={})
    store.index = index  # what a restore assigns
    assert store.seal_released() == 1
    assert store.collect() == [old]  # replayed: ledgered again, exactly as recorded
    assert [i["sha"] for i in ledger._recovery_items()
            if i["kind"] == "artifact.collected"] == [old, old]
    assert new in store.index and store.get(new)
