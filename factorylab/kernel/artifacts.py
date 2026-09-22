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
its owner of record. Rent (C3) and entitlements (C10) are charged by their own
workstreams through ``owner_for``.

**Ownership is a (sha, owner) reference (edition 3, R3-F).** One blob may carry
several references: a second writer of identical bytes owns its own reference,
with its own kind and its own moment, and can read what it wrote rather than
being told the first writer's bytes are private. The first reference stays the
owner of record (``owner_for``), so rent has one payer and retirement has one
subject. Nothing is published: an artifact is its owners' private state (essay
II.I.b), and ruling R11 deleted the publication path nothing ever used.

**Nothing is deleted except an unreferenced blob.** ``collect()`` removes exactly
those — durable bytes no reference names, which is what a crash between
``_write`` and the ledger item leaves behind — and ledgers each removal as
``artifact.collected``. It is called by the runtime at a reserve-window boundary.
A blob any reference names is never a candidate whatever its age.
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


class ArtifactError(ValueError):
    """The archive refuses a malformed hash, unknown artifact or corrupted file."""


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

    def put(self, data: bytes, *, owner: str, kind: str) -> str:
        """Archive ``data`` for ``owner`` and return its hash; the bytes precede the record."""
        if not isinstance(data, (bytes, bytearray)):
            raise TypeError("artifact data must be bytes")
        if not isinstance(owner, str) or not owner or not isinstance(kind, str) or not kind:
            raise ArtifactError("artifact owner and kind are required")
        data = bytes(data)
        sha = hashlib.sha256(data).hexdigest()
        ts = self.clock()
        self._write(sha, data)  # Durable bytes before any authenticated reference.
        self.ledger.append({"kind": "artifact.put", "sha": sha, "owner": owner,
                            "artifact_kind": kind, "bytes": len(data), "ts": ts})
        # The first record of a hash stands as the owner of record — one payer of
        # rent, one subject of retirement — but every writer gets its own reference
        # (R3-F): a second writer of identical bytes owns what it wrote and reads it.
        self.index.setdefault(sha, {"owner": owner, "kind": kind, "bytes": len(data), "ts": ts})
        record = self.index[sha]
        refs = record.setdefault("refs", {})
        for existing in record.get("readers", [record["owner"]]):
            # An index restored from a checkpoint written before references carries
            # its readers; each becomes that reader's own reference, as it always was.
            refs.setdefault(existing, {"kind": record["kind"], "ts": record["ts"]})
        refs.setdefault(owner, {"kind": kind, "ts": ts})
        record["readers"] = sorted(refs)
        self._changed_sha(sha)
        return sha

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
        """Remove durable blobs no reference names; ledger each (R3-F).

        The only blobs this can reach are the ones a crash between ``_write`` and
        the ledger item left behind, and records whose every reference was released.
        An owned blob is never a candidate, so collection can never take a seat's
        state or an inbox body.

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
        only the true leftovers remain.
        """
        live = {sha for sha, record in self.index.items() if self.references(sha, record)}
        if self.root is None:
            orphans = sorted(sha for sha in self._memory if sha not in live)
        else:
            orphans = sorted(path.name for path in self.root.glob("*")
                             if len(path.name) == SHA_HEX_CHARS and path.name not in live)
        if getattr(self.ledger, "recovering", False):
            orphans = [sha for sha in orphans if sha in self.index]
        for sha in orphans:
            if self.root is None:
                self._memory.pop(sha, None)
            else:
                try:
                    (self.root / sha).unlink()
                except OSError:
                    continue
            self.index.pop(sha, None)
            self._changed_sha(sha)
            self.ledger.append({"kind": "artifact.collected", "sha": sha, "ts": self.clock()})
        return orphans

    def visible_to(self, sha: str, reader: str | None,
                   lineage_of: Callable[[str], str] | None = None) -> bool:
        """Whether ``reader`` may read this artifact (C1): own or same lineage.

        A seat reads what it wrote; a program's state
        is private to its program's owner lineage, so the seat that registered a
        program can still read what the program keeps, and a stranger cannot.
        A hash the archive never saw is not private, it is unknown, and the read
        path says so instead.
        """
        record = self.index.get(sha)
        if record is None:
            return False  # Unindexed durable bytes confer no read authority.
        if reader is None:
            return True  # Kernel-only inspection retains its existing contract.
        if reader in self.references(sha, record):
            return True
        if record["kind"] == "program.state" and lineage_of is not None:
            return lineage_of(record["owner"]) == lineage_of(reader)
        return False

    def owner_for(self, sha: str) -> str | None:
        """The seat liable for an artifact's rent, or None for a hash the archive never saw.

        Retirement never changes this: whoever charges rent decides whether a
        retired owner's artifact is billed at the commons rate.
        """
        record = self.index.get(_valid_sha(sha))
        return None if record is None else record["owner"]

    def read(self, sha: Any, *, reader: str | None = None,
             lineage_of: Callable[[str], str] | None = None) -> dict[str, Any]:
        """The ``artifact.get`` view: metadata and inline content, or a bounded error.

        Scoping precedes retrieval: a reader who may not see the artifact is told
        it is private and nothing about its bytes, its size or its owner.
        """
        try:
            sha = _valid_sha(sha)
            if sha not in self.index:
                # Unindexed durable bytes confer no read authority, but a hash the
                # archive never saw at all is unknown, not private, and says so.
                self.get(sha)
                return {"sha": sha, "error": PRIVATE_REFUSAL}
            if not self.visible_to(sha, reader, lineage_of):
                return {"sha": sha, "error": PRIVATE_REFUSAL}
            data = self.get(sha)
        except ArtifactError as exc:
            return {"error": str(exc)}
        record = self.index.get(sha, {})
        # A reader that owns its own reference is shown the kind it wrote under,
        # not the first writer's; the owner of record is the blob's.
        reference = self.references(sha, record).get(reader) if record else None
        view = {"sha": sha, "owner": record.get("owner"),
                "kind": (reference or record).get("kind"), "bytes": len(data)}
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
            self.get(sha)  # Verify an existing file instead of blessing corruption.
            return
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
