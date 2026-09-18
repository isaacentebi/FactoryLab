"""Edition 3, R3-F: the acceptance for attention and continuity, completed.

The plan's acceptance for this workstream, verbatim (``docs/plans/edition3-r3.md``,
"R3-F Attention and continuity, completed", plus the three the task added):

* a refusal lands in the deciding seat's inbox with an id;
* an invocation failure leaves the fold offered and the next wake sees it;
* a second writer reads its own artifact;
* ``ack_through`` an old id leaves newer items unread;
* a declined commission is not malformed;
* a fill reaches the ordering seat's inbox;
* garbage collection never removes an owned or public blob.

Nothing here reaches a network, a credential or a venue beyond the fake.
"""

from __future__ import annotations

import hashlib
from types import SimpleNamespace

import pytest

from factorylab.kernel.artifacts import ArtifactStore
from factorylab.runtime.continuity import OutcomeInbox


class Evidence:
    """A ledger double; the same shape the third-reading regressions use."""

    def __init__(self) -> None:
        self.rows: list[dict] = []

    def append(self, row):
        self.rows.append(dict(row))
        return len(self.rows)

    def kinds(self, kind: str) -> list[dict]:
        return [row for row in self.rows if row.get("kind") == kind]


@pytest.fixture
def archive():
    ledger = Evidence()
    clock = SimpleNamespace(ns=0)
    return ledger, clock, ArtifactStore(ledger, root=None, clock_ns=lambda: clock.ns)


@pytest.fixture
def inbox(archive):
    ledger, clock, store = archive
    return OutcomeInbox(store, ledger, lambda: clock.ns)


# ---- garbage collection never removes an owned or public blob ---------------------------

def test_collection_removes_only_unreferenced_unpublished_blobs(archive):
    ledger, _, store = archive
    owned = store.put(b"a seat's working state", owner="alice", kind="working.state")
    published = store.put(b"a note the population shares", owner="bob", kind="note",
                          public=True)
    # An orphan: durable bytes whose reference never landed (a crash between the two).
    orphan_bytes = b"bytes that outlived their failed record"
    orphan = hashlib.sha256(orphan_bytes).hexdigest()
    store._write(orphan, orphan_bytes)
    # A published record every reference released is still public, and still kept.
    store.index[published]["refs"] = {}
    store.index[published]["readers"] = []

    assert store.collect() == [orphan]

    assert store.get(owned) == b"a seat's working state"
    assert store.get(published) == b"a note the population shares"
    assert orphan not in store._memory and orphan not in store.index
    assert [row["sha"] for row in ledger.kinds("artifact.collected")] == [orphan]
    # Collection is idempotent and takes nothing on a second pass.
    assert store.collect() == []


# ---- a fill reaches the ordering seat's inbox -------------------------------------------
