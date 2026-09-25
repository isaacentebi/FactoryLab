"""Sealed bytes beside the diary: the rolling checkpoint and the recorded answers (wave 17).

The diary is the world's append-only record (essay II: the factory "cannot be
rewound by restoring some prior configuration"). Two kinds of bytes it used to
carry inline are named by hash instead, and kept beside it:

* **The rolling checkpoint** (``<world>.checkpoint/``). A continuation is not
  history: a resume starts from the latest checkpoint and nothing ever reads an
  older one, so writing the whole world state into the chain at every boundary
  grew the diary quadratically. One file holds the latest state; the chain's
  ``snapshot`` item holds its SHA-256 and size.
* **Recorded answers** (``<world>.io/``). An ``io.result`` whose body exceeds
  ``IO_INLINE_BYTES`` is kept as a content-addressed file, so an answer the world
  reads again and again (the venue's instrument listing) is stored once; the
  chain's item holds its SHA-256 and size.

Guarantees, for both:

* **Durable before named.** The bytes are written to a temporary file, flushed to
  stable storage (``F_FULLFSYNC`` where the OS has it, as the wave 10 rail code
  does), renamed into place and the directory flushed, before the ledger item that
  names them is appended. A crash leaves at worst unnamed bytes, never a named
  hash without bytes.
* **Sealed.** Bytes are compressed and then sealed under the diary's own key
  (``Ledger.seal_bytes``); file names are keyed hashes (``Ledger.sidecar_name``),
  so the disk reveals neither content nor which states or answers were equal
  (essay II.I.b: local state is private).
* **Authenticated by the chain.** A reader unseals, decompresses and checks the
  bytes against the SHA-256 and size the chain holds. A missing, stale, foreign or
  altered file is refused; a stale checkpoint under the latest name fails the hash,
  so the factory cannot be rewound through the disk.

A memory-only ledger (a test runtime) keeps the same sealed bytes in memory.
"""

from __future__ import annotations

import hashlib
import os
import tempfile
import zlib
from pathlib import Path
from typing import Any

from factorylab.kernel.ledger import LedgerIntegrityError
from factorylab.runtime.capital_loop import _durable, _fsync_directory

#: An ``io.result`` body up to this many canonical bytes stays inline in the diary;
#: a larger one is a file named by its hash.
IO_INLINE_BYTES = 1_024
#: Every diary item a world writes is at most this many canonical bytes on a scripted
#: world (``tests/runtime/test_bounded_memory.py``): the diary grows with events,
#: never with the size of the world's state or of an answer it read.
ITEM_CAP_BYTES = 65_536

_COMPRESSION = 6
_HEX = frozenset("0123456789abcdef")


class SidecarMissing(LookupError):
    """The chain names bytes that are not beside it."""


class SidecarMismatch(ValueError):
    """Bytes are beside the chain but are not the ones it names."""


def checkpoint_root(ledger_path: str | os.PathLike[str]) -> Path:
    """The rolling checkpoint beside a ledger: ``runs/<world>.checkpoint``."""
    return Path(ledger_path).with_suffix(".checkpoint")


def io_root(ledger_path: str | os.PathLike[str]) -> Path:
    """The recorded answers beside a ledger: ``runs/<world>.io``."""
    return Path(ledger_path).with_suffix(".io")


def _is_name(name: str) -> bool:
    return len(name) == 64 and set(name) <= _HEX


def durable_create(root: Path, name: str, data: bytes) -> None:
    """Make ``root/name`` hold exactly ``data`` on stable storage before returning.

    Guarantees the final name never holds a partial file: the bytes reach a
    temporary file in ``root``, are flushed, and are renamed over the name, and
    the rename is flushed with the directory. A crash at any point leaves either
    the previous file or the new one under the name, and possibly a temporary
    file (``.<prefix>-*``) that no reader ever opens.
    """
    created = not root.exists()
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    if created:
        _fsync_directory(root.parent)
    fd, temporary = tempfile.mkstemp(prefix=f".{name[:12]}-", dir=root)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            _durable(stream.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, root / name)
        _fsync_directory(root)
    except BaseException:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


