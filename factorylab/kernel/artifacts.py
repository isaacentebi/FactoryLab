"""The artifact archive: content-addressed bytes the population keeps (contract C9).

An artifact is a byte string named by its SHA-256. The ledger holds the record
of every put — hash, owner, kind, size, time — and the bytes live beside the
ledger under ``runs/<world>.artifacts/<sha>``, so a diary reader can verify what
a seat kept without the bytes themselves passing through the chain. A world
without a ledger path (a test runtime) keeps the bytes in memory.

Guarantees: the ledger item precedes the bytes, so a crash between them leaves
a record without bytes rather than bytes without a record; a put is idempotent
by content, so replay after a crash rewrites nothing; a read verifies the hash
it was asked for, so a tampered file is refused rather than served; nothing
here deletes — retirement of an owner is not the archive's business, and the
first owner of a hash stays its owner. Rent (C3) and entitlements (C10) are
charged by their own workstreams through ``owner_for``.
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
# What ``artifact.get`` returns inline: a program's private state or a note-sized
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
    """Put, get and list artifacts; every put is a ledger item before it is a file."""

    def __init__(self, ledger: Any, *, root: str | os.PathLike[str] | None,
                 clock_ns: Callable[[], int]) -> None:
        self.ledger = ledger
        self.root = Path(root) if root is not None else None
        self.clock = clock_ns
        # sha -> {"owner", "kind", "bytes", "ts"}: the checkpointed index of the archive.
        self.index: dict[str, dict[str, Any]] = {}
        self._memory: dict[str, bytes] = {}

    def put(self, data: bytes, *, owner: str, kind: str, public: bool = False) -> str:
        """Archive ``data`` for ``owner`` and return its hash; the record precedes the bytes."""
        if not isinstance(data, (bytes, bytearray)):
            raise TypeError("artifact data must be bytes")
        if not isinstance(owner, str) or not owner or not isinstance(kind, str) or not kind:
            raise ArtifactError("artifact owner and kind are required")
        data = bytes(data)
        sha = hashlib.sha256(data).hexdigest()
        ts = self.clock()
        self._write(sha, data)  # Durable bytes before any authenticated reference.
        self.ledger.append({"kind": "artifact.put", "sha": sha, "owner": owner,
                            "artifact_kind": kind, "bytes": len(data), "public": bool(public),
                            "ts": ts})
        # The first record of a hash stands: a second owner of identical bytes is a
        # reader of the first's artifact, not a new liability for the same file.
        self.index.setdefault(sha, {"owner": owner, "kind": kind, "bytes": len(data), "ts": ts,
                                    "public": bool(public)})
        record = self.index[sha]
        record["readers"] = sorted(set(record.get("readers", [record["owner"]])) | {owner})
        record["public"] = bool(record.get("public") or public)
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
                if owner is None or owner in record.get("readers", [record["owner"]])]

    def entries(self) -> list[tuple[str, str, bool, int, int]]:
        """Every record as ``(sha, owner, public, bytes, created_ns)``, in put order.

        The directory listing W4 builds (C4) reads its rows from here, so the
        index has one shape both a scoped read and a bounded listing agree on.
        """
        return [(sha, record["owner"], bool(record.get("public")), record["bytes"],
                 record["ts"]) for sha, record in self.index.items()]

    def visible_to(self, sha: str, reader: str | None,
                   lineage_of: Callable[[str], str] | None = None) -> bool:
        """Whether ``reader`` may read this artifact (C1): own, published, or same lineage.

        A seat reads what it wrote and whatever was published; a program's state
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
        if reader in record.get("readers", [record["owner"]]) or record.get("public"):
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
            if not self.visible_to(sha, reader, lineage_of):
                return {"sha": sha, "error": PRIVATE_REFUSAL}
            data = self.get(sha)
        except ArtifactError as exc:
            return {"error": str(exc)}
        record = self.index.get(sha, {})
        view = {"sha": sha, "owner": record.get("owner"), "kind": record.get("kind"),
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
