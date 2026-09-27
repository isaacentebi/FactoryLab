"""The artifact archive: content-addressed bytes the population keeps (contract C9).

An artifact is a byte string named by its SHA-256. The ledger holds the record
of every put — hash, owner, kind, size, time — and the bytes live beside the
ledger under ``runs/<world>.artifacts/<sha>``, so a diary reader can verify what
a seat kept without the bytes themselves passing through the chain. A world
without a ledger path (a test runtime) keeps the bytes in memory.

Guarantees: the bytes are durable before the ledger item names them, so a crash
between them leaves unreferenced bytes rather than an authenticated reference to
bytes that are not there (GPT-6 third reading, §3: "references can precede
durable bytes"); a put is idempotent
by content, so replay after a crash rewrites nothing; a read verifies the hash
it was asked for, so a tampered file is refused rather than served; retirement
of an owner is not the archive's business, and the first owner of a hash stays
its owner of record (``owner_for``). Holding bytes here costs no money: the
archive is the world's own disk, which pays no one.

**Ownership is a (sha, owner) reference (edition 3, R3-F).** One blob may carry
several references: a second writer of identical bytes owns its own reference,
with its own kind and its own moment, and can read what it wrote rather than
being told the first writer's bytes are private. The first reference stays the
owner of record (``owner_for``), so retirement has one subject. Nothing is
published: an artifact is its owners' private state (essay II.I.b), and ruling
R11 deleted the publication path nothing ever used.

**Nothing is deleted except an unreferenced blob.** ``collect()`` removes exactly
those — records whose every reference was released, each ledgered as
``artifact.collected``, and durable bytes no record names, which is what a crash
between ``_write`` and the ledger item leaves behind. Those leftovers are removed
without an item, and never while the journal is recovering: the diary never named
them, and a replay cannot know which leftovers a disk held, so ledgering them
would make a live run and its replay disagree. It is called by the runtime at a
reserve-window boundary. A blob any reference names is never a candidate
whatever its age.

**A reference can be released (Wave 11).** The disk is the world's own and pays
no one, so retained bytes are a constraint with a hard limit, not a price
(essay II.II.b): a seat keeps one working-state head and a program one private
state, and ``release`` drops the (sha, owner, kind) reference a superseded one
held. A reference remembers every kind its owner wrote the bytes under, so
releasing one kind never drops bytes the same owner still holds as another (an
inbox body, an archived rationale). A record whose last reference is released is
kept until no checkpoint a resume could start from names it. One the latest
checkpoint named (it was in that checkpoint's index) is ``pending`` until the next
durable checkpoint, then ``sealed`` (``seal_released``); one written and released
since that checkpoint is named by none, a replay re-creates it, and it is
``sealed`` at once. Only a sealed record is collected, so a resume never names
bytes that are gone.

A record whose every reference was released and that another seat then writes is
that seat's alone: its owner of record and kind are reset to the new writer, so
nothing shows it who wrote the bytes before (essay II.I.b, the author is private).

**Retained private state has a hard cap (Wave 11).** The disk is finite, so the
whole of what is held as some seat's private state (``PRIVATE_KINDS``: working-state
heads and program private states) is a hard limit, ``private_cap``, never a price
(essay II.II.b, the hard cast). It is counted per reference: every holder's
reference counts its full size, whether or not another seat holds identical bytes,
so what one seat is told about capacity never depends on what another seat wrote
(essay II.I.b; the disk may still keep one copy). A put that would take it over the
cap may release the references of retired seats that the store's owner names as
``reclaimable``, oldest retirement first, through ``release`` (ledgered before the
index changes), and only until the put fits; when even all of them would not make
room, nothing is released and the put is refused with ``ArtifactCapacityError``,
writing nothing, as on a full disk. A put naming the reference it ``supersedes`` is
measured with that reference gone, and releases it, so a seat replacing its state at
the cap is never refused for the state it replaces. The cap bounds the index: bytes
on disk can exceed it by the releases since the last checkpoint, until collection.
"""