class _SealedStore:
    """Sealed, compressed bytes named by the keyed hash of their plaintext SHA-256."""

    def __init__(self, ledger: Any, suffix: str) -> None:
        self._ledger = ledger
        self._suffix = suffix
        self._memory: dict[str, bytes] = {}

    @property
    def root(self) -> Path | None:
        path = getattr(self._ledger, "path", None)
        return None if path is None else Path(path).with_suffix(self._suffix)

    def _name(self, sha: str) -> str:
        return self._ledger.sidecar_name(sha)

    def _write(self, sha: str, data: bytes) -> str:
        name = self._name(sha)
        token = self._ledger.seal_bytes(zlib.compress(data, _COMPRESSION))
        root = self.root
        if root is None:
            self._memory[name] = token
        else:
            durable_create(root, name, token)
        return name

    def _read(self, sha: str, size: int | None) -> bytes:
        """The plaintext the chain names by ``sha`` (and ``size``), or a refusal."""
        name = self._name(sha)
        root = self.root
        if root is None:
            token = self._memory.get(name)
            if token is None:
                raise SidecarMissing(sha)
        else:
            try:
                token = (root / name).read_bytes()
            except FileNotFoundError:
                raise SidecarMissing(sha) from None
        try:
            data = zlib.decompress(self._ledger._unseal_bytes(token))
        except (LedgerIntegrityError, zlib.error) as exc:
            raise SidecarMismatch(sha) from exc
        if hashlib.sha256(data).hexdigest() != sha or (size is not None and len(data) != size):
            raise SidecarMismatch(sha)
        return data

    def _has(self, sha: str, size: int | None) -> bool:
        try:
            self._read(sha, size)
        except (SidecarMissing, SidecarMismatch, OSError):
            return False
        return True


class CheckpointStore(_SealedStore):
    """One rolling checkpoint beside the diary; the chain's ``snapshot`` item names it."""

    def __init__(self, ledger: Any) -> None:
        super().__init__(ledger, ".checkpoint")

    def write(self, state: bytes) -> dict[str, Any]:
        """Write one checkpoint durably and return the reference the chain will hold.

        Guarantees the bytes are on stable storage when this returns, and that the
        reference is ``{"state_sha", "bytes"}`` of the canonical plaintext, identical
        for identical states whatever the key, so two runs of one manifest and seed
        ledger identical items.
        """
        sha = hashlib.sha256(state).hexdigest()
        self._write(sha, state)
        return {"state_sha": sha, "bytes": len(state)}

    def read(self, reference: dict[str, Any]) -> bytes:
        """The canonical checkpoint bytes a ``snapshot`` item names.

        Raises ``SidecarMissing`` when no file carries the name, and
        ``SidecarMismatch`` when the file is foreign, altered, or holds another
        state than the one named (a stale checkpoint put back under the name).
        """
        return self._read(reference["state_sha"], reference.get("bytes"))

    def retire_others(self, reference: dict[str, Any]) -> None:
        """Remove every checkpoint but the one ``reference`` names, and torn temporaries.

        Called only once the item naming ``reference`` is durable in the chain: no
        resume can start from anything older, so nothing older is kept (one
        rolling checkpoint). A file that cannot be removed now is removed at the
        next checkpoint; its presence changes nothing, since only the named hash
        is ever read.
        """
        keep = self._name(reference["state_sha"])
        root = self.root
        if root is None:
            for name in [n for n in self._memory if n != keep]:
                del self._memory[name]
            return
        if not root.is_dir():
            return
        for path in root.iterdir():
            if path.name == keep:
                continue
            if _is_name(path.name) or path.name.startswith("."):
                try:
                    path.unlink()
                except OSError:
                    pass