from __future__ import annotations

import base64
import hashlib
import os
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

SHA_HEX_CHARS = 64
# What ``artifact.get`` returns inline: a program's private state or a seat's own
# text, never a page of the archive. Larger artifacts are readable by their
# owner's program, which receives its state on stdin rather than through a tool.
MAX_TOOL_READ_BYTES = 65_536
# The one thing a scoped-out reader is told (``Reason.ARTIFACT_PRIVATE``); the kernel
# keeps the literal so the archive never imports the runtime.
PRIVATE_REFUSAL = "artifact_private"
# What a reader is told of its own superseded state after it was released
# (``Reason.ARTIFACT_RELEASED``): the archive no longer keeps it for the reader.
RELEASED_REFUSAL = "artifact_released"
#: How many of its own most recent releases a reader is told about by name. Older
#: ones answer like any hash the reader holds no reference to. Bounded, so the
#: checkpoint does not grow with every head a seat ever wrote.
RELEASED_MEMORY = 8
#: The kinds that are a seat's private state; ``private_cap`` bounds their total.
PRIVATE_KINDS = frozenset({"working.state", "program.state"})


class ArtifactError(ValueError):
    """The archive refuses a malformed hash, unknown artifact or corrupted file."""


class ArtifactCapacityError(ArtifactError):
    """A private-state write that does not fit the retained private state cap."""


#: The one thing a private-state write the cap cannot hold is told: no totals, no
#: sizes, nothing about what any other seat holds (essay II.I.b).
CAPACITY_REFUSAL = "private state is at the world's capacity"


def artifact_root(ledger_path: str | os.PathLike[str]) -> Path:
    """The archive beside a ledger: ``runs/<world>.jsonl`` keeps ``runs/<world>.artifacts``."""
    return Path(ledger_path).with_suffix(".artifacts")


def _valid_sha(sha: Any) -> str:
    if (not isinstance(sha, str) or len(sha) != SHA_HEX_CHARS
            or any(c not in "0123456789abcdef" for c in sha)):
        raise ArtifactError("artifact sha must be 64 lowercase hex characters")
    return sha


class ArtifactStore:
    """Put, get and list artifacts; every put is a durable file before it is a ledger item."""

    def __init__(self, ledger: Any, *, root: str | os.PathLike[str] | None,
                 clock_ns: Callable[[], int]) -> None:
        self.ledger = ledger
        self.root = Path(root) if root is not None else None
        self.clock = clock_ns
        # sha -> {"owner", "kind", "bytes", "ts", "readers", "refs"}: the
        # checkpointed index. ``refs`` is the (sha, owner) ownership: one entry per
        # writer, each with the kind it wrote under and when.
        self.index: dict[str, dict[str, Any]] = {}
        self._memory: dict[str, bytes] = {}
        # The hashes the latest durable checkpoint's index held (``seal_released``):
        # derived, never checkpointed, and set identically by a live run and a resume.
        self.checkpointed: frozenset[str] = frozenset()
        # reader -> its own last ``RELEASED_MEMORY`` releases, oldest first. The
        # reader's own history, kept apart from the records so that what it is told
        # never depends on whether another seat holds the bytes or they were collected.
        self.released_recent: dict[str, list[str]] = {}
        # The hard cap on retained private state, in bytes (``[storage]
        # retained_private_bytes``). ``reclaimable`` names the owners whose private
        # references may be released for room, in the order to release them (retired
        # seats, oldest retirement first), and ``on_reclaimed(owner, sha, kind)`` is
        # told of each such release. Unset, a store has no cap (a bare store in a test).
        self.private_cap: int | None = None
        self.reclaimable: Callable[[], list[str]] | None = None
        self.on_reclaimed: Callable[[str, str, str], None] | None = None
        # (sha, holder, kind) -> bytes for every reference held under a private kind:
        # derived from the index, rebuilt whenever the index is replaced or edited
        # from outside.
        self._private: dict[tuple[str, str, str], int] = {}
        self._private_epoch: int | None = None

    # Change tracking, for views derived from the index (the runtime's directory
    # listing) that would otherwise re-read the whole archive on every request:
    # ``generation`` moves on every change; ``epoch`` moves when the index is
    # replaced or edited from outside, which invalidates every derived row; and
    # ``drain_changes`` names the hashes ``put``/``collect`` touched since it was
    # last called. It has one consumer, the runtime that owns this store.

    @property
    def index(self) -> dict[str, dict[str, Any]]:
        """The checkpointed index; ``generation`` says when it last changed."""
        return self._index

    @index.setter
    def index(self, value: dict[str, dict[str, Any]]) -> None:
        # A restore replaces the index whole; every derived view must be rebuilt.
        self._index = value
        self.touch()

    def touch(self) -> None:
        """Declare a change to ``index`` made outside ``put`` and ``collect``.

        Every view derived from the index is rebuilt from scratch afterwards.
        """
        self.generation = getattr(self, "generation", 0) + 1
        self.epoch = getattr(self, "epoch", 0) + 1
        self._changed: set[str] = set()

    def drain_changes(self) -> tuple[int, set[str]]:
        """Return ``(epoch, hashes put or collected since the last drain)`` and forget them."""
        changed, self._changed = self._changed, set()
        return self.epoch, changed

    def _changed_sha(self, sha: str) -> None:
        self.generation += 1
        self._changed.add(sha)

    def put(self, data: bytes, *, owner: str, kind: str,
            supersedes: str | None = None) -> str:
        """Archive ``data`` for ``owner`` and return its hash; the bytes precede the record.

        Guarantees, for a private kind under a cap: after the put, retained private
        state (counted per reference) is at most ``private_cap``; the put releases
        ``reclaimable`` references only when that makes it fit, and only until it
        does; a put that cannot fit raises ``ArtifactCapacityError`` having released
        and written nothing. ``supersedes``, a hash ``owner`` holds under ``kind``, is
        released once the new reference is indexed, and is measured as gone.
        """
        if not isinstance(data, (bytes, bytearray)):
            raise TypeError("artifact data must be bytes")
        if not isinstance(owner, str) or not owner or not isinstance(kind, str) or not kind:
            raise ArtifactError("artifact owner and kind are required")
        data = bytes(data)
        sha = hashlib.sha256(data).hexdigest()
        if supersedes == sha:
            supersedes = None
        if kind in PRIVATE_KINDS and self.private_cap is not None:
            self._admit_private(sha, len(data), owner, kind, supersedes)
        ts = self.clock()
        self._write(sha, data)  # Durable bytes before any authenticated reference.
        self.ledger.append({"kind": "artifact.put", "sha": sha, "owner": owner,
                            "artifact_kind": kind, "bytes": len(data), "ts": ts})
        # The first record of a hash stands as the owner of record — one subject
        # of retirement — but every writer gets its own reference
        # (R3-F): a second writer of identical bytes owns what it wrote and reads it.
        self.index.setdefault(sha, {"owner": owner, "kind": kind, "bytes": len(data), "ts": ts})
        record = self.index[sha]
        refs = record.setdefault("refs", {})
        if record.pop("released", None):
            # Every reference was released: the bytes are the new writer's alone, and
            # nothing about who wrote them before survives on the record.
            record.update(owner=owner, kind=kind, ts=ts)
        else:
            for existing in record.get("readers", [record["owner"]]):
                # An index restored from a checkpoint written before references carries
                # its readers; each becomes that reader's own reference, as it always was.
                refs.setdefault(existing, {"kind": record["kind"], "ts": record["ts"]})
        recent = self.released_recent.get(owner)
        if recent is not None and sha in recent:
            recent.remove(sha)
            if not recent:
                self.released_recent.pop(owner)
        reference = refs.setdefault(owner, {"kind": kind, "ts": ts})
        kinds = set(reference.get("kinds", [reference["kind"]]))
        if kind not in kinds:
            # The same owner holds these bytes under a second kind: both are named,
            # so releasing one never drops the other.
            reference["kinds"] = sorted(kinds | {kind})
        record["readers"] = sorted(refs)
        if kind in PRIVATE_KINDS:
            self._private_refs()[(sha, owner, kind)] = len(data)
        self._changed_sha(sha)
        if supersedes is not None:
            self.release(supersedes, owner=owner, kind=kind)
        return sha

    def _private_refs(self) -> dict[tuple[str, str, str], int]:
        """(sha, holder, kind) -> bytes of every private reference, rebuilt on an index
        change."""
        if self._private_epoch != self.epoch:
            self._private = {
                (sha, holder, k): record["bytes"]
                for sha, record in self.index.items()
                for holder, reference in self.references(sha, record).items()
                for k in reference.get("kinds", [reference["kind"]]) if k in PRIVATE_KINDS}
            self._private_epoch = self.epoch
        return self._private

    def private_bytes(self) -> int:
        """Retained private state: every private reference at its full size, each holder's
        counted whether or not another holds identical bytes."""
        return sum(self._private_refs().values())

    def private_holdings(self, owner: str) -> list[tuple[str, str]]:
        """Every ``(sha, kind)`` ``owner`` holds under a private kind, in put order."""
        return [(sha, k) for sha, record in self.index.items()
                for holder, reference in self.references(sha, record).items()
                if holder == owner
                for k in reference.get("kinds", [reference["kind"]]) if k in PRIVATE_KINDS]

    def _admit_private(self, sha: str, size: int, owner: str, kind: str,
                       supersedes: str | None) -> None:
        """Make room for a private put or refuse it: see ``put``."""
        refs = self._private_refs()
        adds = 0 if (sha, owner, kind) in refs else size
        freed = refs.get((supersedes, owner, kind), 0) if supersedes is not None else 0
        over = self.private_bytes() - freed + adds - self.private_cap
        if over <= 0:
            return
        owners = self.reclaimable() if self.reclaimable is not None else []
        candidates = [(holder, held, k, refs[(held, holder, k)])
                      for holder in owners if holder != owner
                      for held, k in self.private_holdings(holder)]
        if sum(size for *_rest, size in candidates) < over:
            # Even releasing every reclaimable reference would not make room: release
            # nothing, so a refused write takes nothing from anyone.
            raise ArtifactCapacityError(CAPACITY_REFUSAL)
        for holder, held, k, size in candidates:
            if over <= 0:
                break
            self.release(held, owner=holder, kind=k, cause="capacity")
            if self.on_reclaimed is not None:
                self.on_reclaimed(holder, held, k)
            over -= size

    def release(self, sha: str, *, owner: str, kind: str, cause: str | None = None) -> bool:
        """Drop ``owner``'s hold on ``sha`` under ``kind``; return whether the record is now free.

        Guarantees: only the named kind of the named owner's reference is dropped, so
        bytes the owner still holds under another kind, and bytes another owner
        holds, stay owned; a record whose last reference goes is marked ``pending``
        and is collected only once sealed by a later checkpoint; an unknown hash or a
        reference that does not hold the kind changes nothing. The release is
        ledgered as ``artifact.released`` before the index changes, as a put is. The
        reference's ``kind`` names only what the owner still holds, and a record
        whose owner of record lets go names a remaining holder instead. ``cause``,
        when given, is ledgered with the release (``capacity``: room made under the
        retained private state cap).
        """
        record = self.index.get(_valid_sha(sha))
        if record is None:
            return False
        refs = dict(self.references(sha, record))
        reference = refs.get(owner)
        if reference is None:
            return False
        kinds = set(reference.get("kinds", [reference["kind"]]))
        if kind not in kinds:
            return False
        kinds.discard(kind)
        if len(kinds) > 1:
            first = reference["kind"] if reference["kind"] in kinds else sorted(kinds)[0]
            refs[owner] = {"kind": first, "ts": reference["ts"], "kinds": sorted(kinds)}
        elif kinds:
            refs[owner] = {"kind": kinds.pop(), "ts": reference["ts"]}
        else:
            refs.pop(owner)
        self.ledger.append({"kind": "artifact.released", "sha": sha, "owner": owner,
                            "artifact_kind": kind, "free": not refs,
                            **({"cause": cause} if cause is not None else {}),
                            "ts": self.clock()})
        record["refs"] = refs
        record["readers"] = sorted(refs)
        if kind in PRIVATE_KINDS:
            self._private_refs().pop((sha, owner, kind), None)
        if owner not in refs:
            recent = self.released_recent.setdefault(owner, [])
            recent.append(sha)
            del recent[:-RELEASED_MEMORY]
            if refs and record["owner"] == owner:
                # The owner of record no longer holds the bytes: the earliest remaining
                # holder is, with the kind it holds them under. Kernel bookkeeping only:
                # no reader is ever shown a record's owner.
                holder = min(refs, key=lambda seat: (refs[seat]["ts"], seat))
                record.update(owner=holder, kind=refs[holder]["kind"])
        elif record["owner"] == owner:
            # The owner of record keeps the bytes under another kind: the record names
            # what it still holds, never a kind nobody holds them under any more.
            record["kind"] = refs[owner]["kind"]
        if not refs:
            # Named by the latest checkpoint: kept until a later one is durable. Written
            # since it: no checkpoint names it and a replay re-creates it (``collect``).
            record["released"] = "pending" if sha in self.checkpointed else "sealed"
        self._changed_sha(sha)
        return not refs

    def seal_released(self) -> int:
        """Make every pending release collectable; return how many were sealed.

        Called once a checkpoint is durable (and again after a resume restores one):
        the checkpoint just written names no reference to a record released before
        it, so collecting that record can never leave a resume short of bytes.
        """
        sealed = 0
        for record in self.index.values():
            if record.get("released") == "pending":
                record["released"] = "sealed"
                sealed += 1
        self.checkpointed = frozenset(self.index)
        return sealed

    def retained(self) -> dict[str, int]:
        """What the archive holds now: indexed records, their bytes, and the released part."""
        records = len(self.index)
        total = sum(record["bytes"] for record in self.index.values())
        released = sum(record["bytes"] for record in self.index.values()
                       if record.get("released"))
        return {"records": records, "bytes": total, "released_bytes": released}

    def get(self, sha: str) -> bytes:
        """Return the bytes of an archived artifact, verified against the hash asked for."""
        sha = _valid_sha(sha)
        if self.root is None:
            if sha not in self._memory:
                raise ArtifactError("unknown artifact")
            data = self._memory[sha]
            if hashlib.sha256(data).hexdigest() != sha:
                raise ArtifactError("artifact bytes do not match their hash")
            return data
        path = self.root / sha
        try:
            data = path.read_bytes()
        except FileNotFoundError:
            raise ArtifactError("unknown artifact") from None
        if hashlib.sha256(data).hexdigest() != sha:
            raise ArtifactError("artifact bytes do not match their hash")
        return data

    def list(self, *, owner: str | None = None) -> list[dict[str, Any]]:
        """Return detached records in put order, optionally those of one owner."""
        return [{"sha": sha, **record} for sha, record in self.index.items()
                if owner is None or owner in self.references(sha, record)]

    def references(self, sha: str, record: dict[str, Any]) -> dict[str, dict[str, Any]]:
        """The (sha, owner) references on one record, including a pre-R3-F index's readers."""
        refs = record.get("refs")
        if refs:
            return refs
        return {reader: {"kind": record["kind"], "ts": record["ts"]}
                for reader in record.get("readers", [record["owner"]])}

    def entries(self) -> list[tuple[str, str, int, int]]:
        """Every reference as ``(sha, owner, bytes, created_ns)``, in put order.

        One row per (sha, owner) reference (R3-F): a blob two seats wrote is two
        rows, each with its own owner, because that is what each of them owns.
        """
        return [(sha, owner, record["bytes"], reference.get("ts", record["ts"]))
                for sha, record in self.index.items()
                for owner, reference in self.references(sha, record).items()]

    def collect(self) -> list[str]:
        """Remove sealed released records, ledgering each, and unnamed leftovers (R3-F).

        The only blobs this can reach are records whose every reference was released
        and sealed (each leaves the index and is ledgered ``artifact.collected``
        whether or not its unlink succeeds; returned only when its bytes are gone,
        and bytes that stay behind are a leftover), and bytes no record names, such
        as a crash between ``_write`` and the ledger item leaves (removed without an
        item, and only when the journal is not recovering; not returned). An owned
        blob is never a candidate, so collection can never take bytes a seat still
        holds: its state, or an inbox body it has not released (an acknowledged body,
        or one past its published retention, is released by its inbox first). A live
        run and its replay therefore ledger the same removals.

        **A replay collects only what the diary knows (R4-C).** The archive
        directory is not replayed state. After a crash it still holds the bytes
        the interrupted run wrote *after* the checkpoint a resume starts from, and
        the restored index does not name them yet, so sweeping it during recovery
        deletes blobs the recorded path still had live references for and ledgers
        a removal the recording never made -- which is a divergence, and the
        replay is the side that is wrong. While the journal is recovering the
        candidates are therefore restricted to index records whose references were
        all released: that is checkpointed state, it is reached by the same
        deterministic tail, and it is ledgered identically on replay. Untracked
        leftovers keep their bytes until the first boundary after the world is
        live again, by which time the replay has re-put everything still owned and
        only the true leftovers remain; their removal is never ledgered, live or
        replayed, so the two diaries cannot disagree over it.
        """
        # A release not yet sealed by a durable checkpoint is still live: the latest
        # checkpoint may name it, and a resume from it must find the bytes.
        live = {sha for sha, record in self.index.items()
                if self.references(sha, record) or record.get("released") == "pending"}
        # A sealed record is a candidate whether or not its bytes are still on disk:
        # a replay reaches it after the recorded run removed them, and must collect
        # (and ledger) it exactly as the recording did.
        sealed = sorted(sha for sha, record in self.index.items()
                        if record.get("released") == "sealed")
        if self.root is None:
            leftovers = sorted(sha for sha in self._memory
                               if sha not in live and sha not in self.index)
        else:
            leftovers = sorted(path.name for path in self.root.glob("*")
                               if len(path.name) == SHA_HEX_CHARS and path.name not in live
                               and path.name not in self.index)
        if not getattr(self.ledger, "recovering", False):
            # Bytes no ledger item ever named: removed, but not ledgered, since the
            # diary never knew them and a replay cannot know which ones a disk held.
            for sha in leftovers:
                self._remove_bytes(sha)
            if self.root is not None:
                # A write torn before its rename leaves only its temporary file.
                for path in self.root.glob(".*-*"):
                    try:
                        path.unlink()
                    except OSError:
                        pass
        removed = []
        for sha in sealed:
            # The record leaves the index and is ledgered whatever the disk does, so a
            # live run and its replay write the same items. Bytes an unlink could not
            # remove are a leftover from here on, removed (unledgered) at a later
            # boundary; only hashes whose bytes are gone are returned.
            self.index.pop(sha, None)
            self._changed_sha(sha)
            self.ledger.append({"kind": "artifact.collected", "sha": sha, "ts": self.clock()})
            if self._remove_bytes(sha):
                removed.append(sha)
        return removed

    def _remove_bytes(self, sha: str) -> bool:
        """Remove one blob's bytes; True when they are gone, already gone included."""
        if self.root is None:
            self._memory.pop(sha, None)
            return True
        try:
            (self.root / sha).unlink()
        except FileNotFoundError:
            # A replay collects a sealed record whose bytes the recorded run already
            # removed: it is ledgered again, exactly as recorded.
            return True
        except OSError:
            return False
        return True

    def visible_to(self, sha: str, reader: str | None,
                   lineage_of: Callable[[str], str] | None = None) -> bool:
        """Whether ``reader`` may read this artifact (C1): its own, or its lineage's program.

        A seat reads what it holds a reference to. A program's state is private to
        its lineage: a reader sees it when a seat of its own lineage holds a
        ``program.state`` reference to those bytes now, whoever else holds them and
        whoever wrote them first. Unindexed bytes confer no read authority.
        """
        record = self.index.get(sha)
        if record is None:
            return False
        if reader is None:
            return True  # Kernel-only inspection retains its existing contract.
        refs = self.references(sha, record)
        if reader in refs:
            return True
        if lineage_of is None:
            return False
        lineage = lineage_of(reader)
        return any("program.state" in reference.get("kinds", [reference["kind"]])
                   and lineage_of(holder) == lineage
                   for holder, reference in refs.items())

    def owner_for(self, sha: str) -> str | None:
        """The artifact's owner of record, or None for a hash the archive never saw.

        Retirement never changes this. Holding an artifact costs no money: the
        archive is the world's own disk, which pays no one.
        """
        record = self.index.get(_valid_sha(sha))
        return None if record is None else record["owner"]

    def read(self, sha: Any, *, reader: str | None = None,
             lineage_of: Callable[[str], str] | None = None) -> dict[str, Any]:
        """The ``artifact.get`` view: the reader's own reference and the bytes, or a refusal.

        Guarantees (essay II.I.b; AGENTS.md rules 4 and 5): the view never names an
        owner or anything about another holder; its ``kind`` is the reader's own
        (``program.state`` for its lineage's program state). A reader that may not see
        the hash gets ``artifact_released`` when it is one of the reader's own last
        ``RELEASED_MEMORY`` releases, and otherwise ``{sha, error: artifact_private}``,
        byte for byte the same whether the archive never saw the hash, another seat
        holds it, it was collected or leftover bytes sit on disk.
        """
        try:
            sha = _valid_sha(sha)
        except ArtifactError as exc:
            return {"error": str(exc)}
        if reader is None and sha not in self.index:
            return {"error": "unknown artifact"}  # kernel-only inspection, never a seat's
        if not self.visible_to(sha, reader, lineage_of):
            if reader is not None and sha in self.released_recent.get(reader, ()):
                return {"sha": sha, "error": RELEASED_REFUSAL}
            return {"sha": sha, "error": PRIVATE_REFUSAL}
        try:
            data = self.get(sha)
        except ArtifactError as exc:
            return {"error": str(exc)}
        record = self.index.get(sha, {})
        reference = self.references(sha, record).get(reader) if record else None
        view = {"sha": sha, "kind": (reference or {}).get(
                    "kind", "program.state" if reader is not None else record.get("kind")),
                "bytes": len(data)}
        if len(data) > MAX_TOOL_READ_BYTES:
            return {**view, "error": f"artifact exceeds {MAX_TOOL_READ_BYTES} bytes"}
        try:
            return {**view, "text": data.decode("utf-8")}
        except UnicodeDecodeError:
            return {**view, "base64": base64.b64encode(data).decode("ascii")}

    def _write(self, sha: str, data: bytes) -> None:
        if self.root is None:
            existing = self._memory.get(sha)
            if existing is not None and existing != data:
                raise ArtifactError("artifact bytes do not match their hash")
            self._memory.setdefault(sha, data)
            return
        path = self.root / sha
        if path.exists():
            try:
                self.get(sha)  # Verify an existing file instead of blessing corruption.
                return
            except ArtifactError:
                # A torn or corrupted file under this name: the bytes in hand hash to
                # it, so they replace it atomically below rather than fail the put.
                pass
        self.root.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(prefix=f".{sha[:12]}-", dir=self.root)
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            os.chmod(temporary, 0o600)
            os.replace(temporary, path)
            directory_fd = os.open(self.root, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        except BaseException:
            try:
                os.unlink(temporary)
            except OSError:
                pass
            raise