class IoStore(_SealedStore):
    """Recorded answers larger than ``IO_INLINE_BYTES``, content-addressed and deduplicated."""

    def __init__(self, ledger: Any) -> None:
        super().__init__(ledger, ".io")

    def put(self, body: bytes) -> dict[str, Any]:
        """Keep ``body`` durably, once per content, and return what the chain names it by.

        Guarantees the bytes are on stable storage when this returns; an identical
        body already kept and intact is not written again, and a torn or foreign
        file under the name is replaced by the bytes in hand.
        """
        sha = hashlib.sha256(body).hexdigest()
        if not self._has(sha, len(body)):
            self._write(sha, body)
        return {"result_sha": sha, "bytes": len(body)}

    def get(self, item: dict[str, Any]) -> bytes:
        """The canonical body an ``io.result`` item names, verified against its hash."""
        return self._read(item["result_sha"], item.get("bytes"))

    def sweep(self) -> None:
        """Remove temporaries a write torn by a crash left; no named file is touched.

        Called between writes (at a checkpoint boundary), when no write is in flight,
        so every temporary present is a torn one that no item names. A named body is
        never removed: one written by a crash before its item is appended is kept
        like any other (it costs its bytes once, and a replay writing the same
        answer finds it already there).
        """
        root = self.root
        if root is None or not root.is_dir():
            return
        for path in root.glob(".*-*"):
            try:
                path.unlink()
            except OSError:
                pass


def verify_restorable(ledger_path: str | os.PathLike[str],
                      manifest_path: str | os.PathLike[str]) -> dict[str, Any]:
    """Prove a copied diary resumable as far as its bytes go: what ``resume`` would read.

    Guarantees, when it returns: the diary's chain is intact, its latest checkpoint
    is beside it and is exactly the state the chain names, every artifact that
    state's archive index still holds is beside it, hash-true, and every recorded
    answer the replay tail names by hash (an ``io.result`` after that checkpoint) is
    beside it, unseals under the diary's key and matches its hash and size.
    Otherwise it raises (``ResumeError`` with ``checkpoint_missing``,
    ``checkpoint_mismatch``, ``artifact_missing`` or ``io_result_missing``, naming
    the sha, or ``LedgerIntegrityError``). Opens the diary read-only and writes
    nothing. ``deploy/backup.sh`` runs it on the staged copy.
    """
    import json

    from factorylab.kernel.artifacts import ArtifactError, ArtifactStore, artifact_root
    from factorylab.kernel.ledger import Ledger
    from factorylab.runtime.resume import ResumeError, checkpoint_state, decode
    from factorylab.runtime.worlds import load_manifest

    manifest = load_manifest(str(manifest_path))
    ledger = Ledger.open_read_only(ledger_path, manifest=json.loads(manifest.canonical_json()))
    snapshot, tail = ledger._recovery_tail()
    if snapshot is None:
        raise ResumeError("ledger has no recoverable snapshot")
    state = checkpoint_state(ledger, snapshot)
    # The answers a resume replays: only the tail after the checkpoint is read back.
    answers = IoStore(ledger)
    named = 0
    for item in tail:
        if item.get("kind") != "io.result" or "result_sha" not in item:
            continue
        try:
            answers.get(item)
        except (SidecarMissing, SidecarMismatch, OSError):
            raise ResumeError("a recorded answer the replay tail names is missing or altered",
                              code="io_result_missing", sha=item["result_sha"],
                              seq=item["seq"]) from None
        named += 1
    index = (decode(state["components"]).get("artifacts") or {}).get("index") or {}
    archive = ArtifactStore(None, root=artifact_root(ledger_path), clock_ns=lambda: 0)
    for sha, record in index.items():
        if record.get("released"):
            continue
        try:
            archive.get(sha)
        except ArtifactError:
            raise ResumeError("the archive index names bytes that are missing or corrupt",
                              code="artifact_missing", sha=sha) from None
    return {"state_sha": snapshot.get("state_sha"), "snapshot_seq": snapshot["seq"],
            "artifacts": len(index), "answers": named}


if __name__ == "__main__":
    import json
    import sys

    print(json.dumps(verify_restorable(sys.argv[1], sys.argv[2]), sort_keys=True))
